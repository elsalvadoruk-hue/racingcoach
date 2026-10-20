"""Shared desktop LMU endpoints use native measurements and preserve unknowns."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmu_routes import LmuRoutesServices, _file_id, register_routes
from lmu_telemetry import LmuTelemetryReader, LmuTelemetryUnavailable
from test_lmu_telemetry import duckdb
from test_lmu_analysis import make_analysis_file


class WorkspaceDependencyTests(unittest.TestCase):
    def test_missing_optional_dependency_does_not_break_listing(self):
        def missing():
            raise LmuTelemetryUnavailable('의존성 없음')
        app = FastAPI()
        register_routes(app, LmuRoutesServices(discover_dirs=lambda: (),
                        dependency_check=missing))
        with TestClient(app) as client:
            response = client.post('/api/lmu/workspace/files', json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['files'], [])
            response = client.post('/api/lmu/workspace/session', json={'filename': 'a'*24})
            self.assertEqual(response.status_code, 503)

    def test_import_does_not_load_iracing_application_or_memory(self):
        script = "import sys; import lmu_workspace; assert not any(n in sys.modules for n in ['backend_api', 'app_settings', 'memory_store', 'voice_notes', 'irsdk'])"
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없습니다.')
class LmuWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root/'first.duckdb'
        self.other = self.root/'second.duckdb'
        for path in (self.path, self.other):
            make_analysis_file(path)
            with duckdb.connect(str(path)) as con:
                con.execute("INSERT INTO metadata VALUES ('CarClass', 'GT3')")
        self.file_id = _file_id(self.path)
        self.other_id = _file_id(self.other)
        self.client = self.make_client()

    def make_client(self, **overrides):
        values = {'discover_dirs': lambda: (self.root,), 'dependency_check': lambda: None}
        values.update(overrides)
        app = FastAPI()
        register_routes(app, LmuRoutesServices(**values))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def post(self, route, **fields):
        return self.client.post('/api/lmu/workspace/'+route,
                                json={'filename': self.file_id, **fields})

    def payload(self, route, **fields):
        response = self.post(route, **fields)
        self.assertEqual(response.status_code, 200, response.text)
        json.dumps(response.json(), allow_nan=False)
        return response.json()

    def test_file_catalog_uses_opaque_ids_and_unknown_validity(self):
        payload = self.payload('files')
        self.assertEqual(len(payload['files']), 2)
        row = next(r for r in payload['files'] if r['name'] == self.file_id)
        self.assertEqual(row['file_name'], 'first.duckdb')
        self.assertEqual(row['provider'], 'LMU')
        self.assertTrue(row['analyzable'])
        self.assertEqual(row['measurement_laps'], 2)
        self.assertIsNone(row['valid_laps'])
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_session_exposes_every_timed_lap_without_valid_or_best_claim(self):
        result = self.payload('session', preview=True)
        self.assertEqual(result['kind'], 'LIVE_LOCAL')
        self.assertEqual(result['provider'], 'LMU')
        self.assertEqual(result['defaultTarget'], 2)
        self.assertEqual(result['defaultReference'], 1)
        timed = [row for row in result['laps'] if row['measurement_eligible']]
        self.assertEqual(len(timed), 2)
        for row in timed:
            self.assertIsNone(row['valid'])
            self.assertEqual(row['status'], 'VALIDITY_UNKNOWN')
            self.assertIn(row['pedal_usage']['status'], ('PARTIAL', 'AVAILABLE'))
            self.assertNotIn('best', row)
        self.assertFalse(result['laps'][0]['measurement_eligible'])
        self.assertEqual(result['session']['conditions'], 'NOT_COMPARED')
        self.assertIn('setup_effect', result['unsupported_features'])
        self.assertIsNone(result['session']['track_platform_id'])

    def test_file_laps_share_native_pedal_contract(self):
        result = self.payload('file-laps')
        session = self.payload('session')
        self.assertEqual(result['laps'], session['laps'])

    def test_other_recording_can_supply_reference_without_iracing_services(self):
        for route in ('session', 'comparison'):
            result = self.payload(route, target_lap=2, reference_lap=1,
                                  reference_filename=self.other_id)
            comparison = result.get('initialComparison', result)
            self.assertEqual(comparison['reference_filename'], self.other_id)
            self.assertEqual(comparison['reference_file_name'], 'second.duckdb')
            self.assertEqual(comparison['target_lap'], 2)
            self.assertEqual(comparison['reference_lap'], 1)
            self.assertIsNone(comparison['reference_valid'])
            if route == 'session':
                self.assertEqual(result['reference_filename'], self.other_id)
                self.assertTrue(any(row['measurement_eligible'] for row in result['reference_laps']))

    def test_other_recording_reference_rejects_unmatched_car_or_missing_file(self):
        self.assertEqual(self.post('session', reference_filename='f'*24).status_code, 404)
        with duckdb.connect(str(self.other)) as con:
            con.execute("UPDATE metadata SET value = 'Other car' WHERE key = 'CarName'")
        response = self.post('comparison', reference_filename=self.other_id)
        self.assertEqual(response.status_code, 422, response.text)

    def test_comparison_converts_display_units_and_preserves_steering_ratio(self):
        result = self.payload('comparison', target_lap=2, reference_lap=1, points=31)
        self.assertEqual(len(result['trace']), 31)
        self.assertEqual(result['delta'], -1)
        self.assertTrue(all(0 <= row['distance_pct'] <= 1 for row in result['trace']))
        self.assertTrue(all(row['target_speed_kph'] == 180 for row in result['trace']))
        for row in result['trace']:
            self.assertAlmostEqual(row['target_steering_ratio'], .25)
            self.assertIsNone(row['target_steering_deg'])
            self.assertIsNone(row['target_gear'])
        self.assertEqual(result['reference_status'], 'VALIDITY_UNVERIFIED')
        self.assertEqual(result['conditions'], 'NOT_COMPARED')

    def test_schematic_paths_never_claim_geographic_coordinates(self):
        result = self.payload('comparison', target_lap=2, reference_lap=1)
        self.assertTrue(result['track_path'])
        self.assertTrue(result['reference_track_path'])
        self.assertEqual(result['geographic_identity'], 'UNVERIFIED')
        self.assertEqual(result['coordinate_system'], 'LOCAL_TANGENT_APPROXIMATION')
        for point in result['track_path']:
            self.assertIn('x_m', point)
            self.assertIn('segment', point)
            self.assertNotIn('lat', point)
            self.assertNotIn('lon', point)

    def test_display_resolution_and_range_do_not_change_native_pedals(self):
        small = self.payload('comparison', target_lap=2, reference_lap=1, points=3)
        large = self.payload('comparison', target_lap=2, reference_lap=1, points=10000)
        scoped = self.payload('comparison', target_lap=2, reference_lap=1, start_pct=.2, end_pct=.8)
        self.assertEqual(small['pedal_usage'], large['pedal_usage'])
        self.assertEqual(small['pedal_usage'], scoped['pedal_usage'])
        self.assertEqual(len(large['trace']), 501)
        self.assertTrue(all(.2 <= row['distance_pct'] <= .8 for row in scoped['trace']))

    def test_explicit_self_comparison_stays_zero(self):
        result = self.payload('comparison', target_lap=1, reference_lap=1)
        self.assertEqual(result['delta'], 0)
        self.assertTrue(all(row['delta_s'] == 0 for row in result['trace']))

    def test_unsupported_references_explicitly_reported_and_not_silently_used(self):
        result = self.payload('references')
        self.assertEqual(result['files'], [])
        self.assertEqual(result['status'], 'UNSUPPORTED')
        for selection in ({'reference_mode': 'SINGLE'}, {'external_reference_files': ['x.csv']}):
            self.assertEqual(self.post('comparison', **selection).status_code, 422)

    def test_overlay_has_shared_distance_series_and_unknown_validity(self):
        result = self.payload('overlay', laps=[1, 2], base_lap=1, start_pct=.2, end_pct=.8)
        self.assertEqual(set(result['series']), {'1', '2'})
        self.assertEqual(result['base_key'], '1')
        self.assertGreaterEqual(min(result['distance_pct']), .2)
        self.assertLessEqual(max(result['distance_pct']), .8)
        self.assertTrue(all(delta == 0 for delta in result['series']['1']['delta']))
        self.assertTrue(all(row['valid'] is None for row in result['laps']))
        self.assertEqual(result['series']['1']['steering'], [25]*501)
        self.assertEqual(next(channel for channel in result['channels'] if channel['key'] == 'steering')['unit'], '%')
        self.assertEqual(result['series']['1']['speed'], [180]*501)

    def test_overlay_supports_explicit_other_lmu_recording(self):
        result = self.payload('overlay', laps=[1], extra=[{'source': 'IBT', 'filename': self.other_id, 'lap': 2}])
        self.assertIn(f'ibt:{self.other_id}#2', result['series'])
        self.assertEqual(result['laps'][1]['source'], 'LMU')

    def test_cross_file_metadata_mismatch_is_rejected(self):
        with duckdb.connect(str(self.other)) as con:
            con.execute("UPDATE metadata SET value='Other' WHERE key='TrackName'")
        response = self.post('overlay', laps=[1], extra=[{'source': 'LMU', 'filename': self.other_id, 'lap': 2}])
        self.assertEqual(response.status_code, 422)

    def test_overlay_does_not_compare_different_input_stages(self):
        with duckdb.connect(str(self.other)) as con:
            con.execute('CREATE TABLE "Brake Pos Unfiltered" AS SELECT * FROM "Brake Pos"')
            con.execute("INSERT INTO channelsList VALUES ('Brake Pos Unfiltered',50,'%')")
        result = self.payload('overlay', laps=[1], extra=[{'source': 'LMU', 'filename': self.other_id, 'lap': 2}])
        self.assertNotIn('brake', result['series']['1'])
        self.assertNotIn('brake', result['series'][f'ibt:{self.other_id}#2'])
        self.assertEqual(result['channel_support']['brake_ratio']['status'], 'UNAVAILABLE')
        self.assertIn('throttle', result['series']['1'])

    def test_missing_channels_stay_null_without_fabricated_measurements(self):
        with duckdb.connect(str(self.path)) as con:
            con.execute("DELETE FROM channelsList WHERE channelName IN ('Brake Pos','GPS Latitude')")
        result = self.payload('comparison', target_lap=2, reference_lap=1)
        self.assertTrue(all(row['target_brake'] is None for row in result['trace']))
        self.assertEqual(result['track_path'], [])
        self.assertEqual(result['corner_status'], 'UNAVAILABLE')
        self.assertEqual(result['pedal_usage']['target']['status'], 'UNAVAILABLE')

    def test_unknown_id_path_traversal_bad_laps_and_invalid_bounds(self):
        for name in ('../first.duckdb', str(self.path), '', 42):
            self.assertEqual(self.post('session', filename=name).status_code, 422)
        self.assertEqual(self.post('session', filename='f'*24).status_code, 404)
        for fields in ({'target_lap': True}, {'reference_lap': -1}, {'target_lap': 900},
                       {'target_lap': 0}, {'points': False}, {'points': 2},
                       {'start_pct': .9, 'end_pct': .2}):
            self.assertEqual(self.post('comparison', **fields).status_code, 422, fields)
        for fields in ({'laps': [{}]}, {'laps': [True]}, {'laps': []}, {'laps': [1], 'extra': [{}]}):
            self.assertEqual(self.post('overlay', **fields).status_code, 422, fields)

    def test_wal_file_is_shown_but_never_analyzed(self):
        Path(str(self.path)+'.wal').write_bytes(b'pending')
        listing = self.payload('files')
        row = next(r for r in listing['files'] if r['name'] == self.file_id)
        self.assertEqual(row['status'], 'WAL_PRESENT')
        self.assertFalse(row['analyzable'])
        self.assertEqual(self.post('session').status_code, 422)

    def test_snapshot_mutation_during_analysis_rejected(self):
        class MutatingReader(LmuTelemetryReader):
            def analysis_lap(inner, number):
                result = super().analysis_lap(number)
                state = inner.path.stat()
                os.utime(inner.path, ns=(state.st_atime_ns, state.st_mtime_ns+1_000_000))
                return result
        client = self.make_client(reader_factory=MutatingReader)
        response = client.post('/api/lmu/workspace/comparison', json={
            'filename': self.file_id, 'target_lap': 2, 'reference_lap': 1})
        self.assertEqual(response.status_code, 422)
        self.assertIn('변경', response.json()['detail'])

    def test_readers_run_off_event_loop_and_source_files_are_preserved(self):
        def factory(path):
            self.assertNotIn('asyncio-portal', threading.current_thread().name)
            return LmuTelemetryReader(path)
        self.client = self.make_client(reader_factory=factory)
        before = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (self.path, self.other)}
        for route in ('files', 'file-laps', 'references', 'session', 'comparison'):
            self.payload(route)
        self.payload('overlay', laps=[1, 2])
        after = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (self.path, self.other)}
        self.assertEqual(before, after)
        self.assertFalse(Path(str(self.path)+'.wal').exists())


if __name__ == '__main__':
    unittest.main()
