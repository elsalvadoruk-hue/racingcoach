"""Extra parts of a session log: lossless race logs, own measured drive, and the size rule. No personal files."""
import gzip
import json
import os
import random
import struct
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
import io

import drive_summary
import log_pack
import session_log
import session_log_extras as extras
from session_log import Extra
from test_session_log import RUN, SessionLogTests


def write_log(path, header, rows):
    with gzip.open(path, 'at', encoding='utf-8') as handle:      # two gzip members, like the live writer
        handle.write(json.dumps(header) + '\n' + '\n'.join(json.dumps(r, separators=(',', ':')) for r in rows[:5]) + '\n')
    with gzip.open(path, 'at', encoding='utf-8') as handle:
        handle.write('\n'.join(json.dumps(r, separators=(',', ':')) for r in rows[5:]) + '\n')


def traffic_rows(count=300, seed=3):
    rng = random.Random(seed)
    rows, pct = [], .1
    for i in range(count):
        pct = round((pct + .00071) % 1, 5)
        cars = [[idx, 4029, round(rng.uniform(-200, 200), 2), round(rng.uniform(0, 90), 3), 7, idx, idx % 5, 0, 3,
                 None if idx % 2 else round(rng.uniform(-3, 3), 2), None if idx % 2 else round(rng.uniform(-60, 60), 2)]
                for idx in sorted(rng.sample(range(1, 30), rng.randint(0, 6)))]
        rows.append({'t': round(500 + i * .1, 3), 'own': [7, pct, round(rng.uniform(30, 70), 3), round(rng.random(), 3),
                     round(rng.random(), 3), round(rng.uniform(-.3, .3), 3), rng.choice([0, 1, 2]), 0], 'c': cars})
    return rows


def write_recording(path, started, ended, closed=None):
    """The small native IBT headers; no telemetry samples are needed for matching."""
    header = bytearray(112)
    struct.pack_into('<10i', header, 0, 2, 1, 60, 1, 1, 144, 1, 145, 1, 1)
    path.write_bytes(header + struct.pack('<Qddii', int(started), 10., 10. + ended - started, 1, 60))
    os.utime(path, (ended if closed is None else closed,) * 2)


class RecordingAssociationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.start = 1_792_750_000.
        self.identity = {'car_path': 'gt3', 'track_path': 'spa'}
        self.run = {'started_at': self.start, 'ended_at': self.start + 600,
                    'identity': self.identity, 'session_type': 'Race'}

    def select(self, paths, metadata=None):
        return extras.recordings_for(self.run, paths, lambda p: (metadata or {}).get(p, self.identity))

    def test_adjacent_same_car_track_and_session_type_are_not_attached(self):
        paths = [self.folder / f'{name}.ibt' for name in ('previous', 'own', 'next')]
        write_recording(paths[0], self.start - 600, self.start - 20)
        write_recording(paths[1], self.start, self.start + 610)
        write_recording(paths[2], self.start + 620, self.start + 1200)
        self.assertEqual(self.select(paths), [paths[1]])

    def test_split_files_and_recording_started_before_first_call_are_kept(self):
        paths = [self.folder / f'{name}.ibt' for name in ('first', 'second', 'third')]
        write_recording(paths[0], self.start - 900, self.start + 200, closed=self.start + 9000)
        write_recording(paths[1], self.start + 220, self.start + 400)
        write_recording(paths[2], self.start + 420, self.start + 900)
        self.assertEqual(self.select(list(reversed(paths))), paths)

    def test_copying_or_renaming_an_old_recording_does_not_make_it_current(self):
        old, own = self.folder / 'renamed.ibt', self.folder / 'own.ibt'
        write_recording(old, self.start - 86400, self.start - 85000, closed=self.start + 100)
        write_recording(own, self.start, self.start + 600, closed=self.start + 9000)
        self.assertEqual(self.select([old, own]), [own])

    def test_known_other_session_type_or_game_is_not_attached(self):
        paths = [self.folder / f'{name}.ibt' for name in ('quali', 'race', 'other-game')]
        for path in paths:
            write_recording(path, self.start, self.start + 600)
        meta = {paths[0]: {**self.identity, 'session_type': 'Open Qualify'},
                paths[1]: {**self.identity, 'session_type': 'Race'},
                paths[2]: {**self.identity, 'session_type': 'Race', 'game': 'lmu'}}
        self.run['identity']['game'] = 'iracing'
        self.assertEqual(self.select(paths, meta), [paths[1]])

    def test_unknown_corrupt_or_missing_recording_time_is_not_guessed_from_mtime(self):
        paths = [self.folder / f'{name}.ibt' for name in ('truncated', 'zero-date', 'backwards', 'nan')]
        paths[0].write_bytes(b'x')
        write_recording(paths[1], 0, 100)
        write_recording(paths[2], self.start, self.start - 1)
        write_recording(paths[3], self.start, float('nan'), closed=self.start + 100)
        for path in paths:
            os.utime(path, (self.start + 100,) * 2)
        self.assertEqual(self.select(paths + [self.folder / 'missing.ibt']), [])

    def test_missing_identity_does_not_attach_an_unidentified_file(self):
        path = self.folder / 'unknown.ibt'
        write_recording(path, self.start, self.start + 600)
        self.assertEqual(self.select([path], {path: {}}), [])

    def test_unrelated_recordings_are_not_indexed_or_analysed(self):
        path = self.folder / 'other.ibt'
        write_recording(path, self.start + 620, self.start + 1200)
        metadata, analyze = Mock(), Mock()
        made = extras.own_drive(lambda: [path], metadata, analyze)(self.run)
        self.assertFalse(made.files)
        self.assertEqual(made.info['reason'], 'no_recording')
        metadata.assert_not_called()
        analyze.assert_not_called()

    def test_locked_file_is_not_attached(self):
        with patch.object(Path, 'open', side_effect=PermissionError('locked')):
            self.assertEqual(self.select([self.folder / 'locked.ibt']), [])

    def test_newest_three_split_recordings_are_chosen_by_recorded_time(self):
        paths = [self.folder / f'{i}.ibt' for i in range(4)]
        for i, path in enumerate(paths):
            write_recording(path, self.start + i * 100, self.start + i * 100 + 90,
                            closed=self.start + (4 - i) * 1000)
        self.assertEqual(self.select(list(reversed(paths))), paths[1:])

    def test_native_lmu_times_work_without_ibt_headers_or_filename_timestamps(self):
        from datetime import datetime, timezone
        try:
            import duckdb
        except ImportError:
            self.skipTest('optional LMU dependency is not installed')

        paths = [self.folder / f'{name}.duckdb' for name in ('previous', 'own', 'next')]
        for path, started in zip(paths, (self.start - 700, self.start, self.start + 620)):
            with duckdb.connect(str(path)) as connection:
                connection.execute('CREATE TABLE metadata (key VARCHAR, value VARCHAR)')
                recorded = datetime.fromtimestamp(started, timezone.utc).strftime('%Y-%m-%dT%H_%M_%SZ')
                connection.execute('INSERT INTO metadata VALUES (?, ?)', ['RecordingTime', recorded])
                connection.execute('CREATE TABLE channelsList (channelName VARCHAR, frequency DOUBLE, unit VARCHAR)')
                connection.execute('CREATE TABLE "GPS Time" (value DOUBLE)')
                connection.execute('INSERT INTO "GPS Time" VALUES (50), (350), (650)')
            os.utime(path, (self.start + 600,) * 2)
        self.assertEqual(self.select(paths), [paths[1]])
        self.assertEqual(extras.recording_window(paths[1]), (self.start, self.start + 600))
        with duckdb.connect(str(paths[1])) as connection:
            connection.execute('DELETE FROM metadata')
        self.assertEqual(self.select([paths[1]]), [])


