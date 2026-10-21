"""Each recorded call carries the game, track and car of the drive that is running, and only that drive."""
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import voice_coach


class RecordedSessionTests(unittest.TestCase):
    def test_deliveries_of_the_current_drive_name_its_game_track_and_car(self):
        stored = []
        live = SimpleNamespace(lock=threading.Lock(), run_id='run-now-0000',
                               identity={'game': 'lmu', 'track_name': 'Monza', 'car_name': 'GT3', 'user_name': 'secret'},
                               pit_wall=SimpleNamespace(race={'session_type': 'Race'}))
        coach = SimpleNamespace(live=live, store=SimpleNamespace(record_radio=stored.extend), speech=None)
        app = FastAPI()
        with mock.patch.object(voice_coach, 'SpotterAudio'):
            voice_coach.register_voice_routes(app, coach, None, None, None, None)
        event = {'event_id': 'e1', 'key': 'left', 'status': 'completed'}
        response = TestClient(app).post('/api/live-coach/radio-delivery', json={'events': [
            {**event, 'run_id': 'run-now-0000'}, {**event, 'event_id': 'e2', 'run_id': 'older-run-00'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stored[0]['session'], {'game': 'lmu', 'track_name': 'Monza', 'track_path': None, 'track_config': None,
                                                'car_name': 'GT3', 'car_path': None, 'session_type': 'Race'})
        self.assertNotIn('session', stored[1])


if __name__ == '__main__':
    unittest.main()
