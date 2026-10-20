"""Diagnostic journal of the LMU frames the live coach actually read.

Each row is one normalised frame (``lmu_frame.normalise``) with the traffic
the adapter used, so ``scripts/replay_lmu_live.py`` replays a drive through
the live coach exactly as it happened. Recording is explicit and bounded.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LIMIT_SECONDS = 15 * 60
LIMIT_FRAMES = 100_000
LIMIT_BYTES = 128 * 1024 * 1024


def default_directory():
    return Path(os.environ.get('IRACING_LMU_JOURNAL_DIR') or ROOT / 'logs' / 'lmu-live')


class FrameJournal:
    def __init__(self, directory=None, clock=time.monotonic):
        self.directory = Path(directory) if directory else None
        self.clock = clock
        self.lock = threading.Lock()
        self.handle = None
        self.path = None
        self.started = None
        self.frames = 0
        self.bytes = 0
        self.reason = None

    def status(self):
        with self.lock:
            return {'recording': self.handle is not None, 'path': str(self.path) if self.path else None,
                    'frames': self.frames, 'seconds': round(self.clock() - self.started, 1) if self.handle else None,
                    'reason': self.reason}

    def start(self):
        with self.lock:
            if self.handle is not None:
                return
            directory = self.directory or default_directory()
            directory.mkdir(parents=True, exist_ok=True)
            self.path = directory / f'{uuid.uuid4().hex}.jsonl'
            self.handle = self.path.open('w', encoding='utf-8', newline='\n')
            self.started, self.frames, self.bytes, self.reason = self.clock(), 0, 0, None
            self._row({'type': 'LIFECYCLE', 'reason_code': 'STARTED'})
        return self.status()

    def stop(self, reason='STOPPED'):
        with self.lock:
            self._close(reason)
        return self.status()

    def _close(self, reason):
        if self.handle is None:
            return
        try:
            self._row({'type': 'LIFECYCLE', 'reason_code': reason})
        finally:
            self.handle.close()
            self.handle, self.reason = None, reason

    def _row(self, row):
        row = {**row, 'schema_version': 3, 'sequence': self.frames,
               'observed_elapsed_s': round(self.clock() - self.started, 3),
               'observed_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
        text = json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
        self.handle.write(text)
        self.bytes += len(text.encode('utf-8'))

    def write(self, snapshot):
        """Called from the live thread for every new frame."""
        with self.lock:
            if self.handle is None:
                return
            try:
                self._row({'type': 'SNAPSHOT', 'snapshot': snapshot})
                self.frames += 1
            except (OSError, TypeError, ValueError):
                self.handle.close()
                self.handle, self.reason = None, 'WRITE_FAILED'
                return
            if (self.clock() - self.started >= LIMIT_SECONDS or self.frames >= LIMIT_FRAMES
                    or self.bytes >= LIMIT_BYTES):
                self._close('LIMIT_REACHED')
