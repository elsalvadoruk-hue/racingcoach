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
MATCH_CONDITIONS = ('speed_quintile', 'speed_relative', 'brake', 'throttle',
                    'steer_direction', 'lateral_direction', 'lateral_magnitude')
MATCH_LABELS = ('속도 5분위', '속도 4%', '브레이크', '스로틀',
                '핸들 방향', '횡G 방향', '횡G 크기')
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
    p.add_argument('--method', choices=('v1', 'v2', 'v3', 'v4'), default='v1')
    p.add_argument('--candidate', type=Path, help='평가만 할 로컬 후보 모듈')
    p.add_argument('--permutations', type=int, default=200)
    p.add_argument('--skip-null', action='store_true',
                   help='위치 귀무 비교를 건너뛰고 원본 판정·랩 절반 반복만 계산')
    p.add_argument('--seed', type=int, default=20261023)
    p.add_argument('--out', type=Path)
    p.add_argument('--grid-hz', type=float, default=20.)
    p.add_argument('--resume', action='store_true', help='원본·방식·옵션 해시가 같은 파일별 결과만 재사용')
    p.add_argument('--diagnose-matching', action='store_true',
                   help='v3 비교 조건별 탈락만 진단합니다. 판정 변경·귀무 재평가 없음')
    p.add_argument('--diagnose-braking', action='store_true',
                   help='v3 제동 구간·위치 오차·같은 페달의 종G 차이를 진단합니다')
    p.add_argument('--include-population-diagnosis', action='store_true',
                   help='단계 시간 대비 판정 대상 비율과 제외 이유를 추가 집계합니다. 판정 변경 없음')
    return p


def empty_matching_counts():
    return dict(samples=0, reference_pairs=0, available_pairs=0, matched_pairs=0,
                below_three_samples=0, available_below_three_samples=0,
                speed_model_unavailable_samples=0, unavailable_phases=0,
                conditions={key: dict(rejected_pairs=0, rescued_samples=0)
                            for key in MATCH_CONDITIONS})


def matching_diagnosis(module, phase, references, model):
    """Count independent rejections and leave-one-condition-out support only.

    Use the product's native required-time samples, without stride or a new
    grid. Missing reference intervals are unavailable, never condition failures.
    Reaching three other laps is not proof of classifiability: direction,
    200-sample support, noise, and complete-phase guards still apply.
    """
    result = empty_matching_counts()
    if phase is None:
        result['unavailable_phases'] = 1
        return result
    selected = phase['inside'] & phase.get('q_ready', phase['eligible']) & (phase['durations'] > 0)
    result['samples'] = int(selected.sum())
    if not np.all(np.isfinite(model['edges_mps'])):
        result['speed_model_unavailable_samples'] = result['samples']
        return result
    c = phase['local'].channels
    speed, steer, brake, throttle, lateral = (c[k][selected] for k in
        ('Speed', 'SteeringWheelAngle', 'Brake', 'Throttle', 'LatAccel'))
    available = np.zeros(result['samples'], dtype=int)
    matched = np.zeros_like(available)
    without = {key: np.zeros_like(available) for key in MATCH_CONDITIONS}
    for reference in references:
        if reference is None or reference is phase:
            continue
        q, rv, rs, rb, rt, ra = module._reference_at(reference, phase['local'].pct[selected])
        base = np.isfinite(q) & (q > 0)
        conditions = (
            np.searchsorted(model['edges_mps'], rv, side='right') == phase['bands'][selected],
            abs(rv - speed) <= module.MAX_SPEED_RELATIVE * speed,
            abs(rb - brake) <= module.MAX_PEDAL_DIFFERENCE,
            abs(rt - throttle) <= module.MAX_PEDAL_DIFFERENCE,
            rs * steer > 0,
            ra * lateral > 0,
            abs(ra - lateral) <= np.maximum(module.MAX_LAT_ACCEL_ABS,
                                          module.MAX_LAT_ACCEL_RELATIVE * abs(lateral)),
        )
        failures = np.sum(~np.asarray(conditions), axis=0)
        result['reference_pairs'] += result['samples']
        available += base
        matched += base & (failures == 0)
        for key, passed in zip(MATCH_CONDITIONS, conditions):
            result['conditions'][key]['rejected_pairs'] += int(np.sum(base & ~passed))
            without[key] += base & (failures == (~passed).astype(int))
    shortage = matched < module.MIN_REFERENCE_LAPS
    result['available_pairs'] = int(available.sum())
    result['matched_pairs'] = int(matched.sum())
    result['below_three_samples'] = int(shortage.sum())
    result['available_below_three_samples'] = int(np.sum(available < module.MIN_REFERENCE_LAPS))
    for key in MATCH_CONDITIONS:
        result['conditions'][key]['rescued_samples'] = int(np.sum(
            shortage & (without[key] >= module.MIN_REFERENCE_LAPS)))
    return result


