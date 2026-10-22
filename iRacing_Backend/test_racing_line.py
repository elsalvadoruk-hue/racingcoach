"""Geometric offsets, observation boundaries and corner-analysis integration."""
import atexit
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

_runtime = tempfile.TemporaryDirectory(prefix='racing-line-test-')
atexit.register(_runtime.cleanup)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_runtime.name) / 'memory.sqlite3'))

from racing_line import EARTH_R_M, racing_line_context, racing_line_offsets
from analysis_corners import CornerAnalysis


def gps(x, y, *, yaw=1):
    distance = np.arange(len(x), dtype=float)
    lat0, lon0 = 37., 127.
    return {'distance_pct': distance / 1000,
            'Lat': lat0 + np.degrees(np.asarray(y) / EARTH_R_M),
            'Lon': lon0 + np.degrees(np.asarray(x) / (EARTH_R_M * np.cos(np.radians(lat0)))),
            'YawRate': np.where((distance >= 300) & (distance <= 500), yaw * .3, 0.),
            'time_s': distance / 40, 'speed_kph': np.full(len(x), 144.),
            '_coverage': np.ones(len(x), dtype=bool)}


def pair():
    x = np.arange(1001, dtype=float)
    y = np.zeros_like(x)
    corner = {'entry_pct': .3, 'apex_pct': .4, 'exit_pct': .5,
              'target': {'brake_start_pct': .3, 'min_speed_pct': .4},
              'measurement_usable': True}
    reference = gps(x, y)
    return deepcopy(reference), reference, corner


