"""HTTP analysis endpoints with explicit runtime dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
import logging
from fastapi import Body
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import StreamingResponse
import asyncio
import json
from analysis_models import AnalyzeRequest, ComparisonDetailRequest, LapOverlayRequest

logger = logging.getLogger("iRacingSetupTuner")


@dataclass(frozen=True)
class AnalysisRoutesServices:
    get_app_version: Callable[..., Any]
    get_external_reference_dir: Callable[..., Any]
    get_external_reference_max_selection: Callable[..., Any]
    get_external_reference_min_decision: Callable[..., Any]
    get_file_scan_limit: Callable[..., Any]
    get_min_display_laps: Callable[..., Any]
    get_min_display_valid_laps: Callable[..., Any]
    get_telemetry_dirs: Callable[..., Any]
    get_telemetry_analyzer: Callable[..., Any]
    get_telemetry_file_busy: Callable[..., Any]
    _external_reference_paths: Callable[..., Any]
    get_ai_client: Callable[..., Any]
    ai_runtime_info: Callable[..., Any]
    comparison_detail_payload: Callable[..., Any]
    file_laps_payload: Callable[..., Any]
    file_signature: Callable[..., Any]
    get_status: Callable[..., Any]
    json_safe: Callable[..., Any]
    lap_overlay_payload: Callable[..., Any]
    list_ibt_files: Callable[..., Any]
    load_analysis: Callable[..., Any]
    memory_runtime_info: Callable[..., Any]
    get_memory_store: Callable[..., Any]
    quick_file_visibility: Callable[..., Any]
    get_repository: Callable[..., Any]
    resolve_telemetry_file: Callable[..., Any]
    shared_state_summary: Callable[..., Any]
    telemetry_file_visibility: Callable[..., Any]


def register_routes(app, services: AnalysisRoutesServices):
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": services.get_app_version(),
            "ai_enabled": services.get_ai_client() is not None,
            "ai": services.ai_runtime_info(),
            "memory": services.memory_runtime_info(),
            "telemetry_dirs": [str(path) for path in services.get_telemetry_dirs()],
            "file_visibility": {
                "minimum_detected_laps": services.get_min_display_laps(),
                "minimum_valid_laps": services.get_min_display_valid_laps(),
                "scan_limit": services.get_file_scan_limit(),
            },
            "external_references": {
                "directory": str(services.get_external_reference_dir()),
                "csv_files": len(services._external_reference_paths()),
                "max_selection": services.get_external_reference_max_selection(),
                "minimum_for_setup_conclusion": services.get_external_reference_min_decision(),
                "aggregation": "DISTANCE_NORMALIZED_MEDIAN_CONSENSUS",
            },
        }

    @app.get("/api/files")
    async def get_files(limit: int = Query(default=20, ge=1, le=100), fast: bool = Query(default=True)) -> dict[str, Any]:
        files = await asyncio.to_thread(services.list_ibt_files, limit)
        memory_index = (
            await asyncio.to_thread(services.get_memory_store().file_analysis_index)
            if services.get_memory_store() is not None
            else {}
        )
        payload_files: list[dict[str, Any]] = []
        for path in files:
            visibility = await asyncio.to_thread(services.quick_file_visibility if fast else services.telemetry_file_visibility, path)
            payload_files.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "modified_at": path.stat().st_mtime,
                    **{key: visibility.get(key) for key in ("eligible", "analyzable", "comparison_ready", "coaching_ready", "clean_segment_count", "metadata_pending", "status", "reason", "error", "retryable")},
                    "lap_count": visibility.get("detected_laps", 0),
                    "best_lap_s": visibility.get("best_lap_s"),
                    "recording_time": visibility.get("recording_time"),
                    "complete_laps": visibility.get("complete_laps", 0),
                    "valid_laps": visibility.get("valid_laps", 0),
                    "car_name": visibility.get("car_name") or "Unknown Car",
                    "car_path": visibility.get("car_path") or "",
                    "track_name": visibility.get("track_name") or "Unknown Track",
                    "track_path": visibility.get("track_path") or "",
                    "track_config": visibility.get("track_config") or "",
                    "session_type": visibility.get("session_type"),
                    "session_duration_s": visibility.get("session_duration_s"),
                    "suggested_session_mode": visibility.get("suggested_session_mode"),
                    "memory": memory_index.get(
                        path.name,
                        {
                            "analyzed": False,
                            "analysis_count": 0,
                            "session_modes": [],
                        },
                    ),
                }
            )
        return {
            "files": payload_files,
            "memory_enabled": services.get_memory_store() is not None,
            "minimum_display_laps": services.get_min_display_laps(),
            "minimum_display_valid_laps": services.get_min_display_valid_laps(),
            "filter_policy": "SHOW_ALL_WITH_AVAILABILITY_REASON",
        }

    @app.get("/api/status")
    async def get_status(fast: bool = Query(default=True)) -> dict[str, Any]:
        if fast:
            files = await asyncio.to_thread(services.list_ibt_files, 1)
            path = files[0] if files else None
            visibility = await asyncio.to_thread(services.quick_file_visibility, path) if path else None
            return {
                "telemetry": {"file_name": path.name, "file_signature": list(services.file_signature(path)),
                              **visibility} if path else None,
                "shared_state": services.shared_state_summary(),
                "ai_enabled": services.get_ai_client() is not None, "ai": services.ai_runtime_info(), "memory": services.memory_runtime_info(),
                "listing_policy": "HEADER_AND_PERSISTENT_INDEX_FIRST",
            }
        path = await asyncio.to_thread(services.resolve_telemetry_file)
        if path is None:
            return {
                "telemetry": None,
                "shared_state": services.shared_state_summary(),
                "ai_enabled": services.get_ai_client() is not None,
                "ai": services.ai_runtime_info(),
                "memory": services.memory_runtime_info(),
            }

        try:
            telemetry = await asyncio.to_thread(services.get_repository().load, path)
            analyzer = services.get_telemetry_analyzer()(telemetry)
            quality = analyzer._data_quality()
            session = analyzer._session_summary()
            laps = analyzer._lap_table()
            valid_laps = [lap for lap in laps if lap["status"] == "VALID"]
            latest_valid = valid_laps[-1] if valid_laps else None
            return {
                "telemetry": {
                    "file_name": path.name,
                    "file_signature": list(telemetry.signature),
                    "car_name": session["car_name"],
                    "track_name": session["track_name"],
                    "track_config": session["track_config"],
                    "setup_name": session["setup_name"],
                    "track_temp_c": session["track_temp_c"],
                    "records": telemetry.record_count,
                    "tick_rate_hz": telemetry.tick_rate,
                    "valid_laps": quality["valid_laps"],
                    "complete_laps": quality["complete_laps"],
                    "latest_valid_lap": latest_valid,
                    "data_quality": quality,
                },
                "shared_state": services.shared_state_summary(),
                "ai_enabled": services.get_ai_client() is not None,
                "ai": services.ai_runtime_info(),
                "memory": services.memory_runtime_info(),
            }
        except PermissionError as exc:
            raise services.get_telemetry_file_busy()(path) from exc
        except Exception as exc:
            logger.exception("Status load failed")
            raise HTTPException(status_code=500, detail=f"상태 조회 실패: {exc}") from exc

    @app.get("/api/channels")
    async def get_channels(filename: Optional[str] = None) -> dict[str, Any]:
        path = await asyncio.to_thread(services.resolve_telemetry_file, filename)
        if path is None:
            raise HTTPException(status_code=404, detail=".ibt 파일이 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        quality = services.get_telemetry_analyzer()(telemetry)._data_quality()
        return {
            "file_name": path.name,
            "data_quality": quality,
            "channels": [
                {
                    "name": header.name,
                    "type": header.var_type,
                    "count": header.count,
                    "unit": header.unit,
                    "description": header.description,
                    "loaded": header.name in telemetry.channels,
                }
                for header in sorted(telemetry.variables.values(), key=lambda item: item.offset)
            ],
        }

    @app.post("/api/analyze")
    async def analyze_telemetry(payload: AnalyzeRequest = Body(default=AnalyzeRequest())) -> dict[str, Any]:
        return await services.load_analysis(payload)

    @app.post("/api/lap-overlay")
    async def get_lap_overlay(payload: LapOverlayRequest) -> dict[str, Any]:
        path = services.resolve_telemetry_file(payload.filename)
        if path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        return await asyncio.to_thread(services.lap_overlay_payload, telemetry, payload)

    @app.get("/api/file-laps")
    async def get_file_laps(filename: str) -> dict[str, Any]:
        path = await asyncio.to_thread(services.resolve_telemetry_file, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        return services.file_laps_payload(telemetry)

    @app.post("/api/comparison-detail")
    async def get_comparison_detail(payload: ComparisonDetailRequest) -> dict[str, Any]:
        path = services.resolve_telemetry_file(payload.filename)
        if path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        return await asyncio.to_thread(services.comparison_detail_payload, telemetry, payload)

    @app.get("/api/events")
    async def telemetry_events() -> StreamingResponse:
        async def event_stream() -> Any:
            last_key = previous_key = None
            while True:
                try:
                    payload = await services.get_status(fast=True)
                except Exception as exc:
                    payload = {"error": str(exc)}
                telemetry = payload.get('telemetry') or {}
                # A sharing lock can disappear without changing the recording signature.
                key = (tuple(telemetry.get('file_signature') or []), bool(telemetry.get('retryable')))
                # A recording that is still growing changes every poll. Announce it once
                # it holds still, so the screen does not re-analyse it every two seconds.
                growing = (last_key is not None and previous_key is not None and key != previous_key
                           and key[0][:1] == previous_key[0][:1] and key[1] == previous_key[1])
                previous_key = key
                if key != last_key and not growing:
                    last_key = key
                    yield f"data: {json.dumps(services.json_safe(payload), ensure_ascii=False)}\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(2.0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return SimpleNamespace(
        health=health,
        get_files=get_files,
        get_status=get_status,
        get_channels=get_channels,
        analyze_telemetry=analyze_telemetry,
        get_lap_overlay=get_lap_overlay,
        get_file_laps=get_file_laps,
        get_comparison_detail=get_comparison_detail,
        telemetry_events=telemetry_events,
    )
