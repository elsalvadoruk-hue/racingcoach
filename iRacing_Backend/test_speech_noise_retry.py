import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from local_speech import LocalQuestions, SpeechTest
import local_speech_enhancement as noise


class NoiseRetryTests(unittest.IsolatedAsyncioTestCase):
    def test_only_supported_same_subject_questions_can_be_recovered(self):
        self.assertTrue(noise.eligible('뒤차 간겨 갈려 줘.', 'iracing'))
        self.assertEqual(noise.select('뒤차 간겨 갈려 줘.', '뒤차 간격 알려줘', 'iracing')['topic'], 'behind')
        self.assertIsNone(noise.select('뒤차 간겨 갈려 줘.', '앞차 간격 알려줘', 'iracing'))
        for text in ('피트 타이어 교체취', '히트 슬리 타이어 교체', '키트 급유 15리터',
                     '아차 피트 몇 번 했어', '주차의 간격', '뒤차 말고 앞차', '뒤차 그리고 연료',
                     '현재 타이어 압력', '', '엔진 소리', '뒤차 20초', '뒤차 간격'):
            with self.subTest(text=text): self.assertFalse(noise.eligible(text, 'iracing'))

    async def test_failed_question_retries_once_without_changing_original_transcript(self):
        runtime = SimpleNamespace(model='sensevoice', enhancement_ready=True,
                                  recognize=Mock(return_value={'text':'뒤차 간격 알려줘', 'reason':'recognized', 'asr_ms':150}))
        service = LocalQuestions(None, None, runtime)
        payload = SpeechTest(owner='test-owner', audio_base64='test')
        raw = '뒤차 간겨 갈려 줘.'
        result = await service.classify_audio(payload, raw, 'iracing', {})
        self.assertEqual(result['topic'], 'behind')
        self.assertTrue(result['noise_retry']['accepted'])
        self.assertEqual(result['corrections'][0]['from'], raw)
        runtime.recognize.assert_called_once_with('test-owner','test',enhance=True)
        for text in ('뒤차 간격', '피트 타이어 교체취', '연료 얼마나 남았어'):
            await service.classify_audio(payload, text, 'iracing', {})
        runtime.recognize.assert_called_once()

    async def test_missing_model_no_speech_and_failed_retry_keep_original(self):
        runtime = SimpleNamespace(model='sensevoice', enhancement_ready=False, recognize=Mock())
        service = LocalQuestions(None, None, runtime)
        payload = SpeechTest(owner='test-owner', audio_base64='test')
        raw = '뒤차 간겨 갈려 줘.'
        await service.classify_audio(payload, raw, 'iracing', {})
        runtime.enhancement_ready = True
        await service.classify_audio(payload, raw, 'iracing', {'reason':'uncertain_audio'})
        runtime.recognize.assert_not_called()
        runtime.recognize.side_effect = ValueError('stopped')
        result = await service.classify_audio(payload, raw, 'iracing', {})
        self.assertIsNone(result['topic'])
        self.assertFalse(result['noise_retry']['accepted'])
