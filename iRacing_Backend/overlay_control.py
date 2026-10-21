"""Own the native HUD, driving visibility and offline preview over private pipes."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from overlay_preferences import PreferencesStore, default_path, settings_path, car_key
from overlay_visibility import VisibilityStore, driving, preparing
from overlay_cards import SessionCards

ROOT = Path(__file__).resolve().parents[1]
# The SDK reader produces live samples at 30 Hz.  Keeping the HUD transport at
# that cadence prevents the graph from advancing in visible 100 ms steps.
OVERLAY_UPDATE_INTERVAL_S = 1 / 30


class OverlayController:
    def __init__(self, path=None, reference=None):
        self.path = Path(path) if path is not None else None
        self.reference = reference
        self.cards = SessionCards(footer=True)
        self.process = None
        self.condition = threading.Condition(threading.RLock())
        self.operation_lock = threading.RLock()
        self.current = None
        self.sequence = 0
        self.replies = {}
        self.error = None
        self.preview = False
        self.latest = {'connected': False}
        self.auto_show = None
        self.monitor_stop = threading.Event()
        self.monitor = None
        self.suspended = False
        self.log_directory = None
        self.settings_target = None
        self.selected_game = 'iracing'
        self.car_name = None

    def _target(self, identity):
        game = identity.get('game') or ('iracing' if car_key(identity) else self.selected_game)
        return ('lmu' if game == 'lmu' else 'iracing', car_key(identity))

    def _select_target(self, identity):
        target = self._target(identity)
        if target != self.settings_target:
            if self.preview:
                return
            if self.process is not None and self.process.poll() is None:
                self.stop()
            old_game = (self.settings_target or ('iracing', None))[0]
            self.settings_target = target
            self.cards.reset()
            if target[0] != old_game:
                self.auto_show = None
        self.car_name = identity.get('car_name') or identity.get('car_path')

    def select_game(self, game):
        with self.operation_lock:
            self.selected_game = 'lmu' if game == 'lmu' else 'iracing'
            self._select_target(self.latest.get('identity') or {})

    def visibility_store(self):
        legacy = self.path or default_path()
        game = (self.settings_target or (self.selected_game, None))[0]
        return VisibilityStore(settings_path(legacy.parent, game).with_name('visibility.json'),
                               legacy.with_name('overlay-visibility.json'))

    def enabled(self):
        if self.auto_show is None:
            self.auto_show = self.visibility_store().load()
        return self.auto_show

    def store(self):
        legacy = self.path or default_path()
        game, key = self.settings_target or (self.selected_game, None)
        path = settings_path(legacy.parent, game, key)
        return PreferencesStore(path, [settings_path(legacy.parent, game), legacy])

    def status(self):
        with self.condition:
            running = self.process is not None and self.process.poll() is None
            store = self.store()
            # The child owns the current appearance. Avoid rereading the settings
            # file several times per live frame when its state is already known.
            saved = store.load() if not (running and self.current) else None
            return {"supported": os.name == "nt", "running": running,
                    "ready": running and self.current is not None,
                    **(self.current if running and self.current else {"settings": saved, "goal": "abs", "scene": "during"}),
                    "auto_show": self.enabled(), "preview": self.preview and running,
                    "settings_target": {"game": (self.settings_target or (self.selected_game, None))[0],
                                        "car_key": (self.settings_target or (self.selected_game, None))[1],
                                        "car_name": self.car_name},
                    "driving": not self.suspended and driving(self.latest), "suspended": self.suspended,
                    "visible": bool(running and self.current and self.current.get('visible')),
                    "reference": self.reference.status() if self.reference else None,
                    "practice": self.latest.get('practice'), "session": self.latest.get('session'),
                    "error": self.error or store.load_error}

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self.condition:
                    if process is not self.process:
                        return
                    self.current = message["state"]
                    if message.get("id") is not None:
                        self.replies[message["id"]] = message.get("error")
                    self.condition.notify_all()
        except (OSError, ValueError):
            # Closing the owned process can also close a reader at EOF.
            pass
        finally:
            with self.condition:
                if process is self.process:
                    self.condition.notify_all()

    def _start_child(self):
        with self.operation_lock:
            if os.name != "nt":
                raise RuntimeError("오버레이는 Windows에서 사용할 수 있습니다.")
            if importlib.util.find_spec("PIL") is None:
                raise RuntimeError("오버레이 패키지가 없습니다. scripts/requirements_overlay.txt를 설치하세요.")
            with self.condition:
                if self.process is not None and self.process.poll() is None:
                    return self.status()
                self.current, self.error, self.replies = None, None, {}
                executable = Path(sys.executable).with_name("pythonw.exe")
                command = [str(executable if executable.exists() else sys.executable),
                           str(ROOT / "scripts/overlay_sample.py"), "--managed", "--paused", "--hidden",
                           "--settings", str(self.store().path)]
                log_dir = self.log_directory or ROOT / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                with (log_dir/"overlay-web.stderr.log").open("w", encoding="utf-8") as error_log:
                    self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                                                    stdout=subprocess.PIPE, stderr=error_log, text=True,
                                                    encoding="utf-8", bufsize=1,
                                                    creationflags=subprocess.CREATE_NO_WINDOW)
                process = self.process
                threading.Thread(target=self._read, args=(process,), name="overlay-state", daemon=True).start()
                deadline = monotonic()+8
                while self.current is None and process.poll() is None and monotonic() < deadline:
                    self.condition.wait(timeout=min(0.1, max(0, deadline-monotonic())))
                ready = self.current is not None
            if not ready:
                self.stop()
                raise RuntimeError("오버레이 미리보기를 열지 못했습니다. logs/overlay-web.stderr.log를 확인하세요.")
            return self.status()

    def start(self):
        with self.operation_lock:
            if self.suspended:
                raise RuntimeError('현재 선택한 게임에서 오버레이를 여세요.')
            self._select_target(self.latest.get('identity') or {})
            self._start_child()
            self.command('display', {'mode': 'preview', 'visible': True})
            self.preview = True
            return self.status()

    def end_preview(self):
        with self.operation_lock:
            self.preview = False
            if not (self.enabled() and driving(self.latest)):
                return self.stop()
            return self.sync(self.latest)

    def set_visibility(self, enabled):
        with self.operation_lock:
            # This preference must never save uncommitted appearance edits.
            self.visibility_store().save(enabled)
            self.auto_show = enabled
            return self.sync(self.latest)

    def delete_car_settings(self):
        with self.operation_lock:
            if not self.settings_target or not self.settings_target[1]:
                raise RuntimeError('차량을 확인한 뒤 차량 설정을 지울 수 있습니다.')
            self.stop()
            self.store().path.unlink(missing_ok=True)
            return self.status()

    def sync(self, view):
        with self.operation_lock:
            if self.suspended:
                self.latest = {'connected': False, 'snapshot': None, 'points': []}
                self.cards.reset()
                return self.status()
            self.latest = view
            running = self.process is not None and self.process.poll() is None
            if self.preview and not running:
                self.preview = False
            self._select_target(view.get('identity') or {})
            running = self.process is not None and self.process.poll() is None
            show = self.enabled() and driving(view)
            if self.reference:
                self.reference.observe(view, self.enabled() and preparing(view) and not self.preview)
            if self.preview:
                self.cards.reset()
                return self.status()
            if show:
                if self.reference:
                    view = self.reference.enrich(view)
                card = self.cards.select(view)
                self._start_child()
                self.command('display', {'mode': 'live', 'visible': True,
                                        'run_id': view.get('run_id'), 'snapshot': view.get('snapshot'),
                                        'points': view.get('points', []),
                                        'practice': view.get('practice'), 'session': view.get('session'),
                                        'reference_status': view.get('reference_status'),
                                        'feedback': view.get('feedback'),
                                        'gear_comparison': view.get('gear_comparison'),
                                        'card': card,
                                        'comparison': view.get('comparison')})
            else:
                self.cards.reset()
                if running and self.current and self.current.get('visible'):
                    self.command('display', {'mode': 'live', 'visible': False})
            return self.status()

    def set_suspended(self, enabled):
        """Pause this controller and close only its owned HUD, preserving preferences."""
        with self.operation_lock:
            self.suspended = bool(enabled)
            if self.suspended:
                self.latest = {'connected': False, 'snapshot': None, 'points': []}
                self.cards.reset()
                return self.stop()
            return self.status()

    def start_monitor(self, provider):
        if self.monitor and self.monitor.is_alive():
            return
        self.monitor_stop.clear()

        def watch():
            while not self.monitor_stop.is_set():
                started = monotonic()
                try:
                    self.sync(provider())
                except Exception:
                    logging.getLogger('iRacingOverlay').exception('Overlay update failed')
                    self.monitor_stop.wait(1)
                # Calculation, transport and the child's reply belong inside the
                # frame budget, not before another full 33 ms delay.
                self.monitor_stop.wait(max(0., OVERLAY_UPDATE_INTERVAL_S-(monotonic()-started)))

        self.monitor = threading.Thread(target=watch, name='overlay-visibility', daemon=True)
        self.monitor.start()

    def close(self):
        self.monitor_stop.set()
        if self.monitor:
            self.monitor.join(timeout=10)
        if self.reference:
            self.reference.close()
        return self.stop()

    def command(self, action, values):
        with self.operation_lock:
            if action == 'demo' and not self.preview:
                raise RuntimeError('샘플 장면은 미리보기에서만 사용할 수 있습니다.')
            with self.condition:
                process = self.process
                if process is None or process.poll() is not None:
                    raise RuntimeError("먼저 오버레이 미리보기를 켜세요.")
                self.sequence += 1
                request_id = self.sequence
                try:
                    process.stdin.write(json.dumps({"id": request_id, "action": action, "values": values})+"\n")
                    process.stdin.flush()
                except (OSError, ValueError) as error:
                    raise RuntimeError("오버레이 연결이 종료되었습니다. 미리보기를 다시 켜세요.") from error
                deadline = monotonic()+4
                while request_id not in self.replies and process.poll() is None and monotonic() < deadline:
                    self.condition.wait(timeout=min(0.1, max(0, deadline-monotonic())))
                if request_id not in self.replies:
                    raise RuntimeError("오버레이가 응답하지 않았습니다. 미리보기를 다시 켜세요.")
                error = self.replies.pop(request_id)
                if error:
                    raise RuntimeError(error)
                return self.status()

    def stop(self):
        with self.operation_lock:
            process = self.process
            if process is not None:
                if process.poll() is None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=3)
                process.stdout.close()
                if not process.stdin.closed:
                    process.stdin.close()
            with self.condition:
                self.process = None
                self.current = None
                self.preview = False
            return self.status()


class AppearanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    width: int | None = Field(None, ge=75, le=200)
    height: int | None = Field(None, ge=75, le=200)
    x: int | None = Field(None, ge=-100000, le=100000)
    y: int | None = Field(None, ge=-100000, le=100000)
    content_opacity: int | None = Field(None, ge=0, le=100)
    background_opacity: int | None = Field(None, ge=0, le=100)


class FixedBody(BaseModel):
    fixed: bool


class VisibilityBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


class DemoBody(BaseModel):
    goal: Literal["abs", "throttle", "release", "coast", "brake", "relift", "steering"] | None = None
    scene: Literal["before", "during", "result", "missing"] | None = None
    celebrate: bool = False


def routes(controller, prefix="/api/overlay"):
    router = APIRouter(prefix=prefix)

    def call(game, operation, *args):
        try:
            with controller.operation_lock:
                controller.select_game(game)
                return operation(*args)
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("")
    def status(game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.status)

    @router.post("/preview")
    def start(game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.start)

    @router.delete("/preview")
    def stop(game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.end_preview)

    @router.put('/visibility')
    def visibility(body: VisibilityBody, game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.set_visibility, body.enabled)

    @router.put("/settings")
    def appearance(body: AppearanceBody, game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.command, "settings", body.model_dump(exclude_none=True))

    @router.delete('/settings/car')
    def delete_car(game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.delete_car_settings)

    @router.post("/fixed")
    def fixed(body: FixedBody, game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.command, "fixed", {"fixed": body.fixed})

    @router.post("/demo")
    def demo(body: DemoBody, game: Literal['iracing', 'lmu'] = 'iracing'):
        return call(game, controller.command, "demo", body.model_dump(exclude_none=True))

    return router
