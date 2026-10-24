"""Push-to-talk Korean speech, deterministic facts and persistent voice notes."""
from __future__ import annotations

import asyncio
import base64
import contextlib
from collections import OrderedDict
import importlib.util
import io
import json
import logging
import math
import os
import random
import threading
import time
import uuid
import wave
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from live_telemetry import live_facts, number
from voice_notes import VoiceNoteStore, file_identity, identity_compatible, public_note, recording_key
from voice_context import (coaching_instructions, fuel_range_question, general_question, lap_review_question, recent_conversation,
                           recent_observations, compact_recent, review_question, columnar, corner_facts)
from live_corners import corner_answer, lap_answer
from race_engineer import ROLES, role_voice, choose_role, measured_answer, damage_answer
from spotter import SpotterAudio
from radio_audio import CALLS as FIXED_CALLS, RadioAudioCache, fixed_catalog, spoken_form, texts as fixed_texts
import radio_phrases
from radio_policy import HOLD_DEFAULT_S
from instant_answers import instant_answer
from radio_phrases import ACKS
from live_controls import control_answer
from voice_alerts import reports_enabled, category


VOCABULARY = ['언더스티어', '오버스티어', '브레이크 바이어스', '안티롤바', '리어 윙', '디퍼렌셜',
              '브레이크 릴리스', '트레일 브레이킹', '스로틀', '에이펙스', '연석', 'ABS', 'TC', 'iRacing', 'Garage61']

logger = logging.getLogger('iRacingVoiceCoach')
DEFAULT_TTS_VOICE = 'ko-KR-Chirp3-HD-Algenib'
NO_THINKING_MODELS = ('gemini-2.5-flash', 'gemini-3.5-flash')


class RadioDeliveryEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(default='', max_length=80)
    key: str = Field(default='', max_length=80)
    engineer: Literal['spotter', 'race', 'strategy', 'coach'] = 'spotter'
    status: Literal['started', 'completed', 'interrupted', 'failed', 'expired', 'cancelled', 'muted']
    reason: str = Field(default='', max_length=160)
    text: str = Field(default='', max_length=2000)
    session_time: float | None = Field(default=None, allow_inf_nan=False)
    lap_number: int | None = None
    distance_pct: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    at: float | None = Field(default=None, allow_inf_nan=False)
    dropped_parts: int = Field(default=0, ge=0, strict=True)
    frontend_version: str | None = Field(default=None, max_length=64, pattern=r'^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$')
    backend_version: str | None = Field(default=None, max_length=64, pattern=r'^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$')


class RadioDeliveryBatch(BaseModel):
    events: list[RadioDeliveryEvent] = Field(max_length=64)


class SpotterOptions(BaseModel):
    voice: Literal['Fenrir', 'Algieba', 'Schedar', 'Aoede'] = 'Fenrir'
    formation_calls: int = Field(default=5, ge=0, le=8)
    repeat_seconds: float = Field(default=3, ge=1.5, le=8, allow_inf_nan=False)
    clear_delay: float = Field(default=.15, ge=.15, le=.5, allow_inf_nan=False)
    traffic_enabled: bool = True
    pit_box_enabled: bool = False
    alerts: dict[str, bool] | None = Field(default=None, max_length=80)
    wall_alerts: dict[str, bool] | None = Field(default=None, max_length=80)


def speech_region():
    # Chirp 3 Korean is generally available in the us/eu multi-regions.
    return os.getenv('IRACING_SPEECH_REGION', '').strip() or 'us'


def tts_voice():
    return os.getenv('IRACING_TTS_VOICE', '').strip() or DEFAULT_TTS_VOICE


def installed(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


class GoogleSpeech:
    def __init__(self):
        self.recognition_state = 'UNTESTED'
        self.synthesis_state = 'UNTESTED'
        # Channel and credential setup costs more than a short request; keep clients.
        self._clients, self._client_lock = {}, threading.Lock()

    def _client(self, key, factory):
        with self._client_lock:
            if key not in self._clients:
                self._clients[key] = factory()
            return self._clients[key]

    def _drop_client(self, key):
        with self._client_lock:
            self._clients.pop(key, None)

    def status(self):
        enabled = os.getenv('IRACING_AI_ENABLED', '1').lower() not in {'0', 'false', 'off'}
        return {'provider': 'Google Cloud', 'enabled': enabled,
                'recognition_installed': installed('google.cloud.speech_v2'),
                'synthesis_installed': installed('google.cloud.texttospeech'),
                'recognition_model': 'chirp_3', 'language': 'ko-KR',
                'region': speech_region(),
                'voice': tts_voice(),
                'credential_configured': bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS')),
                'connection_verified': self.recognition_state == 'SUCCESS',
                'recognition_state': self.recognition_state, 'synthesis_state': self.synthesis_state,
                'notice': '패키지·설정 상태입니다. 실제 권한과 연결은 음성 요청 시 확인합니다. 음성 인식·합성은 별도 과금됩니다.'}

    def recognize(self, encoded):
        if not self.status()['enabled']:
            raise ValueError('AI 서비스가 비활성화되어 있습니다. 텍스트 메모를 사용하세요.')
        if not self.status()['recognition_installed']:
            raise ValueError('음성 인식 패키지 설치 필요 · 음성코치_설치.bat를 실행하세요.')
        try:
            audio = base64.b64decode(encoded, validate=True)
            with wave.open(io.BytesIO(audio), 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000 or not .25 <= duration <= 45:
                    raise ValueError('16kHz 모노 음성을 0.25~45초 동안 녹음하세요.')
        except (ValueError, wave.Error, EOFError) as exc:
            raise ValueError('녹음 형식을 확인하세요. 16kHz 모노 WAV, 최대 45초입니다.') from exc
        from google.cloud import speech_v2
        from google.cloud.speech_v2.types import cloud_speech
        from google.api_core.client_options import ClientOptions
        region = speech_region()
        project = os.getenv('GCP_PROJECT_ID', '').strip()
        if not project:
            raise ValueError('Google Cloud 프로젝트가 설정되지 않았습니다. 설정 탭에서 키 파일을 등록하세요.')
        client = self._client(('speech', region), lambda: speech_v2.SpeechClient(
            client_options=ClientOptions(api_endpoint=f'{region}-speech.googleapis.com')))
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(), language_codes=['ko-KR'], model='chirp_3',
            adaptation=cloud_speech.SpeechAdaptation(phrase_sets=[
                cloud_speech.SpeechAdaptation.AdaptationPhraseSet(inline_phrase_set=cloud_speech.PhraseSet(
                    phrases=[{'value': text} for text in VOCABULARY]))]))
        try:
            response = client.recognize(request=cloud_speech.RecognizeRequest(
                recognizer=f'projects/{project}/locations/{region}/recognizers/_', config=config, content=audio), timeout=25)
        except Exception:
            self._drop_client(('speech', region))  # a broken channel must not outlive this request
            raise
        self.recognition_state = 'SUCCESS'
        # Chirp 3 confidence values are not calibrated; never use them as a truth score.
        text = ' '.join(result.alternatives[0].transcript for result in response.results if result.alternatives).strip()
        if not text:
            raise ValueError('말을 인식하지 못했습니다. 다시 말하거나 텍스트 메모를 남겨주세요.')
        return text[:2000]

    def synthesize(self, text, role='coach', encoding='mp3'):
        if not self.status()['enabled'] or not self.status()['synthesis_installed']:
            raise ValueError('한국어 음성 합성 연결 필요 · 텍스트 답변은 저장되었습니다.')
        if len(text.encode('utf-8')) > 5000:
            raise ValueError('답변이 음성 합성 길이 제한을 넘었습니다. 전체 답변은 대화 기록에서 확인하세요.')
        text = spoken_form(text)
        from google.cloud import texttospeech
        client = self._client('tts', texttospeech.TextToSpeechClient)
        try:
            response = client.synthesize_speech(request={
                # Pause marks are only understood in the markup field (Chirp 3 HD).
                'input': texttospeech.SynthesisInput(markup=text) if '[pause' in text else texttospeech.SynthesisInput(text=text),
                'voice': texttospeech.VoiceSelectionParams(language_code='ko-KR', name=role_voice(role)),
                # Fragments are joined sample by sample, so they are requested uncompressed.
                'audio_config': (texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16, sample_rate_hertz=24000)
                                 if encoding == 'wav' else texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)),
            }, timeout=20)
        except Exception:
            self._drop_client('tts')
            raise
        self.synthesis_state = 'SUCCESS'
        return base64.b64encode(response.audio_content).decode('ascii')


