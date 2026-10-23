"""Read-only handling evaluation; application imports follow isolate().

Native observations and a separate fixed-relative-position null are reported
with their own denominators. This development tool is not a release asset.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import warnings

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = 'a5eb18da746c98fb3e962b9109396310bac3076f'
BASELINE_BLOB = 'cf0665290afd7a0d96d3b6bd6c014def079501d3'
KINDS = ('understeer', 'oversteer', 'countersteer')
PHASES = ('braking', 'rotation', 'drive')
DEFAULT_NAMES = (
    'mclaren720sgt3_monza full 2026-10-21 19-34-32.ibt',
    'mclaren720sgt3_hockenheim gp 2026-08-01 18-13-18.ibt',
    'mclaren720sgt3_hockenheim gp 2026-10-22 21-55-12.ibt',
    'bmwm4gt3_spa 2024 up 2026-07-11 18-12-59.ibt',
    'bmwm4gt3_nurburgring combinedshortb 2026-07-04 16-42-37.ibt',
    'mclaren720sgt3_monza full 2026-10-21 19-27-35.ibt',
)


def fingerprint(path):
    """Hash recorded inputs only; never credentials or personal stores."""
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('평가 중 원본 파일이 변경되었습니다.')
    return dict(bytes=after.st_size, mtime_ns=after.st_mtime_ns, sha256=digest.hexdigest())


def output_paths(out, inputs):
    out = Path(out).resolve()
    paths = (out.with_suffix('.json'), out.with_suffix('.txt'))
    if any(p == Path(source).resolve() for p in paths for source in inputs):
        raise ValueError('출력 경로가 입력 파일과 같습니다.')
    return paths


def load_method(method, candidate=None):
    """v1 is immutable local Git content, never a remote/dynamic user source."""
    if method == 'v1':
        source = subprocess.run(['git', 'show', f'{BASELINE_COMMIT}:iRacing_Backend/handling.py'],
                                cwd=ROOT, check=True, capture_output=True).stdout
        blob = hashlib.sha1(f'blob {len(source)}\0'.encode() + source).hexdigest()
        if blob != BASELINE_BLOB:
            raise RuntimeError('고정 v1 Git blob 검증에 실패했습니다.')
        module = types.ModuleType('handling_evaluation_v1')
        exec(compile(source, f'git:{BASELINE_COMMIT}:handling.py', 'exec'), module.__dict__)
        return module, dict(commit=BASELINE_COMMIT, blob=blob, method=module.METHOD)
    path = Path(candidate) if candidate else ROOT / 'iRacing_Backend/handling.py'
    source = path.read_bytes()
    module = types.ModuleType(f'handling_evaluation_{method}')
    exec(compile(source, str(path), 'exec'), module.__dict__)
    if module.METHOD != f'personal_corner_phase_{method}':
        raise ValueError(f'{method} 구현이 아닙니다({module.METHOD}). 로컬 후보는 --candidate로 지정하세요.')
    return module, dict(sha256=hashlib.sha256(source).hexdigest(), method=module.METHOD)


def slot_rates(rows):
    out = {}
    for phase in ('ALL', *PHASES):
        selected = rows if phase == 'ALL' else [r for r in rows if r['phase'] == phase]
        kinds = {}
        for kind in KINDS:
            key = 'counter_observed' if kind == 'countersteer' else 'observed'
            observed = [r for r in selected if r[key]]
            hit = lambda row: any(e['kind'] == kind for e in row['events'])
            full_hits = sum(hit(r) for r in observed)
            partial_hits = sum(hit(r) for r in selected if not r[key])
            kinds[kind] = dict(observed_slots=len(observed), observed_hits=full_hits,
                               partial_hits=partial_hits, all_hits=full_hits + partial_hits,
                               rate=full_hits / len(observed) if observed else None,
                               all_slots=len(selected))
        out[phase] = kinds
    return out


def auc(positive, negative):
    positive, negative = np.asarray(positive), np.asarray(negative)
    positive, negative = positive[np.isfinite(positive)], negative[np.isfinite(negative)]
    if len(positive) < 3 or len(negative) < 3:
        return None
    return float(np.mean((positive[:, None] > negative) + .5 * (positive[:, None] == negative)))


def adjusted_auc(rows, corners):
    laps = sorted({r['lap'] for r in rows})
    ci = {c['id']: i for i, c in enumerate(corners)}
    li = {lap: i for i, lap in enumerate(laps)}
    times = np.full((len(corners), len(laps)), np.nan)
    for r in rows:
        if r['time_s'] is not None:
            times[ci[r['corner_id']], li[r['lap']]] = r['time_s']
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        med = np.nanmedian(times, axis=1, keepdims=True)
        spread = 1.4826 * np.nanmedian(abs(times - med), axis=1, keepdims=True)
        spread[spread <= 1e-4] = np.nan
        standard = (times - med) / spread
        adjusted = np.array([standard[c] - np.nanmedian(np.delete(standard, c, axis=0), axis=0)
                             for c in range(len(corners))])
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r['corner_id'], r['phase'])].append(r)
    items = []
    for (corner, phase), values in grouped.items():
        for kind in KINDS:
            observed_key = 'counter_observed' if kind == 'countersteer' else 'observed'
            good = [r for r in values if (r[observed_key] or any(e['kind'] == kind for e in r['events']))
                    and r.get('v_start') is not None
                    and np.isfinite(adjusted[ci[corner], li[r['lap']]])]
            if not good:
                continue
            y = np.array([adjusted[ci[corner], li[r['lap']]] for r in good])
            v = np.array([r['v_start'] for r in good])
            flag = np.array([any(e['kind'] == kind for e in r['events']) for r in good])
            if v.std() > 0:
                z = (v - v.mean()) / v.std()
                y -= np.polyfit(z, y, 1)[0] * z
            value = auc(y[flag], y[~flag])
            if value is not None:
                items.append(dict(corner_id=corner, phase=phase, kind=kind,
                                  hit=int(flag.sum()), control=int((~flag).sum()), auc=value))
    return dict(items=items, item_count=len(items),
                mean=float(np.mean([i['auc'] for i in items])) if items else None,
                convention='동일 코너 강건 표준화 → 같은 랩 다른 코너 중앙값 차감 → 단계 시작 속도 선형 효과 차감; 항목별 AUC 동일 가중')


def parser():
    p = argparse.ArgumentParser(description='개인 저장소를 격리하여 차 거동 지표 6개를 평가합니다.')
    p.add_argument('files', nargs='*', type=Path)
    p.add_argument('--method', choices=('v1', 'v2', 'v3'), default='v1')
    p.add_argument('--candidate', type=Path, help='평가만 할 로컬 후보 모듈')
    p.add_argument('--permutations', type=int, default=200)
    p.add_argument('--skip-null', action='store_true',
                   help='위치 귀무 비교를 건너뛰고 원본 판정·랩 절반 반복만 계산')
    p.add_argument('--seed', type=int, default=20261023)
    p.add_argument('--out', type=Path)
    p.add_argument('--grid-hz', type=float, default=20.)
    p.add_argument('--resume', action='store_true', help='원본·방식·옵션 해시가 같은 파일별 결과만 재사용')
    return p


def matched_matrix(module, phase, references, model):
    c = phase['local'].channels
    speed, steer, brake, throttle = (c[k] for k in ('Speed', 'SteeringWheelAngle', 'Brake', 'Throttle'))
    predictions = []
    support = np.zeros(5, dtype=int)
    for reference in references:
        if reference is None or reference is phase:
            continue
        support += reference['support']
        values = module._reference_at(reference, phase['local'].pct)
        q, rv, rs, rb, rt = values[:5]
        ok = ((np.searchsorted(model['edges_mps'], rv, side='right') == phase['bands'])
              & (abs(rb - brake) <= module.MAX_PEDAL_DIFFERENCE)
              & (abs(rt - throttle) <= module.MAX_PEDAL_DIFFERENCE) & (rs * steer > 0))
        if hasattr(module, 'MAX_SPEED_RELATIVE'):
            ay, ra = c['LatAccel'], values[5]
            ok &= (abs(rv - speed) <= module.MAX_SPEED_RELATIVE * speed)
            ok &= (ra * ay > 0) & (abs(ra - ay) <= np.maximum(module.MAX_LAT_ACCEL_ABS,
                                                                 module.MAX_LAT_ACCEL_RELATIVE * abs(ay)))
        else:
            ok &= (abs(rv - speed) <= module.MAX_SPEED_DIFFERENCE)
            ok &= (abs(rs) <= module.MAX_STEER_RATIO * abs(steer)) & (abs(steer) <= module.MAX_STEER_RATIO * abs(rs))
        predictions.append(np.where(ok, q, np.nan))
    return np.array(predictions), support


def point_values(module, phase, references, model):
    if phase is None or not np.all(np.isfinite(model['edges_mps'])):
        return []
    matrix, support = matched_matrix(module, phase, references, model)
    if not len(matrix):
        return []
    c = phase['local'].channels
    steer = phase.get('q_steer', c['SteeringWheelAngle'])
    count = np.isfinite(matrix).sum(axis=0)
    selected = phase['eligible'] & (steer * c['YawRate'] > 0) & phase['inside'] & (phase['durations'] > 0)
    # Exactly the original audit stride, including the absence of its 200-sample guard.
    indices = np.flatnonzero(selected & (count >= module.MIN_REFERENCE_LAPS))[::3]
    result = []
    rate = np.full(len(steer), np.nan)
    edge = module._edges(phase['local'])
    np.divide(np.diff(abs(steer)), np.diff(phase['local'].time_s), out=rate[1:], where=edge)
    rate[1:][~edge] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        logs = np.log(np.where(matrix > 0, matrix, np.nan))
        center = np.nanmedian(logs, axis=0)
        spreads = 1.4826 * np.nanmedian(abs(logs - center), axis=0)
    good = (count >= module.MIN_REFERENCE_LAPS) & (support[phase['bands']] >= module.MIN_BAND_SAMPLES)
    used_spread = spreads.copy()
    if hasattr(module, 'Z_LIMIT'):
        point = good & (count >= module.MIN_LAPS_FOR_POINT_SPREAD)
        sources = point & phase['inside'] & phase['eligible'] & (steer * c['YawRate'] > 0)
        used_spread[~point] = np.median(spreads[sources]) if np.any(sources) else np.nan
    steady = phase.get('steady_steer', np.ones(len(steer), dtype=bool))
    for i in indices:
        refs = matrix[:, i]
        refs = refs[np.isfinite(refs) & (refs > 0)]
        if len(refs) < 3 or not phase['q'][i] > 0:
            continue
        spread = spreads[i]
        delta = np.log(phase['q'][i]) - center[i]
        outside = delta < np.log(module.UNDER_B) or delta > np.log(module.OVER_B)
        effective = good[i] and outside
        if hasattr(module, 'Z_LIMIT'):
            effective = effective and steady[i] and np.isfinite(used_spread[i]) and (
                used_spread[i] == 0 or abs(delta) > module.Z_LIMIT * used_spread[i])
        result.append([float(spread), float(delta), bool(outside), bool(effective),
                       float(rate[i]) if np.isfinite(rate[i]) else None])
    return result


def point_summary(values):
    out = {}
    for phase in ('ALL', *PHASES):
        rows = [r for name, r in values if phase == 'ALL' or name == phase]
        if not rows:
            out[phase] = dict(points=0, scatter_pct=None, outside_pct=None, effective_outside_pct=None)
            continue
        spread, delta, outside, effective, rate = np.array(rows, dtype=float).T
        out[phase] = dict(points=len(rows), scatter_pct=float(np.expm1(np.median(spread)) * 100),
                          outside_pct=float(outside.mean() * 100), effective_outside_pct=float(effective.mean() * 100),
                          ratio_unwinding_pct=float(np.expm1(np.median(delta[rate < -.05])) * 100) if np.any(rate < -.05) else None,
                          ratio_turning_in_pct=float(np.expm1(np.median(delta[rate > .05])) * 100) if np.any(rate > .05) else None)
    return out


def native_evaluation(module, analyzer, corners):
    """Instrument the selected implementation without changing its decisions."""
    original_prepare, original_classify = module.prepare_phase, module.classify_phase
    original_phases = analyzer._corner_phases
    bounds_names, captures, values = {}, [], []

    def named_phases(*args, **kwargs):
        result = original_phases(*args, **kwargs)
        corner = args[2]['id']
        for name, bounds in (result or {}).items():
            if isinstance(bounds, dict) and 'start_pct' in bounds:
                bounds_names[(bounds['start_pct'], bounds['end_pct'])] = (corner, name)
        return result

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        if result is not None:
            result['_evaluation_name'] = bounds_names.get((result['start'], result['end']))
        return result

    def classify(phase, references, model):
        result = original_classify(phase, references, model)
        captures.append((phase, references, model))
        if phase is not None:
            name = phase['_evaluation_name'][1]
            values.extend((name, r) for r in point_values(module, phase, references, model))
        return result

    module.prepare_phase, module.classify_phase = prepare, classify
    analyzer._corner_phases = named_phases
    try:
        result = module.analyze_handling(analyzer, corners, include_observations=True)
    finally:
        module.prepare_phase, module.classify_phase = original_prepare, original_classify
        analyzer._corner_phases = original_phases
    if result is None:
        raise ValueError('필수 채널 또는 측정 조향 범위가 없어 평가할 수 없습니다.')
    if len(captures) != len(result['observations']):
        raise RuntimeError('native 관측과 캡처 순서가 맞지 않습니다.')
    for row, (phase, _, _) in zip(result['observations'], captures):
        row['v_start'] = None
        if phase is not None:
            row['v_start'] = float(np.interp(phase['start'], phase['local'].pct,
                                             phase['local'].channels['Speed']))
    return result, captures, values


def _interp(phase, axis, values):
    """Interpolate only across a valid native forward edge, including endpoints."""
    local = phase['local']
    x = local.pct
    idx = np.searchsorted(x, axis, side='right') - 1
    clipped = np.clip(idx, 0, max(0, len(x) - 2))
    dt, dp = np.diff(local.time_s), np.diff(x)
    edges = ((dt > 0) & (dt <= 1.5 * local.tick_s)
             & (dp > 0) & (dp * local.track_length_m <= 25)
             & np.isfinite(values[:-1]) & np.isfinite(values[1:]))
    # Never sort a backward/duplicate axis into apparently observed data.
    if np.any(np.diff(x) < 0):
        return np.full(len(axis), np.nan)
    out = np.interp(axis, x, values)
    ok = (idx >= 0) & (idx < len(x) - 1) & edges[clipped]
    out[~ok] = np.nan
    return out


def _phase_grid(module, phases, model, hz):
    duration = [np.sum(p['durations']) for p in phases]
    # Fixed relative axis shared by the laps of this corner/phase, at >=20 Hz
    # for the longest phase. Native and grid rates remain separately labelled.
    bins = max(8, int(np.ceil(max(duration) * hz)))
    relative = np.arange(bins + 1) / bins
    targets, refs, durations, steady, complete = [], [], [], [], []
    for p in phases:
        axis = p['start'] + relative * (p['end'] - p['start'])
        c = p['local'].channels
        keys = ('Speed', 'SteeringWheelAngle', 'Brake', 'Throttle', 'LatAccel', 'YawRate')
        channel = [_interp(p, axis, c[k]) for k in keys]
        q = _interp(p, axis, p['q'])
        qs = _interp(p, axis, p.get('q_steer', c['SteeringWheelAngle']))
        # t increments include no unknown native edge inside the interval.
        times = _interp(p, axis, p['local'].time_s)
        dt = np.diff(times)
        edges = module._edges(p['local'])
        bad = np.r_[0, np.cumsum(~edges)]
        ia = np.searchsorted(p['local'].pct, axis[:-1], side='right') - 1
        ib = np.searchsorted(p['local'].pct, axis[1:], side='left')
        ia, ib = np.clip(ia, 0, len(edges)), np.clip(ib, 0, len(edges))
        dt[(bad[ib] - bad[ia] > 0) | ~np.isfinite(dt) | (dt <= 0)] = 0
        target = np.vstack([q, *channel, qs])[:, :-1]
        reference = module._reference_at(p, axis[:-1])
        if reference.shape[0] == 5:
            reference = np.vstack([reference, _interp(p, axis[:-1], c['LatAccel'])])
        targets.append(target)
        refs.append(reference)
        durations.append(dt)
        if 'steady_steer' in p:
            # Any excluded native point in a cell also excludes that grid cell.
            mask = np.asarray(p['steady_steer'], dtype=bool)
            count = np.r_[0, np.cumsum(~mask)]
            a = np.clip(ia, 0, len(mask) - 1)
            b = np.clip(ib + 1, 0, len(mask))
            steady.append(count[b] == count[a])
        else:
            steady.append(np.ones(bins, dtype=bool))
        complete.append(p['complete'])
    return (np.array(targets), np.array(refs), np.array(durations), np.array(steady),
            np.array(complete), np.array([p['support'] for p in phases]), bins)


def _sustained_any(mask, duration, seconds):
    active = np.zeros(len(mask), dtype=float)
    hit = np.zeros(len(mask), dtype=bool)
    for i in range(mask.shape[1]):
        active = np.where(mask[:, i] & (duration[:, i] > 0), active + duration[:, i], 0.)
        hit |= active + 1e-9 >= seconds
    return hit


def _classify(module, grid, references, model):
    target, _, duration, steady, complete, support, bins = grid
    q, v, steer, brake, throttle, ay, yaw, qsteer = target.transpose(1, 0, 2)
    rq, rv, rs, rb, rt, ra = references.transpose(1, 0, 2)
    band = np.searchsorted(model['edges_mps'], v, side='right')
    ok = ((np.searchsorted(model['edges_mps'], rv, side='right')[None] == band[:, None])
          & (abs(rb[None] - brake[:, None]) <= module.MAX_PEDAL_DIFFERENCE)
          & (abs(rt[None] - throttle[:, None]) <= module.MAX_PEDAL_DIFFERENCE)
          & (rs[None] * steer[:, None] > 0))
    if hasattr(module, 'MAX_SPEED_RELATIVE'):
        ok &= abs(rv[None] - v[:, None]) <= module.MAX_SPEED_RELATIVE * v[:, None]
        ok &= (ra[None] * ay[:, None] > 0) & (abs(ra[None] - ay[:, None]) <= np.maximum(
            module.MAX_LAT_ACCEL_ABS, module.MAX_LAT_ACCEL_RELATIVE * abs(ay[:, None])))
    else:
        ok &= abs(rv[None] - v[:, None]) <= module.MAX_SPEED_DIFFERENCE
        ok &= ((abs(rs[None]) <= module.MAX_STEER_RATIO * abs(steer[:, None]))
               & (abs(steer[:, None]) <= module.MAX_STEER_RATIO * abs(rs[None])))
    ok[np.arange(len(q)), np.arange(len(q))] = False
    matrix = np.where(ok & (rq[None] > 0), rq[None], np.nan)
    matched = np.isfinite(matrix).sum(axis=1)
    total_support = support.sum(axis=0)[None] - support
    good = (matched >= module.MIN_REFERENCE_LAPS) & (
        np.take_along_axis(total_support, band, axis=1) >= module.MIN_BAND_SAMPLES)
    if not np.all(np.isfinite(model['edges_mps'])):
        good[:] = False
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        if hasattr(module, 'Z_LIMIT'):
            logs = np.log(matrix)
            baseline = np.nanmedian(logs, axis=1)
            spread = 1.4826 * np.nanmedian(abs(logs - baseline[:, None]), axis=1)
            sources = good & (matched >= module.MIN_LAPS_FOR_POINT_SPREAD)
            fallback_sources = sources & np.isfinite(q) & (qsteer * yaw > 0)
            fallback = np.nanmedian(np.where(fallback_sources, spread, np.nan), axis=1)
            spread = np.where(sources, spread, fallback[:, None])
            good &= np.isfinite(spread)
            delta = np.log(np.where(q > 0, q, np.nan)) - baseline
            under = (delta < np.log(module.UNDER_B)) & ((spread == 0) | (delta < -module.Z_LIMIT * spread))
            over = (delta > np.log(module.OVER_B)) & ((spread == 0) | (delta > module.Z_LIMIT * spread))
        else:
            baseline = np.nanmedian(matrix, axis=1)
            ratio = q / baseline
            under, over = ratio < module.UNDER_B, ratio > module.OVER_B
    finite = np.all(np.isfinite(target[:, 1:7]), axis=1)
    eligible = finite & (v > module.MIN_SPEED) & (abs(steer) > module.MIN_STEER)
    coverage_min = getattr(module, 'COVERAGE_MIN', None)
    q_ready = eligible
    if coverage_min is not None:
        # Same rule as the product: known small aligned steering is not comparable.
        q_ready = eligible & ~(np.isfinite(qsteer) & (abs(qsteer) <= module.MIN_STEER))
    required = q_ready & (duration > 0)
    known = good & q_ready & np.isfinite(q) & (qsteer * yaw > 0)
    if coverage_min is None:
        observed = required.any(axis=1) & np.all(~required | known, axis=1) & complete
    else:
        total = np.sum(np.where(required, duration, 0.), axis=1)
        compared = np.sum(np.where(required & known, duration, 0.), axis=1)
        fraction = np.divide(compared, total, out=np.zeros_like(total), where=total > 0)
        observed = required.any(axis=1) & complete & (fraction >= coverage_min - 1e-9)
    counter_observed = (eligible & (duration > 0)).any(axis=1) & complete
    under = _sustained_any(under & known & steady, duration, module.UNDER_SECONDS)
    over = _sustained_any(over & known & steady, duration, module.OVER_SECONDS)
    counter = _sustained_any(eligible & (steer * yaw < 0) & (abs(yaw) > module.COUNTER_YAW),
                             duration, module.COUNTER_SECONDS)
    return np.array([under, over, counter]), np.array([observed, observed, counter_observed])


def null_evaluation(module, captures, rows, permutations=200, seed=20261023, grid_hz=20):
    if permutations < 1 or not np.isfinite(grid_hz) or grid_hz <= 0:
        raise ValueError('섞기 횟수와 grid 주파수는 양수여야 합니다.')
    if len(captures) != len(rows):
        raise ValueError('native 캡처와 관측 행 개수가 다릅니다.')
    grouped = defaultdict(list)
    for capture, row in zip(captures, rows):
        if capture[0] is not None:
            grouped[(row['corner_id'], row['phase'])].append(capture)
    rng = np.random.default_rng(seed)
    # counts axis: draw, kind, [observed, observed hits, partial hits,
    # paired observed, paired actual hits, paired null hits].
    counts = np.zeros((permutations, 3, 6), dtype=np.int64)
    aligned = np.zeros((3, 3), dtype=np.int64)
    phase_counts = defaultdict(lambda: np.zeros_like(counts))
    phase_aligned = defaultdict(lambda: np.zeros_like(aligned))
    slots = defaultdict(int)
    grid_groups = []
    for (corner, phase), entries in sorted(grouped.items()):
        phases = [entry[0] for entry in entries]
        if not phases:
            continue
        model = entries[0][2]
        grid = _phase_grid(module, phases, model, grid_hz)
        actual_hit, actual_observed = _classify(module, grid, grid[1], model)
        native_counts = np.stack([actual_observed.sum(axis=1),
                                  (actual_hit & actual_observed).sum(axis=1),
                                  (actual_hit & ~actual_observed).sum(axis=1)], axis=1)
        aligned += native_counts
        phase_aligned[phase] += native_counts
        slots[phase] += len(phases)
        bins = grid[-1]
        for draw in range(permutations):
            shifts = rng.integers(1, bins, size=len(phases))
            index = (np.arange(bins)[None] + shifts[:, None]) % bins
            references = np.take_along_axis(grid[1], index[:, None], axis=2).copy()
            # No interpolation or sustained interval crosses the circular seam.
            references[np.arange(len(phases)), :, (bins - shifts) % bins] = np.nan
            hit, observed = _classify(module, grid, references, model)
            paired = observed & actual_observed
            draw_counts = np.stack([observed.sum(axis=1), (hit & observed).sum(axis=1),
                                    (hit & ~observed).sum(axis=1), paired.sum(axis=1),
                                    (actual_hit & paired).sum(axis=1), (hit & paired).sum(axis=1)], axis=1)
            counts[draw] += draw_counts
            phase_counts[phase][draw] += draw_counts
        grid_groups.append(dict(corner_id=corner, phase=phase, laps=len(phases), bins=bins))

    def rates(values, total):
        return {kind: dict(observed_slots=int(values[k, 0]), observed_hits=int(values[k, 1]),
                           partial_hits=int(values[k, 2]), all_hits=int(values[k, 1] + values[k, 2]),
                           rate=float(values[k, 1] / values[k, 0]) if values[k, 0] else None,
                           all_slots=int(total)) for k, kind in enumerate(KINDS)}

    actual = {'ALL': rates(aligned, sum(slots.values())),
              **{name: rates(value, slots[name]) for name, value in phase_aligned.items()}}
    permutations_out = [{'ALL': rates(counts[i], sum(slots.values())),
                         **{name: rates(value[i], slots[name]) for name, value in phase_counts.items()}}
                        for i in range(permutations)]
    return dict(seed=seed, permutations=permutations, grid_hz=grid_hz,
                design='각 랩의 비교 벡터를 단계 내 상대 위치 grid에서 독립 원형 이동; 비교 대상 행 순열이 아님. 원형 경계의 참조값은 결측. 실제·귀무 공통 observed 칸을 매회 함께 집계.',
                count_columns=['observed_slots', 'observed_hits', 'partial_hits', 'paired_observed_slots',
                               'paired_actual_hits', 'paired_null_hits'],
                kinds=list(KINDS), aligned_grid=aligned.tolist(), permutation_counts=counts.tolist(),
                actual=actual, null_rates=permutations_out,
                phase_permutation_counts={name: value.tolist() for name, value in phase_counts.items()},
                groups=grid_groups,
                countersteer_note='반대 조향은 개인 기준과 무관한 실측이므로 위치 귀무로 변하지 않습니다.')


def repeated_files(files, *, permutations=200, seed=20261023):
    """One-to-one nearby apex matches; Jaccard of flagged corner/phase/kind.

    Missing classifications are reported separately, never negative evidence.
    The null preserves each file's phase/kind patterns while permuting corners.
    """
    rng = np.random.default_rng(seed)
    pairs = []
    for i, left in enumerate(files):
        for right in files[i + 1:]:
            if left['context'] != right['context']:
                continue
            candidates = []
            for a in left['corners']:
                for b in right['corners']:
                    distance = abs(a['apex_pct'] - b['apex_pct'])
                    distance = min(distance, 1 - distance)
                    if distance <= .006:
                        candidates.append((distance, a['id'], b['id']))
            matches, used_a, used_b = [], set(), set()
            for _, a, b in sorted(candidates):
                if a not in used_a and b not in used_b:
                    matches.append((a, b)); used_a.add(a); used_b.add(b)

            def flags(file, corner):
                values = set()
                for row in file['observations']:
                    if row['corner_id'] != corner:
                        continue
                    for event in row['events']:
                        values.add((row['phase'], event['kind']))
                return values

            a_flags = [flags(left, a) for a, _ in matches]
            b_flags = [flags(right, b) for _, b in matches]

            def score(order, kind=None):
                intersection = union = 0
                for j, k in enumerate(order):
                    aa, bb = a_flags[j], b_flags[k]
                    if kind:
                        aa = {x for x in aa if x[1] == kind}
                        bb = {x for x in bb if x[1] == kind}
                    intersection += len(aa & bb)
                    union += len(aa | bb)
                return dict(shared=intersection, union=union,
                            rate=intersection / union if union else None)

            actual = {kind: score(range(len(matches)), None if kind == 'ALL' else kind)
                      for kind in ('ALL', *KINDS)}
            draws = {kind: [] for kind in ('ALL', *KINDS)}
            for _ in range(permutations):
                order = rng.permutation(len(matches))
                for kind in draws:
                    draws[kind].append(score(order, None if kind == 'ALL' else kind)['rate'])
            null = {kind: distribution(values, actual[kind]['rate']) for kind, values in draws.items()}
            pairs.append(dict(files=[left['file'], right['file']], matched_corners=len(matches),
                              apex_tolerance=.006, actual=actual, shuffled_corners=null,
                              matches=matches))
    return dict(pairs=pairs, convention='정점 근접 일대일 코너 대응; 표시된 (코너,단계,종류)의 교집합/합집합. 부분 관측 표시도 포함하며 미관측은 정상으로 해석하지 않음.')


class _LapSubset:
    """The same analyzer restricted to some valid laps; references stay inside the subset."""

    def __init__(self, analyzer, laps):
        self._analyzer = analyzer
        self.valid_laps = list(laps)

    def __getattr__(self, name):
        return getattr(self._analyzer, name)


MIN_HALF_LAPS = 4
MIN_HALF_OBSERVED = 3


def split_half_cells(module, analyzer, corners):
    """Run the method separately on two halves of one file's valid laps.

    Each half only uses its own laps as personal references, so the halves are
    independent judgements of the same corners. Two splits are kept apart:
    first/second half in time (fuel, tyres and track may drift) and odd/even laps.
    """
    laps = list(analyzer.valid_laps)
    n = len(laps)
    out = dict(valid_laps=n, splits={})
    if n < 2 * MIN_HALF_LAPS:
        out['status'] = 'too_few_laps'
        return out
    out['status'] = 'evaluated'
    for name, (a, b) in (('first_second', (laps[:n // 2], laps[n // 2:])),
                         ('odd_even', (laps[0::2], laps[1::2]))):
        halves = []
        for subset in (a, b):
            result = module.analyze_handling(_LapSubset(analyzer, subset), corners, include_observations=True)
            cells = {}
            for row in (result or {}).get('observations', []):
                for kind in KINDS:
                    key = (row['corner_id'], row['phase'], kind)
                    observed_key = 'counter_observed' if kind == 'countersteer' else 'observed'
                    hit = any(e['kind'] == kind for e in row['events'])
                    cell = cells.setdefault(key, dict(observed=0, observed_hits=0, any_hits=0))
                    cell['observed'] += bool(row[observed_key])
                    cell['observed_hits'] += bool(row[observed_key] and hit)
                    cell['any_hits'] += bool(hit)
            halves.append(dict(laps=[lap.lap_number for lap in subset],
                               cells=[[*key, value['observed'], value['observed_hits'], value['any_hits']]
                                      for key, value in sorted(cells.items())]))
        out['splits'][name] = halves
    return out


def _split_scores(files, split, rng, permutations):
    """Pooled Jaccard of flagged cells and correlation of per-cell rates between halves.

    Null: within each file, phase and kind, corners of the second half are permuted.
    """
    actual = defaultdict(lambda: np.zeros(2))
    draws = defaultdict(lambda: np.zeros((permutations, 2)))
    pairs = defaultdict(list)
    for file in files:
        data = (file.get('split_half') or {}).get('splits', {}).get(split)
        if not data:
            continue
        left = {tuple(c[:3]): c[3:] for c in data[0]['cells']}
        right = {tuple(c[:3]): c[3:] for c in data[1]['cells']}
        corners = sorted({key[0] for key in left} | {key[0] for key in right})
        for phase in PHASES:
            for kind in KINDS:
                def flag(table, corner, which):
                    value = table.get((corner, phase, kind))
                    if value is None:
                        return False
                    return value[2] > 0 if which == 'any' else value[1] > 0

                def rate(table, corner):
                    value = table.get((corner, phase, kind))
                    return value[1] / value[0] if value and value[0] >= MIN_HALF_OBSERVED else None

                for which in ('any', 'observed'):
                    a = np.array([flag(left, c, which) for c in corners], dtype=bool)
                    b = np.array([flag(right, c, which) for c in corners], dtype=bool)
                    for label in ((which, 'ALL'), (which, kind)):
                        actual[label] += [np.sum(a & b), np.sum(a | b)]
                    for i in range(permutations):
                        bb = b[rng.permutation(len(b))]
                        for label in ((which, 'ALL'), (which, kind)):
                            draws[label][i] += [np.sum(a & bb), np.sum(a | bb)]
                ra = [rate(left, c) for c in corners]
                rb = [rate(right, c) for c in corners]
                both = [i for i in range(len(corners)) if ra[i] is not None and rb[i] is not None]
                pairs[kind].append(([ra[i] for i in both], [rb[i] for i in both]))

    def jaccard(value):
        return float(value[0] / value[1]) if value[1] else None

    out = {}
    for (which, kind), value in sorted(actual.items()):
        null = [jaccard(row) for row in draws[(which, kind)]]
        rate = jaccard(value)
        out.setdefault(which, {})[kind] = dict(shared=int(value[0]), union=int(value[1]), rate=rate,
                                               shuffled_corners=distribution(null, rate))

    def correlation(groups, shuffle):
        xs, ys = [], []
        for x, y in groups:
            y = list(y)
            if shuffle and len(y) > 1:
                y = [y[i] for i in rng.permutation(len(y))]
            xs.extend(x)
            ys.extend(y)
        xs, ys = np.array(xs, dtype=float), np.array(ys, dtype=float)
        if len(xs) < 5 or xs.std() == 0 or ys.std() == 0:
            return None, len(xs)
        return float(np.corrcoef(xs, ys)[0, 1]), len(xs)

    rates = {}
    for kind in ('understeer', 'oversteer', 'ALL'):
        groups = [g for k in (KINDS[:2] if kind == 'ALL' else (kind,)) for g in pairs[k]]
        value, cells = correlation(groups, False)
        null = [correlation(groups, True)[0] for _ in range(permutations)]
        rates[kind] = dict(cells=cells, correlation=value, shuffled_corners=distribution(null, value))
    out['rate_correlation'] = rates
    return out


def split_half_summary(files, *, permutations=200, seed=20261023):
    rng = np.random.default_rng(seed + 7)
    result = dict(convention=('파일 하나의 유효 랩을 둘로 나눠 각 절반이 자기 랩만 개인 기준으로 따로 판정; '
                              '같은 (코너,단계,종류) 표시의 교집합/합집합과, 두 절반 모두 관측 3랩 이상인 칸의 '
                              '완전 관측 표시율 상관. 귀무는 파일·단계·종류 안에서 둘째 절반의 코너를 섞음.'),
                  min_half_laps=MIN_HALF_LAPS, min_half_observed=MIN_HALF_OBSERVED,
                  files={file['file']: (file.get('split_half') or {}).get('status') for file in files})
    for split in ('first_second', 'odd_even'):
        result[split] = _split_scores(files, split, rng, permutations)
    return result


def distribution(values, actual=None):
    finite = np.array([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    return dict(n=len(finite), mean=float(finite.mean()) if len(finite) else None,
                p025=float(np.percentile(finite, 2.5)) if len(finite) else None,
                p975=float(np.percentile(finite, 97.5)) if len(finite) else None,
                p_ge_actual=float((1 + np.sum(finite >= actual)) / (len(finite) + 1))
                if len(finite) and actual is not None else None)


def summarize_nulls(nulls):
    result = {}
    nulls = [data for data in nulls if data and data.get('permutations')]
    if not nulls:
        return result
    repetitions = {data['permutations'] for data in nulls}
    if len(repetitions) != 1:
        raise ValueError('귀무 반복 횟수가 다른 보고서는 합칠 수 없습니다.')
    for phase in ('ALL', *PHASES):
        draws = np.zeros((next(iter(repetitions)), 3, 6), dtype=np.int64)
        aligned = np.zeros((3, 3), dtype=np.int64)
        for data in nulls:
            source = data['permutation_counts'] if phase == 'ALL' else data['phase_permutation_counts'].get(phase)
            if source is not None:
                draws += np.asarray(source)
            for k, kind in enumerate(KINDS):
                actual = data['actual'].get(phase, {}).get(kind)
                if actual:
                    aligned[k] += [actual['observed_slots'], actual['observed_hits'], actual['partial_hits']]
        kinds = {}
        for k, kind in enumerate(KINDS):
            count = draws[:, k]
            def fractions(numerator, denominator):
                values = np.full(len(count), np.nan)
                np.divide(count[:, numerator], count[:, denominator], out=values, where=count[:, denominator] > 0)
                return values
            native_rate = aligned[k, 1] / aligned[k, 0] if aligned[k, 0] else None
            null_rate = fractions(1, 0)
            paired_actual, paired_null = fractions(4, 3), fractions(5, 3)
            delta = paired_actual - paired_null
            valid = np.isfinite(delta)
            pa, pn = distribution(paired_actual), distribution(paired_null)
            kinds[kind] = dict(aligned_observed_slots=int(aligned[k, 0]),
                               aligned_observed_hits=int(aligned[k, 1]), aligned_partial_hits=int(aligned[k, 2]),
                               aligned_actual_rate=float(native_rate) if native_rate is not None else None,
                               null_rate=distribution(null_rate, native_rate),
                               paired_actual_rate=pa, paired_null_rate=pn,
                               paired_delta=distribution(delta),
                               paired_actual_over_null=pa['mean'] / pn['mean']
                               if pa['mean'] is not None and pn['mean'] is not None and pn['mean'] > 0 else None,
                               paired_fraction_actual_greater=float(np.mean(delta[valid] > 0)) if np.any(valid) else None,
                               paired_observed_slots=distribution(count[:, 3]))
        result[phase] = kinds
    return result


def default_inputs():
    folders = (Path.home() / 'Documents/iRacing/telemetry',
               Path.home() / 'OneDrive/문서/iRacing/telemetry')
    paths = []
    for name in DEFAULT_NAMES:
        found = next((folder / name for folder in folders if (folder / name).is_file()), None)
        if found is None:
            raise FileNotFoundError(f'기준 주행 기록이 없습니다: {name}')
        paths.append(found)
    return paths


def aggregate(files):
    rows = [row for file in files for row in file['observations']]
    counts = Counter()
    reasons = Counter()
    points = []
    items = []
    for file in files:
        counts.update({key: value for key, value in file['coverage'].items()
                       if key in ('classifiable', 'unknown', 'applicable', 'not_applicable', 'total_slots', 'partial')})
        reasons.update(file['unknown_reasons'])
        points.extend(file['point_values'])
        items.extend(dict(file=file['file'], **item) for item in file['time_auc']['items'])
    counts['unknown_fraction'] = counts['unknown'] / counts['applicable'] if counts['applicable'] else None
    return dict(point_metrics=point_summary(points), slot_rates=slot_rates(rows),
                coverage=dict(counts), unknown_reasons=dict(reasons),
                null=summarize_nulls([file['null'] for file in files]),
                time_auc=dict(items=items, item_count=len(items),
                              mean=float(np.mean([item['auc'] for item in items])) if items else None))


def text_report(report):
    lines = ['차 거동 오프라인 평가', f"방식: {report['method']} / 기준: {report['provenance']}",
             f"파일 {len(report['files'])}개 / 고정 시드 {report['options']['seed']} / 섞기 {report['options']['permutations']}회",
             '실제 native 판정과 상대위치 grid 귀무 비교는 별도 분모입니다. 부분 관측 표시를 숨기지 않습니다.',
             '이 평가는 실제 느낌이나 물리적 언더/오버스티어의 정답 검증이 아닙니다.', '']
    def number(value, scale=1):
        return f'{value * scale:.4f}' if value is not None else 'UNKNOWN'

    def metrics(label, data):
        lines.extend(['', label, f"판정 가능 {data['coverage'].get('classifiable', 0)}/{data['coverage'].get('applicable', 0)} / 확인 필요 {number(data['coverage'].get('unknown_fraction'), 100)}%",
                      f"시간 보정 AUC {number(data['time_auc']['mean'])} / 평가 항목 {data['time_auc']['item_count']}",
                      '단계 | 지점 수 | 흩어짐% | ±15% 초과% | 실제 기준 초과% | 푸는 중% | 꺾는 중%'])
        for phase, value in data['point_metrics'].items():
            lines.append(' | '.join([phase, str(value['points']), *[number(value.get(key)) for key in (
                'scatter_pct', 'outside_pct', 'effective_outside_pct', 'ratio_unwinding_pct', 'ratio_turning_in_pct')]]))
        lines.append('단계 / 종류 | 완전 관측 표시/관측칸 (비율%) | 부분 관측 표시 | 전체 표시 | grid 공통분모 실제%/귀무% | 실제/귀무 배율')
        null = data.get('null', {})
        if 'summary' in null:
            null = null['summary']
        for phase, kinds in data['slot_rates'].items():
            for kind, value in kinds.items():
                n = null.get(phase, {}).get(kind, {})
                pa, pn = n.get('paired_actual_rate', {}).get('mean'), n.get('paired_null_rate', {}).get('mean')
                lines.append(f"{phase} / {kind} | {value['observed_hits']}/{value['observed_slots']} ({number(value['rate'], 100)}) | {value['partial_hits']} | {value['all_hits']} | {number(pa, 100)}/{number(pn, 100)} | {number(n.get('paired_actual_over_null'))}")
        lines.append('판정 불가 이유(중복 가능): ' + json.dumps(data['unknown_reasons'], ensure_ascii=False))

    for label, data in [('전체 입력', report['aggregate']), ('원래 5개 기준선', report['baseline_five'])]:
        metrics(label, data)
    for file in report['files']:
        metrics(f"{file['file']} / 유효 랩 {file['valid_laps']} / 원본 보존 {file['source_unchanged']}", file)
    lines.extend(['', '파일 간 반복률', json.dumps(report['replication'], ensure_ascii=False, indent=2)])
    split = report.get('split_half')
    if split:
        lines.extend(['', '랩 절반 나누기 반복', split['convention'], json.dumps(split['files'], ensure_ascii=False)])
        for name, label in (('first_second', '앞/뒤 절반'), ('odd_even', '홀/짝 랩')):
            data = split[name]
            for which, title in (('any', '부분 관측 포함 표시'), ('observed', '완전 관측 표시만')):
                for kind, value in data.get(which, {}).items():
                    null = value['shuffled_corners']
                    lines.append(f"{label} / {title} / {kind}: {value['shared']}/{value['union']} = {number(value['rate'], 100)}% "
                                 f"/ 코너 섞기 평균 {number(null['mean'], 100)}% / p_ge {number(null['p_ge_actual'])}")
            for kind, value in data['rate_correlation'].items():
                null = value['shuffled_corners']
                lines.append(f"{label} / 표시율 상관 / {kind}: r={number(value['correlation'])} (칸 {value['cells']}) "
                             f"/ 섞기 평균 {number(null['mean'])} [{number(null['p025'])}, {number(null['p975'])}] / p_ge {number(null['p_ge_actual'])}")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    args = parser().parse_args(argv)
    if not (args.permutations > 0 and 0 < args.grid_hz <= 60):
        raise ValueError('섞기 횟수는 양수이고 grid-hz는 0~60이어야 합니다.')
    inputs = [path.resolve(strict=True) for path in (args.files or default_inputs())]
    if any(path.suffix.lower() != '.ibt' for path in inputs):
        raise ValueError('평가 입력은 IBT 주행 기록이어야 합니다.')
    if len(set(inputs)) != len(inputs):
        raise ValueError('같은 입력 기록을 두 번 평가할 수 없습니다.')
    out = args.out or ROOT / f'logs/analysis-210/handling-{args.method}.json'
    json_path, txt_path = output_paths(out, inputs)
    cache_folder = json_path.with_suffix('.files')
    for path in inputs:
        if path == cache_folder or cache_folder in path.parents:
            raise ValueError('입력 파일이 출력 캐시 폴더 안에 있습니다.')
    json_path.parent.mkdir(parents=True, exist_ok=True)
    cache_folder.mkdir(exist_ok=True)
    options = dict(seed=args.seed, permutations=args.permutations, grid_hz=args.grid_hz, skip_null=args.skip_null)
    files = []
    with tempfile.TemporaryDirectory(prefix='handling-evaluation-') as folder:
        sys.path.insert(0, str(ROOT / 'scripts'))
        from measure_corner_events import isolate
        isolate(Path(folder))
        sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
        import backend_api as api
        module, provenance = load_method(args.method, args.candidate)
        null_function = null_evaluation
        evaluator_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        for index, source in enumerate(inputs):
            print(f'평가 중 {index + 1}/{len(inputs)}: {source.name}', flush=True)
            before = fingerprint(source)
            cache_path = cache_folder / (hashlib.sha256(str(source).encode()).hexdigest()[:16] + '.json')
            identity = dict(source=before, provenance=provenance, options=options, evaluator_sha256=evaluator_sha)
            if args.resume and cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding='utf-8'))
                if cached.get('identity') == identity:
                    files.append(cached['result'])
                    print('원본·방식·옵션이 같은 평가 결과 재사용', flush=True)
                    continue
            telemetry = api.repository.load(source)
            analyzer = api.TelemetryAnalyzer(telemetry)
            analyzer._handling_analysis = lambda corners: None
            analyzed = analyzer.analyze(api.AnalyzeRequest(filename=source.name, include_ai=False, persist_memory=False))
            corners = [{key: corner[key] for key in ('id', 'label', 'entry_pct', 'apex_pct', 'exit_pct')}
                       for corner in analyzed['performance']['corners']]
            native, captures, points = native_evaluation(module, analyzer, corners)
            rows = native['observations']
            if args.skip_null:
                null = {}
            else:
                null = null_function(module, captures, rows, permutations=args.permutations,
                                     seed=args.seed + index, grid_hz=args.grid_hz)
                null['summary'] = summarize_nulls([null])
            split = split_half_cells(module, analyzer, corners)
            metadata = telemetry.metadata
            context = [metadata.get('game', 'iracing'), metadata.get('car_path') or metadata.get('car_model_key'),
                       metadata.get('track_platform_id') or metadata.get('track_path'),
                       metadata.get('track_config') or metadata.get('track_layout') or '']
            if context[1] is None or context[2] is None:
                # Unknown identity must never group unrelated files together.
                context.append(source.name)
            after = fingerprint(source)
            if before != after:
                raise RuntimeError('평가 전후 원본 파일이 변경되었습니다.')
            result = dict(file=source.name, fingerprint=before, source_unchanged=True,
                          valid_laps=len(analyzer.valid_laps), context=context, corners=corners,
                          observations=rows, coverage=native['coverage'], unknown_reasons=native['unknown_reasons'],
                          point_values=points, point_metrics=point_summary(points), slot_rates=slot_rates(rows),
                          time_auc=adjusted_auc(rows, corners), null=null, split_half=split,
                          steer_lag_s=native.get('steer_lag_s'), fast_steer_rate_rad_s=native.get('fast_steer_rate_rad_s'))
            cache_path.write_text(json.dumps(dict(identity=identity, result=result), ensure_ascii=False, allow_nan=False), encoding='utf-8')
            files.append(result)
            print(f"완료: 판정 가능 {native['coverage']['classifiable']}/{native['coverage']['applicable']}", flush=True)
            api.repository._cache.clear()
            del telemetry, analyzer, analyzed, native, captures, rows, points
            gc.collect()
    baseline = [file for file in files if file['file'] in DEFAULT_NAMES[:5]]
    report = dict(method=args.method, provenance=provenance, evaluator_sha256=evaluator_sha, options=options,
                  files=files, aggregate=aggregate(files), baseline_five=aggregate(baseline),
                  replication=repeated_files(files, permutations=args.permutations, seed=args.seed),
                  split_half=split_half_summary(files, permutations=args.permutations, seed=args.seed))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')
    txt_path.write_text(text_report(report), encoding='utf-8')
    print(f'평가 보고서 저장: {json_path.name}, {txt_path.name}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
