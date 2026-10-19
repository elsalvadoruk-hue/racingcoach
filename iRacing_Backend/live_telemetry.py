"""Read-only iRacing SDK sampling and bounded, timestamped question context."""
from __future__ import annotations

from collections import deque
import ctypes
import logging
import math
import statistics
import os
import threading
import time
import uuid
from race_engineer import RaceEngineer, read_race
from spotter import Spotter
from race_radio import RaceRadio
from radio_policy import TrackSpeechWindows, HOLD_DEFAULT_S
import radio_phrases
from live_corners import LiveCornerCoach, call_seconds, CALL_EARLY_S
from live_controls import ControlAdvisor
from live_sectors import LapSectors, RivalSectors, place_name
from live_damage import DamageWatch, PitStopWatch, damage_parts, repair_parts
from live_pit import COLD_TYRE_S, PitKnowledge, PitLossWatch, TyreWatch, stall_estimate, tyre_advice

logger = logging.getLogger('iRacingLive')


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def session_identity(weekend, driver_info):
    drivers = driver_info.get('Drivers') or []
    driver = next((d for d in drivers if d.get('CarIdx') == driver_info.get('DriverCarIdx')), {})
    return {
        'session_id': str(weekend.get('SessionID') or ''),
        'subsession_id': str(weekend.get('SubSessionID') or ''),
        'track_id': str(weekend.get('TrackID') or ''),
        'track_path': weekend.get('TrackName') or '',
        'track_name': weekend.get('TrackDisplayName') or '',
        'track_config': weekend.get('TrackConfigName') or '',
        'car_id': str(driver.get('CarID') or ''),
        'car_path': driver.get('CarPath') or '',
        'car_name': driver.get('CarScreenName') or '',
    }


CHANNELS = {
    'session_time': 'SessionTime', 'session_num': 'SessionNum', 'lap_number': 'Lap',
    'distance_pct': 'LapDistPct', 'speed_mps': 'Speed', 'brake': 'Brake',
    'throttle': 'Throttle', 'steering_rad': 'SteeringWheelAngle', 'gear': 'Gear',
    'fuel_l': 'FuelLevel', 'brake_bias': 'dcBrakeBias', 'abs_setting': 'dcABS',
    'tc_setting': 'dcTractionControl', 'on_track': 'IsOnTrack', 'on_pit_road': 'OnPitRoad',
    'is_replay': 'IsReplayPlaying', 'track_temp_c': 'TrackTempCrew',
    'air_temp_c': 'AirTemp', 'incidents': 'PlayerCarMyIncidentCount',
    'car_left_right': 'CarLeftRight',
    'session_state': 'SessionState', 'session_flags': 'SessionFlags',
    'session_time_remaining_s': 'SessionTimeRemain', 'session_laps_remaining': 'SessionLapsRemainEx',
    'lap_completed': 'LapCompleted', 'lap_last_s': 'LapLastLapTime', 'lap_best_s': 'LapBestLapTime',
    'lap_current_s': 'LapCurrentLapTime',
    'position': 'PlayerCarPosition', 'class_position': 'PlayerCarClassPosition',
    'engine_warnings': 'EngineWarnings', 'pit_service_status': 'PlayerCarPitSvStatus',
    'pit_repair_left_s': 'PitRepairLeft', 'pit_optional_repair_left_s': 'PitOptRepairLeft',
    'tow_time_s': 'PlayerCarTowTime',
    'abs_active': 'BrakeABSactive', 'yaw_rate': 'YawRate',
    'track_wetness': 'TrackWetness', 'declared_wet': 'WeatherDeclaredWet', 'tire_compound': 'PlayerTireCompound',
    'pit_sv_fuel': 'PitSvFuel', 'pit_sv_flags': 'PitSvFlags',
}


TREAD_CHANNELS = tuple(f'{corner}wear{part}' for corner in ('LF', 'RF', 'LR', 'RR') for part in ('L', 'M', 'R'))


