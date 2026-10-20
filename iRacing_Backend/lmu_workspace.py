"""Read-only LMU presentation adapter for the shared desktop workspace.

No application state, iRacing services, memory databases, or game lifecycle is
touched. Game validity remains unknown even when a timed lap is measurable.
"""
from __future__ import annotations

from dataclasses import asdict
import math
import re

import numpy as np
from fastapi import HTTPException

from lmu_analysis import lap_report
from lmu_comparison import compare_laps, _axis, compatibility
from lmu_corners import compare_corners
from lmu_pedal_usage import pedal_usage
from lmu_telemetry import LmuTelemetryUnavailable
from lmu_session_metrics import session_metrics
from lmu_driving_summary import driving_summary
from lmu_control_channels import control_values


UNSUPPORTED = {
    'garage61': 'LMU에서는 Garage61 기준을 지원하지 않습니다.',
    'setup_effect': 'LMU 셋업 변경의 효과는 검증하지 않았습니다.',
    'ai_coaching': 'LMU 공통 화면에서는 AI 코칭을 지원하지 않습니다.',
    'steering_angle': '조향 입력은 비율이며 휠 각도로 변환하지 않습니다.',
}


def _integer(value, label, low=0, high=1_000_000):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} 범위를 확인하세요.')
    return value


def _range(body):
    start, end = body.get('start_pct', 0.), body.get('end_pct', 1.)
    if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
            for v in (start, end)) or not 0 <= start < end <= 1):
        raise ValueError('표시 범위는 0~1 사이이며 종료가 시작보다 커야 합니다.')
    return start, end


def _eligible(segment):
    return (segment.status == 'VALIDITY_UNKNOWN' and segment.recorded_lap_time_s is not None
            and math.isfinite(segment.recorded_lap_time_s) and segment.recorded_lap_time_s > 0
            and segment.distance_m is not None
            and math.isfinite(segment.distance_m) and segment.distance_m > 0)


def _lap_row(segment, normalized=None):
    row = {**asdict(segment), 'lap_time_s': segment.recorded_lap_time_s,
           'valid': None, 'measurement_eligible': _eligible(segment),
           'complete': _eligible(segment), 'reference_status': 'VALIDITY_UNVERIFIED',
           'invalid_reason': None, 'time_use': None}
    if normalized is not None:
        row['pedal_usage'] = pedal_usage(normalized)
        row['distance_support'] = normalized.support.get('distance_m', {})
    return row


def _track_length(lap):
    axis = _axis(lap)
    return float(axis.values[-1])


def _path(path, length):
    return [{**point, 'distance_pct': point['distance_m']/length}
            for point in path.get('points', [])]


def _corner_metrics(measurement, length, input_compatibility=None):
    source = dict(measurement.get('metrics', {}))
    # Shared views derive their own differences from the role measurements.
    # Carry native stage restrictions through that contract as well as delta.
    compatible = input_compatibility or {}
    dependencies = {
        'brake_ratio': ('brake_start_m', 'brake_release_m', 'peak_brake',
                        'avg_decel_g', 'braking_distance_m'),
        'throttle_ratio': ('throttle_first_m', 'full_throttle_m'),
    }
    for channel, keys in dependencies.items():
        if compatible.get(channel) is False:
            for key in keys:
                source[key] = None
    if any(value is False for value in compatible.values()):
        source['coast_time_s'] = None
    result = {**source, 'min_speed_kph': source.get('min_speed_kmh'),
              'exit_speed_kph': source.get('exit_speed_kmh'),
              'inputs_usable': all(compatible.values()),
              'input_comparison_support': compatible}
    for old, new in (('brake_start_m', 'brake_start_pct'), ('brake_release_m', 'brake_release_pct'),
                     ('throttle_first_m', 'throttle_first_pct'), ('full_throttle_m', 'full_throttle_pct')):
        value = source.get(old)
        result[new] = value/length if value is not None else None
    return result


