"""Synthetic numerator/denominator and shared-source boundaries."""
from copy import deepcopy
import unittest

from driver_report_quality import build_quality


def drive(name='a', *, basis='UTC', native=True, offset=0., validity='VALID', points=2, seconds=600.,
          session_type='RACE'):
    return dict(drive_id=name, game='iracing', car_key='car', track_key='track', layout_key='gp',
                session_type=session_type, identity_status='NATIVE' if native else 'SOURCE_ONLY', observations=[dict(source_id=name,
        coverage=dict(time_basis=basis), metrics=dict(
            lap_quality=dict(completed_laps=[dict(lap_number=1, start_s=offset, end_s=offset+100.,
                analysis_validity=validity, replay_status='UNKNOWN')]),
            incidents=dict(status='PARTIAL', replay_status='UNKNOWN', increase_points=points,
                observed_time_s=seconds, intervals=[[offset, offset+seconds]],
                events=[dict(start_s=offset+10, end_s=offset+11, points=points)])))])


class QualityTests(unittest.TestCase):
    def test_incidents_use_race_and_qualifying_points_and_time_only(self):
        rows = [drive('race', points=2), drive('qualify', offset=1000., points=1, session_type='QUALIFY'),
                drive('practice', offset=2000., seconds=3600., points=99, session_type='PRACTICE'),
                drive('unknown', offset=6000., points=40, session_type='UNKNOWN'),
                drive('missing', offset=7000., points=10)]
        rows[-1].pop('session_type')
        result = build_quality(rows)
        incidents = result['incidents_per_hour']
        self.assertEqual(incidents['increase_points'], 3)
        self.assertEqual(incidents['observed_time_s'], 1200.)
        self.assertEqual(incidents['value'], 9.)
        self.assertEqual(incidents['observation_count'], 2)
        # This scope restriction is specific to incidents, not the valid-lap card.
        self.assertEqual(result['valid_lap_ratio']['classified_laps'], 5)

    def test_practice_only_has_no_competitive_incident_rate_even_with_zero_points(self):
        result = build_quality([drive(points=0, session_type='PRACTICE')])['incidents_per_hour']
        self.assertIsNone(result['value'])
        self.assertIsNone(result['increase_points'])
        self.assertEqual(result['observed_time_s'], 0.)
        self.assertEqual(result['observation_count'], 0)
        self.assertIn('레이스·퀄리', result['reason'])

    def test_missing_counters_are_unknown_and_measurable_laps_do_not_imply_validity(self):
        row = drive()
        row['observations'][0]['metrics'] = dict(laps=[dict(measurable=True)])
        result = build_quality([row])
        self.assertIsNone(result['valid_lap_ratio']['value'])
        self.assertIsNone(result['incidents_per_hour']['value'])

    def test_invalid_untimed_completions_remain_in_valid_ratio_denominator(self):
        result = build_quality([drive(), drive('b', offset=1000., validity='INVALID'),
                                drive('c', offset=2000., validity='UNKNOWN')])
        self.assertEqual(result['valid_lap_ratio']['value'], 50.)
        self.assertEqual(result['valid_lap_ratio']['classified_laps'], 2)
        self.assertEqual(result['valid_lap_ratio']['unknown_laps'], 1)
        self.assertEqual(result['valid_lap_ratio']['authority'], 'ANALYSIS_POLICY')

    def test_copies_and_conflicting_verdicts_cannot_inflate_validity(self):
        one = drive()
        copy = deepcopy(one['observations'][0])
        copy['source_id'] = 'copy'
        one['observations'].append(copy)
        duplicate = drive('b', validity='INVALID', native=False)
        result = build_quality([one, duplicate])
        self.assertEqual(result['valid_lap_ratio']['completed_laps'], 1)
        self.assertEqual(result['valid_lap_ratio']['unknown_laps'], 1)
        self.assertIsNone(result['valid_lap_ratio']['value'])
        self.assertEqual(result['incidents_per_hour']['observed_time_s'], 600.)

    def test_incident_zero_is_available_only_when_the_counter_was_observed(self):
        result = build_quality([drive(points=0)])['incidents_per_hour']
        self.assertEqual(result['value'], 0.)
        self.assertEqual(result['increase_points'], 0.)
        self.assertEqual(result['observed_time_s'], 600.)

    def test_native_same_lap_verdicts_conflict_even_across_clock_bases(self):
        one = drive()
        other = drive(basis='NATIVE_SESSION_TIME', validity='INVALID')['observations'][0]
        one['observations'].append(other)
        self.assertIsNone(build_quality([one])['valid_lap_ratio']['value'])

    def test_identical_incident_windows_with_conflicting_counts_are_withheld(self):
        result = build_quality([drive(), drive('b', points=99)])['incidents_per_hour']
        self.assertIsNone(result['value'])

    def test_unshared_source_laps_do_not_invent_an_independent_denominator(self):
        result = build_quality([drive(basis='SOURCE_RELATIVE', native=False),
            drive('b', basis='SOURCE_RELATIVE', native=False, validity='INVALID')])['valid_lap_ratio']
        self.assertEqual(result['completed_laps'], 1)

    def test_overlap_chain_keeps_the_whole_conflicting_component_unknown(self):
        result = build_quality([drive(offset=0), drive('b', offset=90, validity='INVALID'),
                                drive('c', offset=180)])['valid_lap_ratio']
        self.assertIsNone(result['value'])
        self.assertEqual(result['unknown_laps'], 1)

    def test_overlapping_sources_keep_matching_point_and_time_scope(self):
        result = build_quality([drive(), drive('b', offset=300., points=99),
                                drive('c', offset=1200., points=1)])['incidents_per_hour']
        self.assertEqual(result['increase_points'], 3)
        self.assertEqual(result['observed_time_s'], 1200.)
        self.assertEqual(result['value'], 9.)

    def test_unshared_clocks_are_not_added_to_a_known_pool(self):
        result = build_quality([drive(), drive('local', basis='SOURCE_RELATIVE', native=False,
                                              seconds=900., points=1)])['incidents_per_hour']
        self.assertEqual(result['increase_points'], 1)
        self.assertEqual(result['observed_time_s'], 900.)
        self.assertEqual(result['value'], 4.)

    def test_distinct_native_clocks_can_be_added_without_merging_seconds(self):
        result = build_quality([drive(basis='NATIVE_SESSION_TIME'),
                                drive('b', basis='NATIVE_SESSION_TIME', points=1)])['incidents_per_hour']
        self.assertEqual(result['value'], 9.)

    def test_confirmed_replays_are_excluded_from_both_quality_metrics(self):
        row = drive()
        metrics = row['observations'][0]['metrics']
        metrics['incidents']['replay_status'] = 'CONFIRMED_REPLAY'
        metrics['lap_quality']['completed_laps'][0]['replay_status'] = 'CONFIRMED_REPLAY'
        result = build_quality([row])
        self.assertIsNone(result['valid_lap_ratio']['value'])
        self.assertIsNone(result['incidents_per_hour']['value'])


if __name__ == '__main__':
    unittest.main()
