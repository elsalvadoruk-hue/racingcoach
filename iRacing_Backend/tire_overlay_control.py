"""Own the optional tire window independently of the coaching HUD."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, Field, StrictInt, StrictFloat, model_validator

ROOT = Path(__file__).resolve().parents[1]
ADVICE_INTERVAL_S = 30   # recompute pressure advice at most this often for the same context
ADVICE_PIT_LAPS = 3      # say the next-stop advice when the pit wall has this many fuel laps left
ERRORS = {
    'dependencies_missing': '타이어 OCR 패키지가 없습니다. 설치.bat을 실행한 뒤 다시 켜 주세요.',
    'ocr_unavailable': 'Windows OCR을 시작하지 못했습니다. Windows 언어의 OCR 기능을 확인한 뒤 다시 켜 주세요.',
    'process_exited': '타이어 오버레이가 종료되었습니다. 다시 켜기를 눌러 주세요.',
}


class TireOverlayController:
    def __init__(self, path=None, *, popen=subprocess.Popen, game='iracing', audio=None, run_link=lambda snapshot:'', driving_context=lambda snapshot:None,
                 pit_window=lambda:None):
        if game not in ('iracing', 'lmu'):
            raise ValueError('Unsupported tire game')
        self.game, self.audio = game, audio
        self.run_link = run_link
        self.driving_context = driving_context
        # Laps of fuel left from the pit wall's own calculation, for timing the next-stop call.
        self.pit_window = pit_window
        self.advice, self.advice_key, self.advice_due, self.advice_busy = None, None, 0., False
        self.path = Path(path) if path is not None else None
        self.popen = popen
        self.operation_lock = threading.RLock()
        self.condition = threading.Condition(threading.RLock())
        self.process = None
        self.enabled, self.fixed, self.loaded = False, False, False
        self.voice_enabled = False
        from tire_reader.advisor import TireAdvisor
        self.advisor = TireAdvisor()
        self.snapshot = None
        self.history = None
        self.appearance = {'background_opacity': 85, 'content_opacity': 100, 'radius': 12}
        self.ready, self.error, self.current = False, None, {}
        self.sequence, self.replies = 0, {}
        self.ocr, self.ocr_install, self.digit_reader = None, None, None

    def _path(self):
        if self.path is None:
            from app_settings import data_dir
            self.path = data_dir() / 'overlay' / self.game / 'tires.json'
        return self.path

    def _load(self):
        if self.loaded:
            return
        try:
            path = self._path()
            saved = json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size < 4096 else {}
            if isinstance(saved, dict):
                self.enabled = saved.get('enabled') is True
                self.voice_enabled = saved.get('voice_enabled') is True
                self.fixed = saved.get('fixed') is True
                for key, maximum in (('background_opacity', 100), ('content_opacity', 100), ('radius', 32)):
                    value = saved.get(key)
                    if type(value) is int and 0 <= value <= maximum:
                        self.appearance[key] = value
        except (OSError, ValueError):
            pass
        self.loaded = True

    def _save(self):
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps({'enabled': self.enabled, 'voice_enabled': self.voice_enabled,
                'fixed': self.fixed, **self.appearance}), encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _position_path(self):
        return self._path().with_name('tires-position.json')

    def _targets(self):
        from tire_reader.targets import TireTargets
        return TireTargets(self._path().with_name('tires-targets.json'))

    def _state_source(self, car, targets):
        """How tire states are judged for this car; None means voice has nothing to judge."""
        from tire_reader.color_state import PROFILES
        if self.game == 'iracing' and car in PROFILES:
            return 'dash_color'
        if targets.get('pressure_low_bar') is not None or targets.get('temp_low_c') is not None:
            return 'targets'
        return None

    def status(self):
        with self.operation_lock, self.condition:
            self._load()
            running = self.process is not None and self.process.poll() is None
            car = self.current.get('car_path')
            targets = self._targets().get(car)
            from tire_reader.ocr_language import has_english
            return {'version': 1, 'supported': sys.platform == 'win32', 'enabled': self.enabled,
                    'game': self.game, 'voice_enabled': self.voice_enabled,
                    'voice_audio_ready': self.audio.ready() if self.voice_enabled and hasattr(self.audio, 'ready') else None,
                    'state_source': self._state_source(car, targets) if car else None,
                    'ocr_language': self.ocr['language'] if self.ocr else None,
                    'ocr_english': has_english(self.ocr['languages']) if self.ocr else None,
                    'ocr_install': self.ocr_install,
                    'digit_reader': self.digit_reader,
                    'advice': self.advice,
                    'advice_audio_ready': self.audio.advice_ready() if self.voice_enabled and hasattr(self.audio, 'advice_ready') else None,
                    'targets': targets,
                    'target_temperature_kind': next((v.temp_kind for v in self.snapshot.wheels.values() if v.temp_kind), None) if self.snapshot else None,
                    'history_error': bool(self.history and self.history.error),
                    'coaching': dict(self.advisor.coaching.view),
                    'voice_event': self.advisor.current(time.monotonic()) if self.voice_enabled and running else None,
                    'fixed': self.fixed, **self.appearance, 'running': running, 'ready': self.ready and running,
                    **self.current, 'visible': self.enabled and running and self.current.get('visible') is True,
                    'error': self.error, 'unit': 'bar', 'temperature_unit': 'C', 'interval_s': 1, 'warmup_s': 10}

    def _read(self, process):
        try:
            for line in process.stdout:
                if len(line) > 16384:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                with self.condition:
                    if self.process is not process:
                        return
                    kind = message.get('type')
                    if kind == 'ready':
                        self.ready = True
                        tags = message.get('ocr_languages')
                        tags = [t[:16] for t in tags if isinstance(t, str)][:16] if isinstance(tags, list) else None
                        self.ocr = {'language': message['ocr_language'][:16] if isinstance(message.get('ocr_language'), str) else None,
                                    'languages': tags} if tags is not None else None
                    elif kind == 'state':
                        from tire_reader.snapshot import TireSnapshot
                        data = message.get('tire_snapshot')
                        previous = self.snapshot
                        raw = None
                        self.snapshot = None
                        if isinstance(data, dict):
                            try:
                                raw = TireSnapshot.from_dict(data)
                                self.snapshot = self._targets().apply(raw)
                            except (ValueError, KeyError, TypeError):
                                pass
                        context = self.driving_context(self.snapshot) if self.snapshot else None
                        self.advisor.observe(self.snapshot.to_dict() if self.snapshot else None,
                                             enabled=self.voice_enabled, context=context, analyze=self.enabled)
                        self._schedule_advice()
                        if self.snapshot is not None or previous is not None:
                            from tire_reader.history import TireHistoryWriter
                            from tire_reader.log_export import diagnostics
                            if self.history is None:
                                self.history = TireHistoryWriter(self._path().with_name('tires-history.sqlite3'))
                            selected = self.snapshot or previous
                            detail=diagnostics(message,self.snapshot,raw,self._targets().get(selected.car),
                                    enabled=self.enabled,voice_enabled=self.voice_enabled)
                            detail['coaching']=dict(self.advisor.coaching.view)
                            self.history.submit(self.snapshot,run_id=self.run_link(selected),diagnostics=detail)
                        self.current = {key: message.get(key) for key in
                            ('visible', 'status', 'warmup_remaining_s', 'car_name', 'pressure',
                             'temperature', 'display_status', 'temperature_kind', 'car_path', 'measurement')}
                        digits = message.get('digit_reader')
                        if isinstance(digits, dict):
                            self.digit_reader = {'mode': digits.get('mode') if digits.get('mode') in ('learning', 'self') else None,
                                **{k: digits.get(k) for k in ('compared', 'needed', 'digits_learned', 'values_seen',
                                   'values_needed', 'verify_disagreements') if type(digits.get(k)) is int},
                                'agreement': digits.get('agreement') if isinstance(digits.get('agreement'), float) else None}
                    elif kind == 'fatal':
                        self.error = ERRORS.get(message.get('code'), ERRORS['ocr_unavailable'])
                    elif kind == 'reply' and type(message.get('id')) is int:
                        self.replies[message['id']] = message.get('ok') is True
                    elif kind == 'user_disabled':
                        self.enabled = False
                        try:
                            self._save()
                        except OSError:
                            self.error = '타이어 오버레이 설정을 저장하지 못했습니다.'
                        threading.Thread(target=self.update, kwargs={'enabled': False}, daemon=True).start()
                    self.condition.notify_all()
        finally:
            with self.condition:
                if self.process is process:
                    self.ready = False
                    self.current = {}
                    if (self.enabled or self.voice_enabled) and not self.error:
                        self.error = ERRORS['process_exited']
                    self.condition.notify_all()

    def _start(self):
        if sys.platform != 'win32':
            raise RuntimeError('타이어 OCR 오버레이는 Windows에서 사용할 수 있습니다.')
        if self.process is not None:
            if self.process.poll() is None and self.ready:
                return
            self._stop()
        executable = Path(sys.executable)
        windowless = executable.with_name('pythonw.exe')
        if windowless.is_file():
            executable = windowless
        self.error, self.current, self.ready = None, {}, False
        args = [str(executable), str(ROOT / 'scripts' / 'tire_overlay.py'),
                '--position', str(self._position_path()), '--game', self.game]
        if not self.enabled:
            args.append('--hidden')
        if self.fixed:
            args.append('--fixed')
        for key, value in self.appearance.items():
            args.extend(['--' + key.replace('_', '-'), str(value)])
        try:
            process = self.popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:
            self.error = str(error)
            raise
        with self.condition:
            self.process = process
            threading.Thread(target=self._read, args=(process,), name='tire-overlay-state', daemon=True).start()
            deadline = time.monotonic() + 12
            while not self.ready and not self.error and process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, .2))
            started = self.ready
            error = self.error or '타이어 오버레이 시작 시간이 초과되었습니다. 다시 켜 주세요.'
        if not started:
            self._stop()
            self.error = error
            raise RuntimeError(error)

    def _command(self, action, **payload):
        with self.condition:
            process = self.process
            if process is None or process.poll() is not None or not self.ready:
                raise RuntimeError(ERRORS['process_exited'])
            self.sequence += 1
            ticket = self.sequence
            try:
                process.stdin.write(json.dumps({'id': ticket, 'action': action, **payload}) + '\n')
                process.stdin.flush()
            except (OSError, ValueError) as error:
                raise RuntimeError(ERRORS['process_exited']) from error
            deadline = time.monotonic() + 3
            while ticket not in self.replies and process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, .1))
            if not self.replies.pop(ticket, False):
                raise RuntimeError('타이어 오버레이에 설정을 적용하지 못했습니다. 다시 켜 주세요.')

    def _stop(self):
        with self.condition:
            process, self.process = self.process, None
            self.ready, self.current, self.replies = False, {}, {}
            self.snapshot = None
            self.advisor.reset()
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()  # EOF closes only this owned native window.
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            finally:
                if process.stdout:
                    process.stdout.close()

    def start(self):
        with self.operation_lock:
            self._load()
            if self.enabled or self.voice_enabled:
                try:
                    self._start()
                except (OSError, RuntimeError) as error:
                    self.error = str(error)

    def update(self, *, enabled=None, voice_enabled=None, fixed=None, background_opacity=None, content_opacity=None, radius=None):
        with self.operation_lock:
            self._load()
            appearance = {key: value for key, value in dict(background_opacity=background_opacity,
                content_opacity=content_opacity, radius=radius).items() if value is not None}
            target_enabled = self.enabled if enabled is None else enabled
            target_voice = self.voice_enabled if voice_enabled is None else voice_enabled
            if not (target_enabled or target_voice):
                self._stop()
            if fixed is not None and fixed != self.fixed:
                if self.process is not None and self.process.poll() is None:
                    self._command('fixed', fixed=fixed)
                self.fixed = fixed
            if appearance:
                if self.process is not None and self.process.poll() is None:
                    self._command('appearance', **appearance)
                self.appearance.update(appearance)
            previous = self.enabled, self.voice_enabled
            already_running = self.process is not None and self.process.poll() is None and self.ready
            self.enabled, self.voice_enabled = target_enabled, target_voice
            try:
                if target_enabled or target_voice:
                    self._start()
                    if already_running and previous[0] != target_enabled:
                        self._command('display', enabled=target_enabled)
            except (OSError, RuntimeError):
                self.enabled, self.voice_enabled = previous
                raise
            if not self.voice_enabled:
                self.advisor.reset()
            self._save()
            if not (target_enabled or target_voice):
                self.error = None
            return self.status()

    def reset_position(self):
        with self.operation_lock:
            if self.process is not None and self.process.poll() is None:
                self._command('reset_position')
            else:
                from tire_reader.positions import TirePositions
                TirePositions(self._position_path()).reset()
            return self.status()

    def close(self):
        with self.operation_lock:
            self._stop()  # Preserve the user's enabled setting for the next app start.
            if self.history is not None:
                self.history.close()
                self.history = None

    def _advice_kind(self, s):
        if self.game != 'iracing' or s is None or not s.car or not s.track:
            return None
        if s.in_garage is True:
            return 'garage'
        if s.active and s.on_pit_road is False and s.session_type in ('Race', 'Practice', 'Offline Testing'):
            return 'next_pit'
        return None

    def _schedule_advice(self):
        """Called with the condition held; the database work runs on its own thread."""
        s = self.snapshot
        kind = self._advice_kind(s)
        if kind is None:
            return
        key = (kind, s.car, s.track, s.session_key, tuple(sorted(s.installed_cold_kpa.items())),
               (s.pit_snapshot or {}).get('stop'))
        now = time.monotonic()
        if self.advice_busy or key == self.advice_key and now < self.advice_due:
            return
        self.advice_busy, self.advice_key, self.advice_due = True, key, now + ADVICE_INTERVAL_S
        threading.Thread(target=self._compute_advice, args=(s, kind), name='tire-advice', daemon=True).start()

    def _compute_advice(self, s, kind):
        from tire_reader.pressure_advice import recommendation
        try:
            result = recommendation(self._path().with_name('tires-history.sqlite3'), game=s.game, car=s.car,
                track=s.track, targets=self._targets().get(s.car), fixed_setup=s.fixed_setup,
                track_temp=s.track_temp_c, kind=kind, installed=s.installed_cold_kpa,
                installed_sources=s.installed_cold_source, forecast=None)
        except Exception as error:  # advice must never stop measurement or voice
            result = {'kind': kind, 'status': 'withheld', 'reason': 'advice_error', 'error_type': type(error).__name__}
        laps = None
        if kind == 'next_pit' and result.get('status') == 'ready':
            try:
                laps = (self.pit_window() or {}).get('fuel_laps')
            except Exception:
                laps = None
        with self.condition:
            self.advice_busy = False
            self.advice = {**result, 'car': s.car, 'fuel_laps': laps if isinstance(laps, (int, float)) else None}
            ready = result.get('status') == 'ready'
            if ready and self.voice_enabled and kind == 'garage':
                self.advisor.offer('advice_garage', s.session_key, garage=True, game=s.game)
            elif ready and self.voice_enabled and isinstance(laps, (int, float)) and laps <= ADVICE_PIT_LAPS:
                self.advisor.offer('advice_next_pit', f"{s.session_key}:{(s.pit_snapshot or {}).get('stop')}",
                                   garage=False, game=s.game)
            self._send_advice(result if kind == 'garage' and ready else None)

    def _send_advice(self, result):
        """Garage numbers for the tire window (bar, two decimals). Fire-and-forget; caller holds the condition."""
        process = self.process
        if process is None or process.poll() is not None or not self.ready:
            return
        values = ({w: (f"{v['cold_kpa'] / 100:.2f}" if isinstance(v.get('cold_kpa'), (int, float)) else None)
                   for w, v in result['wheels'].items()} if result else None)
        try:
            process.stdin.write(json.dumps({'id': 0, 'action': 'advice', 'values': values}) + '\n')
            process.stdin.flush()
        except (OSError, ValueError):
            pass

    def install_english_ocr(self):
        """Only on the user's click: UAC prompt, then Windows installs in its own window."""
        if self.game != 'iracing':
            raise ValueError('iRacing 화면 측정에서만 사용합니다.')
        from tire_reader.ocr_language import request_install
        try:
            request_install(wait=False)  # No lock held while the UAC prompt is open.
        except RuntimeError:
            with self.operation_lock:
                self.ocr_install = 'not_started'
            raise
        with self.operation_lock:
            self.ocr_install = 'requested'
            return self.status()

    def recheck_ocr(self):
        """A new measurement process reads the OCR languages Windows now has."""
        with self.operation_lock:
            self._load()
            if self.enabled or self.voice_enabled:
                self._stop()
                self._start()
            self.ocr_install = None
            return self.status()

    def reset_digit_learning(self):
        with self.operation_lock:
            car = self.current.get('car_path')
            if not car:
                raise ValueError('측정 중인 차량이 없습니다.')
            self._command('reset_digits', car=car)
            return self.status()

    def render_voice(self, identifier):
        with self.operation_lock, self.condition:
            event = self.advisor.current(time.monotonic()) if self.voice_enabled else None
            if not event or event['id'] != identifier or self.audio is None:
                return {'event': None}
            text = event['text']
        try:
            encoded = self.audio(text)  # Prepared clips only; no runtime synthesis.
        except (OSError, ValueError, KeyError):
            return {'event': None, 'missing_prepared_audio': True}
        with self.operation_lock, self.condition:
            current = self.advisor.current(time.monotonic()) if self.voice_enabled else None
            if not current or current['id'] != identifier:
                return {'event': None}
            return {'event': {**current,'run_id':self.run_link(self.snapshot)}, 'audio_base64': encoded, 'audio_mime': 'audio/mpeg'}

    def acknowledge_voice(self, identifier):
        with self.operation_lock, self.condition:
            return {'accepted': self.advisor.ack(identifier, time.monotonic()) if self.voice_enabled else False}

    def update_targets(self, body):
        with self.operation_lock, self.condition:
            if body.car != self.current.get('car_path'):
                raise ValueError('차량이 바뀌었습니다. 현재 차량을 확인한 뒤 저장해 주세요.')
            kind = next((v.temp_kind for v in self.snapshot.wheels.values() if v.temp_kind), None) if self.snapshot else None
            if body.temp_low_c is not None and not kind:
                raise ValueError('이 차량의 타이어 온도를 확인한 뒤 저장해 주세요.')
            values = body.model_dump(exclude={'car'}, exclude_none=True)
            if body.temp_low_c is not None:
                values['temperature_kind'] = kind
            self._targets().save(body.car, values)
            self.advisor.reset()
            return self.status()