def merge_matching_counts(values):
    result = empty_matching_counts()
    for value in values:
        for key in result:
            if key != 'conditions':
                result[key] += value[key]
        for key in MATCH_CONDITIONS:
            for metric in ('rejected_pairs', 'rescued_samples'):
                result['conditions'][key][metric] += value['conditions'][key][metric]
    return result


def matching_by_phase(module, captures, rows):
    if len(captures) != len(rows):
        raise ValueError('진단 캡처와 관측 수가 다릅니다.')
    grouped = {name: [] for name in PHASES}
    for (phase, references, model), row in zip(captures, rows):
        grouped[row['phase']].append(matching_diagnosis(module, phase, references, model))
    return {name: merge_matching_counts(values) for name, values in
            [('ALL', [item for values in grouped.values() for item in values]), *grouped.items()]}


def matching_text_report(report):
    lines = ['N2 비교 조건별 탈락 진단 (제품 판정 변경 없음)',
             '표본 분모: 제품 required = 단계 내부 & q_ready & 양의 관측 시간. 간격 재표본화·간추림 없음.',
             '탈락은 같은 위치에서 q가 관측된 다른 랩-표본 쌍 중 해당 조건을 어긴 수(중복 가능).',
             '단독 제외는 현재 3랩 미만이지만 그 조건 하나만 빼면 3랩 이상인 대상 표본 수입니다.',
             '비율 분모는 전체 대상 표본과 현재 3랩 미만 표본을 각각 적습니다.',
             '3랩 확보는 판정 가능 회복과 다릅니다. 200표본·방향·흩어짐·단계 완전성 조건은 그대로 남습니다.',
             '누락 참조 구간과 속도 모델 미확정은 조건 탈락에 넣지 않습니다. 조건 완화는 사용자 결정 전 보류.', '']
    groups = [('전체 6파일', report['matching_diagnosis']),
              ('원래 5파일', report['baseline_five_matching'])]
    groups += [(file['file'], file['matching_diagnosis']) for file in report['files']]
    for label, phases in groups:
        lines.append(label)
        for phase, value in phases.items():
            n, short = value['samples'], value['below_three_samples']
            lines.append(f"{phase}: 대상 {n}, 3랩 미만 {short}, 위치 관측 자체 3랩 미만 {value['available_below_three_samples']}, "
                         f"속도 모델 미확정 {value['speed_model_unavailable_samples']}, 단계 미관측 {value['unavailable_phases']}")
            lines.append('조건 | 탈락 쌍/관측 쌍 | 단독 제외 시 3랩 확보/전체 표본 (%) | 3랩 미만 표본 대비 (%)')
            for key, title in zip(MATCH_CONDITIONS, MATCH_LABELS):
                item = value['conditions'][key]
                rescued = item['rescued_samples']
                pct = f'{rescued / n * 100:.4f}' if n else 'UNKNOWN'
                short_pct = f'{rescued / short * 100:.4f}' if short else 'UNKNOWN'
                lines.append(f"{title} | {item['rejected_pairs']}/{value['available_pairs']} | {rescued}/{n} ({pct}) | {short_pct}")
        lines.append('')
    return '\n'.join(lines)


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
        if phase.get('long_accel_matching', False):
            ok &= module._long_accel_matches(phase['long_accel'], module._long_accel_at(reference, phase['local'].pct))
        predictions.append(np.where(ok, q, np.nan))
    return np.array(predictions), support


def point_values(module, phase, references, model, *, stride=3, include_position=False):
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
    indices = np.flatnonzero(selected & (count >= module.MIN_REFERENCE_LAPS))[::stride]
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
        if include_position:
            result[-1].append(float((phase['local'].pct[i] - phase['start']) / (phase['end'] - phase['start'])))
    return result


