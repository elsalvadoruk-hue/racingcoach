"""Read configured recordings on the reference worker; never alter a recording or index."""
from pathlib import Path
import numpy as np

from overlay_reference import AlignedReference, GridReference, finite
from workspace_personal_reference import _conditions, _metadata_match, _compatibility


class PersonalOverlayLoader:
    def __init__(self, paths, load, metadata, analyzer):
        self.paths, self.load, self.metadata, self.analyzer = paths, load, metadata, analyzer

    def __call__(self, context, length, active=lambda: True):
        game = context.get('game', 'iracing')
        identity = context.get('identity') or {}
        current = {**identity, 'car_platform_id': identity.get('car_id'),
                   'track_platform_id': identity.get('track_id')}
        conditions = context['conditions']
        best = None
        # Quick metadata narrows the candidates before a telemetry file is parsed.
        for path in self.paths(game):
            if not active():
                return None
            path = Path(path)
            previous_best = best
            try:
                if path.suffix.lower() == '.duckdb':
                    if Path(str(path)+'.wal').exists():
                        continue
                    from lmu_imports import import_dir
                    if path.resolve().is_relative_to(import_dir().resolve()):
                        continue  # An imported driver's lap is not the user's own best.
                before = path.stat()
                meta = self.metadata(path)
                if not _metadata_match(meta, current, car=game != 'lmu'):
                    continue
                telemetry = self.load(path)
                if not _metadata_match(telemetry.metadata, current):
                    continue
                # LMU's generated track ID comes from the track name. An
                # explicitly different layout must still be excluded.
                if game == 'lmu' and ' '.join(str(telemetry.metadata.get('track_config') or '').casefold().split()) != ' '.join(str(identity.get('track_config') or '').casefold().split()):
                    continue
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    continue
                for lap in telemetry.laps:
                    if not active():
                        return None
                    duration = finite(lap.lap_time_s)
                    if not lap.valid or not lap.complete or duration is None or duration <= 0 or best and duration >= best[0]:
                        continue
                    recorded = _conditions(telemetry, lap)
                    match = _compatibility('LMU' if game == 'lmu' else 'IRACING', conditions, recorded, False)
                    if not match['compatible']:
                        continue
                    if any(finite(conditions.get(key)) is not None and finite(recorded.get(key)) is not None
                           and abs(conditions[key]-recorded[key]) > limit for key, limit in (('fuel_l', 30), ('track_temp_c', 12))):
                        continue
                    grid = self.analyzer(telemetry)._lap_grid(lap)
                    if any(name not in grid or np.count_nonzero(np.isfinite(grid[name])) < .98*len(grid[name])
                           for name in ('time_s', 'Brake', 'Throttle')):
                        continue
                    aligned = AlignedReference(GridReference(grid, duration), length)
                    final = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                        best = previous_best
                        break
                    aligned.meta.update(recorded)
                    unknown = [label for key, label in (('fuel_l', '연료'), ('track_temp_c', '노면 온도'),
                                                        ('tire_compound', '타이어'))
                               if conditions.get(key) is None or recorded.get(key) is None]
                    best = duration, aligned, {'state': 'READY', 'source': 'PERSONAL',
                        'message': '비슷한 조건의 내 빠른 기록 연결됨', 'filename': path.name,
                        'lap': lap.lap_number, 'lap_id': f'personal:{path.name}:{lap.lap_number}:{after.st_mtime_ns}',
                        'lap_time_s': duration,
                        'condition_note': '같은 차량·레이아웃 · 조건이 맞는 기록 중 가장 빠른 랩'
                            + (' · 노면 조건 일부 미확인' if match.get('warnings') else '')
                            + (' · '+', '.join(unknown)+' 미확인' if unknown else '')
                            + (' · LMU 공식 유효성은 별도 확인' if game == 'lmu' else '')}
                final = path.stat()
                if (before.st_size, before.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                    best = previous_best
            except (OSError, ValueError, KeyError, TypeError):
                best = previous_best
                continue
        return (best[1], best[2]) if best else None


def compatible(context, recorded):
    match = _compatibility('LMU' if context.get('game') == 'lmu' else 'IRACING',
                           context['conditions'], recorded, False)
    return match['compatible'] and all(
        finite(context['conditions'].get(key)) is None or finite(recorded.get(key)) is None
        or abs(context['conditions'][key]-recorded[key]) <= limit
        for key, limit in (('fuel_l', 30), ('track_temp_c', 12)))
