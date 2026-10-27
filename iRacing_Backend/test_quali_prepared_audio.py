"""Qualifying standings use prepared speech without changing measured screen values."""
import asyncio
import unittest
from unittest.mock import MagicMock

import radio_phrases
import test_engineer_variation as radio_fixtures
import test_radio_optional_parts as render_fixtures


class QualifyingPreparedAudioTests(unittest.TestCase):
    def event(self, gap, position=2):
        fixture = radio_fixtures.RadioCallTests()
        fixture.setUp()
        best = 100.
        cars = [radio_fixtures.car(0, best)]
        cars += [radio_fixtures.car(i, best - gap) for i in range(1, position)] if position > 1 else [radio_fixtures.car(1, best + gap)]
        fixture.meta.update(session_type='Open Qualify', cars=cars)
        fixture.tick()
        fixture.lap(best)
        fixture.wait(1.6)
        return fixture.events()['quali_result' if position > 1 else 'quali_p1']

    def test_outside_prepared_gap_keeps_screen_and_speaks_neutral_rank(self):
        for gap in (3.1, 3.8, 4.1, 4.6, 8.8, .01):
            for position in (1, 2, 40, 41, 60):
                with self.subTest(gap=gap, position=position):
                    event = self.event(gap, position)
                    self.assertIn(f'{gap:.1f}초', event['text'])
                    self.assertEqual(event['speech_text'], radio_phrases.build('position_now', pos=position)['text'])
                    self.assertEqual(event['engineer'], 'spotter')
                    self.assertTrue(event['prepared_only'])
                    self.assertEqual(event['alert_categories'], ['spotter.quali'])
                    self.assertNotIn('immediate', event)

    def test_equal_best_times_use_neutral_first_place_without_inventing_gap(self):
        event = self.event(0., 1)
        self.assertIn('0.0초', event['text'])
        self.assertEqual(event['speech_text'], radio_phrases.build('position_now', pos=1)['text'])

    def test_formatted_boundaries_keep_prepared_full_phrase(self):
        for gap in (.06, .1, 2.99, 3., 3.04):
            for position in (1, 2, 40):
                with self.subTest(gap=gap, position=position):
                    event = self.event(gap, position)
                    self.assertEqual(event['speech_text'], event['text'])
                    self.assertEqual(event['engineer'], 'race')
                    self.assertTrue(event['prepared_only'])

    def test_rank_outside_full_phrase_range_uses_rank_audio(self):
        for position in (41, 60):
            event = self.event(.3, position)
            self.assertEqual(event['speech_text'], radio_phrases.build('position_now', pos=position)['text'])

    def test_missing_or_deleted_prepared_clip_never_synthesizes(self):
        fixture = render_fixtures.OptionalRadioPartsTests()
        for position, cached, deleted in ((2, False, False), (2, True, False), (2, True, True), (61, False, False)):
            with self.subTest(position=position, cached=cached, deleted=deleted):
                fixture.setUp()
                event = self.event(4.1, position)
                fixture.event = event
                fixture.stored.clear()
                if cached:
                    fixture.stored.add(event['speech_text'])
                fixture.coach.fixed_audio.read_prepared = MagicMock(return_value='prepared-rank')
                if deleted:
                    fixture.coach.fixed_audio.read_prepared.side_effect = FileNotFoundError()
                result = asyncio.run(fixture.render(event['id']))
                if cached and not deleted:
                    self.assertEqual(result['audio_base64'], 'prepared-rank')
                else:
                    self.assertTrue(result['missing_prepared_audio'])
                fixture.coach.fixed_audio.phrase.assert_not_called()
                fixture.synthesize.assert_not_called()

    def test_missing_custom_pack_never_requests_cloud(self):
        fixture = render_fixtures.OptionalRadioPartsTests()
        fixture.setUp()
        fixture.event = self.event(8.8)
        fixture.stored.clear()
        fixture.coach.custom_packs.has_selection = lambda role: True
        self.assertTrue(asyncio.run(fixture.render(fixture.event['id']))['missing_prepared_audio'])
        fixture.coach.fixed_audio.phrase.assert_not_called()

    def test_cached_fallback_does_not_wait_for_an_active_cloud_question(self):
        fixture = render_fixtures.OptionalRadioPartsTests()
        fixture.setUp()
        fixture.event = self.event(4.1)
        fixture.stored.add(fixture.event['speech_text'])
        fixture.coach.fixed_audio.read_prepared = MagicMock(return_value='prepared-rank')
        async def during_question():
            async with fixture.coach.cloud_lock:
                return await asyncio.wait_for(fixture.render(fixture.event['id']), .5)
        self.assertEqual(asyncio.run(during_question())['audio_base64'], 'prepared-rank')
        fixture.coach.fixed_audio.phrase.assert_not_called()
