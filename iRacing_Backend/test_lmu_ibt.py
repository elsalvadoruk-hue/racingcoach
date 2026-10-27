"""LMU DuckDB recordings presented as parsed IBT files for the shared analysis."""
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(), 'lmu-ibt.sqlite3'))

try:
    import duckdb
except ImportError:
    duckdb = None

RADIUS, SPEED, LAPS = 200., 40., 4
LENGTH = 2 * math.pi * RADIUS
LAP_S = LENGTH / SPEED          # 31.4 s
START = 10.


def make_circuit(path, *, track='Test Ring', layout='Test Ring', off_lap=3, reset_delay_s=.05):
    """Counter-clockwise ring at constant speed; one lap leaves the track edge."""
    duration = LAPS * LAP_S + 2
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE metadata (key VARCHAR, value VARCHAR)')
    con.executemany('INSERT INTO metadata VALUES (?, ?)', [
        ('TrackName', track), ('TrackLayout', layout), ('CarName', 'Test GT3 #7'), ('CarClass', 'GT3'),
        ('SessionType', 'Practice'), ('RecordingTime', '2026-10-20T10_00_00Z')])
    con.execute('CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE, unit VARCHAR)')

    def channel(name, hz, unit, function):
        t = START + np.arange(int(duration * hz)) / hz
        con.execute('INSERT INTO channelsList VALUES (?, ?, ?)', [name, hz, unit])
        values = [float(v) for v in function(t)]
        con.execute(f'CREATE TABLE "{name}" AS SELECT CAST(unnest(?) AS DOUBLE) AS value', [values])

    travelled = lambda t: (t - START) * SPEED
    # The distance resets just after the lap event, as in real LMU files.
    channel('GPS Time', 100, 's', lambda t: t)
    channel('Lap Dist', 10, 'm', lambda t: np.mod(travelled(t) - SPEED * reset_delay_s, LENGTH))
    channel('Ground Speed', 100, 'km/h', lambda t: np.full(len(t), SPEED * 3.6))
    channel('Throttle Pos', 50, '%', lambda t: np.full(len(t), 80.))
    channel('Brake Pos', 50, '%', lambda t: np.zeros(len(t)))
    channel('Steering Pos', 100, '%', lambda t: np.full(len(t), -20.))   # LMU: negative steers left
    channel('Fuel Level', 20, 'L', lambda t: 60 - (t - START) * .05)
    angle = lambda t: travelled(t) / RADIUS
    channel('GPS Latitude', 10, 'deg', lambda t: 60 + np.degrees(RADIUS * np.sin(angle(t)) / 6371000))
    channel('GPS Longitude', 10, 'deg', lambda t: np.degrees(RADIUS * np.cos(angle(t)) / 6371000 / math.cos(math.radians(60))))
    off = lambda t: (t > START + (off_lap - 1) * LAP_S + 10) & (t < START + (off_lap - 1) * LAP_S + 13)
    channel('Path Lateral', 10, 'm', lambda t: np.where(off(t), 9., 0.))
    channel('Track Edge', 10, 'm', lambda t: np.full(len(t), 6.))
    lap_times = [START + (index + 1) * LAP_S for index in range(LAPS)]
    for name, rows in {
        'Lap': [(START, 0)] + [(stamp, index + 1) for index, stamp in enumerate(lap_times)],
        'Lap Time': [(START, 0)] + [(stamp, LAP_S) for stamp in lap_times],
        'In Pits': [(START, 0)], 'Gear': [(START, 4)], 'ABS': [(START, 0)],
        'Brake Bias Rear': [(START, .45)],
    }.items():
        con.execute(f'CREATE TABLE "{name}" (ts DOUBLE, value DOUBLE)')
        con.executemany(f'INSERT INTO "{name}" VALUES (?, ?)', rows)
    con.close()


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuIbtTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / 'Test Ring_P_2026-10-20T10_00_00Z.duckdb'
        make_circuit(self.path)
        patcher = patch.dict(os.environ, {'IRACING_LMU_STEERING_PATH': str(self.folder / 'steering.json')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def parse(self):
        from lmu_ibt import LmuIbtReader
        return LmuIbtReader(self.path).parse()

    def test_laps_are_cut_at_the_line_and_an_off_track_lap_is_invalid(self):
        telemetry = self.parse()
        self.assertEqual(telemetry.tick_rate, 60)
        # LMU numbers the first lap of a practice run 0 (as the earlier LMU screens showed).
        self.assertEqual([lap.lap_number for lap in telemetry.laps], [0, 1, 2, 3])
        self.assertEqual([lap.status for lap in telemetry.laps], ['VALID', 'VALID', 'INVALID', 'VALID'])
        self.assertEqual(telemetry.laps[2].invalid_reason, 'beyond the measured track edge')
        for lap in telemetry.laps:
            self.assertAlmostEqual(lap.lap_time_s, LAP_S, places=3)
            self.assertLess(lap.start_pct, .02)
            self.assertGreater(lap.end_pct, .98)
        self.assertAlmostEqual(telemetry.metadata['track_length_m'], LENGTH, delta=5)

    def test_channels_use_iracing_names_units_and_signs(self):
        c = self.parse().channels
        middle = slice(1000, 3000)
        np.testing.assert_allclose(c['Speed'][middle], SPEED)
        np.testing.assert_allclose(c['Throttle'][middle], .8)
        self.assertTrue(np.all(c['SteeringWheelAngle'][middle] > 0))           # left is positive
        self.assertAlmostEqual(float(np.median(c['YawRate'][middle])), SPEED / RADIUS, delta=.02)
        self.assertAlmostEqual(float(np.median(c['LatAccel'][middle])), SPEED ** 2 / RADIUS, delta=.5)
        self.assertAlmostEqual(float(np.median(c['LongAccel'][middle])), 0, delta=.2)
        self.assertEqual(float(c['Gear'][2000]), 4)
        self.assertAlmostEqual(float(c['dcBrakeBias'][2000]), 55.)
        self.assertTrue(np.all(np.diff(c['SessionTime']) > 0))

    def test_history_input_metadata_preserves_native_rates_without_another_channel_read(self):
        from driver_history_metrics import build_observation_metrics
        with duckdb.connect(str(self.path)) as db:
            db.execute('INSERT INTO channelsList VALUES (?,?,?)', ['Engine RPM', 25., 'rpm'])
            db.execute('CREATE TABLE "Engine RPM" AS SELECT 6000.0 AS value FROM range(?)',
                       [int((LAPS * LAP_S + 2) * 25)])
        sample = self.parse()
        evidence = sample.metadata['history_input_sampling']
        self.assertEqual(evidence['native_hz']['Throttle'], 50.)
        self.assertEqual(evidence['native_hz']['Brake'], 50.)
        self.assertEqual(evidence['native_hz']['SteeringWheelAngle'], 100.)
        self.assertEqual(evidence['native_hz']['RPM'], 25.)
        self.assertEqual(evidence['native_units']['RPM'], 'rpm')
        self.assertTrue(evidence['interpolated'])
        self.assertTrue(evidence['pit_state_available'])
        self.assertTrue(evidence['gear_events_available'])
        self.assertEqual(evidence['gear_event_start_s'], START)
        self.assertNotIn('Gear', evidence['native_hz'])
        self.assertGreater(evidence['steering_half_range_rad'], 0.)
        observation = {'game': 'lmu', 'coverage': {'status': 'OBSERVED', 'intervals': [[10., 135.]], 'reasons': []}}
        result = build_observation_metrics(sample, observation)
        measured = [lap['inputs'] for lap in result['laps'] if lap['measurable']]
        self.assertTrue(measured)
        self.assertTrue(all(item['throttle']['sampling_hz'] == 50. for item in measured))
        self.assertTrue(all('INTERPOLATED_NATIVE_INPUTS' in item['reason_codes'] for item in measured))

    def test_partial_recording_keeps_inputs_but_never_invents_course_length(self):
        with duckdb.connect(str(self.path)) as db:
            for name, hz in db.execute('SELECT channelName, frequency FROM channelsList').fetchall():
                db.execute(f'DELETE FROM "{name}" WHERE rowid >= ?', [int(5 * hz)])
            for name in ('Lap', 'Lap Time'):
                db.execute(f'DELETE FROM "{name}" WHERE ts > ?', [START + 5])
        telemetry = self.parse()
        self.assertIsNone(telemetry.metadata['track_length_m'])
        self.assertEqual(telemetry.metadata['track_length_source'], 'UNCONFIRMED')
        self.assertTrue(np.all(np.isnan(telemetry.channels['LapDistPct'])))
        self.assertTrue(np.any(np.isfinite(telemetry.channels['LapDist'])))
        np.testing.assert_allclose(telemetry.channels['Speed'][10:-10], SPEED)
        self.assertFalse(any(lap.valid or lap.complete for lap in telemetry.laps))
        import backend_api
        from analysis_models import AnalyzeRequest
        result = backend_api.TelemetryAnalyzer(telemetry).analyze(AnalyzeRequest(filename=self.path.name))
        self.assertIsNone(result['session']['track_length_m'])
        self.assertEqual(result['session']['track_length_source'], 'UNCONFIRMED')
        self.assertIsNone(result['comparison'])
        self.assertFalse(result['data_quality']['coaching_ready'])
        self.assertTrue(any('트랙 전체 길이' in text for text in result['data_quality']['messages']))

    def test_steering_uses_the_live_measured_wheel_range(self):
        nominal = self.parse()
        self.assertEqual(nominal.metadata['steering_scale'], 'NOMINAL')
        from lmu_steering import remember
        remember('Test GT3 #7', 540)
        measured = self.parse()
        self.assertEqual(measured.metadata['steering_scale'], 'MEASURED')
        self.assertEqual(measured.metadata['steering_half_range_deg'], 270)
        # 20% of a 540-degree wheel is 54 degrees.
        self.assertAlmostEqual(math.degrees(float(measured.channels['SteeringWheelAngle'][2000])), 54, places=3)

    def test_metadata_and_session_info_match_the_live_adapter(self):
        from lmu_irsdk import stable_id
        telemetry = self.parse()
        metadata = telemetry.metadata
        self.assertEqual(metadata['game'], 'lmu')
        self.assertEqual(metadata['track_platform_id'], stable_id('track', 'Test Ring'))
        self.assertEqual(metadata['car_path'], stable_id('car', 'Test GT3 #7'))
        self.assertEqual(metadata['session_type'], 'Practice')
        self.assertEqual(metadata['track_config'], '')
        self.assertIsNone(metadata['official_track_id'])
        self.assertEqual(telemetry.setup, {})

    def test_the_shared_analysis_runs_on_the_recording(self):
        import backend_api
        from analysis_models import AnalyzeRequest
        telemetry = self.parse()
        result = backend_api.TelemetryAnalyzer(telemetry).analyze(
            AnalyzeRequest(filename=self.path.name, session_mode='BASELINE_3LAP'))
        self.assertEqual(len(result['laps']), 4)
        self.assertEqual(sum(lap['status'] == 'VALID' for lap in result['laps']), 3)

    def test_official_maps_only_for_the_main_layout(self):
        from lmu_ibt import official_track_id
        self.assertEqual(official_track_id('Autodromo Nazionale Monza', 'Autodromo Nazionale Monza'), 239)
        self.assertEqual(official_track_id('Circuit de la Sarthe'), 268)
        self.assertIsNone(official_track_id('Autodromo Nazionale Monza', 'Curva Grande'))
        self.assertIsNone(official_track_id('Bahrain International Circuit'))


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuHistoryClockTests(unittest.TestCase):
    def test_history_preserves_native_samples_gaps_and_resets_without_rereading(self):
        from driver_history_summary import build_source_summary
        from lmu_ibt import LmuIbtReader
        from lmu_telemetry import LmuTelemetryReader

        cases = (
            ('native-100hz', [10., 10.01, 10.02, 10.03, 10.04], 3, [5], False, False),
            ('native-gap', [10., 10.01, 11., 11.01], 61, [4], True, False),
            ('native-reset', [10., 10.01, 10.005, 10.02, 10.03], 2, [2, 3], False, True),
        )
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'IRACING_DATA_DIR': folder,
            'IRACING_LMU_STEERING_PATH': str(Path(folder) / 'steering.json'),
            'IRACING_LMU_CAR_MODELS_PATH': str(Path(folder) / 'models.json'),
            'IRACING_LMU_LAP_VERDICTS_PATH': str(Path(folder) / 'verdicts.json'),
        }):
            for name, native, resampled_count, counts, gap, reset in cases:
                with self.subTest(name=name):
                    path = Path(folder) / (name + '.duckdb')
                    connection = duckdb.connect(str(path))
                    connection.execute('CREATE TABLE metadata (key VARCHAR, value VARCHAR)')
                    connection.executemany('INSERT INTO metadata VALUES (?,?)', [
                        ('TrackName', 'Synthetic Track'), ('TrackLayout', 'GP'), ('CarName', 'Synthetic GT3'),
                        ('CarClass', 'GT3'), ('SessionType', 'Practice'), ('RecordingTime', '2026-10-20T10_00_00Z')])
                    connection.execute('CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE, unit VARCHAR)')
                    connection.execute("INSERT INTO channelsList VALUES ('GPS Time',100,'s')")
                    connection.execute('CREATE TABLE "GPS Time" AS SELECT CAST(unnest(?) AS DOUBLE) AS value', [native])
                    connection.close()
                    telemetry = LmuIbtReader(path).parse()
                    # Existing shared analysis remains on its original 60 Hz axis.
                    self.assertEqual(telemetry.record_count, resampled_count)
                    self.assertEqual(telemetry.tick_rate, 60)
                    with patch.object(LmuTelemetryReader, '_connection', side_effect=AssertionError('no reread')):
                        observations = build_source_summary(telemetry, 'a' * 64)['observations']
                    self.assertEqual([row['coverage']['sample_count'] for row in observations], counts)
                    reasons = set().union(*(set(row['coverage']['reasons']) for row in observations))
                    self.assertEqual('SAMPLE_GAP' in reasons, gap)
                    self.assertEqual('SESSION_CLOCK_RESET' in reasons, reset)
                    if reset:
                        self.assertTrue(all(row['recorded_at'] is None for row in observations))
                        self.assertTrue(all(row['coverage']['time_basis'] == 'SOURCE_RELATIVE' for row in observations))
                    elif gap:
                        self.assertEqual(observations[0]['coverage']['intervals'], [
                            [1792490400., 1792490400.01], [1792490401., 1792490401.01]])
                    else:
                        self.assertEqual(observations[0]['coverage']['intervals'], [[1792490400., 1792490400.04]])
                    self.assertTrue(all(row['identity_status'] == 'SOURCE_ONLY' for row in observations))
                    self.assertNotIn('_driver_history_lmu_clock', telemetry.metadata)
                    self.assertNotIn('_driver_history_lmu_clock', telemetry.channels)
                    if name == 'native-100hz':
                        with patch('driver_history_summary.compact_lmu_clock', side_effect=OverflowError('injected')):
                            recovered = LmuIbtReader(path).parse()
                        np.testing.assert_array_equal(recovered.channels['SessionTime'], telemetry.channels['SessionTime'])
                        self.assertEqual(recovered.record_count, telemetry.record_count)
                        self.assertEqual(recovered.metadata, telemetry.metadata)
                        missing, = build_source_summary(recovered, 'b' * 64)['observations']
                        self.assertEqual(missing['coverage']['status'], 'UNAVAILABLE')
                        self.assertEqual(missing['coverage']['sample_count'], 0)


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuRecordingsTests(unittest.TestCase):
    def test_lmu_catalog_preserves_measurement_eligibility_and_unknown_counts(self):
        import lmu_recordings
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drive.duckdb'
            path.write_bytes(b'synthetic')
            for info, selectable in (({'measurement_laps': 2, 'valid_laps': None, 'analyzable': True}, True),
                                     ({'measurement_laps': 0, 'valid_laps': None}, False),
                                     ({'valid_laps': None, 'metadata_pending': True}, True)):
                with self.subTest(info=info), patch.object(lmu_recordings, 'recordings', return_value=[path]):
                    row = lmu_recordings.files_payload(lambda _: info)['files'][0]
                self.assertEqual(row['selectable'], selectable)
                self.assertEqual(row['measurement_laps'], info.get('measurement_laps'))
                self.assertIsNone(row['valid_laps'])

    def test_catalog_versions_change_and_imported_references_do_not_auto_open(self):
        import lmu_recordings
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = root / 'drive.duckdb'
            current.write_bytes(b'first')
            imported = root / 'imports'
            imported.mkdir()
            reference = imported / 'reference.duckdb'
            reference.write_bytes(b'reference')
            with patch.object(lmu_recordings, 'recordings', return_value=[current, reference]), \
                    patch('lmu_imports.import_dir', return_value=imported):
                before = lmu_recordings.files_payload(lambda p: {'analyzable': True})['files']
                self.assertTrue(before[0]['auto_load'])
                self.assertFalse(before[1]['auto_load'])
                current.write_bytes(b'longer recording')
                after = lmu_recordings.files_payload(lambda p: {'analyzable': True})['files']
                self.assertNotEqual(before[0]['file_signature'], after[0]['file_signature'])

    def test_a_recording_changed_during_visibility_is_not_ready(self):
        import lmu_recordings
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / 'drive.duckdb'
            current.write_bytes(b'first')
            def changing_visibility(path):
                path.write_bytes(b'extended recording')
                return {'analyzable': True}
            with patch.object(lmu_recordings, 'recordings', return_value=[current]):
                row = lmu_recordings.files_payload(changing_visibility)['files'][0]
            self.assertFalse(row['analyzable'])
            self.assertEqual(row['status'], 'RECORDING')
            self.assertTrue(row['retryable'])

    def test_recordings_resolve_by_name_or_old_id_and_busy_files_are_listed_not_read(self):
        import lmu_recordings
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Test Ring_P_2026-10-20T10_00_00Z.duckdb'
            make_circuit(path)
            busy = Path(folder) / 'Test Ring_P_2026-10-20T11_00_00Z.duckdb'
            make_circuit(busy)
            Path(str(busy) + '.wal').write_bytes(b'')
            with patch.dict(os.environ, {'LMU_TELEMETRY_DIRS': folder}):
                self.assertEqual(lmu_recordings.resolve(path.name), path)
                self.assertEqual(lmu_recordings.resolve(lmu_recordings.file_id(path)), path)
                self.assertIsNone(lmu_recordings.resolve('other.ibt'))
                seen = []
                rows = lmu_recordings.files_payload(lambda p: seen.append(p) or {'analyzable': True})['files']
                self.assertEqual({row['name']: row['status'] for row in rows if row['status']},
                                 {busy.name: 'RECORDING'})
                self.assertEqual(seen, [path])


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuSharedWorkspaceTests(unittest.TestCase):
    def test_lmu_routes_run_the_shared_analysis_workspace(self):
        from fastapi.testclient import TestClient
        import backend_api
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Test Ring_P_2026-10-20T10_00_00Z.duckdb'
            make_circuit(path)
            with patch.dict(os.environ, {'LMU_TELEMETRY_DIRS': folder,
                                         'IRACING_LMU_STEERING_PATH': str(Path(folder) / 'steering.json')}):
                client = TestClient(backend_api.app)
                files = client.post('/api/lmu/workspace/files', json={}).json()['files']
                self.assertEqual([(row['name'], row['analyzable']) for row in files], [(path.name, True)])
                session = client.post('/api/lmu/workspace/session',
                                      json={'filename': path.name, 'reference_selection': 'AUTO'}).json()
                self.assertEqual(session['provider'], 'LMU')
                self.assertEqual(session['session']['game'], 'lmu')
                self.assertEqual(session['session']['steering_half_range_deg'], 225)
                self.assertEqual([lap['status'] for lap in session['laps']], ['VALID', 'VALID', 'INVALID', 'VALID'])
                self.assertIn('practiceSummary', session['initialComparison'])
                # The earlier 24-hex file IDs still open the same recording.
                import lmu_recordings
                again = client.post('/api/lmu/workspace/file-laps', json={'filename': lmu_recordings.file_id(path)})
                self.assertEqual(again.status_code, 200)
                names = client.get('/api/corner-names', params={'filename': path.name}).json()
                self.assertEqual(names['track_id'], 'lmu-track-test-ring')


if __name__ == '__main__':
    unittest.main()
