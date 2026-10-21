"""Workspace adapter for automatic, condition-matched personal bests (iRacing and LMU)."""
from pathlib import Path
import logging

from personal_reference import PersonalReferenceIndex, compatibility, identity
from reference_conditions import telemetry_reference_conditions

logger = logging.getLogger(__name__)


def _metadata_match(left, right):
    """Older file-list cache rows omit IDs; compare their exact paths/names."""
    def token(value):
        return ' '.join(str(value or '').casefold().split())
    for prefix in ('car', 'track'):
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
         expected_signature=telemetry.signature)


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
    for path in paths.values():
        try:
            resolved = resolve(path.name)
            if resolved is None or Path(resolved).resolve() != Path(path).resolve() or (game != 'LMU' and index.has_recording(game, path)):
                continue
            # The quick header read is IBT-only; LMU recordings use the cached full parse.
            metadata = (services.telemetry_file_visibility if game == 'LMU' else services.quick_file_visibility)(path)
            if not _metadata_match(metadata, telemetry.metadata):
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
                    'same_lap': candidate['filename'] == telemetry.path.name and candidate['lap'] == target.lap_number}
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
