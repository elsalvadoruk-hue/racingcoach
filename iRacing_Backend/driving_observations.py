"""The same fixed spatial windows and native-channel summaries for IBT and live.

No stored paths, coordinates, screenshots, driver identifiers or free-form notes.
Optional dynamics stay absent when unsupported. A vertical shock alone is not a kerb.
"""
from __future__ import annotations
import hashlib
import json
import math
import time
import numpy as np
from driving_learning import finite

FRAME_CHANNELS = {
    'session_time': 'SessionTime', 'distance_pct': 'LapDistPct', 'speed_mps': 'Speed',
    'brake': 'Brake', 'throttle': 'Throttle', 'steering_rad': 'SteeringWheelAngle',
    'yaw_rate': 'YawRate', 'abs_active': 'BrakeABSactive', 'vert_accel_mps2': 'VertAccel',
    'lat_accel_mps2': 'LatAccel', 'long_accel_mps2': 'LongAccel',
    'fuel_l': 'FuelLevel', 'track_temp_c': 'TrackTempCrew', 'tire_compound': 'PlayerTireCompound',
    'track_wetness': 'TrackWetness', 'brake_bias': 'dcBrakeBias', 'abs_setting': 'dcABS',
    'tc_setting': 'dcTractionControl', 'on_track': 'IsOnTrack', 'on_pit_road': 'OnPitRoad',
    'is_replay': 'IsReplayPlaying', 'incidents': 'PlayerCarMyIncidentCount',
    'session_flags': 'SessionFlags', 'car_left_right': 'CarLeftRight', 'track_surface': 'PlayerTrackSurface',
    'engine_warnings': 'EngineWarnings', 'pit_repair_left_s': 'PitRepairLeft',
    **{f'{wheel}_shock': f'{wheel}shockDefl' for wheel in ('LF', 'RF', 'LR', 'RR')},
    **{f'{wheel}_rumble': f'Tire{wheel}_RumblePitch' for wheel in ('LF', 'RF', 'LR', 'RR')},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]


def scope(identity, source):
    fields = ('game', 'car_id', 'car_path', 'track_id', 'track_path', 'track_config')
    return digest([source, *[identity.get(k) for k in fields]])


def windows(corners, length):
    """Disjoint connected groups; individual and group estimates are not additive."""
    if finite(length) is None or length <= 0:
        return []
    ordered = sorted((c for c in corners if all(finite(c.get(k)) is not None
                     for k in ('entry_pct', 'apex_pct', 'exit_pct'))
                     and 0 <= c['entry_pct'] < c['apex_pct'] < c['exit_pct'] <= 1), key=lambda c: c['entry_pct'])
    result = []

    def build(items, kind, before, after):
        first, last = items[0], items[-1]
        start = max(before, first['entry_pct']-100./length)
        end = max(last['exit_pct'], min(last['exit_pct']+250./length, after))
        members = [str(c['id']) for c in items]
        return dict(id=digest([kind, members, round(start, 6), round(end, 6)]), kind=kind,
                    members=members, start_pct=start, end_pct=end,
                    primary_entry_pct=first['entry_pct'], apex_pct=first['apex_pct'],
                    first_exit_pct=first['exit_pct'])

    for i, corner in enumerate(ordered):
        previous = ordered[i-1]['exit_pct'] if i else 0.
        after = ordered[i+1]['entry_pct']-100./length if i+1 < len(ordered) else 1.
        result.append(build([corner], 'corner', previous, after))
    i = 0
    while i < len(ordered):
        group = [ordered[i]]
        while (i+len(group) < len(ordered) and len(group) < 3
               and 0 <= (ordered[i+len(group)]['entry_pct']-group[-1]['exit_pct'])*length <= 200):
            group.append(ordered[i+len(group)])
        if len(group) > 1:
            next_i = i+len(group)
            previous = ordered[i-1]['exit_pct'] if i else 0.
            after = ordered[next_i]['entry_pct']-100./length if next_i < len(ordered) else 1.
            result.append(build(group, 'chain', previous, after))
        i += len(group)
    return result