def _comparison(reference, target, body):
    requested = _integer(body.get('points', 301), '표시 지점', 3, 10000)
    start, end = _range(body)
    native = compare_laps(reference, target, min(requested, 501))
    length = _track_length(target)
    trace = []
    for point in native['points']:
        pct = point['distance_m']/length
        if not start <= pct <= end:
            continue
        row = {'distance_pct': pct, 'distance_m': point['distance_m'],
               'delta_s': point['delta_time_s'],
               'target_elapsed_s': point['target_elapsed_s'],
               'reference_elapsed_s': point['reference_elapsed_s']}
        for role in ('target', 'reference'):
            row.update({f'{role}_speed_kph': point.get(f'speed_kmh_{role}'),
                        f'{role}_throttle': (point[f'throttle_pct_{role}']/100
                            if f'throttle_pct_{role}' in point else None),
                        f'{role}_brake': (point[f'brake_pct_{role}']/100
                            if f'brake_pct_{role}' in point else None),
                        f'{role}_steering_ratio': (point[f'steering_pct_{role}']/100
                            if f'steering_pct_{role}' in point else None),
                        f'{role}_steering_deg': None, f'{role}_gear': None})
            lap = target if role == 'target' else reference
            row[f'{role}_gear'] = control_values(lap, [lap.start_time_s+point[f'{role}_elapsed_s']], 'gear')[0]
        trace.append(row)
    geometry = compare_corners(reference, target)
    input_compatibility = {}
    for channel in ('brake_ratio', 'throttle_ratio'):
        stages = [lap.support.get(channel, {}).get('input_stage') for lap in (reference, target)]
        input_compatibility[channel] = None not in stages and stages[0] == stages[1]
    corners = [{**corner, 'id': f'C{index}', 'source_id': corner['id'],
                'entry_pct': corner['start_m']/length,
                'apex_pct': corner['apex_m']/length, 'exit_pct': corner['end_m']/length,
                'delta_s': corner.get('delta_time_s'), 'evidence': [],
                'geometry_status': 'REFERENCE_GEOMETRY_UNVERIFIED',
                'target': _corner_metrics(corner['target'], length, input_compatibility),
                'reference': _corner_metrics(corner['reference'], length, input_compatibility)}
               for index, corner in enumerate(geometry['corners'], 1)]
    paths = {path['role']: _path(path, length) for path in native['track_overlay']['paths']}
    observation_notes = []
    distance_roles = (('내 랩', target),) if target is reference else (('내 랩', target), ('기준 랩', reference))
    for role, lap in distance_roles:
        distance_info = lap.support.get('distance_m', {})
        leading = distance_info.get('leading_unmapped_s', 0)
        if isinstance(leading, (int, float)) and math.isfinite(leading) and leading > .15:
            observation_notes.append(f'{role} 시작 {leading:.1f}초는 위치를 확인할 수 없어 거리 비교에서 제외했습니다. 페달 시간은 포함합니다.')
    return {'provider': 'LMU', 'status': 'MEASUREMENT_ONLY',
            'target_lap': target.lap_number, 'reference_lap': reference.lap_number,
            'targetTime': target.recorded_lap_time_s, 'referenceTime': reference.recorded_lap_time_s,
            'delta': native['delta_time_s'], 'target_valid': None, 'reference_valid': None,
            'reference_status': 'VALIDITY_UNVERIFIED', 'reference_mode': 'LOCAL',
            'conditions': 'NOT_COMPARED', 'compatibility': native['compatibility'],
            'trace': trace, 'track_path': paths.get('target', []),
            'reference_track_path': paths.get('reference', []),
            'track_path_status': native['track_overlay']['status'],
            'coordinate_system': 'LOCAL_TANGENT_APPROXIMATION', 'geographic_identity': 'UNVERIFIED',
            'corners': corners, 'corner_geometry': geometry.get('definition'),
            'practiceSummary': driving_summary(corners),
            'corner_status': geometry['status'], 'corner_reason': geometry.get('reason'),
            'channels': native['channel_support'], 'controlEvents': {},
            'pedal_usage': {'target': pedal_usage(target), 'reference': pedal_usage(reference)},
            'braking': {'target': lap_report(target, 3)['braking'],
                        'reference': lap_report(reference, 3)['braking']},
            'distance_sectors': native['sectors'],
            'observation_notes': observation_notes,
            'distance_support': {role: lap.support.get('distance_m', {}) for role, lap in
                                 (('target', target), ('reference', reference))},
            'warnings': ['게임의 랩 유효성과 주행 조건은 확인하지 않았습니다.', *observation_notes],
            'unsupported_features': UNSUPPORTED.copy(), 'max_display_points': 501,
            'target_label': f'관측 랩 {target.lap_number}',
            'reference_label': f'관측 기준 랩 {reference.lap_number}',
            'start_pct': start, 'end_pct': end}


