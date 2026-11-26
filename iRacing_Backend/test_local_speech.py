import asyncio
import base64
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave
import zipfile

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from local_question_intents import EXAMPLES, classify
from local_speech import LocalQuestion, LocalQuestions, register
from local_speech_runtime import LocalSpeechRuntime
from local_speech_worker import decode_audio
from radio_buttons import ButtonRadio, EXAMPLE
from local_question_answers import TOPICS
from voice_notes import VoiceNoteStore


def wav(seconds=1, channels=1, rate=16000):
    data = io.BytesIO()
    with wave.open(data, 'wb') as out:
        out.setnchannels(channels); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(b'\0\0' * int(rate * seconds) * channels)
    return base64.b64encode(data.getvalue()).decode()


class IntentTests(unittest.TestCase):
    def test_practice_fuel_confusions_require_the_whole_fuel_question(self):
        for text in ('열려 얼마나 남았어?', '연류 얼마나 남았어?', '현재 연류가 얼마나 남았어요'):
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result['topic'], 'fuel')
                self.assertEqual(result['correction_status'], 'corrected')
        for text in ('문 열려?', '앞차 연류 얼마나 남았어', '뒤차 열려 얼마나 남았어',
                     '피트 연류 15리터', '연류 얼마나 남았어 말하지 마',
                     '열려 얼마나 남았어 그리고 뒤차 간격', '연류 타이어 압력'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])
                self.assertNotEqual(classify(text)['correction_status'], 'corrected')

    def test_damaged_rival_subject_never_becomes_my_lap_time(self):
        for text in ('아차 최근 랩 타임 알려줘.', '합차 방금 몇 초 찍었어.'):
            self.assertIsNone(classify(text)['topic'])

    def test_recorded_and_benchmark_confusions_use_narrow_context(self):
        cases = {
            '방금 레타임.': ('last', '랩타임'),
            '피트 후 예상 복귀 수.': ('pit_rejoin', '순위'),
            '뒤차간결.': ('behind', '간격'),
            '앞착 간격.': ('ahead', '앞차'),
            '클래스 선도랩 타임.': ('leader_lap', '선두'),
        }
        for text, (topic, corrected) in cases.items():
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result['topic'], topic)
                self.assertEqual(result['correction_status'], 'corrected')
                self.assertIn(corrected, result['normalized_text'])
                self.assertTrue(result['corrections'])

    def test_observed_confusions_do_not_expand_to_unknown_or_mixed_requests(self):
        for text in ['레타임', '레타임 순위', '방금 레타임 알려주지 마',
                     '피트 후 예상 복귀 수리 시간', '피트 후 예상 복귀 수 그리고 연료',
                     '뒤차 간결하게 설명해줘', '뒤차간결 말고 앞차',
                     '앞착 간격 그리고 뒤차', '클래스 선도 랩타임 말고 연료',
                     '뒤차가 랩담 얼마나 좋지?', '공기.', '.', '너.']:
            with self.subTest(text=text):
                result = classify(text)
                self.assertNotEqual(result['correction_status'], 'corrected')
        for text in ['뒤차 간결하게 설명해줘', '피트 후 예상 복귀 수리 시간',
                     '방금 레타임 알려주지 마', '뒤차가 랩담 얼마나 좋지?']:
            self.assertIsNone(classify(text)['topic'])

    def test_lap_time_only_does_not_guess_player_when_subject_was_lost(self):
        self.assertIsNone(classify('랩타임.')['topic'])
        self.assertEqual(classify('방금 랩타임')['topic'], 'last')
        self.assertEqual(classify('앞차 랩타임')['topic'], 'ahead_lap')
        self.assertEqual(classify('선두 최근 랩타임')['topic'], 'leader_lap')

    def test_all_topics_and_examples(self):
        self.assertEqual(set(EXAMPLES), set(TOPICS))
        for topic, samples in EXAMPLES.items():
            for text in samples:
                with self.subTest(text=text):
                    self.assertEqual(classify(text, 'iracing' if topic=='sof' else 'lmu')['topic'], topic)

    def test_natural_variants(self):
        cases = {
            '지금 연료 얼마 남았나요?': 'fuel', '현재 기름으로 얼마나 더 갈 수 있어요': 'fuel_range',
            '한 바퀴에 기름 얼마나 먹음': 'fuel_average', '앞 차 간격 좀 알려 줄래': 'ahead',
            '뒤차가 랩당 얼마나 좁히고 있어요': 'closing_behind', '방금 몇 초 나왔어요': 'last',
            '이번 레이스 남은 바퀴 알려 줘': 'laps', '지금 내 순위 좀 알려줘요': 'position',
            '피트 가면 몇 등이야': 'pit_rejoin', '앞차 최근 랩은 몇 초임': 'ahead_lap',
            '뒤차 피트 몇 번 했나요': 'stops_behind', '앞차 피트 후 몇 랩째': 'stint_ahead',
            '베스트보다 얼마나 느려요': 'sector_delta', '어느 코너에서 제일 많이 잃었어': 'corner_loss',
            '지금 평균 연비는 어때요': 'fuel_average', '현재 에너지 잔량': 'energy_remaining',
        }
        for text, topic in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify(text, 'lmu')['topic'], topic)

    def test_unsupported_and_ambiguous_are_not_arbitrary_answers(self):
        for text in ['완주 연료 목표', '앞차 연료 얼마나 남았어',
                     '뒤차 타이어 상태 비교', '기름 말고 에너지', '알려주지 마', '오늘 날씨',
                     '앞차 뒤차 간격', '연료와 에너지 얼마나 남았어', '뒤차 순위', '연료 절약하려면 어떻게 해',
                     '연료 얼마나 남았어 그리고 순위 알려줘', '랩타임이랑 순위 알려줘',
                     '타이어 공기압 지금 얼마야', '선두 연료 얼마나 남았어', '연료 온도 얼마야']:
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])
        self.assertEqual(classify('에너지 몇 퍼센트')['reason'], 'unsupported_game')

    def test_no_unbounded_text(self):
        self.assertIsNone(classify('연료' * 200)['topic'])

    def test_racing_synonyms_preserve_target_and_quantity(self):
        cases = {
            '뒷차 간격 알려줘': 'behind', '뒤 차량이 얼마나 붙었어': 'behind',
            '후방 차량 피트 몇 번 했어': 'stops_behind', '뒤에 있는 차 스틴트 몇 랩째야': 'stint_behind',
            '앞쪽 차량 최근 랩 타임': 'ahead_lap', '전방 차량 잡을 수 있어': 'catch_ahead',
            '연료 몇 렙 남았어': 'fuel_range', '남은 기름 알려줘': 'fuel',
            '방금 랩 타임': 'last', '뒷차가 랩당 얼마나 좁혀': 'closing_behind',
            '렙당 소비량': 'fuel_average', '뒤차 타이어 몇 렙째야': 'stint_behind',
        }
        for text, topic in cases.items():
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result['topic'], topic)
                self.assertTrue(result['corrections'])
                self.assertEqual(result['correction_status'], 'normalized')
                self.assertEqual(classify(result['normalized_text'])['topic'], topic)

    def test_reported_train_mishearing_needs_supported_racing_context(self):
        cases = {'기차 몇 초 차이야?': 'behind', '지금 기차 간격 알려줘': 'behind',
                 '기차가 랩당 얼마나 좁혀': 'closing_behind', '기차 피트 몇 번 했어': 'stops_behind',
                 '기차 스틴트 몇 랩째야': 'stint_behind', '기차간격 알려줘': 'behind'}
        for text, topic in cases.items():
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result['topic'], topic)
                self.assertEqual(result['correction_status'], 'corrected')
                self.assertIn('뒤차', result['normalized_text'])
                self.assertEqual(result['corrections'][-1]['from'], '기차')
                self.assertEqual(result['corrections'][-1]['kind'], 'context')

    def test_correction_does_not_guess_missing_subject_or_override_negation(self):
        for text in ['기차', '기차 시간 알려줘', '기차 출발 간격 알려줘', '기차역 간격', '전기차 간격',
                     '기차 표 몇 초 뒤에 나와', '기차 랩타임 몇 초야', '기차 연료 얼마나 남았어',
                     '기차 간격 말고 연료', '기차와 앞차 간격', '앞차 기차 피트 몇 번 했어',
                     '뒤차 기차 간격', '기차 간격 그리고 순위', '기차 간격 알려주지 마',
                     '뒷차 연료 얼마나 남았어', '뒷차 랩타임 몇 초야', '앞차 뒷차 간격',
                     '하필이 알려줘', '너무넬료', '연랩 더 달릴 수 있을까?', '기차 타이어 공기압',
                     '기차 시간표 간격', '기차 도착까지 몇 초야', '선두 기차 간격', '기차가방 간격',
                     '기차 순위와 간격', '기차 간격 피트 횟수', '기차 코너 간격', '기차 시간표 알려줘']:
            with self.subTest(text=text):
                result = classify(text)
                self.assertIsNone(result['topic'])
                self.assertNotEqual(result['correction_status'], 'corrected')

    def test_supported_originals_need_no_correction_and_unknowns_are_bounded(self):
        for text in ['뒤차 간격', '연료 얼마 남았어', '방금 랩타임']:
            result = classify(text)
            self.assertEqual(result['normalized_text'], text)
            self.assertEqual(result['corrections'], [])
            self.assertEqual(result['correction_status'], 'none')
        self.assertIsNone(classify('기차 ' * 1000)['topic'])

    def test_reported_short_range_questions_are_not_fuel_quantity(self):
        for text in ['연료 얼마나 가?', '연료, 얼마나 가?', '지금 기름 얼마나 가요',
                     '이 연료로 얼마나 갈까', '기름 얼마나 버텨', '연료로 얼마나 달릴 수 있어']:
            with self.subTest(text=text):
                self.assertEqual(classify(text)['topic'], 'fuel_range')
        for text in ['연료 얼마나 남았어', '연료 얼마나 가지고 있어']:
            self.assertEqual(classify(text)['topic'], 'fuel')
        self.assertIsNone(classify('앞차 연료 얼마나 가')['topic'])


