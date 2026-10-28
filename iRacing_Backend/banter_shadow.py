"""Bounded in-memory shadow proposals; no playback, synthesis, I/O or race commands."""
from collections import deque
import math

from banter_catalog import CATALOG, ACTIVE_TRIGGERS, VERSION
from radio_policy import quiet_window, speech_seconds

POLICY = 'banter-shadow-v1'
FIELDS = ('on_track', 'is_replay', 'on_pit_road', 'car_left_right', 'session_state', 'session_flags',
          'pace_mode', 'speed_mps', 'brake', 'steering_rad', 'throttle', 'workload_in_s', 'speech_window_s',
          'lap_completed', 'lap_last_s', 'lap_best_s', 'class_position', 'incidents', 'pit_service_status')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def gate(sample, client):
    reasons = []
    for key, expected, blocked in [('on_track', True, 'NOT_DRIVING'), ('is_replay', False, 'REPLAY'),
                                    ('on_pit_road', False, 'PIT_ROAD')]:
        value = sample.get(key)
        if type(value) is not bool:
            reasons.append('UNKNOWN_' + key.upper())
        elif value is not expected:
            reasons.append(blocked)
    side = sample.get('car_left_right')
    if not finite(side) or side not in range(7):
        reasons.append('UNKNOWN_SIDE')
    elif side != 1:
        reasons.append('OVERLAP_OR_OFF')
    state, flags, pace = (sample.get(k) for k in ('session_state', 'session_flags', 'pace_mode'))
    if not finite(state) or state != 4:
        reasons.append('RACE_PHASE_UNCONFIRMED')
    # Only explicitly known no-flag/green is a shadow opportunity. Every other bit defers.
    if not finite(flags) or int(flags) != flags or flags < 0 or int(flags) & ~4:
        reasons.append('FLAGS_UNCONFIRMED_OR_ACTIVE')
    if not finite(pace) or pace != 4:
        reasons.append('PACING_UNCONFIRMED_OR_ACTIVE')
    if not finite(sample.get('speed_mps')) or sample['speed_mps'] < 20 or not quiet_window(sample):
        reasons.append('WORKLOAD_OR_SPEED')
    for key in ('audio_busy', 'engineer_busy', 'pending_audio', 'muted'):
        if type(client.get(key)) is not bool:
            reasons.append('UNKNOWN_' + key.upper())
        elif client[key]:
            reasons.append(key.upper())
    return reasons


def choose(record, history):
    """Persisted virtual selections are distinct from actual heard phrases."""
    result = {**record, 'selected': None, 'mode': 'SHADOW', 'policy': POLICY, 'catalog': VERSION}
    if record['trigger'] not in ACTIVE_TRIGGERS:
        result.update(status='DEFERRED', reasons=['UNSUPPORTED_TRIGGER'])
        return result
    if result['reasons']:
        result['status'] = 'DEFERRED'
        return result
    at = record['at']
    recent = [r for r in history if r.get('selected')]
    # Clock reversal never silently removes a cooldown.
    if recent and (not finite(at) or at - recent[0]['at'] < 600):
        result.update(status='DEFERRED', reasons=['SHADOW_COOLDOWN'])
        return result
    used_ids = {r['selected']['id'] for r in recent[:8]}
    used_motifs = {r['selected']['motif'] for r in recent[:4]}
    options = [p for p in CATALOG if p['trigger'] == record['trigger'] and p['id'] not in used_ids and p['motif'] not in used_motifs]
    if not options:
        result.update(status='DEFERRED', reasons=['CATALOG_EXHAUSTED'])
        return result
    selected = options[0]
    window = record['sample'].get('speech_window_s')
    ahead = record['sample'].get('workload_in_s')
    budget = speech_seconds(selected['text']) + 2
    if not finite(window) or not finite(ahead) or min(window, ahead) < budget:
        result.update(status='DEFERRED', reasons=['UNKNOWN_OR_SHORT_SPEECH_WINDOW'])
        return result
    result.update(status='ELIGIBLE_SHADOW', selected=selected, budget_s=budget)
    return result


