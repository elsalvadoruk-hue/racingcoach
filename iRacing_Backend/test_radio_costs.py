"""Cost-path regressions: no implicit LLM and no repeated automatic TTS."""
import asyncio
import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import LiveTelemetry
from radio_audio import RadioAudioCache, CALLS
from radio_audio import speech_fields, texts as fixed_texts
from voice_coach import VoiceCoach, Utterance, ReportRequest, register_voice_routes
from voice_context import compact_recent, recent_observations
from voice_notes import VoiceNoteStore
from test_race_engineer import race_fixture


AUDIO = base64.b64encode(b'ID3' + b'x' * 500).decode()


class RadioCostTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        audio_env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        audio_env.start(); self.addCleanup(audio_env.stop)
        self.clock = [1000.]
        self.live = LiveTelemetry(clock=lambda: self.clock[0])
        self.identity = {'track_id': '1', 'car_id': '2'}
        self.sample = {'session_time': 100., 'session_num': 0, 'lap_number': 4, 'distance_pct': .5,
                       'speed_mps': 50., 'fuel_l': 10., 'brake': 0., 'throttle': 1., 'steering_rad': 0.,
                       'on_track': True, 'on_pit_road': False, 'is_replay': False, 'session_state': 4,
                       'session_flags': 0}
        self.race = race_fixture()
        self.live.ingest(self.identity, self.sample, self.race)
        self.live.fuel_history.extend([2, 2, 2])
        self.live.pit_wall.paces = {0: deque([99] * 3), 1: deque([100] * 3), 2: deque([98] * 3)}
        self.speech, self.ai = MagicMock(), MagicMock()
        self.speech.synthesize.return_value = AUDIO
        self.ai.models.generate_content.return_value = SimpleNamespace(text='입력을 부드럽게 바꾸는 연습을 해보세요.',
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name='STOP'))])
        self.store = VoiceNoteStore(self.root / 'notes.sqlite3')
        self.coach = VoiceCoach(self.live, self.store, ai_client=self.ai, speech=self.speech)
        self.live.pit_wall.SAVE_LIMIT_PCT = 40   # these tests speak a shortage that saving could cover

    def advance(self, session_time, **extra):
        self.clock[0] += .1
        self.sample.update(session_time=session_time, **extra)
        self.live.ingest(self.identity, self.sample, self.race)

    def client(self):
        app = FastAPI()
        register_voice_routes(app, self.coach, None, None, None, None)
        return TestClient(app)

    def test_api_default_and_direct_answers_never_call_llm(self):
        with self.client() as client:
            result = client.post('/api/live-coach/utterances', json={'text': '방금 코너에서 왜 앞이 밀렸어?', 'speak': False}).json()
        self.assertEqual(result['note']['reply_source'], 'LOCAL')
        self.assertEqual(result['note']['context']['analysis_mode'], 'measured')
        self.coach.answer('트레일 브레이킹 방법을 설명해줘', self.live.context())
        self.ai.models.generate_content.assert_not_called()
        self.speech.synthesize.assert_not_called()

    def test_explicit_ai_calls_once_but_measured_questions_and_memos_still_do_not(self):
        with self.client() as client:
            result = client.post('/api/live-coach/utterances', json={
                'text': '방금 코너 어땠어?', 'analysis_mode': 'ai', 'speak': False}).json()
            self.assertEqual(result['note']['reply_source'], 'VERTEX_AI')
            result = client.post('/api/live-coach/utterances', json={
                'text': '끝까지 연료 돼?', 'analysis_mode': 'ai', 'speak': False}).json()
            self.assertEqual(result['note']['reply_source'], 'MEASURED')
            client.post('/api/live-coach/utterances', json={'text': '앞이 밀려', 'analysis_mode': 'ai', 'reply': False, 'speak': False})
            self.assertEqual(client.post('/api/live-coach/utterances', json={'text': '설명', 'analysis_mode': 'auto'}).status_code, 422)
        self.ai.models.generate_content.assert_called_once()
        self.speech.synthesize.assert_not_called()

    def test_a_common_measured_question_is_answered_from_prepared_sentences_without_ai(self):
        self.sample.update(class_position=2)
        self.advance(101)
        with self.client() as client:
            result = client.post('/api/live-coach/utterances', json={'text': '지금 몇 위야?', 'speak': True}).json()
        self.assertEqual((result['note']['reply'], result['note']['reply_source']), ('현재 클래스 2위입니다.', 'MEASURED'))
        self.assertEqual(result['audio_base64'], AUDIO)
        # The clip is the fixed sentence in the spotter's voice, cached on disk like any radio call.
        self.assertEqual(self.coach.fixed_audio.phrase('spotter', '현재 클래스 2위입니다.'), AUDIO)
        self.assertEqual(self.speech.synthesize.call_count, 1)
        self.ai.models.generate_content.assert_not_called()

    def test_fuel_shortage_is_spoken_with_its_number_from_one_cached_sentence(self):
        first = asyncio.run(self.coach.report(ReportRequest()))['report']
        self.assertTrue(first['audio_base64'])
        self.assertIn('3.0리터', first['note']['reply'])
        self.assertEqual(first['spoken_text'], '완주까지 3리터 부족합니다.')  # the whole number of litres to find
        self.coach.acknowledge_report(first['note']['id'])
        self.advance(2000, fuel_l=9)   # the same shortage is not repeated within a stint
        second = asyncio.run(self.coach.report(ReportRequest()))['report']
        self.assertIn('4.0리터', second['note']['reply'])
        # Another wording after a delivery, with the new measured number.
        self.assertEqual(second['spoken_text'], '이대로는 4리터 모자랍니다.')
        self.assertEqual(second['note']['context']['speech_source'], 'FIXED_CACHE')
        self.assertEqual(self.speech.synthesize.call_count, 2)
        self.ai.models.generate_content.assert_not_called()
        restarted_speech = MagicMock()
        restarted = VoiceCoach(self.live, self.store, speech=restarted_speech)
        for report in (first, second):
            self.assertEqual(restarted.fixed_audio.phrase('strategy', report['spoken_text']), report['audio_base64'])
        restarted_speech.synthesize.assert_not_called()

    def test_cached_voice_window_keeps_full_report_text_on_screen(self):
        context = self.live.context()
        reports = self.live.pit_wall.candidates(context['pit_wall'], context['snapshot'], context)
        snapshot = {**context['snapshot'], 'speech_window_s': 8}
        report = self.live.pit_wall.select(reports, snapshot, 'important', cached_audio=True)
        self.assertIsNotNone(report)
        self.assertIn('3.0리터', report['text'])
        self.assertEqual((report['key'], report['speech_text']), ('fuel_short', '완주까지 3리터 부족합니다.'))
        self.assertIsNone(self.live.pit_wall.select(reports, {**snapshot, 'brake': .8}, 'important', cached_audio=True))

    def test_preview_cache_preserves_roles_and_survives_restart(self):
        with self.client() as client:
            for role in ('coach', 'race', 'strategy'):
                first = client.post(f'/api/live-coach/voices/{role}/test').json()
                second = client.post(f'/api/live-coach/voices/{role}/test').json()
                self.assertEqual(first['audio_base64'], second['audio_base64'])
        self.assertEqual([call.kwargs['role'] for call in self.speech.synthesize.call_args_list], ['coach', 'race', 'strategy'])
        restarted = RadioAudioCache(self.speech, self.coach.fixed_audio.directory)
        self.speech.synthesize.reset_mock()
        for role in ('coach', 'race', 'strategy'): self.assertEqual(restarted.clip('preview_' + role), AUDIO)
        self.speech.synthesize.assert_not_called()

    def test_radio_number_changes_reuse_cache_without_writing_driver_notes(self):
        with self.client() as client:
            texts = ('4랩, 1분 40초입니다.', '5랩, 1분 39초입니다.')
            spoken = []
            for text in texts * 4:
                self.live.radio.emit('lap_time', self.clock[0], text, cooldown=0)
                event = self.live.radio.view(self.clock[0])['events'][0]
                result = client.post(f'/api/live-coach/spotter/radio/{event["id"]}/render').json()
                self.assertEqual(result['event']['text'], text)
                spoken.append(result['event']['spoken_text'])
                client.post(f'/api/live-coach/spotter/radio/{event["id"]}/ack')
        # The wording rotates through the fixed variants; each is synthesized once however the numbers change.
        self.assertEqual(spoken[0], CALLS['lap_time'][1])
        self.assertEqual(set(spoken), set(fixed_texts('lap_time')))
        self.assertNotEqual(spoken[0], spoken[1])
        self.assertEqual(self.speech.synthesize.call_count, len(fixed_texts('lap_time')))
        self.assertEqual(self.store.list(), [])

    def test_duplicate_local_question_speech_is_reused_only_in_bounded_memory(self):
        for _ in range(2): asyncio.run(self.coach.utterance(Utterance(text='끝까지 연료 돼?')))
        self.speech.synthesize.assert_called_once()
        self.ai.models.generate_content.assert_not_called()
        self.assertFalse(self.coach.fixed_audio.directory.exists())
        for n in range(70): self.coach.synthesize_answer(f'질문 답변 {n}')
        self.assertLessEqual(len(self.coach.radio_audio), 64)
        self.assertFalse(self.coach.fixed_audio.directory.exists())

    def test_text_only_reports_never_prepare_disk_audio(self):
        report = asyncio.run(self.coach.report(ReportRequest(speak=False)))['report']
        self.assertTrue(report['note']['reply'])
        self.speech.synthesize.assert_not_called()
        self.assertFalse(self.coach.fixed_audio.directory.exists())


