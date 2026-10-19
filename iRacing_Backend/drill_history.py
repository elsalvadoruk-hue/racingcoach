"""Append-only drill results; disk writes run outside the SDK reader thread."""
from copy import deepcopy
import json
import logging
from pathlib import Path
from queue import Queue
import threading


class DrillHistory:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.runs = {}
        self.error = None
        self.queue = Queue()
        self.worker = None
        try:
            self._load()
        except OSError:
            self.error = '연습 기록 파일을 읽지 못했습니다. 데이터 폴더를 확인해 주세요.'

    def _load(self):
        if self.path and self.path.exists():
            with self.path.open(encoding='utf-8') as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        self._apply(json.loads(line))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        # An interrupted final write must not discard earlier attempts.
                        self.error = '연습 기록 일부를 읽지 못했습니다. 이전 기록은 보존됩니다.'
            for run in self.runs.values():
                if run['outcome'] == 'IN_PROGRESS':
                    run['outcome'] = 'INTERRUPTED'

    def _apply(self, event):
        key = event['id']
        if event['kind'] == 'start':
            if not isinstance(event.get('plan'), dict) or not isinstance(event.get('at'), (int, float)):
                raise ValueError('Invalid drill header')
            self.runs[key] = {**event, 'attempts': [], 'outcome': 'IN_PROGRESS'}
        elif key in self.runs:
            if event['kind'] == 'attempt':
                self.runs[key]['attempts'].append(event['attempt'])
            elif event['kind'] == 'end':
                self.runs[key].update(outcome=event['outcome'], ended_at=event['at'])

    def record(self, event):
        event = deepcopy(event)
        with self.lock:
            self._apply(event)
            if self.path:
                if self.worker is None:
                    self.worker = threading.Thread(target=self._write, name='practice-history', daemon=True)
                    self.worker.start()
                self.queue.put(event)

    def _write(self):
        while True:
            event = self.queue.get()
            try:
                if event is None:
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open('a', encoding='utf-8') as handle:
                    # Leading newline recovers from an interrupted previous line.
                    handle.write('\n' + json.dumps(event, ensure_ascii=False, allow_nan=False))
                    handle.write('\n')
            except (OSError, ValueError):
                self.error = '연습 기록을 저장하지 못했습니다. 데이터 폴더를 확인해 주세요.'
                logging.getLogger('iRacingLive').exception('Could not save drill result')
            finally:
                self.queue.task_done()

    def flush(self):
        self.queue.join()

    def close(self):
        with self.lock:
            worker = self.worker
            if worker is not None:
                self.queue.put(None)
                worker.join(timeout=5)
                self.worker = None

    def view(self, task_id):
        with self.lock:
            runs = [run for run in self.runs.values() if run['plan'].get('task_id') == task_id]
            return {'runs': [{**deepcopy({k: v for k, v in run.items() if k != 'attempts'}), 'attempt_count': len(run['attempts']),
                              'attempts': deepcopy(run['attempts'][-30:])} for run in runs[-12:]],
                    'error': self.error}
