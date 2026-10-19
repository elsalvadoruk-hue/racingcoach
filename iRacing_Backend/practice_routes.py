"""HTTP practice endpoints with explicit runtime dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
import logging
from fastapi import HTTPException
import asyncio
from api_route_models import DrillStartRequest, PracticeCloseRequest, PracticeStartRequest
from analysis_models import CornerNamesRequest

logger = logging.getLogger("iRacingSetupTuner")


@dataclass(frozen=True)
class PracticeRoutesServices:
    get_corner_definition_store: Callable[..., Any]
    corner_names_payload: Callable[..., Any]
    drill_fuel: Callable[..., Any]
    drill_lap_time: Callable[..., Any]
    get_live_telemetry: Callable[..., Any]
    get_practice_store: Callable[..., Any]
    refresh_live_practice: Callable[..., Any]
    get_repository: Callable[..., Any]
    resolve_telemetry_file: Callable[..., Any]
    get_shared_sync_state: Callable[..., Any]
    warmup_crossings: Callable[..., Any]


def register_routes(app, services: PracticeRoutesServices):
    @app.post("/api/practice/start")
    async def start_practice(payload: PracticeStartRequest) -> dict[str, Any]:
        """Keep one of the last analysis' ranked focuses as the driver's practice task."""
        result = services.get_shared_sync_state().get("analysis_result") or {}
        if (result.get("file") or {}).get("name") != payload.filename:
            raise HTTPException(status_code=409, detail="이 파일을 먼저 분석한 뒤 과제를 시작해 주세요.")
        focuses = ((result.get("practice_focuses") or {}).get(payload.source) if payload.source
                   else (result.get("closed_loop_coach") or {}).get("ranked_focuses")) or []
        if payload.focus_index >= len(focuses):
            raise HTTPException(status_code=404, detail="고를 수 있는 과제가 없습니다.")
        try:
            task = await asyncio.to_thread(services.get_practice_store().start, result, focuses[payload.focus_index])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["practice"] = await asyncio.to_thread(services.get_practice_store().record, result)
        services.refresh_live_practice()
        return {"task": task, "practice": result["practice"]}

    @app.post("/api/practice/{task_id}/drill")
    async def start_drill(task_id: str, payload: DrillStartRequest) -> dict[str, Any]:
        """Corner drill with Active Reset for one corner of a running practice task (live_drill). The plan comes
        from the analysis on screen; the live session measures the attempts."""
        result = services.get_shared_sync_state().get("analysis_result") or {}
        if (result.get("file") or {}).get("name") != payload.filename:
            raise HTTPException(status_code=409, detail="과제를 정한 차·트랙의 주행 파일을 먼저 분석해 주세요.")
        task = next((t for t in services.get_practice_store().data["tasks"] if t.get("id") == task_id and t.get("status") == "ACTIVE"), None)
        if task is None:
            raise HTTPException(status_code=404, detail="진행 중인 과제를 찾을 수 없습니다.")
        from practice import identity as practice_identity
        if practice_identity(result.get('session') or {}) != task.get('identity'):
            raise HTTPException(status_code=409, detail='과제와 같은 차·트랙·레이아웃의 파일을 분석해 주세요.')
        from copy import deepcopy
        from practice_live import bundle
        saved = task.get('live_bundle') or bundle(task, result)
        plan = next((deepcopy(p) for p in saved.get('plans', []) if p.get('label') == payload.corner_label), None)
        if plan is None:
            raise HTTPException(status_code=422, detail='과제의 코너 측정 정보를 찾지 못했습니다. 과제를 정한 파일을 다시 분석해 주세요.')
        if plan.get('reference_available') is False:
            raise HTTPException(status_code=422, detail='과제의 Garage61 기준을 확인할 수 없습니다. 과제를 정한 파일과 기준을 다시 분석해 주세요.')
        reference = saved.get('reference') or {}
        plan.update({"task_id": task_id, "title": task.get("title"), "track_name": task.get("track_name"),
                     'continue_after_goal': True,
                     'identity': task.get('identity'),
                     "reference_source": reference.get('source'), "reference_label": reference.get('label')})
        plan["lap_time_s"] = services.drill_lap_time(result)
        plan["warmup_crossings"] = services.warmup_crossings(plan["lap_time_s"])
        try:
            plan["fuel"] = await asyncio.to_thread(services.drill_fuel, result, payload.filename, plan["warmup_crossings"])
        except Exception:
            logger.exception("Could not suggest the drill fuel")
            plan["fuel"] = None
        with services.get_live_telemetry().lock:
            services.get_live_telemetry().practice.pause()
            return services.get_live_telemetry().drill.start(plan, warmup=payload.warmup)

    @app.post("/api/drill/skip-warmup")
    async def skip_drill_warmup() -> dict[str, Any]:
        with services.get_live_telemetry().lock:
            return services.get_live_telemetry().drill.skip_warmup()

    @app.get("/api/drill")
    async def drill_status() -> dict[str, Any]:
        with services.get_live_telemetry().lock:
            return services.get_live_telemetry().drill.view()

    @app.delete("/api/drill")
    async def stop_drill() -> dict[str, Any]:
        with services.get_live_telemetry().lock:
            result = services.get_live_telemetry().drill.stop()
            services.get_live_telemetry().practice.pause()
        await asyncio.to_thread(services.get_live_telemetry().drill.history.flush)
        return result

    @app.get('/api/practice/{task_id}/drills')
    async def practice_drills(task_id: str) -> dict[str, Any]:
        return services.get_live_telemetry().drill.history.view(task_id)

    @app.post("/api/practice/{task_id}/close")
    async def close_practice(task_id: str, payload: PracticeCloseRequest) -> dict[str, Any]:
        try:
            task = await asyncio.to_thread(services.get_practice_store().close, task_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="과제를 찾을 수 없습니다.") from exc
        with services.get_live_telemetry().lock:
            if (services.get_live_telemetry().drill.plan or {}).get('task_id') == task_id:
                services.get_live_telemetry().drill.stop()
        services.refresh_live_practice()
        await asyncio.to_thread(services.get_live_telemetry().drill.history.flush)
        return {"task": task, "history": services.get_practice_store().history(task.get('identity'))}

    @app.get("/api/corner-names")
    async def get_corner_names(filename: str) -> dict[str, Any]:
        path = await asyncio.to_thread(services.resolve_telemetry_file, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        return await asyncio.to_thread(services.corner_names_payload, telemetry)

    @app.put("/api/corner-names")
    async def put_corner_names(payload: CornerNamesRequest) -> dict[str, Any]:
        path = await asyncio.to_thread(services.resolve_telemetry_file, payload.filename)
        if path is None:
            raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
        telemetry = await asyncio.to_thread(services.get_repository().load, path)
        current = await asyncio.to_thread(services.corner_names_payload, telemetry)
        positions = {corner["id"]: corner["apex_pct"] for corner in current["corners"]}
        points = []
        for corner in payload.corners:
            number, name, spoken = ((value or "").strip() or None for value in (corner.number, corner.name, corner.spoken))
            if corner.id in positions and (number or name or spoken):
                points.append({"pct": positions[corner.id], "number": number, "name": name, "spoken": spoken})
        try:
            await asyncio.to_thread(services.get_corner_definition_store().set_track_names, current["track_id"], current["track_config"], points)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await asyncio.to_thread(services.corner_names_payload, telemetry)

    return SimpleNamespace(
        start_practice=start_practice,
        start_drill=start_drill,
        skip_drill_warmup=skip_drill_warmup,
        drill_status=drill_status,
        stop_drill=stop_drill,
        practice_drills=practice_drills,
        close_practice=close_practice,
        get_corner_names=get_corner_names,
        put_corner_names=put_corner_names,
    )