class TireOverlays:
    """Separate settings, windows and position maps for the two simulators."""
    def __init__(self, *, audio=None, run_link=lambda snapshot:'', driving_context=lambda snapshot:None, pit_window=lambda:None):
        self.controllers = {game: TireOverlayController(game=game, audio=audio,run_link=run_link,
            driving_context=driving_context, pit_window=pit_window) for game in ('iracing', 'lmu')}

    def log_extra(self, run):
        from session_log import Extra
        from tire_reader.log_export import tire_data
        controller = self.controllers.get(run.get('game'))
        if controller is None:
            return Extra('tire_data',info={'reason':'unknown_game'})
        # No controller/live lock held while waiting for the bounded writer.
        writer = controller.history
        extra = tire_data(controller._path().with_name('tires-history.sqlite3'),
                          prepare=writer.flush if writer else lambda:True)(run)
        extra.info['collection_current']={'scope':'current_at_export',
            'display_enabled':controller.enabled,'voice_enabled':controller.voice_enabled,
            'writer_error':bool(writer and writer.error),'writer_dropped_total':writer.dropped if writer else 0}
        return extra

    def start(self):
        for controller in self.controllers.values():
            controller.start()

    def close(self):
        for controller in self.controllers.values():
            controller.close()


class TireSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool | None = None
    voice_enabled: StrictBool | None = None
    fixed: StrictBool | None = None
    background_opacity: StrictInt | None = Field(None, ge=0, le=100)
    content_opacity: StrictInt | None = Field(None, ge=0, le=100)
    radius: StrictInt | None = Field(None, ge=0, le=32)


class TireTargetSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    car: str = Field(min_length=1, max_length=180)
    pressure_low_bar: StrictFloat | None = Field(None, ge=.4, le=4)
    pressure_high_bar: StrictFloat | None = Field(None, ge=.4, le=4)
    temp_low_c: StrictFloat | None = Field(None, ge=-50, le=250)
    temp_high_c: StrictFloat | None = Field(None, ge=-50, le=250)

    @model_validator(mode='after')
    def ranges(self):
        for low, high in ((self.pressure_low_bar,self.pressure_high_bar),(self.temp_low_c,self.temp_high_c)):
            if (low is None) != (high is None) or low is not None and low >= high:
                raise ValueError('하한과 상한을 함께 입력하고 상한을 더 크게 지정해 주세요.')
        return self


def routes(controller):
    router = APIRouter(prefix='/api/tire-overlay', tags=['Additional tire overlay'])

    def selected(game):
        return controller.controllers[game] if isinstance(controller, TireOverlays) else controller

    @router.get('/voice/{identifier}/render')
    def render_voice(identifier: str, game: Literal['iracing', 'lmu'] = 'iracing'):
        return selected(game).render_voice(identifier)

    @router.post('/voice/{identifier}/ack')
    def acknowledge_voice(identifier: str, game: Literal['iracing', 'lmu'] = 'iracing'):
        return selected(game).acknowledge_voice(identifier)

    @router.get('')
    def status(game: Literal['iracing', 'lmu'] = 'iracing'):
        return selected(game).status()

    @router.put('/settings')
    def settings(body: TireSettings, game: Literal['iracing', 'lmu'] = 'iracing'):
        try:
            return selected(game).update(**body.model_dump(exclude_none=True))
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.put('/targets')
    def targets(body: TireTargetSettings, game: Literal['iracing', 'lmu'] = 'iracing'):
        try:
            return selected(game).update_targets(body)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post('/ocr-language/install')
    def install_english_ocr(game: Literal['iracing', 'lmu'] = 'iracing'):
        try:
            return selected(game).install_english_ocr()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post('/ocr-language/recheck')
    def recheck_ocr(game: Literal['iracing', 'lmu'] = 'iracing'):
        try:
            return selected(game).recheck_ocr()
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post('/digits/reset')
    def reset_digits(game: Literal['iracing', 'lmu'] = 'iracing'):
        try:
            return selected(game).reset_digit_learning()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post('/reset-position')
    def reset_position(game: Literal['iracing', 'lmu'] = 'iracing'):
        try:
            return selected(game).reset_position()
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return router
