"""Approved short wording, prepared-only cache reads, and service delivery."""
import base64
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import local_question_audio as audio
import local_speech
from local_question_answers import render
from local_speech import LocalQuestion
from radio_audio import RadioAudioCache
import test_local_question_details as answers
import test_local_speech as speech


def packs(**selections):
    return SimpleNamespace(selections=selections, is_builtin=lambda key: key == 'builtin-short')


class QuestionRecordingTests(unittest.TestCase):
    def test_all_approved_recordings_use_existing_whole_sentence_cache_without_synthesis(self):
        synth = Mock()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'IRACING_RADIO_AUDIO_DIR': folder}):
            cache = RadioAudioCache(synth, folder)
            paths = set()
            for index, (key, role, style, text) in enumerate(audio.fixed_catalog()):
                with self.subTest(key=key, style=style):
                    path = cache.path(role, text)
                    paths.add(path)
                    with self.assertRaises(FileNotFoundError):
                        cache.read_prepared(role, text)
                    data = bytes([index + 1]) * 160
                    path.write_bytes(data)
                    self.assertTrue(cache.cached(role, text))
                    self.assertEqual(base64.b64decode(cache.read_prepared(role, text)), data)
            self.assertEqual(len(paths), 36)
            self.assertEqual({path.parent for path in paths}, {Path(folder)})
        synth.synthesize.assert_not_called()

    def test_fixed_wordings_stay_short_for_all_role_settings_and_details(self):
        selections = (None, packs(), packs(strategy='builtin-short', coach='user-pack'),
                      packs(strategy='user-pack', race='user-pack', coach='user-pack'))
        for selected in selections:
            for detail in ('default', 'explain', 'short', 'short_explain'):
                for key, role, style, text in audio.fixed_catalog():
                    with self.subTest(key=key, style=style, detail=detail, packs=selected):
                        self.assertEqual(audio.select(role, text, selected, detail=detail), audio.wording(key, 'short'))
                self.assertEqual(audio.select('strategy', 'unrelated answer', selected, detail=detail),
                                 ('strategy', 'unrelated answer'))

    def test_fixed_explanations_stay_short_with_mixed_role_settings(self):
        buttons = answers.buttons()
        buttons.coach.custom_packs = packs(race='builtin-short', coach='builtin-short')
        cases = [('defend_time', 'defend_within'), ('corner_loss', 'corner_basis'),
                 ('corner_repeat', 'corner_repeat_all')]
        for topic, key in cases:
            with self.subTest(topic=topic):
                result = render(topic, answers.sample(), buttons, detail='explain')
                self.assertIn(audio.wording(key, 'short')[1], result['spoken'])
                self.assertNotIn(audio.wording(key)[1], result['spoken'])
        fuel = render('fuel_finish', answers.sample(), buttons, detail='explain')
        self.assertIn(audio.wording('fuel_basis', 'short')[1], fuel['spoken'])

    def test_short_explanation_preserves_basis_and_shortens_each_approved_sentence(self):
        cases = [('fuel_finish', 'fuel_basis'), ('fuel_add', 'fuel_reserve'),
                 ('defend_time', 'defend_within'), ('catch_ahead', 'catch_after'),
                 ('corner_loss', 'corner_basis'), ('fuel_save', 'fuel_target_only')]
        for topic, key in cases:
            with self.subTest(topic=topic):
                result = render(topic, answers.sample(), answers.buttons(), detail='short_explain')
                self.assertIn(audio.wording(key, 'short')[1], result['spoken'])
                self.assertNotIn(audio.wording(key)[1], result['spoken'])
                self.assertFalse(result['missing_audio'])

    def test_missing_short_recording_keeps_display_and_never_claims_it_was_spoken(self):
        text = audio.wording('defend_within', 'short')[1]
        def read(item, role, value):
            if value == text:
                raise FileNotFoundError()
            return 'clip'
        result = render('defend_time', answers.sample(), answers.buttons(read), detail='short_explain')
        self.assertIn(text, result['display'])
        self.assertNotIn(text, result['spoken'])
        self.assertTrue(result['missing_audio'])

    def test_short_energy_caveat_is_atomic_with_the_fuel_target(self):
        context = answers.sample()
        context['game'] = 'lmu'
        context['pit_wall']['energy_finish_shortfall_pct'] = 5
        text = audio.wording('fuel_energy_short', 'short')[1]
        def read(item, role, value):
            if value == text:
                raise FileNotFoundError()
            return 'clip'
        result = render('fuel_save', context, answers.buttons(read), detail='short')
        self.assertIn(text, result['display'])
        self.assertFalse(result['spoken'])
        self.assertFalse(result['parts'])


