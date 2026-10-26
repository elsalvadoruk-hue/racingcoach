"""Lap speech starts at the line or expires, using prepared audio only."""
import asyncio
import unittest
from unittest.mock import MagicMock

import test_race_radio as race_fixtures
import test_radio_optional_parts as audio_fixtures


class ImmediateLapTests(unittest.TestCase):
    def setUp(self):
        self.fixture = race_fixtures.RaceRadioTests()
        self.fixture.setUp()

    def test_lap_has_two_second_deadline_from_crossing(self):
        f = self.fixture
        f.tick(lap_completed=3, lap_last_s=103.)
        event = next(e for e in f.events() if e['key'] == 'lap_time')
        self.assertTrue(event.get('immediate'))
        self.assertAlmostEqual(event['expires_at'], f.now + 2)
        f.wait(2.1)
        self.assertNotIn('lap_time', f.keys())

    def test_delayed_published_time_does_not_restart_deadline(self):
        f = self.fixture
        f.tick(lap_completed=3)
        crossed = f.now
        f.wait(1.7)
        f.tick(lap_last_s=103.)
        event = next(e for e in f.events() if e['key'] == 'lap_time')
        self.assertAlmostEqual(event['expires_at'], crossed + 2)
        self.assertLessEqual(event['expires_in_ms'], 201)

    def test_time_published_after_deadline_is_skipped_but_statistics_survive(self):
        f = self.fixture
        f.tick(lap_completed=3)
        f.wait(2.)
        f.tick(lap_last_s=103.)
        self.assertNotIn('lap_time', f.keys())
        self.assertEqual(f.radio.laps[-1]['time'], 103.)

    def test_qualifying_time_does_not_wait_for_standings(self):
        f = self.fixture
        f.meta.update(session_type='Lone Qualify', player_idx=0,
                      cars=[{'idx': 0, 'class_id': 1, 'best_lap_s': 103.},
                            {'idx': 1, 'class_id': 1, 'best_lap_s': 102.}])
        f.tick()
        f.tick(lap_completed=3, lap_last_s=103.)
        lap = next(e for e in f.events() if e['key'] == 'lap_time')
        self.assertTrue(lap.get('immediate'))
        self.assertEqual(lap['engineer'], 'spotter')
        f.wait(1.6)
        result = next(e for e in f.events() if e['key'] == 'quali_result')
        self.assertNotEqual(result['group'], lap['group'])
        self.assertNotIn(lap['speech_text'], result['speech_text'])
        self.assertEqual(result['engineer'], 'race')


class PreparedImmediateAudioTests(unittest.TestCase):
    def setUp(self):
        self.fixture = audio_fixtures.OptionalRadioPartsTests()
        self.fixture.setUp()
        f = self.fixture
        f.event['immediate'] = True
        f.coach.fixed_audio.read_prepared = MagicMock(side_effect=f.coach.fixed_audio.phrase.side_effect)

    def test_immediate_call_skips_cold_optional_audio_without_cloud_lock(self):
        f = self.fixture
        result = asyncio.run(f.render('run:1'))
        self.assertEqual(result['audio_parts'], ['cached:' + f.first, 'cached:' + f.place])
        self.assertEqual(result['dropped_parts'], 1)
        f.coach.fixed_audio.phrase.assert_not_called()
        f.synthesize.assert_not_called()

    def test_cold_first_sentence_is_skipped_without_synthesis(self):
        f = self.fixture
        f.stored.remove(f.first)
        result = asyncio.run(f.render('run:1'))
        self.assertIsNone(result['event'])
        f.coach.fixed_audio.phrase.assert_not_called()
        f.synthesize.assert_not_called()

    def test_deleted_prepared_audio_cannot_fall_back_to_paid_synthesis(self):
        f = self.fixture
        f.coach.fixed_audio.read_prepared.side_effect = FileNotFoundError('deleted during read')
        result = asyncio.run(f.render('run:1'))
        self.assertIsNone(result['event'])
        f.coach.fixed_audio.phrase.assert_not_called()
        f.synthesize.assert_not_called()
