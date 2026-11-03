"""Finish audio reuses prepared chequered speech despite changing live ranks."""
import base64
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_race_radio
from race_radio import STATIC_CALLS


class FinishPreparedAudioTests(unittest.TestCase):
    setUp = test_race_radio.RadioRouteTests.setUp

    def finish_event(self):
        previous = {**self.sample, 'on_track': True, 'lap_completed': 10, 'session_flags': 1}
        self.sample.update(lap_completed=11, session_flags=1, session_state=5)
        radio = self.live.radio
        radio._results(self.sample, previous, self.now[0], radio.metadata)
        self.now[0] += 3.1
        radio.latest, radio.last_sample = dict(self.sample), self.now[0]
        return next(e for e in radio.pending if e['key'] == 'race_result')['id']

    def test_rank_change_during_prepared_render_updates_text_without_changing_audio(self):
        identifier = self.finish_event()
        audio = base64.b64encode(b'ID3' + b'x' * 500).decode()
        def prepared(role, text):
            self.assertEqual((role, text), ('spotter', STATIC_CALLS['finish'][0]))
            self.sample['class_position'] = 2
            self.live.radio._results(self.sample, self.sample, self.now[0], self.live.radio.metadata)
            return audio
        with patch.object(self.coach, 'radio_audio_mode', return_value=(True, True)), \
             patch.object(self.coach.fixed_audio, 'read_prepared', side_effect=prepared), TestClient(self.app) as client:
            result = client.post(f'/api/live-coach/spotter/radio/{identifier}/render').json()
        self.assertEqual(result['audio_base64'], audio)
        self.assertIn('2위', result['event']['text'])
        self.assertIn('잠정', result['event']['text'])
        self.speech.synthesize.assert_not_called()

    def test_missing_prepared_finish_never_requests_synthesis(self):
        identifier = self.finish_event()
        with patch.object(self.coach, 'radio_audio_mode', return_value=(False, True)), TestClient(self.app) as client:
            result = client.post(f'/api/live-coach/spotter/radio/{identifier}/render').json()
        self.assertEqual(result, {'event': None, 'missing_prepared_audio': True})
        self.speech.synthesize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
