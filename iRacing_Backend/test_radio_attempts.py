"""Playback diagnostic schema and actual API/store/ZIP preservation, isolated only."""
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import session_log
import voice_coach
from voice_notes import VoiceNoteStore


class RadioAttemptTests(unittest.TestCase):
    def attempt(self, **patches):
        return dict(attempt_id='radio-random:1', owner_generation='radio-random:2', game='lmu',
            stage='routing', started=False, created_at=1700000000., elapsed_ms=5000.,
            output_route='fallback', part_index=0, **patches)

    def test_invalid_or_identifying_fields_are_rejected(self):
        base = self.attempt()
        for fields in ({'device_id': 'headset'}, {'stage': 'unknown'}, {'elapsed_ms': float('nan')},
                       {'started': 'false'}, {'part_index': True}, {'attempt_id': 'C:/user/private'},
                       {'game': 'other'}, {'duration_s': float('inf')},
                       {'ready_state': 5}, {'network_state': True}, {'media_error': 0},
                       {'paused': 'false'}, {'effect_attached': 1}, {'context_state': 'USB headset'},
                       {'recovery_plain': 'true'}, {'start_failures': 256}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                voice_coach.RadioPlaybackDiagnostics(**{**base, **fields})

    def test_api_preserves_attempts_to_store_and_export_and_counts_terminal_truth(self):
        with tempfile.TemporaryDirectory() as folder:
            store = VoiceNoteStore(str(Path(folder) / 'notes.sqlite3'))
            live = SimpleNamespace(lock=threading.Lock(), run_id='synthetic-run',
                identity={'game': 'lmu', 'car_name': 'GT3', 'track_name': 'Test'},
                pit_wall=SimpleNamespace(race={'session_type': 'Race'}))
            coach = SimpleNamespace(live=live, store=store, speech=None)
            app = FastAPI()
            with patch.object(voice_coach, 'SpotterAudio'):
                voice_coach.register_voice_routes(app, coach, None, None, None, None)
            events = [dict(event_id='same-event', run_id='synthetic-run', key='yellow', status='failed',
                           reason='playback_start_timeout', playback=self.attempt(ready_state=0, network_state=2,
                               paused=True, effect_attached=True, context_state='suspended', recovery_plain=False,
                               start_failures=2, media_error=None), diagnostics_dropped=3),
                      dict(event_id='same-event', run_id='synthetic-run', key='yellow', status='started',
                           playback={**self.attempt(), 'attempt_id': 'radio-random:2', 'stage': 'playing', 'started': True}),
                      dict(event_id='same-event', run_id='synthetic-run', key='yellow', status='completed',
                           playback={**self.attempt(), 'attempt_id': 'radio-random:2', 'stage': 'playing', 'started': True})]
            response = TestClient(app).post('/api/live-coach/radio-delivery', json={'events': events})
            self.assertEqual(response.status_code, 200)
            _, payload = session_log.export(store, 'synthetic-run', '2.4.5')
            manifest, radio, _ = session_log.read(payload)
            self.assertEqual([e['playback']['attempt_id'] for e in radio], ['radio-random:1', 'radio-random:2', 'radio-random:2'])
            self.assertEqual(radio[0]['playback']['stage'], 'routing')
            self.assertFalse(radio[0]['playback']['started'])
            self.assertEqual(radio[0]['playback']['start_failures'], 2)
            self.assertEqual(radio[0]['playback']['context_state'], 'suspended')
            self.assertTrue(radio[0]['playback']['paused'])
            self.assertEqual(radio[0]['diagnostics_dropped'], 3)
            self.assertEqual(manifest['calls']['yellow']['completed'], 1)


if __name__ == '__main__':
    unittest.main()
