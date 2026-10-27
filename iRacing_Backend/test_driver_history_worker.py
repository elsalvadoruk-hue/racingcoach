"""Owned child lifecycle and pure recording parsing; synthetic paths only."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

import driver_history_worker as worker


def synthetic_ibt(path):
    sizes = [101, 102, 103, 10]
    fields = ('SessionTime', 'SessionNum', 'Speed', 'IsOnTrack', 'Lap', 'LapDistPct',
              'LapLastLapTime', 'LapCompleted', 'LapCurrentLapTime', 'PlayerTrackSurface', 'OnPitRoad')
    columns = {name: [] for name in fields}
    elapsed = 0.
    for lap, size in enumerate(sizes):
        columns['SessionTime'] += [100 + elapsed + tick / 10 for tick in range(size)]
        columns['SessionNum'] += [0] * size
        columns['Speed'] += [50] * size
        columns['IsOnTrack'] += [1] * size
        columns['Lap'] += [lap + 1] * size
        columns['LapDistPct'] += np.linspace(0, .995 if lap < 3 else .1, size).tolist()
        columns['LapLastLapTime'] += [-1 if lap == 0 else (sizes[lap-1] - 1) / 10] * size
        columns['LapCompleted'] += [lap] * size
        columns['LapCurrentLapTime'] += [tick / 10 for tick in range(size)]
        columns['PlayerTrackSurface'] += [3] * size
        columns['OnPitRoad'] += [0] * size
        elapsed += size / 10
    session = json.dumps({'WeekendInfo': {'SessionID': 100, 'SubSessionID': 200, 'TrackID': 8,
                                         'TrackName': 'worker-track', 'TrackLength': '5 km'},
                          'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'UserID': 99, 'CarID': 7,
                                                                       'CarPath': 'worker-car'}]},
                          'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': 'Practice'}]}}).encode()
    session_offset = 144 + len(fields) * 144
    offset = session_offset + len(session)
    header = bytearray(144)
    struct.pack_into('<10i', header, 0, 2, 1, 10, 1, len(session), session_offset, len(fields), 144, 1, len(fields)*8)
    struct.pack_into('<i', header, 40, offset)
    struct.pack_into('<Qddii', header, 112, 1800000000, 100., 100.+elapsed-.1, 0, sum(sizes))
    variables = b''.join(struct.pack('<4i32s64s32s', 5, index*8, 1, 0, name.encode(), b'', b'')
                         for index, name in enumerate(fields))
    path.write_bytes(header+variables+session+np.column_stack([columns[name] for name in fields]).astype('<f8').tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DriverHistoryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='driver-history-worker-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder/'synthetic.ibt'
        self.source_id = synthetic_ibt(self.path)
        self.stack = self.enterContext(ExitStack())
        # These overrides are inherited by actual child processes. No normal
        # app settings, steering/model stores, SDK or provider is touched.
        self.stack.enter_context(patch.dict(os.environ, {
            'IRACING_DATA_DIR': str(self.folder),
            'IRACING_TUNER_DB_PATH': str(self.folder/'never-created.sqlite3'),
            'IRACING_LMU_STEERING_PATH': str(self.folder/'no-steering.json'),
            'IRACING_LMU_CAR_MODELS_PATH': str(self.folder/'no-models.json'),
            'IRACING_LMU_LAP_VERDICTS_PATH': str(self.folder/'no-verdicts.json'),
            'IRACING_AI_ENABLED': '0', 'GARAGE61_AUTO_COLLECT': '0',
            'GOOGLE_APPLICATION_CREDENTIALS': str(self.folder/'PRIVATE-CREDENTIALS.json'),
            'GEMINI_API_KEY': 'SYNTHETIC-NOT-A-KEY',
            'GARAGE61_API_TOKEN': 'SYNTHETIC-NOT-A-TOKEN',
            'OTHER_PROVIDER_SECRET': 'SYNTHETIC-NOT-A-SECRET',
        }))

    def test_already_stopped_never_launches_a_child(self):
        with patch.object(worker.subprocess, 'Popen') as launch:
            with self.assertRaises(InterruptedError):
                worker.summarize_in_process(self.path, self.source_id, lambda: True)
        launch.assert_not_called()

    def test_actual_ibt_child_returns_only_compact_summary_and_preserves_original(self):
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        payload = worker.summarize_in_process(self.path, self.source_id, lambda: False)
        self.assertEqual(payload['source_id'], self.source_id)
        self.assertEqual(payload['game'], 'iracing')
        self.assertEqual(payload['race_results']['source_id'], self.source_id)
        self.assertEqual(payload['race_results']['game'], 'iracing')
        self.assertEqual(payload['race_results']['results'], [])
        self.assertEqual(sum(lap['measurable'] for row in payload['observations'] for lap in row['metrics']['laps']), 3)
        self.assertNotIn(str(self.folder), json.dumps(payload))
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))
        self.assertFalse((self.folder/'never-created.sqlite3').exists())

    def test_actual_child_rejects_a_hash_that_does_not_belong_to_the_file(self):
        with self.assertRaisesRegex(RuntimeError, '^주행 기록의 측정 작업을 완료하지 못했습니다\\.$'):
            worker.summarize_in_process(self.path, 'f'*64, lambda: False)

    def test_actual_input_child_returns_only_input_enrichment_and_preserves_source(self):
        from driver_history_inputs import INPUT_VERSION
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        old = worker.summarize_in_process(self.path, self.source_id, lambda: False)
        payload = worker.enrich_inputs_in_process(self.path, self.source_id, old['observations'], lambda: False)
        self.assertEqual(payload['input_version'], INPUT_VERSION)
        self.assertEqual(payload['source_id'], self.source_id)
        self.assertEqual(sum(len(row['laps']) for row in payload['observations']), 3)
        self.assertNotIn('race_results', payload)
        self.assertTrue(all(set(row) == {'drive_id', 'laps'} for row in payload['observations']))
        self.assertTrue(all(lap['inputs']['input_version'] == INPUT_VERSION
            for row in payload['observations'] for lap in row['laps']))
        self.assertNotIn(str(self.folder), json.dumps(payload))
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_input_mode_reads_minimum_channels_without_recalculating_metrics_or_results(self):
        from ibt_reader import IBTReader
        from driver_history_summary import build_source_summary
        from driver_style import DRIVER_INPUT_CHANNELS
        old = build_source_summary(IBTReader(self.path).parse(), self.source_id)
        calls = []
        original = IBTReader._read_channels
        def read(reader, **kwargs):
            calls.append(set(kwargs['requested']))
            return original(reader, **kwargs)
        with patch.object(IBTReader, '_read_channels', new=read), \
                patch('driver_history_metrics.build_observation_metrics', side_effect=AssertionError('no metrics')), \
                patch('driver_progress.attach_recording_results', side_effect=AssertionError('no results')):
            payload = worker._enrich_recording(self.path, self.source_id, old['observations'])
        self.assertEqual(calls, [set(DRIVER_INPUT_CHANNELS)])
        self.assertEqual(len(payload['observations'][0]['laps']), 3)

    def test_input_mode_rejects_unmatched_lap_boundaries_without_returning_partial_patch(self):
        from ibt_reader import IBTReader
        from driver_history_summary import build_source_summary
        old = build_source_summary(IBTReader(self.path).parse(), self.source_id)
        changed = deepcopy(old['observations'])
        changed[0]['metrics']['laps'][-1]['end_s'] += .03
        with self.assertRaisesRegex(ValueError, 'boundaries unavailable'):
            worker._enrich_recording(self.path, self.source_id, changed)

    def test_input_child_cancellation_reaps_process_and_closes_pipes(self):
        process = Mock(returncode=None)
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', .2), ('', None)]
        process.wait.return_value = 0
        should_stop = Mock(side_effect=[False, False, True])
        with patch.object(worker.subprocess, 'Popen', return_value=process):
            with self.assertRaises(InterruptedError):
                worker.enrich_inputs_in_process(self.path, self.source_id, [], should_stop)
        process.terminate.assert_called_once()
        process.wait.assert_called()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec('duckdb'), 'Optional DuckDB is unavailable')
    def test_actual_lmu_child_is_read_only_and_never_learns_model_associations(self):
        import duckdb
        from lmu_telemetry import car_model_key
        path = self.folder/'synthetic.duckdb'
        setup = json.dumps({f'VM_TEST_{index}': {'available': True, 'minValue': 0, 'maxValue': index+1}
                            for index in range(20)})
        fingerprint = car_model_key(setup, 'GT3')
        models = self.folder/'no-models.json'
        models.write_text(json.dumps({'version': 1, 'entries': {}, 'fingerprints': {fingerprint: 'Known GT3'}}))
        with duckdb.connect(str(path), config={'threads': '1'}) as connection:
            connection.execute('CREATE TABLE metadata (key VARCHAR, value VARCHAR)')
            connection.executemany('INSERT INTO metadata VALUES (?, ?)', [
                ('TrackName', 'Worker Track'), ('CarName', 'Worker Livery #9'), ('CarClass', 'GT3'),
                ('CarSetup', setup), ('SessionType', 'Practice'), ('RecordingTime', '2026-10-20T10_00_00Z')])
            connection.execute('CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE, unit VARCHAR)')
            for name, unit, values in (
                    ('GPS Time', 's', 10+np.arange(101)/10),
                    ('Lap Dist', 'm', np.arange(101)*4.),
                    ('Ground Speed', 'km/h', np.full(101, 144.))):
                connection.execute('INSERT INTO channelsList VALUES (?, 10, ?)', [name, unit])
                connection.execute(f'CREATE TABLE "{name}" AS SELECT CAST(unnest(?) AS DOUBLE) AS value',
                                   [values.tolist()])
        source_id = hashlib.sha256(path.read_bytes()).hexdigest()
        before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in self.folder.iterdir()}
        payload = worker.summarize_in_process(path, source_id, lambda: False)
        self.assertEqual(payload['game'], 'lmu')
        self.assertEqual(payload['source_id'], source_id)
        self.assertEqual(payload['race_results']['source_id'], source_id)
        self.assertEqual(payload['race_results']['game'], 'lmu')
        self.assertEqual(payload['observations'][0]['identity_status'], 'SOURCE_ONLY')
        self.assertEqual(payload['observations'][0]['coverage']['sample_count'], 101)
        self.assertEqual(payload['observations'][0]['coverage']['time_basis'], 'UTC')
        self.assertNotIn(str(self.folder), json.dumps(payload))
        from driver_history_inputs import INPUT_VERSION
        inputs = worker.enrich_inputs_in_process(path, source_id, payload['observations'], lambda: False)
        self.assertEqual(inputs['input_version'], INPUT_VERSION)
        self.assertEqual(inputs['game'], 'lmu')
        self.assertTrue(all(set(row) == {'drive_id', 'laps'} for row in inputs['observations']))
        self.assertNotIn('race_results', inputs)
        self.assertEqual(before, {item.name: (item.read_bytes(), item.stat().st_mtime_ns)
                                  for item in self.folder.iterdir()})

    def test_imported_reference_metadata_never_produces_an_observation(self):
        from analysis_numeric import file_signature
        from ibt_reader import IBTReader
        telemetry = Mock(signature=file_signature(self.path), metadata={'imported': True})
        with patch.object(IBTReader, 'parse', return_value=telemetry):
            self.assertEqual(worker._summarize_recording(self.path, self.source_id),
                             {'skip_reason': 'IMPORTED_REFERENCE'})

    def test_recording_changed_during_parsing_never_produces_a_summary(self):
        from ibt_reader import IBTReader
        telemetry = IBTReader(self.path).parse()
        def changed():
            with self.path.open('ab') as handle:
                handle.write(b'changed')
            return telemetry
        with patch.object(IBTReader, 'parse', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '^Recording changed$'):
                worker._summarize_recording(self.path, self.source_id)

    def test_child_read_only_guard_rejects_writes_network_and_application_stores(self):
        for event, arguments in [('open', ('file', 'w', os.O_WRONLY)),
                                 ('open', ('file', None, os.O_CREAT)),
                                 ('sqlite3.connect', ('database',)),
                                 ('socket.__new__', ()), ('subprocess.Popen', ()),
                                 ('os.rename', ('one', 'two'))]:
            with self.subTest(event=event):
                with self.assertRaises(PermissionError):
                    worker._read_only_audit(event, arguments)
        worker._read_only_audit('open', ('file', 'r', os.O_RDONLY))

    def test_child_environment_is_low_priority_and_has_one_native_compute_thread(self):
        process = Mock(returncode=0)
        process.communicate.return_value = ('{"schema_version":1,"source_id":"' + self.source_id + '","game":"iracing","observations":[]}', None)
        with patch.object(worker.subprocess, 'Popen', return_value=process) as launch:
            worker.summarize_in_process(self.path, self.source_id, lambda: False)
        command = launch.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[-1]).resolve(), Path(worker.__file__).resolve())
        options = launch.call_args.kwargs
        self.assertEqual(options['stderr'], subprocess.DEVNULL)
        self.assertEqual(options['env']['OPENBLAS_NUM_THREADS'], '1')
        self.assertEqual(options['env']['OMP_NUM_THREADS'], '1')
        self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS', options['env'])
        self.assertNotIn('GEMINI_API_KEY', options['env'])
        self.assertNotIn('GARAGE61_API_TOKEN', options['env'])
        self.assertNotIn('OTHER_PROVIDER_SECRET', options['env'])
        if os.name == 'nt':
            self.assertTrue(options['creationflags'] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(options['creationflags'] & subprocess.BELOW_NORMAL_PRIORITY_CLASS)

    def test_failed_or_malformed_child_output_never_exposes_output_or_recording_path(self):
        for status, output in ((5, str(self.path)), (0, 'not JSON '+str(self.path)),
                               (0, '{}'), (0, '{"schema_version":1,"source_id":"bad","observations":[]}')):
            with self.subTest(status=status, output=output):
                process = Mock(returncode=status)
                process.communicate.return_value = (output, None)
                with patch.object(worker.subprocess, 'Popen', return_value=process):
                    with self.assertRaises(RuntimeError) as caught:
                        worker.summarize_in_process(self.path, self.source_id, lambda: False)
                self.assertNotIn(str(self.path), str(caught.exception))

    def test_timeout_cancellation_terminates_and_waits_before_returning(self):
        process = Mock(returncode=None)
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', .2), ('', None)]
        process.wait.return_value = 0
        should_stop = Mock(side_effect=[False, False, True])
        with patch.object(worker.subprocess, 'Popen', return_value=process):
            with self.assertRaises(InterruptedError):
                worker.summarize_in_process(self.path, self.source_id, should_stop)
        process.terminate.assert_called_once()
        process.wait.assert_called()
        process.kill.assert_not_called()

    def test_unresponsive_child_is_killed_and_reaped(self):
        process = Mock(returncode=None)
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', .2), ('', None)]
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 1), 0]
        should_stop = Mock(side_effect=[False, False, True])
        with patch.object(worker.subprocess, 'Popen', return_value=process):
            with self.assertRaises(InterruptedError):
                worker.summarize_in_process(self.path, self.source_id, should_stop)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_actual_large_stdout_is_drained_and_cancelled_child_has_exited(self):
        ready = self.folder/'large-output-ready'
        script = ('import sys,time; from pathlib import Path; '
                  'sys.stdout.write("x"*2097152); sys.stdout.flush(); '
                  f'Path({str(ready)!r}).write_text("ready"); time.sleep(30)')
        original = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = original([sys.executable, '-u', '-c', script], **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        def should_stop():
            return ready.exists() or time.monotonic()-started > 5.
        with patch.object(worker.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(InterruptedError):
                worker.summarize_in_process(self.path, self.source_id, should_stop)
        self.assertTrue(ready.exists(), 'Large stdout must be drained before cancellation')
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertLess(time.monotonic()-started, 4.)

    def test_actual_child_error_stderr_is_discarded_and_child_is_reaped(self):
        script = 'import sys; sys.stderr.write("PRIVATE_RECORDING_PATH\\n"); raise RuntimeError("PRIVATE_RECORDING_PATH")'
        original = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = original([sys.executable, '-u', '-c', script], **kwargs)
            children.append(child)
            return child
        with patch.object(worker.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(RuntimeError) as caught:
                worker.summarize_in_process(self.path, self.source_id, lambda: False)
        self.assertNotIn('PRIVATE_RECORDING_PATH', str(caught.exception))
        self.assertIsNotNone(children[0].poll())


if __name__ == '__main__':
    unittest.main()