class QuestionRecordingServiceTests(unittest.IsolatedAsyncioTestCase):
    setUp = speech.QuestionTests.setUp

    async def ask(self, text, capture, token=''):
        return await self.service.ask(LocalQuestion(owner='test-owner', capture_id=capture, text=text, dialogue_id=token))

    async def test_confirmations_and_rejection_use_short_strategy_wording_and_log_the_actual_text(self):
        self.coach.custom_packs = packs(strategy='user-pack')
        reads = Mock(return_value='audio')
        self.buttons.read_clip = reads
        for index, (query, key) in enumerate([('얼마나 남았어', 'remaining'), ('간격 얼마야', 'gap'),
                                            ('랩타임', 'lap'), ('이 페이스로 끝까지 갈 수 있어', 'pace')]):
            with self.subTest(key=key):
                first = await self.ask(query, f'question-{index}')
                expected = audio.wording(key, 'short')[1]
                self.assertEqual(first['spoken_text'], expected)
                self.assertEqual(first['dialogue']['prompt'], expected)
                self.assertEqual(first['note']['context']['local_question']['prompt'], expected)
                reads.assert_called_with('question_dialogue', 'strategy', expected)
                rejected = await self.ask('아니', f'rejected-{index}', first['dialogue']['id'])
                self.assertEqual(rejected['spoken_text'], audio.wording('rejected', 'short')[1])
                self.assertIsNone(rejected['dialogue'])

    async def test_missing_prompt_does_not_synthesize_and_still_allows_safe_confirmation(self):
        synth = Mock()
        with patch.dict(os.environ, {'IRACING_RADIO_AUDIO_DIR': self.folder.name}):
            self.coach.fixed_audio = RadioAudioCache(synth, self.folder.name)
        self.buttons.read_clip = lambda item, role, text: self.coach.fixed_audio.read_prepared(role, text)
        with patch('pit_voice_commands.execute') as execute:
            first = await self.ask('얼마나 남았어', 'first')
            self.assertFalse(first['audio_parts'])
            self.assertEqual(first['dialogue']['prompt'], audio.wording('remaining', 'short')[1])
            self.assertEqual(first['note']['context']['local_question']['missing_audio'], ['질문 확인 안내'])
            self.assertEqual(first['note']['context']['local_question']['spoken_text'], '')
            second = await self.ask('응', 'second', first['dialogue']['id'])
            self.assertEqual(second['note']['context']['local_question']['topic'], 'race_time')
            execute.assert_not_called()
        synth.synthesize.assert_not_called()

    async def test_explain_then_short_then_repeat_keeps_short_basis_and_revalidates_it(self):
        self.context['pit_wall'].update(fuel_finish_shortfall_l=2, fuel_next_stop_l=3.25,
                                        fuel_reserve_l=1.25, tank_capacity_l=100, fuel_next_stop_full=False)
        first = await self.ask('완주까지 연료 충분해', 'first')
        explanation = await self.ask('왜 그래', 'explain', first['dialogue']['id'])
        self.assertIn(audio.wording('fuel_basis', 'short')[1], explanation['spoken_text'])
        short = await self.ask('짧게 말해줘', 'short', explanation['dialogue']['id'])
        expected = audio.wording('fuel_basis', 'short')[1]
        self.assertIn(expected, short['spoken_text'])
        self.assertEqual(short['note']['context']['local_question']['answer_detail'], 'short_explain')
        repeated = await self.ask('다시 말해줘', 'repeat', short['dialogue']['id'])
        self.assertIn(expected, repeated['spoken_text'])
        self.assertEqual(repeated['note']['context']['local_question']['answer_detail'], 'short_explain')
        app, router = FastAPI(), APIRouter()
        service = local_speech.register(router, self.coach, self.buttons)
        service._runtime = self.runtime
        app.include_router(router)
        with TestClient(app) as client:
            request = {'owner': 'test-owner', 'capture_id': 'repeat'}
            self.assertTrue(client.post('/local-speech/readback-check', json=request).json()['valid'])
            self.context['pit_wall']['fuel_next_stop_l'] += 10
            self.assertFalse(client.post('/local-speech/readback-check', json=request).json()['valid'])


if __name__ == '__main__':
    unittest.main()
