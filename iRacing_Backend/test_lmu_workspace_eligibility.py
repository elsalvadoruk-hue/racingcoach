"""Measurement eligibility requires an observed distance as well as lap timing."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_routes import LmuRoutesServices, _file_id, register_routes
from lmu_workspace import _eligible
from test_lmu_analysis import make_analysis_file
from test_lmu_telemetry import duckdb


class WorkspaceEligibilityTests(unittest.TestCase):
    def test_missing_nonfinite_or_zero_distance_is_not_measurement_eligible(self):
        for distance in (None, float('nan'), float('inf'), -1., 0.):
            with self.subTest(distance=distance):
                segment = SimpleNamespace(status='VALIDITY_UNKNOWN',
                    recorded_lap_time_s=90., distance_m=distance)
                self.assertFalse(_eligible(segment))

    def test_observed_distance_keeps_game_validity_unknown(self):
        segment = SimpleNamespace(status='VALIDITY_UNKNOWN',
            recorded_lap_time_s=90., distance_m=5000.)
        self.assertTrue(_eligible(segment))


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없습니다.')
class WorkspaceDistanceCatalogTests(unittest.TestCase):
    def test_unavailable_distance_axes_are_not_advertised_as_analyzable(self):
        mutations = {
            'missing_distance': "DELETE FROM channelsList WHERE channelName='Lap Dist'",
            'unsupported_distance_unit': "UPDATE channelsList SET unit='unknown' WHERE channelName='Lap Dist'",
            'missing_time_axis': "DELETE FROM channelsList WHERE channelName='GPS Time'",
            'stationary_distance': 'UPDATE "Lap Dist" SET value=0',
        }
        for name, query in mutations.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root/'recording.duckdb'
                make_analysis_file(path)
                with duckdb.connect(str(path)) as connection:
                    connection.execute(query)
                app = FastAPI()
                register_routes(app, LmuRoutesServices(discover_dirs=lambda: (root,),
                    dependency_check=lambda: None))
                with TestClient(app) as client:
                    listing = client.post('/api/lmu/workspace/files', json={})
                    self.assertEqual(listing.status_code, 200, listing.text)
                    row, = listing.json()['files']
                    self.assertFalse(row['analyzable'])
                    self.assertEqual(row['measurement_laps'], 0)
                    self.assertIsNone(row['valid_laps'])
                    body = {'filename': _file_id(path)}
                    response = client.post('/api/lmu/workspace/file-laps', json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    timed = [lap for lap in response.json()['laps'] if lap['lap_time_s']]
                    self.assertTrue(timed)
                    for lap in timed:
                        self.assertFalse(lap['measurement_eligible'])
                        self.assertFalse(lap['complete'])
                        self.assertIsNone(lap['valid'])
                    session = client.post('/api/lmu/workspace/session', json=body)
                    self.assertEqual(session.status_code, 422, session.text)

    def test_normal_recording_remains_measurable_in_catalog_and_lap_list(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'recording.duckdb'
            make_analysis_file(path)
            app = FastAPI()
            register_routes(app, LmuRoutesServices(discover_dirs=lambda: (root,),
                dependency_check=lambda: None))
            with TestClient(app) as client:
                listing = client.post('/api/lmu/workspace/files', json={})
                self.assertEqual(listing.status_code, 200, listing.text)
                row, = listing.json()['files']
                self.assertTrue(row['analyzable'])
                self.assertEqual(row['measurement_laps'], 2)
                response = client.post('/api/lmu/workspace/file-laps',
                    json={'filename': _file_id(path)})
                self.assertEqual(response.status_code, 200, response.text)
                measured = [lap for lap in response.json()['laps'] if lap['measurement_eligible']]
                self.assertEqual(len(measured), 2)
                self.assertTrue(all(lap['valid'] is None for lap in measured))


if __name__ == '__main__':
    unittest.main()
