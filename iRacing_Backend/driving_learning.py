"""Bounded empirical driving patterns. Observed associations, never physical optima.

Each comparison uses disjoint passes, locally matched fuel/conditions, a discovery
prefix and a later confirmation suffix. Samples of a pass are never independent
evidence. No SDK, cloud, audio or database dependencies.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
from statistics import median
import threading
import uuid
import time

MAX_ROWS = 1800
MAX_BYTES = 4_000_000
RETENTION_S = 30 * 86400
_LOCK = threading.RLock()
FEATURES = {
    'brake_start_m': (3., 'brake_earlier', 'brake_later'),
    'release_m': (3., 'release_earlier', 'release_later'),
    'throttle_start_m': (3., 'throttle_earlier', 'throttle_later'),
}
ACTION_TEXT = {
    'brake_earlier': '제동을 조금 일찍 시작한 패턴에서 반복 이득이 관측됐습니다.',
    'brake_later': '제동을 조금 늦게 시작한 패턴에서 반복 이득이 관측됐습니다.',
    'release_earlier': '브레이크를 조금 일찍 푼 패턴에서 반복 이득이 관측됐습니다.',
    'release_later': '브레이크를 조금 더 이어간 패턴에서 반복 이득이 관측됐습니다.',
    'throttle_earlier': '가속을 조금 일찍 시작한 패턴에서 반복 이득이 관측됐습니다.',
    'throttle_later': '가속을 조금 기다린 패턴에서 반복 이득이 관측됐습니다.',
    'chain_entry_slower': '첫 코너 진입속도를 줄인 패턴에서 연결 구간 전체가 반복해서 빨랐습니다.',
    'impact_less': '충격이 작았던 통과에서 이후 구간까지 반복 이득이 관측됐습니다.',
    'impact_more': '연석 신호가 있는 통과에서 이후 구간까지 반복 이득이 관측됐습니다.',
}


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def unique(rows):
    """Stable pass IDs survive repeated imports; last occurrence does not gain weight."""
    found = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('id'), str):
            found.setdefault(row['id'], row)
    return list(found.values())


def comparable(a, b, *, control=None, entry=True):
    if a.get('traffic') != 'SOLO' or b.get('traffic') != 'SOLO':
        return False
    if any(a.get(k) != b.get(k) for k in ('scope', 'window', 'kind', 'source')):
        return False
    if (a.get('run'), a.get('lap')) == (b.get('run'), b.get('lap')):
        return False
    # An unknown mechanical setup cannot link different runs.
    if (a.get('setup') and b.get('setup') and a['setup'] != b['setup']) or (a.get('run') != b.get('run') and (not a.get('setup') or a.get('setup') != b.get('setup'))):
        return False
    ca, cb = a.get('conditions') or {}, b.get('conditions') or {}
    for key, tolerance in (('fuel', 6.), ('temp', 3.), ('compound', 0.), ('wet', 0.)):
        x, y = finite(ca.get(key)), finite(cb.get(key))
        if x is None or y is None or abs(x-y) > tolerance:
            return False
    aa, bb = a.get('controls') or {}, b.get('controls') or {}
    if not aa or set(aa) != set(bb):
        return False
    for key in aa:
        x, y = finite(aa[key]), finite(bb[key])
        if x is None or y is None or (key != control and abs(x-y) > 1e-5):
            return False
    ma, mb = a.get('metrics') or {}, b.get('metrics') or {}
    if entry:
        x, y = finite(ma.get('entry_speed')), finite(mb.get('entry_speed'))
        if x is None or y is None or abs(x-y) > 3.:
            return False
    return all(finite(m.get('time_s')) is not None and m['time_s'] > 0 for m in (ma, mb))


def paired(low, high, *, control=None, entry=True):
    """Greedy nearest-condition matching, without reusing a lap on either side."""
    remaining = list(high)
    result, used_laps = [], set()
    for a in sorted(low, key=lambda r: r.get('ordinal', 0)):
        a_lap = (a.get('run'), a.get('lap'))
        if a_lap in used_laps:
            continue
        options = [(i, b) for i, b in enumerate(remaining)
                   if (b.get('run'), b.get('lap')) not in used_laps
                   and comparable(a, b, control=control, entry=entry)]
        if not options:
            continue
        index, b = min(options, key=lambda p: (
            abs(a['conditions']['fuel']-p[1]['conditions']['fuel']),
            abs(a.get('ordinal', 0)-p[1].get('ordinal', 0))))
        remaining.pop(index)
        used_laps.update((a_lap, (b.get('run'), b.get('lap'))))
        result.append((a, b))
    return sorted(result, key=lambda p: max(r.get('ordinal', 0) for r in p))


def evaluate(pairs):
    """Discovery selects a direction; subsequent independent pairs must confirm it."""
    if len(pairs) < 4:
        return None
    cut = max(2, len(pairs)//2)
    differences = [b['metrics']['time_s']-a['metrics']['time_s'] for a, b in pairs]
    discovery = median(differences[:cut])
    sign = 1 if discovery > 0 else -1
    spread = median(abs(v-median(differences[:cut])) for v in differences[:cut]) * 1.4826
    errors = [sum(finite(r['metrics'].get('time_error_s')) or .05 for r in p) for p in pairs]
    margin = max(.04, spread, median(errors))
    if abs(discovery) <= margin or any(sign*d <= margin for d in differences[cut:]):
        return None
    winners = [a if sign == 1 else b for a, b in pairs]
    losers = [b if sign == 1 else a for a, b in pairs]
    # A window's faster time must not be purchased by a slower terminal state.
    for good, bad in zip(winners, losers):
        gm, bm = good['metrics'], bad['metrics']
        if finite(gm.get('end_speed')) is None or finite(bm.get('end_speed')) is None:
            return None
        if gm['end_speed'] < bm['end_speed']-.5:
            return None
        for metric in ('corrections', 'relifts'):
            x, y = finite(gm.get(metric)), finite(bm.get(metric))
            if x is not None and y is not None and x > y+1:
                return None
        x, y = finite(gm.get('settling_s')), finite(bm.get('settling_s'))
        if x is not None and y is not None and x > y+.3:
            return None
    all_rows = [r for pair in pairs for r in pair]
    return dict(state='CONFIRMED', gain_s=round(abs(median(differences)), 4),
                margin_s=round(margin, 4), pairs=len(pairs), validation_pairs=len(pairs)-cut,
                evidence_ids=[r['id'] for r in all_rows],
                evidence_laps=[[r.get('run'), r.get('lap')] for r in all_rows],
                preferred_low=sign == 1, causal=False,
                support=[{k: deepcopy(r.get(k)) for k in
                          ('scope', 'window', 'kind', 'source', 'run', 'lap', 'setup', 'conditions', 'controls', 'metrics', 'traffic', 'created')}
                         for r in all_rows[-24:]])


def _pattern(rows, feature, minimum, low_action, high_action=None):
    available = [r for r in rows if finite(r.get('metrics', {}).get(feature)) is not None]
    if len(available) < 8:
        return None
    # Determine the split from the discovery prefix only, without looking at times.
    prefix = available[:max(4, len(available)//2)]
    split = median(r['metrics'][feature] for r in prefix)
    low = [r for r in available if r['metrics'][feature] <= split-minimum/2]
    high = [r for r in available if r['metrics'][feature] >= split+minimum/2]
    outcome = evaluate(paired(low, high, entry=feature != 'entry_speed'))
    if not outcome:
        return None
    action = low_action if outcome['preferred_low'] else high_action
    if not action:
        return None
    good = low if outcome['preferred_low'] else high
    supported = set(outcome['evidence_ids'])
    observed = [r['metrics'][feature] for r in good if r['id'] in supported]
    first = available[0]
    return dict(outcome, scope=first['scope'], source=first['source'], window=first['window'], kind=first['kind'], members=first.get('members', []),
                start_pct=first.get('start_pct'), end_pct=first.get('end_pct'),
                entry_pct=first.get('entry_pct'),
                feature=feature, action=action, text=ACTION_TEXT[action],
                observed_range=[round(min(observed), 3), round(max(observed), 3)])


def setting_outcome(pairs):
    if len(pairs) < 4:
        return None
    outcome = evaluate(pairs)
    deltas = {}
    for metric in ('time_s','end_speed','corrections','relifts','abs_duty','yaw_rms','lat_g','long_g'):
        values = [b['metrics'][metric]-a['metrics'][metric] for a,b in pairs
                  if finite(a['metrics'].get(metric)) is not None and finite(b['metrics'].get(metric)) is not None]
        if len(values) == len(pairs):
            deltas[metric] = round(median(values),4)
    if outcome:
        return dict(outcome,deltas_high_minus_low=deltas)
    # A consistent reduction in corrections/lifts may help stability even without time gain.
    cut = max(2,len(pairs)//2)
    for metric in ('corrections','relifts'):
        differences = [b['metrics'][metric]-a['metrics'][metric] for a,b in pairs
                       if finite(a['metrics'].get(metric)) is not None and finite(b['metrics'].get(metric)) is not None]
        if len(differences)!=len(pairs):
            continue
        sign = 1 if median(differences[:cut])>0 else -1
        if abs(median(differences[:cut]))<2 or any(sign*d<2 for d in differences[cut:]):
            continue
        winners = [a if sign==1 else b for a,b in pairs]
        losers = [b if sign==1 else a for a,b in pairs]
        safe = all(g['metrics']['time_s'] <= b['metrics']['time_s']+.04
                   and finite(g['metrics'].get('end_speed')) is not None and finite(b['metrics'].get('end_speed')) is not None
                   and g['metrics']['end_speed'] >= b['metrics']['end_speed']-.5
                   and all(finite(g['metrics'].get(k)) is not None and finite(b['metrics'].get(k)) is not None
                           and g['metrics'][k]<=b['metrics'][k] for k in ('corrections','relifts'))
                   for g,b in zip(winners,losers))
        if safe:
            return dict(state='STABILITY_CONFIRMED',preferred_low=sign==1,pairs=len(pairs),validation_pairs=len(pairs)-cut,
                        stability_metric=metric,causal=False,deltas_high_minus_low=deltas)
    return dict(state='INCONCLUSIVE',pairs=len(pairs),causal=False,deltas_high_minus_low=deltas)


def learn(rows):
    rows = [c for r in unique(rows)[-MAX_ROWS:] if (c := clean_row(r)) is not None]
    now = time.time()
    rows = [r for r in rows if 0 <= now-r['created'] <= RETENTION_S]
    for n, row in enumerate(rows):
        row['ordinal'] = n
    groups = defaultdict(list)
    for row in rows:
        groups[(row.get('scope'), row.get('window'), row.get('kind'), row.get('source'))].append(row)
    result = dict(state='LEARNING', observations=len(rows), eligible=sum(r.get('traffic') == 'SOLO' for r in rows),
                  technique=[], settings=[], chains=[], impacts=[])
    for values in groups.values():
        values.sort(key=lambda r: r.get('ordinal', 0))
        values = values[-120:]  # Recent independent passes bound fitting work per window.
        if values[0].get('kind') == 'chain':
            item = _pattern(values, 'entry_speed', 2., 'chain_entry_slower')
            if item:
                result['chains'].append(item)
            continue
        patterns = [item for feature, args in FEATURES.items()
                    if (item := _pattern(values, feature, *args))]
        if patterns:
            result['technique'].append(max(patterns, key=lambda item: item['gain_s']))
        item = _pattern(values, 'impact_score', 1., 'impact_less', 'impact_more')
        if item:
            ids = set(item['evidence_ids'])
            evidence = [r for r in values if r['id'] in ids]
            split = median(r['metrics']['impact_score'] for r in evidence)
            high = [r for r in evidence if r['metrics']['impact_score']>split]
            item['kerb_verified'] = bool(high) and all(r.get('impact_verified') is True for r in high)
            if item['kerb_verified'] and item['action']=='impact_less':
                item['text']='연석 충격이 작았던 통과에서 이후 구간까지 반복 이득이 관측됐습니다.'
            if item['action'] != 'impact_more' or item['kerb_verified']:
                result['impacts'].append(item)
        for control in ('bb', 'abs', 'tc'):
            common = Counter(r.get('controls', {}).get(control) for r in values
                             if finite(r.get('controls', {}).get(control)) is not None).most_common(2)
            if len(common) != 2:
                continue
            a, b = sorted(v for v, _ in common)
            pairs = paired([r for r in values if r.get('controls', {}).get(control) == a],
                           [r for r in values if r.get('controls', {}).get(control) == b], control=control)
            outcome = setting_outcome(pairs)
            if outcome:
                preferred = (a if outcome['preferred_low'] else b) if 'preferred_low' in outcome else None
                text = (f'{control.upper()} 두 값의 시간·탈출·안정성에서 일관된 이득을 확인하지 못했습니다.' if preferred is None
                        else f'{control.upper()} {preferred:g} 사용 구간에서 반복 '+('안정성 이득이 관측됐습니다. 시간 개선은 미확인입니다.'
                             if outcome['state']=='STABILITY_CONFIRMED' else '시간 이득이 관측됐습니다.'))
                result['settings'].append(dict(outcome, window=values[0]['window'], members=values[0].get('members', []),
                    control=control, values=[a,b], preferred_value=preferred,text=text))
    if any(item.get('state') in ('CONFIRMED','STABILITY_CONFIRMED') for key in ('technique', 'settings', 'chains', 'impacts') for item in result[key]):
        result['state'] = 'CONFIRMED'
    return result


def applicable(model, current):
    """Current-lap evidence and unsupported conditions cannot drive live instructions."""
    if model.get('state') != 'CONFIRMED':
        return False
    if [current.get('run'), current.get('lap')] in model.get('evidence_laps', []):
        return False
    for observed in model.get('support', []):
        age = time.time() - (finite(observed.get('created')) or 0)
        if 0 <= age <= RETENTION_S and comparable(observed, current, entry=False):
            return True
    return False


def clean_row(row):
    """Persist only bounded codes/hashes and explicitly allowed numeric fields."""
    if not isinstance(row, dict):
        return None
    strings = ('id', 'scope', 'window', 'run')
    if any(not isinstance(row.get(k), str) or not 0 < len(row[k]) <= 96
           or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-' for c in row[k]) for k in strings):
        return None
    if row.get('kind') not in ('corner', 'chain') or row.get('source') not in ('LIVE', 'IBT'):
        return None
    if finite(row.get('lap')) is None or finite(row.get('created')) is None:
        return None
    result = {k: row[k] for k in (*strings, 'kind', 'source', 'lap', 'created')}
    result['traffic'] = 'SOLO' if row.get('traffic') == 'SOLO' else 'UNKNOWN'
    result['setup'] = row.get('setup') if isinstance(row.get('setup'), str) and len(row['setup']) <= 96 and row['setup'].isalnum() else None
    result['members'] = [s for s in row.get('members', []) if isinstance(s,str) and len(s) <= 24 and s.replace('_','').isalnum()][:3]
    for k in ('ordinal', 'start_pct', 'end_pct', 'entry_pct'):
        result[k] = finite(row.get(k))
    result['impact_verified'] = row.get('impact_verified') is True
    allowed = {
        'conditions': ('fuel', 'temp', 'compound', 'wet'),
        'controls': ('bb', 'abs', 'tc'),
        'metrics': ('time_s', 'corner_time_s', 'entry_speed', 'end_speed', 'time_error_s', 'peak_brake',
                    'brake_start_m', 'release_m', 'throttle_start_m', 'relifts', 'corrections',
                    'impact_score', 'settling_s', 'abs_duty', 'yaw_rms', 'lat_g', 'long_g'),
    }
    for key, names in allowed.items():
        if not isinstance(row.get(key), dict):
            return None
        result[key] = {k: finite(row[key][k]) for k in names if k in row[key]}
    if (finite(result['metrics'].get('time_s')) or 0) <= 0:
        return None
    return result


class DrivingHistory:
    """Numeric summaries only; owned atomic temporary file and bounded local retention."""
    def __init__(self, path=None, *, limit=MAX_ROWS):
        self.path = Path(path) if path is not None else None
        self.limit = min(MAX_ROWS, max(1, limit))
        self._rows = []

    def rows(self):
        if self.path is not None:
            try:
                if self.path.stat().st_size > MAX_BYTES:
                    return []
                value = json.loads(self.path.read_text(encoding='utf-8'))
                rows = value.get('rows') if isinstance(value, dict) and value.get('version') == 1 else []
                if isinstance(rows, list):
                    self._rows = unique(cleaned for r in rows if (cleaned := clean_row(r)) is not None)[-self.limit:]
            except (OSError, ValueError, TypeError):
                pass
        return deepcopy(self._rows)

    def update(self, rows):
        with _LOCK:
            merged = unique(cleaned for r in [*self.rows(), *rows] if (cleaned := clean_row(r)) is not None)[-self.limit:]
            now = time.time()
            merged = [r for r in merged if 0 <= now-r['created'] <= RETENTION_S]
            for ordinal, row in enumerate(merged):
                row['ordinal'] = ordinal
            payload = json.dumps(dict(version=1, rows=merged), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            while len(payload.encode('utf-8')) > MAX_BYTES and merged:
                merged.pop(0)
                payload = json.dumps(dict(version=1, rows=merged), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    temporary.write_text(payload, encoding='utf-8')
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)
            self._rows = merged
            return deepcopy(merged)
