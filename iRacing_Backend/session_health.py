"""Small local session diagnostics, independent of voice and the 30 Hz reader.

The two-second sampler is not a playback monitor or a precise disconnect clock.
It writes on state changes and every 30 seconds while receiving fresh samples.
No names, questions, audio, settings or external calls are collected here.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter

logger = logging.getLogger(__name__)
MAX_RUNS, MAX_SAMPLES = 64, 128
POLL_SECONDS, HEARTBEAT_SECONDS = 2., 30.
IDENTITY_KEYS = ('game', 'track_name', 'track_path', 'track_config', 'car_name', 'car_path')
OVAL_IDENTITY_KEYS = ('track_id', 'track_type', 'track_direction', 'track_category',
                      'course_cautions', 'restarts', 'standing_start')
FEEDBACK_FIELDS = ('session_time', 'lap_number', 'distance_pct', 'on_track', 'is_replay',
                   'on_pit_road', 'session_state', 'session_flags', 'speed_mps', 'brake', 'throttle',
                   'steering_rad', 'steering_lock_ratio', 'workload_in_s', 'corner_in_s', 'speech_hold_s',
                   'car_left_right')


def scalar(value):
    return value if value is None or isinstance(value, bool) or type(value) in (int, float) and math.isfinite(value) else None


def feedback_context(live, run_id, now):
    """Caller holds live.lock. Latest state is explicitly NOT the original decision."""
    context = {'captured_at': now, 'scope': 'LATEST_AT_FEEDBACK', 'matched_run': bool(run_id and run_id == live.run_id)}
    radio = getattr(live, 'radio', None)
    if context['matched_run'] and radio is not None:
        context['sample'] = {key: scalar(radio.latest.get(key)) for key in FEEDBACK_FIELDS}
        context['sample_age_s'] = max(0., now - radio.last_sample) if radio.last_sample is not None else None
    return context


class ControlTrialDiagnostics:
    """Per-run GT3 race opportunities. Rechecking one opportunity counts each reason once."""
    def __init__(self):
        self.run = None
        self.dirty = {}
        self.revision = 0

    def observe(self, run, identity, session, now):
        self.now = now
        if self.run == run and (not session or self.session == session):
            return
        from race_wheelspin import MAPS
        from race_braking import BIAS_STEP, DRY_ABS
        if self.run != run:
            self.run = run
            self.identity = {k:v[:200] for k in IDENTITY_KEYS if isinstance(v := identity.get(k),str)}
            self.started = now
            self.counts = {k:Counter() for k in ('tc','abs','brake_bias')}
            self.seen = {k:{} for k in self.counts}
        self.session = session
        if session:
            self.identity['session_type'] = session
        car = identity.get('car_path') if identity.get('game')=='iracing' and str(session).lower()=='race' else None
        self.eligible = {'tc':car in MAPS, 'abs':car in DRY_ABS, 'brake_bias':car in BIAS_STEP}
        self._changed()

    def _changed(self):
        self.revision += 1
        self.dirty[self.run] = (self.revision, self.started, self.now, self.identity, self.eligible, self.counts)
        if len(self.dirty) > MAX_RUNS:
            del self.dirty[next(iter(self.dirty))]

    def mark(self, control, opportunity, stage):
        if self.run is None or not self.eligible[control]:
            return
        seen = self.seen[control]
        stages = seen.get(opportunity)
        if stages is None:
            if len(seen) >= 128:
                del seen[next(iter(seen))]
            stages = seen[opportunity] = set()
        if stage not in stages:
            stages.add(stage)
            self.counts[control][stage] += 1
            self._changed()

    def pending(self):
        snapshots = []
        for run, (revision, started, at, identity, eligible, counts) in self.dirty.items():
            summary = {'version': 1, 'eligible': dict(eligible), 'counts': {k:dict(v) for k,v in counts.items()}}
            snapshots.append((run, revision, started, at, dict(identity), summary))
        return snapshots

    def saved(self, run, revision):
        if run in self.dirty and self.dirty[run][0] == revision:
            del self.dirty[run]


class SessionHealthRecorder:
    def __init__(self, live, store, backend_version, clock=time.time):
        self.live, self.store, self.backend_version, self.clock = live, store, backend_version, clock
        self.stopping = threading.Event()
        self.summary_lock = threading.Lock()
        self.thread = None
        self.last_signature, self.last_at = None, None
        self.last_error = -math.inf

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stopping.clear()
        self.store.flush_control_trials = self.flush_control_trials
        self.thread = threading.Thread(target=self._loop, name='session-health', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                return
        self.run_once(stopping=True)
        self.store.flush_control_trials = None

    def _loop(self):
        while not self.stopping.is_set():
            self.run_once()
            self.stopping.wait(POLL_SECONDS)

    def run_once(self, *, stopping=False):
        self._flush_procedures()
        self.flush_control_trials()
        now = self.clock()
        live = self.live
        if not live.lock.acquire(timeout=.05):
            return False
        try:
            if not live.run_id or not live.samples:
                return False
            sample = live.samples[-1]
            age = max(0., now - sample['wall_time'])
            state = 'STALE' if live.state == 'CONNECTED' and age >= 2 else live.state
            if stopping:
                state = 'BACKEND_STOPPING'
            run_id = live.run_id
            shadow = getattr(live, 'banter', None)
            banter = shadow.expired(now) if shadow is not None else []
            identity = {key: value[:200] for key in IDENTITY_KEYS
                        if isinstance(value := live.identity.get(key), str)}
            if identity.get('game') == 'iracing':
                from oval_spotter import classify, POLICY_VERSION
                identity.update({key: value[:200] for key in OVAL_IDENTITY_KEYS
                                 if isinstance(value := live.identity.get(key), str)})
                identity['spotter_profile'], identity['spotter_profile_reason'] = classify(live.identity)
                identity['spotter_policy'] = POLICY_VERSION
            kind = live.radio.metadata.get('session_type')
            if isinstance(kind, str):
                identity['session_type'] = kind[:80]
            event = {'at': now, 'state': state, 'sample_age_s': round(age, 3),
                     'backend_version': self.backend_version,
                     **{key: scalar(sample.get(key)) for key in ('session_time', 'lap_number', 'on_track', 'is_replay', 'on_pit_road')}}
            if identity.get('spotter_profile') == 'ASPHALT_OVAL':
                from oval_spotter import OVAL_FIELDS
                event['oval'] = {key: scalar(sample.get(key)) for key in OVAL_FIELDS}
                traffic = getattr(live, 'traffic_log', None)
                event['traffic_dropped_rows'] = scalar(getattr(traffic, 'dropped_rows', None))
                event['traffic_failed'] = scalar(getattr(traffic, 'failed', None))
        finally:
            live.lock.release()
        if banter:
            try:
                self.store.record_banter(banter)
                with live.lock:
                    shadow.saved(banter)
            except Exception:
                logger.exception('Shadow-only banter diagnostics could not be saved')
        signature = (run_id, state, event['on_track'], event['is_replay'], event['on_pit_road'])
        if signature == self.last_signature and (state != 'CONNECTED' or self.last_at is not None and 0 <= now - self.last_at < HEARTBEAT_SECONDS):
            return False
        try:
            # No live lock, SDK read, network or main-reader I/O during persistence.
            self.store.record_session_health(run_id, identity, event)
        except Exception:
            if now - self.last_error >= 60:
                self.last_error = now
                logger.exception('Local session health could not be saved; live collection continues')
            return False
        self.last_signature, self.last_at = signature, now
        return True

    def _flush_procedures(self):
        # Also runs after disconnect, when live.run_id/samples have already been cleared.
        if not self.live.lock.acquire(timeout=.05):
            return
        try:
            shadow = getattr(self.live, 'oval_procedures', None)
            records = shadow.pending() if shadow is not None else []
        finally:
            self.live.lock.release()
        if records:
            try:
                self.store.record_oval_procedures(records)
                with self.live.lock:
                    shadow.saved(records)
            except Exception:
                if self.clock() - self.last_error >= 60:
                    self.last_error = self.clock()
                    logger.exception('Shadow-only oval observations could not be saved')

    def flush_control_trials(self):
        # Finish an in-flight sampler write before an export takes its snapshot.
        with self.summary_lock:
            if not self.live.lock.acquire(timeout=.05):
                return
            try:
                trials = getattr(getattr(self.live, 'radio', None), 'control_trials', None)
                summaries = trials.pending() if trials is not None else []
            finally:
                self.live.lock.release()
            for run, revision, started, at, identity, summary in summaries:
                try:
                    summary['backend_version'] = self.backend_version
                    self.store.record_session_health(run, identity, None, control_trials=(started, at, summary))
                    with self.live.lock:
                        trials.saved(run, revision)
                except Exception:
                    if self.clock() - self.last_error >= 60:
                        self.last_error = self.clock()
                        logger.exception('Setting recommendation counts could not be saved')
