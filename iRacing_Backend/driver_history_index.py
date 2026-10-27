"""Capture compact recording observations without reading or migrating old analysis DBs."""
from __future__ import annotations

from pathlib import Path
import re

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_summary import build_source_summary
from driver_progress import (attach_recording_results, notify_after_save,
                             persist_source_results, reconcile_driver_challenges)


def _imported_reference(telemetry):
    metadata = telemetry.metadata or {}
    if metadata.get('imported') is True:
        return True
    if metadata.get('game') == 'lmu' or Path(telemetry.path).suffix.casefold() == '.duckdb':
        from lmu_imports import import_dir
        return Path(telemetry.path).resolve().is_relative_to(import_dir().resolve())
    return False


def _drive_options(summary):
    grouped = {}
    for row in summary.get('observations', []):
        grouped.setdefault(row['drive_id'], []).append(row)
    output = []
    for drive_id, rows in sorted(grouped.items()):
        dates = [row['recorded_at'] for row in rows if row.get('recorded_at')]
        labels = {}
        for key in ('car', 'track'):
            known = {row.get('labels', {}).get(key, '') for row in rows
                     if row.get('labels', {}).get(key, '')}
            labels[key] = next(iter(known)) if len(known) == 1 else ''
        output.append(dict(drive_id=drive_id, recorded_at=min(dates) if dates else None,
                           labels=labels, session_type=rows[0].get('session_type', 'UNKNOWN')))
    return output


def index_telemetry(telemetry, source_id, *, store=None, before_save=None, after_save=None):
    """Reuse the analysis content hash; a renamed copy remains the same source.

    Only the recording being analyzed is read. Imported reference recordings are
    excluded, and no historical analysis rows are inferred to be actual drives.
    File stat checks guard against attaching changed samples to an older hash.
    """
    if not isinstance(source_id, str) or not re.fullmatch(r'[a-f0-9]{64}', source_id):
        raise ValueError('A recording content SHA256 is required')
    if _imported_reference(telemetry):
        return {'saved': False, 'reason': 'IMPORTED_REFERENCE'}
    if tuple(file_signature(telemetry.path)) != tuple(telemetry.signature):
        return {'saved': False, 'reason': 'SOURCE_CHANGED'}
    summary = build_source_summary(telemetry, source_id)
    if tuple(file_signature(telemetry.path)) != tuple(telemetry.signature):
        return {'saved': False, 'reason': 'SOURCE_CHANGED'}
    summary = attach_recording_results(telemetry, summary)
    if tuple(file_signature(telemetry.path)) != tuple(telemetry.signature):
        return {'saved': False, 'reason': 'SOURCE_CHANGED'}
    history = store if store is not None else DriverHistoryStore()
    # Normal foreground analysis persists all progress beside its actual
    # history ledger. Injected doubles retain the previous no-extra-store path.
    concrete = isinstance(history, DriverHistoryStore)
    before = before_save if before_save is not None else persist_source_results if concrete else None
    after = after_save if after_save is not None else reconcile_driver_challenges if concrete else None
    if before is not None:
        before(history, summary)
    if tuple(file_signature(telemetry.path)) != tuple(telemetry.signature):
        return {'saved': False, 'reason': 'SOURCE_CHANGED'}
    result = history.record_source(summary)
    warnings = notify_after_save(history, summary, after)
    options = _drive_options(summary)
    drive_ids = [row['drive_id'] for row in options]
    return {**result, 'drive_ids': drive_ids, 'selected_drive_id': drive_ids[0] if len(drive_ids) == 1 else None,
            'drive_options': options,
            **({'warnings': warnings} if warnings else {})}