class RacingLineTests(unittest.TestCase):
    def test_parallel_one_metre_keeps_shift_and_signed_offsets(self):
        target, reference, corner = pair()
        target['Lat'] += np.degrees(1 / EARTH_R_M)
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(row['quality'], 'ok')
        for name in ('entry', 'apex', 'exit'):
            self.assertAlmostEqual(row[name + '_m'], -1., places=3)
            self.assertEqual(row['side_words'][name], '안쪽')
        self.assertAlmostEqual(row['alignment_m'], 1., places=3)
        self.assertAlmostEqual(row['path_length_delta_m'], 0., places=3)

    def test_apex_only_inside_two_metres_and_longer_path(self):
        target, reference, corner = pair()
        displacement = 2 * np.maximum(0, 1 - np.abs(np.arange(1001) - 400) / 40)
        target['Lat'] += np.degrees(displacement / EARTH_R_M)
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(row['quality'], 'ok')
        self.assertAlmostEqual(row['apex_m'], -2., places=3)
        self.assertAlmostEqual(row['entry_m'], 0., places=3)
        self.assertAlmostEqual(row['exit_m'], 0., places=3)
        self.assertGreater(row['path_length_delta_m'], .09)

    def test_hairpin_cannot_project_onto_nearby_opposite_leg(self):
        x = np.arange(1001, dtype=float)
        y = np.zeros_like(x)
        # Opposite leg passes 0.1 m above the target at distance 450 m,
        # but is at progress 550 m, outside the permitted 430..470 m window.
        x[501:601] = np.arange(499, 399, -1)
        y[501:601] = 2.1
        reference = gps(x, y)
        target = deepcopy(reference)
        target['Lat'][450] += np.degrees(2 / EARTH_R_M)
        corner = {'entry_pct': .4, 'exit_pct': .6,
                  'target': {'brake_start_pct': .42, 'min_speed_pct': .45}}
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(row['quality'], 'ok')
        self.assertAlmostEqual(row['apex_m'], -2., places=3)

    def test_three_metre_alignment_rejects_without_subtracting_offset(self):
        target, reference, corner = pair()
        target['Lat'] += np.degrees(3 / EARTH_R_M)
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(row['quality'], 'low')
        self.assertEqual(row['alignment_m'], 3.)
        for key in ('entry_m', 'apex_m', 'exit_m', 'path_length_delta_m'):
            self.assertIsNone(row[key])
        self.assertEqual(row['side_words'], {})

    def test_right_turn_inside_and_outside_use_same_public_sign(self):
        target, reference, corner = pair()
        reference['YawRate'] *= -1
        target['Lat'] -= np.degrees(1 / EARTH_R_M)
        inside = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(inside['apex_m'], -1.)
        self.assertEqual(inside['side_words']['apex'], '안쪽')
        target['Lat'] += np.degrees(2 / EARTH_R_M)
        outside = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(outside['apex_m'], 1.)
        self.assertEqual(outside['side_words']['apex'], '바깥')

    def test_target_events_not_reference_events_or_definition_apex(self):
        target, reference, corner = pair()
        target['Lat'][420] += np.degrees(2 / EARTH_R_M)
        target['Lat'][280] += np.degrees(1 / EARTH_R_M)
        corner['reference'] = {'brake_start_pct': .31, 'min_speed_pct': .39}
        corner['target'] = {'brake_start_pct': .28, 'min_speed_pct': .42}
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(row['entry_m'], -1.)
        self.assertEqual(row['apex_m'], -2.)

    def test_projection_uses_segment_interior_instead_of_nearest_sample(self):
        target, reference, corner = pair()
        # Shift longitudinally by half a metre; it must not inflate the normal distance.
        target['Lon'] += np.degrees(.5 / (EARTH_R_M * np.cos(np.radians(37))))
        target['Lat'] += np.degrees(1 / EARTH_R_M)
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertAlmostEqual(row['apex_m'], -1., places=3)

    def test_missing_gps_or_corner_coverage_never_connects_gaps(self):
        for field, value in (('Lat', np.nan), ('Lon', 0.), ('_coverage', False),
                             ('_analysis_coverage', False)):
            for source in ('target', 'reference'):
                with self.subTest(field=field, source=source):
                    target, reference, corner = pair()
                    grid = target if source == 'target' else reference
                    if field not in grid:
                        grid[field] = np.ones(1001, dtype=bool)
                    grid[field][410] = value
                    row = racing_line_offsets(target, reference, corner, 1000)
                    self.assertEqual(row['quality'], 'low')
                    self.assertIsNone(row['path_length_delta_m'])

    def test_missing_event_does_not_fall_back_to_detected_apex(self):
        target, reference, corner = pair()
        corner['target']['min_speed_pct'] = None
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertEqual(row['quality'], 'ok')
        self.assertIsNone(row['apex_m'])
        self.assertNotIn('apex', row['side_words'])
        self.assertEqual(row['exit_m'], 0.)

    def test_unknown_turn_direction_keeps_distances_unavailable(self):
        for yaw in (None, 0, np.nan):
            target, reference, corner = pair()
            if yaw is None:
                reference.pop('YawRate')
            else:
                reference['YawRate'][:] = yaw
            row = racing_line_offsets(target, reference, corner, 1000)
            self.assertEqual(row['quality'], 'ok')
            self.assertEqual(row['reason'], 'TURN_DIRECTION_UNKNOWN')
            self.assertEqual(row['side_words'], {})
            self.assertIsNone(row['apex_m'])
            self.assertEqual(row['path_length_delta_m'], 0.)

    def test_same_game_and_lmu_same_recording_required(self):
        target, reference, corner = pair()
        target['_game'] = 'lmu'
        target['_source_file'] = 'recording-a'
        self.assertEqual(racing_line_offsets(target, reference, corner, 1000)['quality'], 'low')
        reference['_game'] = 'lmu'
        reference['_source_file'] = 'recording-b'
        self.assertEqual(racing_line_offsets(target, reference, corner, 1000)['quality'], 'low')
        reference['_source_file'] = 'recording-a'
        self.assertEqual(racing_line_offsets(target, reference, corner, 1000)['quality'], 'ok')

    def test_distance_axis_change_preserves_identity_and_lmu_cross_recording_rejection(self):
        import backend_api as api
        target, reference, corner = pair()
        target.update(_game='lmu', _source_file='recording-a')
        reference.update(_game='lmu', _source_file='recording-b')
        moved = api._on_distance_axis(reference, np.linspace(0, 1, 501))
        self.assertEqual(moved['_game'], 'lmu')
        self.assertEqual(moved['_source_file'], 'recording-b')
        self.assertEqual(racing_line_offsets(target, moved, corner, 1000)['quality'], 'low')

    def test_alignment_excludes_all_detected_corners(self):
        target, reference, corner = pair()
        target['Lat'][700:801] += np.degrees(5 / EARTH_R_M)
        context = racing_line_context(target, reference, 1000, [(.3, .5), (.7, .8)])
        self.assertEqual(context['alignment_m'], 0.)
        self.assertEqual(racing_line_offsets(target, reference, corner, 1000, _context=context)['quality'], 'ok')

    def test_provenance_does_not_accept_an_unmarked_aggregate_as_an_actual_lap(self):
        target, reference, corner = pair()
        target['_source_file'] = 'actual-target.ibt'
        self.assertEqual(racing_line_offsets(target, reference, corner, 1000)['quality'], 'low')
        reference['_source_file'] = 'actual-reference.ibt'
        self.assertEqual(racing_line_offsets(target, reference, corner, 1000)['quality'], 'ok')

    def test_ranges_and_channel_shapes_reject_cleanly(self):
        for variant in ('length', 'axis', 'shape', 'no_gps', 'corner_bounds', 'unusable'):
            target, reference, corner = pair()
            length = 1000
            if variant == 'length':
                length = float('nan')
            elif variant == 'axis':
                target['distance_pct'][3] = target['distance_pct'][2]
            elif variant == 'shape':
                target['Lat'] = target['Lat'][:-1]
            elif variant == 'no_gps':
                target.pop('Lat')
            elif variant == 'corner_bounds':
                corner['exit_pct'] = 1.01
            elif variant == 'unusable':
                corner['measurement_usable'] = False
            with self.subTest(variant=variant):
                self.assertEqual(racing_line_offsets(target, reference, corner, length)['quality'], 'low')

    def test_no_extrapolation_for_events_or_reference_window(self):
        target, reference, corner = pair()
        corner['target']['brake_start_pct'] = .01
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertIsNone(row['entry_m'])
        self.assertIsNotNone(row['apex_m'])
        # A gap within the projection window is not joined, even away from its centre.
        reference['Lat'][285] = np.nan
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertIsNotNone(row['apex_m'])
        corner['target']['brake_start_pct'] = .3
        row = racing_line_offsets(target, reference, corner, 1000)
        self.assertIsNone(row['entry_m'])
        self.assertIsNotNone(row['apex_m'])

    def test_corner_analysis_attaches_line_and_preserves_old_metrics(self):
        target, reference, _ = pair()
        class Subject(CornerAnalysis):
            track_length_m = 1000
            def _corner_metrics(self, grid, entry, apex, exit_idx):
                return {'min_speed_kph': 144., 'exit_speed_kph': 144., 'inputs_usable': True,
                        'brake_start_pct': .3, 'min_speed_pct': .4}
            def _corner_evidence(self, *args):
                return [], 'NONE', ''
            def speed_band(self, *args):
                return None
        row = Subject()._corner_analysis(target, reference, [{'entry': 300, 'apex': 400, 'exit': 500}])[0]
        self.assertEqual(row['line']['quality'], 'ok')
        self.assertEqual(row['line']['apex_m'], 0.)
        self.assertEqual(row['target']['min_speed_kph'], 144.)
        self.assertEqual(row['delta_s'], 0.)
        no_reference = Subject()._corner_analysis(target, None, [{'entry': 300, 'apex': 400, 'exit': 500}])[0]
        self.assertEqual(no_reference['line']['quality'], 'low')


if __name__ == '__main__':
    unittest.main()
