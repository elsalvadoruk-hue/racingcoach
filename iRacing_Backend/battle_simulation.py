"""Short, conditional two-car rollouts; no lap-time transplant or future rival inputs.

The own-car response is identified from other laps of this file. Track speed targets
are a spatial envelope, not a replayed pass. Rival reactions are deliberately separate
assumptions, not learned probabilities. Unknown lateral space blocks a lane-change
candidate. These retrospective scenarios are not a validated overtaking policy.
"""
from __future__ import annotations

import numpy as np

VERSION = 'battle-rollout-v1'
DT, HORIZON = .1, 2.
LENGTH, WIDTH, MARGIN = 4.64, 2.05, .35
REACTION_S = .25


def _features(v, brake, throttle):
    v = np.atleast_1d(v)
    return np.column_stack([np.broadcast_to(throttle, v.shape), -np.broadcast_to(brake, v.shape),
                            -np.ones_like(v), -(v / 60.) ** 2])


def _fit(x, y):
    # Nonnegative coefficients keep throttle, braking and drag signs physical.
    beta = np.array([5., 12., .2, .3])
    gram, rhs = x.T @ x, x.T @ y
    for _ in range(80):
        for j in range(4):
            beta[j] = max(0., beta[j] + (rhs[j] - gram[j] @ beta) / (gram[j, j] + .1))
    return beta


def _accel(model, v, brake, throttle):
    return float(np.clip(_features(v, brake, throttle)[0] @ model['beta'], *model['accel_bounds']))


def fit_vehicle(ch, excluded_lap, allowed_laps=None):
    t = ch['SessionTime']; step = max(1, round(.1 / float(np.median(np.diff(t)))))
    ids = np.arange(0, len(t) - step, step)
    dt = t[ids + step] - t[ids]
    lap = ch['Lap'][ids].astype(int)
    good = (dt > .07) & (dt < .13) & (lap >= 1) & (lap != excluded_lap)
    good &= ch['Lap'][ids + step] == lap
    if allowed_laps is not None:
        good &= np.isin(lap, allowed_laps)
    good &= (ch['OnPitRoad'][ids] == 0) & (ch['OnPitRoad'][ids + step] == 0)
    good &= (ch['Speed'][ids] > 8) & (ch['Speed'][ids] < 100)
    good &= np.isfinite(ch['Brake'][ids]) & np.isfinite(ch['Throttle'][ids])
    good &= (ch['Brake'][ids] >= 0) & (ch['Brake'][ids] <= 1)
    good &= (ch['Throttle'][ids] >= 0) & (ch['Throttle'][ids] <= 1)
    progress = ch['LapDistPct'][ids + step] - ch['LapDistPct'][ids]
    good &= np.isfinite(progress) & (progress >= 0) & (progress < .02)
    accel = (ch['Speed'][ids + step] - ch['Speed'][ids]) / np.maximum(.001, dt)
    good &= np.isfinite(accel) & (accel > -25) & (accel < 15)
    ids, lap, accel = ids[good], lap[good], accel[good]
    if len(ids) < 100 or np.count_nonzero(ch['Brake'][ids] > .1) < 15:
        return None
    x = _features(ch['Speed'][ids], ch['Brake'][ids], ch['Throttle'][ids])
    beta = _fit(x, accel)
    bounds = [min(-.5, float(np.quantile(accel, .005))), max(.5, float(np.quantile(accel, .995)))]
    model = {'beta': beta, 'accel_bounds': bounds, 'training_laps': sorted(set(map(int, lap))),
             'samples': len(ids), 'validation': {'status': 'NO_INDEPENDENT_LAP', 'anchors': 0},
             'speed_range': [float(ch['Speed'][ids].min()), float(ch['Speed'][ids].max())]}
    # Fit a separate model without the validation lap. Supplied future controls here
    # test vehicle response only; candidate rollouts below never receive them.
    unique = np.unique(lap)
    if len(unique) >= 2:
        held = unique[-1]; train = lap != held
        if train.sum() >= 100:
            check_model = dict(model, beta=_fit(x[train], accel[train]),
                               accel_bounds=[min(-.5, float(np.quantile(accel[train], .005))),
                                             max(.5, float(np.quantile(accel[train], .995)))])
            distance_errors, speed_errors = [], []
            for k in ids[lap == held][::20]:
                end = k + round(HORIZON / float(np.median(np.diff(t))))
                if end >= len(t) or ch['Lap'][end] != held or np.max(np.diff(t[k:end + 1])) > .04:
                    continue
                if np.any(ch['OnPitRoad'][k:end + 1] > 0):
                    continue
                v, distance = float(ch['Speed'][k]), 0.
                for j in range(k, end, step):
                    nxt = min(end, j + step); delta = float(t[nxt] - t[j])
                    v1 = max(0., v + _accel(check_model, v, ch['Brake'][j], ch['Throttle'][j]) * delta)
                    distance += (v + v1) * .5 * delta; v = v1
                actual_distance = float(np.sum((ch['Speed'][k:end] + ch['Speed'][k + 1:end + 1]) * .5 * np.diff(t[k:end + 1])))
                distance_errors.append(abs(distance - actual_distance)); speed_errors.append(abs(v - ch['Speed'][end]) * 3.6)
            if distance_errors:
                model['validation'] = {'status': 'HELD_OUT_LAP', 'lap': int(held),
                    'training_laps': sorted(set(map(int, lap[train]))), 'anchors': len(distance_errors),
                    'horizon_s': HORIZON, 'distance_p90_m': round(float(np.quantile(distance_errors, .9)), 2),
                    'speed_mae_kph': round(float(np.mean(speed_errors)), 2),
                    'scope': 'known-input vehicle response, not action or opponent validation'}
    # A median speed envelope is merely a controller target within measured operation.
    bins = np.linspace(0, 1, 161)
    pct, speed = ch['LapDistPct'][ids], ch['Speed'][ids]
    bucket = np.minimum(159, np.maximum(0, np.searchsorted(bins, pct) - 1))
    values = np.array([np.median(speed[bucket == n]) if np.any(bucket == n) else np.nan for n in range(160)])
    valid = np.flatnonzero(np.isfinite(values))
    if len(valid) < 20:
        return None
    model['target_speed'] = np.interp(np.arange(160), np.r_[valid - 160, valid, valid + 160], np.tile(values[valid], 3))
    return model