def phase_channel(phase, key):
    """Optional native channel, without changing the product's REQUIRED list."""
    raw = phase['raw']
    values = raw.channels.get(key)
    if values is None or len(values) != len(raw.time_s):
        return np.full(len(phase['local'].pct), np.nan)
    start = phase['offset']
    return np.asarray(values[start:start + len(phase['local'].pct)], dtype=float)


def reference_channel_at(reference, pct, key):
    """Use the product's q-valid spans, also splitting missing optional values."""
    result = np.full(len(pct), np.nan)
    seen = np.zeros(len(pct), dtype=int)
    values = phase_channel(reference, key)
    for span in reference['segments']:
        axis = reference['local'].pct[span]
        data = values[span]
        connected = np.isfinite(data[:-1]) & np.isfinite(data[1:])
        changes = np.diff(np.r_[False, connected, False].astype(int))
        for a, b in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            x, y = axis[a:b + 1], data[a:b + 1]
            keep = ((pct >= max(x[0], reference['start'])) & (pct <= min(x[-1], reference['end'])))
            result[keep] = np.interp(pct[keep], x, y)
            seen[keep] += 1
    result[seen != 1] = np.nan
    return result


def empty_braking_data():
    return dict(phase_counts={name: dict(defined=0, eligible=0, required=0, three_references=0) for name in PHASES},
                points=[], shifts={str(m): [] for m in (-2., -1., -.5, .5, 1., 2.)},
                long_differences=dict(brake=[], both_pedals=[]), long_target_samples=0)


def braking_diagnosis(module, captures, rows):
    """Native sample diagnosis only; hold reference membership fixed for shifts."""
    result = empty_braking_data()
    for (phase, references, model), row in zip(captures, rows):
        if phase is None:
            continue
        c = phase['local'].channels
        inside = phase['inside'] & (phase['durations'] > 0)
        eligible = inside & phase['eligible']
        required = inside & phase.get('q_ready', phase['eligible'])
        matrix, _ = matched_matrix(module, phase, references, model)
        counts = result['phase_counts'][row['phase']]
        counts['defined'] += int(inside.sum())
        counts['eligible'] += int(eligible.sum())
        counts['required'] += int(required.sum())
        if len(matrix):
            counts['three_references'] += int(np.sum(required & (np.isfinite(matrix).sum(axis=0) >= module.MIN_REFERENCE_LAPS)))
        if row['phase'] != 'braking':
            continue
        result['points'].extend(point_values(module, phase, references, model, stride=1, include_position=True))
        other = [p for p in references if p is not None and p is not phase]
        own_long = phase_channel(phase, 'LongAccel')
        result['long_target_samples'] += int(np.sum(required & np.isfinite(own_long)))
        for reference in other:
            values = module._reference_at(reference, phase['local'].pct)
            ref_long = reference_channel_at(reference, phase['local'].pct, 'LongAccel')
            base = required & np.isfinite(own_long) & np.isfinite(ref_long) & np.isfinite(values[0]) & (values[0] > 0)
            same_brake = abs(values[3] - c['Brake']) <= module.MAX_PEDAL_DIFFERENCE
            same_throttle = abs(values[4] - c['Throttle']) <= module.MAX_PEDAL_DIFFERENCE
            difference = abs(ref_long - own_long)
            result['long_differences']['brake'].append(difference[base & same_brake])
            result['long_differences']['both_pedals'].append(difference[base & same_brake & same_throttle])
        if not len(matrix):
            continue
        selected = (eligible & (phase.get('q_steer', c['SteeringWheelAngle']) * c['YawRate'] > 0)
                    & np.isfinite(phase['q']) & (phase['q'] > 0))
        for distance, batches in result['shifts'].items():
            axis = phase['local'].pct + float(distance) / phase['local'].track_length_m
            shifted = np.array([module._reference_at(ref, axis)[0] for ref in other])
            common = np.isfinite(matrix) & (matrix > 0) & np.isfinite(shifted) & (shifted > 0)
            keep = selected & (common.sum(axis=0) >= module.MIN_REFERENCE_LAPS)
            if not np.any(keep):
                continue
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', RuntimeWarning)
                before = np.nanmedian(np.log(np.where(common[:, keep], matrix[:, keep], np.nan)), axis=0)
                after = np.nanmedian(np.log(np.where(common[:, keep], shifted[:, keep], np.nan)), axis=0)
            batches.append(np.expm1(before - after) * 100)
    return result


