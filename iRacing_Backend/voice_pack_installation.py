"""Small in-memory installation status; reading it never takes the audio-library lock."""
import re
import threading
import time
import uuid


class InstallBusy(ValueError):
    pass


class InstallProgress:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.value = {'state': 'IDLE'}

    def begin(self, operation_id=None, stage='checking', kind='install'):
        ident = operation_id or uuid.uuid4().hex
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}', ident):
            raise ValueError('설치 요청 ID를 확인하세요.')
        with self.lock:
            if self.value['state'] == 'RUNNING':
                raise InstallBusy('음성팩 설치 또는 목소리 적용 중입니다. 현재 작업이 끝날 때까지 기다려 주세요.')
            now = self.clock()
            self.value = dict(id=ident, kind=kind, state='RUNNING', stage=stage, done=0, total=None,
                              bytes_done=0, started=now, changed=now, progressed=now)
        return ident

    def update(self, ident, stage, **values):
        with self.lock:
            if self.value.get('id') != ident or self.value['state'] != 'RUNNING':
                return
            now = self.clock()
            if self.value['stage'] != stage:
                self.value.update(stage=stage, changed=now, done=0, total=None, bytes_done=0)
            self.value.update(values, progressed=now)

    def complete(self, ident, imported=None, **values):
        self._finish(ident, 'COMPLETE', **({'imported': imported} if imported else {}), **values)

    def install(self, ident, manager, source, cleanup=False):
        try:
            result = manager.install(source, lambda stage, **values: self.update(ident, stage, **values))
            self.complete(ident, result)
            return result
        except Exception as exc:
            self.fail(ident, exc)
            raise
        finally:
            if cleanup:
                from pathlib import Path
                Path(source).unlink(missing_ok=True)

    def fail(self, ident, error):
        self._finish(ident, 'FAILED', error=str(error))

    def _finish(self, ident, state, **values):
        with self.lock:
            if self.value.get('id') == ident and self.value['state'] == 'RUNNING':
                self.value.update(values, state=state, ended=self.clock())

    def snapshot(self):
        with self.lock:
            value = dict(self.value)
        if value['state'] == 'IDLE':
            return value
        now = value.get('ended', self.clock())
        value.update(elapsed_s=max(0, now-value['started']),
                     stage_elapsed_s=max(0, now-value['changed']),
                     last_progress_s=max(0, now-value['progressed']))
        for key in ('started', 'changed', 'progressed', 'ended'):
            value.pop(key, None)
        return value
