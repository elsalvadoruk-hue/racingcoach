"""After a race: the battles in it, and how I could have driven each corner of them.

Inputs, both written automatically for every iRacing race and joined by SessionTime:
  * the app's traffic log (10 Hz): each nearby car's along-track gap, our CarLeftRight;
  * the race IBT (60 Hz): our GPS, speed and pedals.

Measured: our position and heading (GPS); the along-track gap to each car (track progress),
checked where CarLeftRight switches on/off (the cars' ends are level there).
Estimated: the rival's position across the track (iRacing never reports it). While the side
signal can only mean that car, it is that side at least a car width away; otherwise it is a
range growing from the last known side, clipped to the spread of our own lines.

'What if' for each corner of a battle uses only our own other passes of that corner in the
same race, starting at about the same speed, with the rival held to what it really did:
  * usual: our solo median, moved to our entry speed (the default, least biased);
  * best: our quickest such pass. Shown only when it beats what picking the best of N passes
    gives by chance, and does not need more minimum speed than the car close ahead had.
Checked on three races (Hockenheim, Spa x2) in logs/battle-iracing-precise-20261028/.
"""
from __future__ import annotations

from collections import OrderedDict
import gzip
import json
import math
import threading
from pathlib import Path

import numpy as np

FORMAT = 'iracing-coach-traffic'
CHANNELS = ('SessionTime', 'Lap', 'LapDistPct', 'Lat', 'Lon', 'Speed', 'Brake', 'Throttle',
            'SteeringWheelAngle', 'Gear', 'OnPitRoad')

# Assumptions shown on screen; not measured.
CAR_LENGTH_M, CAR_WIDTH_M = 4.64, 2.05
SIDE_MAX_M = 2 * CAR_WIDTH_M + .5      # iRacing's side-detection reach is not published
LATERAL_RATE_MPS = 2.5                 # sideways drift between two known positions
NEAR_M, EPISODE_MIN_S, JOIN_S = 20., 4., .6
SOLO_CLEAR_M, BATTLE_NEAR_M = 30., 15.
ENTRY_MATCH_KPH, MIN_PASSES, MIN_GAIN_S = 5., 5, .05
GRID = 2000
BRAKE_ON, FULL_THROTTLE = .05, .95
MAX_LOGS = 30

_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()


# ---- finding the traffic log of this race ----------------------------------------------

def _read_log(path):
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        header = json.loads(handle.readline(262144))
        if not isinstance(header, dict) or header.get('format') != FORMAT:
            return None, []
        rows = []
        for line in handle:
            item = json.loads(line)
            if isinstance(item, dict) and item.get('format') == FORMAT:
                return None, []          # several runs in one old file cannot be joined safely
            if isinstance(item, dict) and isinstance(item.get('t'), (int, float)):
                rows.append(item)
    return header, rows


def _nearest(times, at):
    j = np.clip(np.searchsorted(times, at), 1, len(times) - 1)
    return np.where(np.abs(times[j - 1] - at) < np.abs(times[j] - at), j - 1, j)


def _own(header, rows, name):
    fields = header.get('own_fields') or []
    if name not in fields:
        return None
    i = fields.index(name)
    return np.array([np.nan if r['own'][i] is None else r['own'][i] for r in rows], float)


def _fingerprint(header, rows, ch):
    """Share of moving rows whose own progress and speed match the IBT at the same time."""
    times = np.array([r['t'] for r in rows])
    t = ch['SessionTime']
    inside = (times >= t[0]) & (times <= t[-1])
    speed, pct = _own(header, rows, 'speed_mps'), _own(header, rows, 'distance_pct')
    if speed is None or pct is None:
        return 0, 0.
    pick = np.nonzero(inside & (speed > 10))[0]
    if len(pick) < 20:
        return len(pick), 0.
    pick = pick[np.linspace(0, len(pick) - 1, min(150, len(pick))).astype(int)]
    j = _nearest(t, times[pick])
    ok = np.abs(t[j] - times[pick]) <= .02
    ok &= np.abs(((ch['LapDistPct'][j] - pct[pick] + .5) % 1) - .5) <= 6e-4   # old logs: 3 places
    ok &= np.abs(ch['Speed'][j] - speed[pick]) <= .002
    return len(pick), float(np.mean(ok))