def observation(frames, window, identity, run, lap, length, *, source='LIVE', setup=None):
    if len(frames) < 5:
        return None, 'INCOMPLETE_WINDOW'
    try:
        distance = np.array([f['distance_pct'] for f in frames], dtype=float)
        clock = np.array([f['session_time'] for f in frames], dtype=float)
        start, end = window['start_pct'], window['end_pct']
        left = max(0, int(np.searchsorted(distance, start, side='right'))-1)
        right = int(np.searchsorted(distance, end, side='left'))
        if right >= len(frames) or distance[0] > start or distance[-1] < end:
            return None, 'INCOMPLETE_WINDOW'
        frames = frames[left:right+1]
        distance, clock = distance[left:right+1], clock[left:right+1]
        if (not np.all(np.isfinite(distance)) or not np.all(np.isfinite(clock))
                or np.any(np.diff(distance) < 0) or np.any(np.diff(clock) <= 0)
                or np.max(np.diff(clock)) > .2 or clock[-1]-clock[0] > 100):
            return None, 'SAMPLE_GAP'
        if any(f.get('on_track') not in (True, 1) or f.get('on_pit_road') not in (False, 0)
               or f.get('is_replay') not in (False, 0) or f.get('track_surface') != 3
               or finite(f.get('incidents')) is None or f['incidents'] < 0 or finite(f.get('session_flags')) is None
               or int(f['session_flags']) & 0xC108 or f.get('car_left_right') != 1
               or finite(f.get('engine_warnings')) is None or f['engine_warnings'] != 0
               or finite(f.get('pit_repair_left_s')) is None or f['pit_repair_left_s'] != 0 for f in frames):
            return None, 'UNSAFE_OR_UNCONFIRMED_STATE'
        if len({f['incidents'] for f in frames}) != 1:
            return None, 'INCIDENT'
        values = {}
        for key in ('speed_mps', 'brake', 'throttle'):
            value = np.array([f.get(key, np.nan) for f in frames], dtype=float)
            if not np.all(np.isfinite(value)):
                return None, 'INPUT_MISSING'
            values[key] = value
        speed, brake, throttle = (values[k] for k in ('speed_mps', 'brake', 'throttle'))
        if np.any(speed < 3) or np.any((brake < 0) | (brake > 1) | (throttle < 0) | (throttle > 1)):
            return None, 'PAUSE_OR_INVALID_INPUT'
        at = lambda value, x: float(np.interp(x, distance, value))
        entered = window['primary_entry_pct']
        metrics = dict(time_s=at(clock, end)-at(clock, start),
                       corner_time_s=at(clock, window['first_exit_pct'])-at(clock, start),
                       entry_speed=at(speed, entered), end_speed=at(speed, end),
                       time_error_s=float(np.max(np.diff(clock)))*2,
                       peak_brake=float(np.max(brake)))
        onset = np.flatnonzero((brake[1:] > .08) & (brake[:-1] <= .08))+1
        onset = onset[distance[onset] <= window['apex_pct']]
        if len(onset):
            index = int(onset[0])
            metrics['brake_start_m'] = float((distance[index]-start)*length)
            releases = np.flatnonzero((brake[1:] <= .05) & (brake[:-1] > .05))+1
            releases = releases[(releases > index) & (distance[releases] <= window['first_exit_pct'])]
            if len(releases):
                metrics['release_m'] = float((distance[releases[-1]]-start)*length)
        active = np.flatnonzero((throttle[1:] > .2) & (throttle[:-1] <= .2))+1
        active = active[distance[active] >= window['apex_pct']]
        if len(active):
            metrics['throttle_start_m'] = float((distance[active[0]]-start)*length)
        after = distance >= window['apex_pct']
        # Count lift episodes at a fixed time grid, not each noisy telemetry tick.
        grid = np.arange(clock[0], clock[-1], .1)
        pedal = np.interp(grid, clock, throttle)
        grid_distance = np.interp(grid, clock, distance)
        lifted = False
        peak_pedal = 0.
        relifts = 0
        for p, d in zip(pedal, grid_distance):
            if d < window['apex_pct']:
                continue
            peak_pedal = max(peak_pedal, p)
            if not lifted and peak_pedal-p >= .15:
                relifts += 1
                lifted = True
            elif lifted and p >= peak_pedal-.05:
                lifted = False
        metrics['relifts'] = relifts
        for metric, channel, scale in (('abs_duty','abs_active',1.), ('yaw_rms','yaw_rate',1.),
                                       ('lat_g','lat_accel_mps2',9.80665), ('long_g','long_accel_mps2',9.80665)):
            samples = [finite(float(f[channel])) if type(f.get(channel)) is bool else finite(f.get(channel)) for f in frames]
            if all(v is not None for v in samples):
                metrics[metric] = float(np.sqrt(np.mean(np.square(samples))))/scale
        steer = np.array([f.get('steering_rad', np.nan) for f in frames], dtype=float)
        if np.all(np.isfinite(steer)):
            # Sustained reversals, not sample-by-sample vibration.
            smooth = np.interp(np.arange(clock[0], clock[-1], .15), clock, steer)
            changes = np.diff(smooth)
            significant = changes[np.abs(changes) > .025]
            metrics['corrections'] = int(np.sum(significant[1:]*significant[:-1] < 0))
        vert = np.array([f.get('vert_accel_mps2', np.nan) for f in frames], dtype=float)
        shocks = [np.array([f.get(f'{w}_shock', np.nan) for f in frames], dtype=float) for w in ('LF', 'RF', 'LR', 'RR')]
        verified = False
        if np.all(np.isfinite(vert)) and any(np.all(np.isfinite(s)) and np.ptp(s) >= .002 for s in shocks):
            # Both body response and observed suspension motion support an impact measure.
            baseline = float(np.median(vert))
            metrics['impact_score'] = float(np.percentile(np.abs(vert-baseline), 95))
            peak = int(np.argmax(np.abs(vert-baseline)))
            # The rumble signal must coincide with the measured impact, not a different kerb later.
            verified = any(abs(clock[i]-clock[peak]) <= .15 and (finite(f.get(f'{w}_rumble')) or 0) > 0
                           for i,f in enumerate(frames) for w in ('LF','RF','LR','RR'))
            quiet = np.abs(vert-baseline) < 1.
            settled = [i for i in range(peak+1,len(clock)) if clock[i] >= clock[peak]+.2
                       and clock[-1]-clock[i] >= .3 and np.all(quiet[i:np.searchsorted(clock,clock[i]+.3)+1])]
            metrics['settling_s'] = float(clock[settled[0]]-clock[peak]) if len(settled) else None
        conditions = {}
        for key, channel in (('fuel', 'fuel_l'), ('temp', 'track_temp_c'), ('compound', 'tire_compound'), ('wet', 'track_wetness')):
            samples = [finite(f.get(channel)) for f in frames]
            conditions[key] = (float(np.median(samples)) if all(v is not None for v in samples) else None)
            if key in ('compound', 'wet') and len(set(samples)) != 1:
                return None, 'CONDITION_CHANGED'
        controls = {}
        for key, channel in (('bb', 'brake_bias'), ('abs', 'abs_setting'), ('tc', 'tc_setting')):
            samples = [finite(f.get(channel)) for f in frames]
            if all(v is None for v in samples):
                continue
            if any(v is None for v in samples) or max(samples)-min(samples) > 1e-5:
                return None, 'CONTROL_CHANGED'
            controls[key] = samples[0]
        row = dict(scope=scope(identity, source), window=window['id'], kind=window['kind'], members=window['members'],
                   source=source, run=str(run), lap=int(lap), created=time.time(),
                   start_pct=start, end_pct=end, entry_pct=entered, conditions=conditions, controls=controls, setup=setup,
                   metrics=metrics, impact_verified=verified,
                   traffic='SOLO' if all(f.get('traffic_context') == 'SOLO' for f in frames) else 'UNKNOWN')
        row['id'] = digest([row['scope'], run, lap, window['id']])
        return row, None
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None, 'INVALID_SAMPLES'


