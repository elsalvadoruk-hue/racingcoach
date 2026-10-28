"""Shadow-only oval procedures. Bounded memory; no SDK writes, I/O or audio calls."""
from collections import deque
import math

from oval_spotter import OVAL_FIELDS

POLICY = 'oval-procedures-shadow-v1'
CAUTION, ONE_LAP, GREEN, START_GO = 0xC000, 0x200, 4, 0x80000000
FIELDS = tuple(dict.fromkeys((*OVAL_FIELDS, 'on_track', 'is_replay', 'on_pit_road', 'lap_number',
    'lap_completed', 'distance_pct', 'speed_mps', 'brake', 'throttle', 'steering_rad', 'car_left_right',
    'class_position', 'incidents', 'pit_service_status', 'workload_in_s', 'speech_window_s')))
TRACKED = ('session_state', 'session_flags', 'pace_mode', 'pits_open', 'player_pace_line',
           'player_pace_row', 'player_pace_flags', 'on_track', 'is_replay', 'on_pit_road')


def integer(value):
    return value if type(value) is int and 0 <= value <= 0xFFFFFFFF else None


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def scalar(value):
    return value if type(value) is bool or finite(value) else None


def phase(sample, session_type):
    if str(session_type or '').casefold() != 'race':
        return 'NON_RACE' if session_type else 'UNKNOWN'
    flags, pace, state = (integer(sample.get(k)) for k in ('session_flags', 'pace_mode', 'session_state'))
    if flags is None or pace not in range(5) or state is None:
        return 'UNKNOWN'
    if flags & (1 | 0x20000) or state in (5, 6):
        return 'FINISHED'
    if pace in (0, 1) and state in (1, 2, 3):
        return 'INITIAL_PACING'
    if pace in (2, 3) and state in (3, 4):
        return 'RESTART_PACING'
    if flags & CAUTION:
        return 'CAUTION'
    if state == 4 and pace == 4:
        return 'RACING'
    return 'UNKNOWN'


