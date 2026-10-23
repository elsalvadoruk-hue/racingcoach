"""Stable analysis request models and session policy, without application startup."""
from __future__ import annotations
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class SessionMode(str, Enum):
    QUALIFYING = "QUALIFYING"
    BASELINE_3LAP = "BASELINE_3LAP"
    ENDURANCE = "ENDURANCE"


class PitRule(str, Enum):
    CONCURRENT = "CONCURRENT"
    SEQUENTIAL = "SEQUENTIAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


SESSION_POLICIES: dict[SessionMode, dict[str, Any]] = {
    SessionMode.QUALIFYING: {
        "label": "퀄리파잉",
        "expected_run_structure": ["OUT_LAP", "TIMED_LAP_1", "TIMED_LAP_2"],
        "minimum_analysis_laps": 1,
        "comparison_laps": 2,
        "quality_target_laps": 2,
        "setup_repeat_laps": 2,
        "recommended_timed_laps": 2,
        "setup_confidence_cap": 0.68,
    },
    SessionMode.BASELINE_3LAP: {
        "label": "3-Lap 베이스",
        "expected_run_structure": ["OUT_LAP", "TIMED_LAP_1", "TIMED_LAP_2", "TIMED_LAP_3"],
        "minimum_analysis_laps": 1,
        "comparison_laps": 2,
        "quality_target_laps": 3,
        "setup_repeat_laps": 3,
        "recommended_timed_laps": 3,
        "setup_confidence_cap": 0.82,
    },
    SessionMode.ENDURANCE: {
        "label": "인듀런스",
        "expected_run_structure": ["OUT_LAP", "TIMED_LAPS_5_PLUS"],
        "minimum_analysis_laps": 1,
        "comparison_laps": 2,
        "quality_target_laps": 5,
        "setup_repeat_laps": 3,
        "recommended_timed_laps": 5,
        "setup_confidence_cap": 0.82,
    },
}


def session_policy(mode: SessionMode) -> dict[str, Any]:
    return dict(SESSION_POLICIES[mode])


class AnalyzeRequest(BaseModel):
    progress_id: Optional[str] = Field(default=None, max_length=64)
    session_mode: SessionMode = SessionMode.BASELINE_3LAP
    pit_rule: PitRule = PitRule.NOT_APPLICABLE
    user_feedback: str = Field(default="", max_length=2000)
    feedback_phase: Optional[Literal["ENTRY", "MID_CORNER", "EXIT"]] = None
    feedback_speed: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    feedback_symptom: Optional[Literal["UNDERSTEER", "OVERSTEER", "BRAKING", "KERB"]] = None
    setup_mode: Literal["EXPLORE", "EVIDENCE"] = "EXPLORE"
    target_lap: Optional[int] = None
    reference_lap: Optional[int] = None
    # Accepted from older clients only; analysis always stays local.
    include_ai: bool = Field(default=False, json_schema_extra={"deprecated": True}, description="Retired; analysis never calls AI.")
    filename: Optional[str] = None
    # Local analysis is read-only by default. The frontend sets this only after
    # the user explicitly assigns a session type to the selected IBT.
    persist_memory: bool = False
    # Required when replacing a previously assigned QUALIFYING/BASELINE/ENDURANCE
    # classification for the same IBT file.
    replace_existing_mode: bool = False
    # Garage61 CSV reference laps are read locally. One file is display-only;
    # setup conclusions require at least two faster compatible references.
    external_reference_files: list[str] = Field(default_factory=list, max_length=24)


class ComparisonDetailRequest(BaseModel):
    filename: Optional[str] = None
    reference_filename: Optional[str] = None
    target_lap: Optional[int] = None
    reference_lap: Optional[int] = None
    external_reference_files: list[str] = Field(default_factory=list, max_length=24)
    start_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    end_pct: float = Field(default=1.0, ge=0.0, le=1.0)
    points: int = Field(default=2400, ge=100, le=10000)
    reference_mode: Literal["LOCAL", "AGGREGATE", "SINGLE"] = "LOCAL"