class LiveTelemetry:
    SAMPLE_RATE_HZ, BUFFER_SECONDS = 30, 180

    def __init__(self, sdk_factory=None, clock=time.time):
        self.clock = clock
        self.sdk_factory = sdk_factory
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.samples = deque(maxlen=self.SAMPLE_RATE_HZ * self.BUFFER_SECONDS)
        self.identity = {}
        self.run_id = None
        self.state = 'STOPPED'
        self.message = '실시간 수집을 시작하지 않았습니다.'
        self.hotkey_down = False
        self.hotkey_serial = 0
        self.hotkey_events = deque(maxlen=32)
        self.last_clock = None
        self.lap_fuel = None
        self.fuel_history = deque(maxlen=5)
        self.fuel_feedback = deque(maxlen=3)
        self.speech_windows = TrackSpeechWindows()
        self.speech_hold_s = HOLD_DEFAULT_S   # seconds before braking/steering in which no call starts; below zero is off
        self.pit_wall = RaceEngineer()
        self.spotter = Spotter()
        self.radio = RaceRadio()
        self.corners = LiveCornerCoach()
        self.controls = ControlAdvisor()
        self._corner_failure = False
        self._control_lap = None
        self._corner_call = (None, [])
        self._briefed = {}
        self._stint = {}
        self.lap_sectors = LapSectors()
        self.rival_sectors = RivalSectors()
        self.damage = DamageWatch()
        self.pit_stop = PitStopWatch()
        self.pit_knowledge = PitKnowledge()   # in memory until the backend gives it a file beside the database
        self.tyres = TyreWatch()
        self.pit_loss = PitLossWatch()
        self.pit_rule = None                  # 'CONCURRENT' or 'SEQUENTIAL', as the driver set it for this series
        self._tyres_fitted = None
        self.race_options = {'reserve_laps': .5, 'pit_loss_s': None, 'fuel_save_mode': 'finish'}
        self._logged_failure = -math.inf

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name='iracing-live-reader', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def ingest(self, identity, sample, race=None):
        """Also used by isolated tests. Never accepts telemetry through an HTTP endpoint."""
        sample = {key: (None if isinstance(value, float) and not math.isfinite(value) else value) for key, value in sample.items()}
        session_time = number(sample.get('session_time'))
        if session_time is None:
            return
        now = self.clock()
        with self.lock:
            identity = {**identity, 'session_num': sample.get('session_num')}
            changed = identity != self.identity or (self.last_clock is not None and session_time < self.last_clock - 2)
            if changed or self.run_id is None:
                self.samples.clear()
                self.lap_fuel = None
                self.fuel_history.clear()
                self.fuel_feedback.clear()
                self.speech_windows.reset()
                self.pit_wall.reset()
                self.spotter.reset()
                self.radio.reset()
                self.corners.reset()
                self.controls.reset()
                self.lap_sectors.reset()
                self.rival_sectors.reset()
                self.damage.reset()
                self.pit_stop.reset()
                self.tyres.reset()
                self.pit_loss.reset()
                self._tyres_fitted = None
                self._stint = {}
                self.run_id = str(uuid.uuid4())
                self.identity = identity
            self.last_clock = session_time
            if self.samples and self.samples[-1]['session_time'] == session_time:
                return
            self.speech_windows.ingest(sample)
            sample = {**sample, **self.speech_windows.view(sample), 'speech_hold_s': self.speech_hold_s}
            if race is not None:
                self.pit_wall.ingest(race, self.run_id, session_time)
            self._track_fuel(sample)
            self.spotter.ingest(sample, self.run_id, now)
            self.radio.ingest(sample, self.run_id, now, race)
            self._coach_corners(identity, sample, now)
            try:
                self._track_sectors(identity, sample, race, session_time)
            except Exception:
                if not self._briefed.get('sectors_failed'):
                    self._briefed['sectors_failed'] = True
                    logger.exception('Sector timing failed; continuing without it')
            self._brief_straight(sample, now)
            self.samples.append({**sample, 'session_time': session_time, 'wall_time': now, 'run_id': self.run_id})
            self.state = 'CONNECTED'
            self.message = 'iRacing 실시간 텔레메트리를 읽고 있습니다.'

    def _coach_corners(self, identity, sample, now):
        # Coaching is optional; a fault here must never reset the spotter.
        try:
            self.corners.ingest(sample, identity, now, self.radio.metadata.get('track_length_m'))
            pending = self.corners.focus
            if pending and not pending['announced']:
                if self._corner_call[0] is not pending:
                    # The whole call, and the same without its last sentence for a straight too short for all of it.
                    kind, values = ('corner_call_', {'turn': pending['call']}) if pending.get('call') else ('corner_focus_', {})
                    texts = [self.radio.wording(kind + cause, loss=pending['loss_s'], **values) for cause in dict.fromkeys((pending['cause'], 'time'))]
                    self._corner_call = (pending, texts)
                texts = self._corner_call[1]
                focus = self.corners.reminder(sample, sample.get('lap_last_s'), self.radio.metadata.get('track_length_m'),
                                              [call_seconds(text) for text in texts])
                if focus:
                    self.radio.emit('corner_focus', now, focus['text'], role='coach', ttl=CALL_EARLY_S, cooldown=20, group='corner_focus',
                                    condition=('corner_ahead', (focus['until_pct'], focus['margin_pct'])), speech_text=texts[focus['fits']])
            self.controls.ingest(sample)
            newest = self.controls.laps[-1]['lap'] if self.controls.laps else None
            if newest != self._control_lap:
                # Judge once per completed clean lap; only repeated instability is volunteered.
                self._control_lap = newest
                for item in self.controls.view()['advice']:
                    if item['call']:
                        self.radio.say(item['call'], now, role='coach', ttl=30, cooldown=600, delay=8, group='control_advice')
        except Exception:
            if not self._corner_failure:
                self._corner_failure = True
                logger.exception('Live corner comparison failed; continuing without it')

    # A straight this long is quiet enough for one status line; lines are this far apart at least.
    # Each further sentence of the same call needs three more seconds of straight.
    BRIEF_STRAIGHT_S, BRIEF_EVERY_S, BRIEF_SENTENCE_S = 8., 40., 3.
    BRIEF_REPEAT_S = {'gap_ahead': 90., 'gap_behind': 90., 'position': 300., 'time': 600., 'leader_lap': 600., 'ahead_lap': 300.,
                      'rival_ahead': 180., 'rival_behind': 180., 'catch': 600., 'defend': 300., 'theoretical': 600.}
    QUALI_BRIEFS = ('corner', 'delta', 'theoretical')   # what helps a qualifying lap; other cars are never mentioned

    def _track_sectors(self, identity, sample, race, session_time):
        """Own sectors at 30Hz, every car's at the 1Hz race data, and the laps of the stint in progress."""
        self.lap_sectors.ingest(sample, identity)
        self.damage.ingest(sample)
        # What this session's own stops show is kept for the next ones, here and in later sessions.
        measured = len(self.pit_stop.rates)
        wear = self.tyres.ingest(sample)
        if wear:
            self.pit_knowledge.learn(identity, wear_pct_per_lap=wear)
        laps = [item['time'] for item in self.radio.laps]
        transit = self.pit_loss.ingest(sample, statistics.median(laps) if len(laps) >= 3 else None)
        if transit:
            changed, self._tyres_fitted = self.tyres.fitted_lap != self._tyres_fitted, self.tyres.fitted_lap
            self.pit_knowledge.learn(identity, transit_loss_s=transit - (COLD_TYRE_S if changed else 0.))
        # Stopped in the stall with a repair on offer: say what it costs while the driver can still skip it.
        stop = self.pit_stop.ingest(sample, self.pit_wall.race.get('tank_capacity_l'))
        if len(self.pit_stop.rates) > measured:
            self.pit_knowledge.learn(identity, fuel_rate_lps=self.pit_stop.rates[-1])
        if stop:
            # Laps still to drive after this stop decide whether a repair pays back.
            to_go = self.pit_wall.view(sample).get('finish_laps_to_go')
            parts = [self.radio.wording(key, **values) for key, values in repair_parts(stop, self.damage.result, to_go)]
            self.radio.emit('repair_check', self.clock(), ' '.join(parts), role='race', priority=2, ttl=20, cooldown=0,
                            group='repair_check', speech_text=' '.join(parts), speech_parts=parts)
        if race is not None:
            self.rival_sectors.ingest(race.get('cars'), session_time)
        newest = self.radio.laps[-1] if self.radio.laps else None
        if newest and newest['lap'] != self._stint.get('lap'):
            self._stint['lap'] = newest['lap']
            self._stint.setdefault('times', []).append(newest['time'])
            # The first clean lap after a contact: say what it shows against the laps before.
            found = self.damage.lap_done(newest['time'])
            if found:
                parts = [self.radio.wording(key, **values) for key, values in damage_parts(found)]
                self.radio.emit('damage_check', self.clock(), ' '.join(parts), role='race', ttl=60, cooldown=0,
                                group='damage_check', speech_text=' '.join(parts), speech_parts=parts)
        pit = sample.get('on_pit_road')
        if pit is True and self._stint.get('pit') is False:
            times = self._stint.get('times') or []
            # The stint that just ended is summed up once the car is back on track.
            self._stint['average'] = sum(times) / len(times) if len(times) >= 3 else None
            self._stint['times'] = []
        if pit is False and self._stint.get('pit') is True:
            self.damage.result = None   # whatever the stop repaired, the old comparison is over
        if pit in (True, False):
            self._stint['pit'] = pit

    def _brief_options(self, sample, ahead_s):
        """Every line worth saying now: (name, role, groups). A group is one or more sentences that belong together."""
        say, lap_number = self.radio.wording, sample.get('lap_number')
        lap_sentence = lambda seconds: radio_phrases.build('lap_time_read', 0, lap=seconds)['text']
        options = []
        review = self.corners.review
        if review and lap_number == review['lap'] + 1 and review['lap'] != self._briefed.get('corner_lap'):
            # Only corners the driver confirmed by number or name are named aloud.
            losses = [c for c in review.get('losses') or [] if c.get('call') and .1 <= c['delta_s'] < 10][:2]
            groups = [[('brief_corner_loss' if i == 0 else 'brief_corner_also', {'turn': c['call'], 'loss': c['delta_s']})] for i, c in enumerate(losses)]
            gain = review.get('best_gain')
            if gain and gain.get('call') and .1 <= abs(gain['delta_s']) < 10:
                groups.append([('brief_best_corner', {'turn': gain['call']}), ('brief_gain', {'loss': abs(gain['delta_s'])})])
            if groups:
                options.append(('corner', 'coach', groups))
        delta = self.lap_sectors.delta()
        if (delta and delta['lap'] == lap_number and .25 <= delta['at_pct'] <= .9 and .1 <= abs(delta['delta_s']) < 10
                and self._briefed.get('delta_lap') != lap_number):
            options.append(('delta', 'coach', [[('delta_ahead' if delta['delta_s'] < 0 else 'delta_behind', {'loss': abs(delta['delta_s'])})]]))
        best = self.lap_sectors.theoretical()
        if best and best['gain_s'] >= .2 and best['lap_s'] < self._briefed.get('theoretical_lap_s', math.inf) - .05:
            options.append(('theoretical', 'spotter', [[('label_theoretical', {}), lap_sentence(best['lap_s'])],
                                                       [('theoretical_gain', {'loss': best['gain_s']})]]))
        if self._stint.get('average'):
            options.append(('stint', 'spotter', [[('label_stint_avg', {}), lap_sentence(self._stint['average'])]]))
        view = self.pit_wall.view(sample)
        if not view['active']:
            return options, view
        own_idx = self.pit_wall.race.get('player_idx')
        definitions = getattr(self.corners, 'definitions', [])
        for side, other in (('ahead', 'behind'), ('behind', 'ahead')):
            car = view.get(side)
            gap = car.get('gap_estimate_s') if car else None
            if gap is None or car.get('on_pit_road') or car.get('track_surface') in (-1, 0):
                continue
            near = [(f'brief_gap_{side}', {'gap': gap})] if .1 <= gap < 9.95 else None
            if near and gap < 5:
                # Where the two cars differ most, from at least two timed laps of each through that stretch.
                sectors = self.rival_sectors.compare(own_idx, car['idx'])
                faster = min(sectors, key=lambda r: r['delta_s'], default=None)
                slower = max(sectors, key=lambda r: r['delta_s'], default=None)
                found = []
                if faster and faster['delta_s'] <= -.3:
                    found.append(('faster', [(f'rival_{side}_faster_at', {'place': place_name(faster['middle_pct'], faster['sector'], definitions)}),
                                             ('by_faster', {'loss': -faster['delta_s']})]))
                if slower and slower['delta_s'] >= .3:
                    found.append(('slower', [(f'rival_{side}_slower_at', {'place': place_name(slower['middle_pct'], slower['sector'], definitions)}),
                                             ('by_slower', {'loss': slower['delta_s']})]))
                # What was not said last time comes first.
                found.sort(key=lambda item: item[0] == self._briefed.get(f'rival_{side}_told'))
                if found:
                    options.append((f'rival_{side}', 'race', [near, *(group for _, group in found)]))
            change, line = car.get('gap_change_s_per_lap'), car.get('gap_at_line_s')
            closing = car.get('gap_trend') == 'closing' and change is not None and change < 0 and line is not None
            if side == 'ahead' and gap >= 3:
                groups = [near or [('ahead_far', {'sec': gap})]] if gap <= 180 else []
                remaining = view.get('remaining_laps')
                need = gap / remaining if remaining and remaining >= 2 else None
                if closing:
                    groups.append([('catch_rate', {'loss': -change})])
                    if math.ceil(line / -change) <= 30:
                        groups.append([('catch_in', {'laps': math.ceil(line / -change)})])
                elif need is not None and .05 <= need <= 3:
                    groups.append([('catch_need', {'loss': need})])
                if len(groups) > 1:
                    options.append(('catch', 'race', groups))
            if side == 'behind' and closing and near:
                groups = [near, [('lose_rate', {'loss': -change})]]
                if math.ceil(line / -change) <= 30:
                    groups.append([('caught_in', {'laps': math.ceil(line / -change)})])
                options.append(('defend', 'race', groups))
            if near:   # the plain gap takes its turn after the lines that say more
                options.append((f'gap_{side}', 'race', [near]))
        if view.get('class_position'):
            options.append(('position', 'spotter', [[('position_now', {'pos': view['class_position']})]]))
        left = sample.get('session_time_remaining_s')
        if type(left) in (int, float) and 11 * 60 <= left < 60 * 60:   # the last ten minutes have their own calls
            options.append(('time', 'spotter', [[('time_left', {'min': left // 60})]]))
        usable = lambda seconds: type(seconds) in (int, float) and 20 < seconds < 1800
        leader = next((c for c in view.get('class_cars') or [] if c.get('class_position') == 1 and c.get('idx') != own_idx), None)
        if leader and usable(leader.get('last_lap_s')):
            options.append(('leader_lap', 'spotter', [[('label_leader_lap', {}), lap_sentence(leader['last_lap_s'])]]))
        if view.get('ahead') and view['ahead'].get('idx') != (leader or {}).get('idx') and usable(view['ahead'].get('last_lap_s')):
            options.append(('ahead_lap', 'spotter', [[('label_ahead_lap', {}), lap_sentence(view['ahead']['last_lap_s'])]]))
        return options, view

    def _brief_straight(self, sample, now):
        """One call of measured status on a long straight when the radio is otherwise empty."""
        try:
            ahead = sample.get('workload_in_s')
            if (type(ahead) not in (int, float) or ahead < self.BRIEF_STRAIGHT_S
                    or now - self._briefed.get('at', -math.inf) < self.BRIEF_EVERY_S
                    or sample.get('on_pit_road') is not False or sample.get('session_state') != 4
                    or int(sample.get('session_flags') or 0) & 0xC119
                    or any(self.radio.valid(event, now) for event in self.radio.pending)):
                return
            options, _ = self._brief_options(sample, ahead)
            if self.radio.qualifying:
                options = [o for o in options if o[0] in self.QUALI_BRIEFS]
            fits = lambda count: ahead >= self.BRIEF_STRAIGHT_S + self.BRIEF_SENTENCE_S * (count - 1)
            options = [o for o in options if now - self._briefed.get(o[0], -math.inf) >= self.BRIEF_REPEAT_S.get(o[0], 0)
                       and fits(len(o[2][0]))]
            if not options:
                return
            # The line that has waited longest; in a tie the earlier one in the list (the lap's own review first).
            name, role, groups = min(options, key=lambda o: self._briefed.get(o[0], -math.inf))
            chosen = []
            for group in groups:   # as many whole groups as this straight has room for
                if chosen and not fits(len(chosen) + len(group)):
                    break
                chosen.extend(group)
            parts = [part if isinstance(part, str) else self.radio.wording(part[0], **part[1]) for part in chosen]
            text = ' '.join(parts)
            self.radio.emit('straight_brief', now, text, role=role, ttl=4, cooldown=0, group='straight_brief',
                            condition=('straight', 5. + self.BRIEF_SENTENCE_S * (len(parts) - 1)), speech_text=text, speech_parts=parts)
            self._briefed.update({'at': now, name: now})
            keys = [part[0] for part in chosen if not isinstance(part, str)]
            if name == 'corner':
                self._briefed['corner_lap'] = self.corners.review['lap']
            elif name == 'delta':
                self._briefed['delta_lap'] = sample.get('lap_number')
            elif name == 'theoretical':
                self._briefed['theoretical_lap_s'] = self.lap_sectors.theoretical()['lap_s']
            elif name == 'stint':
                self._stint['average'] = None
            elif name.startswith('rival_'):
                told = [key for key in keys if key.endswith(('_faster_at', '_slower_at'))]
                self._briefed[f'{name}_told'] = 'faster' if told and told[-1].endswith('_faster_at') else 'slower'
        except Exception:
            if not self._briefed.get('failed'):
                self._briefed['failed'] = True
                logger.exception('Straight briefing failed; continuing without it')

    def context(self):
        with self.lock:
            now = self.clock()
            latest = dict(self.samples[-1]) if self.samples else None
            fresh = latest is not None and now - latest['wall_time'] < 2 and self.state == 'CONNECTED'
            if not fresh:
                return {'connected': False, 'identity': {}, 'snapshot': None, 'recent': [], 'captured_at': now}
            # Five samples/sec for twenty seconds is sufficient to anchor a comment;
            # the full live buffer never leaves this process or enters the database.
            # Only the newest twenty seconds are needed; don't walk the whole buffer.
            window = []
            for point in reversed(self.samples):
                if point['wall_time'] < now - 20:
                    break
                window.append(point)
            recent, last_time = [], -math.inf
            for point in reversed(window):
                if point['session_time'] - last_time >= .19:
                    recent.append(dict(point))
                    last_time = point['session_time']
            if not recent or recent[-1]['session_time'] != latest['session_time']:
                recent.append(latest)
            return {'connected': True, 'identity': dict(self.identity), 'snapshot': latest,
                    'recent': recent, 'captured_at': now,
                    'fuel_l_per_lap': max(self.fuel_history) if self.fuel_history else None,
                    'fuel_observed_laps': len(self.fuel_history),
                    'fuel_feedback': list(self.fuel_feedback),
                    'corner_coach': self.corners.view(),
                    'controls': self.controls.view(),
                    'radio': {'briefing': self.radio.briefing(), 'capabilities': self.radio.capabilities(),
                              'pit_speed_limit_kph': self.radio.metadata.get('pit_speed_limit_kph'),
                              'recent_laps': list(self.radio.laps),
                              'history': list(self.radio.history)},
                    'pit_wall': {**self._pit_view(latest, max(self.fuel_history) if self.fuel_history else None, len(self.fuel_history), **self.race_options),
                                 'fuel_last_lap': dict(self.fuel_feedback[-1]) if self.fuel_feedback else None}}

    def _pit_view(self, snapshot, fuel_use=None, fuel_laps=0, reserve_laps=.5, pit_loss_s=None, fuel_save_mode='finish'):
        """The pit-wall view with what the stops driven so far add: the pit loss when the driver
        entered none, and the tyre plan for the coming stop."""
        view = self.pit_wall.view(snapshot, fuel_use, fuel_laps, reserve_laps, pit_loss_s, fuel_save_mode)
        try:
            facts = self._pit_facts(view, snapshot)
        except Exception:
            facts = {}
            if not self._briefed.get('pit_facts_failed'):
                self._briefed['pit_facts_failed'] = True
                logger.exception('Pit facts failed; continuing without them')
        if pit_loss_s is None and facts.get('pit_loss_auto_s'):
            view = self.pit_wall.view(snapshot, fuel_use, fuel_laps, reserve_laps, facts['pit_loss_auto_s'], fuel_save_mode)
        return {**view, **facts, 'pit_loss_source': 'INPUT' if pit_loss_s else 'AUTO' if facts.get('pit_loss_auto_s') else None}

    def _pit_facts(self, view, snapshot):
        known_track, known_car = self.pit_knowledge.track(self.identity), self.pit_knowledge.car(self.identity)
        fuel_rate = self.pit_stop.fuel_rate() or known_car.get('fuel_rate_lps')
        wear = self.tyres.rate or known_car.get('wear_pct_per_lap')
        transit, fill = known_track.get('transit_loss_s'), view.get('fuel_next_stop_l')
        race = self.pit_wall.race
        race_s = race.get('session_duration_s')
        if type(race_s) not in (int, float) or not 0 < race_s < 86400:
            total, pace = race.get('total_laps'), view.get('pace_s')
            race_s = total * pace if type(total) in (int, float) and 0 < total < 32767 and pace else None
        flags = snapshot.get('pit_sv_flags')
        ticked = None if type(flags) not in (int, float) else True if int(flags) & 0xF == 0xF else False if int(flags) & 0xF == 0 else None
        decision, parts = None, []
        available, remaining, use = view.get('fuel_laps'), view.get('remaining_laps'), view.get('fuel_l_per_lap')
        on_set = self.tyres.laps_on_set(snapshot.get('lap_number'))
        if view.get('active') and available is not None and (view.get('fuel_finish_shortfall_l') or 0) > .1:
            capacity = view.get('tank_capacity_l')
            reach = capacity / use if capacity and use else None
            after = max(0., remaining - available) if remaining is not None else None
            next_stint = min(after, reach) if after is not None and reach else after
            # Whole laps, so that the plan does not change wording from one poll to the next.
            decision, parts = tyre_advice(race_s, self.pit_rule, fill, fuel_rate, wear,
                                          on_set + math.floor(available) if on_set is not None else None,
                                          math.ceil(next_stint) if next_stint is not None else None, ticked, bool(snapshot.get('declared_wet')),
                                          self.tyres.stints, view.get('pace_s'))
        change = decision == 'change' or (decision != 'keep' and ticked is True)
        stall = stall_estimate(fill, fuel_rate, change, self.pit_rule) if fill is not None else None
        auto = transit + stall + (COLD_TYRE_S if change else 0.) if transit and stall is not None else None
        return {'pit_transit_loss_s': transit, 'pit_stall_estimate_s': round(stall, 1) if stall is not None else None,
                'pit_loss_auto_s': round(auto, 1) if auto is not None else None, 'fuel_rate_lps': fuel_rate,
                'tyre_wear_pct_per_lap': wear, 'tyre_laps_on_set': on_set, 'tyre_stints_on_set': self.tyres.stints, 'tyre_decision': decision,
                'tyre_parts': [[key, values] for key, values in parts], 'pit_rule': self.pit_rule}

    def race_view(self, reserve_laps=.5, pit_loss_s=None, fuel_save_mode='finish'):
        with self.lock:
            context = self.context()
            if not context['connected']:
                return None
            return {**self._pit_view(context['snapshot'], context.get('fuel_l_per_lap'),
                                     context.get('fuel_observed_laps', 0), reserve_laps, pit_loss_s, fuel_save_mode),
                    'fuel_last_lap': dict(self.fuel_feedback[-1]) if self.fuel_feedback else None}

    def spotter_status(self, after=0):
        # The fast event path does not copy the 180-second coaching buffer.
        with self.lock:
            view = self.spotter.view(self.clock(), after, connected=self.state == 'CONNECTED')
            view['radio'] = self.radio.view(self.clock()) if self.state == 'CONNECTED' else {'connected': False, 'events': [], 'history': [], 'capabilities': {}}
            view['settings'] = self.spotter_settings()
            return view

    def spotter_settings(self):
        return {'repeat_seconds': self.spotter.repeat_seconds, 'clear_delay': self.spotter.clear_delay,
                'traffic_enabled': self.radio.traffic_enabled, 'pit_box_enabled': self.radio.pit_box_enabled}

    def configure_spotter(self, settings):
        with self.lock:
            self.spotter.repeat_seconds = settings['repeat_seconds']
            self.spotter.clear_delay = settings['clear_delay']
            self.radio.traffic_enabled = settings['traffic_enabled']
            self.radio.pit_box_enabled = settings['pit_box_enabled']
            return self.spotter_settings()

    def _track_fuel(self, sample):
        pct, fuel = number(sample.get('distance_pct')), number(sample.get('fuel_l'))
        lap = sample.get('lap_number')
        if pct is None or fuel is None or lap is None:
            self.lap_fuel = None
            return
        previous = self.lap_fuel
        usable = (sample.get('on_track') is True and sample.get('is_replay') is False
                  and sample.get('on_pit_road') is False)
        continuous = (previous is not None and 0 < sample['session_time'] - previous['at'] <= 2)
        if previous and fuel > previous['fuel'] + .2:
            self.fuel_history.clear()
            self.fuel_feedback.clear()
        if previous and lap != previous['lap']:
            use = previous['start_fuel'] - fuel
            if (previous['clean'] and usable and continuous and sample.get('incidents') == previous['incidents']
                    and previous['distance'] >= .94 and previous['pct'] >= .95 and pct <= .03
                    and lap == previous['lap'] + 1 and .05 < use < previous['start_fuel']):
                self.fuel_history.append(round(use, 3))
                self.fuel_feedback.append({'lap': previous['lap'], 'use_l': round(use, 3),
                    'target_l': previous['target_l'], 'mode': previous['mode'], 'reserve_laps': previous['reserve_laps'], 'at': sample['session_time']})
            previous = None
        if previous is None:
            view = self.pit_wall.view(sample, max(self.fuel_history) if self.fuel_history else None,
                                      len(self.fuel_history), **self.race_options)
            self.lap_fuel = {'lap': lap, 'pct': pct, 'distance': 0., 'start_fuel': fuel, 'fuel': fuel,
                             'incidents': sample.get('incidents'), 'clean': pct <= .03 and usable,
                             'at': sample['session_time'], 'target_l': view.get('fuel_save_target_l_per_lap'),
                             'reserve_laps': self.race_options.get('reserve_laps', .5),
                             'mode': self.race_options.get('fuel_save_mode', 'finish')}
        else:
            delta = pct - previous['pct']
            if (not continuous or not usable or not 0 <= delta <= .1 or fuel > previous['fuel'] + .2
                    or sample.get('incidents') != previous['incidents'] or int(sample.get('session_flags') or 0) & 0xC108):
                previous['clean'] = False
            previous['distance'] += max(0, delta)
            previous['pct'], previous['fuel'] = pct, fuel
            previous['at'] = sample['session_time']

    def status(self, after=0):
        context = self.context()
        with self.lock:
            return {**{k: v for k, v in context.items() if k != 'recent'},
                    'state': self.state if context['connected'] else ('STALE' if self.state == 'CONNECTED' else self.state),
                    'message': self.message if context['connected'] else 'iRacing 실시간 데이터 대기 · 저장된 IBT 분석은 계속 사용할 수 있습니다.' if self.state == 'CONNECTED' else self.message,
                    'hotkey': 'F8', 'hotkey_supported': os.name == 'nt', 'hotkey_down': self.hotkey_down,
                    'hotkey_serial': self.hotkey_serial, 'hotkey_events': [e for e in self.hotkey_events if e['serial'] > after],
                    'buffer_seconds': self.BUFFER_SECONDS, 'sample_rate_target_hz': self.SAMPLE_RATE_HZ}

    def hotkey_state(self, after=0):
        # Push-to-talk edges only; the event socket must not rebuild the coaching context.
        with self.lock:
            return {'hotkey_serial': self.hotkey_serial,
                    'hotkey_events': [e for e in self.hotkey_events if e['serial'] > after]}

    def _poll_hotkey(self):
        if os.name != 'nt':
            return
        pressed = bool(ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000)
        with self.lock:
            if pressed != self.hotkey_down:
                self.hotkey_down = pressed
                self.hotkey_serial += 1
                self.hotkey_events.append({'serial': self.hotkey_serial, 'down': pressed,
                                           'at': self.clock(), 'context': self.context() if pressed else None})

    def _loop(self):
        sdk = None
        race_read_at = -math.inf
        race_session = None
        try:
            if self.sdk_factory is None:
                if os.name != 'nt':
                    raise RuntimeError('실시간 SDK는 Windows에서 사용할 수 있습니다.')
                import irsdk
                self.sdk_factory = irsdk.IRSDK
            sdk = self.sdk_factory()
        except (ImportError, RuntimeError) as exc:
            self.state = 'UNAVAILABLE'
            self.message = str(exc) if isinstance(exc, RuntimeError) else '실시간 패키지 설치 필요 · 음성코치_설치.bat를 실행하세요.'
        while not self.stop_event.is_set():
            self._poll_hotkey()
            if sdk is None:
                self.stop_event.wait(.05)
                continue
            try:
                if not sdk.is_initialized:
                    self.state = 'WAITING'
                    self.message = 'iRacing을 실행하고 차량에 탑승하면 연결됩니다.'
                    if not sdk.startup():
                        self.stop_event.wait(1)
                        continue
                if not sdk.is_connected:
                    with self.lock:
                        self.spotter.reset()
                        self.radio.reset()
                    sdk.shutdown()
                    self.state = 'WAITING'
                    self.stop_event.wait(.5)
                    continue
                sdk.freeze_var_buffer_latest()
                try:
                    names = set(sdk.var_headers_names)
                    sample = {key: sdk[name] if name in names else None for key, name in CHANNELS.items()}
                    # Least tread left on any part of any tyre; the sim only refreshes it in the pit stall.
                    sample['tyre_tread_min'] = min(sdk[name] for name in TREAD_CHANNELS) if names.issuperset(TREAD_CHANNELS) else None
                    # Replay/spectator data must never masquerade as the driver's current car.
                    if sample.get('is_replay') or not sample.get('on_track'):
                        with self.lock:
                            self.spotter.reset()
                            self.radio.reset()
                        self.state = 'WAITING'
                        self.message = '실제 차량 탑승 대기 · 리플레이는 실시간 코칭에 사용하지 않습니다.'
                    else:
                        identity = session_identity(sdk['WeekendInfo'] or {}, sdk['DriverInfo'] or {})
                        race = None
                        race_key = (identity.get('subsession_id'), sample.get('session_num'))
                        if self.clock() - race_read_at >= 1 or race_key != race_session:
                            race = read_race(sdk, names, sample.get('session_num'))
                            race_read_at, race_session = self.clock(), race_key
                        self.ingest(identity, sample, race)
                finally:
                    sdk.unfreeze_var_buffer_latest()
            except Exception:
                # A logic error here looks like a reconnect to the driver; keep the cause.
                failure = time.monotonic()
                if failure - self._logged_failure >= 10:
                    self._logged_failure = failure
                    logger.exception('Live telemetry tick failed; resetting the SDK connection')
                with self.lock:
                    self.spotter.reset()
                    self.radio.reset()
                self.state = 'WAITING'
                self.message = '실시간 데이터 연결을 다시 확인하고 있습니다.'
                try:
                    sdk.shutdown()
                except Exception:
                    pass
                self.stop_event.wait(1)
            self.stop_event.wait(1 / 30)
        if sdk is not None:
            sdk.shutdown()
        self.state = 'STOPPED'


def live_facts(context):
    """Measurements with explicit absence, and completed-lap fuel use only."""
    snapshot = context.get('snapshot') or {}
    facts = {k: snapshot.get(k) for k in ('fuel_l', 'gear', 'brake_bias', 'abs_setting', 'tc_setting', 'air_temp_c', 'track_temp_c')}
    speed = number(snapshot.get('speed_mps'))
    facts['speed_kph'] = None if speed is None else round(speed * 3.6, 1)
    facts['fuel_l_per_lap'] = context.get('fuel_l_per_lap')
    facts['fuel_observed_laps'] = context.get('fuel_observed_laps', 0)
    return facts
