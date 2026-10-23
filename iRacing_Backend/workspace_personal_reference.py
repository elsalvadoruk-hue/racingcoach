"""Workspace adapter for automatic, condition-matched personal bests (iRacing and LMU)."""
from pathlib import Path
import functools
import logging

from personal_reference import PersonalReferenceIndex, compatibility, identity
from reference_conditions import telemetry_reference_conditions

logger = logging.getLogger(__name__)


def _metadata_match(left, right, car=True):
    """Older file-list cache rows omit IDs; compare their exact paths/names."""
    def token(value):
        return ' '.join(str(value or '').casefold().split())
    for prefix in ('car', 'track') if car else ('track',):
        a, b = left.get(prefix + '_platform_id'), right.get(prefix + '_platform_id')
        if a and b:
            if str(a) != str(b):
                return False
            continue
        a = token(left.get(prefix + '_path') or left.get(prefix + '_name'))
        b = token(right.get(prefix + '_path') or right.get(prefix + '_name'))
        if not a or a != b:
            return False
        if prefix == 'track' and token(left.get('track_config')) != token(right.get('track_config')):
            return False
    return True


def _game(telemetry):
    return 'LMU' if (telemetry.metadata or {}).get('game') == 'lmu' else 'IRACING'


# Raise when what is stored per lap changes, so older index rows are read again.
INDEX_VERSION = {'IRACING': '', 'LMU': 'lmu-model-1'}


def _conditions(telemetry, lap):
    measured = telemetry_reference_conditions(telemetry, lap)
    if _game(telemetry) == 'LMU':
        measured.update(telemetry.metadata.get('lmu_lap_conditions', {}).get(lap.lap_number, {}))
    return measured


def _compatibility(game, current, reference, same_file):
    if game == 'LMU':
        from lmu_personal_reference import reference_compatibility
        return reference_compatibility(current, reference)
    return compatibility(current, reference, same_file)


def _imported(services, game, path):
    """A downloaded recording may be someone else's lap: say so, with the name it carries."""
    if game != 'LMU':
        return {}
    from lmu_imports import import_dir
    if path.resolve().parent != import_dir().resolve():
        return {'imported': False}
    return {'imported': True, 'driver_name': _driver_name(path)}


@functools.lru_cache(maxsize=64)
def _recorded_driver(path, size, mtime_ns):
    import duckdb
    with duckdb.connect(path, read_only=True) as db:
        row = db.execute("SELECT value FROM metadata WHERE key = 'DriverName'").fetchone()
    return str(row[0]).strip() or None if row and row[0] else None


def _driver_name(path):
    # One metadata row, not a parse of the whole recording.
    try:
        stat = path.stat()
        return _recorded_driver(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    except Exception:  # duckdb raises its own error types for unreadable files
        return None


def _recordings(services, telemetry):
    """Every recording that may be resolved by name, listed once."""
    if _game(telemetry) == 'LMU':
        import lmu_recordings
        listed = lmu_recordings.recordings()
    else:
        listed = services.list_ibt_files(None)
    known = {}
    for path in listed:
        known.setdefault(Path(path).name, Path(path))
    return known


def index_telemetry(telemetry, index=None):
    index = index or PersonalReferenceIndex()
    index.index_recording(_game(telemetry), telemetry.path, identity(telemetry.metadata), [
        {'lap': lap.lap_number, 'lap_time_s': lap.lap_time_s,
         'eligible': bool(lap.valid and lap.complete),
         'conditions': _conditions(telemetry, lap)} for lap in telemetry.laps],
         expected_signature=telemetry.signature, version=INDEX_VERSION[_game(telemetry)])


def select_personal_reference(services, telemetry, target_lap=None):
    index = PersonalReferenceIndex()
    index_telemetry(telemetry, index)
    key = identity(telemetry.metadata)
    analyzer = services.get_telemetry_analyzer()(telemetry)
    target, _, _ = analyzer._select_laps(target_lap, None)
    if target is None:
        return {'status': 'UNAVAILABLE', 'reason': 'NO_TARGET_LAP'}
    current = _conditions(telemetry, target)
    # Existing compact analysis records seed older files; current discovery adds
    # recordings that predate automatic persistence. Only configured roots may
    # resolve a candidate, even when its original absolute path remains on disk.
    game = _game(telemetry)
    known = _recordings(services, telemetry)  # one folder scan, not one per candidate

    def resolve(name):
        return known.get(Path(name).name)
    paths = {str(path): path for path in known.values()}
    paths.update({str(path): path for path in index.previous_analysis_paths()})
    indexed = index.current_recordings(game, known.values(), INDEX_VERSION[game])
    for path in paths.values():
        try:
            resolved = resolve(path.name)
            if resolved is None or Path(resolved).resolve() != Path(path).resolve() or str(path.resolve()) in indexed:
                continue
            # The quick header read is IBT-only; LMU recordings use the cached full parse.
            metadata = (services.telemetry_file_visibility if game == 'LMU' else services.quick_file_visibility)(path)
            # An LMU entry name differs between liveries; the loaded model key decides below.
            if not _metadata_match(metadata, telemetry.metadata, car=game != 'LMU'):
                continue
            other = services.get_repository().load(path)
            if identity(other.metadata) == key:
                index_telemetry(other, index)
        except (OSError, ValueError, KeyError):
            continue
    for candidate in index.candidates(game, key):
        resolved = resolve(candidate['filename'])
        if resolved is None or Path(resolved).resolve() != Path(candidate['path']).resolve():
            continue
        match = _compatibility(game, current, candidate['conditions'], Path(candidate['path']).resolve() == telemetry.path.resolve())
        if match['compatible']:
            return {'status': 'READY', 'filename': candidate['filename'], 'lap': candidate['lap'],
                    'lap_time_s': candidate['lap_time_s'], 'conditions': match, 'selection': 'FASTEST_COMPATIBLE_PERSONAL_LAP',
                    'same_lap': candidate['filename'] == telemetry.path.name and candidate['lap'] == target.lap_number,
                    **_imported(services, game, Path(candidate['path']))}
    return {'status': 'UNAVAILABLE', 'reason': 'NO_COMPATIBLE_PERSONAL_LAP'}


def validate_personal_reference(telemetry, target, reference_telemetry, reference):
    if identity(telemetry.metadata) != identity(reference_telemetry.metadata):
        raise ValueError('내 기준 랩은 같은 차량·트랙·레이아웃이어야 합니다.')
    if reference is None or not reference.valid or not reference.complete:
        raise ValueError('유효하게 완주한 내 기준 랩을 선택하세요.')
    match = _compatibility(_game(telemetry), _conditions(telemetry, target),
                          _conditions(reference_telemetry, reference),
                          telemetry.path.resolve() == reference_telemetry.path.resolve())
    if not match['compatible']:
        raise ValueError('내 기준 랩의 주행 조건이 다르거나 확인되지 않았습니다. ' + ', '.join(match['reasons']))
    return match