def find_traffic_log(telemetry, ch, folder):
    folder = Path(folder) if folder else None
    if folder is None or not folder.is_dir():
        return None, 'NO_TRAFFIC_LOGS'
    meta = telemetry.metadata
    car, track = str(meta.get('car_path') or ''), str(meta.get('track_platform_id') or '')
    found = []
    for path in sorted(folder.glob('traffic-*.jsonl.gz'))[-MAX_LOGS:]:
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                header = json.loads(handle.readline(262144))
            if (not isinstance(header, dict) or header.get('format') != FORMAT or header.get('game', 'iracing') != 'iracing'
                    or str(header.get('car') or '') != car or str(header.get('track_id') or '') != track):
                continue
            header, rows = _read_log(path)
            if header is None:
                continue
            n, share = _fingerprint(header, rows, ch)
            if n >= 20 and share >= .98:
                found.append((path, header, rows))
        except (OSError, ValueError, EOFError, KeyError, TypeError):
            continue
    if len(found) != 1:
        return None, 'NO_MATCHING_TRAFFIC_LOG' if not found else 'AMBIGUOUS_TRAFFIC_LOG'
    return found[0], None


# ---- our lines ----------------------------------------------------------------------------

def _local(lat, lon, origin):
    r = 6378137.0
    return (np.radians(lon - origin[1]) * r * math.cos(math.radians(origin[0])),
            np.radians(lat - origin[0]) * r)


def _clean_laps(ch):
    laps = []
    for n in np.unique(ch['Lap']):
        idx = np.nonzero(ch['Lap'] == n)[0]
        if n < 1 or len(idx) < 600:
            continue
        pct = ch['LapDistPct'][idx]
        if pct[0] > .02 or pct[-1] < .98 or np.any(np.diff(pct) < -.5):
            continue
        if np.any(ch['OnPitRoad'][idx] > 0) or np.nanmin(ch['Speed'][idx]) < 8:
            continue
        laps.append((int(n), idx))
    return laps


def _resample(ch, idx, x, y, grid):
    pct = ch['LapDistPct'][idx]
    keep = np.r_[True, np.diff(pct) > 0]
    return np.interp(grid, pct[keep], x[idx][keep]), np.interp(grid, pct[keep], y[idx][keep])


# ---- corners --------------------------------------------------------------------------------

def corner_windows(definitions, names):
    out = []
    for n, c in enumerate(definitions or []):
        nxt, prev = definitions[(n + 1) % len(definitions)], definitions[n - 1]
        named = (names or {}).get(c['id']) or {}
        label = (f"T{named['number']}" if named.get('number') else named.get('name')) or c['id']
        if named.get('number') and named.get('name'):
            label += f" {named['name']}"
        out.append({'id': c['id'], 'label': label, 'entry': c['entry_pct'], 'apex': c['apex_pct'], 'exit': c['exit_pct'],
                    'next_entry': nxt['entry_pct'], 'prev_exit': prev['exit_pct'] if n else prev['exit_pct'] - 1})
    return out


def _time_at(pct, time, at):
    keep = np.r_[True, np.diff(pct) > 0]
    return float(np.interp(at, pct[keep], time[keep]))