def from_telemetry(analyzer, corners):
    telemetry = analyzer.telemetry
    native = telemetry.channels
    required = ('SessionTime', 'LapDistPct', 'Speed', 'Brake', 'Throttle')
    if any(key not in native for key in required):
        return [], {'INPUT_MISSING': 1}
    metadata = telemetry.metadata
    identity = dict(game=metadata.get('game', 'iracing'), car_path=metadata.get('car_path'),
                    car_id=metadata.get('car_id'), track_id=metadata.get('track_platform_id'),
                    track_path=metadata.get('track_path'), track_config=metadata.get('track_config'))
    # Absolute session identifiers are stable for copies and partial/growing files.
    weekend = (getattr(telemetry,'session_info',None) or {}).get('WeekendInfo') or {}
    session = weekend.get('SubSessionID') or weekend.get('SessionID')
    if not session:
        return [], {'SESSION_ID_MISSING': 1}
    run = digest([identity, session])
    setup = digest(telemetry.setup) if telemetry.setup else None
    definitions = windows(corners, analyzer.track_length_m)
    rows, rejected = [], {}
    for lap in telemetry.laps:
        if lap.end_idx <= lap.start_idx:
            continue
        # Keep sound parts of incident laps; every window validates its own raw safety channels.
        for definition in definitions:
            distance = native['LapDistPct'][lap.start_idx:lap.end_idx+1]
            if not len(distance) or np.any(np.diff(distance) < -.01):
                continue
            lo = max(0, int(np.searchsorted(distance, definition['start_pct'], side='right'))-1)
            hi = min(len(distance)-1, int(np.searchsorted(distance, definition['end_pct'], side='left')))
            frames = []
            for index in range(lap.start_idx+lo, lap.start_idx+hi+1):
                frame = {key: (value.item() if hasattr(value, 'item') else value)
                         for key, channel in FRAME_CHANNELS.items() if channel in native
                         for value in [native[channel][index]]}
                frames.append(frame)
            # SessionNum is needed to avoid merging practice and race lap numbers.
            session_num = native.get('SessionNum')
            segment_run = digest([run,int(session_num[lap.start_idx])]) if session_num is not None else None
            if segment_run is None:
                rejected['SESSION_ID_MISSING'] = rejected.get('SESSION_ID_MISSING',0)+1
                continue
            row, reason = observation(frames, definition, identity, segment_run, lap.lap_number,
                                      analyzer.track_length_m, source='IBT', setup=setup)
            if row:
                rows.append(row)
            elif reason:
                rejected[reason] = rejected.get(reason, 0)+1
    return rows, rejected


def analysis_result(analyzer, corners):
    """Use already parsed arrays once per geometry. No user profile I/O in analysis."""
    from driving_learning import learn
    from driving_service import public_view
    key = digest([(c.get('id'),c.get('entry_pct'),c.get('apex_pct'),c.get('exit_pct')) for c in corners])
    cache = getattr(analyzer,'_driving_learning_cache',None)
    if cache and cache[0] == key:
        return cache[1]
    if analyzer.telemetry.metadata.get('game','iracing') != 'iracing':
        result = {'state':'UNSUPPORTED','observations':0,'eligible':0,'rejections':{'CHANNELS_UNSUPPORTED':1}}
    else:
        rows, rejected = from_telemetry(analyzer,corners)
        result = dict(public_view(learn(rows)), rejections=rejected)
        result['notes'] = ['주변 차량을 확인할 수 없는 IBT 구간은 확정 추천에서 제외합니다.',
                           '연속 구간과 개별 코너의 시간 이득은 합산하지 않습니다.']
    analyzer._driving_learning_cache = (key,result)
    return result
