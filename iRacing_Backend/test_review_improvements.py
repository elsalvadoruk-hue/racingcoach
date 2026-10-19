"""Regressions for the 2026-10-09 review: traffic labels, gaps, note storage, caches."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np

from test_corner_comparison import api, telemetry
from analysis_support import CornerDefinitionStore
from live_telemetry import LiveTelemetry
from race_engineer import read_race
from spotter_context import TrafficApproach, estimated_gap_s
from test_spotter_upgrade import traffic_race
from voice_notes import VoiceNoteStore


class TrafficLabelTests(unittest.TestCase):
    def test_lap_count_difference_outside_a_race_is_not_called_lapping(self):
        tracker = TrafficApproach()
        for t in range(3):
            race = traffic_race(t, 'behind', False, 1)
            race['session_type'] = 'Practice'
            tracker.ingest(race, t)
        behind = tracker.view(2)['behind']
        self.assertEqual(behind['kind'], 'same_class')
        self.assertIsNone(behind['call_key'])

    def test_class_estimate_decides_class_pace_after_one_slow_own_lap(self):
        tracker = TrafficApproach()
        for t in range(3):
            race = traffic_race(t, 'behind')
            own, other = race['cars']
            # Own last lap was a spin (slow); the other class is really the slower one.
            own.update(last_lap_s=140, class_est_lap_s=100)
            other.update(last_lap_s=120, class_est_lap_s=120)
            tracker.ingest(race, t)
        behind = tracker.view(2)['behind']
        self.assertEqual(behind['kind'], 'slower_class')
        self.assertIsNone(behind['call_key'])

    def test_estimated_gap_uses_sdk_estimate_only_when_it_agrees(self):
        own = {'class_id': 1, 'class_est_lap_s': 100., 'est_time_s': 50.}
        same = {'class_id': 1, 'est_time_s': 53.}
        self.assertAlmostEqual(estimated_gap_s(own, same, .04, 100.), 3.)
        # Start/finish wrap: 1s before the line versus 2s after it.
        self.assertAlmostEqual(estimated_gap_s({**own, 'est_time_s': 99.}, {**same, 'est_time_s': 2.}, .03, 100.), 3.)
        # Disagreeing sign, another class or missing data keep the distance estimate.
        self.assertAlmostEqual(estimated_gap_s(own, {**same, 'est_time_s': 40.}, .04, 100.), 4.)
        self.assertAlmostEqual(estimated_gap_s(own, {**same, 'class_id': 2}, .04, 100.), 4.)
        self.assertAlmostEqual(estimated_gap_s({'class_id': 1}, {'class_id': 1}, .04, 100.), 4.)
        self.assertIsNone(estimated_gap_s({'class_id': 1}, {'class_id': 1}, .04, None))

    def test_read_race_carries_estimates_and_tolerates_their_absence(self):
        class Sdk(dict):
            pass
        sdk = Sdk(DriverInfo={'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'CarClassID': 1, 'CarClassEstLapTime': 91.5},
                                                             {'CarIdx': 1, 'CarClassID': 1}]},
                  SessionInfo={'Sessions': [{'SessionNum': 0, 'SessionType': 'Race'}]}, WeekendInfo={},
                  CarIdxEstTime=[10., 12.5])
        cars = read_race(sdk, {'CarIdxEstTime'}, 0)['cars']
        self.assertEqual((cars[0]['class_est_lap_s'], cars[0]['est_time_s'], cars[1]['est_time_s']), (91.5, 10., 12.5))
        self.assertIsNone(cars[1]['class_est_lap_s'])
        self.assertIsNone(read_race(sdk, set(), 0)['cars'][0]['est_time_s'])


class NoteStorageTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name) / 'notes.sqlite3'

    def test_sample_trail_is_kept_but_not_listed(self):
        store = VoiceNoteStore(self.path)
        trail = [{'session_time': 1., 'distance_pct': .1}]
        note = store.save('앞이 밀려', {'snapshot': trail[0], 'recent': trail, 'connected': True})
        self.assertEqual(note['context']['recent'], trail)
        self.assertNotIn('recent', store.list()[0]['context'])
        self.assertEqual(store.list(with_recent=True)[0]['context']['recent'], trail)
        self.assertEqual(store.recents([note['id'], 'missing']), {note['id']: trail})
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertNotIn('"recent"', db.execute('SELECT context_json FROM voice_notes').fetchone()[0])
        revision = store.revision
        store.update(note['id'], reply='확인')
        self.assertGreater(store.revision, revision)
        self.assertEqual(store.get(note['id'])['context']['recent'], trail)

    def test_older_database_is_migrated_without_losing_samples(self):
        trail = [{'session_time': 5., 'distance_pct': .5}]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''CREATE TABLE voice_notes (id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                text TEXT NOT NULL, original_text TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
                context_json TEXT NOT NULL, feeling_json TEXT NOT NULL, reply_source TEXT NOT NULL DEFAULT '',
                service_error TEXT NOT NULL DEFAULT '')''')
            db.execute("INSERT INTO voice_notes (id,created_at,updated_at,text,original_text,source,context_json,feeling_json) VALUES ('a',1,1,'t','t','TEXT',?,'[]')",
                       (json.dumps({'snapshot': trail[0], 'recent': trail}),))
        for _ in range(2):  # opening twice must be harmless
            store = VoiceNoteStore(self.path)
            self.assertTrue(store.available)
            self.assertEqual(store.get('a')['context'], {'snapshot': trail[0], 'recent': trail})
            self.assertEqual(store.list()[0]['context'], {'snapshot': trail[0]})


class LiveBufferTests(unittest.TestCase):
    def test_buffer_matches_reported_window_and_hotkey_state_is_light(self):
        now = [1000.]
        live = LiveTelemetry(clock=lambda: now[0])
        self.assertEqual(live.samples.maxlen, live.SAMPLE_RATE_HZ * live.BUFFER_SECONDS)
        base = {'session_num': 0, 'lap_number': 1, 'on_track': True, 'is_replay': False, 'on_pit_road': False}
        for index in range(60):
            now[0] = 1000. + index
            live.ingest({'car_id': '1'}, {**base, 'session_time': float(index), 'distance_pct': index / 100})
        recent = live.context()['recent']
        self.assertEqual(recent[-1]['session_time'], 59.)
        self.assertTrue(all(point['wall_time'] >= now[0] - 20 for point in recent))
        self.assertEqual([point['session_time'] for point in recent], sorted(point['session_time'] for point in recent))
        self.assertEqual(live.hotkey_state(), {'hotkey_serial': 0, 'hotkey_events': []})
        self.assertEqual(live.status()['buffer_seconds'], 180)


class AnalysisCacheTests(unittest.TestCase):
    def setUp(self):
        api._CORNER_DEFINITIONS.clear()

    def late_corner(self):
        data = telemetry()
        distance = np.linspace(.0003, .9997, len(data.channels['LapDistPct']))
        braking = (distance > .96) & (distance < .985)
        data.channels.update({
            'LapDistPct': distance, 'SessionTime': 1000 + distance * 100, 'LapCurrentLapTime': distance * 100,
            'Brake': np.where(braking, .8, 0.), 'Throttle': np.where(distance < .96, 1., 0.),
            'Speed': np.where(distance < .96, 50., np.where(distance < .985, 50 - (distance - .96) * 1200, 20.))})
        return data

    def test_corner_reaching_the_finish_line_is_trimmed_not_dropped(self):
        analyzer = api.TelemetryAnalyzer(self.late_corner())
        grid = analyzer._lap_grid(analyzer.valid_laps[0])
        self.assertFalse(grid['_coverage'][-1])
        corners = analyzer._detect_corners(grid)
        self.assertEqual(len(corners), 1)
        self.assertLess(corners[0]['exit'], len(grid['distance_pct']) - 1)
        self.assertGreater(corners[0]['exit_pct'], .99)
        self.assertTrue(grid['_coverage'][corners[0]['entry']:corners[0]['exit'] + 1].all())

    def test_second_analyzer_reuses_grids_and_metrics_with_equal_results(self):
        data = self.late_corner()
        first = api.TelemetryAnalyzer(data)
        grid = first._lap_grid(first.valid_laps[0])
        definitions = first._detect_corners(grid)
        expected = first._corner_analysis(grid, None, definitions)
        second = api.TelemetryAnalyzer(data)
        self.assertIs(second._lap_grid(second.valid_laps[0]), grid)
        calls = []
        second._compute_corner_metrics = lambda *args: calls.append(args) or {}
        self.assertEqual(second._corner_analysis(grid, None, definitions), expected)
        self.assertEqual(calls, [])
        # A copied grid is a different object and must be measured, not served from the memo.
        second._corner_metrics(dict(grid), definitions[0]['entry'], definitions[0]['apex'], definitions[0]['exit'])
        self.assertEqual(len(calls), 1)
        # Returned metrics are copies; a caller cannot corrupt the memo.
        first._corner_metrics(grid, definitions[0]['entry'], definitions[0]['apex'], definitions[0]['exit'])['min_speed_kph'] = -1
        self.assertEqual(first._corner_analysis(grid, None, definitions), expected)

    def test_corner_definitions_survive_restart_only_for_real_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'corners.json'
            identity = ('car-1', 'path', 'name', 'track-1', 'tpath', 'tname', '', 'v1', 5000)
            definitions = [{'id': 'C1', 'source': 'BRAKING_METERS', 'entry_pct': .1, 'apex_pct': .2, 'exit_pct': .3}]
            store = CornerDefinitionStore(path)
            self.assertIsNone(store.get(identity))
            store.put(identity, definitions)
            store.put(identity, [])  # the first definition keeps defining the layout
            self.assertEqual(CornerDefinitionStore(path).get(identity), definitions)
            path.write_text('{"version": 0, "items": {}}', encoding='utf-8')
            self.assertIsNone(CornerDefinitionStore(path).get(identity))
            path.write_text('not json', encoding='utf-8')
            self.assertIsNone(CornerDefinitionStore(path).get(identity))
        self.assertTrue(api._corner_identity_persistent(identity))
        self.assertFalse(api._corner_identity_persistent(('', 'path', 'name', '', 'tpath', 'tname', '', '', 5000)))


class ExposureTests(unittest.TestCase):
    def test_status_summary_never_carries_the_analysis_result(self):
        previous = dict(api.shared_sync_state)
        self.addCleanup(lambda: api.shared_sync_state.update(previous))
        api.shared_sync_state.update(analysis_result={'big': 'x' * 1000}, last_analysis_time=5., file_signature=('f', 1, 2))
        summary = api.shared_state_summary()
        self.assertEqual(summary, {'last_analysis_time': 5., 'file_signature': ('f', 1, 2), 'analysis_available': True})

    def test_default_origins_are_local_screens_only(self):
        origins = api.DEFAULT_CORS_ORIGINS.split(',')
        self.assertIn('http://localhost:24157', origins)
        self.assertNotIn('*', origins)
        self.assertTrue(all(origin.startswith(('http://localhost:', 'http://127.0.0.1:')) for origin in origins))


if __name__ == '__main__':
    unittest.main()
