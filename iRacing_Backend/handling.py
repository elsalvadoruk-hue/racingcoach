"""Observed steering/yaw tendencies; thresholds are initial, unvalidated values.

This module does not select practice causes or write to a game. Persistence is
explicit through HandlingHistory; analyzing recorded samples is read-only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import warnings

import numpy as np

from corner_events import RawLap, _edges, _window_ok

UNDER_B, OVER_B = .85, 1.15
UNDER_SECONDS, OVER_SECONDS, COUNTER_SECONDS = .3, .2, .15
MIN_SPEED, MIN_STEER, COUNTER_YAW = 15., .03, .1
MAX_STABLE_YAW_ACCEL = .3
MIN_BAND_SAMPLES = 200
MIN_REFERENCE_LAPS = 3
MIN_LAPS_FOR_POINT_SPREAD = 5
Z_LIMIT = 2.5
MAX_SPEED_RELATIVE = .04
MAX_LAT_ACCEL_ABS = .8
MAX_LAT_ACCEL_RELATIVE = .10
MAX_PEDAL_DIFFERENCE = .2
# A phase counts as compared when this share of its eligible time has a
# personal comparison. Fixed before evaluation; not tuned to any result.
COVERAGE_MIN = .9
METHOD = 'personal_corner_phase_v3'
# How much the offline evaluation supports each kind (logs/analysis-210/handling-v2-vs-v3.txt,
# five recordings, 2026-10-23). Understeer: above the position null (1.13x, 76.5% of
# draws), slower laps (AUC .71) and repeats between lap halves. Oversteer: barely above
# the null (1.07x, 55.5%) and no time relation (AUC .46), so it is shown as a reference.
# Countersteer is a measured event, not a personal comparison. Display only; detection unchanged.
EVIDENCE = {'understeer': 'compared', 'oversteer': 'reference', 'countersteer': 'measured'}
REQUIRED = ('Speed', 'SteeringWheelAngle', 'YawRate', 'LatAccel', 'Brake', 'Throttle')
PHASES = ('braking', 'rotation', 'drive')
TIME_LINK_PERMUTATIONS = 200
TIME_LINK_PERCENTILES = (10, 90)


def adjusted_corner_times(rows, corners):
    """Remove shared lap drift; missing/constant corner evidence stays unknown."""
    laps = sorted({row['lap'] for row in rows})
    ci = {corner['id']: i for i, corner in enumerate(corners)}
    li = {lap: i for i, lap in enumerate(laps)}
    times = np.full((len(ci), len(li)), np.nan)
    for row in rows:
        if row['time_s'] is not None:
            times[ci[row['corner_id']], li[row['lap']]] = row['time_s']
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        center = np.nanmedian(times, axis=1, keepdims=True)
        spread = 1.4826 * np.nanmedian(abs(times - center), axis=1, keepdims=True)
        spread[spread <= 1e-4] = np.nan
        standard = (times - center) / spread
        adjusted = np.array([standard[c] - np.nanmedian(np.delete(standard, c, axis=0), axis=0)
                             for c in range(len(ci))])
    return {(corner, lap): adjusted[c, l] for corner, c in ci.items() for lap, l in li.items()}


def corner_time_link(rows, adjusted, kind, signature):
    """A seeded within-corner label permutation; association is not a cause."""
    observed_key = 'counter_observed' if kind == 'countersteer' else 'observed'
    good = [row for row in rows
            if (row[observed_key] or any(event['kind'] == kind for event in row['events']))
            and row.get('v_start') is not None and np.isfinite(row['v_start'])
            and np.isfinite(adjusted.get((row['corner_id'], row['lap']), np.nan))]
    flag = np.array([any(event['kind'] == kind for event in row['events']) for row in good], dtype=bool)
    if flag.sum() < 3 or (~flag).sum() < 3:
        return None
    y = np.array([adjusted[(row['corner_id'], row['lap'])] for row in good])
    speed = np.array([row['v_start'] for row in good])
    if len({row['lap'] for row in good}) >= 6 and speed.std() > 0:
        z = (speed - speed.mean()) / speed.std()
        y -= np.polyfit(z, y, 1)[0] * z
    # Floating-point cancellation of identical shared drift must not create a link.
    if np.ptp(y) <= 1e-12:
        return 'unclear'
    difference = float(np.median(y[flag]) - np.median(y[~flag]))
    seed_bytes = hashlib.sha256(json.dumps(signature, default=str, sort_keys=True).encode()).digest()
    rng = np.random.default_rng(int.from_bytes(seed_bytes[:8], 'big'))
    shuffled = []
    for _ in range(TIME_LINK_PERMUTATIONS):
        labels = rng.permutation(flag)
        shuffled.append(np.median(y[labels]) - np.median(y[~labels]))
    low, high = np.percentile(shuffled, TIME_LINK_PERCENTILES)
    return 'slower' if difference > high else 'faster' if difference < low else 'unclear'


def _supported(raw):
    return raw is not None and all(key in raw.channels for key in REQUIRED)


def learn_bands(raw_laps):
    """Speed quintiles; the old global k values are diagnostics, not references."""
    speeds, reactions = [], []
    for raw in raw_laps:
        if not _supported(raw):
            continue
        c = raw.channels
        speed, steer, yaw = (c[key] for key in REQUIRED[:3])
        finite = np.isfinite(raw.time_s) & np.isfinite(raw.pct)
        for key in REQUIRED:
            finite &= np.isfinite(c[key])
        derivative = np.full(len(speed), np.nan)
        edges = _edges(raw)
        np.divide(np.diff(yaw), np.diff(raw.time_s), out=derivative[1:], where=edges)
        derivative[1:][~edges] = np.nan
        stable = (finite & (speed > MIN_SPEED) & (np.abs(steer) > MIN_STEER)
                  & (c['Brake'] < .05) & (c['Throttle'] > .05) & (c['Throttle'] < .6)
                  & (np.abs(derivative) < MAX_STABLE_YAW_ACCEL) & (steer * yaw > 0))
        speeds.extend(speed[stable])
        reactions.extend(np.abs(yaw[stable]) / (np.abs(steer[stable]) * speed[stable]))
    speed, reaction = np.asarray(speeds), np.asarray(reactions)
    edges = np.percentile(speed, [20, 40, 60, 80]) if len(speed) else np.full(4, np.nan)
    assignment = np.searchsorted(edges, speed, side='right')
    bands = []
    for index in range(5):
        values = reaction[assignment == index]
        bands.append({'low_mps': float(edges[index - 1]) if index > 0 and len(speed) else None,
                      'high_mps': float(edges[index]) if index < 4 and len(speed) else None,
                      'samples': int(len(values)),
                      'k_band': float(np.median(values)) if len(values) >= MIN_BAND_SAMPLES else None})
    return {'edges_mps': edges, 'bands': bands}


def _sustained(mask, durations, minimum):
    """Invalid/unknown intervals break runs, including inside a partial phase."""
    mask = np.asarray(mask) & (durations > 0)
    changes = np.diff(np.r_[False, mask, False].astype(int))
    found = np.zeros(len(mask), dtype=bool)
    for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
        if np.sum(durations[start:end]) + 1e-9 >= minimum:
            found[start:end] = True
    return found


def _accompaniment(raw, indices, durations):
    c = raw.channels
    votes = Counter()
    edges = _edges(raw)
    for i, weight in zip(indices, durations):
        before = int(np.searchsorted(raw.time_s, raw.time_s[i] - .5))
        throttle = c['Throttle'][before:i + 1]
        lift = (len(throttle) > 1 and np.all(edges[before:i]) and np.all(np.isfinite(throttle))
                and np.max(throttle) - c['Throttle'][i] >= .3)
        kind = 'braking' if c['Brake'][i] > .08 else 'lift' if lift else 'power' if c['Throttle'][i] > .5 else None
        votes[kind] += weight
    return votes.most_common(1)[0][0] if votes else None


def lagged_values(raw, values, lag_s):
    result = np.full(len(values), np.nan)
    if lag_s == 0:
        return np.asarray(values, dtype=float).copy()
    finite = np.isfinite(values) & np.isfinite(raw.time_s)
    connected = _edges(raw) & finite[:-1] & finite[1:]
    changes = np.diff(np.r_[False, connected, False].astype(int))
    for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
        axis = raw.time_s[start:end + 1]
        query = axis - lag_s
        keep = (query >= axis[0]) & (query <= axis[-1])
        indices = np.arange(start, end + 1)[keep]
        result[indices] = np.interp(query[keep], axis, values[start:end + 1])
    return result


def steer_rate(raw):
    rate = np.full(len(raw.time_s), np.nan)
    steer = raw.channels['SteeringWheelAngle']
    good = _edges(raw) & np.isfinite(steer[:-1]) & np.isfinite(steer[1:])
    np.divide(np.diff(steer), np.diff(raw.time_s), out=rate[1:], where=good)
    rate[1:][~good] = np.nan
    return rate


def estimate_lag(raws, selections):
    if not raws:
        return 0.
    tick = float(np.median([raw.tick_s for raw in raws]))
    best, best_corr = 0., -np.inf
    for lag_s in np.arange(int(np.floor(.30 / tick + 1e-9)) + 1) * tick:
        count = sx = sy = sxx = syy = sxy = 0.
        for raw, selection in zip(raws, selections):
            c = raw.channels
            x = lagged_values(raw, c['SteeringWheelAngle'] * c['Speed'], lag_s)
            y = c['YawRate']
            good = selection & np.isfinite(x) & np.isfinite(y)
            x, y = x[good], y[good]
            count += len(x)
            sx += float(x.sum()); sy += float(y.sum())
            sxx += float(x @ x); syy += float(y @ y); sxy += float(x @ y)
        if count < 3:
            continue
        vx, vy = sxx - sx * sx / count, syy - sy * sy / count
        if vx <= 1e-12 or vy <= 1e-12:
            continue
        corr = (sxy - sx * sy / count) / np.sqrt(vx * vy)
        if corr > best_corr + 1e-12:
            best, best_corr = float(lag_s), float(corr)
    return best if best_corr >= .5 else 0.


def prepare_phase(raw, start_pct, end_pct, model):
    """Keep original time order and a boundary-straddling pair on each side."""
    if not _supported(raw) or end_pct <= start_pct:
        return None
    indices = np.flatnonzero((raw.pct >= start_pct) & (raw.pct <= end_pct))
    if not len(indices):
        return None
    start, end = max(0, indices[0] - 1), min(len(raw.pct), indices[-1] + 2)
    local = RawLap(raw.time_s[start:end], raw.pct[start:end],
                   {key: raw.channels[key][start:end] for key in REQUIRED}, raw.tick_s, raw.track_length_m)
    c = local.channels
    speed, steer, yaw = (c[key] for key in REQUIRED[:3])
    finite = np.isfinite(local.time_s) & np.isfinite(local.pct)
    for key in REQUIRED:
        finite &= np.isfinite(c[key])
    eligible = finite & (speed > MIN_SPEED) & (np.abs(steer) > MIN_STEER)
    aligned = model.get('_aligned_steer', {}).get(id(raw))
    q_steer = steer if aligned is None else aligned[start:end]
    q = np.full(len(speed), np.nan)
    np.divide(np.abs(yaw), np.abs(q_steer) * speed, out=q,
              where=eligible & np.isfinite(q_steer) & (abs(q_steer) > MIN_STEER))
    # q is undefined where the aligned steering is known to be below the minimum,
    # just as low current steering was never compared. Unknown aligned steering
    # (e.g. right after a recording gap) stays missing evidence.
    q_ready = eligible & ~(np.isfinite(q_steer) & (abs(q_steer) <= MIN_STEER))
    inside = (local.pct >= start_pct) & (local.pct <= end_pct)
    edges = _edges(local) & finite[:-1] & finite[1:]
    dp = np.diff(local.pct)
    left, right = np.zeros(len(dp)), np.ones(len(dp))
    np.divide(start_pct - local.pct[:-1], dp, out=left, where=dp > 0)
    np.divide(end_pct - local.pct[:-1], dp, out=right, where=dp > 0)
    durations = np.zeros(len(speed))
    durations[:-1] = np.where(edges & (dp > 0), np.maximum(0., np.minimum(1., right) - np.maximum(0., left)) * np.diff(local.time_s), 0.)
    bands = np.searchsorted(model['edges_mps'], speed, side='right')
    reference_ok = eligible & (q_steer * yaw > 0) & (q > 0)
    interval_ok = edges & reference_ok[:-1] & reference_ok[1:] & (dp > 0) & (durations[:-1] > 0)
    support = np.bincount(bands[reference_ok & inside & (np.r_[interval_ok, False] | np.r_[False, interval_ok])], minlength=5)
    changes = np.diff(np.r_[False, interval_ok, False].astype(int))
    segments = [slice(a, b + 1) for a, b in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))]
    return {'raw': raw, 'local': local, 'offset': start, 'start': start_pct, 'end': end_pct,
            'q': q, 'q_steer': q_steer, 'bands': bands, 'support': support, 'eligible': eligible, 'q_ready': q_ready,
            'inside': inside, 'durations': durations, 'segments': segments,
            'complete': bool(raw.pct[0] <= start_pct < end_pct <= raw.pct[-1]
                             and _window_ok(local, start_pct, end_pct, REQUIRED))}


def _reference_at(reference, pct):
    """Interpolate only within one observed forward interval; overlaps are unknown."""
    values = np.full((6, len(pct)), np.nan)
    seen = np.zeros(len(pct), dtype=int)
    local = reference['local']
    columns = [reference['q'], *[local.channels[key] for key in ('Speed', 'SteeringWheelAngle', 'Brake', 'Throttle', 'LatAccel')]]
    for span in reference['segments']:
        axis = local.pct[span]
        mask = ((pct >= max(axis[0], reference['start'])) & (pct <= min(axis[-1], reference['end'])))
        if np.any(mask):
            for index, column in enumerate(columns):
                values[index, mask] = np.interp(pct[mask], axis, column[span])
            seen[mask] += 1
    values[:, seen != 1] = np.nan
    return values


def _classify_phases(phases, model):
    """Share interpolation across targets, retaining only a small comparison batch.

    The matrix budget is 16 MiB of float64 predictions. A single unusually long
    phase still needs its original reference matrix, but phases are never joined
    into an unbounded lap-by-lap array. No data survives this analysis call.
    """
    references = [phase for phase in phases if phase is not None]
    point_limit = max(1, _REFERENCE_BATCH_VALUES // max(1, len(references)))
    batch, points = [], 0
    for phase in phases:
        size = len(phase['local'].pct) if phase is not None else 0
        if batch and points + size > point_limit:
            yield from _classify_phase_batch(batch, references, model)
            batch, points = [], 0
        batch.append(phase)
        points += size
    if batch:
        yield from _classify_phase_batch(batch, references, model)


_REFERENCE_BATCH_VALUES = 2097152


def _classify_phase_batch(phases, references, model):
    targets = [phase for phase in phases if phase is not None]
    if not targets:
        for phase in phases:
            yield classify_phase(phase, references, model)
        return
    pct = np.concatenate([phase['local'].pct for phase in targets])
    speed, steer, brake, throttle, lat = (
        np.concatenate([phase['local'].channels[key] for phase in targets])
        for key in ('Speed', 'SteeringWheelAngle', 'Brake', 'Throttle', 'LatAccel'))
    bands = np.concatenate([phase['bands'] for phase in targets])
    bounds = np.cumsum([0, *[len(phase['local'].pct) for phase in targets]])
    slices = defaultdict(list)
    for phase, start, end in zip(targets, bounds[:-1], bounds[1:]):
        slices[id(phase)].append(slice(start, end))
    predictions = np.empty((len(references), len(pct)))
    available = np.zeros(len(pct), dtype=int)
    lateral_rejected = np.zeros(len(pct), dtype=int)
    support = np.zeros(5, dtype=int)
    for index, reference in enumerate(references):
        support += reference['support']
        # np.interp and the observed-segment masks are exactly the scalar path.
        # Concatenating queries only removes repeated Python calls/allocations.
        q, rspeed, rsteer, rbrake, rthrottle, rlat = _reference_at(reference, pct)
        for span in slices.get(id(reference), ()):
            q[span] = np.nan  # A target never supplies its own reference.
        available += np.isfinite(q)
        comparable = ((np.searchsorted(model['edges_mps'], rspeed, side='right') == bands)
                      & (abs(rspeed - speed) <= MAX_SPEED_RELATIVE * speed)
                      & (abs(rbrake - brake) <= MAX_PEDAL_DIFFERENCE)
                      & (abs(rthrottle - throttle) <= MAX_PEDAL_DIFFERENCE)
                      & (rsteer * steer > 0))
        lateral = ((rlat * lat > 0)
                   & (abs(rlat - lat) <= np.maximum(MAX_LAT_ACCEL_ABS, MAX_LAT_ACCEL_RELATIVE * abs(lat))))
        lateral_rejected += np.isfinite(q) & comparable & ~lateral
        predictions[index] = np.where(comparable & lateral, q, np.nan)
    reference_counts = Counter(id(reference) for reference in references)
    supports = [support - reference_counts[id(phase)] * phase['support'] for phase in targets]
    baseline = np.full(len(pct), np.nan)
    log_baseline = np.full(len(pct), np.nan)
    spread = np.full(len(pct), np.nan)
    matched = np.zeros(len(pct), dtype=int)
    if np.all(np.isfinite(model['edges_mps'])):
        logs = np.full_like(predictions, np.nan)
        np.log(predictions, out=logs, where=np.isfinite(predictions) & (predictions > 0))
        del predictions
        matched = np.sum(np.isfinite(logs), axis=0)
        point_support = np.concatenate([counts[phase['bands']] for phase, counts in zip(targets, supports)])
        good = (matched >= MIN_REFERENCE_LAPS) & (point_support >= MIN_BAND_SAMPLES)
        # A self-reference is NaN in its own target columns. Removing that row
        # and ignoring its NaNs give exactly the same median and MAD; grouping
        # columns avoids copying/sorting one small matrix for every target lap.
        log_baseline[good] = np.nanmedian(logs[:, good], axis=0)
        baseline[good] = np.exp(log_baseline[good])
        point_spread = good & (matched >= MIN_LAPS_FOR_POINT_SPREAD)
        if np.any(point_spread):
            spread[point_spread] = 1.4826 * np.nanmedian(
                abs(logs[:, point_spread] - log_baseline[point_spread]), axis=0)
        # Noise borrowing remains restricted to this target's own phase.
        for phase, start, end in zip(targets, bounds[:-1], bounds[1:]):
            span = slice(start, end)
            sources = (point_spread[span] & phase['inside'] & phase['eligible']
                       & (phase['q_steer'] * phase['local'].channels['YawRate'] > 0))
            if np.any(sources):
                spread[span][good[span] & ~point_spread[span]] = np.median(spread[span][sources])
    position = 0
    for phase in phases:
        if phase is None:
            yield classify_phase(phase, references, model)
            continue
        span = slice(bounds[position], bounds[position + 1])
        comparison = (baseline[span], spread[span], matched[span], supports[position],
                      available[span], lateral_rejected[span])
        yield classify_phase(phase, references, model, _comparison=comparison)
        position += 1


def classify_phase(phase, references, model, *, _comparison=None):
    """Other laps predict this phase's usual q at the same physical position.

    200 original reference samples are required per corner/phase/speed band,
    not per interpolated point. Each point also needs three independent laps.
    These reference matching guards, like the detection limits, are unvalidated.
    """
    unknown = {'observed': False, 'counter_observed': False, 'b_median': None, 'z_median': None, 'events': [], 'known_fraction': 0.,
               'unknown_reasons': ['phase_unavailable']}
    if phase is None:
        return unknown
    local, raw = phase['local'], phase['raw']
    speed, steer, yaw, brake, throttle = (local.channels[key] for key in ('Speed', 'SteeringWheelAngle', 'YawRate', 'Brake', 'Throttle'))
    eligible, durations = phase['eligible'], phase['durations']
    if _comparison is None:
        predictions = []
        support = np.zeros(5, dtype=int)
        available = np.zeros(len(speed), dtype=int)
        lateral_rejected = np.zeros(len(speed), dtype=int)
        for reference in references:
            if reference is None or reference is phase:
                continue
            support += reference['support']
            q, rspeed, rsteer, rbrake, rthrottle, rlat = _reference_at(reference, local.pct)
            available += np.isfinite(q)
            comparable = ((np.searchsorted(model['edges_mps'], rspeed, side='right') == phase['bands'])
                          & (abs(rspeed - speed) <= MAX_SPEED_RELATIVE * speed)
                          & (abs(rbrake - brake) <= MAX_PEDAL_DIFFERENCE)
                          & (abs(rthrottle - throttle) <= MAX_PEDAL_DIFFERENCE)
                          & (rsteer * steer > 0))
            lat = local.channels['LatAccel']
            lateral = ((rlat * lat > 0)
                       & (abs(rlat - lat) <= np.maximum(MAX_LAT_ACCEL_ABS, MAX_LAT_ACCEL_RELATIVE * abs(lat))))
            lateral_rejected += np.isfinite(q) & comparable & ~lateral
            comparable &= lateral
            predictions.append(np.where(comparable, q, np.nan))
        baseline = np.full(len(speed), np.nan)
        log_baseline = np.full(len(speed), np.nan)
        spread = np.full(len(speed), np.nan)
        matched = np.zeros(len(speed), dtype=int)
        if len(predictions) and np.all(np.isfinite(model['edges_mps'])):
            matrix = np.asarray(predictions)
            logs = np.full_like(matrix, np.nan)
            np.log(matrix, out=logs, where=np.isfinite(matrix) & (matrix > 0))
            matched = np.sum(np.isfinite(logs), axis=0)
            good = (matched >= MIN_REFERENCE_LAPS) & (support[phase['bands']] >= MIN_BAND_SAMPLES)
            log_baseline[good] = np.nanmedian(logs[:, good], axis=0)
            baseline[good] = np.exp(log_baseline[good])
            point_spread = good & (matched >= MIN_LAPS_FOR_POINT_SPREAD)
            if np.any(point_spread):
                spread[point_spread] = 1.4826 * np.nanmedian(
                    abs(logs[:, point_spread] - log_baseline[point_spread]), axis=0)
                # Borrow only point spreads supported by five other laps in this phase.
                sources = point_spread & phase['inside'] & eligible & (phase['q_steer'] * yaw > 0)
                if np.any(sources):
                    spread[good & ~point_spread] = np.median(spread[sources])
    else:
        baseline, spread, matched, support, available, lateral_rejected = _comparison
    ratio = np.full(len(speed), np.nan)
    np.divide(phase['q'], baseline, out=ratio,
              where=eligible & (phase['q_steer'] * yaw > 0) & np.isfinite(baseline) & (baseline > 0) & np.isfinite(spread))
    delta = np.full(len(speed), np.nan)
    np.log(ratio, out=delta, where=np.isfinite(ratio) & (ratio > 0))
    z = np.full(len(speed), np.nan)
    np.divide(delta, spread, out=z, where=np.isfinite(delta) & (spread > 0))
    z[np.isfinite(delta) & (spread == 0) & (delta == 0)] = 0.
    # With identical reference values the minimum ratio alone decides; z is undefined.
    steady = phase.get('steady_steer', np.ones(len(speed), dtype=bool))
    under = _sustained(steady & (delta < np.log(UNDER_B)) & ((spread == 0) | (z < -Z_LIMIT)), durations, UNDER_SECONDS)
    over = _sustained(steady & (delta > np.log(OVER_B)) & ((spread == 0) | (z > Z_LIMIT)), durations, OVER_SECONDS)
    # Countersteering is a separate measured event, independent of personal q.
    counter = _sustained(eligible & (steer * yaw < 0) & (np.abs(yaw) > COUNTER_YAW), durations, COUNTER_SECONDS)
    events = []
    for kind, selected in (('understeer', under), ('oversteer', over), ('countersteer', counter)):
        if np.any(selected):
            indices = np.flatnonzero(selected)
            events.append({'kind': kind, 'with': _accompaniment(raw, indices + phase['offset'], durations[indices]),
                           'seconds': float(np.sum(durations[selected])),
                           '_z_samples': z[selected & np.isfinite(z)].tolist() if kind != 'countersteer' else [],
                           'z_median': float(np.median(z[selected & np.isfinite(z)]))
                               if kind != 'countersteer' and np.any(selected & np.isfinite(z)) else None})
    required = phase['inside'] & phase.get('q_ready', eligible) & (durations > 0)
    counter_required = phase['inside'] & eligible & (durations > 0)
    known = required & np.isfinite(ratio)
    known_fraction = float(np.sum(durations[known]) / np.sum(durations[required])) if np.any(required) else 0.
    observed = bool(np.any(required) and phase['complete'] and known_fraction >= COVERAGE_MIN - 1e-9)
    # Explain missing evidence without relaxing the personal reference or detection limits.
    # Several reasons may apply to the same phase; these are not disjoint percentages.
    reasons = []
    if not phase['complete']:
        reasons.append('phase_incomplete')
    if not np.any(required):
        reasons.append('no_eligible_samples')
    elif not observed:
        if not np.all(np.isfinite(model['edges_mps'])):
            reasons.append('speed_bands_unavailable')
        if np.any(required & (available < MIN_REFERENCE_LAPS)):
            reasons.append('reference_laps_shortage')
        if np.any(required & (support[phase['bands']] < MIN_BAND_SAMPLES)):
            reasons.append('reference_samples_shortage')
        if np.all(np.isfinite(model['edges_mps'])) and np.any(required & (available >= MIN_REFERENCE_LAPS) & (matched < MIN_REFERENCE_LAPS)):
            reasons.append('inputs_not_comparable')
        if np.any(required & (matched < MIN_REFERENCE_LAPS) & (lateral_rejected > 0)):
            reasons.append('lateral_not_comparable')
        if np.any(required & (phase['q_steer'] * yaw <= 0)):
            reasons.append('rotation_direction_not_comparable')
        if np.any(required & ~np.isfinite(phase['q_steer'])):
            reasons.append('steer_lag_unavailable')
        if np.any(required & np.isfinite(baseline) & ~np.isfinite(spread)):
            reasons.append('noise_unknown')
    return {'observed': observed, 'counter_observed': bool(np.any(counter_required) and phase['complete']),
            'b_median': float(np.median(ratio[known])) if np.any(known) else None,
            'z_median': float(np.median(z[known & np.isfinite(z)])) if np.any(known & np.isfinite(z)) else None,
            'known_fraction': known_fraction,
            'events': events, 'unknown_reasons': reasons}


def analyze_handling(analyzer, corners, *, include_observations=False):
    """Summarize valid recorded laps without persisting or changing diagnoses."""
    metadata = analyzer.telemetry.metadata
    approximate = metadata.get('game') == 'lmu'
    if approximate and metadata.get('steering_scale') != 'MEASURED':
        return None
    if not all(key in analyzer.channels for key in REQUIRED):
        return None
    laps = []
    for lap in analyzer.valid_laps:
        grid = analyzer._lap_grid(lap)
        raw = analyzer._raw_lap_for(grid)
        if raw is not None:
            laps.append((lap, grid, raw))
    model = learn_bands([raw for _, _, raw in laps])
    observations = defaultdict(list)
    event_z = defaultdict(list)
    prepared = defaultdict(list)
    coverage = Counter(classifiable=0, not_applicable=0, partial=0,
                       total_slots=len(analyzer.valid_laps) * len(corners) * len(PHASES),
                       unknown=(len(analyzer.valid_laps) - len(laps)) * len(corners) * len(PHASES))
    applicable_keys = {(corner['id'], phase) for corner in corners for phase in PHASES} if coverage['unknown'] else set()
    phase_counts = defaultdict(Counter)
    unknown_reasons = Counter()
    phase_reasons = defaultdict(Counter)
    for key in applicable_keys:
        phase_counts[key]['unknown'] = len(analyzer.valid_laps) - len(laps)
        phase_reasons[key]['raw_lap_unavailable'] = len(analyzer.valid_laps) - len(laps)
        unknown_reasons['raw_lap_unavailable'] += len(analyzer.valid_laps) - len(laps)
    for lap, grid, raw in laps:
        for corner in corners:
            entry, apex, exit_pct = (corner[key] for key in ('entry_pct', 'apex_pct', 'exit_pct'))
            indices = [int(np.argmin(abs(grid['distance_pct'] - value))) for value in (entry, apex, exit_pct)]
            metrics = analyzer._corner_metrics(grid, *indices)
            # The same T3 partition, using this lap's own input events.
            own = {**corner, 'reference': metrics, 'delta_s': 0.}
            phases = analyzer._corner_phases(grid, grid, own, None)
            elapsed = None
            if (_window_ok(raw, entry, exit_pct, REQUIRED)
                    and raw.pct[0] <= entry <= exit_pct <= raw.pct[-1]):
                elapsed = float(np.interp(exit_pct, raw.pct, raw.time_s) - np.interp(entry, raw.pct, raw.time_s))
            for phase in PHASES:
                bounds = phases.get(phase) if phases else None
                if bounds is None:
                    applicable_keys.add((corner['id'], phase))
                    coverage['unknown'] += 1
                    phase_counts[(corner['id'], phase)]['unknown'] += 1
                    phase_reasons[(corner['id'], phase)]['phase_unavailable'] += 1
                    unknown_reasons['phase_unavailable'] += 1
                    continue
                if bounds['start_pct'] == bounds['end_pct']:
                    coverage['not_applicable'] += 1
                    phase_counts[(corner['id'], phase)]['not_applicable'] += 1
                    continue
                applicable_keys.add((corner['id'], phase))
                data = prepare_phase(raw, bounds['start_pct'], bounds['end_pct'], model)
                prepared[(corner['id'], phase)].append({'lap': lap.lap_number, 'time_s': elapsed, 'data': data})
    selections = {id(raw): np.zeros(len(raw.time_s), dtype=bool) for _, _, raw in laps}
    for rows in prepared.values():
        for row in rows:
            data = row['data']
            if data is not None:
                used = data['inside'] & data['eligible'] & (data['durations'] > 0)
                selections[id(data['raw'])][data['offset']:data['offset'] + len(used)] |= used
    # Incomplete raw channel arrays stay unknown, as in prepare_phase().
    raws = [raw for _, _, raw in laps if _supported(raw)]
    lag_s = estimate_lag(raws, [selections[id(raw)] for raw in raws])
    model['_aligned_steer'] = {id(raw): lagged_values(raw, raw.channels['SteeringWheelAngle'], lag_s) for raw in raws}
    rates = {id(raw): lagged_values(raw, steer_rate(raw), lag_s) for raw in raws}
    phase_rates = {}
    for phase_name in PHASES:
        values = []
        for key, rows in prepared.items():
            if key[1] != phase_name:
                continue
            for row in rows:
                data = row['data']
                if data is not None:
                    rate = rates[id(data['raw'])][data['offset']:data['offset'] + len(data['q'])]
                    keep = data['inside'] & data['eligible'] & (data['durations'] > 0) & np.isfinite(rate)
                    values.extend(abs(rate[keep]))
        phase_rates[phase_name] = float(np.percentile(values, 90)) if values else None
    for key, rows in prepared.items():
        for row in rows:
            previous = row['data']
            if previous is not None:
                data = prepare_phase(previous['raw'], previous['start'], previous['end'], model)
                rate = rates[id(data['raw'])][data['offset']:data['offset'] + len(data['q'])]
                limit = phase_rates[key[1]]
                data['steady_steer'] = (np.isfinite(rate) & (abs(rate) <= limit)) if limit is not None else np.zeros(len(rate), dtype=bool)
                row['data'] = data
    for key, rows in prepared.items():
        references = [row['data'] for row in rows]
        for row, measured in zip(rows, _classify_phases(references, model)):
            # Keep full sustained-sample values only during aggregation, never in API rows.
            for event in measured['events']:
                event_z[(*key, event['kind'])].extend(event.pop('_z_samples', []))
            coverage['classifiable' if measured['observed'] else 'unknown'] += 1
            phase_counts[key]['classifiable' if measured['observed'] else 'unknown'] += 1
            coverage['partial'] += bool(0 < measured['known_fraction'] < 1)
            coverage['classifiable_partial'] += bool(measured['observed'] and measured['known_fraction'] < 1)
            unknown_reasons.update(measured['unknown_reasons'])
            phase_reasons[key].update(measured['unknown_reasons'])
            data = row['data']
            v_start = (float(np.interp(data['start'], data['local'].pct, data['local'].channels['Speed']))
                       if data is not None else None)
            observations[key].append({'lap': row['lap'], 'time_s': row['time_s'], 'v_start': v_start, **measured})
    time_rows = [{'corner_id': key[0], **row} for key, rows in observations.items() for row in rows]
    adjusted = adjusted_corner_times(time_rows, corners)
    signature = getattr(analyzer.telemetry, 'signature', None)
    summaries = []
    for corner in corners:
        for phase in PHASES:
            rows = observations[(corner['id'], phase)]
            for kind in ('understeer', 'oversteer', 'countersteer'):
                hits = [row for row in rows if any(event['kind'] == kind for event in row['events'])]
                if not hits:
                    continue
                hit_laps = {row['lap'] for row in hits}
                slower = [row['time_s'] for row in hits if row['time_s'] is not None]
                observation_key = 'counter_observed' if kind == 'countersteer' else 'observed'
                other = [row['time_s'] for row in rows if row['lap'] not in hit_laps and row[observation_key] and row['time_s'] is not None]
                votes = Counter(event['with'] for row in hits for event in row['events'] if event['kind'] == kind)
                selected_z = event_z[(corner['id'], phase, kind)]
                summaries.append({'corner_id': corner['id'], 'label': corner['label'], 'apex_pct': corner['apex_pct'],
                                  'phase': phase, 'kind': kind, 'with': votes.most_common(1)[0][0],
                                  'laps': len(hit_laps), 'of_laps': len(analyzer.valid_laps),
                                  'observed_laps': sum(row[observation_key] for row in rows),
                                  'z_median': float(np.median(selected_z)) if selected_z else None,
                                  'time_cost_s': round(float(np.median(slower) - np.median(other)), 4)
                                      if len(slower) >= 3 and len(other) >= 3 else None,
                                  'time_link': corner_time_link(
                                      [{**row, 'corner_id': corner['id']} for row in rows], adjusted, kind, signature),
                                  'approximate': approximate, 'method': METHOD, 'evidence': EVIDENCE[kind],
                                  'basis': 'measured_countersteer' if kind == 'countersteer' else 'personal_corner_phase'})
    applicable = coverage['total_slots'] - coverage['not_applicable']
    coverage.update(applicable=applicable,
                    classifiable_corner_phases=sum(any(row['observed'] for row in rows) for rows in observations.values()),
                    applicable_corner_phases=len(applicable_keys),
                    result_items=len(summaries), time_cost_items=sum(item['time_cost_s'] is not None for item in summaries))
    coverage['unknown_fraction'] = coverage['unknown'] / applicable if applicable else None
    summaries.sort(key=lambda item: (item['evidence'] == 'reference', item['time_link'] != 'slower', item['time_cost_s'] is None,
                                     -(item['time_cost_s'] or 0), -item['laps']))
    result = {'source': 'approximate' if approximate else 'measured', 'approximate': approximate,
            'corners': summaries, 'k_bands': model['bands'],
            'valid_laps': len(analyzer.valid_laps), 'thresholds_validated': False, 'method': METHOD,
            'phase_coverage': [{'corner_id': key[0], 'phase': key[1], 'classifiable': counts['classifiable'],
                                'unknown': counts['unknown'], 'not_applicable': counts['not_applicable'],
                                'unknown_reasons': dict(phase_reasons[key])}
                               for key, counts in phase_counts.items()],
            'unknown_reasons': dict(unknown_reasons),
            'steer_lag_s': lag_s, 'fast_steer_rate_rad_s': phase_rates, 'evidence': dict(EVIDENCE),
            'coverage_rule': {'min_compared_time_fraction': COVERAGE_MIN},
            'time_link_rules': {'permutations': TIME_LINK_PERMUTATIONS,
                                'percentiles': list(TIME_LINK_PERCENTILES), 'min_group_laps': 3},
            'noise_rules': {'z_limit': Z_LIMIT, 'min_ratio': [UNDER_B, OVER_B], 'spread': 'mad',
                            'min_laps_for_point_spread': MIN_LAPS_FOR_POINT_SPREAD},
            'coverage': dict(coverage), 'reference_rules': {'other_laps': MIN_REFERENCE_LAPS,
                'samples_per_corner_phase_speed_band': MIN_BAND_SAMPLES, 'max_speed_relative': MAX_SPEED_RELATIVE,
                'max_lat_accel_abs': MAX_LAT_ACCEL_ABS, 'max_lat_accel_relative': MAX_LAT_ACCEL_RELATIVE,
                'max_pedal_difference': MAX_PEDAL_DIFFERENCE}}
    if include_observations:
        # Only the offline release audit asks for per-lap facts; ordinary API payloads stay compact.
        result['observations'] = [{'corner_id': key[0], 'phase': key[1], **row}
                                  for key, rows in observations.items() for row in rows]
    return result


class HandlingHistory:
    """One bounded local index; a file signature replaces its previous observations."""
    _lock = threading.RLock()

    def __init__(self, path):
        self.path = Path(path)

    def update(self, metadata, signature, handling, *, now=None):
        if handling is None:
            return []
        game = metadata.get('game') or 'iracing'
        car = metadata.get('car_model_key') if game == 'lmu' else metadata.get('car_path')
        track = metadata.get('track_platform_id') or metadata.get('track_path')
        if not car or not track:
            return []
        file_id = hashlib.sha256(json.dumps(signature, default=str).encode()).hexdigest()
        stamp = time.time() if now is None else now
        with self._lock:
            try:
                stored = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                stored = {'files': {}, 'corners': {}}
            files, corners = stored['files'], stored['corners']
            files[file_id] = stamp
            for values in corners.values():
                values.pop(file_id, None)
            for item in handling['corners']:
                key = json.dumps([game, car, track, metadata.get('track_config') or metadata.get('track_layout') or '',
                                  round(round(item['apex_pct'] / .005) * .005, 3)], ensure_ascii=False)
                corners.setdefault(key, {}).setdefault(file_id, []).append({**item, 'method': handling.get('method', METHOD), 'at': stamp})
            keep = set(sorted(files, key=lambda key: files[key], reverse=True)[:50])
            stored['files'] = {key: value for key, value in files.items() if key in keep}
            stored['corners'] = {key: {file: rows for file, rows in values.items() if file in keep}
                                 for key, values in corners.items() if any(file in keep for file in values)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(stored, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            os.replace(temporary, self.path)
        repeated = []
        for values in stored['corners'].values():
            current = values.get(file_id)
            if not current:
                continue
            for item in current:
                matches = {file: next((entry for entry in rows
                            if (entry['kind'], entry['phase'], entry.get('method')) ==
                               (item['kind'], item['phase'], handling.get('method', METHOD))), None)
                           for file, rows in values.items()}
                matches = [entry for entry in matches.values() if entry is not None]
                if len(matches) >= 2:
                    costs = [entry['time_cost_s'] for entry in matches if entry['time_cost_s'] is not None]
                    repeated.append({**item, 'files': len(matches),
                                     'time_cost_s': float(np.median(costs)) if costs else None})
        repeated.sort(key=lambda item: item['time_cost_s'] if item['time_cost_s'] is not None else -float('inf'), reverse=True)
        # Reference-level tendencies never push a better supported repeat out of the top three.
        return sorted(repeated, key=lambda item: (EVIDENCE.get(item['kind']) == 'reference',
                                                  item.get('time_link') != 'slower'))[:3]
