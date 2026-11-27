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
import local_question_audio as question_audio
from local_question_dialogue import Dialogue, identity


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
    dialogue_id: str = Field(default='', max_length=100)


class LocalInstall(LocalOwner):
    enhancement_only: bool = False


class ReadbackCheck(LocalOwner):
    capture_id: str = Field(min_length=1, max_length=100)


class SpeechTest(LocalOwner):
    audio_base64: str = Field(max_length=480000)
    game: Literal['iracing', 'lmu'] = 'iracing'


class DialogueEvent(LocalOwner):
    dialogue_id: str = Field(min_length=1, max_length=100)
    capture_id: str = Field(default='', max_length=100)
    reason: Literal['timeout', 'cancelled', 'state_changed', 'recording_failed', 'playback_failed', 'disabled'] = 'cancelled'


class SpeechDelivery(LocalOwner):
    capture_id: str = Field(min_length=1, max_length=100)
    event: Literal['started', 'completed', 'cancelled', 'failed', 'not_played']
    capture_end_to_start_ms: float | None = Field(default=None, ge=0, le=120000, allow_inf_nan=False)
    client_response_ms: float | None = Field(default=None, ge=0, le=120000, allow_inf_nan=False)
    response_to_start_ms: float | None = Field(default=None, ge=0, le=120000, allow_inf_nan=False)
    playback_ms: float | None = Field(default=None, ge=0, le=120000, allow_inf_nan=False)
    reason: str = Field(default='', max_length=100)


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
    'dialogue_expired': '이어 묻기 시간이 지났습니다. 질문을 다시 말씀해 주세요.',
    'dialogue_missing': '이어 답할 질문이 없습니다. 원하는 정보를 질문해 주세요.',
    'dialogue_cancelled': '이어 묻기를 취소했습니다.',
    'clarification_rejected': '어떤 정보를 원하시는지 질문을 다시 말씀해 주세요.',
    'dialogue_target_changed': '비교 대상이 바뀌었습니다. 대상과 질문을 다시 말씀해 주세요.',
}


