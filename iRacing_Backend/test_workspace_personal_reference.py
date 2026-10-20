"""Real desktop cross-file comparisons and fallback, all files/stores synthetic."""
from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from test_rolling_best import BackToBackLaps, api


class WorkspacePersonalReferenceTests(BackToBackLaps, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sources = {}
        for name, seconds in (('current.ibt', 55), ('best.ibt', 50), ('rain.ibt', 45)):
            source = self.telemetry([(seconds, seconds), (seconds + 2, seconds + 2)])
            path = Path(self.work.name) / name
            path.write_bytes(name.encode())
            pct = source.channels['LapDistPct']
            source.channels.update(SteeringWheelAngle=np.sin(pct * 2 * np.pi) * .2,
                TrackTemp=np.full(len(pct), 30.), AirTemp=np.full(len(pct), 20.),
                TrackWetness=np.full(len(pct), 5 if name == 'rain.ibt' else 1),
                Precipitation=np.full(len(pct), .2 if name == 'rain.ibt' else 0.),
                WeatherDeclaredWet=np.full(len(pct), name == 'rain.ibt'))
            source.metadata.update(car_platform_id='42', track_platform_id='100', car_path='gt3', track_path='monza full', track_config='GP')
            self.sources[name] = replace(source, path=path, signature=api.file_signature(path))
        self.state = {'analysis_result': None, 'last_analysis_time': None, 'file_signature': None}
        patches = self.enterContext(ExitStack())
        patches.enter_context(patch.dict(os.environ, {'IRACING_PERSONAL_REFERENCE_DB_PATH': str(Path(self.work.name) / 'library.sqlite3')}))
        patches.enter_context(patch.object(api, 'shared_sync_state', self.state))
        patches.enter_context(patch.object(api, 'memory_store', None))
        patches.enter_context(patch.object(api, 'refresh_live_practice'))
        patches.enter_context(patch.object(api, 'review_notes', return_value={'notes': [], 'pending': []}))
        patches.enter_context(patch.object(api, 'resolve_telemetry_file', side_effect=lambda name: self.sources[name].path if name in self.sources else None))
        patches.enter_context(patch.object(api.repository, 'load', side_effect=lambda path: self.sources[path.name]))
        patches.enter_context(patch.object(api, 'list_ibt_files', side_effect=lambda limit: [source.path for source in self.sources.values()]))
        # Older already-analyzed file metadata cache lacks platform IDs.
        patches.enter_context(patch.object(api, 'quick_file_visibility', side_effect=lambda path:
            {key: value for key, value in self.sources[path.name].metadata.items() if not key.endswith('_platform_id')}))
        patches.enter_context(patch.object(api, 'list_external_reference_metadata', return_value={'files': []}))
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def post(self, route='session', **extra):
        return self.client.post('/api/workspace/' + route, json={'filename': 'current.ibt', 'target_lap': 2, **extra})

    def test_auto_uses_faster_other_file_and_all_charts_corners_times_agree(self):
        response = self.post(reference_selection='AUTO')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['defaultReferenceFile'], 'best.ibt')
        self.assertEqual(result['reference_selection'], 'AUTO')
        comparison = result['initialComparison']
        self.assertEqual(comparison['referenceTime'], 100)
        self.assertEqual(comparison['targetTime'], 110)
        self.assertEqual(comparison['delta'], 10)
        self.assertEqual(comparison['reference_filename'], 'best.ibt')
        self.assertTrue(comparison['reference_track_path'] == [] or isinstance(comparison['reference_track_path'], list))
        finite = [row['delta_s'] for row in comparison['trace'] if row['delta_s'] is not None]
        self.assertAlmostEqual(finite[-1], 10, delta=.1)
        self.assertEqual(result['analysisResult']['comparison']['reference_filename'], 'best.ibt')
        self.assertEqual(result['analysisResult']['setup_recommendation'], {})
        self.assertEqual(self.state['analysis_result']['comparison']['reference_filename'], 'best.ibt')

    def test_current_fastest_is_allowed_as_explicit_zero_baseline(self):
        self.sources.pop('best.ibt')
        response = self.post(reference_selection='AUTO')
        self.assertEqual(response.status_code, 200, response.text)
        comparison = response.json()['initialComparison']
        self.assertEqual(comparison['reference_filename'], 'current.ibt')
        self.assertEqual(comparison['delta'], 0)
        self.assertTrue(comparison['personalReference']['same_lap'])

    def test_garage61_missing_falls_back_to_condition_matched_personal_best(self):
        response = self.post(reference_mode='SINGLE', external_reference_files=['disconnected.csv'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['defaultReferenceFile'], 'best.ibt')
        self.assertEqual(response.json()['referenceFallback'], 'GARAGE61_UNAVAILABLE')

    def test_manual_other_file_revalidated_and_weather_mismatch_rejected(self):
        response = self.post('comparison', reference_selection='MANUAL', reference_filename='best.ibt', reference_lap=2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['referenceTime'], 100)
        response = self.post('comparison', reference_selection='MANUAL', reference_filename='rain.ibt', reference_lap=2)
        self.assertEqual(response.status_code, 422, response.text)
        response = self.post('comparison', reference_selection='MANUAL', reference_filename='../best.ibt', reference_lap=2)
        self.assertEqual(response.status_code, 422, response.text)

    def test_cross_file_unknown_surface_excluded_until_observed(self):
        self.sources['best.ibt'].channels.pop('TrackWetness')
        self.sources['best.ibt'].channels.pop('WeatherDeclaredWet')
        response = self.post(reference_selection='AUTO')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['defaultReferenceFile'], 'current.ibt')


if __name__ == '__main__':
    unittest.main()