class BanterShadow:
    CLEAR_S, OVERLAP_S, STABLE_PHASE_S, TTL_S = 3., 5., 10., 5.

    def __init__(self):
        self.serial = 0
        self.reset()

    def reset(self):
        self.run_id = None
        self.latest = None
        self.last_at = self.last_time = self.overlap_at = self.clear_at = self.phase_at = None
        self.multi = False
        self.trigger = None
        self.proposals = deque(maxlen=16)
        self.observed = set()
        self.pending = {}
        self.previous = None
        self.quiet_at = self.stint_lap = self.stint_incidents = None
        self.position_target = self.position_at = None

    def propose(self, trigger, sample, run_id, now, reasons=(), evidence=None):
        self.serial += 1
        t = sample['session_time']
        self.proposals.append({'candidate_id': f'{run_id}:banter:{self.serial}', 'run_id': run_id,
            'trigger': trigger, 'session_time': t, 'at': now, 'evidence': evidence or {},
            'trigger_reasons': list(reasons),
            'stable_phase': self.phase_at is not None and t-self.phase_at >= self.STABLE_PHASE_S,
            'sample': dict(self.latest)})

    def other_triggers(self, sample, run_id, now, valid, phase):
        """Observed changes, never inferred passes, clean laps, lifetime PBs or finishes."""
        before, t = self.previous, sample['session_time']
        if before:
            old_flags, flags = before.get('session_flags'), sample.get('session_flags')
            if finite(flags) and finite(old_flags) and int(flags) == flags and int(old_flags) == old_flags:
                if int(flags) & 1 and not int(old_flags) & 1:
                    self.propose('race_finish', sample, run_id, now, ('PLAYER_FINISH_UNCONFIRMED',),
                                 {'before_flags':old_flags, 'after_flags':flags})
            if before.get('pit_service_status') == 1 and sample.get('pit_service_status') == 2:
                self.propose('pit_service_done', sample, run_id, now, ('SERVICE_SIGNAL_ONLY',),
                             {'before':1, 'after':2})
        if not valid or not phase or sample.get('car_left_right') != 1:
            self.quiet_at = self.stint_lap = self.stint_incidents = None
            self.position_target = self.position_at = None
            return
        self.quiet_at = t if self.quiet_at is None else self.quiet_at
        if t-self.quiet_at >= 120:
            self.propose('quiet_running', sample, run_id, now, evidence={'continuous_clear_s':t-self.quiet_at})
            self.quiet_at = t
        lap, incidents = sample.get('lap_completed'), sample.get('incidents')
        counter = lambda v: type(v) is int and v >= 0
        if not counter(lap) or not counter(incidents):
            self.stint_lap = self.stint_incidents = None
        elif self.stint_lap is None or lap < self.stint_lap or incidents != self.stint_incidents:
            self.stint_lap, self.stint_incidents = lap, incidents
        elif lap-self.stint_lap >= 3:
            self.propose('clean_stint', sample, run_id, now, ('INCIDENT_OBSERVATION_ONLY',),
                         {'lap_from':self.stint_lap, 'lap_to':lap, 'incidents':incidents})
            self.stint_lap = lap
        if before:
            old_best, best, last = before.get('lap_best_s'), sample.get('lap_best_s'), sample.get('lap_last_s')
            old_lap = before.get('lap_completed')
            if (counter(lap) and counter(old_lap) and lap == old_lap+1 and all(finite(v) and v > 0 for v in (old_best,best,last))
                    and best < old_best and abs(last-best) < .002):
                self.propose('personal_best', sample, run_id, now, ('SESSION_BEST_ONLY','LAP_VALIDITY_UNCONFIRMED'),
                             {'previous_best_s':old_best, 'best_s':best, 'last_s':last, 'lap':lap})
            old_position, position = before.get('class_position'), sample.get('class_position')
            if counter(position) and counter(old_position) and 0 < position < old_position:
                self.position_target, self.position_at = position, t
            elif position != self.position_target:
                self.position_target = self.position_at = None
            if self.position_at is not None and t-self.position_at >= 3:
                self.propose('position_gain', sample, run_id, now, ('OVERTAKE_UNCONFIRMED',),
                             {'class_position':position, 'stable_s':t-self.position_at})
                self.position_target = self.position_at = None

    def ingest(self, sample, run_id, now, enabled):
        t = sample.get('session_time')
        if not enabled or not finite(t) or not finite(now):
            if self.run_id is not None or self.proposals:
                self.reset()
            return
        if (run_id != self.run_id or self.last_time is None or not 0 < t - self.last_time <= .5
                or self.last_at is None or not 0 < now - self.last_at <= .5):
            self.reset()
        self.run_id, self.last_at, self.last_time = run_id, now, t
        self.latest = {key: value if type(value) is bool or finite(value) else None
                       for key in FIELDS for value in (sample.get(key),)}
        phase = (sample.get('session_state') == 4 and finite(sample.get('session_flags'))
                 and int(sample['session_flags']) == sample['session_flags'] and sample['session_flags'] >= 0
                 and not int(sample['session_flags']) & ~4 and finite(sample.get('pace_mode')) and sample['pace_mode'] == 4)
        if not phase:
            self.phase_at = None
        elif self.phase_at is None:
            self.phase_at = t
        side = sample.get('car_left_right')
        valid = sample.get('on_track') is True and sample.get('is_replay') is False and sample.get('on_pit_road') is False
        if not valid or not finite(side) or side not in (1, 2, 3, 4, 5, 6):
            for proposal in self.proposals:
                proposal['invalidated'] = True
            self.overlap_at = self.clear_at = self.trigger = None
            self.multi = False
        elif side != 1:
            for proposal in self.proposals:
                proposal['invalidated'] = True
            self.overlap_at = t if self.overlap_at is None else self.overlap_at
            self.multi = self.multi or side == 4
            self.clear_at = self.trigger = None
        else:
            if self.overlap_at is not None:
                if t - self.overlap_at >= self.OVERLAP_S:
                    self.trigger = 'sandwich_resolved' if self.multi else 'overlap_resolved'
                    self.clear_at = t
                self.overlap_at, self.multi = None, False
            if self.trigger and self.clear_at is not None and t - self.clear_at >= self.CLEAR_S:
                self.propose(self.trigger, sample, run_id, now)
                self.trigger = self.clear_at = None
        self.other_triggers(sample, run_id, now, valid, phase)
        self.previous = dict(self.latest)
        ids = {p['candidate_id'] for p in self.proposals}
        self.pending = {key: record for key, record in self.pending.items() if key in ids}
        self.observed.intersection_update(ids)

    def view(self, now):
        return [{'candidate_id': p['candidate_id'], 'run_id': p['run_id'], 'session_time': p['session_time']}
                for p in self.proposals if p['candidate_id'] not in self.observed and p['candidate_id'] not in self.pending
                and 0 <= now - p['at'] <= self.TTL_S]

    def feedback(self, payload, now):
        proposal = next((p for p in self.proposals if p['candidate_id'] == payload['candidate_id'] and p['run_id'] == payload['run_id']), None)
        if proposal is None:
            return None
        key = proposal['candidate_id']
        if key in self.observed:
            return None
        if key in self.pending:
            return self.pending[key]
        reasons = gate(self.latest or {}, payload)
        reasons.extend(proposal.get('trigger_reasons', ()))
        t = payload.get('session_time')
        if (not finite(t) or self.last_time is None or not 0 <= self.last_time - t <= .5
                or self.last_at is None or not 0 <= now - self.last_at <= .5
                or not 0 <= now - proposal['at'] <= self.TTL_S):
            reasons.append('STALE_OR_EXPIRED')
        if (not proposal['stable_phase'] or self.phase_at is None or self.last_time - self.phase_at < self.STABLE_PHASE_S):
            reasons.append('PHASE_NOT_STABLE')
        if proposal.get('invalidated'):
            reasons.append('CONDITION_CHANGED')
        record = {**proposal, 'candidate_session_time': proposal['session_time'], 'session_time': t,
                'feedback_at': now, 'backend_session_time': self.last_time,
                'candidate_sample': dict(proposal['sample']), 'sample': dict(self.latest or {}), 'reasons': reasons,
                'client': {key: payload.get(key) for key in ('audio_busy', 'engineer_busy', 'pending_audio', 'muted')}}
        self.pending[key] = record
        return record

    def saved(self, records):
        for record in records:
            if record['run_id'] == self.run_id:
                key = record['candidate_id']
                self.pending.pop(key, None)
                self.observed.add(key)
        self.observed.intersection_update(p['candidate_id'] for p in self.proposals)

    def expired(self, now):
        for proposal in self.proposals:
            key = proposal['candidate_id']
            if key not in self.observed and key not in self.pending and now - proposal['at'] > self.TTL_S:
                self.pending[key] = {**proposal, 'client': {}, 'reasons': ['NO_FRONTEND_OBSERVATION', 'STALE_OR_EXPIRED']}
        return list(self.pending.values())