class OverlayExtraLap(BaseModel):
    source: Literal["IBT", "GARAGE61"]
    filename: str
    lap: Optional[int] = None


class LapOverlayRequest(BaseModel):
    filename: Optional[str] = None
    laps: list[int] = Field(default_factory=list, max_length=8)
    extra: list[OverlayExtraLap] = Field(default_factory=list, max_length=4)
    base_lap: Optional[int] = None
    base_key: Optional[str] = None
    start_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    end_pct: float = Field(default=1.0, ge=0.0, le=1.0)
    points: int = Field(default=1500, ge=100, le=4000)


def request_with_structured_feedback(request: Any) -> Any:
    selections = [f"{name}={getattr(request, name, None)}" for name in ("feedback_phase", "feedback_speed", "feedback_symptom") if getattr(request, name, None)]
    if not selections:
        return request
    suffix = "[선택한 주행 피드백: " + ", ".join(selections) + "]"
    feedback = request.user_feedback.strip()
    if suffix in feedback:
        return request
    return request.model_copy(update={"user_feedback": feedback + ("\n" if feedback else "") + suffix})


class Garage61CollectRequest(BaseModel):
    filename: str
    force: bool = False
    target_count: Optional[int] = Field(default=None, ge=3, le=24)
    headless: bool = False
    collector_mode: Literal["AUTO", "API", "BROWSER"] = "AUTO"


class CornerName(BaseModel):
    id: str = Field(min_length=1, max_length=12)
    number: Optional[str] = Field(default=None, max_length=12)
    name: Optional[str] = Field(default=None, max_length=60)
    spoken: Optional[str] = Field(default=None, max_length=60)


class CornerNamesRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    corners: list[CornerName] = Field(max_length=300)


class ExperimentDriverFeedback(BaseModel):
    change: Literal["IMPROVED", "SAME", "WORSENED"]
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    side_effects: str = Field(default="", max_length=2000)


class ExperimentRequest(BaseModel):
    baseline_file: str
    candidate_file: str
    session_mode: SessionMode = SessionMode.BASELINE_3LAP
    plan_id: Optional[str] = None
    goal: Literal["COMFORT", "PACE", "ENDURANCE"] = "COMFORT"
    driver_feedback: Optional[ExperimentDriverFeedback] = None
    trial_id: Optional[str] = Field(default=None, max_length=160)
    # The workbench's single planned adjustment is verified before persistence.
    planned_change: Optional[dict[str, Any]] = None


class BriefingSectionSchema(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    items: list[str] = Field(min_length=1, max_length=12)


class AIBriefingSchema(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    sections: list[BriefingSectionSchema] = Field(min_length=1, max_length=12)


class ExperimentPlanRequest(BaseModel):
    session_mode: SessionMode = SessionMode.BASELINE_3LAP
    pit_rule: PitRule = PitRule.NOT_APPLICABLE
    user_feedback: str = Field(default="", max_length=2000)
    feedback_phase: Optional[Literal["ENTRY", "MID_CORNER", "EXIT"]] = None
    feedback_speed: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    feedback_symptom: Optional[Literal["UNDERSTEER", "OVERSTEER", "BRAKING", "KERB"]] = None
    setup_mode: Literal["EXPLORE", "EVIDENCE"] = "EXPLORE"
    target_lap: Optional[int] = None
    reference_lap: Optional[int] = None
    filename: Optional[str] = None
    external_reference_files: list[str] = Field(default_factory=list, max_length=24)


class AIPlannerSelectionSchema(BaseModel):
    decision: Literal["DRIVING_TEST", "SETUP_TEST", "HOLD"]
    selected_candidate_id: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=1200)
    rationale: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    confidence: float = Field(ge=0.0, le=1.0)
