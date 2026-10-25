"""Select a real Garage61 lap off the SDK thread and align pedals by track position."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import threading
import tempfile
import time
from urllib import error, parse, request

import numpy as np

import garage61_api_collector as g61
from overlay_visibility import driving, preparing
from overlay_feedback import corner_feedback
from overlay_diagnostics import CornerDiagnostics
from overlay_gear import gear_display, gear_value
from overlay_timing import reference_clearance
from overlay_reference_quality import check_csv, check_lap


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def live_request(view):
    identity, sample = view.get('identity') or {}, view.get('snapshot') or {}
    wetness = finite(sample.get('track_wetness'))
    wet = None if wetness is None or not 1 <= wetness <= 7 else wetness > 1
    tire = finite(sample.get('tire_compound'))
    best = finite(sample.get('lap_best_s'))
    game = identity.get('game') or 'iracing'
    source = view.get('reference_source') or 'auto'
    return {
        'game': game, 'identity': dict(identity), 'source': source,
        'car_platform_id': str(identity.get('car_id') or ''),
        'track_platform_id': str(identity.get('track_id') or ''),
        'car_name': identity.get('car_name'), 'track_name': identity.get('track_name'),
        'track_config': identity.get('track_config'), 'track_path': identity.get('track_path'),
        'current_best_lap_s': best if best is not None and best > 0 else None,
        'pinned_reference': (view.get('practice') or {}).get('reference') if game != 'lmu' and source == 'auto' else None,
        # PlayerTireCompound indexes the SDK's per-car DriverTires list. It is
        # not a Garage61 tireCompound code, even when the numbers happen to match.
        'sdk_tire_compound': int(tire) if tire is not None and tire >= 0 and tire.is_integer() else None,
        'conditions': {'track_temp_c': finite(sample.get('track_temp_c')),
                       'air_temp_c': finite(sample.get('air_temp_c')),
                       'fuel_l': finite(sample.get('fuel_l')),
                       'tire_compound': None,  # Unknown until a verified G61 mapping is available.
                       'weather_declared_wet': wet},
    }


def matching(meta, context):
    """A similar name cannot substitute for a verified car or layout ID."""
    if any(not context.get(key) or str(meta.get(key) or '') != str(context[key])
           for key in ('car_platform_id', 'track_platform_id')):
        return False
    if meta.get('clean') is not True or any(meta.get(key) for key in
            ('offtrack', 'pitlane', 'pit_in', 'pit_out', 'incomplete', 'discontinuity', 'missing', 'joker')):
        return False
    if not (finite(meta.get('lap_time_s')) or 0) > 0:
        return False
    conditions = context['conditions']
    wet = conditions.get('weather_declared_wet')
    if type(wet) is not bool or meta.get('weather_declared_wet') is not wet:
        return False
    tire, reference_tire = conditions.get('tire_compound'), meta.get('tire_compound')
    if tire is not None and (reference_tire is None or str(tire) != str(reference_tire)):
        return False
    for key, limit in (('track_temp_c', 12), ('fuel_l', 30)):
        current, reference = finite(conditions.get(key)), finite(meta.get(key))
        if current is not None and reference is not None and abs(current-reference) > limit:
            return False
    # Old tyre models/BoP should not silently become a current driving reference.
    try:
        recorded = datetime.fromisoformat(str(meta.get('start_time')).replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc)-recorded).total_seconds()
        if not 0 <= age <= 180*86400:
            return False
    except (TypeError, ValueError):
        return False
    return True


def ranked_metadata(items, context):
    output = []
    best = context.get('current_best_lap_s')
    eligible = [meta for meta in items if matching(meta, context)]
    # Use several comparable laps so a single implausibly fast result cannot
    # reject the entire pool. These are pace gates, not a claim of equal setup.
    peer_pace = float(np.percentile([meta['lap_time_s'] for meta in eligible], 25, method='higher')) if len(eligible) >= 3 else None
    for meta in eligible:
        if ((best is not None and meta['lap_time_s'] > best*1.03)
                or (peer_pace is not None and meta['lap_time_s'] > peer_pace*1.10)):
            continue
        score, coverage, reasons = g61.condition_score(meta, context['conditions'])
        faster = best is not None and best > 0 and meta['lap_time_s'] < best
        output.append({**meta, 'similarity': score, 'coverage': coverage, 'reasons': reasons,
                       '_unknown_conditions': [name for name in ('track_temp_c', 'air_temp_c', 'fuel_l', 'tire_compound')
                                               if meta.get(name) is None or context['conditions'].get(name) is None],
                       '_rank': (round(score/0.10), faster, -meta['lap_time_s'],
                                 g61.recency_score(meta.get('start_time')))})
    return sorted(output, key=lambda item: item['_rank'], reverse=True)


class DownloadRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and parse.urlsplit(req.full_url).netloc != parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


class GridReference:
    """A pre-existing IBT or Garage61 analysis grid; no invented pedal curve."""
    def __init__(self, grid, duration):
        self.values = grid
        self.metadata = {'lap_time_s': duration}

    def grid(self, count, length):
        return self.values


class AlignedReference:
    def __init__(self, lap, length):
        self.meta = lap.metadata
        self.duration = float(self.meta['lap_time_s'])
        grid = lap.grid(min(20000, max(1000, math.ceil(length/2))), length)
        # A pinned task can share the analysis grid; circular HUD padding is private.
        self.grid = {name: values.copy() if isinstance(values, np.ndarray) else values
                     for name, values in grid.items()}
        self.distance = self.grid['distance_pct']
        self.times = self.grid['time_s']
        self._join_finish(length)

    def _join_finish(self, length):
        """Interpolate a short circular interval between observed finish/start samples."""
        indices = np.flatnonzero(np.isfinite(self.times))
        if len(indices) < 2:
            return
        left, right = int(indices[-1]), int(indices[0])
        gap_m = (1-self.distance[left]+self.distance[right])*length
        gap_s = self.duration-self.times[left]+self.times[right]
        if not 0 < gap_m <= 25 or not 0 < gap_s <= .5:
            return
        # Only the small boundary interval is supported. Internal holes stay holes.
        for key, values in self.grid.items():
            if not isinstance(values, np.ndarray) or values.ndim != 1 or len(values) != len(self.distance):
                continue
            if key == 'distance_pct' or key.startswith('_') or not np.isfinite(values[[left, right]]).all():
                continue
            end, start = float(values[left]), float(values[right])
            for index in (*range(right), *range(left+1, len(values))):
                pct = float(self.distance[index])
                x = pct+1 if index < right else pct
                fraction = (x-self.distance[left])/(1-self.distance[left]+self.distance[right])
                if key == 'time_s':
                    values[index] = end+fraction*gap_s-(self.duration if index < right else 0)
                elif key in ('Gear', 'BrakeABSactive'):
                    values[index] = end
                else:
                    values[index] = end+fraction*(start-end)

    def at(self, pct):
        if finite(pct) is None or not 0 <= pct <= 1:
            return None
        index = int(np.searchsorted(self.distance, pct))
        if index < len(self.distance) and pct == self.distance[index]:
            return finite(self.times[index].item())
        if index == 0 or index >= len(self.distance):
            return None
        left, right = index-1, index
        if not np.isfinite(self.times[[left, right]]).all():
            return None
        return float(np.interp(pct, self.distance[left:right+1], self.times[left:right+1]))

    def gear_at(self, pct):
        """Hold the observed gear; never blend adjacent gears at a shift."""
        gears = self.grid.get('Gear')
        if gears is None or finite(pct) is None or not self.distance[0] <= pct <= self.distance[-1]:
            return None
        index = int(np.searchsorted(self.distance, pct, side='right'))-1
        if (not np.isfinite(self.times[index])
                or (pct != self.distance[index] and self.at(pct) is None)):
            return None
        return gear_value(float(gears[index]))

    def window(self, view):
        options = view.get('graph_options') or {}
        window = options.get('window', 10)
        future = 2 if options.get('future', True) else 0
        sample = view.get('snapshot') or {}
        anchor, lap = self.at(sample.get('distance_pct')), finite(sample.get('lap_number'))
        if anchor is None or lap is None:
            return None
        reference = []
        for shift in (-self.duration, 0, self.duration):
            offsets = self.times+shift-anchor
            indices = np.flatnonzero(np.isfinite(offsets) & (offsets >= -window) & (offsets <= future))
            previous = None
            for index in indices:
                row = {'offset': float(offsets[index]), 'break': bool(previous is None or index-previous > 1)}
                for channel, name in (('brake', 'Brake'), ('throttle', 'Throttle')):
                    row[channel] = finite(self.grid[name][index].item())
                angle = self.grid.get('SteeringWheelAngle')
                value = finite(angle[index].item()) if angle is not None else None
                row['steering_deg'] = abs(math.degrees(value)) if value is not None else None
                reference.append(row)
                previous = index
        current, newer = [], None
        anchor_pct = finite(sample.get('distance_pct'))
        cycle = 0
        history = view.get('_history')
        for point in reversed(history if history is not None else view.get('points') or []):
            if history is not None and (point.get('on_track') is not True or point.get('is_replay') is not False):
                break
            if point.get('on_pit_road') is not False:
                break
            at, point_lap = finite(point.get('session_time')), finite(point.get('lap_number'))
            position = self.at(point.get('distance_pct'))
            if at is None or point_lap is None or position is None:
                break
            pct = finite(point.get('distance_pct'))
            newer_pct = newer[2] if newer else anchor_pct
            newer_lap = newer[3] if newer else lap
            if not 0 <= newer_lap-point_lap <= 1:
                break
            if pct > newer_pct:
                if pct > .85 and newer_pct < .15:
                    cycle -= 1
                else:
                    break
            elif newer_lap != point_lap and not (pct > .85 or newer_pct < .15):
                break
            # Lap and LapDistPct can cross the line on different SDK ticks.
            # Position determines the cycle; the lap counter only guards resets.
            absolute = position+cycle*self.duration
            offset = absolute-anchor
            if newer:
                if absolute > newer[1]:
                    # Do not carry inputs from before an Active Reset backwards jump.
                    break
                if not 0 < newer[0]-at <= .5 or not 0 <= newer[1]-absolute <= .5:
                    if current:
                        current[-1]['break'] = True
                    # A collection gap may include a reset. Older private history
                    # must not reconnect to the new driving sequence.
                    if history is not None and not 0 < newer[0]-at <= .5:
                        break
            if offset < -window:
                break
            if -window <= offset <= .02:
                current.append({'offset': min(0, offset), 'brake': point.get('brake'),
                                'throttle': point.get('throttle'), 'break': False,
                                'steering_deg': abs(math.degrees(point['steering_rad'])) if finite(point.get('steering_rad')) is not None else None})
            newer = at, absolute, pct, point_lap
        if current:
            current[-1]['break'] = True
        return {'reference': reference, 'current': current[::-1]}


class OverlayReference:
    """One latest request, cancellable HTTP waits, no disk/network access in observe()."""
    def __init__(self, reference_dir, loader, clock=time.monotonic, fetch=None, pinned_loader=None, personal_loader=None):
        self.directory = Path(reference_dir)
        self.loader, self.clock = loader, clock
        self.pinned_loader = pinned_loader
        self.personal_loader = personal_loader
        self.fetch = fetch or self._fetch
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.worker = None
        self.key = self.hard_key = None
        self.last_lap = None
        self.pending = None
        self.reference = None
        self.diagnostics = CornerDiagnostics()
        self.retry_at = 0
        self.network_retry_at = 0
        self.state = {'state': 'IDLE', 'message': '주행 대기'}

    def status(self):
        with self.condition:
            return dict(self.state)

    def observe(self, view, enabled):
        sample = view.get('snapshot') or {}
        context = live_request(view)
        conditions = context['conditions']
        pinned = context.get('pinned_reference')
        hard_key = (view.get('run_id'), context['game'], context['source'], context['car_platform_id'], context['track_platform_id'],
                    conditions['weather_declared_wet'], context['sdk_tire_compound'], json.dumps(pinned, sort_keys=True))
        def bucket(name, size):
            value = conditions.get(name)
            return None if value is None else math.floor(value/size)
        key = (*hard_key, bucket('fuel_l', 20), bucket('track_temp_c', 8))
        with self.condition:
            if not enabled or not preparing(view):
                self.key = self.hard_key = self.pending = self.reference = None
                self.state = {'state': 'IDLE', 'message': '주행 대기'}
                return
            if not view.get('run_id') or (not pinned and not all((context['car_platform_id'], context['track_platform_id']))):
                self.key = self.hard_key = self.pending = self.reference = None
                self.state = {'state': 'UNAVAILABLE', 'message': '차량·트랙 확인 필요'}
                return
            if not pinned and conditions['weather_declared_wet'] is None and self.personal_loader is None:
                self.key = self.hard_key = self.pending = self.reference = None
                self.state = {'state': 'UNAVAILABLE', 'message': '노면 상태 확인 필요'}
                return
            boundary = sample.get('lap_number') != self.last_lap
            self.last_lap = sample.get('lap_number')
            changed = hard_key != self.hard_key or (boundary and key != self.key)
            suitable = True
            if self.reference is not None and self.state.get('source') == 'PERSONAL' and not pinned:
                from overlay_personal import compatible
                suitable = compatible(context, self.reference.meta)
                changed = changed or not suitable
            # Keep a suitable reference throughout a stint. A small fuel/temperature
            # bucket change must not clear or replace the graph at every lap boundary.
            if (self.reference is not None and hard_key == self.hard_key and driving(view)
                    and sample.get('on_pit_road') is False and (pinned or self.state.get('source') == 'PERSONAL' and suitable or matching(self.reference.meta, context))):
                return
            if not changed and (self.reference is not None or self.clock() < self.retry_at):
                return
            if self.pending is not None and key == self.key:
                return
            self.key, self.hard_key = key, hard_key
            self.reference = None
            self.state = {'state': 'LOADING', 'message': '과제 기준 읽는 중' if pinned else '비교 기록 찾는 중'}
            self.retry_at = max(self.clock()+300, self.network_retry_at)
            self.pending = key, context, finite(view.get('track_length_m'))
            if self.worker is None:
                self.worker = threading.Thread(target=self._work, name='overlay-reference', daemon=True)
                self.worker.start()
            self.condition.notify_all()

    def enrich(self, view):
        with self.condition:
            reference, status = self.reference, dict(self.state)
        active = driving(view) and (view.get('snapshot') or {}).get('on_pit_road') is False
        if driving(view) and (view.get('snapshot') or {}).get('on_pit_road') is True:
            status = {**status, 'state': 'PIT', 'message': '피트로드 · 기준 준비 중' if reference is None else '피트로드 · 기준 준비됨'}
        return {**view, 'reference_status': status,
                'diagnostics': self.diagnostics.build(reference, view),
                'card_window': reference_clearance(reference, view) if reference and active else None,
                'comparison': reference.window(view) if reference and active else None,
                'feedback': corner_feedback(reference, view) if reference and active else None,
                'gear_comparison': gear_display((view.get('snapshot') or {}).get('gear') if driving(view) else None,
                                               reference.gear_at((view.get('snapshot') or {}).get('distance_pct')) if reference and active else None)}

    def _active(self, key):
        return not self.stopped.is_set() and key == self.key

    def _fetch(self, path, token, query=None):
        url = f"{g61.API_BASE}/{path.lstrip('/')}"
        if query:
            url += '?'+parse.urlencode(query)
        req = request.Request(url, headers={'Authorization': f'Bearer {token}', 'User-Agent': g61.USER_AGENT})
        for attempt in range(3):
            if self.stopped.is_set():
                raise RuntimeError('stopped')
            try:
                with request.build_opener(DownloadRedirect()).open(req, timeout=15) as response:
                    return response.read()
            except error.HTTPError as failure:
                if failure.code != 429:
                    raise
                try:
                    payload = json.loads(failure.read())
                except ValueError:
                    payload = {}
                # Never retry sooner than Garage61 requests. Long waits are
                # deferred to the next selection attempt instead of blocking shutdown.
                delay = g61.retry_delay(failure.headers, payload, attempt)
                with self.condition:
                    self.network_retry_at = max(self.network_retry_at, self.clock()+delay)
                    self.retry_at = max(self.retry_at, self.clock()+delay)
                if delay > 300 or attempt == 2:
                    raise
                if self.stopped.wait(delay):
                    raise RuntimeError('stopped')
        raise RuntimeError('request failed')

    def _json(self, path, token, query=None):
        return json.loads(self.fetch(path, token, query))

    def _candidates(self, context):
        candidates = []
        for folder in (self.directory, self.directory/'live'):
            for path in folder.glob('Garage 61 - *.meta.json'):
                meta = g61.load_json(path)
                csv = path.with_suffix('').with_suffix('.csv')
                if csv.is_file():
                    candidates.append({**meta, '_path': csv})
        return ranked_metadata(candidates, context)

    def _load(self, candidate, length):
        check_csv(candidate['_path'])
        lap = self.loader(candidate['_path'])
        quality = check_lap(lap, length)
        # Preserve the actual lap's metadata. The score is for display only.
        aligned = AlignedReference(lap, length)
        unknown = candidate.get('coverage', 0) < 1 or bool(candidate.get('_unknown_conditions'))
        note = '조건 일부 미확인' if unknown else '확인된 조건으로 비교'
        if 'tire_compound' in candidate.get('_unknown_conditions', []):
            note = ('타이어 조건 미확인' if candidate['_unknown_conditions'] == ['tire_compound']
                    else '타이어 등 조건 일부 미확인')
        return aligned, {'state': 'READY', 'message': 'Garage61 기준 연결됨',
                         'lap_time_s': candidate['lap_time_s'], 'lap_id': candidate['lap_id'],
                         'track_temp_c': candidate.get('track_temp_c'), 'fuel_l': candidate.get('fuel_l'),
                         'similarity': candidate['similarity'], 'coverage': candidate['coverage'],
                         'condition_note': note, 'quality': quality}

    def _prepare_garage(self, key, context, length):
        if length is None or length <= 0:
            return None, {'state': 'UNAVAILABLE', 'message': '트랙 길이 확인 필요'}
        pinned = context.get('pinned_reference')
        if pinned:
            if self.pinned_loader is None:
                return None, {'state': 'TASK_REFERENCE_MISSING', 'message': '과제 기준을 읽을 수 없습니다.'}
            try:
                lap = self.pinned_loader(pinned)
                aligned = AlignedReference(lap, length)
                source = pinned.get('source')
                return aligned, {'state': 'READY', 'source': source, 'label': pinned.get('label') or '과제 기준',
                                 'message': '오늘의 한 가지와 같은 기준 연결됨', 'lap_time_s': aligned.duration,
                                 'condition_note': '과제 분석과 동일한 기준 · 페이스 개선은 별도 검증'}
            except Exception:
                return None, {'state': 'TASK_REFERENCE_MISSING',
                              'message': '과제 기준 정보·파일 확인 필요 · 과제를 정한 파일을 다시 분석하세요.'}
        for candidate in self._candidates(context):
            if not self._active(key):
                return None, {}
            try:
                return self._load(candidate, length)
            except (OSError, ValueError, KeyError):
                continue
        token, _ = g61._token_from_sources(self.directory, None)
        if not token:
            return None, {'state': 'TOKEN_REQUIRED', 'message': '설정에서 Garage61 토큰을 등록하세요'}
        if os.getenv('GARAGE61_AUTO_COLLECT', '1') == '0':
            return None, {'state': 'DISABLED', 'message': 'Garage61 자동 수집 꺼짐'}
        if self.clock() < self.network_retry_at:
            return None, {'state': 'RATE_LIMIT', 'message': 'Garage61 요청 제한 · 대기 중'}
        cache = (g61.load_json(self.directory/'garage61_api_content_cache.json') or
                 g61.load_json(self.directory/'live/content-cache.json'))
        cars = cache.get('cars') or self._json('cars', token).get('items', [])
        if not self._active(key):
            return None, {}
        tracks = cache.get('tracks') or self._json('tracks', token).get('items', [])
        if not cache and self._active(key):
            g61.atomic_json_write(self.directory/'live/content-cache.json', {'cars': cars, 'tracks': tracks})
        car = next((item for item in cars if str(item.get('platform_id')) == context['car_platform_id']), None)
        track = next((item for item in tracks if str(item.get('platform_id')) == context['track_platform_id']), None)
        if not car or not track:
            return None, {'state': 'UNAVAILABLE', 'message': 'Garage61 차량·레이아웃 매칭 없음'}
        merged = {}
        # Prefer tight matching; broaden conditions only when no usable lap exists.
        for _, query in g61.query_passes(context, track['id'], car['id'])[:3]:
            if not self._active(key):
                return None, {}
            for lap in self._json('laps', token, query).get('items', []):
                if lap.get('canViewTelemetry'):
                    meta = g61.api_lap_metadata(lap)
                    merged[meta['lap_id']] = meta
            candidates = ranked_metadata(merged.values(), context)
            if candidates:
                break
        for candidate in candidates[:4]:
            if not self._active(key):
                return None, {}
            lap_id = str(candidate['lap_id'])
            if not lap_id or len(lap_id) > 128 or any(not (char.isascii() and (char.isalnum() or char in '-_')) for char in lap_id):
                continue
            try:
                raw = self.fetch(f'laps/{lap_id}/csv', token)
                g61.validate_csv(raw)
                if not self._active(key):
                    return None, {}
                folder = self.directory/'live'
                folder.mkdir(parents=True, exist_ok=True)
                path = folder/g61.filename_for(candidate)
                # A failed admission check must not publish a reference into the cache.
                with tempfile.TemporaryDirectory(prefix='.checking-', dir=folder) as temporary:
                    staged = Path(temporary)/path.name
                    staged.write_bytes(raw)
                    g61.sidecar_write(staged, candidate)
                    result = self._load({**candidate, '_path': staged}, length)
                    if not self._active(key):
                        return None, {}
                    staged.replace(path)
                    staged.with_suffix('.meta.json').replace(path.with_suffix('.meta.json'))
                return result
            except error.HTTPError as failure:
                if failure.code in (401, 403, 429):
                    raise
            except (OSError, ValueError, KeyError):
                continue
        return None, {'state': 'NO_MATCH', 'message': '비슷한 조건의 공개 기준 랩 없음'}

    def _prepare(self, key, context, length):
        if not self.personal_loader:
            return self._prepare_garage(key, context, length)
        if length is None or length <= 0:
            return None, {'state': 'UNAVAILABLE', 'message': '트랙 길이 확인 필요'}
        if context.get('game') == 'lmu' or context.get('source', 'auto') != 'auto':
            context = {**context, 'pinned_reference': None}
        pinned = context.get('pinned_reference')
        if pinned:
            return self._prepare_garage(key, context, length)
        personal_only = context.get('game') == 'lmu' or context.get('source') == 'personal'
        if not personal_only:
            try:
                result = self._prepare_garage(key, context, length)
                if result[0] is not None or context.get('source') == 'garage61':
                    return result
            except (error.HTTPError, OSError, ValueError, KeyError):
                if context.get('source') == 'garage61':
                    raise
        result = self.personal_loader(context, length, lambda: self._active(key))
        return result or (None, {'state': 'NO_MATCH', 'source': 'PERSONAL',
                                 'message': '조건에 맞는 내 기록 없음 · 내 입력만 표시합니다.'})

    def _work(self):
        while not self.stopped.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None or self.stopped.is_set())
                if self.stopped.is_set():
                    return
                key, context, length = self.pending
                self.pending = None
            try:
                reference, status = self._prepare(key, context, length)
            except error.HTTPError as failure:
                state, message = ('AUTH_REQUIRED', 'Garage61 토큰·데이터 권한 확인 필요') if failure.code in (401, 403) else (
                    ('RATE_LIMIT', 'Garage61 요청 제한 · 대기 중') if failure.code == 429 else
                    ('ERROR', 'Garage61 연결 확인 필요'))
                reference, status = None, {'state': state, 'message': message}
            except Exception:
                reference, status = None, {'state': 'ERROR', 'message': '비교 기록 확인 필요'}
            with self.condition:
                if self._active(key):
                    self.reference, self.state = reference, status

    def close(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        if self.worker:
            self.worker.join(timeout=1)
