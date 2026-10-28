"""Automatic session-log sending, only with the driver's recorded consent.

One switch covers iRacing and LMU. Completed drives use the same file as the
"보내기" button. Confirmed asphalt-oval drives can also qualify without radio
calls, using independent health observations of non-replay driving, with:

- no microphone questions or answers (always left out here),
- names, session numbers, ratings and paths already removed (session_log.scrub),
- only drives that ended after consent was given (nothing from before),
- a visible status: what was sent, what failed and why. A failed drive is tried at most
  MAX_ATTEMPTS times, spaced out, and then left for the driver to send by hand.

Turning it off stops all sending at once and forgets the consent. What was already sent
stays listed so the driver can see it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import app_settings
import session_log

logger = logging.getLogger(__name__)

# Raise when what the consent text says changes: the driver is then asked again.
# 4 (2026-10-28): confirmed asphalt-oval practice, silent oval drives and pacing
# signals; bounded traffic windows when the complete oval log does not fit.
CONSENT_VERSION = 4
# Update with CONSENT_VERSION when the notice's scope changes, independently of app releases.
CONSENT_CHANGES = (
    '확인된 포장 오벌 연습에서도 주변 차량·피트 허용·출발 대열 신호를 보냅니다.',
    '실제 주행이 확인된 포장 오벌은 2분 이상이면 안내가 없었던 기록도 보냅니다.',
    '오벌 절차·위트 무전의 무음 평가 근거와 음성 점유 상태·가상 문구를 추가합니다.',
    '파일이 크면 오벌 주변 차량 기록을 일부 구간으로 줄이고 생략 범위를 표시합니다.',
)
FINISHED_AFTER_S = 600       # no new radio/health record for 10 minutes, and not the current run
MIN_CALLS, MIN_MINUTES = 10, 2.  # confirmed driven asphalt ovals only waive MIN_CALLS
MAX_ATTEMPTS, RETRY_AFTER_S = 3, 1800
DAILY_LIMIT = 20             # the receiving script takes 300 a day from everyone together
CHECK_EVERY_S = 60
FILE_NAME = 'session-log-auto.json'


def path():
    return app_settings.data_dir() / FILE_NAME


def load():
    try:
        saved = json.loads(path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    consent = saved.get('consent') if isinstance(saved.get('consent'), dict) else None
    renewal = bool(consent and consent.get('version') != CONSENT_VERSION)
    previous = consent.get('version') if renewal else None
    if renewal:
        consent = None   # the notice changed since: ask again
    runs = saved.get('runs') if isinstance(saved.get('runs'), dict) else {}
    return {'consent': consent, 'runs': runs, 'reconsent_required': renewal,
            'previous_consent_version': previous if type(previous) is int else None}


def save(state):
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    # Renewal metadata is derived from the recorded consent; never persist stale UI flags.
    temporary.write_text(json.dumps({key:state[key] for key in ('consent','runs')}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


def set_enabled(enabled, consent_version=None, now=None):
    """Turn sending on (with the consent the screen showed) or off. Returns the status."""
    state = load()
    if enabled:
        if consent_version != CONSENT_VERSION:
            raise ValueError('안내문을 다시 확인한 뒤 켜 주세요.')
        state['consent'] = {'version': CONSENT_VERSION, 'given_at': now or time.time()}
    else:
        state['consent'] = None
    save(state)
    return status()


def status():
    state = load()
    runs = sorted(({'run_id': run_id, **entry} for run_id, entry in state['runs'].items()),
                  key=lambda entry: -(entry.get('ended_at') or 0))
    return {'enabled': bool(state['consent']), 'consent_version': CONSENT_VERSION,
            'reconsent_required':state['reconsent_required'], 'previous_consent_version':state['previous_consent_version'],
            'consent_changes':list(CONSENT_CHANGES) if state['reconsent_required'] else [],
            'consented_at': (state['consent'] or {}).get('given_at'),
            'available': bool(app_settings.log_upload_url()),
            'sent': sum(1 for entry in runs if entry.get('sent_at')),
            'recent': runs[:10]}


class AutoUploader:
    def __init__(self, store, app_version, current_run=lambda: None, send=session_log.send, clock=time.time, extras=()):
        self.store, self.app_version, self.current_run = store, app_version, current_run
        self.extras = extras
        self.send_file, self.clock = send, clock
        self.stopping = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name='session-log-auto', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()

    def _loop(self):
        while not self.stopping.wait(CHECK_EVERY_S):
            try:
                self.run_once()
            except Exception:
                logger.exception('Automatic session log sending failed; trying again later')

    def run_once(self):
        """Send the drives that are due. Returns how many were sent."""
        with self.lock:
            state = load()
            url = app_settings.log_upload_url()
            if not state['consent'] or not url:
                return 0
            now, given = self.clock(), state['consent']['given_at']
            today = time.strftime('%Y-%m-%d', time.localtime(now))
            sent_today = sum(1 for entry in state['runs'].values()
                             if entry.get('sent_at') and time.strftime('%Y-%m-%d', time.localtime(entry['sent_at'])) == today)
            current, count = self.current_run(), 0
            for run in reversed(session_log.sessions(self.store, 30)):     # oldest first
                entry = state['runs'].get(run['run_id'], {})
                if (run['run_id'] == current or run['started_at'] < given or now - run['ended_at'] < FINISHED_AFTER_S
                        or (run['calls'] < MIN_CALLS and not (run.get('game') == 'iracing'
                            and run.get('spotter_profile') == 'ASPHALT_OVAL' and run.get('has_driving_sample')))
                        or run['ended_at'] - run['started_at'] < MIN_MINUTES * 60 or entry.get('sent_at')
                        or entry.get('attempts', 0) >= MAX_ATTEMPTS
                        or now - entry.get('tried_at', 0) < RETRY_AFTER_S or sent_today >= DAILY_LIMIT):
                    continue
                found = error = None
                try:
                    found = session_log.export(self.store, run['run_id'], self.app_version, include_questions=False,
                                               extras=self.extras, limit=session_log.SEND_LIMIT)
                except session_log.Deferred:
                    continue      # an extra waits until the driver is off track; not a failed attempt
                except ValueError as exc:
                    error = exc
                entry = {**entry, 'track': run.get('track'), 'car': run.get('car'), 'ended_at': run['ended_at'],
                         'attempts': entry.get('attempts', 0) + 1, 'tried_at': now}
                try:
                    if error is not None:
                        raise error
                    if not found:
                        continue
                    self.send_file(url, found[0], found[1], '자동 보내기', self.app_version)
                    entry.update(sent_at=now, name=found[0], error=None)
                    count += 1
                    sent_today += 1
                except ValueError as exc:
                    entry['error'] = str(exc)
                state['runs'][run['run_id']] = entry
                save(state)
            return count