def merge_braking_data(values):
    result = empty_braking_data()
    for value in values:
        for phase in PHASES:
            for key in result['phase_counts'][phase]:
                result['phase_counts'][phase][key] += value['phase_counts'][phase][key]
        result['points'].extend(value['points'])
        for key in result['shifts']:
            result['shifts'][key].extend(value['shifts'][key])
        for key in result['long_differences']:
            result['long_differences'][key].extend(value['long_differences'][key])
        result['long_target_samples'] += value['long_target_samples']
    return result


def braking_summary(data):
    def summary(batches, signed=False):
        values = np.concatenate(batches) if batches else np.array([])
        values = values[np.isfinite(values)]
        result = dict(n=len(values), median=float(np.median(values)) if len(values) else None,
                      p90=float(np.percentile(values, 90)) if len(values) else None)
        if signed:
            result.update(median_abs=float(np.median(abs(values))) if len(values) else None,
                          p90_abs=float(np.percentile(abs(values), 90)) if len(values) else None)
        return result

    points = data['points']
    thirds = {}
    for i, label in enumerate(('first', 'middle', 'last')):
        selected = [('braking', r[:5]) for r in points if min(2, int(r[5] * 3)) == i]
        thirds[label] = point_summary(selected)['braking']
    counts = data['phase_counts']
    short = [name for name in ('rotation', 'drive') if counts['braking']['required'] * 2 < counts[name]['required']]
    return dict(phase_counts=counts, thirds=thirds,
                shifts={key: summary(value, signed=True) for key, value in data['shifts'].items()},
                long_differences={key: summary(value) for key, value in data['long_differences'].items()},
                long_target_samples=data['long_target_samples'], stop_before_candidate=bool(short),
                less_than_half_of=short,
                definition_also_short=[name for name in short if counts['braking']['defined'] * 2 < counts[name]['defined']])


def braking_text_report(report):
    lines = ['N3 제동 구간 진단 (제품 v3 유지, 종G 후보 미실행)',
             '상대 위치는 단계의 거리 범위를 3등분합니다. 지점 진단은 간추림 없이 같은 위치 3랩 이상이며 200표본 조건 전입니다.',
             '흩어짐은 log q의 1.4826 MAD를 비율로 환산합니다. 실제 기준 초과는 200표본·흩어짐·조향 변화 조건도 적용하되 지속 시간 전입니다.',
             '위치 이동은 원래 조건에 맞은 비교 랩을 고정하고 참조 q만 이동합니다. 이동 전후 모두 관측된 공통3랩 이상에서 같은 집단의 log 중앙값을 비교합니다.',
             'q 비율 변화(%) = 100 × (이동 후 q/기준 ÷ 이동 전 q/기준 − 1). 비교 상대 재선택 효과는 포함하지 않습니다.',
             '종G 차이는 m/s²이며 다른 랩의 같은 위치·q 관측 쌍에서 브레이크 ±0.2만/두 페달 모두 ±0.2를 나눠 셉니다.',
             '같은 파일 여러 표본/랩 쌍이 포함됩니다. 독립 실주행 검증이나 원인 확정은 아닙니다.', '']
    groups = [('전체 6파일', report['braking_diagnosis']), ('원래 5파일', report['baseline_five_braking'])]
    groups += [(f['file'], f['braking_diagnosis']) for f in report['files']]
    for label, value in groups:
        lines.extend([label, '단계 | 정의된 표본 | 입력/채널 적격 | 판정 대상 | 비교 조건 후 3랩 확보'])
        for name, counts in value['phase_counts'].items():
            lines.append(name + ' | ' + ' | '.join(str(n) for n in counts.values()))
        lines.append('상대 위치 | 지점 수 | 흩어짐% | ±15% 초과% | 실제 기준 초과%')
        for name, counts in value['thirds'].items():
            lines.append(name + ' | ' + ' | '.join(str(counts.get(k)) for k in ('points', 'scatter_pct', 'outside_pct', 'effective_outside_pct')))
        lines.append('위치 이동m | 관측 수 | 부호 포함 중앙% | 절댓값 중앙% | 절댓값90백분위%')
        for name, counts in value['shifts'].items():
            lines.append(name + ' | ' + ' | '.join(str(counts[k]) for k in ('n', 'median', 'median_abs', 'p90_abs')))
        lines.append('같은 페달 안 종G 절대 차이: ' + json.dumps(value['long_differences'], ensure_ascii=False))
        lines.append('후보 전 중단 조건: ' + json.dumps({k: value[k] for k in ('stop_before_candidate', 'less_than_half_of', 'definition_also_short')}, ensure_ascii=False))
        lines.append('')
    return '\n'.join(lines)


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