class LocalQuestions:
    def __init__(self, coach, buttons, runtime=None):
        self.coach, self.buttons = coach, buttons
        self._runtime = runtime
        self._runtime_lock = threading.Lock()
        self.lock = asyncio.Lock()
        self.dialogue = Dialogue()
        self.dialogue_lock = threading.RLock()
        self.delivery_lock = asyncio.Lock()

    def scope(self, owner, context, consent):
        return identity(owner, context, consent)

    async def dialogue_event(self, event, payload):
        consent = require_logs()
        if self.runtime.owner != payload.owner:
            raise HTTPException(409, '마이크 사용자가 바뀌었습니다.')
        context = await asyncio.to_thread(self.coach.live.context, include_recent=False)
        scope = self.scope(payload.owner, context, consent)
        if not self.same_session(context):
            with self.dialogue_lock:
                self.dialogue.clear()
            return {'accepted':False, 'dialogue':None, 'cancelled':True}
        if event == 'begin' and payload.capture_id:
            try:
                capture = await asyncio.to_thread(self.coach.capture_context, payload.capture_id)
            except ValueError:
                return {'accepted':False}
            if not self.same_session(capture):
                return {'accepted':False}
        with self.dialogue_lock:
            if event == 'ready':
                s = self.dialogue.valid(scope)
                if not s or s['capture_id'] != payload.capture_id:
                    return {'dialogue':None}
                return {'dialogue':self.dialogue.ready(scope, payload.dialogue_id)}
            if event == 'begin':
                accepted = self.dialogue.begin(scope, payload.dialogue_id, payload.capture_id)
                return {'accepted':bool(accepted)}
            old = self.dialogue.cancel(scope, payload.dialogue_id, payload.reason)
        if old:
            await asyncio.to_thread(self.coach.store.update, old['capture_id'], local_question={
                'dialogue_outcome':{'status':payload.reason, 'confirmed':None}})
        return {'cancelled':bool(old)}

    async def delivery(self, payload):
        async with self.delivery_lock:
            return await self._delivery(payload)

    async def _delivery(self, payload):
        consent = require_logs()
        note = await asyncio.to_thread(self.coach.store.get, payload.capture_id)
        context = (note or {}).get('context') or {}
        detail = context.get('local_question') or {}
        if (self.runtime.owner != payload.owner or detail.get('log_consent_at') != consent
                or detail.get('request_owner') != payload.owner or (note or {}).get('source') != 'LOCAL_VOICE'):
            raise HTTPException(409, '음성 질문의 재생 기록을 확인하지 못했습니다.')
        delivery = dict(detail.get('delivery') or {})
        if payload.event == 'started' and delivery.get('status') in ('completed','cancelled','failed','not_played'):
            return {'ok':True}
        delivery.update(status=payload.event, reason=payload.reason)
        for key in ('capture_end_to_start_ms','client_response_ms','response_to_start_ms','playback_ms'):
            value = getattr(payload, key)
            if value is not None:
                delivery.setdefault(key, round(value))
        await asyncio.to_thread(self.coach.store.update, payload.capture_id, local_question={'delivery':delivery})
        return {'ok':True}

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
        with self.dialogue_lock:
            self.dialogue.clear()
        detail = {'topic': None, 'topic_name':command['name'], 'command':command,
                  'result':'command_pending', 'model':self.runtime.model, 'engine':'sherpa-onnx' if self.runtime.model=='sensevoice' else 'faster-whisper',
                  'asr_ms':asr.get('asr_ms'), 'audio_saved':False, 'total_ms':0, 'sent':[],
                  'log_consent_at':context.get('speech_consent_at'), 'request_owner':payload.owner}
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
            if asr.get('reason') in ('no_speech','uncertain_audio'):
                result = {**result, 'topic':None, 'reason':asr['reason'], 'candidates':[]}
            scope = self.scope(payload.owner, context, consent_at)
            dialogue_meta = {'action':'unrecognized','confirmed':None}
            if not error and text and asr.get('reason') not in ('no_speech','uncertain_audio'):
                dialogue_context = await asyncio.to_thread(self.coach.live.context, include_recent=False)
                with self.dialogue_lock:
                    result, dialogue_meta = self.dialogue.interpret(text, result, scope,
                        dialogue_id=payload.dialogue_id, capture_id=payload.capture_id, context=dialogue_context)
            classify_ms = round((time.perf_counter()-classify_start)*1000, 3)
            topic = result['topic']
            additional_topics = result.get('additional_topics', [])[:1]
            if topic and dialogue_meta['action'] != 'standalone':
                dialogue_meta.setdefault('resolved_text', EXAMPLES[topic][0])
            reason = 'recognition_failed' if error else asr.get('reason') if not text else result['reason']
            reason = reason or 'unrecognized'
            parts, spoken, audible = [], [], []
            missing_data, missing_audio = [], []
            focus = None
            rendered = {}
            answer_detail = result.get('answer_detail', 'default')
            prompt = result.get('prompt', '') or (REASONS['clarification_rejected'] if reason=='clarification_rejected' else '')
            if prompt:
                _, prompt = question_audio.select('strategy', prompt, getattr(self.coach, 'custom_packs', None),
                                                   detail=answer_detail)
                result['prompt'] = prompt
            if prompt and self.same_session(context) and self.runtime.owner == payload.owner:
                spoken = [prompt]
                try:
                    parts = [await asyncio.to_thread(self.buttons.read_clip, 'question_dialogue', 'strategy', prompt)]
                    audible = [prompt]
                except (OSError, ValueError, KeyError):
                    missing_audio = ['질문 확인 안내']
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
                        if topic in EXTRA_TOPICS or topic in ('catch_ahead','corner_loss') or answer_detail != 'default' or additional_topics:
                            rendered = await asyncio.to_thread(render_question, topic, current, self.buttons, detail=answer_detail)
                            for additional in additional_topics:
                                if additional == topic:
                                    continue
                                await asyncio.to_thread(enrich, additional, current, self.coach.live)
                                more = await asyncio.to_thread(render_question, additional, current, self.buttons)
                                for key in ('parts','display','spoken','missing_data','missing_audio'):
                                    rendered[key].extend(more[key])
                                rendered.setdefault('additional_evidence', {})[additional] = more.get('answer_evidence', {})
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
            warning = error or (result.get('correction_note') if reason in ('unsupported', 'ambiguous') else '') or REASONS.get(reason, '')
            if reason not in ('stale', 'recognition_failed') and (missing_data or missing_audio):
                warning = ' · '.join((['주행 데이터 부족: ' + ', '.join(missing_data)] if missing_data else [])
                                     + (['음원 없음: ' + ', '.join(missing_audio)] if missing_audio else []))
            elapsed = round((time.perf_counter()-start)*1000)
            detail = {**result, 'result': reason, 'model': self.runtime.model, 'engine': 'sherpa-onnx' if self.runtime.model=='sensevoice' else 'faster-whisper',
                      'requested_at': time.time() - (time.perf_counter() - start),
                      'classifier_version': VERSION, 'asr_ms': asr.get('asr_ms'), 'cpu_ms': asr.get('cpu_ms'),
                      'classify_ms': classify_ms, 'total_ms': elapsed,
                      'missing_data': missing_data, 'missing_audio': missing_audio,
                      'topic_name': TOPICS[topic][1] if topic else '', 'audio_saved': False, 'log_consent_at':consent_at,
                      'request_owner':payload.owner, 'dialogue':dialogue_meta, 'answer_detail':answer_detail,
                      'spoken_text':' '.join(audible), 'answer_facts':rendered.get('answer_evidence',{}),
                      'answer_explanation':rendered.get('explanation','')}
            if rendered.get('data_notes'):
                detail['data_notes'] = rendered['data_notes']
            if rendered.get('additional_evidence'):
                detail['additional_evidence'] = rendered['additional_evidence']
            if focus:
                detail['race_focus'] = {key:value for key,value in focus.items() if key not in ('normal','short')}
                detail['focus_signature'] = focus_signature
            if topic and audible and (dialogue_meta['action'] != 'standalone' or topic in ('fuel_save','corner_repeat')):
                from local_question_dialogue import rival
                topics = list(dict.fromkeys([topic, *additional_topics]))
                detail['playback_check'] = {'spoken':audible, 'topics':topics,
                                            'rivals':{item:rival(current,item) for item in topics}}
            context = {k: v for k, v in context.items() if k != 'recent'}
            context = {**context, 'local_question': detail, 'engineer': 'coach',
                       'backend_version': getattr(self.coach, 'app_version', None)}
            note = await asyncio.to_thread(self.coach.store.save, text, context, 'LOCAL_VOICE', payload.capture_id)
            note = await asyncio.to_thread(self.coach.store.update, note['id'], reply=' '.join(spoken),
                                          reply_source='MEASURED' if spoken else 'LOCAL', service_error=warning)
            parent_id = dialogue_meta.get('parent_capture_id')
            if parent_id:
                await asyncio.to_thread(self.coach.store.update, parent_id, local_question={
                    'dialogue_outcome':{'status':dialogue_meta['action'], 'confirmed':dialogue_meta.get('confirmed'),
                                        'reply_capture_id':payload.capture_id}})
            dialogue = None
            with self.dialogue_lock:
                if reason == 'clarification_needed' and self.same_session(context) and self.runtime.owner == payload.owner:
                    dialogue = self.dialogue.offer(scope, payload.capture_id, result['proposed_topic'],
                        prompt=prompt, choices=result['choices'], context=context)
                elif topic and spoken and not additional_topics and reason in ('prepared','prepared_partial','text_only','text_partial','audio_unavailable'):
                    dialogue = self.dialogue.offer(scope, payload.capture_id, topic, context=current, detail=answer_detail)
                else:
                    self.dialogue.clear()
            return {'note': public_note(note), 'audio_parts': parts, 'audio_base64': parts[0] if parts else None,
                    'spoken_text': ' '.join(audible),
                    'warning': warning, 'audio_stored': False, 'remaining_ms': 5000, 'dialogue':dialogue}


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
        with service.dialogue_lock:
            if service.dialogue.state and service.dialogue.state['scope'][0] == payload.owner:
                service.dialogue.clear()
        return {'ok': True}

    @router.post('/local-speech/dialogue/ready')
    async def dialogue_ready(payload: DialogueEvent):
        return await service.dialogue_event('ready', payload)

    @router.post('/local-speech/dialogue/begin')
    async def dialogue_begin(payload: DialogueEvent):
        return await service.dialogue_event('begin', payload)

    @router.post('/local-speech/dialogue/cancel')
    async def dialogue_cancel(payload: DialogueEvent):
        return await service.dialogue_event('cancel', payload)

    @router.post('/local-speech/delivery')
    async def delivery(payload: SpeechDelivery):
        return await service.delivery(payload)

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
                     and (detail.get('readback_text') or detail.get('focus_signature') or detail.get('playback_check')))
        if valid and detail.get('focus_signature'):
            import race_focus
            valid = detail.get('result') in ('prepared','prepared_partial') and await asyncio.to_thread(
                race_focus.current, coach.live, detail['focus_signature'])
        if valid and detail.get('result') == 'command_applied':
            with coach.live.lock:
                _, sample = coach.live._current_snapshot()
                valid = bool(sample and pit_voice_commands.applied(detail['command'], sample))
        if valid and detail.get('playback_check'):
            from local_question_dialogue import rival
            current = await asyncio.to_thread(coach.live.context, include_recent=False)
            expected = detail['playback_check']
            spoken = []
            for index, topic in enumerate(expected['topics']):
                await asyncio.to_thread(enrich, topic, current, coach.live)
                fresh = await asyncio.to_thread(render_question, topic, current, buttons,
                    detail=detail.get('answer_detail','default') if index==0 else 'default')
                spoken.extend(fresh['spoken'])
            valid = (service.same_session(current) and all(rival(current,t)==expected['rivals'][t] for t in expected['topics'])
                     and spoken == expected['spoken'] and service.runtime.owner == payload.owner and require_logs()==consent)
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
