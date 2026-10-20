"""Conservative, in-memory LMU lap-signal observations, never official validity.

Telemetry and scoring are independent streams.  In particular, a count flag
received after a telemetry lap boundary must not be assigned to the old lap.
No result from this module is suitable for backfilling recorded DuckDB files.
"""

from collections import deque
from copy import deepcopy
import math


VALIDATION_STATE = 'GAME_SCREEN_UNVERIFIED'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class LapValidityTracker:
    """Track diagnostic evidence with bounded history and no validity promotion."""

    def __init__(self, *, max_completed=50, max_sample_gap_s=1.0, scoring_grace_s=2.0):
        if not _integer(max_completed) or not 1 <= max_completed <= 1000:
            raise ValueError('max_completed must be between 1 and 1000')
        if not _number(max_sample_gap_s) or max_sample_gap_s <= 0:
            raise ValueError('max_sample_gap_s must be positive')
        if not _number(scoring_grace_s) or scoring_grace_s <= 0:
            raise ValueError('scoring_grace_s must be positive')
        self._history = deque(maxlen=max_completed)
        self._max_gap = float(max_sample_gap_s)
        self._grace = float(scoring_grace_s)
        self._current = None
        self._pending = None
        self._last = None
        self._received = None
        self._score_et = None
        self._score_total = None

    def _new(self, s, *, complete=False, total=None):
        lap = {
            'session_key': s['session_key'], 'vehicle_id': s['vehicle_id'],
            'lap_start_et': float(s['lap_start_et']), 'lap_end_et': None,
            'total_laps_at_start': total, 'total_laps_at_end': None,
            'last_lap_time': None, 'valid': None, 'validation_state': VALIDATION_STATE,
            'diagnostic_verdict': 'UNKNOWN', 'reasons': [],
            'evidence': {'telemetry_samples': 0, 'scoring_samples': 0,
                         'lap_invalidated_observed': False, 'count_lap_flags': [],
                         'track_limits_steps_min': None, 'track_limits_steps_max': None,
                         'pit_observed': False, 'boundary_observed': complete,
                         'scoring_confirmation': False},
        }
        if not complete:
            self._reason(lap, 'PARTIAL_LAP_AT_ATTACH')
        return lap

    @staticmethod
    def _reason(lap, reason):
        if reason not in lap['reasons']:
            lap['reasons'].append(reason)

    def _finish(self, lap, event_type='LAP_COMPLETED'):
        evidence = lap['evidence']
        if lap['reasons'] or event_type != 'LAP_COMPLETED':
            verdict = 'UNKNOWN'
        elif evidence['lap_invalidated_observed']:
            verdict = 'INVALID_SIGNAL_OBSERVED'
        elif any(f != 2 for f in evidence['count_lap_flags']):
            # Count/time policy can temporarily change on a game-accepted lap.
            # Preserve the observation without labelling it lap invalidation or
            # promoting the later recovery to an officially valid result.
            verdict = 'COUNT_POLICY_SIGNAL_OBSERVED'
        elif (evidence['boundary_observed'] and evidence['scoring_confirmation']
              and evidence['telemetry_samples'] >= 2 and evidence['scoring_samples'] >= 1
              and evidence['count_lap_flags'] == [2]):
            verdict = 'CONSISTENT_VALID_SIGNALS'
        else:
            verdict = 'UNKNOWN'
        lap['event_type'] = event_type
        lap['diagnostic_verdict'] = verdict
        self._history.append(deepcopy(lap))
        return deepcopy(lap)

    def interrupt(self, reason='OBSERVATION_INTERRUPTED'):
        """Close unfinished evidence, including same-slot reconnects, as UNKNOWN."""
        reason = str(reason)[:120]
        result = []
        for lap in (self._pending, self._current):
            if lap is not None:
                self._reason(lap, reason)
                result.append(self._finish(lap, 'LAP_OBSERVATION_INTERRUPTED'))
        self._pending = self._current = self._last = None
        self._received = self._score_et = self._score_total = None
        return result

    reset = interrupt

    def status(self):
        return deepcopy({'valid': None, 'validation_state': VALIDATION_STATE,
                         'current': self._current, 'pending': self._pending,
                         'completed': list(self._history)})

    def ingest(self, snapshot, received_monotonic=None):
        """Consume one pair of cached streams; duplicates do not add evidence.

        ``scoring_lap_start_et`` is necessary for positive signal consistency.
        Missing data and inconsistent clocks retain reasons even if later fixed.
        SDK event labels are used only as interruption evidence, not lap validity.
        """
        s = snapshot
        if not isinstance(s, dict):
            return self.interrupt('MALFORMED_SNAPSHOT')
        if (not isinstance(s.get('session_key'), str) or not s['session_key']
                or not _integer(s.get('vehicle_id'))
                or not _number(s.get('telemetry_et')) or s['telemetry_et'] < 0
                or not _number(s.get('lap_start_et')) or s['lap_start_et'] < 0
                or s['lap_start_et'] > s['telemetry_et'] + 1e-6):
            return self.interrupt('MISSING_OR_INVALID_TELEMETRY_IDENTITY')
        result = []
        events = s.get('events', [])
        interruption_events = {'SME_START_SESSION', 'SME_END_SESSION', 'SME_EXIT_REALTIME',
                               'SME_UNLOAD', 'SME_SHUTDOWN'}
        if isinstance(events, list) and any(e in interruption_events for e in events if isinstance(e, str)):
            result.extend(self.interrupt('SDK_LIFECYCLE_EVENT'))
        elif events is not None and not isinstance(events, list):
            result.extend(self.interrupt('MALFORMED_EVENTS'))
        if self._last is not None:
            previous = self._last
            reason = None
            if (s['session_key'], s['vehicle_id']) != (previous['session_key'], previous['vehicle_id']):
                reason = 'SESSION_OR_VEHICLE_CHANGED'
            elif s['telemetry_et'] < previous['telemetry_et'] or s['lap_start_et'] < previous['lap_start_et']:
                reason = 'TELEMETRY_CLOCK_REGRESSION'
            elif (_number(received_monotonic) and self._received is not None
                  and received_monotonic < self._received):
                reason = 'RECEIVE_CLOCK_REGRESSION'
            elif (_number(s.get('scoring_et')) and self._score_et is not None
                  and s['scoring_et'] < self._score_et):
                reason = 'SCORING_CLOCK_REGRESSION'
            elif (_number(s.get('scoring_et')) and self._score_et is not None
                  and s['scoring_et'] > self._score_et and _integer(s.get('total_laps'))
                  and self._score_total is not None and s['total_laps'] < self._score_total):
                reason = 'LAP_COUNT_REGRESSION'
            if reason:
                result.extend(self.interrupt(reason))
        if self._current is None:
            self._current = self._new(s)
        previous = self._last
        fresh_telemetry = previous is None or s['telemetry_et'] > previous['telemetry_et']
        score_et = s.get('scoring_et')
        fresh_scoring = (_number(score_et) and score_et >= 0
                         and (self._score_et is None or score_et > self._score_et))
        gap = (previous is not None and fresh_telemetry
               and s['telemetry_et'] - previous['telemetry_et'] > self._max_gap + 1e-9)
        receive_gap = (_number(received_monotonic) and self._received is not None
                       and received_monotonic - self._received > self._max_gap + 1e-9)
        if gap or receive_gap:
            self._reason(self._current, 'OBSERVATION_GAP')
            if self._pending:
                self._reason(self._pending, 'OBSERVATION_GAP')
        boundary = s['lap_start_et'] > self._current['lap_start_et']
        if boundary:
            if self._pending:
                self._reason(self._pending, 'SCORING_CONFIRMATION_MISSING')
                result.append(self._finish(self._pending))
            old = self._current
            old['lap_end_et'] = float(s['lap_start_et'])
            self._pending = old
            total = old['total_laps_at_start']
            self._current = self._new(s, complete=fresh_telemetry and not (gap or receive_gap),
                                      total=total + 1 if total is not None else None)
            if not fresh_telemetry:
                self._reason(self._pending, 'LAP_BOUNDARY_WITHOUT_NEW_TELEMETRY')
        current = self._current
        evidence = current['evidence']
        if fresh_telemetry:
            evidence['telemetry_samples'] += 1
            invalid = s.get('lap_invalidated')
            if invalid is True:
                evidence['lap_invalidated_observed'] = True
            elif invalid is not False:
                self._reason(current, 'LAP_INVALIDATION_SIGNAL_MISSING')
            steps = s.get('track_limits_steps')
            if _integer(steps):
                low, high = evidence['track_limits_steps_min'], evidence['track_limits_steps_max']
                evidence['track_limits_steps_min'] = steps if low is None else min(low, steps)
                evidence['track_limits_steps_max'] = steps if high is None else max(high, steps)
            else:
                self._reason(current, 'TRACK_LIMITS_SIGNAL_MISSING')
        if s.get('in_pits') is True:
            evidence['pit_observed'] = True
            self._reason(current, 'PIT_OBSERVED')
        elif s.get('in_pits') is not False:
            self._reason(current, 'PIT_SIGNAL_MISSING')
        if s.get('realtime') is not True:
            self._reason(current, 'NOT_REALTIME_OR_UNKNOWN')
        if s.get('paused') is not False:
            self._reason(current, 'PAUSED_OR_UNKNOWN')
        if not _number(score_et) or score_et < 0:
            self._reason(current, 'SCORING_MISSING')
        elif s['telemetry_et'] - score_et > self._grace:
            self._reason(current, 'SCORING_STALE')
        # A scoring update can precede the matching telemetry lap boundary.
        # Reconcile that cached score once when telemetry catches up.
        if fresh_scoring or (boundary and _number(score_et)):
            total = s.get('total_laps')
            score_start = s.get('scoring_lap_start_et')
            if not _integer(total):
                self._reason(current, 'LAP_COUNT_MISSING')
            elif not _number(score_start):
                self._reason(current, 'SCORING_LAP_UNIDENTIFIED')
            elif abs(score_start - current['lap_start_et']) <= 1e-6:
                if current['total_laps_at_start'] is None:
                    current['total_laps_at_start'] = total
                if total != current['total_laps_at_start']:
                    self._reason(current, 'LAP_COUNT_CONFLICT')
                    evidence['lap_count_conflict'] = {
                        'expected': current['total_laps_at_start'], 'observed': total}
                    # Preserve this lap as UNKNOWN but allow a later complete
                    # lap to recover after a skipped boundary or reconnect.
                    current['total_laps_at_start'] = total
                elif score_et >= current['lap_start_et']:
                    evidence['scoring_samples'] += 1
                    flag = s.get('count_lap_flag')
                    if _integer(flag) and flag <= 2:
                        if flag not in evidence['count_lap_flags']:
                            evidence['count_lap_flags'].append(flag)
                            evidence['count_lap_flags'].sort()
                    else:
                        self._reason(current, 'COUNT_LAP_FLAG_MISSING')
            if self._pending:
                pending = self._pending
                baseline = pending['total_laps_at_start']
                if (_integer(total) and baseline is not None and total >= baseline + 1
                        and _number(score_start) and score_start >= pending['lap_end_et'] - 1e-6
                        and score_et >= pending['lap_end_et']):
                    pending['total_laps_at_end'] = total
                    pending['evidence']['scoring_confirmation'] = True
                    if score_et - pending['lap_end_et'] > self._grace:
                        self._reason(pending, 'SCORING_CONFIRMATION_DELAYED')
                    last = s.get('last_lap_time')
                    if total != baseline + 1 or abs(score_start - pending['lap_end_et']) > 1e-6:
                        self._reason(pending, 'LAP_COUNT_OR_BOUNDARY_CONFLICT')
                    if not _number(last) or last <= 0:
                        self._reason(pending, 'LAST_LAP_TIME_MISSING')
                    else:
                        pending['last_lap_time'] = float(last)
                        duration = pending['lap_end_et'] - pending['lap_start_et']
                        if abs(last - duration) > .05:
                            self._reason(pending, 'LAST_LAP_TIME_CONFLICT')
                    result.append(self._finish(pending))
                    self._pending = None
            self._score_et = float(score_et)
            self._score_total = total if _integer(total) else None
        if self._pending and s['telemetry_et'] - self._pending['lap_end_et'] > self._grace:
            self._reason(self._pending, 'SCORING_CONFIRMATION_TIMEOUT')
            result.append(self._finish(self._pending))
            self._pending = None
        self._last = {k: s[k] for k in ('session_key', 'vehicle_id', 'telemetry_et', 'lap_start_et')}
        self._received = float(received_monotonic) if _number(received_monotonic) else None
        return result