class RuntimeTests(unittest.TestCase):
    def test_routes_register_without_opening_storage_or_recognizer(self):
        app, router = FastAPI(), APIRouter()
        with patch('local_speech.LocalSpeechRuntime') as factory:
            register(router, SimpleNamespace(), None)
            app.include_router(router)
            with TestClient(app) as client:
                self.assertEqual(len(client.get('/local-speech/questions').json()['questions']), len(TOPICS))
                self.assertEqual(client.post('/local-speech/activate', json={'owner': 'x', 'model': 'large'}).status_code, 422)
            factory.assert_not_called()

    def test_failed_process_launch_does_not_keep_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = LocalSpeechRuntime(folder)
            self.addCleanup(runtime.close)
            with patch.object(runtime, 'installed', return_value=True), patch('subprocess.Popen', side_effect=OSError('launch failed')):
                with self.assertRaises(OSError):
                    runtime.activate('test-owner', 'base')
            self.assertIsNone(runtime.owner)
            self.assertFalse(runtime.busy)

    def test_wave_limits_and_format(self):
        self.assertEqual(len(decode_audio(wav())), 32000)
        for bad in [wav(.1), wav(10.1), wav(1, 2), wav(1, rate=8000), 'invalid', 'a' * 480001]:
            with self.subTest(size=len(bad)), self.assertRaises(Exception):
                decode_audio(bad)

    def test_unused_runtime_does_not_import_whisper_or_launch_process(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            runtime = LocalSpeechRuntime(folder)
            self.addCleanup(runtime.close)
            self.assertEqual(runtime.status()['state'], 'off')
            self.assertFalse(runtime.status()['installed']['base'])
            launch.assert_not_called()
            with self.assertRaises(ValueError):
                runtime.activate('test-owner', 'base')

    def test_other_owner_cannot_release_model(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = LocalSpeechRuntime(folder)
            self.addCleanup(runtime.close)
            runtime.owner = 'one-owner'
            runtime.deactivate('other-owner')
            self.assertEqual(runtime.owner, 'one-owner')
            runtime.deactivate('one-owner')
            self.assertIsNone(runtime.owner)

    def test_owner_lease_releases_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = LocalSpeechRuntime(folder)
            runtime.owner = 'owner-one'
            runtime.expires = time.monotonic() - 1
            self.assertEqual(runtime.status()['state'], 'off')
            self.assertIsNone(runtime.owner)
            runtime.close()


class QuestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        consent = patch("local_speech.require_logs", return_value=1000.)
        consent.start(); self.addCleanup(consent.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.context = deepcopy(EXAMPLE)
        self.context.update(connected=True, identity={'car_name': 'test', 'track_name': 'test'})
        self.context['snapshot']['run_id'] = 'run-one'
        self.context['snapshot']['session_time'] = 100
        self.live = SimpleNamespace(lock=threading.RLock(), game='lmu',
            _current_snapshot=lambda: (None, self.context['snapshot']),
            context=lambda **kw: deepcopy(self.context),
            lap_sectors=SimpleNamespace(theoretical=lambda: self.context['theoretical'], delta=lambda: self.context['sector_delta']))
        self.store = VoiceNoteStore(Path(self.folder.name) / 'notes.db')
        self.coach = SimpleNamespace(store=self.store, live=self.live, capture_context=lambda key: deepcopy(self.context))
        self.buttons = ButtonRadio(self.coach)
        self.buttons.read_clip = lambda item, role, text: 'prepared-audio'
        self.runtime = SimpleNamespace(owner='test-owner', model='base', heartbeat=lambda owner: None,
            recognize=lambda owner, audio: {'text': '지금 연료로 얼마나 더 갈 수 있음', 'asr_ms': 120, 'cpu_ms': 90})
        self.service = LocalQuestions(self.coach, self.buttons, self.runtime)

    def payload(self, **kw):
        return LocalQuestion(owner='test-owner', capture_id='capture-one', **kw)

    async def test_recognition_routes_to_prepared_voice_and_persists_details(self):
        reply = await self.service.ask(self.payload(audio_base64=wav()))
        self.assertEqual(reply['note']['text'], '지금 연료로 얼마나 더 갈 수 있음')
        self.assertEqual(reply['note']['reply'], '지금 연료로 약 7.7랩 더 갈 수 있습니다.')
        self.assertEqual(reply['audio_parts'], ['prepared-audio'])
        log = self.store.get('capture-one')['context']['local_question']
        self.assertEqual(log['topic'], 'fuel_range')
        self.assertEqual(log['result'], 'prepared')
        self.assertEqual(log['asr_ms'], 120)
        self.assertFalse(log['audio_saved'])
        self.assertNotIn('audio_base64', str(self.store.get('capture-one')))

    async def test_report_partial_data_is_saved_with_specific_missing_fields(self):
        result = await self.service.ask(self.payload(text='경기 상황 보고'))
        detail = result['note']['context']['local_question']
        self.assertEqual(detail['result'], 'prepared_partial')
        self.assertEqual(detail['missing_data'], ['경기 남은 시간'])
        self.assertIn('경기 남은 시간', result['warning'])
        self.assertEqual(len(result['audio_parts']), 4)

    async def test_partial_report_distinguishes_display_from_actually_spoken_words(self):
        self.context['snapshot']['session_time_remaining_s'] = 307
        def read(item, role, text):
            if item == 'race_time':
                raise FileNotFoundError()
            return 'clip'
        self.buttons.read_clip = read
        result = await self.service.ask(self.payload(text='경기 상황 보고'))
        self.assertIn('5분', result['note']['reply'])
        self.assertNotIn('5분', result['spoken_text'])
        self.assertEqual(result['note']['context']['local_question']['missing_audio'], ['경기 남은 시간'])

    async def test_new_answer_reads_current_measurement_and_cancels_when_owner_changes(self):
        self.context['pit_wall']['fuel_next_stop_l'] = 5.2
        def read(*args):
            self.runtime.owner = None
            return 'clip'
        self.buttons.read_clip = read
        result = await self.service.ask(self.payload(text='추가 급유량 알려줘'))
        self.assertEqual(result['note']['context']['local_question']['result'], 'stale')
        self.assertFalse(result['audio_parts'])
        self.assertFalse(result['note']['reply'])

    async def test_unavailable_audio_text_is_also_cleared_on_session_change(self):
        self.context['pit_wall']['fuel_next_stop_l'] = 5.2
        def read(*args):
            self.context['snapshot']['run_id'] = 'new-run'
            raise FileNotFoundError()
        self.buttons.read_clip = read
        result = await self.service.ask(self.payload(text='추가 급유량 알려줘'))
        self.assertEqual(result['note']['context']['local_question']['result'], 'stale')
        self.assertFalse(result['note']['reply'])

    async def test_rival_sections_are_computed_only_for_the_requested_live_target(self):
        from unittest.mock import Mock
        self.context['pit_wall']['behind']['idx'] = 8
        compare = Mock(return_value=[{'middle_pct':.1, 'sector':0, 'delta_s':-.5}])
        self.live.rival_sectors = SimpleNamespace(compare=compare)
        self.live.pit_wall = SimpleNamespace(race={'player_idx':3})
        result = await self.service.ask(self.payload(text='뒤차보다 어디서 빨라'))
        compare.assert_called_once_with(3, 8)
        self.assertIn('뒤차보다 빠른 곳', result['note']['reply'])
        self.assertEqual(len(result['audio_parts']), 2)

    async def test_self_subject_transcript_reaches_ahead_comparison_and_keeps_original_log(self):
        from unittest.mock import Mock
        text='앞차보다 내가 어디서 빨라?'
        self.context['pit_wall']['ahead']['idx']=5
        compare=Mock(return_value=[{'middle_pct':.1,'sector':0,'delta_s':-.5}])
        self.live.rival_sectors=SimpleNamespace(compare=compare)
        self.live.pit_wall=SimpleNamespace(race={'player_idx':3})
        self.runtime.recognize=lambda *args:{'text':text,'asr_ms':120}
        result=await self.service.ask(self.payload(audio_base64=wav()))
        compare.assert_called_once_with(3,5)
        self.assertIn('앞차보다 빠른 곳',result['note']['reply'])
        self.assertEqual(len(result['audio_parts']),2)
        note=self.store.get('capture-one')
        self.assertEqual(note['text'],text)
        self.assertEqual(note['context']['local_question']['topic'],'ahead_gain')
        self.assertEqual(note['context']['local_question']['classifier_version'],8)

    async def test_incident_limit_comes_from_current_session_metadata(self):
        self.live.radio = SimpleNamespace(metadata={'incident_limit':17})
        self.context['snapshot']['incidents'] = 13
        result = await self.service.ask(self.payload(text='인시던트 몇 점이야'))
        self.assertIn('13점', result['note']['reply'])
        self.assertIn('4점', result['note']['reply'])

    async def test_microphone_test_without_game_saves_diagnostics(self):
        app, router = FastAPI(), APIRouter()
        service = register(router, self.coach, self.buttons)
        service._runtime = self.runtime
        app.include_router(router)
        self.context['connected'] = False
        with TestClient(app) as client:
            response = client.post('/local-speech/test', json={'owner':'test-owner', 'audio_base64':wav(), 'game':'lmu'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['topic'], 'fuel_range')
        self.assertTrue(response.json()['saved'])
        self.assertEqual(len(self.store.list()), 1)

    async def test_question_started_without_car_reports_connection_not_session_change(self):
        self.context.update(connected=False, game=None, snapshot=None)
        result = await self.service.ask(self.payload(text='연료 얼마나 가?'))
        log = result['note']['context']['local_question']
        self.assertEqual(log['topic'], 'fuel_range')
        self.assertEqual(log['result'], 'not_connected')
        self.assertIn('차량 연결', result['warning'])
        self.assertIn('인식 테스트', result['warning'])
        self.assertFalse(result['audio_parts'])

    async def test_repeat_capture_never_replays_or_retranscribes(self):
        await self.service.ask(self.payload(text='연료 얼마나 남았어'))
        self.runtime.recognize = lambda *args: self.fail('duplicate recognition')
        result = await self.service.ask(self.payload(audio_base64=wav()))
        self.assertTrue(result['reused']); self.assertIsNone(result['audio_base64'])
        self.assertEqual(len(self.store.list()), 1)

    async def test_corrected_question_retains_original_and_correction_in_saved_note(self):
        raw = '기차 몇 초 차이야?'
        self.runtime.recognize = lambda *a: {'text': raw, 'asr_ms': 110}
        result = await self.service.ask(self.payload(audio_base64=wav()))
        self.assertEqual(result['note']['text'], raw)
        detail = result['note']['context']['local_question']
        self.assertEqual(detail['topic'], 'behind')
        self.assertEqual(detail['normalized_text'], '뒤차 몇 초 차이야?')
        self.assertEqual(detail['correction_status'], 'corrected')
        self.assertTrue(result['audio_parts'])
        saved = self.store.get('capture-one')
        self.assertEqual(saved['text'], raw)
        self.assertEqual(saved['context']['local_question']['corrections'], detail['corrections'])
        self.assertNotIn('audio_base64', str(saved))

    async def test_uncertain_correction_never_renders_or_answers_with_other_facts(self):
        with patch.object(self.buttons, 'render', side_effect=AssertionError('must not render')):
            result = await self.service.ask(self.payload(text='기차 연료 얼마나 남았어'))
        self.assertEqual(result['note']['context']['local_question']['correction_status'], 'withheld')
        self.assertFalse(result['audio_parts'])
        self.assertFalse(result['note']['reply'])

    async def test_offline_test_returns_and_saves_raw_and_corrected_text(self):
        app, router = FastAPI(), APIRouter()
        service = register(router, self.coach, self.buttons)
        self.runtime.recognize = lambda *a: {'text': '기차 간격 알려줘', 'reason': 'recognized'}
        service._runtime = self.runtime
        app.include_router(router)
        with TestClient(app) as client:
            result = client.post('/local-speech/test', json={'owner': 'test-owner', 'audio_base64': wav()}).json()
        self.assertEqual(result['text'], '기차 간격 알려줘')
        self.assertEqual(result['normalized_text'], '뒤차 간격 알려줘')
        self.assertEqual(result['topic'], 'behind')
        self.assertEqual(result['corrections'][0]['kind'], 'context')
        self.assertEqual(len(self.store.list()), 1)

    async def test_corrected_text_export_requires_question_opt_in(self):
        import session_log
        await self.service.ask(self.payload(text='기차 간격 알려줘'))
        self.store.record_radio([{'event_id': 'capture-one', 'run_id': 'run-one', 'key': 'answer',
                                 'status': 'completed', 'text': '준비된 뒤차 답변'}])
        for include in (False, True):
            _, data = session_log.export(self.store, 'run-one', 'test', include_questions=include)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                reports = archive.read('reports.jsonl').decode()
            self.assertEqual('기차 간격 알려줘' in reports, include)
            self.assertEqual('뒤차 간격 알려줘' in reports, include)
            self.assertEqual('correction_status' in reports, include)

    async def test_unknown_questions_are_logged_without_audio(self):
        result = await self.service.ask(self.payload(text='앞차 타이어랑 비교해줘'))
        self.assertFalse(result['audio_parts'])
        self.assertEqual(result['note']['context']['local_question']['result'], 'unsupported')
        self.assertEqual(result['note']['text'], '앞차 타이어랑 비교해줘')

    async def test_no_speech_and_inference_error_are_logged(self):
        self.runtime.recognize = lambda *a: {'text': '', 'reason': 'no_speech'}
        result = await self.service.ask(self.payload(audio_base64=wav()))
        self.assertEqual(result['note']['context']['local_question']['result'], 'no_speech')
        def fail(*a): raise ValueError('인식 시간 초과')
        self.runtime.recognize = fail
        result = await self.service.ask(LocalQuestion(owner='test-owner', capture_id='two', audio_base64=wav()))
        self.assertEqual(result['note']['context']['local_question']['result'], 'recognition_failed')

    async def test_data_and_missing_audio_have_distinct_results(self):
        self.context['pit_wall']['fuel_laps'] = None
        result = await self.service.ask(self.payload(text='연료 몇 랩 남았어'))
        self.assertEqual(result['note']['context']['local_question']['result'], 'data_unavailable')
        def missing(*a): raise FileNotFoundError()
        self.buttons.read_clip = missing
        result = await self.service.ask(LocalQuestion(owner='test-owner', capture_id='two', text='연료 얼마나 남았어'))
        self.assertEqual(result['note']['context']['local_question']['result'], 'audio_unavailable')
        self.assertFalse(result['audio_parts']); self.assertTrue(result['note']['reply'])

    async def test_session_change_during_recognition_suppresses_old_answer(self):
        def recognize(*args):
            self.context['snapshot']['run_id'] = 'new-run'
            return {'text': '연료 얼마나 남았어'}
        self.runtime.recognize = recognize
        result = await self.service.ask(self.payload(audio_base64=wav()))
        self.assertEqual(result['note']['context']['snapshot']['run_id'], 'run-one')
        self.assertEqual(result['note']['context']['local_question']['result'], 'stale')
        self.assertFalse(result['audio_parts'])

    async def test_microphone_off_during_render_suppresses_answer(self):
        original = self.buttons.render
        def render(*args, **kw):
            self.runtime.owner = None
            return original(*args, **kw)
        self.buttons.render = render
        result = await self.service.ask(self.payload(text='연료 얼마나 남았어'))
        self.assertFalse(result['audio_parts'])
        self.assertEqual(result['note']['context']['local_question']['result'], 'stale')

    async def test_inference_does_not_block_event_loop_and_no_queue_accumulates(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*a):
            entered.set(); release.wait(2); return {'text': '앞차 간격'}
        self.runtime.recognize = slow
        task = asyncio.create_task(self.service.ask(self.payload(audio_base64=wav())))
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(.01)
            self.assertTrue(entered.is_set())
            with self.assertRaises(HTTPException) as raised:
                await self.service.ask(self.payload(text='뒤차 간격'))
            self.assertEqual(raised.exception.status_code, 409)
        finally:
            release.set(); await task

    async def test_muted_question_still_records_text(self):
        result = await self.service.ask(self.payload(text='지금 몇 등이야', speak=False))
        self.assertFalse(result['audio_parts'])
        self.assertEqual(result['note']['context']['local_question']['result'], 'text_only')

    async def test_session_export_only_includes_local_question_with_explicit_option(self):
        import session_log
        await self.service.ask(self.payload(text='연료 얼마나 남았어'))
        event = {'event_id': 'capture-one', 'run_id': 'run-one', 'key': 'answer',
                 'status': 'completed', 'text': '지금 연료는 24리터입니다.'}
        self.store.record_radio([event])
        for included in (False, True):
            _, data = session_log.export(self.store, 'run-one', 'test', include_questions=included)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                reports = archive.read('reports.jsonl').decode()
            self.assertEqual('연료 얼마나 남았어' in reports, included)
            self.assertEqual('local_question' in reports, included)


if __name__ == '__main__':
    unittest.main()
