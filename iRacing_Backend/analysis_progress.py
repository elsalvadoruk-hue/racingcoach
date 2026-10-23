"""How far one file's analysis has come, for the picker's progress bar.

A request that wants a bar names a job (``progress_id``). The route starts the job and the
analysis marks the stage it enters (``stage``); marks outside a job do nothing. The job is
carried by a context variable, which ``asyncio.to_thread`` copies into the worker thread.

Long stages have no inner steps to count, so the bar inside a stage moves by an estimate:
seconds per valid lap learned from earlier analyses in this run of the app (DEFAULT_RATES on
the first). It never passes the stage's end before the stage really ends, and jumps there
when it does. The bar is a guide to how long is left, not a measurement.
"""
from __future__ import annotations

from contextvars import ContextVar
import threading
import time

# Stage order with seconds per valid lap seen on a 45-lap file (2026-10-23 profile).
STAGES = (
    ('load', '주행 파일 읽는 중', None),
    ('reference', '비교 기준 찾는 중', .03),     # the automatic reference is chosen before the analysis
    ('laps', '랩 나누는 중', .02),
    ('corners', '코너 측정 중', .2),
    ('handling', '차 거동 비교 중', .62),
    ('repetition', '랩 반복성 보는 중', .19),
    ('summary', '결과 정리 중', .05),
    ('comparison', '비교 그래프 만드는 중', .03),
)
DEFAULT_RATES = {key: rate for key, _, rate in STAGES if rate is not None}
LOAD_SECONDS_PER_GB = 3.
CAP = .95                    # inside a stage the estimate stops short of its end
KEEP_S = 600

_current: ContextVar = ContextVar('analysis_progress', default=None)
_jobs: dict = {}
_rates = dict(DEFAULT_RATES)
_lock = threading.Lock()


class Job:
    def __init__(self, job_id, clock=time.monotonic):
        self.id, self.clock = job_id, clock
        self.created = clock()
        self.index, self.stage_started = 0, self.created
        self.laps, self.file_gb = None, None
        self.done = False
        self.failed = False
        self.finished_at = None
        self.percent = 0
        self.lock = threading.RLock()
        with _lock:
            self.rates = dict(_rates)

    def _expected(self, key):
        if key == 'load':
            return max(.5, (self.file_gb or .3) * LOAD_SECONDS_PER_GB)
        return max(.1, self.rates.get(key, .05) * (self.laps or 20))

    def enter(self, key):
        with self.lock:
            self._enter(key)

    def _enter(self, key):
        if self.done or self.failed:
            return
        keys = [stage[0] for stage in STAGES]
        if key not in keys:
            return
        index = keys.index(key)
        if index <= self.index:
            return                     # a job starts in 'load'; repeated or earlier marks never move the bar back
        now = self.clock()
        previous = keys[self.index]
        if previous in DEFAULT_RATES and self.laps:
            measured = (now - self.stage_started) / self.laps
            with _lock:
                _rates[previous] = .5 * _rates[previous] + .5 * measured
        self.index, self.stage_started = index, now

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        now = self.finished_at if self.finished_at is not None else self.clock()
        expected = [self._expected(key) for key, _, _ in STAGES]
        total = sum(expected)
        if self.done:
            return {'percent': 100, 'stage': 'done', 'label': '완료', 'elapsed_s': round(now - self.created, 1)}
        if self.failed:
            return {'percent': self.percent, 'stage': 'error', 'label': '분석 중단',
                    'elapsed_s': round(now - self.created, 1), 'estimated': True}
        before = sum(expected[:self.index])
        inside = min(CAP * expected[self.index], now - self.stage_started)
        key, label, _ = STAGES[self.index]
        self.percent = max(self.percent, int(min(99, 100 * (before + inside) / total)))
        left = max(0., total - before - inside)
        return {'percent': self.percent, 'stage': key, 'label': label, 'elapsed_s': round(now - self.created, 1),
                'remaining_s': round(left), 'estimated': True}

    def complete(self, success):
        with self.lock:
            self._snapshot()
            self.finished_at = self.clock()
            self.done, self.failed = bool(success), not success


def start(job_id, file_bytes=None):
    """Begin a job for this request; returns the token to pass to finish()."""
    if not job_id:
        return None
    job = Job(str(job_id)[:64])
    if file_bytes:
        job.file_gb = file_bytes / 1e9
    with _lock:
        now = time.monotonic()
        for key in [key for key, item in _jobs.items() if now - item.created > KEEP_S]:
            _jobs.pop(key, None)
        _jobs[job.id] = job
    return _current.set(job)


def finish(token, *, success=True):
    if token is None:
        return
    job = _current.get()
    if job is not None:
        job.complete(success)
    _current.reset(token)


def file_size(path):
    """The recording's size sets how long reading it is expected to take."""
    job = _current.get()
    if job is not None:
        with job.lock:
            if job.index == 0:
                try:
                    job.file_gb = path.stat().st_size / 1e9
                except OSError:
                    pass


def stage(key, laps=None):
    """Mark the stage the current analysis enters. No job: nothing happens."""
    job = _current.get()
    if job is None:
        return
    with job.lock:
        if laps:
            job.laps = laps
        job.enter(key)


def status(job_id):
    with _lock:
        job = _jobs.get(str(job_id)[:64])
    return None if job is None else job.snapshot()
