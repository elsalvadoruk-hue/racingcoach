"""Oval diagnostics through the consented auto-upload path, with synthetic stores."""
import gzip
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import log_pack
import session_log
import session_log_auto as auto
from session_log_extras import nearby_cars
from voice_notes import VoiceNoteStore


IDENTITY = {'game': 'iracing', 'track_name': 'Test Oval', 'track_path': 'test oval',
            'track_id': '123', 'track_type': 'short oval', 'track_direction': 'left',
            'car_name': 'Test car', 'session_type': 'Practice'}


class OvalAutoLogTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = patch.dict(os.environ, {'IRACING_DATA_DIR': folder.name,
                                     'IRACING_LOG_UPLOAD_URL': 'https://script.google.com/macros/s/test/exec'})
        env.start()
        self.addCleanup(env.stop)
        self.store = VoiceNoteStore(str(self.folder / 'notes.sqlite3'))
        self.sent = []
        self.uploader = auto.AutoUploader(self.store, 'test', current_run=lambda: None,
            clock=lambda: 100_000., send=lambda *args: self.sent.append(args))

    def health(self, run='oval', identity=None, start=60_000., end=60_180.):
        for at in (start, end):
            self.store.record_session_health(run, dict(IDENTITY if identity is None else identity),
                {'at': at, 'state': 'CONNECTED', 'on_track': True, 'is_replay': False,
                 'oval': {'pits_open': False, 'player_pace_row': 0}})

    def consent(self):
        auto.set_enabled(True, auto.CONSENT_VERSION, now=50_000.)

    def traffic(self, count=2400):
        rng = random.Random(42)
        rows = [{'t': round(i / 10, 1), 'own': [i // 100, i % 100, 0],
                 'c': [[j, round(rng.uniform(-200, 200), 4), round(rng.random(), 5)] for j in range(10)],
                 'oval': [0, None, False, i % 3]} for i in range(count)]
        header = {'run_id': 'oval', 'version': 3, 'spotter_profile': 'ASPHALT_OVAL',
                  'track_type': 'short oval', 'oval_fields': ['pace_mode', 'pits_open', 'row', 'flags']}
        path = self.folder / 'traffic-test.jsonl.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as handle:
            handle.write(json.dumps(header) + '\n')
            for row in rows:
                handle.write(json.dumps(row) + '\n')
        return path, header, rows

    def test_silent_confirmed_oval_is_sent_once_with_its_classification(self):
        self.health()
        self.consent()
        self.assertEqual(self.uploader.run_once(), 1)
        manifest, radio, _ = session_log.read(self.sent[0][2])
        self.assertEqual(manifest['spotter']['profile'], 'ASPHALT_OVAL')
        self.assertEqual(radio, [])
        self.assertEqual(self.uploader.run_once(), 0)
        self.assertFalse(manifest['questions_included'])

    def test_quiet_road_dirt_unknown_and_lmu_keep_the_existing_call_threshold(self):
        for kind, game in (('road course', 'iracing'), ('dirt oval', 'iracing'), ('', 'iracing'), ('short oval', 'lmu')):
            self.health(run=kind + game, identity={**IDENTITY, 'track_type': kind, 'game': game})
        self.consent()
        self.assertEqual(self.uploader.run_once(), 0)

    def test_silent_replay_or_parked_oval_is_not_a_driven_session(self):
        for run, on_track, replay in (('replay', True, True), ('parked', False, False)):
            for at in (60_000., 60_180.):
                self.store.record_session_health(run, IDENTITY,
                    {'at': at, 'state': 'CONNECTED', 'on_track': on_track, 'is_replay': replay})
        self.consent()
        self.assertEqual(self.uploader.run_once(), 0)

    def test_oval_still_requires_consent_duration_finished_and_non_current_run(self):
        self.health()
        self.assertEqual(self.uploader.run_once(), 0)
        self.consent()
        self.uploader.current_run = lambda: 'oval'
        self.assertEqual(self.uploader.run_once(), 0)
        self.uploader.current_run = lambda: None
        self.uploader.clock = lambda: 60_200.
        self.assertEqual(self.uploader.run_once(), 0)
        self.uploader.clock = lambda: 100_000.
        self.health('short', end=60_060.)
        self.health('almost-two-minutes', end=60_119.)
        self.health('before', start=40_000., end=40_180.)
        self.assertEqual(self.uploader.run_once(), 1)
        auto.set_enabled(False)
        self.health('after', start=70_000., end=70_180.)
        self.assertEqual(self.uploader.run_once(), 0)

    def test_older_report_identity_cannot_erase_observed_oval_metadata(self):
        self.health()
        with patch('voice_notes.time.time', return_value=60_010.):
            self.store.save('', {'snapshot': {'run_id': 'oval'},
                'identity': {'game': 'iracing', 'track_name': 'Test Oval', 'car_name': 'Test car'}}, source='AUTO_REPORT')
        listed = session_log.sessions(self.store)[0]
        self.assertEqual(listed['spotter_profile'], 'ASPHALT_OVAL')
        _, data = session_log.export(self.store, 'oval', 'test')
        self.assertEqual(session_log.read(data)[0]['spotter']['profile'], 'ASPHALT_OVAL')

    def test_size_limited_upload_keeps_explicit_lossless_windows_and_health(self):
        self.health()
        path, original_header, rows = self.traffic()
        provider = nearby_cars(self.folder, None)
        _, full = session_log.export(self.store, 'oval', 'test', extras=[provider])
        self.assertEqual(session_log.read_extras(full)['traffic'], (original_header, rows))
        with patch.object(session_log, 'SEND_LIMIT', 110_000):
            self.uploader.extras = (provider,)
            self.consent()
            self.assertEqual(self.uploader.run_once(), 1)
        data = self.sent[0][2]
        self.assertLessEqual(len(data), 110_000)
        manifest = session_log.read(data)[0]
        parts = session_log.read_extras(data)
        header, selected = parts['traffic']
        selection = header['export_selection']
        self.assertEqual(selection['mode'], 'event_windows')
        self.assertEqual(selection['source_rows'], len(rows))
        self.assertLess(len(selected), len(rows))
        self.assertLessEqual(len(selected), 1200)
        expected = [row for segment in selection['segments'] for row in rows[segment['start']:segment['stop']]]
        self.assertEqual(selected, expected)
        self.assertEqual(selected[0], rows[0])
        self.assertEqual(selected[-1], rows[-1])
        self.assertTrue(manifest['extras']['nearby_cars']['included'])
        self.assertTrue(manifest['extras']['session_health']['included'])
        self.assertEqual(log_pack.read_lines(path), (original_header, rows))

    def test_large_export_reduces_traffic_before_discarding_it(self):
        self.health()
        self.traffic()
        _, data = session_log.export(self.store, 'oval', 'test',
            extras=[nearby_cars(self.folder, None)], limit=110_000)
        self.assertIn('traffic', session_log.read_extras(data))

    def test_export_passes_failure_clock_without_question_text_to_reduction(self):
        import oval_review
        self.health()
        self.traffic()
        self.store.record_radio([{'event_id': 'failed', 'run_id': 'oval', 'key': 'answer',
                                 'status': 'failed', 'session_time': 130.5, 'text': 'private question reply'}])
        with patch('oval_review.select_windows', wraps=oval_review.select_windows) as select:
            _, data = session_log.export(self.store, 'oval', 'test',
                extras=[nearby_cars(self.folder, None)], limit=110_000)
        self.assertTrue(select.called)
        self.assertEqual(select.call_args.args[2], [{'session_time': 130.5, 'status': 'failed'}])
        _, selected = session_log.read_extras(data)['traffic']
        self.assertTrue(any(row['t'] == 130.5 for row in selected))

    def test_base_metadata_survives_even_when_no_extra_can_fit(self):
        self.health()
        _, base = session_log.export(self.store, 'oval', 'test',
            extras=[lambda run: session_log.Extra('nearby_cars', {'large.bin': b'x' * 5000})], limit=2200)
        self.assertEqual(session_log.read(base)[0]['spotter']['profile'], 'ASPHALT_OVAL')
        self.assertLessEqual(len(base), 2200)

    def test_broken_reduction_does_not_prevent_base_upload(self):
        self.health()
        def broken():
            raise OSError('synthetic removed log')
        extra = session_log.Extra('nearby_cars', {'large.bin': b'x' * 5000}, fallback=broken)
        with self.assertLogs('session_log', level='ERROR'):
            _, data = session_log.export(self.store, 'oval', 'test', extras=[lambda run: extra], limit=2200)
        manifest = session_log.read(data)[0]
        self.assertEqual(manifest['spotter']['profile'], 'ASPHALT_OVAL')
        self.assertFalse(manifest['extras']['nearby_cars']['included'])

    def test_tiny_window_budget_shrinks_without_changing_selected_values(self):
        from session_log_extras import _oval_reduction
        path, _, rows = self.traffic()
        with patch('session_log_extras.OVAL_PACKED_LIMIT', 10_000):
            reduced = _oval_reduction(path, 'oval')
        data = reduced.files['traffic.packed.xz']
        self.assertLessEqual(len(data), 10_000)
        header, selected = log_pack.unpack(data)
        segments = header['export_selection']['segments']
        self.assertEqual(selected, [row for part in segments for row in rows[part['start']:part['stop']]])
        self.assertLess(len(selected), 1200)


if __name__ == '__main__':
    unittest.main()