def service_error(exc):
    if isinstance(exc, ValueError):
        return str(exc)
    name = type(exc).__name__
    message = str(exc).lower()
    if name in {'PermissionDenied', 'InvalidArgument', 'NotFound'} and 'region' in message and any(
            text in message for text in ('no longer generally available', 'not available', 'not supported')):
        return '현재 음성 인식 리전에서 한국어 Chirp 3를 사용할 수 없습니다. IRACING_SPEECH_REGION을 us 또는 eu로 설정하고 앱을 재시작하세요.'
    if name in {'PermissionDenied', 'Unauthenticated', 'DefaultCredentialsError'}:
        return 'Google 서비스 인증·권한 또는 API 활성화 상태를 확인하세요.'
    if name in {'ResourceExhausted', 'TooManyRequests'}:
        return 'Google 서비스 사용량 한도에 도달했습니다. 기록은 유지됩니다.'
    return '음성/AI 서비스 요청을 완료하지 못했습니다. 연결·모델 리전을 확인하거나 텍스트 메모를 사용하세요.'


class Utterance(BaseModel):
    capture_id: str | None = None
    text: str = Field(default='', max_length=2000)
    audio_base64: str = Field(default='', max_length=2_000_000)
    reply: bool = True
    speak: bool = True
    analysis_mode: Literal['measured', 'ai'] = 'measured'


