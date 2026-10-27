"""LMU recordings as files of the shared analysis workspace.

A recording is addressed by its file name (as IBT files are). The 24-hex IDs
the earlier LMU screens stored are still accepted.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from recording_selection import FILTER_POLICY, is_selectable_recording

FILE_ID = re.compile(r'^[a-f0-9]{24}$')


def file_id(path: Path) -> str:
    return hashlib.sha256(str(Path(path).resolve()).casefold().encode('utf-8')).hexdigest()[:24]


def recordings(limit=None):
    from lmu_telemetry import discover_lmu_telemetry_dirs, list_lmu_telemetry_files
    try:
        paths = list_lmu_telemetry_files(discover_lmu_telemetry_dirs())
    except OSError:
        return []
    paths = sorted(paths, key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return paths[:limit] if limit else paths


def is_lmu_name(name) -> bool:
    return isinstance(name, str) and (name.casefold().endswith('.duckdb') or bool(FILE_ID.match(name)))


def resolve(name):
    if not is_lmu_name(name):
        return None
    wanted = Path(name).name
    for path in recordings():
        if path.name == wanted or file_id(path) == wanted:
            return path
    return None


def recording_busy(path: Path) -> bool:
    """A recording still being written keeps a WAL next to it; never read it then."""
    return Path(str(path) + '.wal').exists()


def files_payload(visibility, limit=250):
    from lmu_imports import import_dir
    imported = import_dir().resolve()
    fields = ('car_name', 'car_path', 'track_name', 'track_path', 'track_config', 'track_platform_id', 'analyzable',
              'reason', 'detected_laps', 'complete_laps', 'valid_laps', 'measurement_laps', 'metadata_pending', 'status', 'retryable',
              'session_type', 'session_duration_s', 'suggested_session_mode', 'best_lap_s', 'recording_time')
    rows = []
    for path in recordings(limit):
        stat = path.stat()
        signature = [stat.st_size, stat.st_mtime_ns]
        row = {'name': path.name, 'file_id': file_id(path), 'display_name': path.name, 'provider': 'LMU',
               'file_signature': signature, 'auto_load': path.resolve().parent != imported,
               'imported': path.resolve().parent == imported}
        if recording_busy(path):
            row.update(analyzable=False, status='RECORDING', reason='LMU가 아직 이 파일을 기록 중입니다.')
        else:
            try:
                info = visibility(path)
            except (OSError, ValueError) as exc:
                info = {'analyzable': False, 'status': 'UNAVAILABLE', 'reason': str(exc)}
            row.update({key: info.get(key) for key in fields})
            after = path.stat()
            if recording_busy(path) or signature != [after.st_size, after.st_mtime_ns]:
                row.update(analyzable=False, status='RECORDING', retryable=True,
                           reason='LMU가 아직 이 파일을 기록 중입니다.')
        row['selectable'] = is_selectable_recording(row)
        rows.append(row)
    return {'provider': 'LMU', 'files': rows, 'filter_policy': FILTER_POLICY}