class StaticAudioCacheTests(unittest.TestCase):
    def setUp(self):
        audio_env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        audio_env.start(); self.addCleanup(audio_env.stop)

    def test_concurrent_identical_calls_synthesize_once_and_voice_changes_invalidate(self):
        with tempfile.TemporaryDirectory() as root:
            speech = MagicMock(); speech.synthesize.return_value = AUDIO
            cache = RadioAudioCache(speech, root)
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(cache.clip, ['fuel_short'] * 8)), [AUDIO] * 8)
            speech.synthesize.assert_called_once()
            with patch.dict('os.environ', {'IRACING_TTS_STRATEGY_VOICE': 'new-voice'}):
                cache.clip('fuel_short')
            self.assertEqual(speech.synthesize.call_count, 2)

    def test_failed_or_short_audio_is_not_cached_and_unknown_phrases_cannot_synthesize(self):
        with tempfile.TemporaryDirectory() as root:
            speech = MagicMock(); cache = RadioAudioCache(speech, root)
            for encoded in ('invalid-', base64.b64encode(b'short').decode()):
                speech.synthesize.return_value = encoded
                with self.assertRaises(ValueError): cache.clip('fuel_short')
                self.assertFalse(list(Path(root).glob('*.mp3')))
            speech.synthesize.return_value = AUDIO
            cache.clip('fuel_short')
            count = speech.synthesize.call_count
            with self.assertRaises(KeyError): cache.clip('private driver statement')
            self.assertEqual(speech.synthesize.call_count, count)