def _at(geometry, pct, lat):
    n = geometry['grid']; q = (pct % 1.) * n; i = int(q) % n; j = (i + 1) % n; w = q - int(q)
    ref = np.asarray(geometry['ref']); normal = np.asarray(geometry['normal'])
    p = ref[i] * (1 - w) + ref[j] * w
    direction = normal[i] * (1 - w) + normal[j] * w
    direction /= max(1e-6, float(np.linalg.norm(direction)))
    return p + direction * lat


def _lateral(frame, geometry):
    reference = _at(geometry, frame['pct'], 0.)
    normal = _at(geometry, frame['pct'], 1.) - reference
    return float(np.dot(np.asarray(frame['me']) - reference, normal))


def _rival_state(history, geometry):
    """Only history ending at the decision. Never uses an observed future rival path."""
    now = history[-1]; first = history[0]; dt = now['t'] - first['t']
    if len(history) < 4 or dt < .25 or max(np.diff([f['t'] for f in history])) > .2:
        return None
    times = np.array([f['t'] - now['t'] for f in history])
    gaps = np.array([f['gap_m'] for f in history])
    relative_v = float(np.polyfit(times, gaps, 1)[0])
    v = max(0., now['speed_kph'] / 3.6 + relative_v)
    if v > 110 or abs(relative_v) > 40:
        return None
    # The retrospective display may use a later side signal. Predictions must not.
    side = next((f for f in reversed(history) if f['beside']), None)
    lat = _lateral(now, geometry)
    width = 5.
    if side:
        lat = _lateral(side, geometry) + (1 if side['beside'] == 'L' else -1) * 3.4
        width = 1.2 + 2.5 * (now['t'] - side['t'])
    return {'speed': v, 'gap': now['gap_m'], 'lat': lat, 'lat_width': width,
            'side_known': side is not None, 'relative_v': relative_v}


def _speed_target(model, s, length, v):
    envelope = model['target_speed']
    # Preview deceleration constraints rather than jumping to a different lap's state.
    decel = max(1., -model['accel_bounds'][0] * .75)
    positions = (s + np.linspace(0, max(20., v * 2.), 12)) / length * len(envelope)
    target = np.interp(positions % len(envelope), np.arange(len(envelope) + 1), np.r_[envelope, envelope[0]])
    return float(np.min(np.sqrt(target ** 2 + 2 * decel * np.linspace(0, max(20., v * 2.), 12))))


