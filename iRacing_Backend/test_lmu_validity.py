from copy import deepcopy
import json
import unittest

from lmu_validity import LapValidityTracker


def snapshot(t, **changes):
    lap = int(t // 4)
    result = {
        'session_key': 'process-1:epoch-1:session-10', 'vehicle_id': 7,
        'telemetry_et': t, 'scoring_et': t, 'lap_start_et': 4.0 * lap,
        'scoring_lap_start_et': 4.0 * lap, 'total_laps': lap,
        'last_lap_time': 4.0 if lap else None,
        'lap_invalidated': False, 'count_lap_flag': 2, 'track_limits_steps': 0,
        'in_pits': False, 'realtime': True, 'paused': False, 'events': [],
    }
    result.update(changes)
    return result


def drive(tracker, begin=0, end=8, change=None):
    events = []
    for i in range(round(begin * 2), round(end * 2) + 1):
        s = snapshot(i / 2)
        if change:
            s.update(change(i / 2))
        events.extend(tracker.ingest(s, i / 2))
    return events


class LmuValidityTests(unittest.TestCase):
    def test_full_observation_produces_only_unverified_candidate(self):
        tracker = LapValidityTracker()
        events = drive(tracker)
        self.assertEqual([e['diagnostic_verdict'] for e in events],
                         ['UNKNOWN', 'CONSISTENT_VALID_SIGNALS'])
        self.assertIn('PARTIAL_LAP_AT_ATTACH', events[0]['reasons'])
        candidate = events[1]
        self.assertEqual(candidate['lap_start_et'], 4)
        self.assertEqual(candidate['last_lap_time'], 4)
        self.assertEqual(candidate['evidence']['count_lap_flags'], [2])
        self.assertTrue(candidate['evidence']['scoring_confirmation'])
        for e in events:
            self.assertIsNone(e['valid'])
            self.assertEqual(e['validation_state'], 'GAME_SCREEN_UNVERIFIED')
        json.dumps(tracker.status(), allow_nan=False)

    def test_cut_signal_is_latched_despite_later_clear(self):
        tracker = LapValidityTracker()
        events = drive(tracker, change=lambda t: {'lap_invalidated': t == 6})
        self.assertEqual(events[-1]['diagnostic_verdict'], 'INVALID_SIGNAL_OBSERVED')
        self.assertTrue(events[-1]['evidence']['lap_invalidated_observed'])
        self.assertIsNone(events[-1]['valid'])

    def test_count_policy_is_evidence_but_track_limit_points_alone_are_not_invalidity(self):
        for flag, verdict in ((0, 'COUNT_POLICY_SIGNAL_OBSERVED'),
                              (1, 'COUNT_POLICY_SIGNAL_OBSERVED'),
                              (2, 'CONSISTENT_VALID_SIGNALS')):
            with self.subTest(flag=flag):
                events = drive(LapValidityTracker(), change=lambda t: {
                    'count_lap_flag': flag if 5 <= t < 7 else 2,
                    'track_limits_steps': 3 if t == 6 else 0})
                self.assertEqual(events[-1]['diagnostic_verdict'], verdict)
                self.assertEqual(events[-1]['evidence']['track_limits_steps_max'], 3)

    def test_recovered_count_policy_is_not_lap_invalidation_or_automatic_validity(self):
        # Two count/time-policy excursions also occur on the user-confirmed
        # accepted Monza lap. Reproduce that signal shape without raw telemetry.
        events = drive(LapValidityTracker(), change=lambda t: {
            'count_lap_flag': 1 if 4.5 <= t < 5.5 or 6.5 <= t < 7 else 2})
        lap = events[-1]
        self.assertEqual(lap['diagnostic_verdict'], 'COUNT_POLICY_SIGNAL_OBSERVED')
        self.assertEqual(lap['evidence']['count_lap_flags'], [1, 2])
        self.assertFalse(lap['evidence']['lap_invalidated_observed'])
        self.assertTrue(lap['evidence']['scoring_confirmation'])
        self.assertEqual(lap['reasons'], [])
        self.assertIsNone(lap['valid'])

    def test_persistent_count_policy_and_explicit_invalidation_remain_distinct(self):
        for invalid in (False, True):
            for flag in (0, 1):
                with self.subTest(invalid=invalid, flag=flag):
                    events = drive(LapValidityTracker(), change=lambda t: {
                        'count_lap_flag': flag if 4 <= t < 8 else 2,
                        'lap_invalidated': invalid and t == 6})
                    lap = events[-1]
                    self.assertEqual(lap['diagnostic_verdict'],
                                     'INVALID_SIGNAL_OBSERVED' if invalid else 'COUNT_POLICY_SIGNAL_OBSERVED')
                    self.assertEqual(lap['evidence']['count_lap_flags'], [flag])
                    self.assertIsNone(lap['valid'])

    def test_next_lap_count_policy_is_not_a_completed_lap_verdict(self):
        tracker = LapValidityTracker()
        drive(tracker, end=7.5, change=lambda t: {'count_lap_flag': 1 if t == 5 else 2})
        old = {'scoring_et': 7.5, 'scoring_lap_start_et': 4,
               'total_laps': 1, 'last_lap_time': 4, 'count_lap_flag': 2}
        self.assertEqual(tracker.ingest(snapshot(8, **old), 8), [])
        events = tracker.ingest(snapshot(8.5, count_lap_flag=0), 8.5)
        self.assertEqual(events[0]['diagnostic_verdict'], 'COUNT_POLICY_SIGNAL_OBSERVED')
        self.assertEqual(events[0]['evidence']['count_lap_flags'], [1, 2])
        self.assertEqual(tracker.status()['current']['evidence']['count_lap_flags'], [0])
        self.assertIsNone(events[0]['valid'])

    def test_missing_pit_pause_and_non_realtime_stay_unknown(self):
        variants = [({'in_pits': True}, 'PIT_OBSERVED'),
                    ({'in_pits': None}, 'PIT_SIGNAL_MISSING'),
                    ({'paused': True}, 'PAUSED_OR_UNKNOWN'),
                    ({'realtime': False}, 'NOT_REALTIME_OR_UNKNOWN'),
                    ({'lap_invalidated': None}, 'LAP_INVALIDATION_SIGNAL_MISSING'),
                    ({'count_lap_flag': None}, 'COUNT_LAP_FLAG_MISSING'),
                    ({'track_limits_steps': None}, 'TRACK_LIMITS_SIGNAL_MISSING'),
                    ({'scoring_et': None}, 'SCORING_MISSING'),
                    ({'scoring_lap_start_et': None}, 'SCORING_LAP_UNIDENTIFIED')]
        for changes, reason in variants:
            with self.subTest(reason=reason):
                events = drive(LapValidityTracker(), change=lambda t: changes if t == 6 else {})
                self.assertEqual(events[-1]['diagnostic_verdict'], 'UNKNOWN')
                self.assertIn(reason, events[-1]['reasons'])

    def test_delayed_scoring_confirms_old_lap_and_does_not_reassign_next_count_flag(self):
        tracker = LapValidityTracker()
        drive(tracker, end=7.5)
        old_score = {'scoring_et': 7.5, 'scoring_lap_start_et': 4,
                     'total_laps': 1, 'last_lap_time': 4, 'count_lap_flag': 2}
        self.assertEqual(tracker.ingest(snapshot(8, **old_score), 8), [])
        self.assertIsNotNone(tracker.status()['pending'])
        self.assertEqual(tracker.ingest(snapshot(8.5, **old_score), 8.5), [])
        events = tracker.ingest(snapshot(9, count_lap_flag=0), 9)
        self.assertEqual(events[0]['diagnostic_verdict'], 'CONSISTENT_VALID_SIGNALS')
        self.assertEqual(events[0]['evidence']['count_lap_flags'], [2])
        self.assertEqual(tracker.status()['current']['evidence']['count_lap_flags'], [0])

    def test_scoring_ahead_of_telemetry_is_reconciled_without_new_score(self):
        tracker = LapValidityTracker()
        drive(tracker, end=7)
        score_ahead = {'scoring_et': 8, 'scoring_lap_start_et': 8,
                       'total_laps': 2, 'last_lap_time': 4, 'count_lap_flag': 0}
        self.assertEqual(tracker.ingest(snapshot(7.5, **score_ahead), 7.5), [])
        events = tracker.ingest(snapshot(8, **score_ahead), 8)
        self.assertEqual(events[0]['diagnostic_verdict'], 'CONSISTENT_VALID_SIGNALS')
        self.assertEqual(events[0]['evidence']['count_lap_flags'], [2])

    def test_repeated_scoring_does_not_supply_false_confirmation(self):
        tracker = LapValidityTracker()
        drive(tracker, end=7.5)
        stale = {'scoring_et': 7.5, 'scoring_lap_start_et': 4,
                 'total_laps': 1, 'last_lap_time': 4, 'count_lap_flag': 2}
        result = []
        for t in (8, 8.5, 9, 9.5, 10, 10.5):
            result.extend(tracker.ingest(snapshot(t, **stale), t))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['diagnostic_verdict'], 'UNKNOWN')
        self.assertIn('SCORING_CONFIRMATION_TIMEOUT', result[0]['reasons'])

    def test_late_mismatched_and_missing_last_time_cannot_be_candidates(self):
        for changes, reason in (({'last_lap_time': None}, 'LAST_LAP_TIME_MISSING'),
                                ({'last_lap_time': -1}, 'LAST_LAP_TIME_MISSING'),
                                ({'last_lap_time': 0}, 'LAST_LAP_TIME_MISSING'),
                                ({'last_lap_time': 3}, 'LAST_LAP_TIME_CONFLICT'),
                                ({'total_laps': 3}, 'LAP_COUNT_OR_BOUNDARY_CONFLICT'),
                                ({'scoring_et': 10.5}, 'SCORING_CONFIRMATION_DELAYED')):
            with self.subTest(reason=reason):
                tracker = LapValidityTracker()
                drive(tracker, end=7.5)
                events = tracker.ingest(snapshot(8, **changes), 8)
                self.assertEqual(events[0]['diagnostic_verdict'], 'UNKNOWN')
                self.assertIn(reason, events[0]['reasons'])

    def test_sample_gap_or_receive_gap_overrides_invalid_evidence(self):
        for game_gap in (True, False):
            tracker = LapValidityTracker()
            drive(tracker, end=5)
            t = 6.5 if game_gap else 5.5
            tracker.ingest(snapshot(t, lap_invalidated=True), 6.5)
            events = []
            for i in range(round(t * 2) + 1, 17):
                events.extend(tracker.ingest(snapshot(i / 2), i / 2 + (0 if game_gap else 1)))
            self.assertEqual(events[-1]['diagnostic_verdict'], 'UNKNOWN')
            self.assertIn('OBSERVATION_GAP', events[-1]['reasons'])
            self.assertTrue(events[-1]['evidence']['lap_invalidated_observed'])

    def test_duplicate_samples_do_not_add_evidence(self):
        tracker = LapValidityTracker()
        tracker.ingest(snapshot(4), 4)
        before = deepcopy(tracker.status()['current']['evidence'])
        for _ in range(20):
            self.assertEqual(tracker.ingest(snapshot(4), 4), [])
        self.assertEqual(tracker.status()['current']['evidence'], before)

    def test_skipped_laps_are_unknown_and_a_later_full_lap_recovers(self):
        tracker = LapValidityTracker()
        drive(tracker, end=5)
        interrupted = tracker.ingest(snapshot(12), 12)
        self.assertEqual(interrupted[-1]['diagnostic_verdict'], 'UNKNOWN')
        self.assertIn('OBSERVATION_GAP', interrupted[-1]['reasons'])
        events = drive(tracker, begin=12.5, end=20)
        self.assertEqual(events[0]['diagnostic_verdict'], 'UNKNOWN')
        self.assertEqual(events[-1]['diagnostic_verdict'], 'CONSISTENT_VALID_SIGNALS')
        self.assertEqual(events[-1]['lap_start_et'], 16)

    def test_session_vehicle_reuse_clock_and_counter_resets_interrupt(self):
        variants = [({'session_key': 'process-1:epoch-2:session-10'}, 'SESSION_OR_VEHICLE_CHANGED'),
                    ({'vehicle_id': 8}, 'SESSION_OR_VEHICLE_CHANGED'),
                    ({'telemetry_et': 4.5}, 'TELEMETRY_CLOCK_REGRESSION'),
                    ({'scoring_et': 4.5}, 'SCORING_CLOCK_REGRESSION'),
                    ({'total_laps': 0}, 'LAP_COUNT_REGRESSION'),
                    ({'events': ['SME_END_SESSION']}, 'SDK_LIFECYCLE_EVENT')]
        for changes, reason in variants:
            with self.subTest(reason=reason):
                tracker = LapValidityTracker()
                drive(tracker, end=5)
                events = tracker.ingest(snapshot(5.5, **changes), 5.5)
                self.assertEqual(events[-1]['event_type'], 'LAP_OBSERVATION_INTERRUPTED')
                self.assertIn(reason, events[-1]['reasons'])
                self.assertEqual(events[-1]['diagnostic_verdict'], 'UNKNOWN')
                self.assertIn('PARTIAL_LAP_AT_ATTACH', tracker.status()['current']['reasons'])

    def test_interrupt_flushes_pending_and_current_and_reconnect_is_partial(self):
        tracker = LapValidityTracker()
        drive(tracker, end=7.5)
        tracker.ingest(snapshot(8, scoring_et=7.5, scoring_lap_start_et=4, total_laps=1), 8)
        events = tracker.interrupt('SOURCE_DISCONNECTED')
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e['diagnostic_verdict'] == 'UNKNOWN' for e in events))
        self.assertIsNone(tracker.status()['current'])
        tracker.ingest(snapshot(8.5), 8.5)
        self.assertIn('PARTIAL_LAP_AT_ATTACH', tracker.status()['current']['reasons'])

    def test_history_is_bounded_and_copies_cannot_mutate_internal_state(self):
        tracker = LapValidityTracker(max_completed=2)
        drive(tracker, end=16)
        status = tracker.status()
        self.assertEqual(len(status['completed']), 2)
        status['completed'][0]['valid'] = True
        status['current']['reasons'].append('EXTERNAL_MUTATION')
        self.assertIsNone(tracker.status()['completed'][0]['valid'])
        self.assertNotIn('EXTERNAL_MUTATION', tracker.status()['current']['reasons'])

    def test_bad_input_and_limits_do_not_create_validity(self):
        for value in (None, {}, snapshot(1, telemetry_et=float('nan')),
                      snapshot(1, vehicle_id=True), snapshot(1, lap_start_et=2)):
            tracker = LapValidityTracker()
            drive(tracker, end=1)
            events = tracker.ingest(value)
            self.assertEqual(events[-1]['diagnostic_verdict'], 'UNKNOWN')
            self.assertIsNone(tracker.status()['current'])
        for kwargs in ({'max_completed': 0}, {'max_sample_gap_s': 0}, {'scoring_grace_s': float('inf')}):
            with self.assertRaises(ValueError):
                LapValidityTracker(**kwargs)


if __name__ == '__main__':
    unittest.main()
