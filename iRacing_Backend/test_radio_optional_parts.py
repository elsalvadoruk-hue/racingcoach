"""Multipart radio survives missing optional clips; no cloud, game or personal stores."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
import zipfile
import io

from fastapi import HTTPException
from pydantic import ValidationError
from voice_coach import VoiceCoach, RadioDeliveryEvent, register_voice_routes
from voice_notes import VoiceNoteStore
import session_log
import radio_phrases
import test_engineer_variation as radio_fixtures


class QualifyingVoicePartsTests(unittest.TestCase):
    def test_qualifying_time_keeps_spotter_and_standings_are_a_separate_race_call(self):
        for standings in (True, False):
            fixture = radio_fixtures.RadioCallTests()
            fixture.setUp()
            fixture.meta.update(session_type='Lone Qualify', cars=[radio_fixtures.car(0, 91.), radio_fixtures.car(1, 90.7)] if standings else [])
            fixture.tick()
            fixture.lap(91.)
            lap = fixture.events()['lap_time']
            self.assertEqual(lap['engineer'], 'spotter')
            self.assertTrue(lap['immediate'])
            self.assertIn('1분 31초', lap['speech_text'])
            fixture.wait(1.6)
            if standings:
                event = fixture.events()['quali_result']
                self.assertEqual(event['engineer'], 'race')
                self.assertNotIn('1분 31초', event['speech_text'])
                self.assertNotEqual(event['group'], lap['group'])
            else:
                self.assertNotIn('quali_result', fixture.events())


class OptionalRadioPartsTests(unittest.TestCase):
    def setUp(self):
        self.first, self.pace, self.place = '이번 랩 1분 54초 1입니다.', '최근 페이스보다 1.2초 느렸습니다.', '현재 클래스 3위입니다.'
        self.event = {'id': 'run:1', 'engineer': 'spotter', 'speech_parts': [self.first, self.pace, self.place]}
        self.stored = {self.first, self.place}
        self.synthesize = MagicMock(side_effect=ValueError('No key'))
        def phrase(role, text):
            return 'cached:' + text if text in self.stored else self.synthesize(text, role=role)
        self.coach = VoiceCoach.__new__(VoiceCoach)
        self.coach.custom_packs = SimpleNamespace(selected=None,has_selection=lambda role:False)
        self.coach.cloud_lock = asyncio.Lock()
        self.coach.speech = MagicMock()
        self.coach.fixed_audio = SimpleNamespace(cached=lambda role, text: text in self.stored,
                                                 phrase=MagicMock(side_effect=phrase))
        self.coach.live = SimpleNamespace(lock=threading.RLock(), clock=lambda: 1,
                                          radio=SimpleNamespace(get=lambda *args: self.event))
        app = SimpleNamespace(include_router=lambda router: setattr(app, 'routes', router.routes))
        register_voice_routes(app, self.coach, None, None, None, None)
        self.render = next(r.endpoint for r in app.routes if r.path.endswith('/radio/{identifier}/render'))

    def test_missing_optional_keeps_first_and_later_cached_part(self):
        result = asyncio.run(self.render('run:1'))
        self.assertEqual(result['audio_parts'], ['cached:' + self.first, 'cached:' + self.place])
        self.assertEqual(result['audio_base64'], result['audio_parts'][0])
        self.assertEqual(result['dropped_parts'], 1)

    def test_missing_first_still_fails_even_if_later_parts_cached(self):
        self.stored.remove(self.first)
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(self.render('run:1'))
        self.assertEqual(caught.exception.status_code, 422)

    def test_cached_first_bypasses_question_without_optional_synthesis(self):
        async def during_question():
            async with self.coach.cloud_lock:
                return await asyncio.wait_for(self.render('run:1'), .5)
        result = asyncio.run(during_question())
        self.assertEqual(result['dropped_parts'], 1)
        self.assertEqual(len(result['audio_parts']), 2)
        self.synthesize.assert_not_called()

    def test_cold_first_still_waits_for_question(self):
        self.stored.remove(self.first)
        async def during_question():
            async with self.coach.cloud_lock:
                return await self.render('run:1')
        self.assertEqual(asyncio.run(during_question()), {'busy': True})
        self.synthesize.assert_not_called()

    def test_available_cloud_still_synthesizes_optional_under_lock(self):
        def synthesize(text, role):
            self.assertTrue(self.coach.cloud_lock.locked())
            self.stored.add(text)
            return 'new:' + text
        self.synthesize.side_effect = synthesize
        result = asyncio.run(self.render('run:1'))
        self.assertEqual(result['dropped_parts'], 0)
        self.assertEqual(result['audio_parts'][1], 'new:' + self.pace)
        self.synthesize.assert_called_once()

    def test_all_cached_keeps_order_and_zero_drops(self):
        self.stored.add(self.pace)
        result = asyncio.run(self.render('run:1'))
        self.assertEqual(result['audio_parts'], ['cached:' + p for p in self.event['speech_parts']])
        self.assertEqual(result['dropped_parts'], 0)
        self.synthesize.assert_not_called()

    def test_missing_cached_optional_is_dropped_if_read_itself_fails(self):
        original = self.coach.fixed_audio.phrase.side_effect
        self.coach.fixed_audio.phrase.side_effect = lambda role, text: (_ for _ in ()).throw(OSError('broken clip')) if text == self.place else original(role, text)
        self.assertEqual(asyncio.run(self.render('run:1'))['dropped_parts'], 2)

    def test_audio_on_disk_only_requires_multipart_first(self):
        self.assertTrue(self.coach.audio_on_disk(self.event))
        self.assertFalse(self.coach.audio_on_disk(self.event, optional_parts=False))
        self.stored.remove(self.first)
        self.assertFalse(self.coach.audio_on_disk(self.event))

    def test_stale_event_after_preparation_is_discarded(self):
        calls = iter([self.event, self.event, None])
        self.coach.live.radio.get = lambda *args: next(calls)
        self.assertEqual(asyncio.run(self.render('run:1')), {'event': None})

    def test_dropped_parts_is_validated_integer_and_exported(self):
        payload = dict(event_id='run:1', run_id='run', key='lap_time', status='completed', dropped_parts=1)
        event = RadioDeliveryEvent(**payload).model_dump()
        self.assertEqual(event['dropped_parts'], 1)
        for invalid in (-1, 1.5, '1', True):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                RadioDeliveryEvent(**{**payload, 'dropped_parts': invalid})
        with tempfile.TemporaryDirectory() as folder:
            store = VoiceNoteStore(Path(folder) / 'voice.sqlite3')
            store.record_radio([event])
            self.assertEqual(store.radio_log()[0]['dropped_parts'], 1)
            exported = session_log.export(store, 'run', '2.1.1')
            with zipfile.ZipFile(io.BytesIO(exported[1])) as archive:
                radio = archive.read('radio.jsonl').decode('utf8')
                self.assertEqual(json.loads(radio)['dropped_parts'], 1)