def rollout(model, initial, rival, geometry, length, action, response, times):
    """Deterministic feedback policy with bounded actuators and a reacting rival."""
    s, v, lat = initial['s'], initial['v'], initial['lat']
    rs, rv, rl = s + rival['gap'], rival['speed'], rival['lat']
    brake, throttle = initial['brake'], initial['throttle']
    output = []; collision = False; unsupported = False
    last_t = times[0]
    for t in times:
        delta = t - last_t; elapsed = t - times[0]
        if delta > 0:
            target = _speed_target(model, s, length, v)
            wanted_b = float(np.clip((v - target) / 4., 0, 1))
            wanted_t = float(np.clip((target - v) / 3. + .25, 0, 1)) if wanted_b < .03 else 0.
            if elapsed <= REACTION_S:
                wanted_b, wanted_t = initial['brake'], initial['throttle']
            elif action == 'lift' and elapsed < .85:
                wanted_t = 0.
            elif action == 'earlier_brake' and elapsed < .85:
                wanted_b = min(1., max(wanted_b, initial['brake'] + .12)); wanted_t = 0.
            elif action == 'later_brake' and elapsed < .45:
                wanted_b = 0.; wanted_t = min(.2, initial['throttle'])
            # The following rule responds to the evolving gap, including rival braking.
            lateral_overlap = abs(lat - rl) < WIDTH + MARGIN + rival['lat_width']
            gap = rs - s
            if 0 < gap and lateral_overlap:
                desired_gap = LENGTH + MARGIN + (.45 if action == 'follow' else .25) * v
                closing = v - rv
                if gap < desired_gap or (closing > 0 and gap / max(.1, closing) < 1.5):
                    safe_target = max(0., rv + (gap - desired_gap) * .65)
                    wanted_b = max(wanted_b, float(np.clip((v - safe_target) / 5., 0, 1)))
                    wanted_t = min(wanted_t, float(np.clip((safe_target - v) / 3., 0, 1)))
            brake += float(np.clip(wanted_b - brake, -2.5 * delta, 2.5 * delta))
            throttle += float(np.clip(wanted_t - throttle, -3 * delta, 3 * delta))
            a = _accel(model, v, brake, throttle)
            v1 = max(0., v + a * delta); s += (v + v1) * .5 * delta; v = v1
            # Three conditional rival reactions. No scenario is a probability estimate.
            rival_target = _speed_target(model, rs, length, rv)
            ra = float(np.clip((rival_target - rv) * .9, *model['accel_bounds']))
            if response == 'brakes':
                ra = min(0., ra) - 1.5
            elif response == 'holds_speed':
                ra = 0.
            if s > rs and lateral_overlap:
                safe = max(0., v + (s - rs - LENGTH - .3 * rv) * .65)
                ra = min(ra, (safe - rv) * 1.2)
            rv1 = max(0., rv + float(np.clip(ra, *model['accel_bounds'])) * delta)
            rs += (rv + rv1) * .5 * delta; rv = rv1
            unsupported |= not (model['speed_range'][0] - 2 <= v <= model['speed_range'][1] + 2)
        gap = rs - s
        collision |= abs(gap) < LENGTH + MARGIN and abs(lat - rl) < WIDTH + MARGIN
        # Unknown rival lane grows with time. It must not shrink into apparent certainty.
        lateral_error = rival['lat_width'] + (.25 if rival['side_known'] else .8) * elapsed
        point, other = _at(geometry, s / length, lat), _at(geometry, rs / length, rl)
        output.append({'t': round(float(t), 3), 'me': [round(float(a), 3) for a in point],
                       'rival': [round(float(a), 3) for a in other], 'pct': float(s / length % 1),
                       'rival_pct': float(rs / length % 1), 'lat': round(lat, 3),
                       'rival_lat': [round(rl - lateral_error, 3), round(rl + lateral_error, 3)],
                       'speed_kph': round(v * 3.6, 2), 'rival_speed_kph': round(rv * 3.6, 2),
                       'brake': round(brake, 3), 'throttle': round(throttle, 3), 'gap_m': round(gap, 3),
                       'distance_m': round(s - initial['s'], 3)})
        last_t = t
    return {'frames': output, 'overlap': bool(collision), 'outside_model': bool(unsupported)}


