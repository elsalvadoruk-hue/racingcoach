"""Local microphone questions reuse prepared radio answers and existing note logs."""
import asyncio
import threading
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from local_question_intents import EXAMPLES, VERSION, classify
from local_speech_runtime import LocalSpeechRuntime
from radio_buttons import wordings
from local_question_answers import TOPICS, EXTRA_TOPICS, REPORTS, enrich, render as render_question
from voice_notes import public_note
import pit_voice_commands
import local_speech_enhancement as enhancement
import pit_command_audio


def require_logs():
    import session_log_auto
    state = session_log_auto.status()
    if not state.get('enabled') or not state.get('available'):
        raise HTTPException(403, '로컬 음성 인식은 로그 보내기가 필수입니다. 전송 안내를 확인하고 로그 보내기를 켜 주세요.')
    return state['consented_at']


class LocalOwner(BaseModel):
    owner: str = Field(min_length=8, max_length=100)
    model: Literal['base', 'small', 'sensevoice'] = 'sensevoice'


class LocalQuestion(LocalOwner):
    capture_id: str = Field(min_length=1, max_length=100)
    audio_base64: str = Field(default='', max_length=480000)
    text: str = Field(default='', max_length=500)
    speak: bool = True
    allow_pit_commands: bool = False


class LocalInstall(LocalOwner):
    enhancement_only: bool = False


class ReadbackCheck(LocalOwner):
    capture_id: str = Field(min_length=1, max_length=100)


class SpeechTest(LocalOwner):
    audio_base64: str = Field(max_length=480000)
    game: Literal['iracing', 'lmu'] = 'iracing'


REASONS = {
    'no_speech': '목소리가 감지되지 않았습니다.',
    'uncertain_audio': '말씀을 확실히 알아듣지 못했습니다.',
    'unrecognized': '질문을 알아듣지 못했습니다.',
    'unsupported': '지원하는 질문을 고르지 못했습니다. 질문 예시를 확인하세요.',
    'ambiguous': '여러 질문으로 해석됩니다. 한 가지씩 질문해 주세요.',
    'unsupported_game': '가상 에너지는 LMU, SOF는 iRacing에서 사용할 수 있습니다.',
    'not_connected': '질문은 인식했지만 실시간 차량 연결이 없어 답할 수 없습니다. 주행 없이 확인하려면 인식 테스트를 사용하세요.',
    'data_unavailable': '지금 답할 수 있는 주행 정보가 없습니다.',
    'audio_unavailable': '답변 음성이 없습니다. 설정에서 빠진 음성을 받아 주세요.',
    'stale': '주행이나 마이크 상태가 바뀌어 답변을 취소했습니다.',
    'recognition_failed': '음성인식을 완료하지 못했습니다. 마이크를 다시 준비하세요.',
}


