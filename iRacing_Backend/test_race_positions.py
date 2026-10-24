"""Race chart data boundaries, with synthetic recordings and no personal stores."""
from dataclasses import replace
from contextlib import closing
import gzip
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from analysis_numeric import file_signature
from race_positions import read_history, _native, _append, MAX_FRAMES
from telemetry_types import VariableHeader
from timing_log import TimingLog, read


class RacePositionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.timing = self.folder / 'timing'
        self.database = self.folder / 'voice.sqlite3'
        self.identity = {'game': 'iracing', 'car_path': 'car-a', 'track_path': 'track-a', 'session_type': 'Race'}

    def native(self, n=600, tick=60):
        width = 40
        path = self.folder / 'race.ibt'
        header = bytearray(144)
        struct.pack_into('<10i', header, 0, 2, 0, tick, 1, 0, 0, 4, 0, 1, width)
        struct.pack_into('<Qddii', header, 112, 1800000000, 100., 100. + (n-1)/tick, 0, n)
        raw = np.zeros((n, width), dtype='u1')
        times = np.ndarray((n,), dtype='<f8', buffer=raw, offset=0, strides=(width,))
        times[:] = 100 + np.arange(n) / tick
        overall = np.ndarray((n, 3), dtype='<i4', buffer=raw, offset=8, strides=(width, 4))
        overall[:] = [2, 1, 3]; overall[n//2:] = [1, 2, 3]
        classes = np.ndarray((n, 3), dtype='<i4', buffer=raw, offset=20, strides=(width, 4))
        classes[:] = [1, 1, 2]
        classes[n//2:] = [2, 1, 1]
        nums = np.ndarray((n,), dtype='<i4', buffer=raw, offset=32, strides=(width,))
        nums[:] = 2
        path.write_bytes(header + raw.tobytes())
        variables = {name: VariableHeader(kind, offset, count, False, name, '', '') for name, kind, offset, count in (
            ('SessionTime', 5, 0, 1), ('CarIdxPosition', 2, 8, 3), ('CarIdxClassPosition', 2, 20, 3), ('SessionNum', 2, 32, 1))}
        return SimpleNamespace(path=path, signature=file_signature(path), buffer_length=width, record_count=n,
            data_offset=144, tick_rate=tick, variables=variables, channels={'SessionTime': times.copy()},
            metadata={**self.identity}, session_info={'SessionInfo': {'Sessions': [{'SessionNum': 2, 'SessionType': 'Race'}]},
                'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [
                    {'CarIdx': 0, 'CarNumber': '09', 'CarClassID': 10, 'CarClassShortName': 'GT3'},
                    {'CarIdx': 1, 'CarNumber': '2', 'CarClassID': 20, 'CarClassShortName': 'GTP'},
                    {'CarIdx': 2, 'CarNumber': '31', 'CarClassID': 10, 'CarClassShortName': 'GT3'}]}})

    def history(self, telemetry):
        return read_history(telemetry, timing_folder=self.timing, voice_database=self.database)

    def race(self):
        return {'session_type': 'Race', 'track_name': 'Synthetic', 'player_idx': 0,
                'cars': [{'idx': 0, 'class_id': 10, 'position': 2, 'class_position': 1, 'number': '09'},
                         {'idx': 1, 'class_id': 20, 'position': 1, 'class_position': 1, 'number': '2'},
                         {'idx': 2, 'class_id': 10, 'position': 3, 'class_position': 2, 'number': '31'}]}

    def log(self, *, identity=True, run_id='test', offset=0):
        log = TimingLog(self.timing)
        for at in range(100, 110):
            kwargs = {'identity': self.identity, 'wall_time': 1800000000 + at - 100 + offset} if identity else {}
            log.record(self.race(), run_id, float(at), **kwargs)
        log.flush()
        return log.path

    def test_native_filters_other_classes_and_preserves_observed_changes(self):
        telemetry = self.native()
        result = self.history(telemetry)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['source'], 'IBT')
        self.assertEqual([row['label'] for row in result['series']], ['#09', '#31'])
        self.assertEqual([row['id'] for row in result['classes']], ['10'])
        own = result['series'][0]
        self.assertTrue(own['own'])
        self.assertEqual({p[2] for p in own['points']}, {1, 2})
        self.assertLess(len(own['points']), 10)
        self.assertEqual(file_signature(telemetry.path), telemetry.signature)

    def test_rank_zero_missing_and_gaps_are_never_filled(self):
        telemetry = self.native()
        raw = bytearray(telemetry.path.read_bytes())
        for i in range(120, 300):
            struct.pack_into('<i', raw, 144 + i * 40 + 20, 0)
        telemetry.path.write_bytes(raw)
        result = self.history(telemetry)
        points = result['series'][0]['points']
        self.assertTrue(any(p[2] is None for p in points))
        row = {'points': []}
        _append(row, 1, 2, 1)
        _append(row, 10, 2, 1, gap=True)
        self.assertEqual(row['points'][1][1:], [None, None])
        self.assertEqual(row['points'][1][0], 1.001)

    def test_missing_clock_samples_break_the_line_without_dropping_earlier_history(self):
        telemetry = self.native()
        raw = bytearray(telemetry.path.read_bytes())
        for i in range(120, 180):
            struct.pack_into('<d', raw, 144 + i * 40, float('nan'))
        telemetry.path.write_bytes(raw)
        points = self.history(telemetry)['series'][0]['points']
        self.assertEqual(points[0][0], 100)
        self.assertTrue(any(p[2] is None for p in points))
        self.assertGreater(points[-1][0], 108)

    def test_large_endurance_input_is_bounded_and_normal_channels_are_unchanged(self):
        telemetry = self.native(n=MAX_FRAMES * 3, tick=1)
        before = telemetry.channels['SessionTime'].copy()
        result = self.history(telemetry)
        self.assertLessEqual(len(result['series'][0]['points']), MAX_FRAMES + 1)
        np.testing.assert_array_equal(telemetry.channels['SessionTime'], before)
        self.assertGreaterEqual(result['sample_interval_s'], 3)

    def test_malformed_array_layout_is_rejected(self):
        telemetry = self.native()
        telemetry.variables['CarIdxPosition'] = replace(telemetry.variables['CarIdxPosition'], count=129)
        with self.assertRaisesRegex(ValueError, '범위'):
            self.history(telemetry)

    def test_unknown_player_class_is_not_replaced_with_all_classes(self):
        telemetry = self.native()
        telemetry.session_info['DriverInfo']['Drivers'][0].pop('CarClassID')
        self.assertEqual(self.history(telemetry)['status'], 'UNAVAILABLE')

    def test_new_timing_identity_and_native_clocks_match_for_both_games(self):
        for game in ('iracing', 'lmu'):
            with self.subTest(game=game):
                self.identity['game'] = game
                telemetry = self.native()
                telemetry.variables = {}
                self.log()
                result = self.history(telemetry)
                self.assertEqual(result['status'], 'READY')
                self.assertEqual(result['source'], 'LOCAL_TIMING')
                self.assertEqual([row['id'] for row in result['series']], ['0', '2'])

    def test_adjacent_session_wrong_car_and_wrong_game_are_rejected(self):
        telemetry = self.native(); telemetry.variables = {}
        path = self.log(offset=1000)
        self.assertEqual(self.history(telemetry)['status'], 'UNAVAILABLE')
        path.unlink()
        self.identity['car_path'] = 'other-car'
        self.log()
        self.assertEqual(self.history(telemetry)['status'], 'UNAVAILABLE')

    def test_legacy_timing_needs_two_matching_delivery_clocks(self):
        telemetry = self.native(); telemetry.variables = {}
        self.log(identity=False)
        self.assertEqual(self.history(telemetry)['status'], 'UNAVAILABLE')
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE radio_delivery(id INTEGER PRIMARY KEY, created_at REAL, run_id TEXT, event_json TEXT)')
            for i, at in enumerate((101, 105)):
                item = {'at': 1800000000 + at - 100, 'session_time': at, 'session': self.identity}
                db.execute('INSERT INTO radio_delivery VALUES(?,?,?,?)', (i, item['at'], 'test', json.dumps(item)))
            db.commit()
        self.assertEqual(self.history(telemetry)['status'], 'READY')
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('DELETE FROM radio_delivery WHERE id=1')
            db.commit()
        self.assertEqual(self.history(telemetry)['status'], 'UNAVAILABLE')

    def test_practice_missing_and_changed_files_have_explicit_states(self):
        telemetry = self.native()
        telemetry.metadata['session_type'] = 'Practice'
        self.assertEqual(self.history(telemetry)['status'], 'UNAVAILABLE')
        telemetry.metadata['session_type'] = 'Race'
        telemetry.path.with_name(telemetry.path.name + '.wal').touch()
        with self.assertRaisesRegex(ValueError, '기록 중'):
            self.history(telemetry)

    def test_new_log_has_no_driver_names_or_personal_paths(self):
        path = self.log()
        header, rows = read(path)
        self.assertEqual(len(rows), 10)
        self.assertEqual(header['identity'], self.identity)
        self.assertNotIn('name', header['entries'][0])
        self.assertNotIn(str(self.folder), json.dumps(header))

    def test_short_race_tail_is_flushed_on_disconnect_and_stop(self):
        from live_telemetry import LiveTelemetry
        live = LiveTelemetry()
        live.timing_log = TimingLog(self.timing)
        live.timing_log.record(self.race(), 'tail', 100.)
        path = live.timing_log.path
        self.assertFalse(path.exists())
        live._reset_live('DISCONNECTED')
        self.assertEqual(len(read(path)[1]), 1)
        live.timing_log.record(self.race(), 'tail', 101.)
        live.stop()
        self.assertEqual(len(read(path)[1]), 2)


if __name__ == '__main__':
    unittest.main()
