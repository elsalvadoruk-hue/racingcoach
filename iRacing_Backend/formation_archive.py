"""Bounded background history access. SDK submits immutable jobs, never reads a store."""
from copy import deepcopy
from datetime import datetime
import logging
import queue
import threading

from live_formation import RaceHistory


def date_label(at):
    try:
        return datetime.fromtimestamp(float(at)).strftime('%Y-%m-%d')
    except (ValueError, TypeError, OSError, OverflowError):
        return '날짜 미확인'


def same_combo(a, b):
    if a.get('game') != b.get('game') or a.get('game') not in ('lmu', 'iracing'):
        return False
    for primary, alternate in (('car_id', 'car_path'), ('track_id', 'track_path')):
        left, right = a.get(primary) or a.get(alternate), b.get(primary) or b.get(alternate)
        if not left or not right or str(left) != str(right):
            return False
    return (a.get('track_config') or '') == (b.get('track_config') or '')


class FormationArchive:
    def __init__(self, owner):
        self.owner = owner
        self.lock = threading.Lock()
        self.jobs = queue.Queue(maxsize=4)
        self.cache = {}
        self.requested = {}
        self.thread = None
        self.closing = False

    def submit(self, identity, now, result=None):
        key = RaceHistory.key(identity)
        if not key or self.closing:
            return False
        if result is None and now - self.requested.get(key, -1e30) < 2:
            return False
        job = (deepcopy(identity), deepcopy(result))
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            return False
        self.requested[key] = now
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._work, name='formation-history', daemon=True)
            self.thread.start()
        return True

    def _load(self, identity, result):
        if result is not None:
            self.owner.race_history.add(identity, result)
        last = self.owner.race_history.last(identity)
        if last:
            last['at_label'] = date_label(last.get('at'))
        lookup = self.owner.history_lookup
        analysed = lookup(identity) if lookup else None
        # New text history accepts analysis only with explicit game identity.
        checked = analysed if analysed and analysed.get('game') == identity.get('game') else None
        notes = []
        if self.owner.notes_lookup:
            for note in self.owner.notes_lookup(identity):
                ctx = note.get('context') or {}
                candidate = {**(ctx.get('identity') or {})}
                candidate.setdefault('game', ctx.get('game'))
                if note.get('source') in ('TEXT', 'VOICE') and same_combo(candidate, identity) and isinstance(note.get('text'), str):
                    notes.append({'id': note.get('id'), 'text': note['text'],
                                  'at_label': date_label(note.get('updated_at') or note.get('created_at'))})
        return {'last': last, 'analysed': checked, 'voice_analysed': analysed, 'notes': notes[:1],
                'count': self.owner.race_history.count(identity),
                'records': self.owner.race_history.records(identity)}

    def _work(self):
        while True:
            try:
                job = self.jobs.get(timeout=.25)
            except queue.Empty:
                if self.closing:
                    return
                continue
            try:
                identity, result = job
                value = self._load(identity, result)
                with self.lock:
                    self.cache[RaceHistory.key(identity)] = value
                    while len(self.cache) > 8:
                        self.cache.pop(next(iter(self.cache)))
            except Exception:
                logging.getLogger('iRacingLive').exception('Could not refresh formation history')
            finally:
                self.jobs.task_done()

    def view(self, identity):
        with self.lock:
            return deepcopy(self.cache.get(RaceHistory.key(identity)) or {})

    def close(self):
        self.closing = True
        if self.thread:
            self.thread.join(timeout=3)