class LocalQuestions:
    def __init__(self, coach, buttons, runtime=None):
        self.coach, self.buttons = coach, buttons
        self._runtime = runtime
        self._runtime_lock = threading.Lock()
        self.lock = asyncio.Lock()

    @property
    def runtime(self):
        # Route registration should not access personal storage or create a
        # recognizer. Initialize only when the speech feature is requested.
        with self._runtime_lock:
            if self._runtime is None:
                self._runtime = LocalSpeechRuntime(self.coach.store.path.parent / 'local-speech')
                self.coach.live.local_speech_runtime = self._runtime
            return self._runtime

    def same_session(self, context):
        with self.coach.live.lock:
            _, sample = self.coach.live._current_snapshot()
            return bool(context.get('connected') and sample and sample.get('run_id')
                        and sample['run_id'] == (context.get('snapshot') or {}).get('run_id')
                        and self.coach.live.game == context.get('game'))

    async def classify_audio(self, payload, text, game, asr):
        result = classify(text, game)
        if (payload.audio_base64 and self.runtime.model == 'sensevoice'
                and getattr(self.runtime, 'enhancement_ready', False)
                and asr.get('reason') not in ('no_speech', 'uncertain_audio')
                and enhancement.eligible(text, game)):
            retry = {'attempted': True, 'accepted': False, 'text': ''}
            try:
                decoded = await asyncio.to_thread(self.runtime.recognize, payload.owner, payload.audio_base64, enhance=True)
                candidate = decoded.get('text', '')[:500]
                chosen = enhancement.select(text, candidate, game)
                retry.update(text=candidate, asr_ms=decoded.get('asr_ms'))
                if chosen and decoded.get('reason') not in ('no_speech', 'uncertain_audio'):
                    result = {**chosen, 'correction_status': 'corrected', 'corrections': [
                        {'from':text, 'to':candidate, 'kind':'noise_retry', 'reason':'원본 질문 미연결 후 소음 제거 재인식'},
                        *chosen['corrections']]}
                    retry['accepted'] = True
            except (ValueError, OSError):
                retry['error'] = '잡음 제거 재인식을 완료하지 못했습니다.'
            result['noise_retry'] = retry
        return result

    async def command(self, payload, text, command, context, asr, start):
        detail = {'topic': None, 'topic_name':command['name'], 'command':command,
                  'result':'command_pending', 'model':self.runtime.model, 'engine':'sherpa-onnx' if self.runtime.model=='sensevoice' else 'faster-whisper',
                  'asr_ms':asr.get('asr_ms'), 'audio_saved':False, 'total_ms':0, 'sent':[],
                  'log_consent_at':context.get('speech_consent_at')}
        # Persist a receipt BEFORE sending. Retrying a capture after a timeout or
        # crash can read the receipt but must never send the command a second time.
        saved_context={k:v for k,v in context.items() if k!='recent'}
        saved_context['local_question']=detail
        await asyncio.to_thread(self.coach.store.save,text,saved_context,'LOCAL_VOICE',payload.capture_id)
        outcome={'result':'command_blocked','message':'명령을 전송하지 않았습니다.','sent':[]}
        try:
            if not payload.allow_pit_commands:
                outcome.update(result='command_disabled',message='음성 인식에서 “iRacing 피트 명령 보내기”를 켜면 사용할 수 있습니다.')
            elif command.get('error'):
                outcome.update(message=command['error'])
            elif not context.get('connected') or context.get('game')!='iracing' or not self.same_session(context):
                outcome.update(message='iRacing의 현재 차량에 연결된 상태에서만 피트 명령을 보낼 수 있습니다.')
            else:
                current=await asyncio.to_thread(self.coach.live.context,include_recent=False)
                if not self.same_session(context) or not self.same_session(current):
                    raise ValueError('주행이 바뀌어 명령을 전송하지 않았습니다.')
                prepared=pit_voice_commands.plan(command,current)
                detail['command']=prepared; detail['topic_name']=prepared['name']
                outcome=await asyncio.to_thread(pit_voice_commands.execute,prepared,context,self.coach.live,
                                               lambda:self.runtime.owner==payload.owner and require_logs()==context.get('speech_consent_at'))
        except ValueError as exc:
            outcome.update(message=str(exc))
        except Exception:
            # Do not claim a transport failure means that no earlier operation
            # reached the simulator, and never retry an uncertain command.
            outcome.update(result='command_unconfirmed',message='명령 처리 결과를 확인하지 못했습니다. 게임의 피트 설정을 확인하세요.')
        detail.update(result=outcome['result'], sent=outcome['sent'], total_ms=round((time.perf_counter()-start)*1000))
        audio = {'parts':[], 'spoken':[], 'missing':False}
        prepared = detail['command']
        if payload.speak and self.same_session(context) and self.runtime.owner == payload.owner:
            audio = await asyncio.to_thread(pit_command_audio.render, prepared, outcome, self.buttons)
            if not self.same_session(context) or self.runtime.owner != payload.owner:
                audio = {'parts':[], 'spoken':[], 'missing':False}
            elif outcome['result'] == 'command_applied':
                # Preparation can yield to another control input. Success audio
                # must still describe the setting currently observed in game.
                with self.coach.live.lock:
                    _, sample = self.coach.live._current_snapshot()
                    if not sample or not pit_voice_commands.applied(prepared, sample):
                        audio = {'parts':[], 'spoken':[], 'missing':False}
                        detail['readback_result'] = 'settings_changed'
        detail.update(readback_text=' '.join(audio['spoken']), readback_audio_missing=audio['missing'])
        note=await asyncio.to_thread(self.coach.store.update,payload.capture_id,reply=outcome['message'],reply_source='LOCAL',
                                    service_error='' if outcome['result']=='command_applied' else outcome['message'],local_command=detail)
        warning = outcome['message'] if outcome['result'] != 'command_applied' else ''
        if audio['missing']:
            warning = ' · '.join(filter(None, [warning, '피트 명령 복창 음성이 준비되지 않았습니다. 화면의 적용 결과를 확인하세요.']))
        return {'note':public_note(note),'audio_parts':audio['parts'],'audio_base64':audio['parts'][0] if audio['parts'] else None,
                'spoken_text':' '.join(audio['spoken']), 'command_result':outcome, 'warning':warning,'audio_stored':False}

    async def ask(self, payload):
        consent_at = require_logs()
        if self.lock.locked():
            raise HTTPException(409, '이전 질문을 처리 중입니다.')
        async with self.lock:
            start = time.perf_counter()
            existing = await asyncio.to_thread(self.coach.store.get, payload.capture_id)
            if existing:
                return {'note': public_note(existing), 'audio_base64': None, 'reused': True}
            try:
                context = await asyncio.to_thread(self.coach.capture_context, payload.capture_id)
            except ValueError as exc:
                raise HTTPException(410, str(exc)) from exc
            context['speech_consent_at'] = consent_at
            # Audio never reaches a cloud fallback, even if recognition fails.
            text, asr, error = payload.text.strip(), {}, ''
            try:
                self.runtime.heartbeat(payload.owner)
                if not text:
                    asr = await asyncio.to_thread(self.runtime.recognize, payload.owner, payload.audio_base64)
                    text = asr.get('text', '')
            except Exception as exc:
                error = str(exc) if isinstance(exc, ValueError) else REASONS['recognition_failed']
            command = pit_voice_commands.parse(text)
            if require_logs() != consent_at:
                raise HTTPException(403, '로그 보내기 설정이 바뀌어 질문을 취소했습니다.')
            if command and not error and asr.get('reason') not in ('no_speech','uncertain_audio'):
                return await self.command(payload,text,command,context,asr,start)
            classify_start = time.perf_counter()
            result = await self.classify_audio(payload, text, context.get('game'), asr)
            classify_ms = round((time.perf_counter()-classify_start)*1000, 3)
            topic = result['topic']
            reason = 'recognition_failed' if error else asr.get('reason') if not text else result['reason']
            reason = reason or 'unrecognized'
            parts, spoken, audible = [], [], []
            missing_data, missing_audio = [], []
            focus = None
            if not error and topic:
                if not context.get('connected'):
                    reason = 'not_connected'
                elif not self.same_session(context) or self.runtime.owner != payload.owner:
                    reason = 'stale'
                else:
                    # Current numerical answers use current data, while the note
                    # retains the driver's original question position.
                    current = await asyncio.to_thread(self.coach.live.context, include_recent=False)
                    if not self.same_session(context) or not self.same_session(current):
                        reason = 'stale'
                    else:
                        await asyncio.to_thread(enrich, topic, current, self.coach.live)
                        if topic == 'race_focus':
                            focus = current.get('question_focus')
                        if topic in EXTRA_TOPICS:
                            rendered = await asyncio.to_thread(render_question, topic, current, self.buttons)
                            parts, spoken = rendered['parts'], rendered['display']
                            audible = rendered['spoken']
                            missing_data, missing_audio = rendered['missing_data'], rendered['missing_audio']
                            reason = ('prepared_partial' if missing_data or missing_audio else 'prepared') if parts else (
                                'audio_unavailable' if missing_audio else 'data_unavailable')
                        else:
                            candidates = wordings(topic, current)
                            if not candidates:
                                reason = 'data_unavailable'
                            else:
                                rendered = await asyncio.to_thread(self.buttons.render, [topic], current, driving=True)
                                parts, spoken = rendered['parts'], rendered['spoken']
                                audible = spoken if parts else []
                                reason = 'prepared' if parts else 'audio_unavailable'
                                if not parts:
                                    spoken = [text for _, text in candidates]
            if (parts or spoken) and (not self.same_session(context) or self.runtime.owner != payload.owner):
                reason, parts, spoken, audible = 'stale', [], [], []
            focus_signature = ''
            if focus:
                import race_focus
                focus_signature = race_focus.signature(focus)
                if not await asyncio.to_thread(race_focus.current, self.coach.live, focus_signature):
                    reason, parts, spoken, audible = 'stale', [], [], []
            if not payload.speak:
                parts = []
                audible = []
                if reason in ('prepared', 'prepared_partial'):
                    reason = 'text_partial' if missing_data or missing_audio else 'text_only'
            warning = error or REASONS.get(reason, '')
            if reason not in ('stale', 'recognition_failed') and (missing_data or missing_audio):
                warning = ' · '.join((['주행 데이터 부족: ' + ', '.join(missing_data)] if missing_data else [])
                                     + (['음원 없음: ' + ', '.join(missing_audio)] if missing_audio else []))
            elapsed = round((time.perf_counter()-start)*1000)
            detail = {**result, 'result': reason, 'model': self.runtime.model, 'engine': 'sherpa-onnx' if self.runtime.model=='sensevoice' else 'faster-whisper',
                      'requested_at': time.time() - (time.perf_counter() - start),
                      'classifier_version': VERSION, 'asr_ms': asr.get('asr_ms'), 'cpu_ms': asr.get('cpu_ms'),
                      'classify_ms': classify_ms, 'total_ms': elapsed,
                      'missing_data': missing_data, 'missing_audio': missing_audio,
                      'topic_name': TOPICS[topic][1] if topic else '', 'audio_saved': False, 'log_consent_at':consent_at}
            if focus:
                detail['race_focus'] = {key:value for key,value in focus.items() if key not in ('normal','short')}
                detail['focus_signature'] = focus_signature
            context = {k: v for k, v in context.items() if k != 'recent'}
            context = {**context, 'local_question': detail, 'engineer': 'coach',
                       'backend_version': getattr(self.coach, 'app_version', None)}
            note = await asyncio.to_thread(self.coach.store.save, text, context, 'LOCAL_VOICE', payload.capture_id)
            note = await asyncio.to_thread(self.coach.store.update, note['id'], reply=' '.join(spoken),
                                          reply_source='MEASURED' if spoken else 'LOCAL', service_error=warning)
            return {'note': public_note(note), 'audio_parts': parts, 'audio_base64': parts[0] if parts else None,
                    'spoken_text': ' '.join(audible),
                    'warning': warning, 'audio_stored': False, 'remaining_ms': 5000}


