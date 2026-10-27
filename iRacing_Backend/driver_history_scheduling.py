"""Give live driving and foreground analysis priority over historical imports."""
from contextlib import contextmanager
import ctypes
import os
import threading
import time


def is_foreground_analysis(method, path):
    if method == 'POST':
        return (path in {'/api/analyze', '/api/lap-overlay', '/api/comparison-detail',
                         '/api/lmu/telemetry/import', '/api/lmu/telemetry/replay',
                         '/api/lmu/telemetry/corner-consistency'}
                or path.startswith(('/api/workspace/', '/api/lmu/workspace/'))
                or (path.startswith('/api/lmu/practice/tasks/') and path.endswith('/compare')))
    return method == 'GET' and path in {
        '/api/file-laps', '/api/channels', '/api/lmu/telemetry/file',
        '/api/lmu/telemetry/lap-analysis', '/api/lmu/telemetry/vehicle-channels',
        '/api/lmu/telemetry/vehicle-comparison', '/api/lmu/telemetry/setup',
        '/api/lmu/telemetry/setup-comparison', '/api/lmu/telemetry/compare-files',
        '/api/lmu/telemetry/compare'}


class SystemPressure:
    """One-second OS samples; no new runtime dependency or SDK polling."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._at = float('-inf')
        self._previous = None
        self._busy = False

    def __call__(self):
        now = self.clock()
        if now - self._at < 1.:
            return self._busy
        self._at = now
        if os.name != 'nt':
            return False
        from ctypes import wintypes

        class Memory(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in ('total_phys','available_phys','total_page','available_page',
                                                       'total_virtual','available_virtual','extended')]
        try:
            idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
            cpu_busy = False
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                value = lambda item: (item.dwHighDateTime << 32) | item.dwLowDateTime
                current = (value(idle), value(kernel) + value(user))
                if self._previous is not None:
                    idle_delta, total_delta = (current[index]-self._previous[index] for index in (0,1))
                    cpu_busy = total_delta > 0 and 1. - idle_delta/total_delta >= .75
                self._previous = current
            memory = Memory()
            memory.length = ctypes.sizeof(memory)
            memory_busy = bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
                and memory.available_phys < max(512*1024**2, min(2*1024**3, memory.total_phys*.1)))
            self._busy = cpu_busy or memory_busy
        except (AttributeError, OSError, ValueError):
            self._busy = False
        return self._busy


class HistoryActivity:
    def __init__(self, *, live_state, pressure=None, clock=time.monotonic, idle_seconds=10.):
        self.live_state, self.clock = live_state, clock
        self.pressure = pressure if pressure is not None else SystemPressure(clock)
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._requests = 0
        self._quiet_after = float('-inf')
        self._completion = None
        self._was_driving = False

    @contextmanager
    def foreground(self, *, settle=True):
        with self._lock:
            self._requests += 1
        try:
            yield
        finally:
            with self._lock:
                self._requests -= 1
                # File catalogs poll every five seconds even on the report.
                # Yield during their reads without perpetually resetting idle.
                if settle:
                    self._quiet_after = self.clock() + self.idle_seconds

    def reason(self):
        with self._lock:
            now = self.clock()
            try:
                live = self.live_state()
            except Exception:
                return 'DRIVING'
            completion = (live.get('lifecycle_id'), live.get('completed_serial'))
            if live.get('active_drive'):
                self._was_driving = True
                self._completion = completion
                return 'DRIVING'
            if self._was_driving or (self._completion is not None and self._completion != completion):
                self._quiet_after = now + self.idle_seconds
            self._was_driving = False
            self._completion = completion
            if self._requests:
                return 'ANALYZING'
            if self.pressure():
                self._quiet_after = now + self.idle_seconds
                return 'RESOURCE_BUSY'
            return 'COOLDOWN' if now < self._quiet_after else None
