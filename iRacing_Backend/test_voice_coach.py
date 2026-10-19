"""No microphone, live game, real credentials or billable requests in these tests."""
import asyncio
from dataclasses import replace
import base64
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import wave

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import numpy as np

from test_analysis_reliability import api
import test_partial_coaching as partial_fixture
from live_telemetry import CHANNELS, LiveTelemetry, live_facts
from voice_notes import VoiceNoteStore, file_identity, matching_context, review_notes, anchor_corner, recording_key
from voice_coach import GoogleSpeech, Utterance, VoiceCoach, register_voice_routes, speech_region, service_error
from voice_context import recent_conversation, recent_observations


class VoiceCoachTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.store = VoiceNoteStore(self.root / 'notes.sqlite3')
        self.now = [1000.0]
        self.live = LiveTelemetry(clock=lambda: self.now[0])
        self.identity = {'session_id': '12', 'subsession_id': '99', 'track_id': '1', 'car_id': '2',
                         'track_path': 'test-track', 'car_path': 'test-car', 'track_config': '', 'car_name': 'Test', 'track_name': 'Test'}
        self.sample = {'session_time': 125., 'session_num': 0, 'lap_number': 2, 'distance_pct': .25,
                       'speed_mps': 23., 'steering_rad': .5, 'brake': .5, 'throttle': 0., 'fuel_l': 20.,
                       'on_track': True, 'is_replay': False, 'tc_setting': None}
        self.live.ingest(self.identity, self.sample)
        self.speech = MagicMock()
        self.speech.status.return_value = {'enabled': True}
        self.speech.recognize.return_value = '이 코너에서 앞이 밀려서 어려워'
        self.speech.synthesize.return_value = 'fixture-audio'
        self.coach = VoiceCoach(self.live, self.store, speech=self.speech)

    def telemetry(self):
        telemetry = partial_fixture.PartialCoachingTests.telemetry(self)
        info = {'WeekendInfo': {'SessionID': 12, 'SubSessionID': 99, 'TrackID': 1, 'TrackName': 'test-track', 'TrackConfigName': ''},
                'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'CarID': 2, 'CarPath': 'test-car'}]}}
        return replace(telemetry, session_info=info)

    def test_stale_telemetry_never_supplies_a_current_position(self):
        self.now[0] += 3
        context = self.live.context()
        self.assertFalse(context['connected'])
        self.assertIsNone(context['snapshot'])
        self.assertEqual(context['recent'], [])

    def test_clock_reset_and_new_session_drop_the_previous_buffer(self):
        self.live.ingest(self.identity, {**self.sample, 'session_time': 1., 'lap_number': 0})
        self.assertEqual(len(self.live.samples), 1)
        self.assertEqual(self.live.context()['snapshot']['session_time'], 1.)
        self.live.ingest({**self.identity, 'subsession_id': '100'}, {**self.sample, 'session_time': 2.})
        self.assertEqual(len(self.live.samples), 1)

    def test_capture_freezes_the_position_before_recognition_and_response(self):
        capture = self.coach.capture()
        self.live.ingest(self.identity, {**self.sample, 'session_time': 130., 'distance_pct': .30})
        result = asyncio.run(self.coach.utterance(Utterance(capture_id=capture['capture_id'], audio_base64='fixture', reply=False, speak=False)))
        self.assertEqual(result['note']['context']['snapshot']['distance_pct'], .25)
        self.speech.synthesize.assert_not_called()
        persisted = VoiceNoteStore(self.store.path).get(result['note']['id'])
        self.assertIn('UNDERSTEER', persisted['feelings'])
        self.assertEqual(persisted['original_text'], self.speech.recognize.return_value)

    def test_tts_failure_retains_the_statement_and_answer_and_is_idempotent(self):
        self.speech.synthesize.side_effect = RuntimeError('network down')
        capture = self.coach.capture()
        payload = Utterance(capture_id=capture['capture_id'], text='연료 얼마야?')
        result = asyncio.run(self.coach.utterance(payload))
        self.assertIn('20.0', result['note']['reply'])
        self.assertTrue(result['warning'])
        again = asyncio.run(self.coach.utterance(payload))
        self.assertTrue(again['reused'])
        self.assertEqual(len(self.store.list()), 1)
        self.speech.synthesize.assert_called_once()

    def test_unrecognized_audio_never_creates_a_fake_statement(self):
        self.speech.recognize.side_effect = ValueError('음성 인식 실패')
        with self.assertRaises(HTTPException):
            asyncio.run(self.coach.utterance(Utterance(audio_base64='fixture')))
        self.assertEqual(self.store.list(), [])

    def ai_response(self, text, reason='STOP'):
        return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=reason))])

    def test_completed_ai_answer_is_saved_and_spoken_without_character_slicing(self):
        answer = ('코너 진입 속도와 브레이크 해제 시점을 확인하세요.\n' * 45).strip()
        self.assertGreater(len(answer), 1000)
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = self.ai_response(answer)
        result = asyncio.run(self.coach.utterance(Utterance(text='방금 코너 어땠어?', analysis_mode='ai')))
        self.assertEqual(result['note']['reply'], answer)
        self.assertEqual(self.store.get(result['note']['id'])['reply'], answer)
        self.speech.synthesize.assert_called_once_with(answer)
        config = self.coach.ai_client.models.generate_content.call_args.kwargs['config']
        self.assertEqual(config['thinking_config']['thinking_budget'], 0)

    def test_token_exhaustion_retries_before_saving_or_speaking_the_answer(self):
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.side_effect = [
            self.ai_response('브레이크를 늦게 떼', 'MAX_TOKENS'),
            self.ai_response('브레이크를 부드럽게 놓는 연습을 해보세요.'),
        ]
        result = asyncio.run(self.coach.utterance(Utterance(text='방금 코너 어땠어?', analysis_mode='ai')))
        self.assertEqual(result['note']['reply'], '브레이크를 부드럽게 놓는 연습을 해보세요.')
        self.assertEqual(result['note']['reply_source'], 'VERTEX_AI')
        self.assertFalse(result['warning'])
        self.speech.synthesize.assert_called_once_with(result['note']['reply'])
        calls = self.coach.ai_client.models.generate_content.call_args_list
        self.assertEqual([call.kwargs['config']['max_output_tokens'] for call in calls], [1024, 2048])

    def test_repeated_incomplete_answer_keeps_question_but_never_saves_the_fragment(self):
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = self.ai_response('브레이크를 늦게 떼', 'MAX_TOKENS')
        result = asyncio.run(self.coach.utterance(Utterance(text='방금 코너 어땠어?', speak=False, analysis_mode='ai')))
        self.assertEqual(result['note']['text'], '방금 코너 어땠어?')
        self.assertNotIn('브레이크를 늦게 떼', result['note']['reply'])
        self.assertEqual(result['note']['reply_source'], 'LOCAL')
        self.assertIn('길이 제한', result['warning'])
        self.assertEqual(self.coach.ai_client.models.generate_content.call_count, 2)

    def test_abnormally_finished_answer_never_saves_partial_advice(self):
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = self.ai_response('불완전한 조언', 'SAFETY')
        result = asyncio.run(self.coach.utterance(Utterance(text='방금 코너 어땠어?', speak=False, analysis_mode='ai')))
        self.assertTrue(result['warning'])
        self.assertNotIn('불완전한 조언', result['note']['reply'])
        self.assertEqual(self.coach.ai_client.models.generate_content.call_count, 1)

    def test_followup_uses_same_run_history_without_repeating_the_current_question(self):
        context = self.live.context()
        earlier = self.store.save('진입에서 앞이 밀려', context)
        self.store.update(earlier['id'], reply='브레이크를 놓는 과정부터 확인해보세요.', reply_source='VERTEX_AI')
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = self.ai_response('페달을 조금씩 놓으면서 핸들을 돌리는 연습을 해보세요.')
        result = asyncio.run(self.coach.utterance(Utterance(text='그건 어떻게 해? 더 자세히 설명해줘', speak=False, analysis_mode='ai')))
        payload = json.loads(self.coach.ai_client.models.generate_content.call_args.kwargs['contents'])
        self.assertEqual(len(payload['conversation_history']), 1)
        self.assertEqual(payload['conversation_history'][0]['driver_statement'], earlier['text'])
        self.assertNotIn(result['note']['text'], [item['driver_statement'] for item in payload['conversation_history']])
        self.assertFalse(payload['recent_observations']['full_previous_lap_available'])

    def test_history_never_crosses_driving_runs_or_uses_future_and_old_notes(self):
        context = self.live.context()
        for changed in ({'session_time': -100}, {'session_time': 130}, {'run_id': 'other-run'}):
            previous = {**context, 'snapshot': {**context['snapshot'], **changed}}
            self.store.save('다른 위치 메모', previous)
        other_car = {**context, 'identity': {**context['identity'], 'car_id': '3'}}
        self.store.save('다른 차량 메모', other_car)
        for index in range(5):
            self.store.save(f'현재 주행 메모 {index}', context)
        history = recent_conversation(self.store, context)
        self.assertEqual(len(history), 3)
        self.assertTrue(all(item['driver_statement'].startswith('현재 주행 메모') for item in history))
        self.assertEqual(recent_conversation(self.store, {**context, 'connected': False}), [])

    def test_recent_summary_excludes_old_future_replay_pit_and_other_run_samples(self):
        context = self.live.context()
        good = context['snapshot']
        context['recent'] = [good, {**good, 'session_time': 124, 'speed_mps': 30, 'brake': .8}]
        for changed in ({'session_time': 100}, {'session_time': 130}, {'is_replay': True},
                        {'on_pit_road': True}, {'on_track': False}, {'run_id': 'other'}, {'session_num': 2}):
            context['recent'].append({**good, 'speed_mps': 999, 'brake': 1, **changed})
        observed = recent_observations(context)
        self.assertEqual(observed['sample_count'], 2)
        self.assertEqual(observed['observed_seconds'], 1)
        self.assertEqual(observed['max_speed_kph'], 108)
        self.assertEqual(observed['peak_brake_pct'], 80)
        self.assertFalse(observed['reference_lap_available'])
        self.assertEqual(recent_observations({**context, 'connected': False})['sample_count'], 0)

    def test_general_concept_can_be_explained_offline_without_stale_measurements(self):
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = self.ai_response('트레일 브레이킹은 코너에 들어가면서 브레이크를 점차 놓는 방법입니다.')
        context = {**self.live.context(), 'connected': False}
        _, source = self.coach.answer('트레일 브레이킹 원리와 방법을 설명해줘', context, allow_ai=True)
        self.assertEqual(source, 'VERTEX_AI')
        payload = json.loads(self.coach.ai_client.models.generate_content.call_args.kwargs['contents'])
        self.assertIsNone(payload['facts']['fuel_l'])
        self.assertEqual(payload['recent'], [])
        self.coach.ai_client.models.generate_content.reset_mock()
        _, source = self.coach.answer('방금 코너에서 왜 앞이 밀렸어?', context)
        self.assertEqual(source, 'LOCAL')
        self.coach.ai_client.models.generate_content.assert_not_called()

    def test_fuel_range_without_the_word_fuel_still_uses_measured_consumption(self):
        self.coach.ai_client = MagicMock()
        context = {**self.live.context(), 'fuel_l_per_lap': 1.5, 'fuel_observed_laps': 2}
        answer, source = self.coach.answer('몇 바퀴쯤 더 돌 수 있겠노?', context)
        self.assertEqual(source, 'MEASURED')
        self.assertIn('13랩', answer)
        self.coach.ai_client.models.generate_content.assert_not_called()
        context['fuel_l_per_lap'] = None
        answer, source = self.coach.answer('몇 바퀴쯤 더 돌 수 있겠노?', context)
        self.assertEqual(source, 'LOCAL')
        self.assertIn('계산할 수 없습니다', answer)

    def test_practice_lap_count_is_not_mistaken_for_remaining_fuel_range(self):
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = self.ai_response('한 번에 같은 입력 하나를 연습하고 체감 변화를 확인하세요.')
        _, source = self.coach.answer('몇 바퀴 더 연습하면 돼?', self.live.context(), allow_ai=True)
        self.assertEqual(source, 'VERTEX_AI')

    def test_previous_lap_review_explains_scope_without_inventing_full_lap_evidence(self):
        self.coach.ai_client = MagicMock()
        answer, source = self.coach.answer('이전 랩에서 개선해야 할 것을 알려줘', self.live.context())
        self.assertEqual(source, 'LOCAL')
        self.assertIn('최근 약 20초', answer)
        self.assertIn('주행 후 IBT', answer)
        self.coach.ai_client.models.generate_content.assert_not_called()

    def test_matching_requires_the_same_session_car_and_layout(self):
        note = self.store.save('어려워', self.live.context())
        telemetry = self.telemetry()
        self.assertIsNotNone(matching_context(note, telemetry))
        info = json.loads(json.dumps(telemetry.session_info)); info['WeekendInfo']['SubSessionID'] = 100
        self.assertIsNone(matching_context(note, replace(telemetry, session_info=info)))
        info['WeekendInfo']['SubSessionID'] = 99; info['WeekendInfo']['TrackConfigName'] = 'other-layout'
        self.assertIsNone(matching_context(note, replace(telemetry, session_info=info)))

    def test_missing_speech_sample_does_not_link_an_earlier_file_fragment(self):
        context = self.live.context()
        context['recent'] = [self.sample]
        context['snapshot'] = {**self.sample, 'session_time': 150., 'distance_pct': .50}
        note = self.store.save('어려워', context)
        self.assertIsNone(matching_context(note, self.telemetry()))

    def test_after_corner_comment_uses_the_recent_turn_but_manual_anchor_is_kept(self):
        corners = [{'id': 'C1', 'entry_pct': .20, 'exit_pct': .28}]
        note = {'text': '방금 코너 어렵네', 'context': {'snapshot': {**self.sample, 'session_time': 130}, 'recent': [self.sample]}}
        match = {'distance_pct': .30, 'lap_number': 2, 'method': 'SESSION_AND_SAMPLES'}
        corner, pct, source = anchor_corner(note, match, corners)
        self.assertEqual((corner['id'], pct, source), ('C1', .25, 'RECENT_CORNER'))
        corner, pct, source = anchor_corner(note, {**match, 'method': 'MANUAL'}, corners)
        self.assertEqual((corner, pct, source), (None, .30, 'MANUAL'))

    def test_offline_review_measures_the_spoken_lap_and_keeps_effect_unconfirmed(self):
        telemetry = self.telemetry()
        self.store.save('이 코너가 어렵네', self.live.context())
        analyzer = api.TelemetryAnalyzer(telemetry)
        request = api.AnalyzeRequest()
        result = analyzer.analyze(request)
        review = review_notes(self.store, analyzer, result, request)
        self.assertEqual(review['linked_count'], 1)
        self.assertEqual(review['notes'][0]['lap_number'], 2)
        self.assertTrue(review['notes'][0]['corner_id'])
        self.assertFalse(review['notes'][0]['setup_effect_confirmed'])
        self.assertFalse(review['audio_stored'])
        self.assertEqual(review['notes'][0]['setup_candidates'], [])

    def test_manual_edit_persists_original_text_and_rejects_other_vehicle(self):
        note = self.store.save('인식 오류', self.live.context())
        app = FastAPI()
        telemetry = self.telemetry()
        repository = MagicMock(); repository.load.return_value = telemetry
        register_voice_routes(app, self.coach, lambda name: telemetry.path, repository, api.TelemetryAnalyzer, api.AnalyzeRequest)
        client = TestClient(app)
        response = client.patch(f"/api/live-coach/notes/{note['id']}", json={'text': '앞이 밀림', 'filename': telemetry.path.name, 'lap_number': 2, 'distance_pct': .25})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get(note['id'])['original_text'], '인식 오류')
        self.assertEqual(self.store.links(telemetry.path.name)[note['id']]['method'], 'MANUAL')
        info = json.loads(json.dumps(telemetry.session_info)); info['DriverInfo']['Drivers'][0]['CarID'] = 3
        repository.load.return_value = replace(telemetry, session_info=info)
        response = client.patch(f"/api/live-coach/notes/{note['id']}", json={'filename': telemetry.path.name, 'lap_number': 2, 'distance_pct': .25})
        self.assertEqual(response.status_code, 422)

    def test_local_practice_requires_multiple_samples_and_new_file_time(self):
        telemetry = self.telemetry()
        telemetry.session_info['WeekendInfo'].update(SessionID=0, SubSessionID=0)
        identity = {**self.identity, 'session_id': '', 'subsession_id': ''}
        samples = [{**self.sample, 'session_time': 125 + i * .2, 'distance_pct': .25 + i * .002,
                    'speed_mps': float(telemetry.channels['Speed'][1500 + i * 12])} for i in range(6)]
        note = self.store.save('어려워', {'identity': identity, 'recent': samples, 'snapshot': samples[-1]})
        os.utime(telemetry.path, (note['created_at'] + 30, note['created_at'] + 30))
        self.assertIsNotNone(matching_context(note, telemetry))
        os.utime(telemetry.path, (note['created_at'] - 30, note['created_at'] - 30))
        self.assertIsNone(matching_context(note, telemetry))

    def test_speech_config_uses_korean_and_vocabulary_with_valid_wav(self):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0\0' * 8000)
        client = MagicMock()
        client.recognize.return_value.results = [MagicMock(alternatives=[MagicMock(transcript='앞이 밀려')])]
        with patch.dict(os.environ, {'IRACING_AI_ENABLED': '1', 'IRACING_SPEECH_REGION': ''}), patch('google.cloud.speech_v2.SpeechClient', return_value=client) as factory:
            text = GoogleSpeech().recognize(base64.b64encode(stream.getvalue()).decode())
        self.assertEqual(text, '앞이 밀려')
        request = client.recognize.call_args.kwargs['request']
        self.assertEqual(list(request.config.language_codes), ['ko-KR'])
        self.assertEqual(request.config.model, 'chirp_3')
        self.assertTrue(request.config.adaptation.phrase_sets)
        self.assertIn('/locations/us/', request.recognizer)
        self.assertEqual(factory.call_args.kwargs['client_options'].api_endpoint, 'us-speech.googleapis.com')

    def test_supported_default_region_preserves_an_explicit_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(speech_region(), 'us')
            self.assertEqual(GoogleSpeech().status()['region'], 'us')
        with patch.dict(os.environ, {'IRACING_SPEECH_REGION': 'eu'}):
            self.assertEqual(speech_region(), 'eu')

    def test_model_region_denial_is_explained_separately_from_iam_and_not_saved(self):
        from google.api_core.exceptions import PermissionDenied
        error = PermissionDenied('Permission denied on model chirp_3 locale ko-KR. It is no longer generally available in region asia-northeast1.')
        self.assertIn('us 또는 eu', service_error(error))
        self.assertNotIn('리전', service_error(PermissionDenied('speech.recognizers.recognize denied')))
        self.speech.recognize.side_effect = error
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(self.coach.utterance(Utterance(audio_base64='fixture')))
        self.assertIn('저장되지 않았습니다', caught.exception.detail)
        self.assertEqual(self.store.list(), [])

    def test_valid_lap_review_uses_its_own_measurements_with_no_external_reference(self):
        telemetry = self.telemetry()
        lap = replace(telemetry.laps[0], valid=True, status='VALID', lap_time_s=100.)
        telemetry = replace(telemetry, laps=[lap])
        telemetry.channels['PlayerTrackSurface'][:] = 3
        note = self.store.save('앞이 밀려서 어려워', self.live.context())
        analyzer = api.TelemetryAnalyzer(telemetry); request = api.AnalyzeRequest()
        result = analyzer.analyze(request)
        review = review_notes(self.store, analyzer, result, request)
        self.assertEqual(review['notes'][0]['id'], note['id'])
        self.assertEqual(review['notes'][0]['lap_number'], lap.lap_number)
        self.assertFalse(review['notes'][0]['setup_effect_confirmed'])
        self.assertTrue(all(candidate['confidence'] <= .4 for candidate in review['notes'][0]['setup_candidates']))

    def test_nonfinite_live_channels_are_saved_as_missing_instead_of_breaking_the_note(self):
        self.live.ingest(self.identity, {**self.sample, 'session_time': 126., 'fuel_l': float('nan')})
        result = asyncio.run(self.coach.utterance(Utterance(text='느낌 기록', reply=False)))
        self.assertIsNone(result['note']['context']['snapshot']['fuel_l'])

    def test_fuel_estimate_requires_a_complete_clean_lap_and_refueling_resets_evidence(self):
        for i in range(51):
            self.live.ingest(self.identity, {**self.sample, 'session_time': i, 'lap_number': 3,
                'distance_pct': i * .0198, 'fuel_l': 20 - i * .0792, 'on_pit_road': False, 'incidents': 0})
        self.live.ingest(self.identity, {**self.sample, 'session_time': 51, 'lap_number': 4,
            'distance_pct': .01, 'fuel_l': 16., 'on_pit_road': False, 'incidents': 0})
        context = self.live.context()
        self.assertEqual(context['fuel_l_per_lap'], 4.)
        answer, source = self.coach.answer('연료 몇 랩 갈 수 있어?', context)
        self.assertIn('4랩분', answer); self.assertEqual(source, 'MEASURED')
        self.live.ingest({**self.identity, 'subsession_id': '100'}, {**self.sample, 'session_time': 2})
        self.assertIsNone(self.live.context()['fuel_l_per_lap'])

    def test_manual_link_survives_append_but_not_a_replaced_recording(self):
        telemetry = self.telemetry()
        key = recording_key(telemetry)
        appended = replace(telemetry, channels={name: np.concatenate([values, values[-1:]])
                                               for name, values in telemetry.channels.items()})
        self.assertEqual(recording_key(appended), key)
        note = self.store.save('위치 직접 지정', {'identity': {}, 'snapshot': None})
        self.store.link(note['id'], telemetry.path.name, key, 'MANUAL', 2, .25)
        request = api.AnalyzeRequest()
        analyzer = api.TelemetryAnalyzer(telemetry)
        self.assertEqual(review_notes(self.store, analyzer, analyzer.analyze(request), request)['linked_count'], 1)
        replaced_channels = {name: values.copy() for name, values in telemetry.channels.items()}
        replaced_channels['Speed'][0] += 1
        replaced = replace(telemetry, channels=replaced_channels)
        self.assertNotEqual(recording_key(replaced), key)
        analyzer = api.TelemetryAnalyzer(replaced)
        review = review_notes(self.store, analyzer, analyzer.analyze(request), request)
        self.assertEqual(review['linked_count'], 0)
        self.assertEqual(review['pending'][0]['id'], note['id'])

    def test_global_f8_edges_freeze_location_and_ignore_key_repeat(self):
        user32 = MagicMock()
        user32.GetAsyncKeyState.side_effect = [0x8000, 0x8000, 0]
        with patch('live_telemetry.os.name', 'nt'), patch('live_telemetry.ctypes.windll', create=True) as windows:
            windows.user32 = user32
            self.live._poll_hotkey()
            self.live.ingest(self.identity, {**self.sample, 'session_time': 130., 'distance_pct': .30})
            self.live._poll_hotkey()
            self.live._poll_hotkey()
        self.assertEqual([e['down'] for e in self.live.hotkey_events], [True, False])
        capture = self.coach.capture(hotkey_serial=1)
        self.assertEqual(capture['context']['snapshot']['distance_pct'], .25)
        user32.GetAsyncKeyState.assert_called_with(0x77)
        app = FastAPI()
        register_voice_routes(app, self.coach, lambda _name: None, MagicMock(), api.TelemetryAnalyzer, api.AnalyzeRequest)
        with TestClient(app) as client:
            status = client.get('/api/live-coach/status').json()
        self.assertNotIn('context', status['hotkey_events'][0])
        self.assertNotIn('recent', status)

    def test_sdk_reader_excludes_replay_and_out_of_car_samples(self):
        for replay, on_track in [(True, True), (False, False), (False, True)]:
            with self.subTest(replay=replay, on_track=on_track):
                data = {CHANNELS[key]: value for key, value in
                        {**self.sample, 'is_replay': replay, 'on_track': on_track}.items() if key in CHANNELS}
                data.update(WeekendInfo={'SessionID': 12, 'TrackID': 1},
                            DriverInfo={'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'CarID': 2}]})
                sdk = MagicMock()
                sdk.is_initialized = sdk.is_connected = True
                sdk.var_headers_names = list(data)
                sdk.__getitem__.side_effect = data.__getitem__
                reader = LiveTelemetry(sdk_factory=lambda: sdk, clock=lambda: 1000.)
                reader._poll_hotkey = MagicMock()
                # One reader iteration, with no real SDK or global keyboard access.
                reader.stop_event.wait = lambda _delay: reader.stop_event.set()
                reader._loop()
                self.assertEqual(len(reader.samples), int(not replay and on_track))
                sdk.unfreeze_var_buffer_latest.assert_called_once()
                sdk.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