def alternatives(scene, corner, model, geometry, length):
    if model is None:
        return {'status': 'UNAVAILABLE', 'reason': 'INSUFFICIENT_DYNAMICS', 'alternatives': []}
    geometry = {**geometry, **{key: np.asarray(geometry[key]) for key in ('ref', 'normal')}}
    frames = scene['frames']; entry = corner['t_entry']
    decision = max(frames[0]['t'] + .5, entry - 1.)
    start = next((i for i, f in enumerate(frames) if f['t'] >= decision), len(frames) - 1)
    initial_frame = frames[start]
    end = min(initial_frame['t'] + HORIZON, frames[-1]['t'])
    times = [f['t'] for f in frames[start:] if f['t'] <= end + 1e-6]
    if len(times) < 11 or max(np.diff(times)) > .2:
        return {'status': 'UNAVAILABLE', 'reason': 'DISCONTINUOUS_SCENE', 'alternatives': []}
    history = [f for f in frames[:start + 1] if initial_frame['t'] - .6 <= f['t']]
    rival = _rival_state(history, geometry)
    if rival is None:
        return {'status': 'UNAVAILABLE', 'reason': 'SHORT_RIVAL_HISTORY', 'alternatives': []}
    initial = {'s': initial_frame['pct'] * length, 'v': initial_frame['speed_kph'] / 3.6,
               'lat': _lateral(initial_frame, geometry), 'brake': initial_frame['brake'], 'throttle': initial_frame['throttle']}
    responses = ['track_pace', 'brakes', 'holds_speed']
    baseline = {r: rollout(model, initial, rival, geometry, length, 'baseline', r, times) for r in responses}
    # Anchor the Frenet path to the measured GPS at the decision, avoiding a visual teleport.
    initial_offset = np.asarray(initial_frame['me']) - baseline['track_pace']['frames'][0]['me']
    for run in baseline.values():
        for f in run['frames']:
            f['me'] = [round(float(a), 3) for a in np.asarray(f['me']) + initial_offset]
    actual = frames[start:start + len(times)]
    baseline_error = max(float(np.linalg.norm(np.asarray(predicted['me']) - observed['me']))
                         for predicted, observed in zip(baseline['track_pace']['frames'], actual))
    independent_error = model['validation'].get('distance_p90_m')
    items, omitted, previous_runs = [], [], []
    multi_car = initial_frame.get('other_nearby', 0) > 0
    for action, label in [('follow', '앞차에 맞춰 진입'), ('lift', '짧게 가속 페달 해제'),
                           ('earlier_brake', '제동을 먼저 시작'), ('later_brake', '제동을 0.2초 늦춤')]:
        runs = {r: rollout(model, initial, rival, geometry, length, action, r, times) for r in responses}
        for run in runs.values():
            for f in run['frames']:
                f['me'] = [round(float(a), 3) for a in np.asarray(f['me']) + initial_offset]
        gains = [runs[r]['frames'][-1]['distance_m'] - baseline[r]['frames'][-1]['distance_m'] for r in responses]
        gaps = [runs[r]['frames'][-1]['gap_m'] for r in responses]
        def same(a, b):
            return all(max(abs(x['distance_m'] - y['distance_m']) for x, y in zip(a[r]['frames'], b[r]['frames'])) < .1
                       and max(abs(x['speed_kph'] - y['speed_kph']) for x, y in zip(a[r]['frames'], b[r]['frames'])) < .2
                       for r in responses)
        if same(runs, baseline):
            omitted.append({'id': action, 'reason': 'NO_EFFECT_UNDER_TRAFFIC'})
            continue
        if any(same(runs, previous) for previous in previous_runs):
            omitted.append({'id': action, 'reason': 'DUPLICATE_RESPONSE'})
            continue
        previous_runs.append(runs)
        # Bound future position using model error plus unmodelled rival response. Not a CI.
        error = max(1., baseline_error, independent_error or 0.)
        overlap = any(run['overlap'] for run in runs.values())
        possible_overlap = any(abs(f['gap_m']) < LENGTH + MARGIN + error and
                               f['rival_lat'][0] - WIDTH - MARGIN <= f['lat'] <= f['rival_lat'][1] + WIDTH + MARGIN
                               for run in runs.values() for f in run['frames'])
        outside = any(run['outside_model'] for run in runs.values())
        reliable = independent_error is not None and baseline_error <= 3. and not outside and not multi_car
        status = ('OVERLAP_POSSIBLE' if overlap or possible_overlap else 'MODEL_UNCERTAIN' if not reliable
                  else 'EFFECT_WITHIN_ERROR' if max(abs(g) for g in gains) <= error else 'CONDITIONAL')
        items.append({'id': action, 'label': label, 'status': status, 'start_t': times[0], 'end_t': times[-1],
            'horizon_s': round(times[-1] - times[0], 2),
            'responses': [{'id': r, 'end_gap_m': round(gaps[i], 2), 'overlap': runs[r]['overlap'],
                           'trajectory': runs[r]['frames']}
                          for i, r in enumerate(responses)],
            'progress_delta_m': round(gains[0], 2), 'progress_range_m': [round(min(gains), 2), round(max(gains), 2)],
            'end_gap_range_m': [round(min(gaps) - error, 2), round(max(gaps) + error, 2)],
            'speed_delta_kph': round(runs['track_pace']['frames'][-1]['speed_kph'] - baseline['track_pace']['frames'][-1]['speed_kph'], 2),
            'position_error_m': round(error, 2), 'rankable': reliable and not possible_overlap and not overlap and min(gains) > error})
    return {'status': 'READY', 'version': VERSION, 'start_t': times[0], 'end_t': times[-1],
            'baselines': [{'id': r, 'trajectory': baseline[r]['frames']} for r in responses],
            'baseline_error_m': round(baseline_error, 2),
            'validation': model['validation'], 'training_laps': model['training_laps'],
            'lateral_planning': 'UNAVAILABLE_TRACK_EDGES', 'multi_car_unmodelled': multi_car,
            'omitted': omitted, 'alternatives': items}
