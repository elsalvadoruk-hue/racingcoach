"""Synthetic SDK states; no game, personal database, or cloud requests."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import CHANNELS, LiveTelemetry
from race_engineer import role_voice
from spotter import PHRASES, VARIANTS, Spotter, SpotterAudio
from voice_coach import VoiceCoach, register_voice_routes
from voice_notes import VoiceNoteStore


class SpotterTests(unittest.TestCase):
    def setUp(self):
        self.s = Spotter()
        self.now = 1000.
        self.sample = {'session_time': 0., 'on_track': True, 'is_replay': False,
                       'on_pit_road': False, 'car_left_right': 1}

    def tick(self, raw, advance=.03, run='run', **extra):
        self.now += advance
        sample = {**self.sample, 'session_time': self.now - 1000, 'car_left_right': raw, **extra}
        self.s.ingest(sample, run, self.now)
        return self.s.view(self.now)

    def stable(self, raw, duration=Spotter.ENTER_SECONDS + .01, **extra):
        self.tick(raw, **extra)
        return self.tick(raw, duration, **extra)

    def keys(self):
        return [e['key'] for e in self.s.events]

    def test_sdk_channel_and_dedicated_male_default(self):
        self.assertEqual(CHANNELS['car_left_right'], 'CarLeftRight')
        with patch.dict('os.environ', {'IRACING_TTS_SPOTTER_VOICE': ''}):
            self.assertEqual(role_voice('spotter'), 'ko-KR-Chirp3-HD-Fenrir')

    def test_initial_clear_and_off_never_announce_clear(self):
        # 0 is the sim's "off": everything starts over and nothing is called clear.
        self.stable(2)
        self.tick(0)
        self.stable(1)
        self.assertEqual(self.keys(), [])

    def test_a_short_dropout_keeps_the_call_and_a_long_one_starts_over_silently(self):
        for raw in [None, True, 7, -1, float('nan')]:
            with self.subTest(raw=raw):
                self.s.reset()
                self.stable(2)
                self.assertFalse(self.tick(raw, .3)['active'])
                self.stable(2)
                self.assertEqual(self.keys(), ['left'])          # the same car is not called again
                self.stable(1)
                self.assertEqual(self.keys(), ['left', 'clear_left'])
                self.stable(2)
                for _ in range(12):
                    self.tick(raw, .1)
                self.stable(1)
                self.assertEqual(self.keys(), [])                # unknown never becomes "clear"

    def test_each_occupied_state(self):
        for raw, key in [(2, 'left'), (3, 'right'), (4, 'both'), (5, 'two_left'), (6, 'two_right')]:
            with self.subTest(raw=raw):
                self.s.reset()
                view = self.stable(raw)
                self.assertEqual(self.keys(), [key])
                self.assertTrue(view['occupied'])

    def test_overlap_debounce_and_raw_priority_gate(self):
        view = self.tick(2)
        self.assertTrue(view['occupied'])
        self.assertFalse(view['events'])
        self.tick(1)
        self.tick(2)
        self.tick(2, Spotter.ENTER_SECONDS - .01)
        self.assertEqual(self.keys(), [])
        self.tick(2, .02)
        self.assertEqual(self.keys(), ['left'])

    def test_a_car_sweeping_past_is_never_called(self):
        self.tick(2)
        self.tick(2, .3)
        self.stable(1)
        self.assertEqual(self.keys(), [])                     # gone within the wait: no "left", no "clear"
        for _ in range(20):
            self.tick(3, .03, side_closing_mps=12.)           # 43 km/h faster along our car
        self.stable(1)
        self.assertEqual(self.keys(), [])

    def test_a_passing_car_that_settles_beside_us_is_called_then(self):
        for _ in range(20):
            self.tick(3, .03, side_closing_mps=10.)
        self.assertEqual(self.keys(), [])
        self.tick(3, .03, side_closing_mps=2.)
        self.assertEqual(self.keys(), ['right'])
        self.stable(1)
        self.assertEqual(self.keys(), ['right', 'clear_right'])

    def test_a_second_car_arriving_waits_too_and_a_leaving_one_is_said_at_once(self):
        self.stable(2)
        self.tick(4)
        self.tick(4, .2)
        self.assertEqual(self.keys(), ['left'])
        self.tick(4, .21)
        self.assertEqual(self.keys(), ['left', 'both'])
        self.tick(2)
        self.tick(2, self.s.clear_delay + .01)
        self.assertEqual(self.keys(), ['left', 'both', 'left_only'])

    def test_clear_confirmation_and_flapping(self):
        self.stable(2)
        self.tick(1)
        self.tick(1, .149)
        self.assertEqual(self.keys(), ['left'])
        self.tick(2)
        self.tick(2, .06)
        self.assertEqual(self.keys(), ['left'])
        self.stable(1)
        self.assertEqual(self.keys(), ['left', 'clear_left'])

    def test_remaining_side_and_two_to_one_semantics(self):
        for old, new, key in [(4, 2, 'left_only'), (4, 3, 'right_only'),
                              (5, 2, 'left_one'), (6, 3, 'right_one'),
                              (3, 1, 'clear_right'), (4, 1, 'clear_all'),
                              (2, 3, 'right')]:
            with self.subTest(old=old, new=new):
                self.s.reset()
                self.stable(old)
                self.stable(new)
                self.assertEqual(self.keys()[-1], key)

    def test_hold_repeat_only_after_three_seconds_of_live_data(self):
        self.stable(2)
        for _ in range(29):
            self.tick(2, .1)
        self.assertEqual(self.keys(), ['left'])
        self.tick(2, .11)
        self.assertEqual(self.keys(), ['left', 'hold_left'])
        self.tick(2, .1)
        self.assertEqual(self.keys(), ['left', 'hold_left'])

    def test_a_long_side_by_side_is_repeated_twice_then_only_before_corners(self):
        self.stable(2, corner_in_s=20.)
        for _ in range(300):                                  # 30 s alongside on a straight
            self.tick(2, .1, corner_in_s=20.)
        self.assertEqual(self.keys(), ['left', 'hold_left', 'hold_left'])
        for _ in range(40):                                   # the next corner comes up
            self.tick(2, .1, corner_in_s=2.5)
        self.assertEqual(self.keys()[-1], 'hold_left')
        self.assertEqual(len(self.keys()), 4)                 # once for that corner
        for _ in range(40):
            self.tick(2, .1, corner_in_s=10.)
        for _ in range(5):
            self.tick(2, .1, corner_in_s=2.)
        self.assertEqual(len(self.keys()), 5)                 # and once for the next
        self.stable(1)
        self.assertEqual(self.keys()[-1], 'clear_left')

    def test_pending_messages_discard_on_raw_change_and_expiry(self):
        self.assertEqual(len(self.stable(2)['events']), 1)
        self.assertEqual(self.tick(3)['events'], [])
        self.tick(3, .06)
        serial = self.s.serial
        self.assertEqual(self.s.view(self.now, after=serial)['events'], [])
        self.assertEqual(self.s.view(self.now + .61)['events'], [])
        self.assertFalse(self.s.view(self.now, connected=False)['active'])

    def test_missing_pit_flag_replay_and_out_of_car_reset_without_clear(self):
        for extra in [{'is_replay': True}, {'on_track': False}, {'on_pit_road': True},
                      {'on_pit_road': None}, {'is_replay': None}]:
            with self.subTest(extra=extra):
                self.stable(2)
                self.assertFalse(self.tick(2, **extra)['active'])
                self.stable(1)
                self.assertEqual(self.keys(), [])

    def test_gap_session_reset_and_pause_never_infer_clear(self):
        for change in [{'advance': .6}, {'run': 'new'}, {'session_time': -10}]:
            with self.subTest(change=change):
                self.stable(2)
                self.tick(1, **change)
                self.stable(1)
                self.assertNotIn('clear_left', self.keys())

    def test_live_status_has_stricter_freshness_than_coaching(self):
        clock = [self.now]
        live = LiveTelemetry(clock=lambda: clock[0])
        live.ingest({}, {**self.sample, 'session_num': 0})
        self.assertTrue(live.spotter_status()['active'])
        clock[0] += .51
        self.assertFalse(live.spotter_status()['active'])
        self.assertTrue(live.context()['connected'])


class SpotterAudioTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.patch = patch.dict('os.environ', {'IRACING_SPOTTER_AUDIO_DIR': str(self.root / 'clips')})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.speech = MagicMock()
        self.speech.synthesize.return_value = base64.b64encode(b'ID3' + b'x' * 500).decode()
        self.audio = SpotterAudio(self.speech, lambda: 'test-male')

    def test_prepare_persists_clips_and_reuses_them_without_cloud(self):
        first = self.audio.prepare()
        self.assertEqual(set(first['clips']), set(PHRASES))
        self.assertEqual(self.speech.synthesize.call_count, sum(map(len, VARIANTS.values())))
        self.assertTrue(all(c.kwargs['role'] == 'spotter' for c in self.speech.synthesize.call_args_list))
        self.speech.synthesize.reset_mock()
        self.assertEqual(SpotterAudio(self.speech, lambda: 'test-male').prepare(), first)
        self.speech.synthesize.assert_not_called()

    def test_voice_change_creates_separate_cache(self):
        self.audio.prepare()
        self.speech.synthesize.reset_mock()
        SpotterAudio(self.speech, lambda: 'second-male').prepare()
        self.assertEqual(self.speech.synthesize.call_count, sum(map(len, VARIANTS.values())))

    def test_failed_synthesis_is_not_cached(self):
        self.speech.synthesize.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.audio.prepare()
        self.assertEqual(list((self.root / 'clips').glob('*.mp3')), [])

    def test_prepare_route_and_fast_socket_do_not_write_notes_or_synthesize(self):
        live = LiveTelemetry(clock=lambda: 1000.)
        store = VoiceNoteStore(self.root / 'notes.sqlite3')
        coach = VoiceCoach(live, store, speech=self.speech)
        app = FastAPI()
        register_voice_routes(app, coach, None, None, None, None)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/live-coach/spotter/prepare').status_code, 200)
            self.speech.synthesize.reset_mock()
            self.assertEqual(client.post('/api/live-coach/spotter/prepare').status_code, 200)
            with client.websocket_connect('/api/live-coach/spotter/events') as socket:
                ready = socket.receive_json()
                self.assertEqual(ready['type'], 'ready')
                self.assertFalse(ready['active'])
                self.assertEqual(socket.receive_json()['type'], 'spotter')
            self.speech.synthesize.assert_not_called()
            self.assertEqual(store.list(), [])


if __name__ == '__main__':
    unittest.main()