def register(router, coach, buttons):
    service = LocalQuestions(coach, buttons)

    @router.get('/local-speech')
    def status():
        return service.runtime.status()

    @router.get('/local-speech/questions')
    def questions():
        return {'questions': [{'id': key, 'name': TOPICS[key][1], 'group': TOPICS[key][0], 'examples': samples,
                               'includes': [TOPICS[item][1] for item in REPORTS.get(key, ())],
                               'game': 'lmu' if key == 'energy_remaining' else 'iracing' if key == 'sof' else 'both'}
                              for key, samples in EXAMPLES.items()], 'commands':pit_voice_commands.COMMAND_EXAMPLES}

    @router.post('/local-speech/install')
    def install(payload: LocalInstall):
        context = coach.live.context(include_recent=False)
        speed = (context.get('snapshot') or {}).get('speed_mps') or 0
        if context.get('connected') and speed > 2:
            raise HTTPException(409, '인식기 설치는 주행을 마친 뒤 진행하세요.')
        try:
            return (service.runtime.setup(payload.model, enhancement_only=True) if payload.enhancement_only
                    else service.runtime.setup(payload.model))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/local-speech/activate')
    def activate(payload: LocalOwner):
        require_logs()
        try:
            return service.runtime.activate(payload.owner, payload.model)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/local-speech/heartbeat')
    def heartbeat(payload: LocalOwner):
        try:
            require_logs()
        except HTTPException:
            service.runtime.deactivate(payload.owner)
            raise
        try:
            service.runtime.heartbeat(payload.owner)
            return {'ok': True}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/local-speech/deactivate')
    def deactivate(payload: LocalOwner):
        service.runtime.deactivate(payload.owner)
        return {'ok': True}

    @router.post('/local-speech/utterances')
    async def utterance(payload: LocalQuestion):
        return await service.ask(payload)

    @router.post('/local-speech/readback-check')
    async def check_readback(payload: ReadbackCheck):
        consent = require_logs()
        note = await asyncio.to_thread(coach.store.get, payload.capture_id)
        context = (note or {}).get('context') or {}
        detail = context.get('local_question') or {}
        valid = bool(service.runtime.owner == payload.owner and service.same_session(context)
                     and detail.get('log_consent_at') == consent
                     and (detail.get('readback_text') or detail.get('focus_signature')))
        if valid and detail.get('focus_signature'):
            import race_focus
            valid = detail.get('result') in ('prepared','prepared_partial') and await asyncio.to_thread(
                race_focus.current, coach.live, detail['focus_signature'])
        if valid and detail.get('result') == 'command_applied':
            with coach.live.lock:
                _, sample = coach.live._current_snapshot()
                valid = bool(sample and pit_voice_commands.applied(detail['command'], sample))
        return {'valid': valid}

    @router.post('/local-speech/test')
    async def test_recognition(payload: SpeechTest):
        consent_at = require_logs()
        start = time.perf_counter()
        try:
            result = await asyncio.to_thread(service.runtime.recognize, payload.owner, payload.audio_base64)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        intent = await service.classify_audio(payload, result.get('text', ''), payload.game, result)
        command = pit_voice_commands.parse(result.get('text', ''))
        if require_logs() != consent_at:
            raise HTTPException(403, '로그 보내기 설정이 바뀌어 테스트를 취소했습니다.')
        detail={**intent,'test':True,'model':payload.model,'engine':'sherpa-onnx' if payload.model=='sensevoice' else 'faster-whisper',
                'result':result.get('reason') or intent.get('reason'), 'asr_ms':result.get('asr_ms'),
                'topic_name':command['name'] if command else TOPICS[intent['topic']][1] if intent['topic'] else '',
                'command':command, 'classifier_version':VERSION, 'cpu_ms':result.get('cpu_ms'),
                'total_ms':round((time.perf_counter()-start)*1000),'audio_saved':False,'log_consent_at':consent_at}
        await asyncio.to_thread(coach.store.save,result.get('text',''),{'game':payload.game,'local_question':detail},'LOCAL_VOICE')
        return {**result, **intent, 'audio_reason': result.get('reason'),
                'topic_name': (command['name']+' · 테스트에서는 전송 안 함') if command else TOPICS[intent['topic']][1] if intent['topic'] else '',
                'command': command,
                'total_ms': round((time.perf_counter()-start)*1000), 'saved': True}

    return service
