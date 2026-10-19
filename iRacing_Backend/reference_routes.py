"""HTTP reference endpoints with explicit runtime dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
import logging
from fastapi import HTTPException
import asyncio
from analysis_models import Garage61CollectRequest

logger = logging.getLogger("iRacingSetupTuner")


@dataclass(frozen=True)
class ReferenceRoutesServices:
    get_garage61_action_timeout_ms: Callable[..., Any]
    get_garage61_auto_collect_enabled: Callable[..., Any]
    get_garage61_collector_cooldown_min: Callable[..., Any]
    get_garage61_collector_max_wall_seconds: Callable[..., Any]
    get_garage61_navigation_timeout_ms: Callable[..., Any]
    get_garage61_retry_count: Callable[..., Any]
    get_garage61_settle_ms: Callable[..., Any]
    get_garage61_target_references: Callable[..., Any]
    get_parsed_telemetry: Callable[..., Any]
    garage61_api_connection_status: Callable[..., Any]
    get_garage61_collector: Callable[..., Any]
    garage61_collector_status_payload: Callable[..., Any]
    garage61_reference_health: Callable[..., Any]
    list_external_reference_metadata: Callable[..., Any]
    get_repository: Callable[..., Any]
    resolve_telemetry_file: Callable[..., Any]


def register_routes(app, services: ReferenceRoutesServices):
    @app.get("/api/external-references")
    async def get_external_references(filename: Optional[str] = None) -> dict[str, Any]:
        path = await asyncio.to_thread(services.resolve_telemetry_file, filename)
        telemetry: Optional[services.get_parsed_telemetry()] = None
        if filename and path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        if path is not None:
            telemetry = await asyncio.to_thread(services.get_repository().load, path)
        payload = await asyncio.to_thread(services.list_external_reference_metadata, telemetry)
        payload["status"] = "success"
        payload["context"] = (
            {
                "filename": path.name,
                "car_name": telemetry.metadata.get("car_name"),
                "track_name": telemetry.metadata.get("track_name"),
                "track_config": telemetry.metadata.get("track_config"),
            }
            if path is not None and telemetry is not None
            else None
        )
        if path is not None and telemetry is not None:
            payload["collector_health"] = await asyncio.to_thread(
                services.garage61_reference_health, telemetry, path.name, payload
            )
        return payload

    @app.get("/api/garage61/api/status")
    async def get_garage61_api_status() -> dict[str, Any]:
        return {"status": "success", "api": await asyncio.to_thread(services.garage61_api_connection_status)}

    @app.get("/api/garage61/collector/status")
    async def get_garage61_collector_status(filename: Optional[str] = None) -> dict[str, Any]:
        path = await asyncio.to_thread(services.resolve_telemetry_file, filename)
        if filename and path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        if path is None:
            return {
                "status": "success",
                "health": None,
                "job": await asyncio.to_thread(services.get_garage61_collector().active_job),
            }
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        references = await asyncio.to_thread(services.list_external_reference_metadata, telemetry)
        return await asyncio.to_thread(
            services.garage61_collector_status_payload, telemetry, path.name, references
        )

    @app.post("/api/garage61/collector/start")
    async def start_garage61_collector(payload: Garage61CollectRequest) -> dict[str, Any]:
        if not services.get_garage61_auto_collect_enabled():
            raise HTTPException(status_code=503, detail="Garage61 자동 수집이 비활성화되어 있습니다.")
        path = await asyncio.to_thread(services.resolve_telemetry_file, payload.filename)
        if path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        references = await asyncio.to_thread(services.list_external_reference_metadata, telemetry)
        health = await asyncio.to_thread(services.garage61_reference_health, telemetry, path.name, references)

        if not payload.force and health.get("enough_data") and health.get("cache_fresh"):
            return {
                "status": "success",
                "health": health,
                "job": {
                    "state": "SKIPPED_CACHE_SUFFICIENT",
                    "message": "현재 IBT 조건에 맞는 Garage61 캐시가 충분하고 최신입니다.",
                    "busy": False,
                },
            }
        age_hours = health.get("last_context_scan_age_hours")
        if (
            not payload.force
            and age_hours is not None
            and float(age_hours) * 60.0 < services.get_garage61_collector_cooldown_min()
        ):
            return {
                "status": "success",
                "health": health,
                "job": {
                    "state": "SKIPPED_COOLDOWN",
                    "message": f"동일 조건을 최근 {services.get_garage61_collector_cooldown_min():.0f}분 이내 수집하여 재수집을 생략했습니다.",
                    "busy": False,
                },
            }

        request_payload = dict(health["context"])
        request_payload.update(
            {
                "force": bool(payload.force),
                "headless": bool(payload.headless),
                "target_count": int(payload.target_count or services.get_garage61_target_references()),
                "max_wall_seconds": services.get_garage61_collector_max_wall_seconds(),
                "navigation_timeout_ms": services.get_garage61_navigation_timeout_ms(),
                "action_timeout_ms": services.get_garage61_action_timeout_ms(),
                "settle_ms": services.get_garage61_settle_ms(),
                "retry_count": services.get_garage61_retry_count(),
                "collector_mode": payload.collector_mode,
            }
        )
        job = await asyncio.to_thread(services.get_garage61_collector().start, request_payload)
        return {"status": "success", "health": health, "job": job}

    @app.post("/api/garage61/collector/cancel")
    async def cancel_garage61_collector() -> dict[str, Any]:
        job = await asyncio.to_thread(services.get_garage61_collector().cancel)
        return {"status": "success", "job": job}

    return SimpleNamespace(
        get_external_references=get_external_references,
        get_garage61_api_status=get_garage61_api_status,
        get_garage61_collector_status=get_garage61_collector_status,
        start_garage61_collector=start_garage61_collector,
        cancel_garage61_collector=cancel_garage61_collector,
    )