POPULATION_REASONS = ('boundary_interval', 'missing_required_interval', 'speed_below_minimum',
                      'steer_below_minimum', 'aligned_steer_below_minimum', 'other_excluded')


def empty_population():
    return {name: dict(instances=0, unavailable_instances=0, phase_samples=0, phase_seconds=0.,
                       required_samples=0, required_seconds=0.,
                       reasons={key: dict(samples=0, seconds=0.) for key in POPULATION_REASONS},
                       low_steer_independent=dict(samples=0, seconds=0.)) for name in PHASES}


def population_diagnosis(module, captures, rows, *, native=None):
    """Separate phase duration from target eligibility; never alter a mask."""
    if len(captures) != len(rows):
        raise ValueError('진단 캡처와 관측 수가 다릅니다.')
    result = empty_population()
    for (phase, _, _), row in zip(captures, rows):
        out = result[row['phase']]
        if phase is None:
            out['unavailable_instances'] += 1
            continue
        out['instances'] += 1
        local = phase['local']
        dp = np.diff(local.pct)
        a, b = np.zeros(len(dp)), np.ones(len(dp))
        np.divide(phase['start'] - local.pct[:-1], dp, out=a, where=dp > 0)
        np.divide(phase['end'] - local.pct[:-1], dp, out=b, where=dp > 0)
        duration = np.zeros(len(local.pct))
        duration[:-1] = np.where(module._edges(local) & (dp > 0),
                                np.maximum(0., np.minimum(1., b) - np.maximum(0., a)) * np.diff(local.time_s), 0.)
        base = duration > 0
        inside = phase['inside']
        out['phase_samples'] += int(np.sum(base & inside))
        out['phase_seconds'] += float(duration.sum())
        required = inside & phase.get('q_ready', phase['eligible']) & (phase['durations'] > 0)
        out['required_samples'] += int(required.sum())
        out['required_seconds'] += float(phase['durations'][required].sum())
        c = local.channels
        finite = np.ones(len(base), dtype=bool)
        for key in module.REQUIRED:
            values = c[key]
            finite &= np.isfinite(values) & np.r_[np.isfinite(values[1:]), False]
        steer_low = np.isfinite(c['SteeringWheelAngle']) & (abs(c['SteeringWheelAngle']) <= module.MIN_STEER)
        qsteer = phase.get('q_steer', c['SteeringWheelAngle'])
        masks = (~inside, ~finite, c['Speed'] <= module.MIN_SPEED, steer_low,
                 np.isfinite(qsteer) & (abs(qsteer) <= module.MIN_STEER), ~required)
        remaining = base.copy()
        for key, mask in zip(POPULATION_REASONS, masks):
            selected = remaining & mask
            out['reasons'][key]['samples'] += int(np.sum(selected & inside))
            out['reasons'][key]['seconds'] += float(duration[selected].sum())
            remaining &= ~mask
        if not np.array_equal(remaining, required):
            raise RuntimeError('진단의 판정 대상 마스크가 제품과 다릅니다.')
        independent = base & inside & steer_low
        out['low_steer_independent']['samples'] += int(independent.sum())
        out['low_steer_independent']['seconds'] += float(duration[independent].sum())
    if native is not None:
        # Undefined phases are counted in coverage but never sent to classify().
        unavailable = Counter()
        for entry in native.get('phase_coverage', []):
            reasons = entry.get('unknown_reasons', {})
            unavailable[entry['phase']] += reasons.get('phase_unavailable', 0) + reasons.get('raw_lap_unavailable', 0)
        for phase, count in unavailable.items():
            result[phase]['unavailable_instances'] = max(result[phase]['unavailable_instances'], count)
    return result


def merge_population(values):
    result = empty_population()
    for value in values:
        for phase, out in result.items():
            for key in ('instances', 'unavailable_instances', 'phase_samples', 'phase_seconds', 'required_samples', 'required_seconds'):
                out[key] += value[phase][key]
            for key in POPULATION_REASONS:
                for metric in ('samples', 'seconds'):
                    out['reasons'][key][metric] += value[phase]['reasons'][key][metric]
            for metric in ('samples', 'seconds'):
                out['low_steer_independent'][metric] += value[phase]['low_steer_independent'][metric]
    return result


