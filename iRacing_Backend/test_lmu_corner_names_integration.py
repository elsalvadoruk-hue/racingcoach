"""Corner-name routes and workspace labels with fake readers and temp stores.

No DuckDB, live game, personal database, or personal naming store is accessed.
The fake files are immutable sentinels used only by the real snapshot guards.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_corner_names import LmuCornerNamesStore
from lmu_practice_store import LmuPracticeStore
from lmu_routes import LmuRoutesServices, _file_fingerprint, _file_id, register_routes
from lmu_telemetry import LmuLapSegment, LmuTelemetrySummary
from test_lmu_corners import lap


class FakeReader:
    def __init__(self, path, laps, requests):
        self.path, self.laps, self.requests = path, laps, requests

    def summary(self):
        segments = tuple(LmuLapSegment(number, observed.start_time_s, observed.end_time_s,
                            0, len(observed.channels['distance_m'].values),
                            float(observed.channels['distance_m'].values[-1]),
                            observed.end_time_s-observed.start_time_s, observed.recorded_lap_time_s,
                            None, 'VALIDITY_UNKNOWN') for number, observed in self.laps.items())
        return LmuTelemetrySummary(self.path, dict(next(iter(self.laps.values())).metadata), (), (), {}, segments)

    def analysis_lap(self, number):
        self.requests.append((self.path, number, threading.current_thread().name))
        try:
            return self.laps[number]
        except KeyError as exc:
            raise ValueError('측정할 수 있는 랩이 없습니다.') from exc


class LmuCornerNamesIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = [self.root/'target.duckdb', self.root/'reference.duckdb']
        for source in self.files:
            source.write_bytes(b'fake reader sentinel; never open as telemetry')
        self.primary, self.other = map(_file_id, self.files)
        self.laps = {
            self.files[0]: {1: lap(number=1), 2: lap(speed=40., number=2),
                            3: lap(number=3, bend=(850., 1050.))},
            self.files[1]: {1: lap(number=1, bend=(850., 1050.)),
                            2: lap(speed=40., number=2, bend=(850., 1050.))},
        }
        self.requests = []
        self.store = LmuCornerNamesStore(self.root/'names'/'corners.json')
        self.practice = LmuPracticeStore(self.root/'practice'/'practice.sqlite3')
        self.snapshots = {source: (source.read_bytes(), source.stat().st_mtime_ns) for source in self.files}
        self.observed = {(source, number): (deepcopy(observed.metadata), deepcopy(observed.support),
                          {key: (trace.time_s.copy(), trace.values.copy(), trace.valid_mask.copy())
                           for key, trace in observed.channels.items()})
                         for source, laps in self.laps.items() for number, observed in laps.items()}
        app = FastAPI()
        register_routes(app, LmuRoutesServices(discover_dirs=lambda: (), list_files=lambda _: self.files,
            dependency_check=lambda: None,
            reader_factory=lambda source: FakeReader(source, self.laps[source], self.requests),
            practice_store=self.practice, corner_names_store=self.store))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def tearDown(self):
        for source, before in self.snapshots.items():
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
            self.assertFalse(Path(str(source)+'.wal').exists())
        for (source, number), (metadata, support, channels) in self.observed.items():
            observed = self.laps[source][number]
            self.assertEqual(observed.metadata, metadata)
            self.assertEqual(observed.support, support)
            for key, before in channels.items():
                trace = observed.channels[key]
                for actual, expected in zip((trace.time_s, trace.values, trace.valid_mask), before):
                    np.testing.assert_array_equal(actual, expected)
        self.assertFalse(self.practice.path.exists())

    def selection(self, **overrides):
        return {'filename': self.primary, 'target_lap': 2, 'reference_lap': 1, **overrides}

    def get_names(self, **overrides):
        response = self.client.get('/api/lmu/corner-names', params=self.selection(**overrides))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def put_names(self, entries=None, **overrides):
        if entries is None:
            rows = self.get_names(**overrides)['corners']
            entries = [dict(id=row['id'], number='1-2', name='첫 시케인', spoken='첫 시케인 진입') for row in rows]
        return self.client.put('/api/lmu/corner-names', json={**self.selection(**overrides), 'corners': entries})

    def workspace(self, route, **overrides):
        response = self.client.post('/api/lmu/workspace/'+route, json=self.selection(**overrides))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_get_regenerates_shared_geometry_without_creating_any_store(self):
        result = self.get_names()
        comparison = self.workspace('comparison')
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['suggestion_status'], 'NO_OFFICIAL_MAP')
        self.assertEqual([row['id'] for row in result['corners']], ['C1'])
        for row, corner in zip(result['corners'], comparison['corners']):
            for field in ('id', 'entry_pct', 'apex_pct', 'exit_pct'):
                self.assertEqual(row[field], corner[field])
            self.assertIsNone(row['number'])
            self.assertIsNone(row['call'])
        self.assertFalse(self.store.path.parent.exists())
        self.assertTrue(self.requests)
        self.assertTrue(all('asyncio-portal' not in name for _, _, name in self.requests))

    def test_get_suggestion_does_not_confirm_or_inject_labels_into_comparison(self):
        proposed = {'status': 'SUGGESTED', 'kind': 'numbered', 'fit_median_m': 5.,
                    'corners': {'C1': {'number': '9', 'name': 'Reference suggestion', 'labels': ['9']}}}
        with patch('lmu_corner_names._suggest', return_value=proposed):
            result = self.get_names()
        self.assertEqual(result['corners'][0]['suggested_number'], '9')
        self.assertIsNone(result['corners'][0]['number'])
        self.assertFalse(result['confirmed'])
        self.assertFalse(self.store.path.exists())
        comparison = self.workspace('comparison')
        self.assertFalse(comparison['corner_names_confirmed'])
        self.assertIsNone(comparison['corners'][0]['official_turn_number'])

    def test_put_persists_and_labels_comparison_session_and_driving_summary(self):
        before = self.workspace('comparison')
        response = self.put_names()
        self.assertEqual(response.status_code, 200, response.text)
        confirmed = response.json()
        self.assertTrue(confirmed['confirmed'])
        self.assertEqual(confirmed['corners'][0]['call'], '첫 시케인 진입')
        self.assertTrue(self.store.path.is_file())
        self.assertEqual(self.get_names()['corners'][0]['number'], '1-2')
        comparison = self.workspace('comparison')
        session = self.workspace('session')
        self.assertTrue(comparison['corner_names_confirmed'])
        for corners in (comparison['corners'], session['corners'], session['initialComparison']['corners']):
            row = corners[0]
            self.assertEqual(row['official_turn_number'], '1-2')
            self.assertEqual(row['turn_name'], '첫 시케인')
            self.assertEqual(row['spoken'], '첫 시케인 진입')
            self.assertEqual(row['label'], '1-2번 코너 · 첫 시케인')
        self.assertTrue(comparison['practiceSummary']['options'])
        self.assertIn('첫 시케인', comparison['practiceSummary']['options'][0]['focus']['root_cause_label'])
        for field in ('trace', 'track_path', 'reference_track_path', 'corner_geometry', 'channels',
                      'pedal_usage', 'braking', 'distance_sectors', 'distance_support', 'delta'):
            self.assertEqual(comparison[field], before[field], field)
        naming = {'official_turn_number', 'turn_name', 'spoken', 'label'}
        self.assertEqual([{k: v for k, v in row.items() if k not in naming} for row in comparison['corners']],
                         [{k: v for k, v in row.items() if k not in naming} for row in before['corners']])
        self.assertIsNone(comparison['target_valid'])
        self.assertEqual(comparison['reference_status'], 'VALIDITY_UNVERIFIED')

    def test_selected_reference_file_controls_geometry_and_saved_anchor(self):
        default = self.get_names()
        chosen = self.get_names(reference_filename=self.other)
        self.assertNotAlmostEqual(default['corners'][0]['apex_pct'], chosen['corners'][0]['apex_pct'])
        response = self.put_names(reference_filename=self.other)
        self.assertEqual(response.status_code, 200, response.text)
        points = self.store.track_names(self.laps[self.files[1]][1].metadata)['points']
        self.assertEqual(points[0]['apex_pct'], chosen['corners'][0]['apex_pct'])
        selected = self.workspace('comparison', reference_filename=self.other)
        self.assertEqual(selected['reference_filename'], self.other)
        self.assertEqual(selected['corners'][0]['turn_name'], '첫 시케인')
        self.assertFalse(self.workspace('comparison')['corner_names_confirmed'])
        self.assertIsNone(self.workspace('comparison')['corners'][0]['turn_name'])

    def test_names_apply_to_another_recording_and_do_not_depend_on_car_name(self):
        response = self.put_names()
        self.assertEqual(response.status_code, 200, response.text)
        # A later file can retain the same geometry with another vehicle. Both
        # laps in that comparison still must match each other's car metadata.
        for number, observed in self.laps[self.files[0]].items():
            self.laps[self.files[1]][number] = replace(observed, metadata={**observed.metadata, 'car_name': 'Another car'})
        # Update test-owned snapshots for the intentionally replaced fixtures.
        for number, observed in self.laps[self.files[1]].items():
            self.observed[self.files[1], number] = (dict(observed.metadata), deepcopy(observed.support),
                {key: (trace.time_s.copy(), trace.values.copy(), trace.valid_mask.copy()) for key, trace in observed.channels.items()})
        response = self.client.post('/api/lmu/workspace/comparison', json={
            'filename': self.other, 'target_lap': 2, 'reference_lap': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['corners'][0]['turn_name'], '첫 시케인')

    def test_selected_reference_lap_controls_geometry_in_both_get_and_put(self):
        original = self.get_names()
        selected = self.get_names(reference_lap=3)
        self.assertNotAlmostEqual(original['corners'][0]['apex_pct'], selected['corners'][0]['apex_pct'])
        response = self.put_names(reference_lap=3)
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.store.track_names(self.laps[self.files[0]][3].metadata)['points'][0]
        self.assertEqual(saved['apex_pct'], selected['corners'][0]['apex_pct'])
        self.assertEqual(self.workspace('comparison', reference_lap=3)['corners'][0]['turn_name'], '첫 시케인')
        self.assertFalse(self.workspace('comparison')['corner_names_confirmed'])

    def test_invalid_edit_is_rejected_without_replacing_confirmed_names(self):
        self.assertEqual(self.put_names().status_code, 200)
        before = self.store.path.read_bytes()
        for entries in ([], [{'id': 'wrong', 'number': '9'}],
                        [{'id': 'C1', 'number': '9'}, {'id': 'C1', 'number': '10'}],
                        [{'id': 'C1', 'spoken': 'bad\ntext'}]):
            response = self.put_names(entries)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.store.path.read_bytes(), before)
        response = self.client.put('/api/lmu/corner-names', json=self.selection())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_explicit_blank_confirmation_removes_labels_from_next_comparison(self):
        default_label = self.workspace('comparison')['corners'][0]['label']
        self.assertEqual(self.put_names().status_code, 200)
        response = self.put_names([{'id': 'C1', 'number': '', 'name': '', 'spoken': ''}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['confirmed'])
        compared = self.workspace('comparison')
        self.assertFalse(compared['corner_names_confirmed'])
        self.assertIsNone(compared['corners'][0]['official_turn_number'])
        self.assertIsNone(compared['corners'][0]['turn_name'])
        self.assertEqual(compared['corners'][0]['label'], default_label)

    def test_recording_snapshot_mismatch_rejects_put_before_store_write(self):
        calls = {}
        def changed_fingerprint(source):
            signature = _file_fingerprint(source)
            calls[source] = calls.get(source, 0)+1
            return signature if calls[source] == 1 else (signature[0], signature[1]+1, signature[2])
        with patch('lmu_routes._file_fingerprint', side_effect=changed_fingerprint):
            response = self.put_names([{'id': 'C1', 'number': '1'}])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('변경', response.json()['detail'])
        self.assertFalse(self.store.path.exists())

    def test_unknown_sources_and_invalid_reference_selection_do_not_write(self):
        for overrides, expected in (({'reference_filename': 'f'*24}, 404),
                                    ({'reference_lap': 999}, 422), ({'reference_lap': -1}, 422)):
            selection = {**self.selection(), **overrides}
            response = self.client.get('/api/lmu/corner-names', params=selection)
            self.assertEqual(response.status_code, expected, response.text)
            response = self.client.put('/api/lmu/corner-names', json={**selection, 'corners': [{'id': 'C1'}]})
            self.assertEqual(response.status_code, expected, response.text)
        self.assertFalse(self.store.path.exists())


if __name__ == '__main__':
    unittest.main()