class CompactContextTests(unittest.TestCase):
    def test_compaction_keeps_endpoints_extrema_and_scope_with_far_less_payload(self):
        snapshot = {'session_time': 20., 'session_num': 0, 'run_id': 'run'}
        points = [{'session_time': i * .2, 'session_num': 0, 'run_id': 'run', 'on_track': True,
                   'is_replay': False, 'on_pit_road': False, 'speed_mps': 40., 'brake': .1,
                   'throttle': .9, 'steering_rad': .02, 'gear': 3, 'lap_number': 2,
                   'engine_warnings': 0, 'speech_window_s': 10, 'pit_service_status': 0, 'position': 4}
                  for i in range(100)]
        points[23].update(speed_mps=10., brake=.9, steering_rad=-.8)
        context = {'snapshot': snapshot, 'recent': points, 'connected': True}
        compact = compact_recent(context)
        self.assertLessEqual(len(compact), 24)
        self.assertEqual((compact[0]['session_time'], compact[-1]['session_time']), (0, 19.8))
        self.assertTrue(any(p['brake'] == .9 and p['speed_mps'] == 10 and p['steering_rad'] == -.8 for p in compact))
        self.assertTrue(all('run_id' not in p and 'speech_window_s' not in p for p in compact))
        self.assertLess(len(json.dumps(compact)), len(json.dumps(points)) / 2)
        self.assertEqual(recent_observations(context)['sample_count'], 100)
        self.assertFalse(recent_observations(context)['full_previous_lap_available'])
        self.assertEqual(compact_recent({**context, 'connected': False}), [])