def population_summary(value):
    result = json.loads(json.dumps(value))
    total = sum(p['phase_seconds'] for p in result.values())
    def fraction(n, d):
        return n / d if d else None
    for phase in result.values():
        phase['mean_phase_seconds'] = fraction(phase['phase_seconds'], phase['instances'])
        phase['phase_time_share'] = fraction(phase['phase_seconds'], total)
        phase['required_time_fraction'] = fraction(phase['required_seconds'], phase['phase_seconds'])
        phase['required_sample_fraction'] = fraction(phase['required_samples'], phase['phase_samples'])
        excluded_samples = phase['phase_samples'] - phase['required_samples']
        excluded_seconds = phase['phase_seconds'] - phase['required_seconds']
        for reason in [*phase['reasons'].values(), phase['low_steer_independent']]:
            reason['phase_sample_fraction'] = fraction(reason['samples'], phase['phase_samples'])
            reason['phase_time_fraction'] = fraction(reason['seconds'], phase['phase_seconds'])
            reason['excluded_sample_fraction'] = fraction(reason['samples'], excluded_samples)
            reason['excluded_time_fraction'] = fraction(reason['seconds'], excluded_seconds)
    return result


def population_text_report(report):
    lines = ['추가 진단: (a) 단계 길이/판정 대상 시간 (b) 제외 이유',
             '시간 분모는 단계 경계로 자른 관측된 전진 구간 시간입니다. 단절 시간은 추정하지 않습니다.',
             '단계 미정의/원시 자료 없음은 unavailable_instances로 분리하고 시간 분모에 넣지 않습니다.',
             '표본 수는 단계 안 구간 왼쪽 표본만 셉니다. 경계 직전 표본의 잘린 시간은 boundary_interval로 별도 집계합니다.',
             '제외 이유는 경계→필수 채널 결측→속도≤15m/s→현재 핸들각≤0.03rad→보정 핸들각≤0.03rad 순서의 배타적 집계입니다.',
             'low_steer_independent는 다른 제외 이유와 겹칠 수 있는 현재 핸들각 최소값 이하 전체입니다. 제동 단계에서 직선 제동의 진단용 대리값이며 실제 주행선의 직선 판정은 아닙니다.',
             '판정 대상 마스크는 기존 v3와 동일합니다. 비교 상대 조건에 의한 탈락은 이 표의 제외 이유가 아닙니다.', '']
    groups = [('전체6파일', report['population_diagnosis']), ('원래5파일', report['baseline_five_population'])]
    groups += [(file['file'], file['population_diagnosis']) for file in report['files']]
    for label, data in groups:
        lines += [label, '단계 | 관측 시간s | 평균 단계 시간s | 단계 시간 비중 | 대상 시간s | 대상/단계 시간 | 대상 표본/단계 표본']
        for phase, value in data.items():
            lines.append(phase + ' | ' + ' | '.join(str(value[k]) for k in ('phase_seconds', 'mean_phase_seconds', 'phase_time_share', 'required_seconds', 'required_time_fraction'))
                         + f" | {value['required_samples']}/{value['phase_samples']}")
            lines.append('제외 이유 | 표본 수 | 시간s | 단계 시간 비율 | 제외 시간 중 비율')
            for key, reason in [*value['reasons'].items(), ('low_steer_independent (중복 가능)', value['low_steer_independent'])]:
                lines.append(key + ' | ' + ' | '.join(str(reason[k]) for k in ('samples', 'seconds', 'phase_time_fraction', 'excluded_time_fraction')))
        lines.append('')
    return '\n'.join(lines)


def native_evaluation(module, analyzer, corners, *, collect_points=True):
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

    def classify(phase, references, model, **kwargs):
        result = original_classify(phase, references, model, **kwargs)
        captures.append((phase, references, model))
        if phase is not None and collect_points:
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
        if hasattr(module, 'MAX_LONG_ACCEL_ABS'):
            target = np.vstack([target, _interp(p, axis[:-1], p['long_accel']),
                                np.full(bins, p['long_accel_matching'], dtype=float)])
            reference = np.vstack([reference, module._long_accel_at(p, axis[:-1])])
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
    q, v, steer, brake, throttle, ay, yaw, qsteer = target[:, :8].transpose(1, 0, 2)
    rq, rv, rs, rb, rt, ra = references[:, :6].transpose(1, 0, 2)
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
    if hasattr(module, 'MAX_LONG_ACCEL_ABS'):
        long_ok = module._long_accel_matches(target[:, 8, None, :], references[None, :, 6, :])
        ok &= (target[:, 9, None, :] == 0) | long_ok
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
                                      for key, value in sorted(cells.items())],
                               time_links=[[item['corner_id'], item['phase'], item['kind'], item.get('time_link')]
                                           for item in (result or {}).get('corners', [])]))
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
        result[split]['time_link'] = time_link_replication(files, split, permutations=permutations, seed=seed)
    return result


