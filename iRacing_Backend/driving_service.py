"""Bounded background learning; SDK ingestion never reads/writes a profile or fits models."""
from __future__ import annotations
from collections import deque
from copy import deepcopy
import logging
import queue
import threading
import time
from driving_learning import DrivingHistory, applicable, finite, learn
from driving_observations import FRAME_CHANNELS, digest, observation, scope, windows

logger = logging.getLogger('iRacingLive')
GROUPS = ('technique', 'settings', 'chains', 'impacts')


def public_view(result):
    return {k: [{n: deepcopy(v) for n,v in row.items() if n not in ('support','evidence_ids','evidence_laps')}
                for row in value][:24] if k in GROUPS else deepcopy(value) for k,value in result.items()}


class DrivingLearningService:
    def __init__(self, path=None):
        self.history = DrivingHistory(path)
        self.queue = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.thread = None
        self.results = {}
        self.failure = None
        self.dropped = 0
        self.reset()

    def reset(self):
        self.frames = deque(maxlen=3300)
        self.key = None
        self.definitions = []
        self.previous = None
        self.current = None
        self.calls = {}
        self.announced = set()
        self.updated_at = 0.

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.reset()

    def resume(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = None
            self.stop_event.clear()

    def _submit(self, job):
        if self.stop_event.is_set():
            return
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name='driving-learning', daemon=True)
            self.thread.start()
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            self.dropped += 1  # Never delay telemetry or silently count an unprocessed pass.

    def _work(self):
        while not self.stop_event.is_set():
            try:
                job = self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                identity, run, lap, length, setup, frames, definitions = job
                added, reasons = [], {}
                for definition in definitions:
                    row, reason = observation(frames, definition, identity, run, lap, length, setup=setup)
                    if row:
                        added.append(row)
                    else:
                        reasons[reason] = reasons.get(reason,0)+1
                key = scope(identity,'LIVE')
                rows = self.history.update(added) if added else self.history.rows()
                result = learn([r for r in rows if r['scope'] == key])
                result['last_rejections'] = reasons
                # Single immutable snapshot publication; readers do not take a worker lock.
                self.results = dict(list({**self.results, key: result}.items())[-32:])
                self.failure = None
            except Exception:
                self.failure = 'PROFILE_UNAVAILABLE'
                logger.exception('Driving learning unavailable; telemetry continues')
            finally:
                self.queue.task_done()

    def ingest(self, identity, sample, run, definitions, length, now):
        if identity.get('game') != 'iracing' or not run or finite(length) is None:
            self.current = None
            return
        key = (run, digest([(c.get('id'),c.get('entry_pct'),c.get('apex_pct'),c.get('exit_pct')) for c in definitions]), length)
        if key != self.key:
            self.reset()
            self.key = key
            self.definitions = windows(definitions,length)
        frame = {k: sample.get(k) for k in (*FRAME_CHANNELS,'lap_number','traffic_context','setup_fingerprint')}
        self.current = (dict(identity),frame,run)
        self.updated_at = now
        previous = self.previous
        self.previous = frame
        pct, stamp = finite(frame.get('distance_pct')), finite(frame.get('session_time'))
        if pct is None or stamp is None or frame.get('is_replay') is not False or frame.get('on_track') is not True:
            self.frames.clear()
            self.previous = None
            return
        if previous and (previous.get('lap_number') != frame.get('lap_number')
                         or finite(previous.get('distance_pct')) is None
                         or pct < previous['distance_pct'] or stamp <= (finite(previous.get('session_time')) or 0)):
            self.frames.clear()
            previous = None
        self.frames.append(frame)
        if not previous:
            return
        finished = [d for d in self.definitions if previous['distance_pct'] < d['end_pct'] <= pct]
        if finished:
            self._submit((dict(identity),run,frame['lap_number'],length,frame.get('setup_fingerprint'),
                          tuple(self.frames),finished))

    def _probe(self, model):
        if self.current is None:
            return None
        identity, frame, run = self.current
        return dict(scope=scope(identity,'LIVE'), source='LIVE', window=model.get('window'), kind=model.get('kind'),
                    run=run, lap=frame.get('lap_number'), setup=frame.get('setup_fingerprint'),
                    conditions={k:frame.get(v) for k,v in (('fuel','fuel_l'),('temp','track_temp_c'),
                                ('compound','tire_compound'),('wet','track_wetness'))},
                    controls={k:frame[v] for k,v in (('bb','brake_bias'),('abs','abs_setting'),('tc','tc_setting')) if frame.get(v) is not None},
                    traffic=frame.get('traffic_context'), metrics={'time_s':1.})

    def valid(self, token, now):
        model = self.calls.get(token)
        if not model or self.current is None or not 0 <= now-self.updated_at <= 2:
            return False
        _, frame, run = self.current
        if model['_run'] != run or model['_lap'] != frame.get('lap_number'):
            return False
        if (frame.get('on_track') is not True or frame.get('on_pit_road') is not False
                or frame.get('is_replay') is not False or frame.get('session_flags') is None
                or int(frame['session_flags']) & 0xC108 or frame.get('engine_warnings') != 0
                or frame.get('pit_repair_left_s') != 0 or frame.get('car_left_right') != 1):
            return False
        from radio_policy import quiet_window
        if not quiet_window(frame):
            return False
        if not applicable(model,self._probe(model)):
            return False
        pct, speed = finite(frame.get('distance_pct')), finite(frame.get('speed_mps'))
        if pct is None or speed is None or speed < 5:
            return False
        distance = ((model['entry_pct']-pct)%1)*self.key[2]
        from live_corners import call_seconds
        return call_seconds(model['_speech'])+1.5 < distance/speed < 14

    def choose(self, now, phrases):
        if self.current is None or not self.definitions:
            return None
        identity, frame, run = self.current
        lap_key = (run, frame.get('lap_number'))
        self.calls = {k:m for k,m in self.calls.items() if (m['_run'],m['_lap']) == lap_key}
        if lap_key in self.announced:
            return None
        result = self.results.get(scope(identity,'LIVE'),{})
        # A connected outcome takes priority over advice for one of its members.
        candidates = [m for k in ('chains','technique','impacts') for m in result.get(k,[])]
        active = {d['id'] for d in self.definitions}
        pct = finite(frame.get('distance_pct'))
        if pct is None:
            return None
        nearest = min((d for d in self.definitions if d['kind']=='corner'),
                      key=lambda d:(d['primary_entry_pct']-pct)%1, default=None)
        for item in candidates:
            action = item.get('action')
            if item.get('window') not in active or action not in phrases or finite(item.get('entry_pct')) is None:
                continue
            if nearest is None or item.get('members',[None])[0] != nearest['members'][0]:
                continue
            if item['kind'] == 'corner' and any(item.get('members',[None])[0] in chain.get('members',[])
                    and applicable(chain,self._probe(chain)) for chain in result.get('chains',[])):
                continue
            token = digest([run,lap_key,item['window'],action,item['evidence_ids']])
            self.calls[token] = dict(item,_run=run,_lap=frame.get('lap_number'),_speech=phrases[action])
            if self.valid(token,now):
                return dict(item,token=token,speech=phrases[action])
            self.calls.pop(token,None)
        return None

    def mark_announced(self):
        if self.current:
            self.announced = {(self.current[2],self.current[1].get('lap_number'))}

    def supersedes(self, corner_id):
        if not self.current:
            return False
        result = self.results.get(scope(self.current[0],'LIVE'),{})
        active = {d['id'] for d in self.definitions}
        return any(item['window'] in active and corner_id in item.get('members',[]) and applicable(item,self._probe(item))
                   for kind in ('chains','technique') for item in result.get(kind,[]))

    def view(self):
        if self.current is None:
            return {'state':'WAITING','observations':0,'eligible':0}
        result = self.results.get(scope(self.current[0],'LIVE'), {'state':'LEARNING','observations':0,'eligible':0})
        return dict(public_view(result), dropped_windows=self.dropped, failure=self.failure)