def _pass(ch, idx, c, length_m):
    pct, t = ch['LapDistPct'][idx], ch['SessionTime'][idx]
    end = c['next_entry'] if c['next_entry'] > c['entry'] else 1.0
    if pct[0] > c['entry'] - .02 or pct[-1] < end:
        return None
    look = max(c['entry'] - 250 / length_m, c['prev_exit'], pct[0])   # braking for this corner only
    brake, thr, spd = ch['Brake'][idx], ch['Throttle'][idx], ch['Speed'][idx]
    bi = np.nonzero((pct >= look) & (pct <= c['exit']) & (brake > BRAKE_ON))[0]
    core = (pct >= c['entry']) & (pct <= c['exit'])
    if core.sum() < 5:
        return None
    k_min = np.nonzero(core)[0][np.argmin(spd[core])]
    full = np.nonzero((pct >= pct[k_min]) & (pct <= end) & (thr > FULL_THROTTLE))[0]
    t_entry = _time_at(pct, t, c['entry'])
    return {
        'entry_kph': float(np.interp(c['entry'], pct, spd) * 3.6), 'min_kph': float(spd[k_min] * 3.6),
        'exit_kph': float(np.interp(c['exit'], pct, spd) * 3.6), 'end_kph': float(np.interp(end, pct, spd) * 3.6),
        'brake_m': None if not len(bi) else (float(pct[bi[0]]) - c['entry']) * length_m,
        'full_throttle_m': None if not len(full) else (float(pct[full[0]]) - c['apex']) * length_m,
        'segment_s': _time_at(pct, t, end) - t_entry, 'turns': 'L' if ch['SteeringWheelAngle'][idx][k_min] > 0 else 'R',
        't_entry': t_entry, 't_exit': _time_at(pct, t, c['exit']), 't_end': _time_at(pct, t, end),
    }


def entry_model(passes):
    """Segment time against entry speed at a corner, and our lap-to-lap noise around it."""
    if len(passes) < MIN_PASSES:
        return None
    x = np.array([p['entry_kph'] for p in passes]); y = np.array([p['segment_s'] for p in passes])
    b = 0.0 if np.ptp(x) < 1 else float(np.polyfit(x, y, 1)[0])
    resid = y - (np.median(y - b * x) + b * x)
    solo = np.array([p['solo'] for p in passes])
    pool = resid[solo] if solo.sum() >= MIN_PASSES else resid
    return {'slope': b, 'noise_s': float(1.4826 * np.median(np.abs(pool - np.median(pool))))}


_BEST_OF = {}


def best_of_n(n, sims=20000):
    """Expected advantage, in noise units, of the best of n passes over a typical one."""
    if n < 2:
        return 0.0
    if n not in _BEST_OF:
        rng = np.random.default_rng(7)
        _BEST_OF[n] = float(-np.mean(np.min(rng.standard_normal((sims, n)), axis=1)))
    return _BEST_OF[n]


# ---- the whole analysis -------------------------------------------------------------------