def time_link_replication(files, split, *, permutations=200, seed=20261023):
    """Directional replication: first-half slower items found slower again."""
    rng = np.random.default_rng(seed + 19)
    shared = first = second = testable = 0
    draws = np.zeros(permutations, dtype=int)
    for file in files:
        halves = (file.get('split_half') or {}).get('splits', {}).get(split)
        if not halves:
            continue
        left = {tuple(item[:3]): item[3] for item in halves[0].get('time_links', [])}
        right = {tuple(item[:3]): item[3] for item in halves[1].get('time_links', [])}
        corners = sorted({cell[0] for half in halves for cell in half['cells']})
        for phase in PHASES:
            for kind in KINDS:
                a = np.array([left.get((c, phase, kind)) == 'slower' for c in corners])
                b = np.array([right.get((c, phase, kind)) == 'slower' for c in corners])
                first += int(a.sum())
                second += int(b.sum())
                shared += int(np.sum(a & b))
                testable += sum(bool(hit) and right.get((c, phase, kind)) is not None
                                for c, hit in zip(corners, a))
                for i in range(permutations):
                    draws[i] += int(np.sum(a & b[rng.permutation(len(b))]))
    rate = shared / first if first else None
    return dict(first_slower=first, second_slower=second, shared=shared, second_testable=testable,
                rate=rate, shuffled_corners=distribution(draws / first if first else [], rate),
                convention='앞 절반 slower 중 뒤 절반도 slower인 비율. 뒤 절반 자료 부족도 미반복으로 셈; 파일·단계·종류 안 코너 섞기 200회와 비교.')


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
                time_link_counts=dict(Counter(item.get('time_link') or 'unknown'
                                              for file in files for item in file.get('summaries', []))),
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
        if 'time_link_counts' in data:
            lines.append('시간 연결 항목 수: ' + json.dumps(data['time_link_counts'], ensure_ascii=False))

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
            link = data.get('time_link')
            if link:
                lines.append(f"{label} / 시간 연결 반복: {link['shared']}/{link['first_slower']} = {number(link['rate'], 100)}% "
                             f"/ 뒤 절반 slower {link['second_slower']} / 뒤 절반 비교 가능 {link['second_testable']} "
                             f"/ 코너 섞기 평균 {number(link['shuffled_corners']['mean'], 100)}%")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    args = parser().parse_args(argv)
    if (args.diagnose_matching or args.diagnose_braking) and args.method != 'v3':
        raise ValueError('비교 조건 진단은 --method v3로 실행하세요.')
    if args.diagnose_braking and (args.resume or args.diagnose_matching):
        raise ValueError('제동 진단은 --resume 또는 --diagnose-matching과 함께 실행하지 않습니다.')
    if args.include_population_diagnosis and args.method not in ('v3', 'v4'):
        raise ValueError('대상 표본 진단은 --method v3 또는 v4로 실행하세요.')
    if not (args.permutations > 0 and 0 < args.grid_hz <= 60):
        raise ValueError('섞기 횟수는 양수이고 grid-hz는 0~60이어야 합니다.')
    inputs = [path.resolve(strict=True) for path in (args.files or default_inputs())]
    if any(path.suffix.lower() != '.ibt' for path in inputs):
        raise ValueError('평가 입력은 IBT 주행 기록이어야 합니다.')
    if len(set(inputs)) != len(inputs):
        raise ValueError('같은 입력 기록을 두 번 평가할 수 없습니다.')
    default_name = 'n3-diagnosis' if args.diagnose_braking else 'n2-diagnosis' if args.diagnose_matching else args.method
    out = args.out or ROOT / f'logs/analysis-210/handling-{default_name}.json'
    json_path, txt_path = output_paths(out, inputs)
    cache_folder = json_path.with_suffix('.files')
    for path in inputs:
        if path == cache_folder or cache_folder in path.parents:
            raise ValueError('입력 파일이 출력 캐시 폴더 안에 있습니다.')
    json_path.parent.mkdir(parents=True, exist_ok=True)
    cache_folder.mkdir(exist_ok=True)
    options = dict(seed=args.seed, permutations=args.permutations, grid_hz=args.grid_hz, skip_null=args.skip_null)
    if args.diagnose_matching:
        options['diagnose_matching'] = True
    if args.diagnose_braking:
        options['diagnose_braking'] = True
    if args.include_population_diagnosis:
        options['include_population_diagnosis'] = True
    files = []
    braking_values = []
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
            native, captures, points = native_evaluation(module, analyzer, corners,
                                                       collect_points=not (args.diagnose_matching or args.diagnose_braking))
            rows = native['observations']
            if args.skip_null or args.diagnose_matching or args.diagnose_braking:
                null = {}
            else:
                null = null_function(module, captures, rows, permutations=args.permutations,
                                     seed=args.seed + index, grid_hz=args.grid_hz)
                null['summary'] = summarize_nulls([null])
            split = {} if args.diagnose_matching or args.diagnose_braking else split_half_cells(module, analyzer, corners)
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
                          summaries=native['corners'], time_link_counts=dict(Counter(
                              item.get('time_link') or 'unknown' for item in native['corners'])),
                          point_values=points, point_metrics=point_summary(points), slot_rates=slot_rates(rows),
                          time_auc=adjusted_auc(rows, corners), null=null, split_half=split,
                          steer_lag_s=native.get('steer_lag_s'), fast_steer_rate_rad_s=native.get('fast_steer_rate_rad_s'))
            if args.diagnose_matching:
                result['matching_diagnosis'] = matching_by_phase(module, captures, rows)
            if args.diagnose_braking:
                data = braking_diagnosis(module, captures, rows)
                result['braking_diagnosis'] = braking_summary(data)
                braking_values.append((source.name, data))
            if args.include_population_diagnosis:
                result['population_diagnosis'] = population_summary(population_diagnosis(module, captures, rows, native=native))
            if hasattr(module, 'MAX_LONG_ACCEL_ABS'):
                result['long_accel_matching'] = native['long_accel_matching']
            cache_path.write_text(json.dumps(dict(identity=identity, result=result), ensure_ascii=False, allow_nan=False), encoding='utf-8')
            files.append(result)
            print(f"완료: 판정 가능 {native['coverage']['classifiable']}/{native['coverage']['applicable']}", flush=True)
            api.repository._cache.clear()
            del telemetry, analyzer, analyzed, native, captures, rows, points
            gc.collect()
    baseline = [file for file in files if file['file'] in DEFAULT_NAMES[:5]]
    report = dict(method=args.method, provenance=provenance, evaluator_sha256=evaluator_sha, options=options,
                  files=files, aggregate=aggregate(files), baseline_five=aggregate(baseline),
                  replication={} if args.diagnose_matching or args.diagnose_braking else repeated_files(files, permutations=args.permutations, seed=args.seed),
                  split_half={} if args.diagnose_matching or args.diagnose_braking else split_half_summary(files, permutations=args.permutations, seed=args.seed))
    if args.diagnose_matching:
        for key, selected in [('matching_diagnosis', files), ('baseline_five_matching', baseline)]:
            report[key] = {phase: merge_matching_counts(file['matching_diagnosis'][phase] for file in selected)
                           for phase in ('ALL', *PHASES)}
    if args.diagnose_braking:
        report['braking_diagnosis'] = braking_summary(merge_braking_data(data for _, data in braking_values))
        report['baseline_five_braking'] = braking_summary(merge_braking_data(
            data for name, data in braking_values if name in DEFAULT_NAMES[:5]))
    if args.include_population_diagnosis:
        report['population_diagnosis'] = population_summary(merge_population(file['population_diagnosis'] for file in files))
        report['baseline_five_population'] = population_summary(merge_population(file['population_diagnosis'] for file in baseline))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')
    render = braking_text_report if args.diagnose_braking else matching_text_report if args.diagnose_matching else text_report
    txt_path.write_text(render(report) + ('\n\n' + population_text_report(report) if args.include_population_diagnosis else ''), encoding='utf-8')
    print(f'평가 보고서 저장: {json_path.name}, {txt_path.name}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
