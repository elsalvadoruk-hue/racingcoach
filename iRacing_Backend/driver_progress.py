"""Ordered, compact result persistence and optional challenge reconciliation.

Recording readers call the pure attachment helper with already parsed telemetry.
Results are saved before history; challenge evaluation follows a successful
history transaction and cannot undo it. No helper opens an original recording.
"""
from __future__ import annotations

import logging


logger = logging.getLogger(__name__)
CHALLENGE_RECONCILE_FAILED = 'CHALLENGE_RECONCILE_FAILED'


def attach_recording_results(telemetry, summary):
    from driver_results import extract_recording_results
    stamps = [row['recorded_at'] for row in summary.get('observations', []) if row.get('recorded_at')]
    batch = extract_recording_results(telemetry, summary['source_id'], recorded_at=min(stamps) if stamps else None)
    return {**summary, 'race_results': batch}


def persist_source_results(history, summary):
    """Before-save callback: failures propagate so this source remains retryable."""
    from driver_results import DriverResultsStore
    batch = summary.get('race_results')
    if (not isinstance(batch, dict) or batch.get('source_id') != summary.get('source_id')
            or batch.get('game') != summary.get('game')):
        raise ValueError('경기 결과 요약의 원본 연결을 확인할 수 없습니다.')
    return DriverResultsStore(history_path=history.path).record_batch(batch)


def reconcile_driver_challenges(history, game):
    """After-save callback; accept a game or the common (store, summary) shape."""
    from driver_journal import DriverJournal
    game = game.get('game') if isinstance(game, dict) else game
    journal = DriverJournal(history.path)
    if not journal.has_active(game):
        return {'checked': False, 'challenge': None}
    return {'checked': True, 'challenge': journal.reconcile(history.report_snapshot(game), game)}


def notify_after_save(history, summary, callback):
    """Keep committed history on a failed optional evaluator; expose a retry code."""
    if callback is None:
        return []
    try:
        callback(history, summary)
        return []
    except Exception:
        # Exception messages may include personal paths. Surface a stable code
        # and the report's explicit POST /driver-journal/reconcile retry instead.
        logger.warning('도전 달성 자동 확인을 완료하지 못했습니다. 드라이버 리포트에서 다시 확인할 수 있습니다.')
        return [CHALLENGE_RECONCILE_FAILED]
