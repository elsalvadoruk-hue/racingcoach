"""Desktop workspace endpoints backed by the application's real stores and calculators.

Presentation adapters never import the Lab, start runtime services or change
environment variables. Analysis remains local, with explicit memory assignment.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal

from fastapi import Body, HTTPException
from pydantic import ValidationError

from analysis_models import AnalyzeRequest, ComparisonDetailRequest, ExperimentRequest, LapOverlayRequest
from workspace_controls import comparison_controls
from workspace_setup import setup_plan
from workspace_summary import practice_summary


@dataclass(frozen=True)
class WorkspaceRoutesServices:
    get_telemetry_analyzer: Callable[..., Any]
    get_ibt_reader: Callable[..., Any]
    get_repository: Callable[..., Any]
    get_external_reference_repository: Callable[..., Any]
    resolve_telemetry_file: Callable[..., Any]
    resolve_external_reference_file: Callable[..., Any]
    telemetry_file_visibility: Callable[..., Any]
    quick_file_visibility: Callable[..., Any]
    list_ibt_files: Callable[..., Any]
    list_external_reference_metadata: Callable[..., Any]
    comparison_detail_payload: Callable[..., Any]
    file_laps_payload: Callable[..., Any]
    lap_overlay_payload: Callable[..., Any]
    load_analysis: Callable[..., Any]
    compare_setup_experiments: Callable[..., Any]
    build_closed_loop_coach: Callable[..., Any]
    extract_setup_summary: Callable[..., Any]
    setup_exploration_candidate: Callable[..., Any]
    json_safe: Callable[..., Any]


def _filename(name):
    if not isinstance(name, str) or not name or Path(name).name != name or '/' in name or '\\' in name:
        raise HTTPException(status_code=422, detail='목록에서 주행 파일을 선택하세요.')
    return name


def _recording(services, name):
    path = services.resolve_telemetry_file(_filename(name))
    if path is None:
        raise HTTPException(status_code=404, detail='주행 파일을 찾을 수 없습니다. 목록을 새로 불러오세요.')
    telemetry = services.get_repository().load(path)
    services.telemetry_file_visibility(path, telemetry)
    return telemetry


def workspace_comparison(services, telemetry, body):
    request = ComparisonDetailRequest(**body)
    analyzer = services.get_telemetry_analyzer()(telemetry)
    target, reference, _ = analyzer._select_laps(request.target_lap, request.reference_lap)
    if target is None:
        raise ValueError('비교할 수 있는 랩이 없습니다.')
    controls_api = SimpleNamespace(IBTReader=services.get_ibt_reader())
    if request.reference_mode != 'LOCAL':
        if request.reference_mode == 'SINGLE' and len(request.external_reference_files) != 1:
            raise ValueError('단일 비교에는 Garage61 랩 한 개를 선택하세요.')
        external = analyzer._external_reference_analysis(target, request.external_reference_files)
        if external.get('status') != 'READY':
            raise ValueError('사용 가능한 Garage61 기준 랩이 없습니다.')
        aggregate = external['aggregate']
        detail = services.comparison_detail_payload(telemetry, request)
        source = getattr(analyzer, '_external_line_source', {}) or {}
        path = (services.resolve_external_reference_file(source.get('reference_file'))
                if request.reference_mode == 'SINGLE' and source.get('reference_file') else None)
        signals = comparison_controls(controls_api, telemetry, target, None,
            analyzer._lap_grid(target) if target.valid else analyzer._coaching_grid(target),
            services.get_external_reference_repository().load(path) if path is not None else None,
            aggregate=request.reference_mode == 'AGGREGATE')
        return {**detail, 'corners': external['corners'], 'targetTime': aggregate.get('target_time_s'),
                'referenceTime': aggregate.get('reference_median_time_s'), 'delta': aggregate.get('gap_to_reference_median_s'),
                'warnings': external.get('warnings', []), 'aggregate': aggregate, 'controlEvents': signals}
    # An explicitly chosen self-comparison stays a zero baseline rather than
    # silently selecting a different lap as the legacy default selector does.
    if target.valid and request.reference_lap == target.lap_number:
        reference = target
    grid = analyzer._lap_grid(target) if target.valid else analyzer._coaching_grid(target)
    base = analyzer._lap_grid(reference) if reference else None
    definitions = analyzer._detect_corners(base if base is not None else grid)
    # A clean section can expose its observed inputs without a timed reference.
    # Dropping those corners would discard coaching-only recordings entirely.
    corners = analyzer._corner_analysis(grid, base, definitions)
    visual = analyzer._visualizations(grid, base, corners)
    detail = services.comparison_detail_payload(telemetry, request)
    if reference == target:
        detail = {**detail, **visual, 'reference_track_path': visual.get('track_path', []),
                  'reference_label': f'내 기준 랩 {target.lap_number}', 'reference_lap': target.lap_number,
                  'reference_line_source': {'type': 'LOCAL_ACTUAL_LAP', 'label': f'내 기준 랩 {target.lap_number}', 'chart_basis': 'ACTUAL_LAP'},
                  'quality': {**detail['quality'], 'reference_coverage_ratio': detail['quality']['target_coverage_ratio'], 'trace_points': len(visual.get('trace', []))}}
    own_time = target.lap_time_s if target.valid else None
    base_time = reference.lap_time_s if reference and reference.valid else None
    return {**detail, 'corners': corners, 'targetTime': own_time, 'referenceTime': base_time,
            'delta': own_time - base_time if own_time is not None and base_time is not None else None,
            'controlEvents': comparison_controls(controls_api, telemetry, target, reference, grid)}


def _attach_practice(services, full, comparison, body):
    external = body.get('reference_mode', 'LOCAL') != 'LOCAL'
    if not external:
        full['performance']['corners'] = comparison['corners']
        full['performance']['top_losses'] = sorted([corner for corner in comparison['corners'] if (corner.get('delta_s') or 0) > 0],
                                                  key=lambda corner: corner['delta_s'], reverse=True)
        selected = full.get('comparison') or {}
        full['comparison'] = {**selected, 'target_lap': comparison.get('target_lap'), 'reference_lap': comparison.get('reference_lap'),
            'target_time_s': comparison.get('targetTime'), 'reference_time_s': comparison.get('referenceTime'),
            'delta_s': comparison.get('delta'), 'total_delta_s': comparison.get('delta')}
        if not comparison.get('quality', {}).get('pace_eligible', True):
            full['comparison'].update(status='COACHING_ONLY', scope='CLEAN_SEGMENTS_ONLY',
                                      target_time_s=None, reference_time_s=None, delta_s=None, total_delta_s=None)
        if body.get('target_lap') == body.get('reference_lap'):
            full['setup_recommendation'] = {}
    summary = practice_summary(services.build_closed_loop_coach, full, comparison['corners'], external)
    # The summary's displayed focus is addressable through the existing practice
    # lifecycle. Keep the original personal/Garage61 lists for older clients.
    for index, option in enumerate(summary['options']):
        option.update(practice_source='WORKSPACE', practice_focus_index=index)
    full.setdefault('practice_focuses', {})['WORKSPACE'] = [option['focus'] for option in summary['options']]
    comparison.update(practiceSummary=summary, analysisResult=full,
                      practice=full.get('practice'), practiceFocuses=full.get('practice_focuses'))
    return comparison


def register_routes(app, services: WorkspaceRoutesServices):
    # Session changes are serialized so a practice start always refers to the
    # comparison most recently completed by the desktop workspace.
    request_lock = asyncio.Lock()

    @app.post('/api/workspace/{route}')
    async def workspace_request(route: Literal['files', 'session', 'file-laps', 'references', 'overlay', 'comparison', 'setup-plan', 'setup-compare'],
                                body: dict[str, Any] = Body(default_factory=dict)):
        async with request_lock:
            try:
                if route == 'files':
                    paths = await asyncio.to_thread(services.list_ibt_files, 250)
                    fields = ('car_name', 'car_path', 'track_name', 'track_path', 'track_config', 'track_platform_id', 'analyzable', 'reason',
                              'detected_laps', 'complete_laps', 'valid_laps', 'metadata_pending', 'status', 'retryable',
                              'session_type', 'session_duration_s', 'suggested_session_mode')
                    files = []
                    for path in paths:
                        visibility = await asyncio.to_thread(services.quick_file_visibility, path)
                        files.append({'name': path.name, **{key: visibility.get(key) for key in fields}})
                    return services.json_safe({'files': files})
                if route == 'setup-compare':
                    _filename(body.get('baseline_file'))
                    _filename(body.get('candidate_file'))
                    payload = ExperimentRequest(**body)
                    response = await services.compare_setup_experiments(payload)
                    return services.json_safe({**response['comparison'], 'memory_record': response.get('memory_record')})
                telemetry = await asyncio.to_thread(_recording, services, body.get('filename'))
                if route == 'file-laps':
                    result = services.file_laps_payload(telemetry)
                elif route == 'references':
                    catalog = await asyncio.to_thread(services.list_external_reference_metadata, telemetry)
                    fields = ('name', 'driver_name', 'car_name', 'track_name', 'track_config', 'track_platform_id',
                              'lap_time_s', 'usable', 'compatible', 'condition_similarity', 'validation_error')
                    result = {'files': [{key: row.get(key) for key in fields} for row in catalog['files']]}
                elif route == 'overlay':
                    result = await asyncio.to_thread(services.lap_overlay_payload, telemetry, LapOverlayRequest(**body))
                elif route == 'setup-plan':
                    api = SimpleNamespace(TelemetryAnalyzer=services.get_telemetry_analyzer(), AnalyzeRequest=AnalyzeRequest,
                        extract_setup_summary=services.extract_setup_summary, setup_exploration_candidate=services.setup_exploration_candidate)
                    result = await asyncio.to_thread(setup_plan, api, telemetry, body)
                else:
                    selection_request = ComparisonDetailRequest(**body)
                    if selection_request.reference_mode == 'SINGLE' and len(selection_request.external_reference_files) != 1:
                        raise ValueError('단일 비교에는 Garage61 랩 한 개를 선택하세요.')
                    request = AnalyzeRequest(
                        filename=body['filename'], session_mode=body.get('session_mode') or telemetry.metadata.get('suggested_session_mode') or 'BASELINE_3LAP',
                        target_lap=body.get('target_lap'), reference_lap=body.get('reference_lap'),
                        external_reference_files=body.get('external_reference_files', []) if body.get('reference_mode', 'LOCAL') != 'LOCAL' else [],
                        include_ai=False, persist_memory=body.get('persist_memory', False),
                        replace_existing_mode=body.get('replace_existing_mode', False),
                        **{key: body[key] for key in ('user_feedback', 'feedback_phase', 'feedback_speed', 'feedback_symptom',
                            'setup_mode', 'pit_rule') if key in body})
                    # Browsing a file in an unconfirmed picker must not replace
                    # the active practice or voice context behind the visible UI.
                    full = await services.load_analysis(request, update_current_analysis=not body.get('preview', False))
                    selected = full.get('comparison') or {}
                    lap_selection = full.get('lap_selection') or {}
                    target = body.get('target_lap') if body.get('target_lap') is not None else selected.get('target_lap')
                    reference = body.get('reference_lap') if body.get('reference_lap') is not None else selected.get('reference_lap')
                    if target is None:
                        target = lap_selection.get('target_lap')
                    if reference is None:
                        reference = lap_selection.get('reference_lap')
                    clean_corners = (full.get('coaching') or {}).get('clean_segment_corners') or []
                    if target is None:
                        target = next((corner.get('lap_number') for corner in clean_corners if corner.get('lap_number') is not None), None)
                    if target is None:
                        raise ValueError('비교할 랩을 찾지 못했습니다.')
                    selection = {**body, 'target_lap': target, 'reference_lap': reference}
                    result = await asyncio.to_thread(workspace_comparison, services, telemetry, selection)
                    target = result.get('target_lap', target)
                    if body.get('reference_mode', 'LOCAL') == 'LOCAL':
                        reference = result.get('reference_lap')
                    selection.update(target_lap=target, reference_lap=reference)
                    if not result['corners']:
                        result['corners'] = [corner for corner in clean_corners if corner.get('lap_number') in (None, target)]
                    _attach_practice(services, full, result, selection)
                    if route == 'session':
                        initial = {key: value for key, value in result.items() if key not in ('analysisResult', 'practice', 'practiceFocuses')}
                        result = {'kind': 'LIVE_LOCAL', 'filename': body['filename'],
                            'defaultTarget': target, 'defaultReference': reference,
                            'session': {key: full['session'].get(key, telemetry.metadata.get(key)) for key in (
                                'car_name', 'car_path', 'track_name', 'track_path', 'track_config', 'track_length_m', 'track_platform_id',
                                'session_type', 'session_duration_s', 'session_laps_limit', 'suggested_session_mode')},
                            'laps': full['laps'], 'corners': result['corners'], 'initialComparison': initial,
                            'consistency': full['performance'].get('corner_consistency', {}),
                            'comparisonSummary': full['comparison'], 'lapSelection': full.get('lap_selection'),
                            'quality': full.get('data_quality'), 'priorities': full.get('coaching', {}).get('priorities', []),
                            'analysisResult': full, 'practice': full.get('practice'), 'practiceFocuses': full.get('practice_focuses')}
                return services.json_safe(result)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors(include_context=False, include_input=False)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SimpleNamespace(workspace_request=workspace_request)