class LmuWorkspace:
    def __init__(self, services, paths, identify, fingerprint):
        self.services = services
        self.paths = paths
        self.identify = identify
        self.fingerprint = fingerprint
        self.known = {identify(path): path for path in paths}
        self.snapshots = {}
        self.readers = {}
        self.laps = {}

    def reader(self, file_id):
        if not isinstance(file_id, str) or re.fullmatch('[a-f0-9]{24}', file_id) is None:
            raise ValueError('목록에서 LMU 기록 파일을 선택하세요.')
        path = self.known.get(file_id)
        if path is None:
            raise HTTPException(404, '선택한 LMU 파일을 찾을 수 없습니다.')
        if file_id not in self.readers:
            signature = self.fingerprint(path)
            if signature[2] is not None:
                raise ValueError('기록 중인 파일은 분석하지 않습니다. LMU 기록을 종료한 뒤 다시 선택하세요.')
            self.snapshots[path] = signature
            self.readers[file_id] = self.services.reader_factory(path)
        return self.readers[file_id]

    def lap(self, file_id, number):
        number = _integer(number, '랩 번호')
        if (file_id, number) not in self.laps:
            self.laps[file_id, number] = self.reader(file_id).analysis_lap(number)
        return self.laps[file_id, number]

    def unchanged(self):
        if any(signature != self.fingerprint(path) for path, signature in self.snapshots.items()):
            raise ValueError('분석 중 LMU 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 선택하세요.')

    def files(self):
        rows = []
        try:
            self.services.dependency_check()
            dependency_ready = True
        except LmuTelemetryUnavailable:
            dependency_ready = False
        for file_id, path in list(self.known.items())[:250]:
            try:
                signature = self.fingerprint(path)
                row = {'name': file_id, 'file_id': file_id, 'file_name': path.name,
                       'display_name': path.name, 'provider': 'LMU',
                       'analyzable': False, 'metadata_pending': True,
                       'status': 'WAL_PRESENT' if signature[2] is not None else 'DEPENDENCY_MISSING',
                       'valid_laps': None, 'reference_status': 'VALIDITY_UNVERIFIED'}
                if signature[2] is None and dependency_ready:
                    try:
                        summary = self.reader(file_id).summary()
                        count = sum(_eligible(lap) for lap in summary.laps)
                        row.update(summary.metadata, analyzable=bool(count), metadata_pending=False,
                                   status='MEASUREMENT_ONLY', detected_laps=len(summary.laps),
                                   complete_laps=count, measurement_laps=count,
                                   track_config=summary.metadata.get('track_layout'))
                    except ValueError:
                        row.update(status='UNAVAILABLE', reason='LMU 기록의 형식 또는 시간·거리 축을 확인하세요.')
                rows.append(row)
            except FileNotFoundError:
                continue
        return {'provider': 'LMU', 'files': rows, 'reference_status': 'VALIDITY_UNVERIFIED'}

    def request(self, route, body):
        if route == 'files':
            return self.files()
        self.services.dependency_check()
        file_id = body.get('filename')
        reader = self.reader(file_id)
        if route == 'references':
            summary = reader.summary()
            eligible = [lap for lap in summary.laps if _eligible(lap)]
            personal = None
            if eligible and (body.get('target_lap') is not None or body.get('reference_selection') == 'AUTO'):
                from lmu_personal_reference import select_reference
                personal = select_reference(self, file_id, body.get('target_lap') or eligible[-1].lap_number)
            return {'provider': 'LMU', 'files': [], 'status': 'UNSUPPORTED',
                    'reason': UNSUPPORTED['garage61'], 'personalReference': personal}
        if body.get('reference_mode', 'LOCAL') != 'LOCAL' or body.get('external_reference_files'):
            raise ValueError(UNSUPPORTED['garage61'])
        summary = reader.summary()
        segments = list(summary.laps)
        eligible = [segment for segment in segments if _eligible(segment)]
        if route == 'file-laps':
            return {'provider': 'LMU', 'filename': file_id,
                    'laps': [_lap_row(s, self.lap(file_id, s.lap_number) if _eligible(s) else None) for s in segments]}
        if not eligible:
            raise ValueError('시간과 거리 진단을 통과한 측정 가능 랩이 없습니다.')
        if route == 'overlay':
            return self.overlay(file_id, body)
        target_number = body.get('target_lap')
        if target_number is None:
            target_number = eligible[-1].lap_number
        personal = None
        if body.get('reference_selection') == 'AUTO':
            from lmu_personal_reference import select_reference
            personal = select_reference(self, file_id, target_number)
            if personal['status'] != 'READY':
                raise ValueError('조건이 비슷한 측정 가능 개인 기준 랩이 없습니다.')
            body = {**body, 'reference_filename': personal['filename'], 'reference_lap': personal['lap']}
        reference_file_id = body.get('reference_filename') or file_id
        reference_segments = (segments if reference_file_id == file_id else
                              list(self.reader(reference_file_id).summary().laps))
        reference_eligible = [segment for segment in reference_segments if _eligible(segment)]
        if not reference_eligible:
            raise ValueError('기준 파일에 시간과 거리 진단을 통과한 측정 가능 랩이 없습니다.')
        target = self.lap(file_id, target_number)
        reference_number = body.get('reference_lap')
        if reference_number is None:
            previous = [s for s in reference_eligible
                        if reference_file_id != file_id or s.lap_number != target_number]
            reference_number = (previous[-1] if previous else reference_eligible[-1]).lap_number
        reference = self.lap(reference_file_id, reference_number)
        comparison = _comparison(reference, target, body)
        names_store = getattr(self.services, 'corner_names_store', None)
        if route == 'corner-names':
            from lmu_corner_names import corner_names_payload
            if names_store is None:
                raise ValueError('LMU 코너 번호 저장소를 확인할 수 없습니다.')
            if 'corners' in body:
                self.unchanged()
                names_store.confirm(reference.metadata, comparison['corners'], body['corners'])
            return corner_names_payload(reference, comparison['corners'], names_store)
        if names_store is not None:
            named = names_store.names_for(comparison['corners'], reference.metadata)
            for corner in comparison['corners']:
                saved = named.get(corner['id'], {})
                corner.update(official_turn_number=saved.get('number'),
                              turn_name=saved.get('name'), spoken=saved.get('spoken'))
                if saved.get('number') or saved.get('name'):
                    corner['label'] = ' · '.join(part for part in
                        (f"{saved['number']}번 코너" if saved.get('number') else '', saved.get('name')) if part)
            comparison['practiceSummary'] = driving_summary(comparison['corners'])
            comparison['corner_names_confirmed'] = bool(named)
        # Analysis and live coaching must use the very same observed windows.
        # Register only after checking that the source did not change, and use
        # the reference's exact vehicle/track/layout rather than the live slot.
        if comparison['corners']:
            import lmu_corner_registry
            self.unchanged()
            lmu_corner_registry.register(reference.metadata, comparison['corners'],
                                         names_store.names_for(comparison['corners'], reference.metadata)
                                         if names_store is not None else None)
        comparison.update(target_filename=file_id, reference_filename=reference_file_id,
                          reference_file_name=self.known[reference_file_id].name)
        if personal is not None:
            comparison.update(personalReference=personal, reference_selection='AUTO',
                              conditions=personal['conditions']['status'])
        if route == 'comparison':
            return comparison
        length = _track_length(target)
        rows = [_lap_row(s, self.lap(file_id, s.lap_number) if _eligible(s) else None) for s in segments]
        session = {**summary.metadata, 'track_config': summary.metadata.get('track_layout', ''),
                   'track_length_m': length, 'track_platform_id': None, 'provider': 'LMU',
                   'reference_status': 'VALIDITY_UNVERIFIED', 'conditions': comparison['conditions']}
        analysis = {'provider': 'LMU', 'session': session, 'laps': rows,
                    'comparison': {key: comparison[key] for key in ('target_lap', 'reference_lap')},
                    'reference_status': 'VALIDITY_UNVERIFIED', 'conditions': comparison['conditions'],
                    'unsupported_features': UNSUPPORTED.copy()}
        reference_rows = (rows if reference_file_id == file_id else
                          [_lap_row(s) for s in reference_segments])
        statistics, sectors = session_metrics([self.lap(file_id, s.lap_number) for s in eligible])
        return {'provider': 'LMU', 'kind': 'LIVE_LOCAL', 'filename': file_id,
                'lap_statistics': statistics, 'sector_summary': sectors,
                'reference_filename': reference_file_id,
                'defaultReferenceFile': reference_file_id,
                'personalReference': personal,
                'reference_selection': 'AUTO' if personal else 'MANUAL',
                'reference_file_name': self.known[reference_file_id].name,
                'reference_laps': reference_rows,
                'file_name': self.known[file_id].name, 'defaultTarget': target_number,
                'defaultReference': reference_number, 'session': session, 'laps': rows,
                'corners': comparison['corners'], 'initialComparison': comparison,
                'comparisonSummary': analysis['comparison'], 'consistency': {}, 'external': [],
                'analysisResult': analysis, 'quality': {'status': 'MEASUREMENT_ONLY',
                    'validity': 'UNVERIFIED', 'conditions': comparison['conditions']},
                'priorities': [], 'unsupported_features': UNSUPPORTED.copy()}

    def overlay(self, file_id, body):
        numbers = body.get('laps', [])
        extra = body.get('extra', [])
        if (not isinstance(numbers, list) or len(numbers) > 8 or
                not isinstance(extra, list) or len(extra) > 4):
            raise ValueError('같은 파일은 최대 8랩, 다른 파일은 최대 4랩을 선택하세요.')
        checked = [_integer(n, '랩 번호') for n in numbers]
        entries = [(str(n), file_id, n, 'OWN') for n in dict.fromkeys(checked)]
        for item in extra:
            if not isinstance(item, dict) or item.get('source') not in ('LMU', 'IBT'):
                raise ValueError('LMU 기록 파일의 측정 가능 랩만 겹쳐 볼 수 있습니다.')
            other, number = item.get('filename'), _integer(item.get('lap'), '랩 번호')
            entries.append((f'ibt:{other}#{number}', other, number, 'LMU'))
        if not entries:
            raise ValueError('겹쳐 볼 측정 가능 랩을 선택하세요.')
        if len({entry[0] for entry in entries}) != len(entries):
            raise ValueError('같은 랩을 중복 선택할 수 없습니다.')
        normalized = [self.lap(source, number) for _, source, number, _ in entries]
        axes = [_axis(lap) for lap in normalized]
        for lap in normalized[1:]:
            compatibility(normalized[0], lap)
        lengths = [axis.values[-1]-axis.values[0] for axis in axes]
        if max(lengths)-min(lengths) > max(50, .01*max(lengths)):
            raise ValueError('선택한 랩의 거리 범위가 달라 겹치지 않았습니다.')
        base_key = body.get('base_key') or (str(body['base_lap']) if body.get('base_lap') is not None else entries[0][0])
        base = next((i for i, entry in enumerate(entries) if entry[0] == base_key), 0)
        start, end = _range(body)
        points = min(501, _integer(body.get('points', 501), '표시 지점', 3, 10000))
        length = _track_length(normalized[base])
        low = max(start*length, *(axis.values[0] for axis in axes))
        high = min(end*length, *(axis.values[-1] for axis in axes))
        if low >= high:
            raise ValueError('공통으로 관측한 표시 거리 구간이 없습니다.')
        grid = np.linspace(low, high, points)
        elapsed = [np.interp(grid, axis.values, axis.time_s)-lap.start_time_s
                   for lap, axis in zip(normalized, axes)]
        series, channels = {}, [{'key': 'delta', 'label': '시간 차이', 'unit': 's'}]
        support = {}
        for input_key in ('throttle_ratio', 'brake_ratio', 'steering_ratio'):
            stages = [lap.support.get(input_key, {}).get('input_stage') for lap in normalized]
            support[input_key] = ({'status': 'AVAILABLE', 'input_stage': stages[0]}
                                  if stages[0] in ('UNFILTERED', 'GAME_PROCESSED') and len(set(stages)) == 1
                                  else {'status': 'UNAVAILABLE', 'reason': '입력 처리 단계가 미확인이거나 서로 다릅니다.'})
        present = set()
        for index, ((key, source, number, origin), lap, axis) in enumerate(zip(entries, normalized, axes)):
            values = {'delta': (elapsed[index]-elapsed[base]).tolist()}
            query = np.interp(grid, axis.values, axis.time_s)
            for source_key, key_name, scale in (('speed_mps', 'speed', 3.6),
                    ('throttle_ratio', 'throttle', 100), ('brake_ratio', 'brake', 100),
                    ('steering_ratio', 'steering', 100), ('gear', 'gear', 1)):
                signal = lap.channels.get(source_key)
                if source_key in support and support[source_key]['status'] != 'AVAILABLE':
                    continue
                if signal is not None:
                    samples = signal.at(query)*scale
                    values[key_name] = [float(v) if np.isfinite(v) else None for v in samples]
                    present.add(key_name)
            series[key] = values
        channels.extend({'key': key, 'label': label, 'unit': unit} for key, label, unit in
                        (('speed', '속도', 'km/h'), ('throttle', '스로틀', '%'), ('brake', '브레이크', '%'),
                         ('steering', '조향 입력', '%'), ('gear', '기어', 'gear')) if key in present)
        return {'provider': 'LMU', 'base_key': entries[base][0], 'base_lap': entries[base][2],
                'distance_pct': (grid/length).tolist(), 'track_length_m': length,
                'series': series, 'channels': channels, 'skipped': [], 'start_pct': start, 'end_pct': end,
                'channel_support': support,
                'reference_status': 'VALIDITY_UNVERIFIED', 'conditions': 'NOT_COMPARED',
                'laps': [{'key': key, 'lap': number, 'source': origin, 'file': source,
                          'label': f'관측 랩 {number}', 'valid': None,
                          'measurement_eligible': True, 'lap_time_s': lap.recorded_lap_time_s}
                         for (key, source, number, origin), lap in zip(entries, normalized)]}


def workspace_payload(services, paths, identify, fingerprint, route, body):
    adapter = LmuWorkspace(services, paths, identify, fingerprint)
    result = adapter.request(route, body)
    adapter.unchanged()
    return result
