"""Synthetic event coverage and conservative offline review across discontinuities."""
from copy import deepcopy
import unittest

import oval_review as review


HEADER = {'run_id': 'synthetic', 'spotter_profile': 'ASPHALT_OVAL',
          'own_fields': ['car_left_right', 'on_pit_road', 'speed_mps'],
          'oval_fields': ['session_state', 'session_flags', 'pits_open'], 'fields': ['idx', 'gap_m']}


def rows(count=1000):
    return [{'t': round(i / 10, 1), 'own': [1, False, 60.], 'oval': [4, 0, None], 'c': [[2, 10.]]}
            for i in range(count)]


class OvalReviewTests(unittest.TestCase):
    def test_procedure_alignment_coverage_and_regression_respect_clock_boundaries(self):
        procedures=[{'session_time':.5,'kind':'restart_cancelled','status':'CANCELLED_SHADOW'},
                    {'session_time':90.,'kind':'free_pass','status':'CANDIDATE_SHADOW'}]
        result,frames=review.review({},HEADER,rows(10),[],procedures=procedures)
        self.assertEqual(result['coverage']['unaligned_procedure_rows'],1)
        self.assertEqual(frames[5]['procedures'],procedures[:1])
        self.assertEqual(result['feature_coverage']['free_pass']['verdict'],'REVIEW_REQUIRED')
        self.assertEqual(result['feature_coverage']['wave_around']['verdict'],'NOT_OBSERVED')
        self.assertEqual(result['radio_counts'],{})
        doc=review.regression_windows(result,frames)
        self.assertIs(doc['exact_live_replay'],False)
        self.assertEqual(len(doc['windows']),1)
        repeated,frames=review.review({},HEADER,rows(10)+rows(10),[],procedures=procedures)
        self.assertEqual(repeated['coverage']['aligned_procedure_rows'],0)
        self.assertEqual(review.regression_windows(repeated,frames)['windows'],[])

    def test_shadow_alignment_keeps_all_results_and_never_infers_playback(self):
        shadow = [{'session_time': .5, 'status': 'ELIGIBLE_SHADOW', 'selected': {'text': '시험'}},
                  {'session_time': 99., 'status': 'DEFERRED', 'reasons': ['MUTED']}]
        result, frames = review.review({}, HEADER, rows(10), [], shadow)
        self.assertEqual(frames[5]['banter'], shadow[:1])
        self.assertEqual(result['coverage']['unaligned_banter_rows'], 1)
        self.assertEqual(result['banter_observations'], shadow)
        self.assertEqual(result['radio_counts'], {})
        self.assertTrue(any(e['kind'] == 'banter_shadow' for e in result['navigation']))
        repeated, frames = review.review({}, HEADER, rows(10)+rows(10), [], shadow[:1])
        self.assertEqual(repeated['coverage']['unaligned_banter_rows'], 1)
        self.assertFalse(any(f['banter'] for f in frames))
    def test_schema_is_recorded_not_assumed(self):
        d = review.decode({'own_fields': ['on_pit_road', 'car_left_right'], 'oval_fields': ['pits_open']},
                          {'t': 0, 'own': [False, 4], 'oval': [0], 'c': []})
        self.assertIs(d['own']['on_pit_road'], False)
        self.assertEqual(d['own']['car_left_right'], 4)
        self.assertEqual(d['oval']['pits_open'], 0)
        self.assertEqual(review.decode({}, rows(1)[0])['own'], {})

    def test_clock_gaps_reverse_duplicate_missing_split(self):
        values = [0, .1, .2, 1., 1., .2, None, .3, .4]
        data = [{**rows(1)[0], 't': t} for t in values]
        self.assertEqual(review.spans({}, data), [(0, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 9)])

    def test_exported_ranges_never_join_even_with_close_timestamps(self):
        header = {**HEADER, 'export_selection': {'segments': [{'start': 0, 'stop': 2}, {'start': 100, 'stop': 102}]}}
        self.assertEqual(review.spans(header, rows(4)), [(0, 2), (2, 4)])

    def test_bad_coverage_fails_closed(self):
        header = {**HEADER, 'export_selection': {'segments': [{'start': 5, 'stop': 8}]}}
        self.assertEqual(review.spans(header, rows(4)), [(0, 1), (1, 2), (2, 3), (3, 4)])

    def test_multi_and_radio_outside_old_uniform_windows_are_preserved(self):
        data = rows(10000)
        for i in range(810, 870):
            data[i]['own'][0] = 4
        radio = [{'session_time': 130.5, 'status': 'failed'}]
        before = deepcopy(data)
        selected, info = review.select_windows(HEADER, data, radio)
        self.assertIn(data[810], selected)
        self.assertIn(data[1305], selected)
        self.assertGreater(info['selected_event_counts']['multi_overlap'], 0)
        self.assertGreater(info['selected_event_counts']['radio_problem'], 0)
        self.assertEqual(data, before)
        self.assertEqual(selected, [r for p in info['segments'] for r in data[p['start']:p['stop']]])
        self.assertLessEqual(len(selected), 1200)

    def test_event_storm_bounded_deterministic_and_omissions_explicit(self):
        data = rows(6000)
        for i, row in enumerate(data):
            row['own'][0] = 4 if i % 2 else 1
            row['oval'][2] = i % 2
        a = review.select_windows(HEADER, data)
        self.assertEqual(a, review.select_windows(HEADER, data))
        self.assertLessEqual(len(a[0]), 1200)
        self.assertGreater(a[1]['omitted_events'], 0)
        self.assertEqual(a[1]['selected_events'] + a[1]['omitted_events'], a[1]['events'])

    def test_windows_do_not_cross_a_clock_boundary(self):
        data = rows(1000) + rows(1000)
        _, info = review.select_windows(HEADER, data)
        self.assertTrue(all(not p['start'] < 1000 < p['stop'] for p in info['segments']))

    def test_zero_false_missing_are_not_clear_or_rule_commands(self):
        data = rows(4)
        for row, value in zip(data, [None, 0, False, 1]):
            row['own'][0] = value
            row['oval'][2] = value
        _, _, events = review.observe(HEADER, data)
        self.assertFalse(any(e['kind'] == 'clear_transition' for e in events))
        self.assertTrue(any(e['field'] == 'pits_open' and e['after'] == 0 for e in events if 'field' in e))

    def test_long_overlap_observed_once_and_clear_missing_is_not_invented(self):
        data = rows(100)
        for row in data:
            row['own'][0] = 2
        data[90]['own'][0] = None
        _, _, events = review.observe(HEADER, data)
        self.assertEqual(sum(e['kind'] == 'long_overlap' for e in events), 1)
        self.assertFalse(any(e['kind'] == 'clear_transition' for e in events))

    def test_repeated_clocks_make_radio_alignment_unknown(self):
        data = rows(100) + rows(100)
        result, frames = review.review({}, HEADER, data, [{'session_time': 3., 'status': 'started', 'key': 'clear_all'}])
        self.assertEqual(result['coverage']['unaligned_radio_rows'], 1)
        self.assertFalse(any(f['radio'] for f in frames))

    def test_clear_review_checks_the_corresponding_side_only(self):
        data = rows(10)
        for row in data:
            row['own'][0] = 3
        radio = [{'session_time': .3, 'status': 'started', 'key': 'clear_left'},
                 {'session_time': .5, 'status': 'started', 'key': 'clear_right'}]
        result, _ = review.review({}, HEADER, data, radio)
        self.assertEqual([f['radio_index'] for f in result['findings']], [1])

    def test_missing_traffic_keeps_failed_interrupted_and_no_pass_claim(self):
        radio = [{'status': 'failed', 'reason': 'media_error'}, {'status': 'interrupted'}]
        result, frames = review.review({}, {}, [], radio)
        self.assertEqual(len(result['findings']), 2)
        self.assertEqual(result['coverage']['unaligned_radio_rows'], 2)
        self.assertEqual(result['radio_timeline'], radio)
        self.assertEqual(frames, [])
        self.assertEqual(review.review({}, {}, [], [])[0]['verdict'], 'INSUFFICIENT_FOR_PASS')

    def test_playback_problems_are_navigable_and_cars_are_retained_for_viewer(self):
        result, frames = review.review({}, HEADER, rows(10), [{'session_time': .5, 'status': 'failed'}])
        self.assertTrue(any(e['kind'] == 'playback_failed' and e['index'] == 5 for e in result['navigation']))
        self.assertEqual(frames[5]['cars'], [{'idx': 2, 'gap_m': 10.}])

    def test_banter_candidates_always_deferred_and_dont_cross_missing(self):
        data = rows(6)
        for row, value in zip(data, [2, 1, 2, None, 1, 1]):
            row['own'][0] = value
        result, _ = review.review({}, HEADER, data, [])
        self.assertEqual(len(result['banter_candidates']), 1)
        self.assertEqual(result['banter_candidates'][0]['status'], 'DEFERRED')

    def test_repeat_review_does_not_cross_segments(self):
        data = rows(10)
        data[5]['t'] = 5.
        radio = [{'event_id': 'a', 'key': 'left', 'status': 'started', 'session_time': .4},
                 {'event_id': 'b', 'key': 'left', 'status': 'started', 'session_time': .6}]
        self.assertEqual(review.review({}, HEADER, data, radio)[0]['findings'], [])
        self.assertEqual(review.review({}, HEADER, rows(10), radio)[0]['findings'][0]['kind'], 'rapid_repeat')


if __name__ == '__main__':
    unittest.main()