class NoteEdit(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    filename: str | None = Field(default=None, max_length=500)
    lap_number: int | None = Field(default=None, ge=1)
    distance_pct: float | None = Field(default=None, ge=0, le=1)


class ReportRequest(BaseModel):
    alerts: dict[str, bool] | None = Field(default=None, max_length=80)
    level: str = Field(default='important', pattern='^(off|important|all)$')
    reserve_laps: float = Field(default=.5, ge=0, le=3, allow_inf_nan=False)
    pit_loss_s: float | None = Field(default=None, gt=0, le=600, allow_inf_nan=False)
    fuel_save_mode: Literal['off', 'finish', 'reserve'] = 'finish'
    pit_rule: Literal['UNKNOWN', 'CONCURRENT', 'SEQUENTIAL'] = 'UNKNOWN'
    hold_enabled: bool = True
    hold_lead_s: float = Field(default=HOLD_DEFAULT_S, ge=0, le=5, allow_inf_nan=False)
    speak: bool = True
    basic_spotter: bool = False


class AudioPreparation:
    """Synthesizes every engineer wording, with its numbers, before a drive.

    A few worker threads share one queue. Already cached clips cost nothing,
    so it can be started again after a stop or a failure.
    IRACING_AUDIO_WORKERS raises the thread count for a bulk fetch from the scripts (1-16, default 4).
    """
    WORKERS = max(1, min(16, int(os.getenv('IRACING_AUDIO_WORKERS', '4') or 4)))

    def __init__(self, cache):
        self.cache = cache
        self.lock = threading.Lock()
        self.threads = []
        self.stop_event = threading.Event()
        self.progress = {'state': 'IDLE', 'failed': 0, 'message': ''}
        self._items = None
        self._ready = (0., None)
        self._known, self._known_items = set(), None   # clips seen on disk for this list

    def items(self):
        if self._items is None:
            self._items = list(dict.fromkeys(radio_phrases.catalog() + fixed_catalog()))
        return self._items

    def running(self):
        return any(thread.is_alive() for thread in self.threads)

    def status(self):
        items = self.items()
        # Thousands of files: count them at most once a second.
        if time.monotonic() - self._ready[0] >= 1 or self._ready[1] is None:
            # 58,000 files took about 0.9 s to look at on every call. A clip once there stays there,
            # so after the first full look only the ones still missing are looked at again.
            known = self._known if self._known_items is items else set()
            missing = [item for item in items if item not in known and not self.cache.cached(*item)]
            gone = set(missing)
            self._known, self._known_items = {item for item in items if item not in gone}, items
            self._ready = (time.monotonic(), (len(items) - len(missing), sum(len(text) for _, text in missing)))
        ready, characters = self._ready[1]
        with self.lock:
            state = ('RUNNING' if self.running() else 'COMPLETE' if ready == len(items)
                     else self.progress['state'] if self.progress['state'] in ('FAILED', 'STOPPED') else 'IDLE')
            return {'state': state, 'total': len(items), 'ready': ready, 'missing': len(items) - ready,
                    'characters_missing': characters, 'failed': self.progress['failed'], 'message': self.progress['message'],
                    # The count covers the engineers and the chosen spotter voice only; the other voices are not in it.
                    'spotter_voice': radio_phrases.spotter_voice()}

    def start(self):
        with self.lock:
            if self.running():
                return
            self.stop_event.clear()
            self.progress = {'state': 'RUNNING', 'failed': 0, 'message': '', 'streak': 0}
            self._ready = (0., None)
            pending = iter([item for item in self.items() if not self.cache.cached(*item)])
            guard = threading.Lock()
            self.threads = [threading.Thread(target=self._work, args=(pending, guard), name=f'radio-audio-prepare-{n}', daemon=True)
                            for n in range(self.WORKERS)]
            for thread in self.threads:
                thread.start()

    def stop(self):
        self.stop_event.set()
        if self.progress['state'] == 'RUNNING':
            self.progress.update(state='STOPPED', message='준비를 멈췄습니다. 받은 음성은 그대로 사용합니다.')

    def join(self, timeout=None):
        for thread in self.threads:
            thread.join(timeout)

    def _work(self, pending, guard):
        while not self.stop_event.is_set():
            with guard:
                item = next(pending, None)
            if item is None:
                break
            for attempt in range(4):
                try:
                    self.cache.phrase(*item)
                    self.progress['streak'] = 0
                    break
                except Exception as exc:
                    if type(exc).__name__ in ('ResourceExhausted', 'TooManyRequests') and attempt < 3:
                        # Per-minute quota: wait it out instead of failing the batch.
                        if self.stop_event.wait(20):
                            return
                        continue
                    with guard:
                        self.progress['failed'] += 1
                        self.progress['streak'] += 1
                        logger.warning('Radio audio preparation failed (%s) for %r', type(exc).__name__, item[1])
                        if self.progress['streak'] >= 3 and self.progress['state'] == 'RUNNING':
                            # Credentials, quota or network: stop instead of failing thousands of requests.
                            self.progress.update(state='FAILED', message=service_error(exc))
                            self.stop_event.set()
                    break
        with guard:
            if self.progress['state'] == 'RUNNING' and not any(t.is_alive() for t in self.threads if t is not threading.current_thread()):
                self.progress.update(state='COMPLETE' if not self.progress['failed'] else 'FAILED',
                                     message='' if not self.progress['failed'] else '일부 음성을 받지 못했습니다. 다시 준비하면 빠진 것만 받습니다.')


FEELING_SAVED = '말씀하신 느낌을 저장했습니다. 현재 측정값만으로 원인은 확정할 수 없습니다. 주행 후 해당 구간을 비교하거나 AI 원인 분석을 선택해 설명을 요청하세요.'


def control_question(text):
    """Asking what to do with ABS / TC / brake bias, not what the current value is."""
    upper = ''.join(text.upper().split())
    return (any(word in upper for word in ('ABS', '에이비에스', 'TC', '티씨', '트랙션', '바이어스', '브레이크밸런스', '비밸'))
            and any(word in upper for word in ('추천', '바꿔', '바꿀', '어떻게', '올려', '내려', '올릴', '내릴', '조정', '조절', '괜찮', '적당', '맞아'))
            and not any(word in upper for word in ('얼마', '몇', '현재값', '지금값')))


class VoiceCoach:
    def __init__(self, live, store, ai_client=None, model='gemini-3.5-flash', speech=None):
        self.live, self.store, self.ai_client, self.model = live, store, ai_client, model
        self.speech = speech or GoogleSpeech()
        self.captures = OrderedDict()
        self.lock = threading.RLock()
        self.cloud_lock = asyncio.Lock()
        # Pit wall reports take turns among themselves only. A question holding the cloud lock no longer
        # holds them back when their clips are already on disk (see audio_on_disk).
        self.report_lock = asyncio.Lock()
        self.race_options = {'reserve_laps': .5, 'pit_loss_s': None}
        self.pending_report = None
        self.radio_audio = OrderedDict()
        self.fixed_audio = RadioAudioCache(self.speech, self.store.path.parent / 'radio_audio')
        self.live.setup_audio_ready=lambda text:self.fixed_audio.cached('coach',text)
        self._report_notes = (None, [])
        self.audio_preparation = AudioPreparation(self.fixed_audio)

    def report_notes(self):
        # Polled every couple of seconds; reread only after the store changed.
        revision = self.store.revision
        if self._report_notes[0] != revision:
            self._report_notes = (revision, self.store.list(100))
        return self._report_notes[1]

    def synthesize_answer(self, text, role='coach'):
        # Bounded memory only: private questions/replies never go to disk.
        key = (role_voice(role), text)
        if key not in self.radio_audio:
            audio = self.speech.synthesize(text) if role == 'coach' else self.speech.synthesize(text, role=role)
            self.radio_audio[key] = audio
            while len(self.radio_audio) > 64:
                self.radio_audio.popitem(last=False)
        self.radio_audio.move_to_end(key)
        return self.radio_audio[key]

    def capture(self, hotkey_serial=None):
        context = None
        if hotkey_serial is not None:
            for event in self.live.status(after=max(0, hotkey_serial - 1))['hotkey_events']:
                if event['serial'] == hotkey_serial and event['down']:
                    context = event['context']
        context = context or self.live.context()
        identifier = str(uuid.uuid4())
        with self.lock:
            self.captures[identifier] = {'context': context, 'expires': time.time() + 120}
            while len(self.captures) > 64:
                self.captures.popitem(last=False)
        return {'capture_id': identifier, 'context': {k: v for k, v in context.items() if k != 'recent'}}

    def capture_context(self, identifier):
        if not identifier:
            return self.live.context()
        with self.lock:
            capture = self.captures.get(identifier)
            if capture is None or capture['expires'] < time.time():
                raise ValueError('녹음 시작 위치의 유효 시간이 지났습니다. 다시 녹음하거나 텍스트 메모를 남겨주세요.')
            return capture['context']

    def answer(self, text, context, note_id=None, allow_ai=False):
        facts = live_facts(context if context.get('connected') else {})
        if not context.get('connected') and not (allow_ai and self.ai_client is not None and general_question(text)):
            return '실시간 차량 연결이 없어 현재 상태는 확인할 수 없습니다. 메모를 저장했으니 주행 후 파일과 코너를 연결하세요.', 'LOCAL'
        role = context.get('engineer', 'coach')
        # How the corner or lap just driven went is a measured comparison with the
        # driver's own best. The AI mode gets the same facts to explain, not to guess.
        corner_view = context.get('corner_coach') if context.get('connected') else None
        review_kind = review_question(text)
        review = corner_answer(corner_view) if review_kind == 'corner' else lap_answer(corner_view) if review_kind == 'lap' else None
        if context.get('connected') and control_question(text):
            return control_answer(context.get('controls'), text), 'MEASURED'
        if review:
            if allow_ai and self.ai_client is not None:
                return self.ai_answer(text, context, note_id)
            return review, 'MEASURED'
        if role == 'race':
            snapshot = context.get('snapshot') or {}
            if any(w in ''.join(text.split()) for w in ('데미지', '대미지', '손상')):
                return damage_answer(snapshot), 'MEASURED'
            radio = context.get('radio') or {}
            history = radio.get('history') or []
            latest_key = history[0]['key'] if history else None
            compact = ''.join(text.split())
            if any(w in compact for w in ('랩타임', '랩기록')) or latest_key in ('lap_time', 'personal_best', 'pace_detail') and any(w in compact for w in ('왜', '자세히', '그건')):
                from race_radio import duration
                last = number(snapshot.get('lap_last_s'))
                previous = [lap['time'] for lap in radio.get('recent_laps', []) if lap['lap'] != snapshot.get('lap_completed')]
                parts = [f'직전 랩은 {duration(last)}입니다.' if last and last > 0 else '완료된 직전 랩 기록이 아직 없습니다.']
                if last and len(previous) >= 2:
                    import statistics
                    delta = last - statistics.median(previous)
                    parts.append(f'이전 깨끗한 {len(previous)}랩 중앙 기록과 비교하면 {abs(delta):.2f}초 {"느립니다" if delta > 0 else "빠릅니다"}.')
                lap_review = (corner_view or {}).get('last_lap')
                if lap_review and lap_review.get('lap') == snapshot.get('lap_completed'):
                    parts.append(lap_answer(corner_view))
                    parts.append('연료나 트래픽에 따른 차이는 분리하지 않은 비교입니다.')
                else:
                    parts.append('현재 자료만으로 어느 코너가 원인인지, 연료나 트래픽 영향인지 분리할 수 없습니다. 주행 후 IBT 코너 비교에서 감속 위치와 탈출 가속을 확인하겠습니다.')
                return ' '.join(parts), 'MEASURED'
            if any(w in compact for w in ('수리', '패널티', '경고', '고장', '수온', '유온', '오일압력')) or latest_key in ('repair', 'black', 'mandatory_repair'):
                flags = int(number(snapshot.get('session_flags')) or 0)
                parts = []
                if flags & 0x10000:
                    parts.append('블랙 플래그가 표시됐습니다. 패널티 유형과 이행 시간은 현재 채널로 확인되지 않으므로 게임에 표시된 지시를 그대로 따라야 합니다.')
                if flags & 0x100000:
                    parts.append('레드 닷이 표시됐습니다. 피트 진입과 필수 수리를 준비하세요.')
                for key, label in [('pit_repair_left_s', '필수 수리'), ('pit_optional_repair_left_s', '선택 수리')]:
                    seconds = number(snapshot.get(key))
                    if snapshot.get('on_pit_road') and seconds is not None and seconds >= 0:
                        parts.append(f'{label} 남은 시간 표시는 {seconds:.0f}초입니다.')
                warnings = int(number(snapshot.get('engine_warnings')) or 0)
                labels = [label for bit, label in [(1, '냉각수 온도'), (2, '연료 압력'), (4, '오일 압력'), (0x40, '오일 온도'), (0x80, '필수 수리'), (0x100, '선택 수리')] if warnings & bit]
                if labels:
                    parts.append('현재 ' + ', '.join(labels) + ' 경고가 켜져 있습니다.')
                parts.append('이 신호만으로 손상 부위나 고장 원인을 단정할 수 없습니다. 계기판과 게임의 작업 지시를 확인하세요.')
                return ' '.join(parts), 'MEASURED'
        if role == 'spotter':
            snapshot = context.get('snapshot') or {}
            from race_radio import duration
            if any(w in text for w in ('브리핑', '경기 전', '경기전')):
                return (context.get('radio') or {}).get('briefing') or '경기 브리핑 데이터를 아직 읽지 못했습니다.', 'MEASURED'
            if any(w in text for w in ('랩타임', '랩 타임', '랩 기록', '랩기록')):
                key = 'lap_best_s' if any(w in text for w in ('최고', '베스트', '가장 빠른')) else 'lap_last_s'
                value = duration(snapshot.get(key))
                return (f'{"이번 세션 최고" if key == "lap_best_s" else "직전 랩"} 기록은 {value}입니다.' if value else '완주 랩타임을 아직 읽지 못했습니다.'), 'MEASURED'
            if '피트' in text:
                limit = (context.get('radio') or {}).get('pit_speed_limit_kph')
                return (f'피트 제한속도는 {limit:g}킬로미터입니다.' if limit is not None else '피트 제한속도 데이터를 읽지 못했습니다. 게임의 표시를 확인하세요.'), 'MEASURED'
            if any(w in text for w in ('순위', '몇 등', '몇등')):
                position = number(snapshot.get('class_position'))
                return (f'현재 클래스 {int(position)}위입니다.' if position and position > 0 else '현재 클래스 순위는 확인 중입니다.'), 'MEASURED'
            raw = snapshot.get('car_left_right')
            from spotter import PHRASES, STATE_KEYS
            return (PHRASES[STATE_KEYS[raw]] if raw in STATE_KEYS else '현재 차량 겹침이 없다는 표시입니다.' if raw == 1 else '좌우 차량 데이터를 읽지 못했습니다.'), 'MEASURED'
        if role in ('race', 'strategy') and context.get('pit_wall', {}).get('active'):
            # Questions use the race facts frozen at capture, not later SDK data.
            return measured_answer(role, text, context['pit_wall']), 'MEASURED'
        if role == 'race' or role == 'strategy' and any(w in text for w in ('피트', '급유', '끝까지', '완주', '전략')):
            return measured_answer(role, text, context.get('pit_wall') or {'active': False}), 'LOCAL'
        if fuel_range_question(text):
            use, fuel = number(facts['fuel_l_per_lap']), number(facts['fuel_l'])
            if use is not None and use > 0 and fuel is not None:
                return f"최근 깨끗한 완주 {facts['fuel_observed_laps']}랩 기준으로 약 {math.floor(fuel / use)}랩분입니다. 여유 연료는 포함하지 않은 추정이며 주행에 따라 달라집니다.", 'MEASURED'
            return '아직 깨끗한 완주랩의 연료 소비 기록이 없어 남은 랩수를 계산할 수 없습니다.', 'LOCAL'
        if '연료' in text and not any(word in text for word in ('바퀴', '랩', '얼마나', '갈 수')):
            return (f"현재 연료는 {facts['fuel_l']:.1f}리터입니다." if number(facts['fuel_l']) is not None else '이 차량에서 현재 연료 값을 읽지 못했습니다.'), 'MEASURED'
        for words, key, label in [(['바이어스'], 'brake_bias', '브레이크 바이어스'), (['ABS', '에이비에스'], 'abs_setting', 'ABS'),
                                  (['TC', '티씨', '트랙션'], 'tc_setting', 'TC')]:
            if any(word in text.upper() for word in words) and any(word in text for word in ('얼마', '몇', '현재', '지금 값')):
                value = number(facts[key])
                return (f'현재 {label} 표시값은 {value:g}입니다. 값의 방향은 차량별로 다릅니다.' if value is not None else f'현재 {label} 채널이 없어 측정할 수 없습니다.'), 'MEASURED'
        if '속도' in text and any(word in text for word in ('현재', '지금', '얼마', '몇')):
            value = number(facts['speed_kph'])
            return (f'현재 속도는 {value:g}킬로미터입니다.' if value is not None else '현재 속도 채널을 읽지 못했습니다.'), 'MEASURED'
        if lap_review_question(text):
            return ('현재는 최근 약 20초의 주행만 보고 있어 이전 랩 전체에서 무엇을 고쳐야 하는지는 아직 판단할 수 없습니다. '
                    '주행 후 IBT 분석에서 기준 랩과 브레이크를 놓는 과정, 코너의 최저속도, 가속을 시작한 위치를 비교하겠습니다. '
                    '먼저 시간 손실이 큰 코너 하나를 골라 어떤 입력이 달랐는지 확인하고, 다음 주행에서는 그 입력 하나만 바꿔 '
                    '같은 코너가 더 편해지는지 확인하는 순서가 좋습니다.'), 'LOCAL'
        if not allow_ai:
            return FEELING_SAVED, 'LOCAL'
        if self.ai_client is None:
            return 'AI 분석 연결을 사용할 수 없습니다. 느낌을 저장했으니 주행 후 해당 구간의 속도와 입력을 비교하세요.', 'LOCAL'
        return self.ai_answer(text, context, note_id)

    def ai_answer(self, text, context, note_id=None):
        facts = live_facts(context if context.get('connected') else {})
        instructions = coaching_instructions(text)
        prompt = json.dumps({
            'driver_statement': text, 'connected': context.get('connected', False),
            'facts': facts, 'identity': context.get('identity'),
            'recent_observations': recent_observations(context),
            'lap_corner_comparison': corner_facts(context),
            'conversation_history': recent_conversation(self.store, context, note_id),
            'recent': columnar(compact_recent(context)),
        }, ensure_ascii=False)
        for token_limit in (1024, 2048):
            config = {'temperature': .1, 'max_output_tokens': token_limit, 'system_instruction': instructions, 'http_options': {'timeout': 20000}}
            # Thinking counts against the output budget and costs seconds. Short driving replies
            # do not need it. 2.5 and 3.5 Flash accept a budget of zero; 3.8 Flash ignores it
            # (and then takes 3 to 11 s), which is why it is not the model for spoken answers.
            if any(name in self.model for name in NO_THINKING_MODELS):
                config['thinking_config'] = {'thinking_budget': 0}
            response = self.ai_client.models.generate_content(model=self.model, contents=prompt, config=config)
            candidates = response.candidates or []
            raw_reason = candidates[0].finish_reason if candidates else None
            reason = getattr(raw_reason, 'name', raw_reason)
            if reason == 'MAX_TOKENS':
                logger.warning('Voice answer incomplete: model=%s finish=%s output_limit=%d', self.model, reason, token_limit)
                continue
            if reason and reason != 'STOP':
                raise ValueError('AI 답변이 정상적으로 완료되지 않았습니다. 질문과 위치는 저장되었습니다. 잠시 후 다시 질문하세요.')
            answer = (response.text or '').strip()
            if not answer:
                raise ValueError('AI 답변이 비어 있습니다. 메모는 저장되었습니다.')
            usage = getattr(response, 'usage_metadata', None)
            logger.info('Voice answer tokens: prompt=%s cached=%s output=%s', getattr(usage, 'prompt_token_count', None),
                        getattr(usage, 'cached_content_token_count', None), getattr(usage, 'candidates_token_count', None))
            return answer, 'VERTEX_AI'
        raise ValueError('AI 답변이 길이 제한으로 중단되었습니다. 질문과 위치는 저장되었습니다. 질문을 짧게 나누어 다시 말해주세요.')

    async def utterance(self, payload):
        # One user turn at a time; retries of a capture return its existing note.
        async with self.cloud_lock:
            if not self.store.available:
                raise HTTPException(status_code=503, detail='대화 기록 DB를 사용할 수 없습니다. 저장 경로를 확인하세요.')
            existing = self.store.get(payload.capture_id) if payload.capture_id else None
            if existing:
                return {'note': public_note(existing), 'audio_base64': None, 'reused': True}
            try:
                context = self.capture_context(payload.capture_id)
                text = payload.text.strip()
                source = 'TEXT'
                if not text:
                    if not payload.audio_base64:
                        raise ValueError('질문이나 메모를 입력하세요.')
                    text = await asyncio.to_thread(self.speech.recognize, payload.audio_base64)
                    source = 'VOICE'
                history = recent_conversation(self.store, context)
                snapshot = context.get('snapshot') or {}
                # A short follow-up may address an unsolicited engineer report.
                for previous in self.store.list(10):
                    old = previous.get('context', {}).get('snapshot') or {}
                    if (context.get('connected') and old.get('run_id') == snapshot.get('run_id')
                            and number(old.get('session_time')) is not None
                            and 0 <= snapshot['session_time'] - old['session_time'] <= 180):
                        history = [*history, {'engineer': previous.get('context', {}).get('engineer', 'coach')}]
                        break
                role = choose_role(text, history)
                radio_history = (context.get('radio') or {}).get('history') or []
                if radio_history and any(w in ''.join(text.split()) for w in ('왜그래', '그건', '더자세히', '자세히설명')):
                    latest_radio = radio_history[0]
                    if 0 <= context.get('captured_at', 0) - latest_radio['at'] <= 120:
                        role = latest_radio['engineer']
                        if role == 'spotter':
                            role = 'strategy' if latest_radio['key'] == 'fuel_low' else 'coach' if latest_radio['key'] == 'incident' else 'race'
                if fuel_range_question(text):
                    role = 'strategy'
                if review_question(text):
                    role = 'coach'
                context = {**context, 'engineer': role, 'analysis_mode': payload.analysis_mode,
                           'backend_version': getattr(self, 'app_version', None)}
            except Exception as exc:
                if payload.audio_base64 and not payload.text:
                    self.speech.recognition_state = 'FAILED'
                detail = service_error(exc)
                logger.warning('Voice recognition/capture failed (%s): %s', type(exc).__name__, detail)
                if payload.audio_base64 and not payload.text:
                    detail += ' 인식되지 않은 음성은 대화 기록으로 저장되지 않았습니다.'
                raise HTTPException(status_code=422, detail=detail) from exc
            note = await asyncio.to_thread(self.store.save, text, context, source, payload.capture_id)
            audio, warning, ack, parts = None, '', None, None
            # A common measured question is answered at once from prepared sentences.
            instant = instant_answer(text, context, self.live) if payload.reply else None
            if instant:
                role = instant[0]
            if payload.reply:
                try:
                    answer, answer_source = ((' '.join(instant[1]), 'MEASURED') if instant else
                                             await asyncio.to_thread(self.answer, text, context, note['id'], payload.analysis_mode == 'ai'))
                    note = await asyncio.to_thread(self.store.update, note['id'], reply=answer, reply_source=answer_source)
                except Exception as exc:
                    warning = service_error(exc)
                    logger.warning('Voice answer failed (%s): %s', type(exc).__name__, warning)
                    note = await asyncio.to_thread(self.store.update, note['id'], reply='메모를 저장했습니다. 주행 후 분석에서 다시 확인하세요.', reply_source='LOCAL', service_error=warning)
                if payload.speak and note.get('reply') and note['reply'] != FEELING_SAVED:
                    try:
                        if instant:
                            parts = [await asyncio.to_thread(self.fixed_audio.phrase, role, sentence) for sentence in instant[1]]
                            audio = parts[0]
                        else:
                            audio = await asyncio.to_thread(self.synthesize_answer, note['reply'], role)
                    except Exception as exc:
                        self.speech.synthesis_state = 'FAILED'
                        warning = service_error(exc)
                        note = await asyncio.to_thread(self.store.update, note['id'], service_error=warning)
            if payload.speak and audio is None and not warning and (not payload.reply or note.get('reply') == FEELING_SAVED):
                # A remark was only recorded: acknowledge it like a pit wall would, in a word or two.
                ack = random.choice(ACKS)
                try:
                    audio = await asyncio.to_thread(self.fixed_audio.phrase, role if role in ROLES else 'coach', ack)
                except Exception as exc:
                    ack, warning = None, service_error(exc)
            return {'note': public_note(note), 'audio_base64': audio, 'audio_parts': parts, 'warning': warning, 'audio_stored': False, 'ack': ack}

    def audio_on_disk(self, item, optional_parts=True):
        """Whether the mandatory audio is cached and can play during a cloud question.

        Multipart calls keep their first sentence; missing later sentences may be omitted.
        """
        cache, role = self.fixed_audio, item.get('engineer')
        if item.get('speech_parts'):
            sentences = item['speech_parts'][:1] if optional_parts else item['speech_parts']
            return all(cache.cached(role, sentence) for sentence in sentences)
        if item.get('speech_text'):
            return cache.cached(role, item['speech_text'])
        if item.get('speech_key'):
            base, _, index = item['speech_key'].partition('#')
            try:
                return cache.cached(FIXED_CALLS[base][0], fixed_texts(base)[int(index or 0)])
            except (KeyError, IndexError, ValueError):
                return False
        return False

    def radio_audio_mode(self, item):
        """(can bypass the cloud lock, omit cold optional clips).

        An idle cloud still prepares first-use optional sentences. During a question,
        a cached first sentence goes out immediately with whichever later clips exist.
        """
        on_disk = self.audio_on_disk(item)
        cached_only = on_disk and self.cloud_lock.locked()
        if on_disk and item.get('speech_parts') and not cached_only:
            on_disk = all(self.fixed_audio.cached(item['engineer'], text) for text in item['speech_parts'][1:])
        return on_disk, cached_only

    async def radio_audio_parts(self, item, cached_only=False):
        parts, dropped = [], 0
        for index, sentence in enumerate(item['speech_parts']):
            if index and cached_only and not self.fixed_audio.cached(item['engineer'], sentence):
                dropped += 1
                continue
            try:
                parts.append(await asyncio.to_thread(self.fixed_audio.phrase, item['engineer'], sentence))
            except Exception:
                if not index:
                    raise
                dropped += 1
        return parts, dropped

    async def report(self, payload):
        options = {'reserve_laps': payload.reserve_laps, 'pit_loss_s': payload.pit_loss_s, 'fuel_save_mode': payload.fuel_save_mode}
        selection_level = 'all' if payload.alerts is not None and payload.level != 'off' else payload.level
        self.race_options = options
        with self.live.lock:
            self.live.race_options = dict(self.race_options)
            self.live.speech_hold_s = payload.hold_lead_s if payload.hold_enabled else -1.
            self.live.pit_rule = payload.pit_rule if payload.pit_rule != 'UNKNOWN' else None
        context = self.live.context()
        view = self.live.race_view(**options)
        result = {'pit_wall': view, 'report': None}
        if payload.level == 'off' or not context.get('connected') or self.report_lock.locked() or not self.store.available:
            if payload.level == 'off' or not context.get('connected'):
                with self.lock:
                    self.pending_report = None
            return result
        async with self.report_lock:
            notes = await asyncio.to_thread(self.report_notes)
            with self.live.lock:
                context = self.live.context()
                view = self.live.race_view(**options)
                if not context.get('connected'):
                    return result
                snapshot = context['snapshot']
                candidates = self.live.pit_wall.candidates(view, snapshot, context, notes)
                if payload.basic_spotter:
                    owned = ('yellow', 'finish', 'class_traffic') if payload.alerts is None and self.live.radio.traffic_enabled else ('yellow', 'finish')
                    candidates = [c for c in candidates if c['key'] not in owned]
                candidates = reports_enabled(candidates, payload.alerts)
                report = self.live.pit_wall.select(candidates, snapshot, selection_level, cached_audio=True)
            if not report:
                with self.lock:
                    self.pending_report = None
                return result
            with self.lock:
                pending = self.pending_report
                if (pending and pending['run_id'] == snapshot['run_id']
                        and 0 <= snapshot['session_time'] - pending['at'] <= 15
                        and pending['decision']['key'] == report['key'] and pending['decision']['token'] == report['token']
                        and pending['decision']['text'] == report['text']
                        and pending['speak'] == payload.speak and pending['options'] == options and pending.get('alerts') == payload.alerts):
                    return {**result, 'pit_wall': view, 'report': pending['response']}
                self.pending_report = None
            audio, warning, parts = None, '', None
            if payload.speak:
                on_disk = self.audio_on_disk(report, optional_parts=False)
                if not on_disk and self.cloud_lock.locked():
                    return result           # it needs synthesis while a question is using the cloud: the next poll
                try:
                    async with (contextlib.nullcontext() if on_disk else self.cloud_lock):
                        if report.get('speech_parts'):
                            parts = [await asyncio.to_thread(self.fixed_audio.phrase, report['engineer'], sentence) for sentence in report['speech_parts']]
                            audio = parts[0]
                        elif report.get('speech_text'):
                            audio = await asyncio.to_thread(self.fixed_audio.phrase, report['engineer'], report['speech_text'])
                        elif report.get('speech_key'):
                            audio = await asyncio.to_thread(self.fixed_audio.clip, report['speech_key'])
                except Exception as exc:
                    warning = service_error(exc)
            # Cloud latency/disconnect/session changes must not deliver old race advice.
            latest = self.live.context()
            if (not latest.get('connected') or latest['snapshot']['run_id'] != snapshot['run_id']
                    or not 0 <= latest['snapshot']['session_time'] - snapshot['session_time'] <= 15):
                return result
            with self.live.lock:
                current_view = self.live.race_view(**options)
                current = self.live.pit_wall.candidates(current_view, latest['snapshot'], latest, notes)
                if payload.basic_spotter:
                    owned = ('yellow', 'finish', 'class_traffic') if payload.alerts is None and self.live.radio.traffic_enabled else ('yellow', 'finish')
                    current = [c for c in current if c['key'] not in owned]
                current = reports_enabled(current, payload.alerts)
                selected = self.live.pit_wall.select(current, latest['snapshot'], selection_level, cached_audio=True)
                if not selected or selected['key'] != report['key'] or selected['token'] != report['token'] or selected['text'] != report['text'] or selected.get('speech_key') != report.get('speech_key'):
                    return result
            note_context = {**context, 'backend_version': getattr(self, 'app_version', None),
                            'engineer': report['engineer'], 'pit_wall': view, 'report_key': report['key'], 'report_delivery': 'PREPARED',
                            'spoken_text': report.get('spoken_text'), 'speech_source': 'FIXED_CACHE'}
            note = await asyncio.to_thread(self.store.save, '자동 보고 · ' + ROLES[report['engineer']]['label'], note_context, 'AUTO_REPORT')
            note = await asyncio.to_thread(self.store.update, note['id'], reply=report['text'], reply_source='LOCAL' if report['engineer'] == 'coach' else 'MEASURED', service_error=warning)
            response = {'note': public_note(note), 'audio_base64': audio, 'audio_parts': parts, 'warning': warning,
                        'alert_categories': report.get('alert_categories') or [category(report['key'])],
                        'engineer': report['engineer'], 'priority': report['priority'],
                        'brief': True, 'spoken_text': report.get('spoken_text'), 'speech_source': 'FIXED_CACHE',
                        'audio_mime': 'audio/mpeg'}
            with self.lock:
                self.pending_report = {'run_id': snapshot['run_id'], 'at': snapshot['session_time'],
                    'decision': report, 'response': response, 'speak': payload.speak, 'options': options, 'alerts': payload.alerts}
            return {**result, 'pit_wall': view, 'report': response}

    def acknowledge_report(self, note_id):
        with self.lock, self.live.lock:
            pending = self.pending_report
            context = self.live.context()
            if not pending or pending['response']['note']['id'] != note_id:
                return {'accepted': False}
            if not context.get('connected') or context['snapshot']['run_id'] != pending['run_id']:
                self.pending_report = None
                return {'accepted': False}
            note = self.store.update(note_id, report_delivery='DELIVERED')
            self.live.pit_wall.delivered(pending['decision'], context['snapshot']['session_time'])
            self.pending_report = None
            return {'accepted': True, 'note': public_note(note)}


def register_voice_routes(app, coach, resolve_file, repository, analyzer_type, analyze_request_type):
    backend_version = getattr(app, 'version', None)
    coach.app_version = backend_version
    router = APIRouter(prefix='/api/live-coach')
    spotter_audio = SpotterAudio(coach.speech, lambda: role_voice('spotter'))

    @router.post('/radio-delivery')
    async def record_radio_delivery(payload: RadioDeliveryBatch):
        # Which game, track and car the call belonged to, so a session log names them even
        # when the drive left no engineer report. Only for the drive that is running now.
        live = coach.live
        with live.lock:
            run_id, identity = live.run_id, dict(live.identity)
            session_type = (live.pit_wall.race or {}).get('session_type')
            for event in payload.events:
                if event.run_id == run_id and callable(getattr(live, 'radio_delivery', None)):
                    live.radio_delivery(event.event_id, event.status, dropped_parts=event.dropped_parts)
                if event.run_id==run_id and getattr(live,'control_recommendations',None) is not None:
                    live.control_recommendations.delivery(event.event_id,event.status,live.clock())
        session = {key: identity.get(key) for key in ('game', 'track_name', 'track_path', 'track_config', 'car_name', 'car_path')}
        session['session_type'] = session_type
        events = [{**e.model_dump(), **({'session': session} if run_id and e.run_id == run_id else {})} for e in payload.events]
        await asyncio.to_thread(coach.store.record_radio, events)
        return {'recorded': len(payload.events)}

    @router.get('/radio-delivery')
    async def radio_delivery_log(limit: int = 200, run_id: str | None = None):
        return {'events': await asyncio.to_thread(coach.store.radio_log, max(1, min(limit, 5000)), run_id)}

    def use_spotter_voice(name):
        # Read by role_voice wherever a spotter sentence is fetched; clips are kept per voice.
        os.environ['IRACING_TTS_SPOTTER_VOICE'] = radio_phrases.VOICE_PREFIX + name
        coach.audio_preparation._items = None            # the list of sentences to prepare depends on the voice

    @router.post('/spotter/prepare')
    async def prepare_spotter(payload: SpotterOptions | None = None):
        if payload is not None:
            use_spotter_voice(payload.voice)
            coach.live.configure_spotter(payload.model_dump())
        async with coach.cloud_lock:
            try:
                return await asyncio.to_thread(spotter_audio.prepare)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=service_error(exc)) from exc

    @router.put('/spotter/settings')
    def configure_spotter(payload: SpotterOptions):
        use_spotter_voice(payload.voice)
        return {'settings': coach.live.configure_spotter(payload.model_dump())}

    @router.get('/radio-audio')
    def radio_audio_status():
        return coach.audio_preparation.status()

    @router.post('/radio-audio/prepare')
    def prepare_radio_audio():
        coach.audio_preparation.start()
        return coach.audio_preparation.status()

    @router.post('/radio-audio/stop')
    def stop_radio_audio():
        coach.audio_preparation.stop()
        return coach.audio_preparation.status()

    @router.post('/spotter/radio/{identifier}/render')
    async def render_radio(identifier: str):
        with coach.live.lock:
            event = coach.live.radio.get(identifier, coach.live.clock())
        if event is None:
            return {'event': None}
        # A cached mandatory sentence can play immediately during a cloud question.
        on_disk, cached_only = coach.radio_audio_mode(event)
        if event.get('control_recommendation',{}).get('setup') and not on_disk:
            # Setup advice uses prepared clips only. Never turn a deleted or
            # missing cache file into an automatic paid synthesis.
            return {'event':None}
        if not on_disk and coach.cloud_lock.locked():
            return {'busy': True}
        async with (contextlib.nullcontext() if on_disk else coach.cloud_lock):
            with coach.live.lock:
                event = coach.live.radio.get(identifier, coach.live.clock())
            if event is None:
                return {'event': None}
            parts, dropped_parts = None, 0
            try:
                if event.get('control_recommendation',{}).get('setup'):
                    audio=await asyncio.to_thread(coach.fixed_audio.read_prepared,event['engineer'],event['speech_text'])
                elif event.get('speech_parts'):
                    parts, dropped_parts = await coach.radio_audio_parts(event, cached_only)
                    audio = parts[0]
                elif event.get('speech_text'):
                    audio = await asyncio.to_thread(coach.fixed_audio.phrase, event['engineer'], event['speech_text'])
                elif event.get('speech_key'):
                    audio = await asyncio.to_thread(coach.fixed_audio.clip, event['speech_key'])
                else:
                    audio = await asyncio.to_thread(coach.synthesize_answer, event['text'], event['engineer'])
            except Exception as exc:
                raise HTTPException(status_code=422, detail=service_error(exc)) from exc
            with coach.live.lock:
                current = coach.live.radio.get(identifier, coach.live.clock())
            if current is None:
                return {'event': None}
            return {'event': {k: v for k, v in current.items() if k != 'condition'}, 'audio_base64': audio,
                    'audio_parts': parts, 'audio_mime': 'audio/mpeg', 'dropped_parts': dropped_parts}

    @router.post('/spotter/radio/{identifier}/ack')
    def acknowledge_radio(identifier: str):
        with coach.live.lock:
            return {'accepted': coach.live.radio.ack(identifier, coach.live.clock())}

    @router.websocket('/spotter/events')
    async def spotter_events(socket: WebSocket):
        await socket.accept()
        with coach.live.lock:
            coach.live.radio.welcome(coach.live.clock())
        view = coach.live.spotter_status()
        serial = view['serial']
        sent_at, signature = time.monotonic(), None
        try:
            await socket.send_json({'type': 'ready', **view, 'events': [], 'backend_version': backend_version})
            while True:
                view = coach.live.spotter_status(after=serial)
                serial = view['serial']
                current = (view['active'], view['raw_state'], view['state'], view['run_id'])
                if current != signature or view['events'] or time.monotonic() - sent_at >= .1:
                    await socket.send_json({'type': 'spotter', **view, 'backend_version': backend_version})
                    sent_at, signature = time.monotonic(), current
                await asyncio.sleep(.02)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    @router.get('/status')
    def status(after: int = 0):
        live_status = coach.live.status(after=max(0, after))
        # Context is retained server-side for /captures; polling only needs key edges.
        live_status['hotkey_events'] = [{k: v for k, v in event.items() if k != 'context'}
                                       for event in live_status['hotkey_events']]
        return {**live_status, 'speech': coach.speech.status(), 'engineers': {k: {**v, 'voice': role_voice(k)} for k, v in ROLES.items()},
                'ai_available': coach.ai_client is not None, 'model': coach.model, 'storage_available': coach.store.available}

    @router.post('/captures')
    def capture(hotkey_serial: int | None = None):
        return coach.capture(hotkey_serial)

    @router.websocket('/events')
    async def events(socket: WebSocket):
        await socket.accept()
        serial = coach.live.hotkey_state()['hotkey_serial']
        heartbeat = time.monotonic()
        try:
            await socket.send_json({'type': 'ready', 'serial': serial})
            while True:
                status = coach.live.hotkey_state(after=serial)
                for event in status['hotkey_events']:
                    serial = event['serial']
                    await socket.send_json({'type': 'ptt', 'serial': serial, 'down': event['down'], 'at': event['at']})
                if time.monotonic() - heartbeat > 1:
                    await socket.send_json({'type': 'heartbeat'})
                    heartbeat = time.monotonic()
                await asyncio.sleep(.03)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    @router.post('/utterances')
    async def utterance(payload: Utterance):
        return await coach.utterance(payload)

    @router.post('/reports')
    async def reports(payload: ReportRequest):
        return await coach.report(payload)

    @router.post('/reports/{note_id}/ack')
    def acknowledge_report(note_id: str):
        return coach.acknowledge_report(note_id)

    @router.post('/voices/{role}/test')
    async def test_voice(role: str):
        if role not in ROLES:
            raise HTTPException(status_code=404, detail='엔지니어를 확인하세요.')
        async with coach.cloud_lock:
            try:
                audio = await asyncio.to_thread(coach.fixed_audio.clip, 'preview_' + role)
                return {'engineer': role, 'audio_base64': audio}
            except Exception as exc:
                raise HTTPException(status_code=422, detail=service_error(exc)) from exc

    @router.get('/notes')
    def notes():
        return {'notes': [public_note(note) for note in coach.store.list()]}

    @router.patch('/notes/{note_id}')
    def edit(note_id: str, payload: NoteEdit):
        note = coach.store.get(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail='대화 기록을 찾을 수 없습니다.')
        if payload.text is not None and not payload.text.strip():
            raise HTTPException(status_code=422, detail='메모 내용이 비어 있습니다.')
        if payload.filename:
            path = resolve_file(payload.filename)
            if path is None:
                raise HTTPException(status_code=404, detail='연결할 주행 파일을 찾을 수 없습니다.')
            telemetry = repository.load(path)
            identity = file_identity(telemetry)
            original = note.get('context', {}).get('identity') or {}
            if original and not identity_compatible(original, identity):
                raise HTTPException(status_code=422, detail='대화와 파일의 차량·트랙·레이아웃이 다릅니다.')
            if payload.lap_number is None or payload.distance_pct is None or not any(l.lap_number == payload.lap_number for l in telemetry.laps):
                raise HTTPException(status_code=422, detail='해당 파일의 랩과 코너 위치를 선택하세요.')
            key = recording_key(telemetry)
            coach.store.link(note_id, path.name, key, 'MANUAL', payload.lap_number, payload.distance_pct)
        elif payload.lap_number is not None or payload.distance_pct is not None:
            raise HTTPException(status_code=422, detail='위치 수정에는 주행 파일도 필요합니다.')
        if payload.text is not None:
            note = coach.store.update(note_id, text=payload.text.strip())
        return {'note': public_note(note), 'linked': bool(payload.filename)}

    app.include_router(router)
