"""Resource regressions with synthetic buffers and disposable files only."""
from collections import deque
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from analysis_laps import LapAnalysis
from corner_events import raw_lap
from live_telemetry import LiveTelemetry
from personal_reference import PersonalReferenceIndex


class LiveViewResourceTests(unittest.TestCase):
    def setUp(self):
        self.live = LiveTelemetry(clock=lambda: 1000.)
        self.addCleanup(self.live.drill.history.close)
        self.live.state = 'CONNECTED'
        self.live.samples.append({'wall_time': 1000., 'session_time': 10., 'fuel_l': 10.})
        self.live.fuel_history.extend([2., 2.1, 1.9])

    def test_status_does_not_walk_or_copy_voice_sample_history(self):
        class HistoryGuard(deque):
            def __reversed__(self):
                raise AssertionError('Status must not build a voice sample trail')
        self.live.samples = HistoryGuard(self.live.samples, maxlen=5400)
        status = self.live.status()
        self.assertTrue(status['connected'])
        self.assertNotIn('recent', status)
        self.assertEqual(status['snapshot']['fuel_l'], 10.)

    def test_race_view_computes_pit_view_once_and_does_not_build_voice_context(self):
        with patch.object(self.live, 'context', side_effect=AssertionError('Unused voice context')):
            with patch.object(self.live, '_pit_view', wraps=self.live._pit_view) as view:
                result = self.live.race_view(reserve_laps=.7, pit_loss_s=22., fuel_save_mode='stint')
        self.assertIsInstance(result, dict)
        view.assert_called_once_with(self.live.samples[-1], 2.1, 3, .7, 22., 'stint')

    def test_status_matches_context_without_the_private_sample_trail(self):
        context = self.live.context()
        self.assertTrue(context['recent'])
        status = self.live.status()
        for key, value in context.items():
            if key != 'recent':
                self.assertEqual(status[key], value, key)

    def test_disconnected_and_two_second_old_samples_cannot_report_a_race(self):
        for state, age, connected in [('CONNECTED', 1.999, True), ('CONNECTED', 2., False),
                                      ('WAITING', 0., False), ('STOPPED', 0., False)]:
            with self.subTest(state=state, age=age):
                self.live.state = state
                self.live.samples[-1]['wall_time'] = 1000. - age
                self.assertEqual(self.live.context()['connected'], connected)
                self.assertEqual(self.live.status()['connected'], connected)
                self.assertEqual(self.live.race_view() is not None, connected)


class RawLapResourceTests(unittest.TestCase):
    def setUp(self):
        self.lap = SimpleNamespace(lap_number=1, start_idx=1, end_idx=3)
        channels = {key: np.array([0., .1, np.nan, .3, .4], dtype=np.float32)
                    for key in ('Speed', 'Brake', 'Throttle', 'SteeringWheelAngle', 'YawRate',
                                'LatAccel', 'BrakeABSactive', 'UnusedSensor')}
        channels.update(SessionTime=np.arange(5.), LapDistPct=np.linspace(0, 1, 5),
                        BadLength=np.arange(2.), Matrix=np.zeros((5, 2)))
        self.subject = SimpleNamespace(channels=channels, telemetry=SimpleNamespace(laps=[self.lap], tick_rate=60, metadata={}),
                                       track_length_m=5000., _grid_ids={}, _raw_lap_cache={})

    def test_default_raw_lap_keeps_all_eligible_channels(self):
        raw = raw_lap(self.subject, self.lap)
        self.assertIn('UnusedSensor', raw.channels)
        self.assertNotIn('BadLength', raw.channels)
        self.assertNotIn('Matrix', raw.channels)

    def test_requested_channels_preserve_float64_values_missing_data_and_span(self):
        raw = raw_lap(self.subject, self.lap, requested=('Speed', 'Missing', 'BadLength'))
        self.assertEqual(set(raw.channels), {'Speed'})
        self.assertEqual(raw.channels['Speed'].dtype, np.dtype(float))
        np.testing.assert_array_equal(raw.channels['Speed'], self.subject.channels['Speed'][1:4].astype(float))
        np.testing.assert_array_equal(raw.time_s, [1., 2., 3.])

    def test_analysis_raw_cache_keeps_event_and_handling_channels_without_unused_sensor_copies(self):
        grid = {'_lap_number': 1}
        self.subject._grid_ids[id(grid)] = (grid, ('lap',))
        raw = LapAnalysis._raw_lap_for(self.subject, grid)
        self.assertEqual(set(raw.channels), {'Speed', 'SteeringWheelAngle', 'YawRate', 'LatAccel', 'Brake', 'Throttle', 'BrakeABSactive'})
        self.assertIs(raw, LapAnalysis._raw_lap_for(self.subject, grid))
        self.assertIsNone(LapAnalysis._raw_lap_for(self.subject, dict(grid)))


class ReferenceIndexResourceTests(unittest.TestCase):
    def test_batch_lookup_matches_individual_signature_version_and_game_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = PersonalReferenceIndex(root / 'index.sqlite3')
            paths = [root / (name + '.ibt') for name in ('good', 'changed', 'deleted', 'version', 'game', 'unknown')]
            for path in paths:
                path.write_bytes(b'fixture')
            for i, path in enumerate(paths[:-1]):
                index.index_recording('LMU' if i == 4 else 'IRACING', path, {}, [], version='old' if i == 3 else 'current')
            paths[1].write_bytes(b'changed size')
            paths[2].unlink()
            expected = {str(p.resolve()) for p in paths if index.has_recording('IRACING', p, 'current')}
            with patch.object(index, '_connect', wraps=index._connect) as connect:
                found = index.current_recordings('IRACING', paths, 'current')
            self.assertEqual(found, expected)
            self.assertEqual(found, {str(paths[0].resolve())})
            connect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