class OvalProcedures:
    MAX_PENDING, MAX_CANDIDATES, TTL = 256, 64, 5.

    def __init__(self):
        self.serial, self.dropped = 0, 0
        self.queue = deque()
        self.candidates = {}
        self.run_id = self.previous = None
        self.current_phase = 'UNKNOWN'
        self.last_at = self.last_t = self.checkpoint_t = None
        self.cycle = 0
        self.armed = None
        self.initial_seen = self.caution_seen = False

    def emit(self, kind, sample, now, *, status='OBSERVED', reasons=(), **details):
        self.serial += 1
        item = dict(record_id=f'{self.run_id}:oval:{self.serial}', run_id=self.run_id, kind=kind,
            session_time=sample.get('session_time'), at=now, policy=POLICY, mode='SHADOW',
            status=status, reasons=list(reasons), phase=self.current_phase, cycle=self.cycle,
            sample={k:sample.get(k) for k in FIELDS}, actual_playback=False, queue_dropped=self.dropped, **details)
        if len(self.queue) >= self.MAX_PENDING:
            self.queue.popleft()
            self.dropped += 1
            item['queue_dropped'] = self.dropped
        self.queue.append(item)
        return item

    def candidate(self, kind, text, sample, now, reasons=(), **details):
        reasons = (*reasons, *(['NOT_DRIVING'] if sample.get('on_track') is not True else []),
                   *(['REPLAY_OR_UNKNOWN'] if sample.get('is_replay') is not False else []))
        item = self.emit(kind, sample, now, status='CANDIDATE_SHADOW',
                         reasons=('FEATURE_DISABLED', 'SIGNAL_MEANING_UNVERIFIED', *reasons),
                         proposed_text=text, **details)
        key = item['record_id']
        self.candidates[key] = {'candidate_id':key, 'run_id':self.run_id, 'session_time':sample['session_time'],
                                'at':now, 'observed':False}
        if len(self.candidates) > self.MAX_CANDIDATES:
            del self.candidates[next(iter(self.candidates))]
        return key

    def reset(self, reason='CONNECTION_RESET', now=None):
        if self.armed and self.previous:
            self.emit('restart_cancelled', self.previous, self.last_at if now is None else now,
                      status='CANCELLED_SHADOW', reasons=(reason,), source_candidate_id=self.armed)
        self.run_id = self.previous = None
        self.last_at = self.last_t = self.checkpoint_t = None
        self.current_phase = 'UNKNOWN'
        self.cycle = 0
        self.armed = None
        self.initial_seen = self.caution_seen = False
        self.candidates.clear()
        # Unwritten events survive disconnect/session changes for the off-loop writer.

    def ingest(self, sample, run_id, now, enabled, session_type=None):
        t = sample.get('session_time')
        if not enabled or not finite(t) or not finite(now):
            if self.run_id is not None:
                self.reset('PROFILE_OR_CLOCK_UNKNOWN', now)
            return
        sample = {k:scalar(sample.get(k)) for k in (*FIELDS,'session_time')}
        boundary = (run_id != self.run_id or self.last_t is None or not 0 < t-self.last_t <= .5
                    or self.last_at is None or not 0 < now-self.last_at <= .5)
        if boundary:
            self.reset('SESSION_OR_CLOCK_BOUNDARY', now)
            self.run_id = run_id
        before, old_phase = self.previous, self.current_phase
        self.current_phase = phase(sample, session_type)
        valid = sample.get('on_track') is True and sample.get('is_replay') is False
        flags = integer(sample.get('session_flags'))
        old_flags = integer(before.get('session_flags')) if before else None
        changes = {k:{'before':before.get(k), 'after':sample.get(k)} for k in TRACKED
                   if before and (type(before.get(k)) is not type(sample.get(k)) or before.get(k) != sample.get(k))}
        if boundary or changes or self.checkpoint_t is None or t-self.checkpoint_t >= 5:
            self.emit('observation', sample, now, reasons=('BOUNDARY_NO_PRIOR_HISTORY',) if boundary else (),
                      changes=changes, session_type=session_type)
            self.checkpoint_t = t
        observed_green = flags is not None and bool(flags & (GREEN | START_GO))
        if self.armed:
            cancelled = not valid or flags is None or self.current_phase in ('UNKNOWN','FINISHED','NON_RACE') or not flags & ONE_LAP
            if cancelled:
                resolved = valid and self.current_phase == 'RACING' and observed_green
                self.emit('restart_resolved' if resolved else 'restart_cancelled', sample, now,
                    status='RESOLVED_SHADOW' if resolved else 'CANCELLED_SHADOW',
                    reasons=('GREEN_OBSERVED' if resolved else 'CONDITION_CHANGED',), source_candidate_id=self.armed)
                self.armed = None
        if flags is not None and flags & CAUTION:
            self.caution_seen = True
        if flags is not None and old_flags is not None and flags & CAUTION and not old_flags & CAUTION:
            # A retained one-lap signal without a new rising edge cannot identify a new caution cycle.
            if old_phase == 'RACING':
                self.cycle += 1
            self.caution_seen = True
            self.candidate('caution', '풀코스 코션 신호입니다. 게임 지시를 확인하세요.', sample, now)
        if valid and self.current_phase == 'INITIAL_PACING':
            self.initial_seen = True
        rising_one = flags is not None and old_flags is not None and flags & ONE_LAP and not old_flags & ONE_LAP
        if valid and rising_one and self.current_phase in ('CAUTION','INITIAL_PACING','RESTART_PACING'):
            self.armed = self.candidate('one_lap_to_green', '한 랩 전 신호입니다. 출발 지시를 확인하세요.', sample, now)
        if before and valid and self.current_phase == 'RACING' and old_phase != 'RACING':
            if observed_green and old_phase in ('CAUTION','RESTART_PACING') and self.caution_seen:
                self.candidate('restart_green', '그린 전환 신호입니다.', sample, now)
            elif observed_green and old_phase == 'INITIAL_PACING' and self.initial_seen:
                self.candidate('initial_green', '첫 출발 신호입니다.', sample, now,
                    reasons=('START_ZONE_CONTROL_UNCONFIRMED',))
        if before and changes:
            if 'pits_open' in changes:
                known = type(before.get('pits_open')) is bool and type(sample.get('pits_open')) is bool
                self.candidate('pit_permission', '본인 피트 정차 허용 신호가 바뀌었습니다.', sample, now,
                               reasons=() if known else ('MISSING_OR_RECOVERED_SIGNAL',), exit_permission='UNKNOWN')
            if 'player_pace_line' in changes or 'player_pace_row' in changes:
                self.candidate('line_assignment', '본인 대열 배정 신호가 바뀌었습니다.', sample, now,
                               reasons=('LINE_DIRECTION_MAPPING_UNVERIFIED',))
            if 'player_pace_flags' in changes:
                old, new = integer(before.get('player_pace_flags')), integer(sample.get('player_pace_flags'))
                for bit, name in [(1,'end_of_line'),(2,'free_pass'),(4,'wave_around')]:
                    if old is not None and new is not None and bool(old & bit) != bool(new & bit):
                        self.candidate(name, '본인 '+{'end_of_line':'후미','free_pass':'프리 패스','wave_around':'웨이브 어라운드'}[name]+' 배정 신호가 바뀌었습니다.',
                            sample, now, assigned=bool(new & bit), pass_permission='UNKNOWN')
        for key, candidate in list(self.candidates.items()):
            if now-candidate['at'] > self.TTL:
                if not candidate['observed']:
                    self.emit('client_observation_expired', sample, now, status='EXPIRED_SHADOW',
                        reasons=('NO_FRONTEND_OBSERVATION',), source_candidate_id=key)
                del self.candidates[key]
        self.previous = {k:sample.get(k) for k in (*FIELDS,'session_time')}
        self.last_t, self.last_at = t, now

    def view(self, now):
        return [{k:p[k] for k in ('candidate_id','run_id','session_time')} for p in self.candidates.values()
                if not p['observed'] and 0 <= now-p['at'] <= self.TTL]

    def feedback(self, payload, now):
        candidate = self.candidates.get(payload['candidate_id'])
        if not candidate or candidate['run_id'] != payload['run_id'] or candidate['observed']:
            return
        reasons = ['FEATURE_DISABLED']
        t = payload.get('session_time')
        if (not finite(t) or self.last_t is None or not 0 <= self.last_t-t <= .5
                or self.last_at is None or not 0 <= now-self.last_at <= .5 or now-candidate['at'] > self.TTL):
            reasons.append('STALE_OR_EXPIRED')
        client={k:payload.get(k) for k in ('audio_busy','engineer_busy','pending_audio','muted')}
        reasons.extend('UNKNOWN_'+k.upper() if type(v) is not bool else k.upper() for k,v in client.items() if v is not False)
        self.emit('client_observation', {**(self.previous or {}), 'session_time':t}, now,
            status='DEFERRED', reasons=reasons, source_candidate_id=payload['candidate_id'], client=client)
        candidate['observed'] = True

    def pending(self):
        return list(self.queue)

    def saved(self, records):
        ids={r['record_id'] for r in records}
        self.queue=deque(r for r in self.queue if r['record_id'] not in ids)