class LogPackTests(unittest.TestCase):
    def test_round_trip_is_exact_and_smaller(self):
        header = {'format': 'iracing-coach-traffic', 'version': 1, 'run_id': RUN, 'track_length_m': 5000.0}
        rows = traffic_rows()
        data = log_pack.pack(header, rows)
        self.assertEqual(log_pack.unpack(data), (header, rows))
        plain = gzip.compress('\n'.join(json.dumps(r) for r in rows).encode())
        self.assertLess(len(data), len(plain))

    def test_odd_values_fall_back_to_plain_lines_without_loss(self):
        header = {'format': 'x'}
        rows = [{'t': 1.5, 'note': 'a', 'c': [[1, 2.5]]}, {'t': 1e-12, 'note': None, 'c': [[1, 'x']]}]
        self.assertEqual(log_pack.unpack(log_pack.pack(header, rows)), (header, rows))
        rows = [{'t': 1}, {'t': 2, 'extra': 3}]          # keys differ between rows
        self.assertEqual(log_pack.unpack(log_pack.pack(header, rows)), (header, rows))

    def test_a_car_change_is_counted_from_the_same_car(self):
        rows = [{'t': i / 10, 'c': [[3, 100.0 + i], [9, -50.0 - i]] if i % 2 else [[9, -50.0 - i]]} for i in range(10)]
        packed = log_pack._encode({}, rows)
        self.assertTrue(all(abs(v) <= 2000 for v in packed['cars']['cols'][0]['d'][2:]))
        self.assertEqual(log_pack._decode(json.loads(json.dumps(packed))), ({}, rows))

    def test_read_lines_reads_every_gzip_member(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'traffic-1.jsonl.gz'
            rows = traffic_rows(20)
            write_log(path, {'run_id': RUN}, rows)
            self.assertEqual(log_pack.read_lines(path), ({'run_id': RUN}, rows))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.run = {'run_id': RUN, 'started_at': time.time() - 3600, 'ended_at': time.time() - 600,
                    'identity': {'car_path': 'testgt3', 'track_path': 'test gp'}, 'session_type': 'Race'}

    def test_nearby_cars_only_for_races_and_only_the_runs_own_logs(self):
        traffic, timing = self.folder / 'traffic', self.folder / 'timing'
        traffic.mkdir(); timing.mkdir()
        write_log(traffic / 'traffic-a.jsonl.gz', {'run_id': 'other-run'}, traffic_rows(30, 1))
        rows = traffic_rows(40, 2)
        write_log(traffic / 'traffic-b.jsonl.gz', {'run_id': RUN, 'track': 'Test'}, rows)
        provider = extras.nearby_cars(traffic, timing)
        self.assertEqual(provider({**self.run, 'session_type': 'Practice'}).info['reason'], 'not_race')
        made = provider(self.run)
        self.assertEqual(set(made.files), {'traffic.packed.xz'})
        self.assertEqual(log_pack.unpack(made.files['traffic.packed.xz'])[1], rows)
        self.assertEqual(made.info['traffic']['rows'], 40)
        self.assertEqual(extras.nearby_cars(self.folder / 'none', None)(self.run).info['reason'], 'no_log')

    def test_recordings_overlapping_the_run_with_the_same_car_and_track(self):
        paths = {}
        for name, closed in (('before', self.run['started_at'] - 3600), ('during', self.run['ended_at'] - 60),
                             ('after', self.run['ended_at'] + 900), ('other_car', self.run['ended_at'] + 60),
                             ('much_later', self.run['ended_at'] + 7200)):
            path = self.folder / f'{name}.ibt'
            write_recording(path, closed - 1200, closed)
            paths[name] = path
        meta = {path: {'car_path': 'othergt4' if name == 'other_car' else 'TestGT3', 'track_path': 'test gp'}
                for name, path in paths.items()}
        found = extras.recordings_for(self.run, list(paths.values()), lambda p: meta[Path(p)])
        self.assertEqual([p.name for p in found], ['during.ibt', 'after.ibt'])

    def test_own_drive_waits_while_driving_and_packs_measured_values(self):
        path = self.folder / 'drive.ibt'
        write_recording(path, self.run['started_at'], self.run['ended_at'])
        result = {'laps': [{'lap_number': 1, 'status': 'VALID', 'lap_time_s': 100.0, 'file_path': 'C:\\x'}],
                  'session': {'environment': {'track_temp_c': 30.0}},
                  'performance': {'corners': [{'id': 'C1', 'label': '1', 'entry_pct': .1, 'apex_pct': .12, 'exit_pct': .14}],
                                  'corner_consistency': {'corners': [{'id': 'C1', 'passes': [
                                      {'lap': 1, 'time_s': 5.0, 'metrics': {'min_speed_kph': 80.0},
                                       'conditions': {'fuel_l': 40.0}}]}]}}}
        handling = {'method': 'personal_corner_phase_v3', 'observations': [
            {'corner_id': 'C1', 'phase': 'drive', 'lap': 1, 'observed': True, 'known_fraction': 1.0,
             'events': [{'kind': 'understeer', 'seconds': .4, 'with': 'power', 'z_median': -3.0, '_z_samples': [1]}]}]}
        metadata = {'car_path': 'testgt3', 'track_path': 'test gp', 'setup_name': 'my secret.sto', 'session_type': 'Race'}
        driving = [True]
        provider = extras.own_drive(lambda: [path], lambda p: metadata, lambda p: (result, metadata, handling),
                                    driving=lambda: driving[0], defer=True, app_version='2.1.0')
        with self.assertRaises(session_log.Deferred):
            provider(self.run)
        driving[0] = False
        made = provider(self.run)
        summary = drive_summary.unpack(made.files['drive_summary.json.xz'])
        recording = summary['recordings'][0]
        self.assertEqual(recording['corner_passes'], [{'corner_id': 'C1', 'lap': 1, 'time_s': 5.0, 'metrics': {'min_speed_kph': 80.0}}])
        self.assertEqual(recording['lap_conditions'], [{'lap': 1, 'fuel_l': 40.0}])
        self.assertEqual(recording['handling_observations'][0]['events'][0]['kind'], 'understeer')
        text = json.dumps(summary)
        self.assertNotIn('secret', text)
        self.assertNotIn('_z_samples', text)
        self.assertNotIn('C:\\\\', text)
        self.assertIsNone(extras.own_drive(lambda: [], lambda p: {}, None)(self.run).files or None)


class SizeRuleTests(SessionLogTests):
    """Reuses the stored session of SessionLogTests; adds extras of chosen sizes."""

    @staticmethod
    def provider(group, size):
        return lambda run: Extra(group, {f'{group}.bin': os.urandom(size)}, {'rows': 1})

    def test_everything_fits_and_the_manifest_lists_it(self):
        name, data = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider('drive_summary', 1000),
                                                                           self.provider('nearby_cars', 1000)],
                                        limit=session_log.SEND_LIMIT)
        manifest = session_log.read(data)[0]
        self.assertTrue(manifest['extras']['nearby_cars']['included'])
        self.assertTrue(manifest['extras']['drive_summary']['included'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.getinfo('nearby_cars.bin').compress_type, zipfile.ZIP_STORED)

    def test_nearby_cars_go_first_then_the_own_drive(self):
        limit = 50_000
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider('drive_summary', 20_000),
                                                                        self.provider('nearby_cars', 40_000)], limit=limit)
        manifest = session_log.read(data)[0]
        self.assertLessEqual(len(data), limit)
        self.assertEqual(manifest['extras']['nearby_cars']['reason'], 'size_limit')
        self.assertFalse(manifest['extras']['nearby_cars']['included'])
        self.assertTrue(manifest['extras']['drive_summary']['included'])
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider('drive_summary', 60_000),
                                                                        self.provider('nearby_cars', 40_000)], limit=limit)
        manifest = session_log.read(data)[0]
        self.assertFalse(manifest['extras']['drive_summary']['included'])
        self.assertNotIn('drive_summary.bin', zipfile.ZipFile(io.BytesIO(data)).namelist())

    def test_a_saved_file_has_no_limit_and_a_broken_extra_is_left_out(self):
        def broken(run):
            raise RuntimeError('boom')
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[broken, self.provider('nearby_cars', 2_000_000)])
        manifest = session_log.read(data)[0]
        self.assertTrue(manifest['extras']['nearby_cars']['included'])
        self.assertNotIn('drive_summary', manifest['extras'])

    def test_an_unavailable_extra_says_why_and_deferred_passes_on(self):
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[lambda run: Extra('nearby_cars', info={'reason': 'not_race'})])
        self.assertEqual(session_log.read(data)[0]['extras']['nearby_cars'], {'included': False, 'reason': 'not_race', 'bytes': 0})
        def waiting(run):
            raise session_log.Deferred('driving')
        with self.assertRaises(session_log.Deferred):
            session_log.export(self.store, RUN, '2.1.0', extras=[waiting])

    def test_read_extras_unpacks_what_was_added(self):
        header, rows = {'run_id': RUN}, traffic_rows(15)
        summary = drive_summary.pack([{'laps': []}])
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[
            lambda run: Extra('nearby_cars', {'traffic.packed.xz': log_pack.pack(header, rows)}),
            lambda run: Extra('drive_summary', {'drive_summary.json.xz': summary})])
        parts = session_log.read_extras(data)
        self.assertEqual(parts['traffic'], (header, rows))
        self.assertEqual(parts['drive_summary']['recordings'], [{'laps': []}])


del SessionLogTests   # its own tests run from test_session_log

if __name__ == '__main__':
    unittest.main()
