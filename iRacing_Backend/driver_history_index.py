"""Capture compact recording observations without reading or migrating old analysis DBs."""
from __future__ import annotations

from pathlib import Path
import re

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_summary import build_source_summary


def _imported_reference(telemetry):
    metadata = telemetry.metadata or {}
    if metadata.get('imported') is True:
        return True
    if metadata.get('game') == 'lmu' or Path(telemetry.path).suffix.casefold() == '.duckdb':
        from lmu_imports import import_dir
        return Path(telemetry.path).resolve().is_relative_to(import_dir().resolve())
    return False


def index_telemetry(telemetry, source_id, *, store=None):
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
    return (store if store is not None else DriverHistoryStore()).record_source(summary)