def analyse(telemetry, ch, header, rows, definitions, names):
    length = float(header.get('track_length_m') or telemetry.metadata.get('track_length_m') or 0)
    if not length:
        return {'status': 'UNAVAILABLE', 'reason': 'NO_TRACK_LENGTH'}
    windows = corner_windows(definitions, names)
    if not windows:
        return {'status': 'UNAVAILABLE', 'reason': 'NO_CORNERS'}
    t = ch['SessionTime']
    origin = (float(np.nanmedian(ch['Lat'])), float(np.nanmedian(ch['Lon'])))
    x, y = _local(ch['Lat'], ch['Lon'], origin)
    grid = np.linspace(0, 1, GRID, endpoint=False)
    laps = _clean_laps(ch)
    if len(laps) < 2:
        return {'status': 'UNAVAILABLE', 'reason': 'TOO_FEW_LAPS', 'laps_used': len(laps)}
    lines = np.array([_resample(ch, idx, x, y, grid) for _, idx in laps])
    ref = np.median(lines, axis=0)
    d = np.gradient(ref, axis=1); tang = d / np.hypot(*d)
    normal = np.array([-tang[1], tang[0]])            # left of travel
    offsets = np.einsum('ij,nij->nj', normal, lines - ref)
    spread_lo, spread_hi = np.percentile(offsets, 2, axis=0), np.percentile(offsets, 98, axis=0)

    fi = {k: i for i, k in enumerate(header['fields'])}
    row_t = np.array([r['t'] for r in rows])
    j = _nearest(t, row_t)
    joined = np.abs(t[j] - row_t) < .02
    own_pct = ch['LapDistPct'][j]
    clr_raw = _own(header, rows, 'car_left_right')
    clr = np.nan_to_num(clr_raw if clr_raw is not None else np.zeros(len(rows)), nan=0).astype(int)
    my_class = min(header.get('classes') or {'': {}}, key=lambda c: (header['classes'][c] or {}).get('est_lap_s') or math.inf) \
        if header.get('classes') else None

    near_at, rival_rows = {}, {}
    for k, r in enumerate(rows):
        if not joined[k]:
            continue
        for c in r.get('c') or []:
            if c[fi['on_pit_road']]:
                continue
            near_at.setdefault(k, []).append((c[0], c[fi['gap_m']]))
            if my_class is None or str(c[fi['class_id']]) == str(my_class):
                rival_rows.setdefault(c[0], []).append((k, c[fi['gap_m']]))

    # side attribution: the only car whose body overlaps ours along the track
    side_of = {}
    for k, cars in near_at.items():
        level = [c for c in cars if abs(c[1]) < CAR_LENGTH_M + .6]
        if clr[k] in (2, 3) and len(level) == 1:
            side_of[k] = [(level[0][0], 'L' if clr[k] == 2 else 'R')]
        elif clr[k] in (5, 6) and len(level) == 2:
            side_of[k] = [(c[0], 'L' if clr[k] == 5 else 'R') for c in level]

    edges = []
    for k in range(1, len(rows)):
        a, b = side_of.get(k - 1), side_of.get(k)
        if (a is None) == (b is None) or 1 not in (clr[k - 1], clr[k]):
            continue
        car = (b or a)[0][0]
        g, g0 = dict(near_at.get(k, [])).get(car), dict(near_at.get(k - 1, [])).get(car)
        if g is not None and g0 is not None:
            edges.append(abs((g + g0) / 2))
    edges = np.array(edges)
    bias = float(np.median(edges) - CAR_LENGTH_M) if len(edges) >= 6 else 0.0
    calibration = {'edges': int(len(edges)), 'median_m': None if not len(edges) else round(float(np.median(edges)), 2),
                   'p10_p90_m': None if not len(edges) else [round(float(np.percentile(edges, q)), 2) for q in (10, 90)],
                   'bias_m': round(bias, 2)}

    def correct(g):
        return (abs(g) - bias) * math.copysign(1, g) if abs(g) > bias else 0.0

    series = {}
    for k in sorted(near_at):
        for car, g in near_at[k]:
            series.setdefault(car, ([], []))
            series[car][0].append(row_t[k]); series[car][1].append(correct(g))
    series = {car: (np.array(a), np.array(b)) for car, (a, b) in series.items()}

    def gap_of(car, at, tol=.15):
        if car not in series:
            return None
        ts, gs = series[car]
        i = int(_nearest(ts, np.array([at]))[0])
        return round(float(gs[i]), 2) if abs(ts[i] - at) <= tol else None

    # episodes
    episodes = []
    for car, pts in rival_rows.items():
        close = [(k, g) for k, g in pts if abs(g) <= NEAR_M]
        start = 0
        for n in range(1, len(close) + 1):
            if n == len(close) or row_t[close[n][0]] - row_t[close[n - 1][0]] > JOIN_S:
                part = close[start:n]; start = n
                if row_t[part[-1][0]] - row_t[part[0][0]] >= EPISODE_MIN_S and ch['Lap'][j[part[0][0]]] >= 1:
                    episodes.append((car, part))
    episodes.sort(key=lambda e: row_t[e[1][0][0]])

    scenes = []
    for number, (car, part) in enumerate(episodes):
        ks = [k for k, _ in part]
        k0, k1 = max(ks[0] - 20, 0), min(ks[-1] + 20, len(rows) - 1)
        constraint = {}
        for k in range(k0, k1 + 1):
            s = next((s for s in side_of.get(k, []) if s[0] == car), None)
            if s:
                constraint[k] = (CAR_WIDTH_M + .2, SIDE_MAX_M) if s[1] == 'L' else (-SIDE_MAX_M, -CAR_WIDTH_M - .2)
        ck = sorted(constraint)
        frames = []
        for k in range(k0, k1 + 1):
            g = dict(near_at.get(k, [])).get(car)
            if not joined[k] or g is None:
                continue
            ti = int(j[k])
            gc = correct(g)
            clamped = False
            if clr[k] == 1 and abs(gc) < CAR_LENGTH_M:
                gc, clamped = math.copysign(CAR_LENGTH_M + .2, gc or 1), True   # no side car: nose to tail
            pct_r = (own_pct[k] + gc / length) % 1
            gi, mi = int(pct_r * GRID) % GRID, int(own_pct[k] * GRID) % GRID
            me = (float(x[ti]), float(y[ti]))
            me_off = float(normal[:, mi] @ (np.array(me) - ref[:, mi]))
            lo, hi = float(spread_lo[gi]) - 1, float(spread_hi[gi]) + 1
            if ck:
                nk = min(ck, key=lambda q: abs(row_t[q] - row_t[k]))
                dt = abs(row_t[nk] - row_t[k])
                clo, chi = me_off + constraint[nk][0] - LATERAL_RATE_MPS * dt, me_off + constraint[nk][1] + LATERAL_RATE_MPS * dt
                lo, hi = max(lo, clo), min(hi, chi)
                if lo > hi:
                    lo, hi = clo, chi
            side = next((s[1] for s in side_of.get(k, []) if s[0] == car), None)
            frames.append({'t': round(float(row_t[k]), 3), 'me': [round(me[0], 2), round(me[1], 2)],
                           'speed_kph': round(float(ch['Speed'][ti]) * 3.6, 1), 'brake': round(float(ch['Brake'][ti]), 3),
                           'throttle': round(float(ch['Throttle'][ti]), 3), 'gear': int(ch['Gear'][ti]),
                           'steer_deg': round(math.degrees(float(ch['SteeringWheelAngle'][ti])), 1),
                           'pct': round(float(own_pct[k]), 5), 'gap_m': round(gc, 2), 'gap_clamped': clamped,
                           'beside': side, 'rival_pct': round(float(pct_r), 5), 'rival_lat': [round(lo, 2), round(hi, 2)]})
        for n, f in enumerate(frames):
            a, b = frames[max(n - 3, 0)], frames[min(n + 3, len(frames) - 1)]
            if b['t'] - a['t'] > .3:
                f['rival_speed_kph'] = round(f['speed_kph'] + (b['gap_m'] - a['gap_m']) / (b['t'] - a['t']) * 3.6, 1)
        if len(frames) < 20:
            continue
        g0, g1 = frames[0]['gap_m'], frames[-1]['gap_m']
        outcome = 'passed' if g0 > 0 > g1 else 'lost' if g0 < 0 < g1 else 'held' if g0 < 0 else 'behind'
        scenes.append({'id': f'B{number + 1}', 'rival': car, 'lap': int(ch['Lap'][j[ks[0]]]),
                       't0': frames[0]['t'], 't1': frames[-1]['t'], 'outcome': outcome,
                       'beside_s': round(sum(1 for f in frames if f['beside']) * float(header.get('rate_s') or .1), 1),
                       'frames': frames})

    # corner passes, solo baseline, what if
    passes = {c['id']: [] for c in windows}
    for n, idx in laps:
        for c in windows:
            p = _pass(ch, idx, c, length)
            if p is None:
                continue
            lo_, hi_ = np.searchsorted(row_t, p['t_entry'] - 1), np.searchsorted(row_t, p['t_end'])
            near = {}
            for k in range(lo_, hi_):
                for car, g in near_at.get(k, []):
                    near[car] = min(near.get(car, 1e9), abs(g))
            p.update(lap=n, solo=not any(g < SOLO_CLEAR_M for g in near.values()), near=near)
            passes[c['id']].append(p)
    models = {cid: entry_model(ps) for cid, ps in passes.items()}

    for s in scenes:
        rows_ = []
        for c in windows:
            for p in passes[c['id']]:
                if p['t_end'] < s['t0'] or p['t_entry'] > s['t1'] or p['near'].get(s['rival'], 1e9) > BATTLE_NEAR_M:
                    continue
                rows_.append(_corner_advice(s, c, p, passes[c['id']], models[c['id']], gap_of))
        s['corners'] = rows_

    step = 4
    geometry = {'ref': [[round(float(ref[0, i]), 2), round(float(ref[1, i]), 2)] for i in range(0, GRID, step)],
                'normal': [[round(float(normal[0, i]), 4), round(float(normal[1, i]), 4)] for i in range(0, GRID, step)],
                'band_left': [[round(float(ref[0, i] + normal[0, i] * spread_hi[i]), 2),
                               round(float(ref[1, i] + normal[1, i] * spread_hi[i]), 2)] for i in range(0, GRID, step)],
                'band_right': [[round(float(ref[0, i] + normal[0, i] * spread_lo[i]), 2),
                                round(float(ref[1, i] + normal[1, i] * spread_lo[i]), 2)] for i in range(0, GRID, step)],
                'grid': GRID // step}
    advices = [r for s in scenes for r in s['corners'] if r.get('best') or r.get('usual')]
    top = max(advices, key=lambda r: (r.get('best') or r.get('usual'))['gain_s'], default=None)
    return {'status': 'READY', 'laps_used': len(laps), 'enough_laps': len(laps) >= MIN_PASSES,
            'calibration': calibration,
            'assumptions': {'car_length_m': CAR_LENGTH_M, 'car_width_m': CAR_WIDTH_M, 'side_max_m': SIDE_MAX_M,
                            'lateral_rate_mps': LATERAL_RATE_MPS, 'entry_match_kph': ENTRY_MATCH_KPH},
            'geometry': geometry, 'scenes': scenes,
            'summary': {'battles': len(scenes), 'side_by_side': sum(1 for s in scenes if s['beside_s'] > 0),
                        'passed': sum(1 for s in scenes if s['outcome'] == 'passed'),
                        'lost': sum(1 for s in scenes if s['outcome'] == 'lost'),
                        'top': None if top is None else {'scene': next(s['id'] for s in scenes if top in s['corners']),
                                                         'label': top['label'], 'lap': top['lap'],
                                                         'gain_s': (top.get('best') or top.get('usual'))['gain_s']}}}


def _corner_advice(scene, c, me, pool_all, model, gap_of):
    frames = scene['frames']
    beside = {f['beside'] for f in frames if f['beside'] and me['t_entry'] <= f['t'] <= me['t_exit']}
    row = {'corner': c['id'], 'label': c['label'], 'lap': me['lap'], 't_entry': round(me['t_entry'], 2),
           't_exit': round(me['t_exit'], 2), 't_end': round(me['t_end'], 2), 'turns': me['turns'],
           'we_were': None if len(beside) != 1 else ('outside' if next(iter(beside)) == me['turns'] else 'inside'),
           'gap_entry_m': gap_of(scene['rival'], me['t_entry']), 'gap_next_m': gap_of(scene['rival'], me['t_end']),
           'actual': _numbers(me), 'usual': None, 'best': None, 'noise_s': None if model is None else round(model['noise_s'], 3)}
    if model is None:
        row['why_not'] = 'TOO_FEW_PASSES'
        return row
    b = model['slope']
    end_mps = me['end_kph'] / 3.6

    def outcome(gain_s):
        g = row['gap_next_m']
        if g is None:
            return None, None
        pred = round(g - gain_s * end_mps, 2)
        verdict = ('ahead' if g > 0 and pred <= -CAR_LENGTH_M else 'alongside' if g > CAR_LENGTH_M and pred <= CAR_LENGTH_M
                   else 'closer' if g > 0 else 'further')
        return pred, verdict

    solo = [p for p in pool_all if p['solo'] and p is not me]
    if len(solo) >= 3:
        gain = me['segment_s'] - float(np.median([p['segment_s'] + b * (me['entry_kph'] - p['entry_kph']) for p in solo]))
        if gain >= max(MIN_GAIN_S, model['noise_s']):
            pred, verdict = outcome(gain)
            med = {k: float(np.median([p[k] for p in solo if p[k] is not None])) if any(p[k] is not None for p in solo) else None
                   for k in ('brake_m', 'min_kph', 'exit_kph', 'full_throttle_m', 'end_kph')}
            row['usual'] = {'gain_s': round(gain, 3), 'gap_next_pred_m': pred, 'verdict': verdict, 'laps': len(solo),
                            'numbers': {k: None if v is None else round(v, 1) for k, v in med.items()}}
    pool = [p for p in pool_all if p is not me and abs(p['entry_kph'] - me['entry_kph']) <= ENTRY_MATCH_KPH]
    if pool:
        best = min(pool, key=lambda p: p['segment_s'] + b * (me['entry_kph'] - p['entry_kph']))
        gain = me['segment_s'] - (best['segment_s'] + b * (me['entry_kph'] - best['entry_kph']))
        chance = model['noise_s'] * best_of_n(len(pool))
        close = [f['rival_speed_kph'] for f in frames
                 if me['t_entry'] <= f['t'] <= me['t_exit'] and 0 < f['gap_m'] < 15 and 'rival_speed_kph' in f]
        capped = bool(close) and best['min_kph'] > min(close) + 2
        if gain >= MIN_GAIN_S and gain > chance + model['noise_s'] and not capped:
            pred, verdict = outcome(gain)
            row['best'] = {'gain_s': round(gain, 3), 'gap_next_pred_m': pred, 'verdict': verdict, 'lap': best['lap'],
                           'solo': best['solo'], 'candidates': len(pool), 'chance_s': round(chance, 3),
                           'numbers': _numbers(best)}
        elif capped:
            row['why_not'] = 'RIVAL_AHEAD'
    return row


def _numbers(p):
    return {k: None if p[k] is None else round(p[k], 1) for k in ('brake_m', 'min_kph', 'exit_kph', 'full_throttle_m', 'end_kph')}


# ---- entry point -------------------------------------------------------------------------

def _channels(telemetry, reader):
    ch = telemetry.channels
    if all(name in ch for name in CHANNELS):
        source = ch
    else:
        source = reader(telemetry.path).parse(requested_channels=list(CHANNELS)).channels
    out = {k: np.asarray(source[k], dtype=float) for k in CHANNELS}
    ok = np.r_[True, np.diff(out['SessionTime']) > 0] & np.isfinite(out['SessionTime'])
    return {k: v[ok] for k, v in out.items()}


def battle_report(telemetry, *, traffic_folder, corner_store, reader):
    meta = telemetry.metadata
    if str(meta.get('game') or 'iracing').casefold() != 'iracing':
        return {'status': 'UNAVAILABLE', 'reason': 'GAME_NOT_SUPPORTED'}
    if str(meta.get('session_type') or '').strip().casefold() not in ('race', 'endurance', 'endurance race'):
        return {'status': 'UNAVAILABLE', 'reason': 'NOT_A_RACE'}
    path = Path(telemetry.path)
    try:
        stat = path.stat()
    except OSError:
        return {'status': 'UNAVAILABLE', 'reason': 'FILE_MISSING'}
    ch = _channels(telemetry, reader)
    if len(ch['SessionTime']) < 600 or not np.isfinite(ch['Lat']).any():
        return {'status': 'UNAVAILABLE', 'reason': 'NO_GPS'}
    found, reason = find_traffic_log(telemetry, ch, traffic_folder)
    if found is None:
        return {'status': 'UNAVAILABLE', 'reason': reason}
    log_path, header, rows = found
    key = (str(path), stat.st_size, stat.st_mtime_ns, str(log_path), log_path.stat().st_mtime_ns)
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    identity = tuple(str(meta.get(k) or '') for k in ('car_platform_id', 'car_path', 'car_name', 'track_platform_id',
                                                       'track_path', 'track_name', 'track_config', 'track_version'))
    identity += (round(float(meta.get('track_length_m') or 0)),)
    definitions = corner_store.get(identity) or corner_store.find(meta.get('car_platform_id'), meta.get('track_platform_id'),
                                                                   meta.get('track_config'))
    names = corner_store.names_for(definitions, meta.get('track_platform_id'), meta.get('track_config')) if definitions else {}
    result = analyse(telemetry, ch, header, rows, definitions, names)
    result['source'] = {'traffic_log': log_path.name, 'ibt': path.name}
    with _CACHE_LOCK:
        _CACHE[key] = result
        while len(_CACHE) > 4:
            _CACHE.popitem(last=False)
    return result
