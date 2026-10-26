"""Independent local session diagnostics, silent exports and bounded storage."""
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import session_log
from voice_notes import VoiceNoteStore


class HealthTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = VoiceNoteStore(str(Path(folder.name) / 'notes.sqlite3'))

    def live(self, now=100.):
        return SimpleNamespace(lock=threading.RLock(), run_id='quiet-run', state='CONNECTED',
            identity={'game': 'lmu', 'track_name': 'Test', 'car_name': 'Test car', 'driver_name': 'PRIVATE'},
            samples=[{'wall_time': now, 'session_time': now - 90, 'lap_number': 1,
                      'on_track': True, 'is_replay': False, 'on_pit_road': False}],
            radio=SimpleNamespace(metadata={'session_type': 'Practice'}, latest={'brake': .6}, last_sample=now))

    def test_silent_run_exports_and_extent_does_not_end_with_last_radio(self):
        from session_health import SessionHealthRecorder
        live = self.live()
        recorder = SessionHealthRecorder(live, self.store, '2.3.1', clock=lambda: 100.)
        self.assertTrue(recorder.run_once())
        self.store.record_radio([{'event_id': 'quiet-run:1', 'run_id': 'quiet-run', 'key': 'left', 'status': 'completed'}])
        # Keep synthetic wall times on the same axis as diagnostic samples.
        with self.store.connect() as db:
            db.execute('UPDATE radio_delivery SET created_at=110')
        live.samples[-1].update(wall_time=160., session_time=70.)
        recorder.clock = lambda: 160.
        self.assertTrue(recorder.run_once())
        _, data = session_log.export(self.store, 'quiet-run', '2.3.1')
        manifest, radio, _ = session_log.read(data)
        self.assertEqual((manifest['started_at'], manifest['ended_at']), (100., 160.))
        self.assertEqual(len(radio), 1)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            health = json.loads(archive.read('session-health.json'))
        self.assertEqual(len(health['samples']), 2)
        self.assertNotIn('PRIVATE', json.dumps(health))
        # A second run has no voice event at all and still remains inspectable.
        live.run_id = 'silent-second-run'
        self.assertTrue(recorder.run_once())
        self.assertEqual(next(r for r in session_log.sessions(self.store) if r['run_id'] == live.run_id)['calls'], 0)
        _, data = session_log.export(self.store, live.run_id, '2.3.1')
        self.assertEqual(session_log.read(data)[1], [])

    def test_heartbeat_is_throttled_and_a_stale_link_is_recorded_once(self):
        from session_health import SessionHealthRecorder
        live = self.live()
        recorder = SessionHealthRecorder(live, self.store, '2.3.1', clock=lambda: 100.)
        self.assertTrue(recorder.run_once())
        live.samples[-1]['wall_time'] = 101.
        recorder.clock = lambda: 101.
        self.assertFalse(recorder.run_once())
        recorder.clock = lambda: 104.
        self.assertTrue(recorder.run_once())
        recorder.clock = lambda: 200.
        self.assertFalse(recorder.run_once())
        _, data = session_log.export(self.store, live.run_id, '2.3.1')
        health = json.loads(zipfile.ZipFile(io.BytesIO(data)).read('session-health.json'))
        self.assertEqual(health['samples'][-1]['state'], 'STALE')

    def test_failed_write_can_retry_and_does_not_break_live_collection(self):
        from session_health import SessionHealthRecorder
        recorder = SessionHealthRecorder(self.live(), self.store, '2.3.1', clock=lambda: 100.)
        with patch.object(self.store, 'record_session_health', side_effect=OSError('synthetic locked disk')):
            self.assertFalse(recorder.run_once())
        self.assertTrue(recorder.run_once())

    def test_persistence_is_bounded_and_first_observation_survives_tail_rotation(self):
        from session_health import MAX_RUNS, MAX_SAMPLES
        for i in range(MAX_SAMPLES + 5):
            self.store.record_session_health('bounded', {'game': 'lmu'}, {'at': float(i), 'state': 'CONNECTED'})
        _, data = session_log.export(self.store, 'bounded', '2.3.1')
        manifest = session_log.read(data)[0]
        health = json.loads(zipfile.ZipFile(io.BytesIO(data)).read('session-health.json'))
        self.assertEqual(len(health['samples']), MAX_SAMPLES)
        self.assertEqual(manifest['started_at'], 0.)
        self.assertEqual(health['dropped_samples'], 5)
        for i in range(MAX_RUNS + 1):
            self.store.record_session_health(f'run-{i}', {}, {'at': float(1000 + i), 'state': 'CONNECTED'})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_health').fetchone()[0], MAX_RUNS)

    def test_feedback_is_labelled_as_current_and_old_run_never_gets_new_context(self):
        from session_health import feedback_context
        live = self.live()
        matched = feedback_context(live, 'quiet-run', 100.)
        self.assertEqual(matched['scope'], 'LATEST_AT_FEEDBACK')
        self.assertEqual(matched['sample']['brake'], .6)
        self.assertEqual(feedback_context(live, 'old-run', 100.),
                         {'captured_at': 100., 'scope': 'LATEST_AT_FEEDBACK', 'matched_run': False})

    def test_owned_sampler_stops_and_storage_io_does_not_hold_the_live_lock(self):
        from session_health import SessionHealthRecorder
        live = self.live()
        live.lock = threading.Lock()
        written = threading.Event()
        original = self.store.record_session_health
        def record(*args):
            self.assertTrue(live.lock.acquire(blocking=False))
            live.lock.release()
            original(*args)
            written.set()
        recorder = SessionHealthRecorder(live, self.store, '2.3.1', clock=lambda: 100.)
        with patch.object(self.store, 'record_session_health', side_effect=record):
            recorder.start()
            try:
                self.assertTrue(written.wait(2))
            finally:
                recorder.stop()
        self.assertFalse(recorder.thread.is_alive())
        _, data = session_log.export(self.store, live.run_id, '2.3.1')
        health = session_log.read_extras(data)['session_health']
        self.assertEqual(health['samples'][-1]['state'], 'BACKEND_STOPPING')

    def test_delivery_api_records_latest_conditions_but_not_for_old_sessions(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import voice_coach
        live = self.live()
        live.pit_wall = SimpleNamespace(race={'session_type': 'Practice'})
        coach = SimpleNamespace(live=live, speech=None, store=self.store)
        app = FastAPI(version='2.3.1')
        with patch.object(voice_coach, 'SpotterAudio'):
            voice_coach.register_voice_routes(app, coach, None, None, None, None)
        response = TestClient(app).post('/api/live-coach/radio-delivery', json={'events': [
            {'event_id': 'quiet-run:1', 'run_id': 'quiet-run', 'key': 'straight_brief', 'status': 'cancelled'},
            {'event_id': 'old:1', 'run_id': 'old', 'key': 'straight_brief', 'status': 'cancelled'}]})
        self.assertEqual(response.status_code, 200)
        events = {event['run_id']: event for event in self.store.radio_log()}
        self.assertEqual(events['quiet-run']['feedback_context']['sample']['brake'], .6)
        self.assertNotIn('sample', events['old']['feedback_context'])
