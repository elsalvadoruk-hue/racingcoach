"""HTTP memory endpoints with explicit runtime dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
import logging
from fastapi import HTTPException
from fastapi import Query
import asyncio
from analysis_models import AnalyzeRequest, ExperimentPlanRequest, ExperimentRequest, SessionMode
from workspace_setup import verify_planned_change

logger = logging.getLogger("iRacingSetupTuner")


@dataclass(frozen=True)
class MemoryRoutesServices:
    get_memory_db_path: Callable[..., Any]
    get_parsed_telemetry: Callable[..., Any]
    compare_experiments: Callable[..., Any]
    experiment_run_observations: Callable[..., Any]
    experiment_setup_evidence: Callable[..., Any]
    generate_ai_experiment_plan: Callable[..., Any]
    load_analysis: Callable[..., Any]
    memory_runtime_info: Callable[..., Any]
    get_memory_store: Callable[..., Any]
    get_plan_store: Callable[..., Any]
    get_repository: Callable[..., Any]
    resolve_telemetry_file: Callable[..., Any]


def register_routes(app, services: MemoryRoutesServices):
    @app.post("/api/ai/experiment-plan")
    async def create_ai_experiment_plan(payload: ExperimentPlanRequest) -> dict[str, Any]:
        return await services.generate_ai_experiment_plan(payload)

    @app.get("/api/experiment-plans")
    async def get_experiment_plans(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        if services.get_plan_store() is None:
            return {"plans": [], "persistence": "DISABLED"}
        plans = await asyncio.to_thread(services.get_plan_store().recent, limit)
        return {"plans": plans, "persistence": "SQLITE"}

    @app.get("/api/memory/status")
    async def get_memory_status() -> dict[str, Any]:
        return services.memory_runtime_info()

    @app.get("/api/memory/sessions")
    async def get_memory_sessions(
        limit: int = Query(default=20, ge=1, le=200),
        car_name: Optional[str] = None,
        track_name: Optional[str] = None,
        session_mode: Optional[SessionMode] = None,
    ) -> dict[str, Any]:
        if services.get_memory_store() is None:
            return {
                "sessions": [],
                "memory": services.memory_runtime_info(),
            }
        sessions = await asyncio.to_thread(
            services.get_memory_store().recent_sessions,
            limit,
            car_name,
            track_name,
            session_mode.value if session_mode else None,
        )
        return {
            "sessions": sessions,
            "memory": services.memory_runtime_info(),
        }

    @app.get("/api/memory/sessions/{session_id}")
    async def get_memory_session(session_id: str) -> dict[str, Any]:
        if services.get_memory_store() is None:
            raise HTTPException(status_code=503, detail="장기 기억 DB가 비활성화되어 있습니다.")
        session = await asyncio.to_thread(services.get_memory_store().get_session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="저장된 세션을 찾을 수 없습니다.")
        return {"session": session, "memory": services.memory_runtime_info()}

    @app.post("/api/experiments/compare")
    async def compare_setup_experiments(payload: ExperimentRequest) -> dict[str, Any]:
        baseline_path, candidate_path = await asyncio.gather(
            asyncio.to_thread(services.resolve_telemetry_file, payload.baseline_file),
            asyncio.to_thread(services.resolve_telemetry_file, payload.candidate_file),
        )
        if baseline_path is None or candidate_path is None:
            raise HTTPException(status_code=404, detail="기준 또는 후보 IBT 파일을 찾을 수 없습니다.")
        if baseline_path == candidate_path:
            raise HTTPException(status_code=400, detail="서로 다른 두 파일을 선택하십시오.")

        baseline_telemetry, candidate_telemetry = await asyncio.gather(
            asyncio.to_thread(services.get_repository().load, baseline_path),
            asyncio.to_thread(services.get_repository().load, candidate_path),
        )

        def comparison_identity(telemetry: services.get_parsed_telemetry()) -> tuple[str, str, str]:
            metadata = telemetry.metadata or {}
            car = str(metadata.get("car_path") or metadata.get("car_name") or "").strip().casefold()
            track = str(metadata.get("track_path") or metadata.get("track_name") or "").strip().casefold()
            config = str(metadata.get("track_config") or "").strip().casefold()
            return car, track, config

        baseline_identity = comparison_identity(baseline_telemetry)
        candidate_identity = comparison_identity(candidate_telemetry)
        if baseline_identity != candidate_identity:
            raise HTTPException(
                status_code=400,
                detail="A/B 비교는 같은 차량·트랙·레이아웃의 IBT끼리만 가능합니다.",
            )

        baseline_request = AnalyzeRequest(
            session_mode=payload.session_mode,
            filename=baseline_path.name,
            include_ai=False,
            persist_memory=False,
        )
        candidate_request = AnalyzeRequest(
            session_mode=payload.session_mode,
            filename=candidate_path.name,
            include_ai=False,
            persist_memory=False,
        )
        baseline, candidate = await asyncio.gather(
            services.load_analysis(baseline_request, update_current_analysis=False),
            services.load_analysis(candidate_request, update_current_analysis=False),
        )
        baseline_run, candidate_run = await asyncio.gather(
            asyncio.to_thread(services.experiment_run_observations, baseline_telemetry),
            asyncio.to_thread(services.experiment_run_observations, candidate_telemetry),
        )
        for result, run in ((baseline, baseline_run), (candidate, candidate_run)):
            result["session"]["environment"] = run["environment"]
            result["session"]["track_temp_c"] = run["environment"].get("track_temp_c")
            result["ab_corner_metrics"] = run["corner_metrics"]
        setup_evidence = services.experiment_setup_evidence(baseline_telemetry, candidate_telemetry)
        comparison = services.compare_experiments(
            baseline, candidate, payload.session_mode, goal=payload.goal,
            driver_feedback=payload.driver_feedback.model_dump() if payload.driver_feedback else None,
            setup_evidence=setup_evidence,
        )
        if payload.planned_change is not None:
            verify_planned_change(comparison, payload.planned_change)
        comparison["trial_id"] = payload.trial_id
        # Every valid lap of each run, for the distribution view (RaceStudio 3's box plots across sessions).
        comparison["lap_distribution"] = {
            side: [{"lap": lap.get("lap_number"), "time_s": lap.get("lap_time_s")} for lap in (run.get("laps") or [])
                   if lap.get("status") == "VALID" and isinstance(lap.get("lap_time_s"), (int, float))]
            for side, run in (("baseline", baseline), ("candidate", candidate))
        }
        comparison["gg"] = {
            "baseline": (baseline.get("vehicle") or {}).get("gg"),
            "candidate": (candidate.get("vehicle") or {}).get("gg"),
        }
        comparison["damper_velocity"] = {
            "baseline": (baseline.get("vehicle") or {}).get("damper_velocity"),
            "candidate": (candidate.get("vehicle") or {}).get("damper_velocity"),
        }
        matched_conditions = comparison["conditions_quality"]
        comparison["conditions"] = {"baseline": matched_conditions["baseline"], "candidate": matched_conditions["candidate"],
                                    "differences": {item["key"]: {"baseline": item["baseline"], "candidate": item["candidate"]}
                                                    for item in matched_conditions["checks"] if item["status"] == "MISMATCHED"}}
        memory_record: dict[str, Any] = {
            "saved": False,
            "path": str(services.get_memory_db_path()),
            "reason": "장기 기억 DB가 비활성화되어 있습니다.",
        }
        if services.get_memory_store() is not None:
            requested_mode = payload.session_mode.value
            baseline_modes = baseline.get("memory", {}).get("assigned_modes", []) or []
            candidate_modes = candidate.get("memory", {}).get("assigned_modes", []) or []
            if requested_mode not in baseline_modes or requested_mode not in candidate_modes:
                memory_record["reason"] = (
                    "A/B 결과는 계산했지만 DB에는 저장하지 않았습니다. "
                    "두 파일 모두 상단에서 동일한 세션 유형으로 먼저 확정해야 합니다."
                )
            else:
                try:
                    baseline_session_id = baseline.get("memory", {}).get("session_id")
                    linked_plan_id = payload.plan_id or await asyncio.to_thread(
                        services.get_memory_store().latest_plan_id_for_session,
                        baseline_session_id,
                    )
                    memory_record = await asyncio.to_thread(
                        services.get_memory_store().save_experiment_result,
                        comparison,
                        baseline,
                        candidate,
                        payload.session_mode.value,
                        linked_plan_id,
                    )
                    memory_record["linked_plan_id"] = linked_plan_id
                except Exception as exc:
                    logger.warning("Could not persist experiment result: %s", exc)
                    memory_record["reason"] = str(exc)
        return {
            "status": "success",
            "comparison": comparison,
            "memory_record": memory_record,
        }

    return SimpleNamespace(
        create_ai_experiment_plan=create_ai_experiment_plan,
        get_experiment_plans=get_experiment_plans,
        get_memory_status=get_memory_status,
        get_memory_sessions=get_memory_sessions,
        get_memory_session=get_memory_session,
        compare_setup_experiments=compare_setup_experiments,
    )
