from __future__ import annotations

import asyncio
import csv
import glob
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import struct
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from collections import Counter, OrderedDict, defaultdict
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Optional

import numpy as np
import uvicorn
import yaml
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import app_settings
import session_log

# The data folder and the settings entered on the screen, before anything reads its configuration.
app_settings.apply_environment()

from garage61_collector_manager import Garage61CollectorManager
from analysis_support import CornerDefinitionStore, FileMetadataIndex, JsonCache, clean_segment_context, mask_grid_to_clean_windows, session_context, suggested_session_mode
from setup_profiles import supplement_setup_summary, vehicle_setup_capabilities
from live_telemetry import LiveTelemetry
from live_pit import PitKnowledge
from live_formation import RaceHistory
from voice_notes import VoiceNoteStore, review_notes
from voice_coach import VoiceCoach, register_voice_routes
import corner_names

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("iRacingSetupTuner")
# Same safe subset as yaml.safe_load; the C parser is several times faster on session YAML.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
USER_HOME = Path.home()
DEFAULT_TELEMETRY_DIRS = [
    USER_HOME / "Documents" / "iRacing" / "telemetry",
    USER_HOME / "OneDrive" / "문서" / "iRacing" / "telemetry",
    USER_HOME / "OneDrive" / "Documents" / "iRacing" / "telemetry",
]


def _configured_telemetry_dirs() -> list[Path]:
    configured = os.getenv("IRACING_TELEMETRY_DIRS", "").strip()
    paths = list(DEFAULT_TELEMETRY_DIRS)
    if configured:
        paths.extend(Path(value).expanduser() for value in configured.split(os.pathsep) if value.strip())

    # Useful for a portable/demo installation. It has no effect when the directory
    # does not exist and does not replace the normal iRacing telemetry folders.
    demo_dir = Path(os.getenv("IRACING_DEMO_DIR", str(APP_DIR)))
    paths.append(demo_dir)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.resolve()) if path.exists() else str(path)
        if resolved not in seen:
            deduped.append(path)
            seen.add(resolved)
    return deduped


TELEMETRY_DIRS = _configured_telemetry_dirs()
MAX_CACHE_FILES = int(os.getenv("IRACING_CACHE_FILES", "3"))
DEFAULT_GRID_POINTS = int(os.getenv("IRACING_GRID_POINTS", "1400"))
VISUAL_GRID_POINTS = int(os.getenv("IRACING_VISUAL_POINTS", "420"))
MIN_DISPLAY_LAPS = max(1, int(os.getenv("IRACING_MIN_DISPLAY_LAPS", "3")))
MIN_DISPLAY_VALID_LAPS = max(1, int(os.getenv("IRACING_MIN_DISPLAY_VALID_LAPS", "2")))
FILE_SCAN_LIMIT = max(20, int(os.getenv("IRACING_FILE_SCAN_LIMIT", "250")))


def _configured_memory_db_path() -> Path:
    """Resolve the unified long-term-memory database path.

    The database lives in the installation's data folder (see app_settings).
    ``IRACING_TUNER_DB_PATH`` is the preferred override. The old
    ``IRACING_PLAN_DB_PATH`` variable is treated only as an import source so
    a previous v2.1 setting cannot silently redirect the unified v2.2 database
    back to another drive.
    """

    configured = (
        os.getenv("IRACING_TUNER_DB_PATH", "").strip()
        or os.getenv("IRACING_MEMORY_DB_PATH", "").strip()
    )
    if configured:
        return Path(configured).expanduser()
    return app_settings.data_dir() / "iracing_tuner.sqlite3"


MEMORY_DB_PATH = _configured_memory_db_path()
try:
    MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    logger.warning("Could not create the data folder: %s", exc)
APP_VERSION = "1.0.2"   # the first version made to be shared (2026-10-12); earlier numbers were internal


def _configured_external_reference_dir() -> Path:
    configured = os.getenv("IRACING_EXTERNAL_REFERENCE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return MEMORY_DB_PATH.parent / "Garage61_References"


EXTERNAL_REFERENCE_DIR = _configured_external_reference_dir()
EXTERNAL_REFERENCE_MAX_SELECTION = max(2, min(24, int(os.getenv("IRACING_EXTERNAL_REFERENCE_MAX", "12"))))
EXTERNAL_REFERENCE_DISPLAY_LIMIT = max(3, min(EXTERNAL_REFERENCE_MAX_SELECTION, int(os.getenv("IRACING_EXTERNAL_REFERENCE_DISPLAY_LIMIT", "9"))))
EXTERNAL_REFERENCE_RECOMMENDED_COHORT = max(2, min(EXTERNAL_REFERENCE_DISPLAY_LIMIT, int(os.getenv("IRACING_EXTERNAL_REFERENCE_COHORT", "5"))))
EXTERNAL_REFERENCE_MIN_DECISION = max(2, int(os.getenv("IRACING_EXTERNAL_REFERENCE_MIN_DECISION", "2")))
EXTERNAL_REFERENCE_STRONG_DECISION = max(EXTERNAL_REFERENCE_MIN_DECISION + 1, int(os.getenv("IRACING_EXTERNAL_REFERENCE_STRONG_DECISION", "3")))
EXTERNAL_REFERENCE_SCAN_LIMIT = max(50, int(os.getenv("IRACING_EXTERNAL_REFERENCE_SCAN_LIMIT", "1500")))
EXTERNAL_REFERENCE_CATALOG_PATH = EXTERNAL_REFERENCE_DIR / "garage61_catalog.json"
GARAGE61_JOB_DIR = MEMORY_DB_PATH.parent / "Garage61_Jobs"
GARAGE61_API_TOKEN_FILE = Path(os.getenv("GARAGE61_API_TOKEN_FILE", str(MEMORY_DB_PATH.parent / "garage61_api_token.txt"))).expanduser()
GARAGE61_API_BASE = os.getenv("GARAGE61_API_BASE", "https://garage61.net/api/v1").rstrip("/")
GARAGE61_AUTO_COLLECT_ENABLED = os.getenv("GARAGE61_AUTO_COLLECT", "1").strip().lower() not in {"0", "false", "off", "no"}
GARAGE61_CACHE_FRESH_HOURS = max(1.0, float(os.getenv("GARAGE61_CACHE_FRESH_HOURS", "168")))
GARAGE61_MIN_USABLE_REFERENCES = max(2, int(os.getenv("GARAGE61_MIN_USABLE_REFERENCES", "5")))
GARAGE61_TARGET_REFERENCES = max(GARAGE61_MIN_USABLE_REFERENCES, min(EXTERNAL_REFERENCE_MAX_SELECTION, int(os.getenv("GARAGE61_TARGET_REFERENCES", "8"))))
GARAGE61_MIN_DISTINCT_DRIVERS = max(2, int(os.getenv("GARAGE61_MIN_DISTINCT_DRIVERS", "3")))
GARAGE61_CONDITION_SCORE_MIN = max(0.0, min(1.0, float(os.getenv("GARAGE61_CONDITION_SCORE_MIN", "0.55"))))
RACING_LINE_COMPARE_MAX_CORNERS = 2
RACING_LINE_COMPARE_MIN_LOSS_S = max(0.0, float(os.getenv("RACING_LINE_COMPARE_MIN_LOSS_S", "0.04")))
RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH = max(0.5, float(os.getenv("RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH", "3.0")))
RACING_LINE_COMPARE_MIN_P75_GAP_M = max(0.2, float(os.getenv("RACING_LINE_COMPARE_MIN_P75_GAP_M", "0.70")))
RACING_LINE_COMPARE_MIN_PEAK_GAP_M = max(RACING_LINE_COMPARE_MIN_P75_GAP_M, float(os.getenv("RACING_LINE_COMPARE_MIN_PEAK_GAP_M", "1.20")))
RACING_LINE_COMPARE_POINTS = max(28, min(80, int(os.getenv("RACING_LINE_COMPARE_POINTS", "48"))))
GARAGE61_COLLECTOR_COOLDOWN_MIN = max(1.0, float(os.getenv("GARAGE61_COLLECTOR_COOLDOWN_MIN", "15")))
GARAGE61_COLLECTOR_MAX_WALL_SECONDS = max(180, int(os.getenv("GARAGE61_COLLECTOR_MAX_WALL_SECONDS", "600")))
GARAGE61_NAVIGATION_TIMEOUT_MS = max(60000, int(os.getenv("GARAGE61_NAVIGATION_TIMEOUT_MS", "180000")))
GARAGE61_ACTION_TIMEOUT_MS = max(20000, int(os.getenv("GARAGE61_ACTION_TIMEOUT_MS", "60000")))
GARAGE61_SETTLE_MS = max(800, int(os.getenv("GARAGE61_SETTLE_MS", "2400")))
GARAGE61_RETRY_COUNT = max(1, min(5, int(os.getenv("GARAGE61_RETRY_COUNT", "3"))))
try:
    EXTERNAL_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    GARAGE61_JOB_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    logger.warning("Could not create Garage61 reference/job directory: %s", exc)

garage61_collector = Garage61CollectorManager(
    app_dir=APP_DIR,
    reference_dir=EXTERNAL_REFERENCE_DIR,
    job_dir=GARAGE61_JOB_DIR,
    token_file=GARAGE61_API_TOKEN_FILE,
)

# Backward-compatible alias used by older integrations/tests.
PLAN_DB_PATH = MEMORY_DB_PATH


def _legacy_plan_db_paths() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("IRACING_PLAN_DB_PATH", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(USER_HOME / ".iracing_setup_tuner" / "experiment_plans.sqlite3")
    output: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            output.append(candidate)
            seen.add(key)
    return output

# Two jobs, two models (compared on the app's own requests, 2026-10-13):
# the briefing and the setup planner want the best content and can take ten seconds or more;
# the spoken answer has to come back in two or three seconds, every time.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_VOICE_MODEL = os.getenv("GEMINI_VOICE_MODEL", "gemini-3.5-flash")
genai_types = None


def _connect_ai():
    """The Vertex AI client for the project and key now configured, or None. Called again when the screen changes them."""
    global genai_types
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        if os.getenv("IRACING_AI_ENABLED", "1").lower() in {"0", "false", "off"}:
            raise RuntimeError("disabled by configuration")
        project = os.getenv("GCP_PROJECT_ID", "").strip()
        if not project or not os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip():
            raise RuntimeError("no Google Cloud project or key configured")
        client = genai.Client(vertexai=True, project=project, location=os.getenv("GCP_LOCATION", "").strip() or "global")
        genai_types = types
        logger.info("Vertex AI client initialized")
        return client
    except Exception as exc:  # The deterministic analysis remains fully usable.
        logger.info("Vertex AI disabled: %s", exc)
        return None


ai_client = _connect_ai()


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------


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
    session_mode: SessionMode = SessionMode.BASELINE_3LAP
    pit_rule: PitRule = PitRule.NOT_APPLICABLE
    user_feedback: str = Field(default="", max_length=2000)
    feedback_phase: Optional[Literal["ENTRY", "MID_CORNER", "EXIT"]] = None
    feedback_speed: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    feedback_symptom: Optional[Literal["UNDERSTEER", "OVERSTEER", "BRAKING", "KERB"]] = None
    setup_mode: Literal["EXPLORE", "EVIDENCE"] = "EXPLORE"
    target_lap: Optional[int] = None
    reference_lap: Optional[int] = None
    include_ai: bool = False
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
    target_lap: Optional[int] = None
    reference_lap: Optional[int] = None
    external_reference_files: list[str] = Field(default_factory=list, max_length=24)
    start_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    end_pct: float = Field(default=1.0, ge=0.0, le=1.0)
    points: int = Field(default=2400, ge=100, le=10000)
    reference_mode: Literal["LOCAL", "AGGREGATE", "SINGLE"] = "LOCAL"


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


# ---------------------------------------------------------------------------
# IBT parsing
# ---------------------------------------------------------------------------


IRSDK_DTYPES: dict[int, np.dtype[Any]] = {
    0: np.dtype("i1"),
    1: np.dtype("?"),
    2: np.dtype("<i4"),
    3: np.dtype("<u4"),
    4: np.dtype("<f4"),
    5: np.dtype("<f8"),
}

CORE_CHANNELS = {
    "SessionTime",
    "Lap",
    "LapDistPct",
    "Speed",
    "Throttle",
    "Brake",
    "SteeringWheelAngle",
    "OnPitRoad",
    "PlayerTrackSurface",
}

ANALYSIS_CHANNELS = {
    # Session/lap state
    "SessionTime",
    "SessionTick",
    "SessionFlags",
    "Lap",
    "LapCompleted",
    "LapDist",
    "LapDistPct",
    "LapCurrentLapTime",
    "LapLastLapTime",
    "LapBestLapTime",
    "LapDeltaToBestLap",
    "LapDeltaToBestLap_OK",
    "OnPitRoad",
    "IsOnTrack",
    "PlayerTrackSurface",
    "PlayerTrackSurfaceMaterial",
    "PlayerIncidents",
    # Driver controls and vehicle response
    "Speed",
    "Throttle",
    "ThrottleRaw",
    "Brake",
    "BrakeRaw",
    "BrakeABSactive",
    "BrakeABScutPct",
    "SteeringWheelAngle",
    "Yaw",
    "YawRate",
    "LatAccel",
    "LongAccel",
    "VertAccel",
    "Pitch",
    "Roll",
    "Gear",
    "RPM",
    # Conditions and position
    "Lat",
    "Lon",
    "Alt",
    "TrackTemp",
    "TrackTempCrew",
    "AirTemp",
    "AirDensity",
    "AirPressure",
    "RelativeHumidity",
    "WindVel",
    "WindDir",
    "Precipitation",
    "FogLevel",
    "TrackWetness",
    "WeatherDeclaredWet",
    "Skies",
    "FuelLevel",
    "TireCompound",
    "PlayerTireCompound",
    # In-car controls
    "dcBrakeBias",
    "dcABS",
    "dcTractionControl",
    "dcThrottleShape",
    # Wheels/tires
    "LFspeed",
    "RFspeed",
    "LRspeed",
    "RRspeed",
    "LFpressure",
    "RFpressure",
    "LRpressure",
    "RRpressure",
    "LFcoldPressure",
    "RFcoldPressure",
    "LRcoldPressure",
    "RRcoldPressure",
    "LFtempL",
    "LFtempM",
    "LFtempR",
    "RFtempL",
    "RFtempM",
    "RFtempR",
    "LRtempL",
    "LRtempM",
    "LRtempR",
    "RRtempL",
    "RRtempM",
    "RRtempR",
    "LFtempCL",
    "LFtempCM",
    "LFtempCR",
    "RFtempCL",
    "RFtempCM",
    "RFtempCR",
    "LRtempCL",
    "LRtempCM",
    "LRtempCR",
    "RRtempCL",
    "RRtempCM",
    "RRtempCR",
    "LFwearL",
    "LFwearM",
    "LFwearR",
    "RFwearL",
    "RFwearM",
    "RFwearR",
    "LRwearL",
    "LRwearM",
    "LRwearR",
    "RRwearL",
    "RRwearM",
    "RRwearR",
    # Suspension and ride heights
    "LFrideHeight",
    "RFrideHeight",
    "LRrideHeight",
    "RRrideHeight",
    "CFSRrideHeight",
    "LFshockDefl",
    "RFshockDefl",
    "LRshockDefl",
    "RRshockDefl",
    "LFshockVel",
    "RFshockVel",
    "LRshockVel",
    "RRshockVel",
    # Kerb/rumble proxy
    "TireLF_RumblePitch",
    "TireRF_RumblePitch",
    "TireLR_RumblePitch",
    "TireRR_RumblePitch",
}

FEATURE_CHANNELS: dict[str, set[str]] = {
    "lap_comparison": {"SessionTime", "Lap", "LapDistPct", "Speed"},
    "driver_inputs": {"Throttle", "Brake", "SteeringWheelAngle"},
    "vehicle_balance": {"YawRate", "LatAccel", "LongAccel"},
    "abs_analysis": {"BrakeABSactive"},
    "tires": {"LFpressure", "RFpressure", "LRpressure", "RRpressure"},
    "suspension": {"LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"},
    "splitter_bottoming": {"CFSRrideHeight"},
    "gps_map": {"Lat", "Lon"},
}

# The IBT embeds current setup values, but not every Garage minimum/maximum.
# These rules validate only changes whose increments are known by this app.
ADJUSTMENT_RULES: dict[str, dict[str, Optional[float]]] = {
    # Discrete Garage controls whose one-click increment is stable enough to
    # use for an A/B candidate.  The IBT does not expose the per-car maximum,
    # so every numeric recommendation remains TEST_ONLY and the UI keeps the
    # Garage-range verification warning.
    "front_arb_blades": {"step": 1.0, "minimum": 1.0, "maximum": None},
    "rear_arb_blades": {"step": 1.0, "minimum": 1.0, "maximum": None},
    "rear_wing_angle_deg": {"step": 1.0, "minimum": 0.0, "maximum": None},
    "front_ride_height": {"step": 1.0, "minimum": 0.0, "maximum": None},
    "rear_ride_height": {"step": 1.0, "minimum": 0.0, "maximum": None},
    "starting_pressure_kpa": {"step": 3.0, "minimum": 159.0, "maximum": None},
}

# Canonical setup controls used by the closed-loop personalization engine.
# The hints match flattened iRacing setup paths already stored in SQLite.
PERSONALIZATION_SETTING_META: dict[str, dict[str, Any]] = {
    "rear_wing_angle_deg": {
        "label": "Rear wing angle", "unit": "deg", "hints": ("rearwingangle", "rear.wingangle"),
    },
    "rear_arb_blades": {
        "label": "Rear ARB blades", "unit": "blade", "hints": ("rear.arbblades", "reararbblades"),
    },
    "front_arb_blades": {
        "label": "Front ARB blades", "unit": "blade", "hints": ("frontbrakeslights.arbblades", "frontbrakes.arbblades", "frontarbblades"),
    },
    "front_ride_height": {
        "label": "Front ride height", "unit": "mm", "hints": ("leftfront.rideheight", "rightfront.rideheight"),
    },
    "rear_ride_height": {
        "label": "Rear ride height", "unit": "mm", "hints": ("leftrear.rideheight", "rightrear.rideheight"),
    },
    "diff_preload_nm": {
        "label": "Differential preload", "unit": "Nm", "hints": ("diffpreload",),
    },
    "brake_bias_pct": {
        "label": "Brake bias", "unit": "%", "hints": ("brakepressurebias",),
    },
    "front_toe_mm": {
        "label": "Front total toe", "unit": "mm", "hints": ("totaltoein",),
    },
}

COACH_ROOT_CAUSE_META: dict[str, dict[str, Any]] = {
    "BRAKE_RELEASE_LATE": {"label": "브레이크 릴리즈 지연", "difficulty": 0.90, "phase": "ENTRY"},
    "BRAKE_POINT_EARLY": {"label": "제동 시작이 너무 이름", "difficulty": 0.82, "phase": "BRAKING"},
    "OVER_BRAKING_ABS": {"label": "과제동 / ABS 의존", "difficulty": 0.78, "phase": "BRAKING"},
    "EXCESS_COASTING": {"label": "코스팅 과다", "difficulty": 0.90, "phase": "MID"},
    "THROTTLE_RETURN_LATE": {"label": "풀스로틀 복귀 지연", "difficulty": 0.92, "phase": "EXIT"},
    "EXIT_INSTABILITY": {"label": "탈출 안정성 부족", "difficulty": 0.72, "phase": "EXIT"},
    "ROTATION_DEFICIT": {"label": "진입/미드 회전 부족", "difficulty": 0.74, "phase": "ENTRY_MID"},
    "DRIVER_VARIANCE": {"label": "입력 패턴 편차 · 원인 관찰", "difficulty": 0.35, "phase": "GENERAL"},
    "CONSISTENCY": {"label": "랩 재현성", "difficulty": 0.95, "phase": "GENERAL"},
}


def canonical_setup_changes(full_setup_diff: dict[str, Any]) -> list[dict[str, Any]]:
    """Map flattened iRacing setup differences to stable canonical controls.

    This copy intentionally lives before LongTermMemoryStore so schema migration
    can backfill old A/B records while the module is still initializing.
    """
    def numeric(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            number = float(value)
            return number if math.isfinite(number) else None
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        try:
            number = float(match.group(0))
            return number if math.isfinite(number) else None
        except ValueError:
            return None

    output: list[dict[str, Any]] = []
    for setting, meta in PERSONALIZATION_SETTING_META.items():
        hints = tuple(str(value).casefold() for value in meta.get("hints") or ())
        matches: list[tuple[float, float]] = []
        raw_keys: list[str] = []
        for raw_key, change in (full_setup_diff or {}).items():
            key = str(raw_key or "").casefold()
            if not any(hint in key for hint in hints) or not isinstance(change, dict):
                continue
            left = numeric(change.get("baseline"))
            right = numeric(change.get("candidate"))
            if left is None or right is None or abs(right - left) <= 1e-9:
                continue
            matches.append((left, right))
            raw_keys.append(str(raw_key))
        if not matches:
            continue
        directions = {1 if right > left else -1 for left, right in matches}
        if len(directions) != 1:
            continue
        baseline_value = float(np.mean([left for left, _ in matches]))
        candidate_value = float(np.mean([right for _, right in matches]))
        output.append({
            "setting": setting,
            "label": meta.get("label") or setting,
            "unit": meta.get("unit"),
            "direction": directions.pop(),
            "baseline_value": round(baseline_value, 4),
            "candidate_value": round(candidate_value, 4),
            "delta_value": round(candidate_value - baseline_value, 4),
            "raw_keys": raw_keys,
        })
    return output


def setup_learning_eligibility(comparison: dict[str, Any], full_setup_diff: dict[str, Any]) -> dict[str, Any]:
    """The same attribution rule is used for new and historical learning."""
    canonical = canonical_setup_changes(full_setup_diff)
    # Symmetric left/right changes to one registered control form one logical
    # change; unregistered controls must still count as possible confounders.
    informational = {"setupname", "setupfilename", "setupfilepath", "setupid", "updatecount", "lastmodified", "carname", "trackname"}
    controls = {key: value for key, value in full_setup_diff.items()
                if isinstance(value, dict) and value.get("baseline") != value.get("candidate")
                and re.sub(r"[^a-z0-9]", "", str(key).split(".")[-1].casefold()) not in informational}
    covered: set[str] = set()
    groups: list[str] = []
    for change in canonical:
        keys = set(change.get("raw_keys") or []) & set(controls)
        if keys:
            covered.update(keys)
            groups.append(str(change["setting"]))
    groups.extend(key for key in controls if key not in covered)
    unknown = [key for key in controls if key not in covered]
    incomplete = [key for key, change in controls.items() if change.get("baseline") is None or change.get("candidate") is None]
    confounded = len(groups) > 1
    reasons = []
    if not groups:
        reasons.append("변경된 셋업 조정 항목을 확인할 수 없습니다.")
    if confounded:
        reasons.append("여러 셋업 항목이 변경되어 개별 항목의 효과를 분리할 수 없습니다.")
    if incomplete:
        reasons.append("기준 또는 후보 셋업의 실제 값을 확인할 수 없습니다.")
    quality = comparison.get("conditions_quality") or {}
    if quality.get("status") != "MATCHED":
        reasons.append("주행 조건이 다르거나 필요한 조건 정보가 없습니다.")
    if not comparison.get("effect_confirmed", False):
        reasons.append("반복 표본에서 객관적인 효과가 확인되지 않았습니다.")
    if str(comparison.get("verdict") or "").upper() not in {"KEEP", "REVERT"}:
        reasons.append("효과 판정이 미확정입니다.")
    stability = (comparison.get("performance_axes") or {}).get("stability") or {}
    if stability.get("status") == "WORSENED" and comparison.get("verdict") == "KEEP":
        reasons.append("완주 안정성 악화를 셋업 개선으로 학습하지 않습니다.")
    return {"eligible": not reasons, "reasons": reasons, "confounded": confounded,
            "logical_change_count": len(groups), "control_changes": groups, "unregistered_changes": unknown,
            "incomplete_changes": incomplete,
            "policy": "MATCHED_CONDITIONS_OBJECTIVE_EFFECT_SINGLE_CONTROL"}


def experiment_setup_evidence(baseline: Any, candidate: Any) -> dict[str, Any]:
    """Compute A/B setup and condition evidence directly from the two IBTs."""
    left = LongTermMemoryStore._flatten_setup(baseline.setup or {})
    right = LongTermMemoryStore._flatten_setup(candidate.setup or {})
    full_diff = {key: {"baseline": left.get(key), "candidate": right.get(key)}
                 for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)}
    canonical = canonical_setup_changes(full_diff)
    if not canonical:
        left_summary, right_summary = extract_setup_summary(baseline.setup or {}), extract_setup_summary(candidate.setup or {})
        for key in sorted(set(left_summary) | set(right_summary)):
            before, after = left_summary.get(key), right_summary.get(key)
            if before is not None and after is not None and before != after:
                canonical.append({"setting": key, "label": key, "unit": None, "baseline_value": before,
                                  "candidate_value": after, "delta_value": after - before, "raw_keys": []})

    def conditions(telemetry: Any) -> dict[str, Any]:
        valid = [lap for lap in telemetry.laps if lap.valid and lap.lap_time_s]
        best = min(valid, key=lambda lap: float(lap.lap_time_s)) if valid else None
        return telemetry_reference_conditions(telemetry, best)

    baseline_conditions, candidate_conditions = conditions(baseline), conditions(candidate)
    condition_diffs = {key: {"baseline": baseline_conditions.get(key), "candidate": candidate_conditions.get(key)}
                       for key in sorted(set(baseline_conditions) | set(candidate_conditions))
                       if baseline_conditions.get(key) != candidate_conditions.get(key)}
    return {"setup_changes": canonical, "canonical_setup_changes": canonical,
            "setup_diff": {"full_setup": full_diff, "changed_field_count": len(full_diff)},
            "conditions": {"baseline": baseline_conditions, "candidate": candidate_conditions, "differences": condition_diffs},
            "setup_data_available": bool(baseline.setup and candidate.setup)}



@dataclass(frozen=True)
class VariableHeader:
    var_type: int
    offset: int
    count: int
    count_as_time: bool
    name: str
    description: str
    unit: str


@dataclass
class LapSegment:
    lap_number: int
    start_idx: int
    end_idx: int
    complete: bool
    valid: bool
    status: str
    lap_time_s: Optional[float]
    start_pct: float
    end_pct: float
    on_track_ratio: float
    pit_samples: int
    fuel_used_l: Optional[float]
    invalid_reason: Optional[str] = None


@dataclass
class ParsedTelemetry:
    path: Path
    signature: tuple[str, int, int]
    tick_rate: int
    buffer_length: int
    record_count: int
    data_offset: int
    session_text: str
    session_info: dict[str, Any]
    variables: dict[str, VariableHeader]
    channels: dict[str, np.ndarray[Any, Any]]
    metadata: dict[str, Any]
    setup: dict[str, Any]
    laps: list[LapSegment]
    parsed_at: float = field(default_factory=time.time)


GARAGE61_FILENAME_RE = re.compile(
    r"^Garage 61 - (?P<driver>.+?) - (?P<car>.+?) - (?P<track>.+?) - "
    r"(?P<time>\d{2}\.\d{2}\.\d{3}) - (?P<lap_id>[^.]+)\.csv$",
    re.IGNORECASE,
)


def _garage61_lap_time_seconds(value: str) -> Optional[float]:
    parts = str(value or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        minutes, seconds, milliseconds = (int(part) for part in parts)
    except ValueError:
        return None
    total = minutes * 60.0 + seconds + milliseconds / 1000.0
    return total if total > 0 else None


def _garage61_track_parts(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text, ""


def garage61_filename_metadata(path: Path) -> Optional[dict[str, Any]]:
    match = GARAGE61_FILENAME_RE.match(path.name)
    if not match:
        return None
    lap_time_s = _garage61_lap_time_seconds(match.group("time"))
    if lap_time_s is None:
        return None
    track_name, track_config = _garage61_track_parts(match.group("track"))
    return {
        "name": path.name,
        "driver_name": match.group("driver").strip(),
        "car_name": match.group("car").strip(),
        "track_name": track_name,
        "track_config": track_config,
        "lap_time_s": lap_time_s,
        "lap_id": match.group("lap_id").strip(),
    }




def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _reference_catalog() -> dict[str, dict[str, Any]]:
    payload = _read_json_object(EXTERNAL_REFERENCE_CATALOG_PATH)
    laps = payload.get("laps") if isinstance(payload, dict) else None
    if isinstance(laps, dict):
        return {str(key): value for key, value in laps.items() if isinstance(value, dict)}
    # Backward-compatible list format for early crawler builds.
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        return {
            str(item.get("lap_id")): item
            for item in items
            if isinstance(item, dict) and item.get("lap_id")
        }
    return {}


def _reference_sidecar(path: Path) -> dict[str, Any]:
    candidates = [
        path.with_suffix(".meta.json"),
        Path(str(path) + ".meta.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            payload = _read_json_object(candidate)
            if payload:
                return payload
    return {}


def garage61_reference_metadata(path: Path, catalog: Optional[dict[str, dict[str, Any]]] = None) -> Optional[dict[str, Any]]:
    """Merge CSV filename identity with Garage61 metadata.

    Legacy browser exports use the filename as the identity fallback. API-first
    references additionally contain Garage61/iRacing IDs in the catalog/sidecar.
    When that API metadata belongs to the same lap ID, the API identity is more
    authoritative than display-name text because iRacing and Garage61 can use
    slightly different car/layout labels.
    """

    base = garage61_filename_metadata(path)
    if base is None:
        return None
    catalog_data = catalog if catalog is not None else _reference_catalog()
    catalog_meta = catalog_data.get(str(base.get("lap_id") or ""), {})
    sidecar = _reference_sidecar(path)
    merged = dict(base)
    identity_keys = {"driver_name", "car_name", "track_name", "track_config", "lap_time_s"}
    for source in (catalog_meta, sidecar):
        source_lap_id = str(source.get("lap_id") or "").strip().upper() if isinstance(source, dict) else ""
        base_lap_id = str(base.get("lap_id") or "").strip().upper()
        trusted_api_identity = (
            isinstance(source, dict)
            and str(source.get("source_type") or "").upper() == "GARAGE61_API"
            and bool(source_lap_id)
            and source_lap_id == base_lap_id
        )
        for key, value in source.items():
            if key in {"name", "lap_id"}:
                continue
            if key in identity_keys and not trusted_api_identity:
                continue
            if value is not None and value != "":
                merged[key] = value
    merged["metadata_enriched"] = bool(catalog_meta or sidecar)
    source_type = str(merged.get("source_type") or "").upper()
    merged["metadata_source"] = (
        "GARAGE61_API" if source_type == "GARAGE61_API" else
        "SIDECAR_AND_CATALOG" if sidecar and catalog_meta else
        "SIDECAR" if sidecar else
        "CRAWLER_CATALOG" if catalog_meta else
        "CSV_FILENAME_ONLY"
    )
    return merged


def _finite_channel_median(
    telemetry: ParsedTelemetry,
    name: str,
    target: Optional["LapSegment"] = None,
) -> Optional[float]:
    values = telemetry.channels.get(name)
    if values is None:
        return None
    numeric = values.astype(float, copy=False)
    if target is not None and len(numeric):
        start = max(0, int(target.start_idx))
        end = min(len(numeric), int(target.end_idx) + 1)
        if end > start:
            numeric = numeric[start:end]
    numeric = numeric[np.isfinite(numeric)]
    return float(np.nanmedian(numeric)) if len(numeric) else None


def _normalize_percent(value: Optional[float]) -> Optional[float]:
    if value is None or not math.isfinite(float(value)):
        return None
    numeric = float(value)
    if abs(numeric) <= 1.5:
        numeric *= 100.0
    return max(0.0, min(100.0, numeric))


def telemetry_reference_conditions(
    telemetry: ParsedTelemetry,
    target: Optional["LapSegment"] = None,
) -> dict[str, Any]:
    track_temp = _finite_channel_median(telemetry, "TrackTempCrew", target)
    if track_temp is None:
        track_temp = _finite_channel_median(telemetry, "TrackTemp", target)
    air_temp = _finite_channel_median(telemetry, "AirTemp", target)
    humidity = _normalize_percent(_finite_channel_median(telemetry, "RelativeHumidity", target))
    pressure_pa = _finite_channel_median(telemetry, "AirPressure", target)
    air_density = _finite_channel_median(telemetry, "AirDensity", target)
    wind_mps = _finite_channel_median(telemetry, "WindVel", target)
    wind_dir_rad = _finite_channel_median(telemetry, "WindDir", target)
    precipitation = _normalize_percent(_finite_channel_median(telemetry, "Precipitation", target))
    fog_level = _normalize_percent(_finite_channel_median(telemetry, "FogLevel", target))
    wet_code = _finite_channel_median(telemetry, "TrackWetness", target)
    declared_wet = _finite_channel_median(telemetry, "WeatherDeclaredWet", target)
    skies = _finite_channel_median(telemetry, "Skies", target)
    tire_compound = _finite_channel_median(telemetry, "TireCompound", target)
    if tire_compound is None:
        tire_compound = _finite_channel_median(telemetry, "PlayerTireCompound", target)

    fuel_l: Optional[float] = None
    fuel_used_l: Optional[float] = None
    fuel = telemetry.channels.get("FuelLevel")
    if fuel is not None and len(fuel):
        if target is not None and 0 <= target.start_idx < len(fuel):
            start_value = float(fuel[target.start_idx])
            fuel_l = start_value if math.isfinite(start_value) else None
            if 0 <= target.end_idx < len(fuel):
                end_value = float(fuel[target.end_idx])
                if math.isfinite(start_value) and math.isfinite(end_value):
                    consumed = start_value - end_value
                    # Reject refuel/noise artifacts. A full lap should never consume
                    # a negative amount and values above 25 L/lap are not useful for
                    # condition matching.
                    if -0.05 <= consumed <= 25.0:
                        fuel_used_l = max(0.0, consumed)
        else:
            numeric = fuel.astype(float, copy=False)
            numeric = numeric[np.isfinite(numeric)]
            fuel_l = float(np.nanmax(numeric)) if len(numeric) else None

    sky_labels = {0: "Clear", 1: "Partly cloudy", 2: "Mostly cloudy", 3: "Overcast"}
    sky_code = int(round(skies)) if skies is not None and math.isfinite(skies) else None
    return {
        "track_temp_c": round_or_none(track_temp, 2),
        "air_temp_c": round_or_none(air_temp, 2),
        "relative_humidity_pct": round_or_none(humidity, 1),
        "air_pressure_hpa": round_or_none(pressure_pa / 100.0, 1) if pressure_pa is not None else None,
        "air_density_kg_m3": round_or_none(air_density, 3),
        "wind_speed_kph": round_or_none(wind_mps * 3.6, 1) if wind_mps is not None else None,
        "wind_direction_deg": round_or_none((math.degrees(wind_dir_rad) % 360.0), 1) if wind_dir_rad is not None else None,
        "precipitation_pct": round_or_none(precipitation, 1),
        "fog_level_pct": round_or_none(fog_level, 1),
        "track_wetness_code": int(round(wet_code)) if wet_code is not None and math.isfinite(wet_code) else None,
        "weather_declared_wet": bool(declared_wet >= 0.5) if declared_wet is not None else None,
        "skies_code": sky_code,
        "weather": sky_labels.get(sky_code) if sky_code is not None else None,
        "fuel_l": round_or_none(fuel_l, 2),
        "fuel_used_l": round_or_none(fuel_used_l, 3),
        "weight_penalty_kg": round_or_none(telemetry.metadata.get("weight_penalty_kg"), 2),
        "power_adjust_pct": round_or_none(telemetry.metadata.get("power_adjust_pct"), 3),
        "tire_compound": int(round(tire_compound)) if tire_compound is not None and math.isfinite(tire_compound) else None,
    }


def _numeric_similarity(reference: Any, current: Any, full_score_delta: float, zero_score_delta: float) -> Optional[float]:
    try:
        left = float(reference)
        right = float(current)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(left) and math.isfinite(right)):
        return None
    delta = abs(left - right)
    if delta <= full_score_delta:
        return 1.0
    if delta >= zero_score_delta:
        return 0.0
    return 1.0 - (delta - full_score_delta) / max(1e-9, zero_score_delta - full_score_delta)


def _circular_direction_similarity(reference_deg: Any, current_deg: Any) -> Optional[float]:
    try:
        left = float(reference_deg) % 360.0
        right = float(current_deg) % 360.0
    except (TypeError, ValueError):
        return None
    delta = abs(left - right)
    delta = min(delta, 360.0 - delta)
    return max(0.0, 1.0 - delta / 120.0)


def reference_condition_similarity(reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    components: list[tuple[str, float, Optional[float], str]] = [
        ("track_temp", 0.20, _numeric_similarity(reference.get("track_temp_c"), current.get("track_temp_c"), 2.0, 15.0), "노면온도"),
        ("air_temp", 0.10, _numeric_similarity(reference.get("air_temp_c"), current.get("air_temp_c"), 2.0, 12.0), "기온"),
        ("fuel", 0.22, _numeric_similarity(reference.get("fuel_l"), current.get("fuel_l"), 3.0, 35.0), "연료"),
        ("fuel_used", 0.04, _numeric_similarity(reference.get("fuel_used_l"), current.get("fuel_used_l"), 0.35, 2.0), "랩 연료소모"),
        ("humidity", 0.06, _numeric_similarity(reference.get("relative_humidity_pct"), current.get("relative_humidity_pct"), 8.0, 45.0), "습도"),
        ("fog", 0.02, _numeric_similarity(reference.get("fog_level_pct"), current.get("fog_level_pct"), 5.0, 35.0), "안개"),
        ("pressure", 0.04, _numeric_similarity(reference.get("air_pressure_hpa"), current.get("air_pressure_hpa"), 5.0, 35.0), "기압"),
        ("air_density", 0.04, _numeric_similarity(reference.get("air_density_kg_m3"), current.get("air_density_kg_m3"), 0.015, 0.12), "공기밀도"),
        ("wind_speed", 0.06, _numeric_similarity(reference.get("wind_speed_kph"), current.get("wind_speed_kph"), 4.0, 30.0), "풍속"),
        ("wind_direction", 0.03, _circular_direction_similarity(reference.get("wind_direction_deg"), current.get("wind_direction_deg")), "풍향"),
        ("precipitation", 0.10, _numeric_similarity(reference.get("precipitation_pct"), current.get("precipitation_pct"), 1.0, 20.0), "강수"),
        ("skies", 0.03, _numeric_similarity(reference.get("skies_code"), current.get("skies_code"), 0.0, 3.0), "운량"),
    ]
    weighted = 0.0
    weight_sum = 0.0
    reasons: list[str] = []
    available = 0
    setup_compatible = True
    setup_incompatibility_reasons: list[str] = []
    for key, weight, score, label in components:
        if score is None:
            continue
        available += 1
        weighted += weight * score
        weight_sum += weight
        if score >= 0.85:
            reasons.append(f"{label} 유사")
        elif score <= 0.25:
            reasons.append(f"{label} 차이 큼")

    ref_wet = reference.get("track_wetness_pct")
    if ref_wet is None:
        ref_wet = reference.get("weather_declared_wet")
    current_wet = current.get("weather_declared_wet")
    wet_score: Optional[float] = None
    if isinstance(ref_wet, bool) and current_wet is not None:
        wet_score = 1.0 if bool(ref_wet) == bool(current_wet) else 0.0
    elif ref_wet is not None and current_wet is not None:
        try:
            wet_score = 1.0 if (float(ref_wet) <= 1.0) == (not bool(current_wet)) else 0.0
        except (TypeError, ValueError):
            wet_score = None
    if wet_score is not None:
        available += 1
        weighted += 0.12 * wet_score
        weight_sum += 0.12
        reasons.append("Dry/Wet 일치" if wet_score >= 0.5 else "Dry/Wet 불일치")
        if wet_score < 0.5:
            setup_compatible = False
            setup_incompatibility_reasons.append("Dry/Wet 조건 불일치")

    # Garage61 API exposes BOP and tire metadata. When the IBT build exposes
    # matching current-session values, use them as additional compatibility
    # evidence instead of mixing fundamentally different performance states.
    for ref_key, current_key, weight, full_delta, zero_delta, label in [
        ("weight_penalty_kg", "weight_penalty_kg", 0.05, 0.1, 20.0, "BOP 중량"),
        ("power_adjust_pct", "power_adjust_pct", 0.05, 0.05, 5.0, "BOP 출력"),
    ]:
        bop_score = _numeric_similarity(reference.get(ref_key), current.get(current_key), full_delta, zero_delta)
        if bop_score is None:
            continue
        available += 1
        weighted += weight * bop_score
        weight_sum += weight
        if bop_score >= 0.90:
            reasons.append(f"{label} 일치")
        elif bop_score <= 0.35:
            reasons.append(f"{label} 차이 큼")

    # BOP differences can create pace that setup tuning cannot reproduce. Keep
    # such laps available for display, but never let them drive a setup decision.
    try:
        if reference.get("weight_penalty_kg") is not None and current.get("weight_penalty_kg") is not None:
            if abs(float(reference.get("weight_penalty_kg")) - float(current.get("weight_penalty_kg"))) > 2.0:
                setup_compatible = False
                setup_incompatibility_reasons.append("BOP 중량 차이 > 2 kg")
    except (TypeError, ValueError):
        pass
    try:
        if reference.get("power_adjust_pct") is not None and current.get("power_adjust_pct") is not None:
            if abs(float(reference.get("power_adjust_pct")) - float(current.get("power_adjust_pct"))) > 0.25:
                setup_compatible = False
                setup_incompatibility_reasons.append("BOP 출력 차이 > 0.25%")
    except (TypeError, ValueError):
        pass

    ref_tire = reference.get("tire_compound")
    current_tire = current.get("tire_compound")
    if ref_tire is not None and current_tire is not None:
        tire_score = 1.0 if str(ref_tire) == str(current_tire) else 0.0
        available += 1
        weighted += 0.05 * tire_score
        weight_sum += 0.05
        reasons.append("타이어 컴파운드 일치" if tire_score else "타이어 컴파운드 불일치")
        if not tire_score:
            setup_compatible = False
            setup_incompatibility_reasons.append("타이어 컴파운드 불일치")

    score = weighted / weight_sum if weight_sum else 0.50
    expected_fields = [
        "track_temp_c", "air_temp_c", "fuel_l", "fuel_used_l",
        "relative_humidity_pct", "fog_level_pct", "air_pressure_hpa",
        "air_density_kg_m3", "wind_speed_kph", "wind_direction_deg",
        "precipitation_pct", "skies_code",
    ]
    expected = sum(1 for key in expected_fields if current.get(key) is not None)
    if current.get("weather_declared_wet") is not None:
        expected += 1
    if current.get("weight_penalty_kg") is not None:
        expected += 1
    if current.get("power_adjust_pct") is not None:
        expected += 1
    if current.get("tire_compound") is not None:
        expected += 1
    coverage = min(1.0, available / max(1.0, float(expected)))
    # Sparse reference metadata should not be treated as highly precise even
    # when the fields that are present happen to match.
    adjusted = 0.50 + (score - 0.50) * (0.45 + 0.55 * coverage)
    return {
        "score": round(max(0.0, min(1.0, adjusted)), 3),
        "raw_score": round(max(0.0, min(1.0, score)), 3),
        "coverage": round(coverage, 3),
        "available_components": available,
        "reasons": reasons[:8],
        "setup_compatible": bool(setup_compatible),
        "setup_incompatibility_reasons": setup_incompatibility_reasons[:4],
    }

def _context_token(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold())


_TRACK_GENERIC_WORDS = (
    "international", "speedway", "circuit", "raceway", "racing", "course",
)


def _track_identity(value: Any, config: Any = "") -> str:
    """Normalize Garage61/iRacing display-name differences without relaxing layout matching.

    iRacing may report e.g. ``Fuji Speedway`` / path ``fuji nochicane`` while
    Garage61 exports ``Fuji International Speedway``.  Generic venue words and
    the separately checked config suffix are removed before comparing the base
    track identity.
    """

    token = _context_token(value)
    config_token = _context_token(config)
    if config_token and token.endswith(config_token):
        token = token[: -len(config_token)]
    for word in _TRACK_GENERIC_WORDS:
        token = token.replace(_context_token(word), "")
    return token


# iRacing does not populate TrackConfigName for every circuit layout.  The
# Garage61 collector already resolves these exact internal track-path aliases
# before selecting a track.  The reference reader must use the same identity
# logic; otherwise a successfully downloaded CSV can be rejected immediately
# after collection (for example ``lemans full`` vs ``24 Heures du Mans``).
_TELEMETRY_TRACK_CONFIG_ALIASES: dict[tuple[str, str], str] = {
    (_context_token("Autodromo Nazionale Monza"), _context_token("monza full")): "Grand Prix",
    (_context_token("Hockenheimring Baden-Württemberg"), _context_token("hockenheim gp")): "Grand Prix",
    (_context_token("Fuji Speedway"), _context_token("fuji no chicane")): "No Chicane",
    (_context_token("Fuji International Speedway"), _context_token("fuji no chicane")): "No Chicane",
    (_context_token("Circuit des 24 Heures du Mans"), _context_token("lemans full")): "24 Heures du Mans",
}


def _resolved_telemetry_track_config(telemetry_metadata: dict[str, Any]) -> str:
    explicit = str(telemetry_metadata.get("track_config") or "").strip()
    if explicit:
        return explicit
    key = (
        _context_token(telemetry_metadata.get("track_name")),
        _context_token(telemetry_metadata.get("track_path")),
    )
    return _TELEMETRY_TRACK_CONFIG_ALIASES.get(key, "")


def _identity_id(value: Any) -> str:
    return str(value or "").strip().casefold()


def external_reference_compatibility(reference: dict[str, Any], telemetry_metadata: dict[str, Any]) -> dict[str, Any]:
    """Match a Garage61 reference to the current IBT, ID-first.

    Garage61 API exposes the simulator platform IDs for both car and track.
    iRacing TrackID identifies the exact layout, so equal platform IDs are a
    stronger identity signal than display strings such as `BMW M4 GT3 EVO` vs
    `BMW M4 GT3` or layout aliases. Legacy browser CSVs without IDs continue to
    use the conservative name/config matcher.
    """

    ref_car_id = _identity_id(reference.get("car_platform_id"))
    cur_car_id = _identity_id(telemetry_metadata.get("car_platform_id"))
    ref_track_id = _identity_id(reference.get("track_platform_id"))
    cur_track_id = _identity_id(telemetry_metadata.get("track_platform_id"))

    car_id_known = bool(ref_car_id and cur_car_id)
    track_id_known = bool(ref_track_id and cur_track_id)
    if car_id_known and ref_car_id != cur_car_id:
        return {"compatible": False, "method": "PLATFORM_ID", "reason": "CAR_PLATFORM_ID_MISMATCH"}
    if track_id_known and ref_track_id != cur_track_id:
        return {"compatible": False, "method": "PLATFORM_ID", "reason": "TRACK_PLATFORM_ID_MISMATCH"}
    if car_id_known and track_id_known:
        return {"compatible": True, "method": "PLATFORM_ID", "reason": "EXACT_CAR_TRACK_PLATFORM_ID"}

    car_ref = _context_token(reference.get("car_name"))
    car_candidates = {
        _context_token(telemetry_metadata.get("car_name")),
        _context_token(telemetry_metadata.get("car_path")),
    }
    car_candidates.discard("")

    resolved_config = _resolved_telemetry_track_config(telemetry_metadata)
    config_ref = _context_token(reference.get("track_config"))
    config_current = _context_token(resolved_config)

    track_ref = _track_identity(reference.get("track_name"), reference.get("track_config"))
    track_candidates = {
        _track_identity(telemetry_metadata.get("track_name"), resolved_config),
        _track_identity(telemetry_metadata.get("track_path"), resolved_config),
    }
    track_candidates.discard("")

    car_match = car_id_known or car_ref in car_candidates
    if not car_match and car_ref:
        car_match = any(
            min(len(car_ref), len(candidate)) >= 8
            and (car_ref in candidate or candidate in car_ref)
            for candidate in car_candidates
        )

    if track_id_known:
        track_match = True
    else:
        track_match = bool(track_ref and track_ref in track_candidates and config_ref == config_current)

    if car_match and track_match:
        method = "MIXED_ID_NAME" if (car_id_known or track_id_known) else "LEGACY_NAME_CONFIG"
        return {"compatible": True, "method": method, "reason": "MATCHED"}

    if not car_match:
        reason = "CAR_NAME_MISMATCH"
    elif not track_match:
        reason = "TRACK_LAYOUT_NAME_MISMATCH"
    else:
        reason = "IDENTITY_MISMATCH"
    return {"compatible": False, "method": "LEGACY_NAME_CONFIG", "reason": reason}


def external_reference_compatible(reference: dict[str, Any], telemetry_metadata: dict[str, Any]) -> bool:
    return bool(external_reference_compatibility(reference, telemetry_metadata).get("compatible"))


@dataclass
class ExternalReferenceLap:
    path: Path
    metadata: dict[str, Any]
    distance_pct: np.ndarray[Any, Any]
    sample_time_s: np.ndarray[Any, Any]
    channels: dict[str, np.ndarray[Any, Any]]

    def grid(self, points: int, track_length_m: Optional[float] = None) -> dict[str, np.ndarray[Any, Any]]:
        count = max(500, int(points))
        grid = np.linspace(0.0, 1.0, count)
        output: dict[str, np.ndarray[Any, Any]] = {"distance_pct": grid}

        def interpolate(values: np.ndarray[Any, Any]) -> Optional[np.ndarray[Any, Any]]:
            numeric = values.astype(float, copy=False)
            valid = np.isfinite(numeric) & np.isfinite(self.distance_pct)
            if np.count_nonzero(valid) < 2:
                return None
            return interpolate_observed(self.distance_pct, numeric, grid, track_length_m or self.metadata.get("track_length_m"), self.sample_time_s)

        time_values = interpolate(self.sample_time_s)
        if time_values is None:
            time_values = np.full(count, np.nan)
        output["time_s"] = time_values
        for name, values in self.channels.items():
            interpolated = interpolate_observed(self.distance_pct, values, grid, track_length_m or self.metadata.get("track_length_m"), self.sample_time_s, stepped=name in {"Gear", "BrakeABSactive"})
            if interpolated is not None:
                output[name] = interpolated
        if "Speed" in output:
            output["speed_kph"] = output["Speed"] * 3.6
        output["_coverage"] = np.isfinite(output["time_s"]) & np.isfinite(output.get("speed_kph", np.full(count, np.nan)))
        output["_channel_support"] = {key: np.isfinite(value) for key, value in output.items() if isinstance(value, np.ndarray) and key != "distance_pct" and not key.startswith("_")}
        return output


def interpolate_observed(distance: np.ndarray, values: np.ndarray, grid: np.ndarray, track_length_m: Optional[float] = None, sample_times: Optional[np.ndarray] = None, *, stepped: bool = False) -> np.ndarray:
    """Interpolate short observed intervals only; never manufacture missing ends."""
    output = np.full(len(grid), np.nan)
    distance, values = np.asarray(distance, dtype=float), np.asarray(values, dtype=float)
    valid = np.isfinite(distance) & np.isfinite(values)
    if np.count_nonzero(valid) < 2:
        return output
    x, y = distance[valid], values[valid]
    order = np.argsort(x)
    x, y = x[order], y[order]
    unique = np.r_[True, np.diff(x) > 1e-9]
    x, y = x[unique], y[unique]
    if len(x) < 2:
        return output
    left = np.searchsorted(x, grid, side="right") - 1
    exact = np.searchsorted(x, grid, side="left")
    exact = np.clip(exact, 0, len(x) - 1)
    exact_match = np.isclose(grid, x[exact], rtol=0, atol=1e-10)
    interval = np.clip(left, 0, len(x) - 2)
    in_range = (grid >= x[0]) & (grid <= x[-1])
    length = float(track_length_m or 5000.0)
    supported = in_range & ((x[interval + 1] - x[interval]) * length <= 25.0)
    if sample_times is not None:
        times = np.asarray(sample_times, dtype=float)[valid][order][unique]
        time_gap = times[interval + 1] - times[interval]
        supported &= np.isfinite(time_gap) & (time_gap >= 0) & (time_gap <= 0.5)
    values_on_grid = y[np.clip(left, 0, len(y) - 1)] if stepped else np.interp(grid, x, y)
    output[supported | exact_match] = values_on_grid[supported | exact_match]
    return output


def _weighted_quantile_stack(
    stack: np.ndarray[Any, Any],
    weights: np.ndarray[Any, Any],
    quantile: float,
) -> np.ndarray[Any, Any]:
    """Column-wise weighted quantile with NaN-safe reference weights."""

    values = np.asarray(stack, dtype=float)
    if values.ndim != 2 or values.shape[0] == 0:
        return np.asarray([], dtype=float)
    row_weights = np.asarray(weights, dtype=float).reshape(-1)
    if len(row_weights) != values.shape[0]:
        row_weights = np.ones(values.shape[0], dtype=float)
    row_weights = np.where(np.isfinite(row_weights) & (row_weights > 0), row_weights, 0.0)
    if float(np.sum(row_weights)) <= 0:
        row_weights = np.ones(values.shape[0], dtype=float)

    order = np.argsort(np.where(np.isfinite(values), values, np.inf), axis=0)
    sorted_values = np.take_along_axis(values, order, axis=0)
    weight_matrix = np.broadcast_to(row_weights[:, None], values.shape)
    sorted_weights = np.take_along_axis(weight_matrix, order, axis=0)
    sorted_weights = np.where(np.isfinite(sorted_values), sorted_weights, 0.0)
    cumulative = np.cumsum(sorted_weights, axis=0)
    totals = cumulative[-1]
    thresholds = totals * max(0.0, min(1.0, float(quantile)))
    hits = cumulative >= thresholds[None, :]
    indices = np.argmax(hits, axis=0)
    result = sorted_values[indices, np.arange(values.shape[1])]
    result = np.where(totals > 0, result, np.nan)
    return result


def comparison_input_channels(target: dict[str, Any], reference: Optional[dict[str, Any]], index: int) -> dict[str, Any]:
    def value(grid: Optional[dict[str, Any]], channel: str, digits: int, degrees: bool = False) -> Optional[float]:
        values = grid.get(channel) if grid is not None else None
        if values is None or index >= len(values):
            return None
        result = float(values[index])
        return round_or_none(math.degrees(result) if degrees else result, digits)

    output = {}
    for prefix, grid in (("target", target), ("reference", reference)):
        for name, channel, digits, degrees in (("brake", "Brake", 3, False), ("throttle", "Throttle", 3, False), ("steering_deg", "SteeringWheelAngle", 1, True), ("gear", "Gear", 0, False)):
            output[f"{prefix}_{name}"] = value(grid, channel, digits, degrees)
    output.update(brake=output["target_brake"], throttle=output["target_throttle"], steering_deg=output["target_steering_deg"])
    return output


class Garage61ReferenceReader:
    NUMERIC_CHANNELS = [
        "Speed", "Brake", "Throttle", "RPM", "SteeringWheelAngle", "Gear",
        "Lat", "Lon", "LatAccel", "LongAccel", "VertAccel", "Yaw", "YawRate",
        "PositionType",
    ]
    BOOLEAN_CHANNELS = ["ABSActive", "DRSActive"]

    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return math.nan

    @staticmethod
    def _as_bool(value: Any) -> float:
        normalized = str(value if value is not None else "").strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return 1.0
        if normalized in {"0", "false", "no", "off"}:
            return 0.0
        return math.nan

    def parse(self) -> ExternalReferenceLap:
        metadata = garage61_reference_metadata(self.path)
        if metadata is None:
            raise ValueError(
                "Garage61 기본 파일명 형식을 인식할 수 없습니다. 파일명을 변경하지 않은 CSV를 사용하십시오."
            )
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            required = {"Speed", "LapDistPct", "Brake", "Throttle", "SteeringWheelAngle"}
            missing = sorted(required - fieldnames)
            if missing:
                raise ValueError("Garage61 CSV 필수 채널 누락: " + ", ".join(missing))
            distance: list[float] = []
            data: dict[str, list[float]] = {
                name: []
                for name in self.NUMERIC_CHANNELS + self.BOOLEAN_CHANNELS
                if name in fieldnames
            }
            time_channel = next((name for name in ("SessionTime", "Time", "TimeS", "TimeSeconds") if name in fieldnames), None)
            original_indices: list[float] = []
            original_times: list[float] = []
            original_time_origin: Optional[float] = None
            total_rows = 0
            for original_index, row in enumerate(reader):
                total_rows = original_index + 1
                row_time = self._as_float(row.get(time_channel)) if time_channel else math.nan
                if original_time_origin is None and math.isfinite(row_time):
                    original_time_origin = row_time
                lap_dist = self._as_float(row.get("LapDistPct"))
                if not math.isfinite(lap_dist):
                    continue
                distance.append(lap_dist)
                original_indices.append(float(original_index))
                original_times.append(row_time)
                for name in list(data):
                    if name in self.BOOLEAN_CHANNELS:
                        data[name].append(self._as_bool(row.get(name)))
                    else:
                        data[name].append(self._as_float(row.get(name)))

        if len(distance) < 300:
            raise ValueError("Garage61 CSV 표본 수가 너무 적습니다.")
        dist = np.asarray(distance, dtype=float)
        arrays = {name: np.asarray(values, dtype=float) for name, values in data.items()}
        arrays["_original_sample_index"] = np.asarray(original_indices, dtype=float)
        arrays["_source_time"] = np.asarray(original_times, dtype=float)
        timing_last_index = max(1, total_rows - 1)

        # Garage61 exports can include the first few samples of the next lap.
        # Keep the first full monotonic 0→1 traversal and discard the wrap tail.
        wraps = np.flatnonzero(np.diff(dist) < -0.50)
        if len(wraps):
            stop = int(wraps[0]) + 1
            timing_last_index = max(1, int(arrays["_original_sample_index"][stop - 1]))
            dist = dist[:stop]
            arrays = {name: values[:stop] for name, values in arrays.items()}
        monotonic = np.r_[True, dist[1:] > np.maximum.accumulate(dist[:-1]) + 1e-7]
        dist = dist[monotonic]
        arrays = {name: values[monotonic] for name, values in arrays.items()}

        # The API export includes PositionType. If a small pre/post-lap tail is
        # present, keep the on-track traversal only; never discard the lap when
        # PositionType is absent in an older export.
        position = arrays.get("PositionType")
        if position is not None:
            on_track = np.isfinite(position) & (np.rint(position).astype(int) == 3)
            if np.count_nonzero(on_track) >= 250:
                on_track_dist = dist[on_track]
                if len(on_track_dist) >= 250 and float(np.nanmax(on_track_dist) - np.nanmin(on_track_dist)) >= 0.90:
                    dist = on_track_dist
                    arrays = {name: values[on_track] for name, values in arrays.items()}

        if len(dist) < 250 or float(np.nanmax(dist) - np.nanmin(dist)) < 0.90:
            raise ValueError("Garage61 CSV가 한 바퀴 전체를 포함하지 않습니다.")

        # Garage61 API currently exports Speed in m/s. Keep a defensive unit
        # guard so a future/legacy km/h export cannot silently inflate speeds.
        if "Speed" in arrays:
            finite_speed = arrays["Speed"][np.isfinite(arrays["Speed"])]
            if len(finite_speed):
                p95 = float(np.nanpercentile(finite_speed, 95))
                if 130.0 < p95 < 500.0:
                    arrays["Speed"] = arrays["Speed"] / 3.6
                    metadata["speed_unit_normalized_from"] = "km/h"
                else:
                    metadata["speed_unit_normalized_from"] = "m/s"
                metadata["speed_p95_raw"] = round_or_none(p95, 3)
        metadata["telemetry_sample_count"] = int(len(dist))
        metadata["telemetry_distance_coverage"] = round_or_none(float(np.nanmax(dist) - np.nanmin(dist)), 4)
        metadata["observed_distance_range"] = [round_or_none(float(np.min(dist)), 6), round_or_none(float(np.max(dist)), 6)]
        metadata["channel_numeric_coverage"] = {key: round(float(np.mean(np.isfinite(values))), 4) for key, values in arrays.items() if not key.startswith("_")}

        lap_time_s = float(metadata["lap_time_s"])
        source_times = arrays.pop("_source_time")
        source_indices = arrays.pop("_original_sample_index")
        valid_times = np.isfinite(source_times)
        if np.count_nonzero(valid_times) >= 2 and np.all(np.diff(source_times[valid_times]) > 0):
            # Trust elapsed timestamps (in seconds) when consistent with lap duration.
            span = float(source_times[valid_times][-1] - source_times[valid_times][0])
            if 0.8 * lap_time_s <= span <= 1.2 * lap_time_s:
                sample_time = np.interp(source_indices, source_indices[valid_times], source_times[valid_times])
                sample_time -= original_time_origin if original_time_origin is not None else source_times[valid_times][0]
                metadata["timing_source"] = time_channel
            else:
                sample_time = source_indices / timing_last_index * lap_time_s
                metadata["timing_source"] = "ORIGINAL_SAMPLE_INDEX"
        else:
            sample_time = source_indices / timing_last_index * lap_time_s
            metadata["timing_source"] = "ORIGINAL_SAMPLE_INDEX"
        for name in required - {"LapDistPct"}:
            if np.count_nonzero(np.isfinite(arrays[name])) < 250:
                raise ValueError(f"Garage61 CSV channel {name} has insufficient numeric data")
        # Match IBT channel naming so the existing corner engine can be reused.
        if "ABSActive" in arrays:
            arrays["BrakeABSactive"] = arrays.pop("ABSActive")
        return ExternalReferenceLap(
            path=self.path,
            metadata=metadata,
            distance_pct=dist,
            sample_time_s=sample_time,
            channels=arrays,
        )


class ExternalReferenceRepository:
    def __init__(self, max_files: int = 12):
        self.max_files = max(3, int(max_files))
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, int, int], ExternalReferenceLap] = OrderedDict()

    def load(self, path: Path) -> ExternalReferenceLap:
        signature = file_signature(path)
        with self._lock:
            cached = self._cache.get(signature)
            if cached is not None:
                self._cache.move_to_end(signature)
                return cached
        parsed = Garage61ReferenceReader(path).parse()
        with self._lock:
            self._cache[signature] = parsed
            self._cache.move_to_end(signature)
            while len(self._cache) > self.max_files:
                self._cache.popitem(last=False)
        return parsed


def _external_reference_paths() -> list[Path]:
    if not EXTERNAL_REFERENCE_DIR.exists():
        return []
    paths = [path for path in EXTERNAL_REFERENCE_DIR.glob("*.csv") if path.is_file()]
    paths.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
    return paths[:EXTERNAL_REFERENCE_SCAN_LIMIT]


def resolve_external_reference_file(filename: str) -> Optional[Path]:
    wanted = Path(str(filename or "")).name
    if not wanted:
        return None
    for path in _external_reference_paths():
        if path.name == wanted:
            return path
    return None


_REFERENCE_VALIDATION_LOCK = threading.RLock()
_REFERENCE_VALIDATION_CACHE: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
# Reading every Garage61 lap to check it took about 18 s each time the app started (244 laps,
# 2026-10-14); the answers are kept in the data folder and only new or changed laps are read.
_REFERENCE_VALIDATION_DISK = JsonCache(MEMORY_DB_PATH.parent / "garage61-validation.json")


def external_reference_validation(path: Path) -> dict[str, Any]:
    signature = file_signature(path)
    with _REFERENCE_VALIDATION_LOCK:
        if signature in _REFERENCE_VALIDATION_CACHE:
            _REFERENCE_VALIDATION_CACHE.move_to_end(signature)
            return dict(_REFERENCE_VALIDATION_CACHE[signature])
    disk_key = JsonCache.key(path)
    result = _REFERENCE_VALIDATION_DISK.get(disk_key)
    if result is None:
        try:
            parsed = Garage61ReferenceReader(path).parse()
            result = {"usable": True, "validation_error": None, "timing_source": parsed.metadata.get("timing_source")}
        except Exception as exc:
            result = {"usable": False, "validation_error": str(exc), "timing_source": None}
        _REFERENCE_VALIDATION_DISK.put(disk_key, result)
    with _REFERENCE_VALIDATION_LOCK:
        _REFERENCE_VALIDATION_CACHE[signature] = result
        while len(_REFERENCE_VALIDATION_CACHE) > EXTERNAL_REFERENCE_SCAN_LIMIT:
            _REFERENCE_VALIDATION_CACHE.popitem(last=False)
    return dict(result)


def list_external_reference_metadata(telemetry: Optional[ParsedTelemetry] = None) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    current_best = None
    best_segment: Optional[LapSegment] = None
    if telemetry is not None:
        valid_laps = [lap for lap in telemetry.laps if lap.valid and lap.lap_time_s]
        best_segment = min(valid_laps, key=lambda lap: float(lap.lap_time_s or math.inf)) if valid_laps else None
        current_best = float(best_segment.lap_time_s) if best_segment is not None and best_segment.lap_time_s else None
    current_conditions = telemetry_reference_conditions(telemetry, best_segment) if telemetry else {}

    catalog = _reference_catalog()
    for path in _external_reference_paths():
        metadata = garage61_reference_metadata(path, catalog)
        if metadata is None:
            parse_errors.append({"name": path.name, "error": "Garage61 파일명 형식 불일치"})
            continue
        condition = reference_condition_similarity(metadata, current_conditions) if telemetry else {
            "score": 0.5, "coverage": 0.0, "available_components": 0, "reasons": []
        }
        lap_time_s = float(metadata.get("lap_time_s") or math.inf)
        faster_than_current = bool(current_best is not None and lap_time_s <= current_best - 0.03)
        pace_gap = (current_best - lap_time_s) if current_best is not None and math.isfinite(lap_time_s) else None
        identity = external_reference_compatibility(metadata, telemetry.metadata) if telemetry else {
            "compatible": None, "method": "NO_IBT", "reason": "NO_IBT"
        }
        validation = external_reference_validation(path)
        if not validation["usable"]:
            parse_errors.append({"name": path.name, "error": validation["validation_error"]})
        item = {
            **metadata,
            **validation,
            "lap_time": format_lap_time(metadata.get("lap_time_s")),
            "size_bytes": path.stat().st_size,
            "modified_at": path.stat().st_mtime,
            "compatible": identity.get("compatible"),
            "compatibility_method": identity.get("method"),
            "compatibility_reason": identity.get("reason"),
            "condition_similarity": condition,
            "faster_than_current_best": faster_than_current,
            "pace_gap_to_current_best_s": round_or_none(pace_gap, 3),
        }
        files.append(item)
    identity_rejections: dict[str, int] = {}
    identity_methods: dict[str, int] = {}
    prefilter_count = len(files)
    if telemetry is not None:
        for item in files:
            method = str(item.get("compatibility_method") or "UNKNOWN")
            identity_methods[method] = identity_methods.get(method, 0) + 1
            if not item.get("compatible"):
                reason = str(item.get("compatibility_reason") or "UNKNOWN")
                identity_rejections[reason] = identity_rejections.get(reason, 0) + 1
        files = [item for item in files if item.get("compatible")]

    def display_rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
        condition = item.get("condition_similarity") or {}
        setup_compatible = bool(condition.get("setup_compatible", True))
        faster = bool(item.get("faster_than_current_best"))
        selection_score = float(item.get("reference_selection_score") or condition.get("score") or 0.0)
        similarity = float(condition.get("score") or 0.0)
        coverage = float(condition.get("coverage") or 0.0)
        metadata_enriched = bool(item.get("metadata_enriched"))
        try:
            rating = float(item.get("driver_rating") or 0.0)
        except (TypeError, ValueError):
            rating = 0.0
        return (
            0 if setup_compatible else 1,
            0 if faster else 1,
            -selection_score,
            -similarity,
            -coverage,
            0 if metadata_enriched else 1,
            float(item.get("lap_time_s") or math.inf),
            -rating,
            item.get("driver_name") or "",
        )

    files.sort(key=display_rank_key)
    total_compatible = len(files)

    # The local library is intentionally allowed to grow without bound, but the UI
    # should remain a compact decision surface. Show at most nine references and
    # prefer one best lap per driver so old cache entries do not crowd out driver
    # diversity. If fewer than nine unique drivers exist, fill the remaining slots
    # with the next best-ranked laps.
    display_files: list[dict[str, Any]] = []
    deferred_duplicates: list[dict[str, Any]] = []
    displayed_drivers: set[str] = set()
    for item in files:
        driver = _context_token(item.get("driver_slug") or item.get("driver_name"))
        if driver and driver in displayed_drivers:
            deferred_duplicates.append(item)
            continue
        display_files.append(item)
        if driver:
            displayed_drivers.add(driver)
        if len(display_files) >= EXTERNAL_REFERENCE_DISPLAY_LIMIT:
            break
    if len(display_files) < min(EXTERNAL_REFERENCE_DISPLAY_LIMIT, total_compatible):
        for item in deferred_duplicates:
            display_files.append(item)
            if len(display_files) >= EXTERNAL_REFERENCE_DISPLAY_LIMIT:
                break

    for rank, item in enumerate(display_files, start=1):
        item["display_rank"] = rank

    # Collection health must still consider the complete compatible library, not
    # only the nine rows exposed to the UI. Otherwise hiding old rows could trigger
    # unnecessary recollection even when enough suitable references already exist.
    health_usable = [
        item for item in files
        if item.get("usable") and float((item.get("condition_similarity") or {}).get("score") or 0.0) >= GARAGE61_CONDITION_SCORE_MIN
        and (current_best is None or bool(item.get("faster_than_current_best")))
    ]
    health_distinct_drivers = {
        _context_token(item.get("driver_slug") or item.get("driver_name"))
        for item in health_usable
        if _context_token(item.get("driver_slug") or item.get("driver_name"))
    }

    # Auto cohort is selected only from the visible top-nine pool. This guarantees
    # that every automatically selected reference is also visible/editable in UI.
    recommended: list[str] = []
    seen_drivers: set[str] = set()
    for item in display_files:
        if not item.get("usable"):
            continue
        driver = _context_token(item.get("driver_slug") or item.get("driver_name"))
        if driver and driver in seen_drivers:
            continue
        recommended.append(str(item["name"]))
        if driver:
            seen_drivers.add(driver)
        if len(recommended) >= EXTERNAL_REFERENCE_RECOMMENDED_COHORT:
            break
    if len(recommended) < min(EXTERNAL_REFERENCE_RECOMMENDED_COHORT, len(display_files)):
        for item in display_files:
            name = str(item["name"])
            if item.get("usable") and name not in recommended:
                recommended.append(name)
            if len(recommended) >= EXTERNAL_REFERENCE_RECOMMENDED_COHORT:
                break

    enriched_count = sum(1 for item in files if item.get("metadata_enriched"))
    return {
        "directory": str(EXTERNAL_REFERENCE_DIR),
        "catalog_path": str(EXTERNAL_REFERENCE_CATALOG_PATH),
        "files": display_files,
        "recommended_selection": recommended,
        "recommended_cohort_size": EXTERNAL_REFERENCE_RECOMMENDED_COHORT,
        "display_limit": EXTERNAL_REFERENCE_DISPLAY_LIMIT,
        "displayed_reference_count": len(display_files),
        "display_policy": "TOP_SETUP_COMPATIBLE_FAST_CONDITION_MATCHED_UNIQUE_DRIVERS",
        "total_compatible": total_compatible,
        "health_usable_reference_count": len(health_usable),
        "health_usable_distinct_drivers": len(health_distinct_drivers),
        "enriched_metadata_count": enriched_count,
        "scanned_reference_count": prefilter_count,
        "identity_match_methods": identity_methods,
        "identity_rejections": identity_rejections,
        "parse_errors": parse_errors,
        "max_selection": EXTERNAL_REFERENCE_MAX_SELECTION,
        "minimum_for_setup_conclusion": EXTERNAL_REFERENCE_MIN_DECISION,
        "strong_consensus_count": EXTERNAL_REFERENCE_STRONG_DECISION,
        "aggregation": "CONDITION_WEIGHTED_DISTANCE_NORMALIZED_ROBUST_CONSENSUS",
        "single_reference_policy": "DISPLAY_ONLY_NO_SETUP_CONCLUSION",
        "two_reference_policy": "PROVISIONAL_SETUP_ASSIST_CONFIDENCE_CAPPED",
        "current_conditions": current_conditions,
    }


def _collection_bucket(value: Any, step: float) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "x"
    if not math.isfinite(numeric):
        return "x"
    return str(int(round(numeric / step) * step))


def garage61_collection_context(telemetry: ParsedTelemetry, filename: str) -> dict[str, Any]:
    valid_laps = [lap for lap in telemetry.laps if lap.valid and lap.lap_time_s]
    best_segment = min(valid_laps, key=lambda lap: float(lap.lap_time_s or math.inf)) if valid_laps else None
    conditions = telemetry_reference_conditions(telemetry, best_segment)
    best_lap = float(best_segment.lap_time_s) if best_segment is not None and best_segment.lap_time_s else None
    car_name = str(telemetry.metadata.get("car_name") or "")
    track_name = str(telemetry.metadata.get("track_name") or "")
    track_config = str(telemetry.metadata.get("track_config") or "")
    wet = conditions.get("weather_declared_wet")
    key_payload = {
        "car": _context_token(car_name),
        "car_platform_id": str(telemetry.metadata.get("car_platform_id") or ""),
        "track": _context_token(track_name),
        "track_platform_id": str(telemetry.metadata.get("track_platform_id") or ""),
        "config": _context_token(track_config),
        "track_temp_bucket": _collection_bucket(conditions.get("track_temp_c"), 5.0),
        "fuel_bucket": _collection_bucket(conditions.get("fuel_l"), 10.0),
        "weight_penalty_bucket": _collection_bucket(conditions.get("weight_penalty_kg"), 1.0),
        "power_adjust_bucket": _collection_bucket(conditions.get("power_adjust_pct"), 0.5),
        "tire_compound": conditions.get("tire_compound"),
        "wet": None if wet is None else bool(wet),
    }
    context_key = hashlib.sha256(
        json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "context_key": context_key,
        "filename": filename,
        "car_name": car_name,
        "car_path": telemetry.metadata.get("car_path") or "",
        "car_platform_id": telemetry.metadata.get("car_platform_id"),
        "track_name": track_name,
        "track_path": telemetry.metadata.get("track_path") or "",
        "track_platform_id": telemetry.metadata.get("track_platform_id"),
        "track_config": track_config,
        "resolved_track_config": _resolved_telemetry_track_config(telemetry.metadata),
        "session_setup_type": (
            2 if telemetry.metadata.get("is_fixed_setup") is True
            else 1 if telemetry.metadata.get("is_fixed_setup") is False
            else None
        ),
        "conditions": conditions,
        "current_best_lap_s": round_or_none(best_lap, 4),
    }


def _parse_iso_epoch(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        from datetime import datetime
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp()
    except Exception:
        return None


def _garage61_context_scan(context_key: str) -> dict[str, Any]:
    catalog = _read_json_object(EXTERNAL_REFERENCE_CATALOG_PATH)
    scans = catalog.get("context_scans") if isinstance(catalog, dict) else None
    if isinstance(scans, dict) and isinstance(scans.get(context_key), dict):
        return dict(scans[context_key])
    return {}


def garage61_reference_health(
    telemetry: ParsedTelemetry,
    filename: str,
    reference_payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    context = garage61_collection_context(telemetry, filename)
    payload = reference_payload or list_external_reference_metadata(telemetry)
    files = list(payload.get("files") or [])
    current_best = context.get("current_best_lap_s")
    usable: list[dict[str, Any]] = []
    for item in files:
        if not item.get("usable", False):
            continue
        score = float((item.get("condition_similarity") or {}).get("score") or 0.0)
        if score < GARAGE61_CONDITION_SCORE_MIN:
            continue
        if current_best is not None and not item.get("faster_than_current_best"):
            continue
        usable.append(item)
    distinct_drivers = {
        _context_token(item.get("driver_slug") or item.get("driver_name"))
        for item in usable
        if _context_token(item.get("driver_slug") or item.get("driver_name"))
    }
    usable_count = int(payload.get("health_usable_reference_count", len(usable)))
    distinct_driver_count = int(payload.get("health_usable_distinct_drivers", len(distinct_drivers)))
    scan = _garage61_context_scan(context["context_key"])
    scanned_at = _parse_iso_epoch(scan.get("scraped_at"))
    age_hours = None if scanned_at is None else max(0.0, (time.time() - scanned_at) / 3600.0)
    fresh = bool(age_hours is not None and age_hours <= GARAGE61_CACHE_FRESH_HOURS)
    enough_data = (
        usable_count >= GARAGE61_MIN_USABLE_REFERENCES
        and distinct_driver_count >= GARAGE61_MIN_DISTINCT_DRIVERS
    )
    needs_collection = not enough_data or not fresh
    reasons: list[str] = []
    if usable_count < GARAGE61_MIN_USABLE_REFERENCES:
        reasons.append(f"조건 유사 + 더 빠른 CSV {usable_count}/{GARAGE61_MIN_USABLE_REFERENCES}")
    if distinct_driver_count < GARAGE61_MIN_DISTINCT_DRIVERS:
        reasons.append(f"서로 다른 드라이버 {distinct_driver_count}/{GARAGE61_MIN_DISTINCT_DRIVERS}")
    if not fresh:
        reasons.append("동일 조건 수집 이력이 없거나 캐시가 오래됨")
    recommendation = "USE_CACHE"
    if needs_collection and enough_data:
        recommendation = "BACKGROUND_REFRESH"
    elif needs_collection:
        recommendation = "COLLECT_NOW"
    return {
        "enabled": GARAGE61_AUTO_COLLECT_ENABLED,
        "context": context,
        "compatible_count": int(payload.get("total_compatible", len(files))),
        "usable_reference_count": usable_count,
        "usable_distinct_drivers": distinct_driver_count,
        "minimum_usable_references": GARAGE61_MIN_USABLE_REFERENCES,
        "minimum_distinct_drivers": GARAGE61_MIN_DISTINCT_DRIVERS,
        "target_reference_count": GARAGE61_TARGET_REFERENCES,
        "condition_score_min": GARAGE61_CONDITION_SCORE_MIN,
        "cache_fresh_hours": GARAGE61_CACHE_FRESH_HOURS,
        "last_context_scan": scan or None,
        "last_context_scan_age_hours": round_or_none(age_hours, 1),
        "cache_fresh": fresh,
        "enough_data": enough_data,
        "needs_collection": bool(GARAGE61_AUTO_COLLECT_ENABLED and needs_collection),
        "reasons": reasons,
        "recommendation": recommendation,
        "collection_policy": {
            "blocking_analysis": False,
            "api_first": True,
            "browser_fallback": True,
            "cache_first": True,
            "preferred_mode": garage61_collector.preferred_mode(),
            "api_token_configured": garage61_collector.api_token_configured(),
            "api_candidate_limit": 1000,
            "condition_query_strategy": "STRICT_THEN_PROGRESSIVE_RELAXATION",
            "telemetry_validation": True,
            "navigation_timeout_ms_browser_fallback": GARAGE61_NAVIGATION_TIMEOUT_MS,
            "max_wall_seconds_browser_fallback": GARAGE61_COLLECTOR_MAX_WALL_SECONDS,
        },
    }


def garage61_collector_status_payload(
    telemetry: ParsedTelemetry,
    filename: str,
    reference_payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    health = garage61_reference_health(telemetry, filename, reference_payload)
    active = garage61_collector.active_job()
    return {
        "status": "success",
        "health": health,
        "job": active,
        "collector_capabilities": garage61_collector.capabilities(),
        "api_token_file": str(GARAGE61_API_TOKEN_FILE),
        "browser_profile": str(MEMORY_DB_PATH.parent / "Garage61_Browser_Profile"),
        "job_directory": str(GARAGE61_JOB_DIR),
    }


def _garage61_api_token_local() -> tuple[str, str]:
    token = os.getenv("GARAGE61_API_TOKEN", "").strip()
    if token:
        return token.removeprefix("Bearer ").strip(), "environment"
    candidates = [GARAGE61_API_TOKEN_FILE, APP_DIR / "garage61_api_token.txt"]
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8-sig").strip()
        except OSError:
            continue
        value = value.removeprefix("Authorization:").strip().removeprefix("Bearer ").strip()
        if value:
            return value, str(candidate)
    return "", ""


def garage61_api_connection_status() -> dict[str, Any]:
    token, source = _garage61_api_token_local()
    base = {
        "configured": bool(token),
        "connected": False,
        "token_source": "environment" if source == "environment" else (str(GARAGE61_API_TOKEN_FILE) if token else None),
        "preferred_mode": garage61_collector.preferred_mode(),
        "api_base": GARAGE61_API_BASE,
    }
    if not token:
        base["message"] = "Garage61 API token이 설정되지 않았습니다."
        return base
    req = urllib.request.Request(
        f"{GARAGE61_API_BASE}/me",
        headers={"Authorization": f"Bearer {token}", "User-Agent": f"iRacing-Setup-Tuner/{APP_VERSION}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
        base.update({
            "connected": True,
            "user_slug": payload.get("slug"),
            "subscription_plan": payload.get("subscriptionPlan"),
            "api_permissions": list(payload.get("apiPermissions") or []),
            "teams": [
                {"name": item.get("name"), "slug": item.get("slug")}
                for item in list(payload.get("teams") or [])
                if isinstance(item, dict)
            ],
            "driving_data": "driving_data" in list(payload.get("apiPermissions") or []),
            "message": "Garage61 API 연결 정상",
        })
        return base
    except urllib.error.HTTPError as exc:
        trace = exc.headers.get("X-G61-Trace", "")
        base.update({"http_status": exc.code, "trace": trace, "message": f"Garage61 API 인증 실패 HTTP {exc.code}"})
        return base
    except Exception as exc:
        base["message"] = f"Garage61 API 연결 확인 실패: {str(exc)[:200]}"
        return base



external_reference_repository = ExternalReferenceRepository()

class IBTFormatError(ValueError):
    pass


IBT_FILE_WAIT_MESSAGE = 'IBT 파일 읽기 대기 · iRacing이 기록 중이거나 다른 프로그램이 파일을 사용 중일 수 있습니다. 텔레메트리 기록을 종료하면 자동으로 다시 확인합니다. 계속 대기하면 파일의 읽기 권한을 확인하세요.'


class TelemetryFileBusy(PermissionError):
    def __init__(self, path):
        self.path = Path(path)
        super().__init__(IBT_FILE_WAIT_MESSAGE)


def waiting_file_visibility():
    return {'eligible': False, 'analyzable': False, 'comparison_ready': False,
            'coaching_ready': False, 'status': 'WAITING_FILE', 'retryable': True,
            'metadata_pending': True, 'reason': IBT_FILE_WAIT_MESSAGE, 'error': None,
            'detected_laps': None, 'complete_laps': None, 'valid_laps': None}


_BARE_QUESTION_MARK_VALUE = re.compile(
    r"(?m)^([ \t]*(?:-[ \t]+)?[A-Za-z0-9_][A-Za-z0-9_ ./'()\-]*:[ \t]*)(\?(?:[ \t].*)?)$"
)


def sanitize_iracing_session_yaml(text: str) -> tuple[str, int]:
    """Quote leading ``?`` placeholder scalar values emitted by iRacing.

    A line such as ``Initials: ?`` is not valid YAML because ``?`` is a
    mapping-key indicator. iRacing uses it as a placeholder value, so quoting
    only that scalar preserves the session data while allowing PyYAML to parse
    the full document, including CarSetup.
    """

    def quote_value(match: re.Match[str]) -> str:
        value = match.group(2)
        comment = re.search(r"[ \t]+#.*$", value)
        suffix = comment.group(0) if comment else ""
        if comment:
            value = value[:comment.start()]
        return match.group(1) + json.dumps(value.rstrip(), ensure_ascii=False) + suffix

    return _BARE_QUESTION_MARK_VALUE.subn(quote_value, text)


class IBTReader:
    """Reads every telemetry sample and only downsamples visualization payloads."""

    HEADER_SIZE = 112
    VAR_HEADER_SIZE = 144

    def __init__(self, path: Path):
        self.path = path
        self.file_size = path.stat().st_size

    def parse(self) -> ParsedTelemetry:
        with self.path.open("rb") as handle:
            header = handle.read(self.HEADER_SIZE)
            if len(header) < self.HEADER_SIZE:
                raise IBTFormatError("IBT header is truncated")

            (
                version,
                status,
                tick_rate,
                session_update,
                session_len,
                session_offset,
                num_vars,
                var_header_offset,
                num_buffers,
                buffer_len,
            ) = struct.unpack("<10i", header[:40])

            if version <= 0 or tick_rate <= 0 or buffer_len <= 0 or num_vars <= 0:
                raise IBTFormatError("IBT header contains invalid dimensions")

            data_offset = self._detect_data_offset(
                header=header,
                session_offset=session_offset,
                session_len=session_len,
                buffer_len=buffer_len,
            )

            handle.seek(session_offset)
            session_bytes = handle.read(session_len)
            session_text = session_bytes.decode("latin-1", errors="replace")
            parse_text, repaired_scalar_count = sanitize_iracing_session_yaml(session_text)
            try:
                session_info = yaml.load(parse_text, Loader=_YAML_LOADER) or {}
                if repaired_scalar_count:
                    logger.debug(
                        "Repaired %s bare '?' YAML value(s) in %s",
                        repaired_scalar_count,
                        self.path.name,
                    )
            except yaml.YAMLError as exc:
                logger.warning(
                    "Session YAML parse failed after safe scalar repair; using regex metadata: %s",
                    exc,
                )
                session_info = {}

            variables = self._read_variable_headers(handle, num_vars, var_header_offset)

        record_count = (self.file_size - data_offset) // buffer_len
        if record_count <= 0:
            raise IBTFormatError("IBT contains no telemetry records")

        channels = self._read_channels(
            variables=variables,
            data_offset=data_offset,
            buffer_len=buffer_len,
            record_count=record_count,
            requested=ANALYSIS_CHANNELS,
        )

        metadata = self._extract_metadata(session_info, session_text)
        setup = session_info.get("CarSetup", {}) if isinstance(session_info, dict) else {}
        laps = self._build_lap_segments(channels, tick_rate)
        signature = file_signature(self.path)

        return ParsedTelemetry(
            path=self.path,
            signature=signature,
            tick_rate=tick_rate,
            buffer_length=buffer_len,
            record_count=record_count,
            data_offset=data_offset,
            session_text=session_text,
            session_info=session_info,
            variables=variables,
            channels=channels,
            metadata=metadata,
            setup=setup if isinstance(setup, dict) else {},
            laps=laps,
        )

    def _detect_data_offset(
        self,
        header: bytes,
        session_offset: int,
        session_len: int,
        buffer_len: int,
    ) -> int:
        session_end = session_offset + session_len
        candidates = list(struct.unpack("<18i", header[40:112]))
        valid: list[tuple[int, int]] = []
        for candidate in candidates:
            if candidate < session_end or candidate >= self.file_size:
                continue
            remainder = (self.file_size - candidate) % buffer_len
            valid.append((0 if remainder == 0 else remainder, candidate))

        if not valid:
            # Most disk telemetry files align the data immediately after session info.
            aligned = session_end
            if aligned < self.file_size:
                return aligned
            raise IBTFormatError("Could not locate telemetry record buffer")

        valid.sort(key=lambda item: (item[0], abs(item[1] - session_end)))
        return valid[0][1]

    def _read_variable_headers(
        self,
        handle: Any,
        num_vars: int,
        offset: int,
    ) -> dict[str, VariableHeader]:
        variables: dict[str, VariableHeader] = {}
        handle.seek(offset)
        for _ in range(num_vars):
            raw = handle.read(self.VAR_HEADER_SIZE)
            if len(raw) < self.VAR_HEADER_SIZE:
                break
            var_type, var_offset, count, count_as_time = struct.unpack("<4i", raw[:16])
            name = raw[16:48].decode("latin-1", errors="ignore").rstrip("\x00").strip()
            description = raw[48:112].decode("latin-1", errors="ignore").rstrip("\x00").strip()
            unit = raw[112:144].decode("latin-1", errors="ignore").rstrip("\x00").strip()
            if name:
                variables[name] = VariableHeader(
                    var_type=var_type,
                    offset=var_offset,
                    count=count,
                    count_as_time=bool(count_as_time),
                    name=name,
                    description=description,
                    unit=unit,
                )
        return variables

    def _read_channels(
        self,
        variables: dict[str, VariableHeader],
        data_offset: int,
        buffer_len: int,
        record_count: int,
        requested: Iterable[str],
    ) -> dict[str, np.ndarray[Any, Any]]:
        mmap = np.memmap(self.path, mode="r", dtype=np.uint8)
        channels: dict[str, np.ndarray[Any, Any]] = {}
        for name in requested:
            header = variables.get(name)
            if not header or header.var_type not in IRSDK_DTYPES or header.count < 1:
                continue
            dtype = IRSDK_DTYPES[header.var_type]
            try:
                view = np.ndarray(
                    shape=(record_count,),
                    dtype=dtype,
                    buffer=mmap,
                    offset=data_offset + header.offset,
                    strides=(buffer_len,),
                )
                channels[name] = np.asarray(view).copy()
            except (ValueError, BufferError) as exc:
                logger.warning("Could not read channel %s: %s", name, exc)
        return channels

    def _extract_metadata(self, session_info: dict[str, Any], session_text: str) -> dict[str, Any]:
        weekend = session_info.get("WeekendInfo", {}) if isinstance(session_info, dict) else {}
        weekend_options = session_info.get("WeekendOptions", {}) if isinstance(session_info, dict) else {}
        driver_info = session_info.get("DriverInfo", {}) if isinstance(session_info, dict) else {}
        drivers = driver_info.get("Drivers", []) if isinstance(driver_info, dict) else []
        driver_idx = driver_info.get("DriverCarIdx", 0) if isinstance(driver_info, dict) else 0
        driver = next(
            (item for item in drivers if isinstance(item, dict) and item.get("CarIdx") == driver_idx),
            drivers[0] if drivers else {},
        )

        split_info = session_info.get("SplitTimeInfo", {}) if isinstance(session_info, dict) else {}
        sector_starts = []
        for sector in split_info.get("Sectors", []) if isinstance(split_info, dict) else []:
            if isinstance(sector, dict) and isinstance(sector.get("SectorStartPct"), (int, float)):
                sector_starts.append(float(sector["SectorStartPct"]))

        track_length_km = parse_number(weekend.get("TrackLength"))
        track_length_m = track_length_km * 1000.0 if track_length_km else None

        fixed_setup_raw = weekend_options.get("IsFixedSetup") if isinstance(weekend_options, dict) else None
        fixed_setup_value: Optional[bool]
        if fixed_setup_raw is None or str(fixed_setup_raw).strip() == "":
            fixed_setup_value = None
        else:
            fixed_setup_value = bool(safe_int(fixed_setup_raw) or 0)

        metadata = {
            "track_name": weekend.get("TrackDisplayName") or regex_value(session_text, r"TrackDisplayName:\s*([^\n]+)") or "Unknown Track",
            "track_config": weekend.get("TrackConfigName") or "",
            "track_path": weekend.get("TrackName") or "",
            "track_platform_id": str(weekend.get("TrackID") or "").strip() or None,
            "track_length_m": track_length_m,
            "track_turns": safe_int(weekend.get("TrackNumTurns")),
            "track_version": weekend.get("TrackVersion") or "",
            "car_name": driver.get("CarScreenName") or regex_value(session_text, r"CarScreenName:\s*([^\n]+)") or "Unknown Car",
            "car_path": driver.get("CarPath") or regex_value(session_text, r"CarPath:\s*([^\n]+)") or "",
            "car_platform_id": str(driver.get("CarID") or "").strip() or None,
            "weight_penalty_kg": parse_number(driver.get("CarClassWeightPenalty")),
            "power_adjust_pct": parse_number(driver.get("CarClassPowerAdjust")),
            "fuel_max_l": parse_number(driver_info.get("DriverCarFuelMaxLtr")),
            "setup_name": driver_info.get("DriverSetupName") or "Embedded setup",
            "setup_modified": bool(driver_info.get("DriverSetupIsModified", 0)),
            "setup_passed_tech": bool(driver_info.get("DriverSetupPassedTech", 0)),
            "estimated_lap_time_s": parse_number(driver_info.get("DriverCarEstLapTime")),
            "sector_starts": sorted(set(sector_starts)),
            "event_type": weekend.get("EventType") or "",
            "is_fixed_setup": fixed_setup_value,
        }
        # The session this file was driven in decides the analysis mode it starts with.
        metadata.update(session_context(session_info))
        metadata["suggested_session_mode"] = suggested_session_mode(
            metadata["session_type"], metadata["session_duration_s"], metadata["session_laps_limit"], metadata["estimated_lap_time_s"])
        return metadata

    def _build_lap_segments(
        self,
        channels: dict[str, np.ndarray[Any, Any]],
        tick_rate: int,
    ) -> list[LapSegment]:
        required = {"Lap", "LapDistPct", "SessionTime"}
        if not required.issubset(channels):
            return []

        lap_values = channels["Lap"].astype(np.int64, copy=False)
        starts = np.r_[0, np.flatnonzero(lap_values[1:] != lap_values[:-1]) + 1]
        ends = np.r_[starts[1:] - 1, len(lap_values) - 1]
        segments: list[LapSegment] = []

        for position, (start, end) in enumerate(zip(starts, ends)):
            lap_number = int(lap_values[start])
            if lap_number <= 0:
                continue

            lap_dist = channels["LapDistPct"][start : end + 1].astype(float, copy=False)
            start_pct = float(lap_dist[0]) if len(lap_dist) else 0.0
            end_pct = float(lap_dist[-1]) if len(lap_dist) else 0.0
            complete = start_pct <= 0.02 and end_pct >= 0.98

            pit_samples = int(np.count_nonzero(channels.get("OnPitRoad", np.zeros(len(lap_values), dtype=bool))[start : end + 1]))
            surface = channels.get("PlayerTrackSurface")
            if surface is not None:
                on_track_ratio = float(np.mean(surface[start : end + 1] == 3))
            else:
                on_track_ratio = 1.0

            official_time = self._completed_lap_time(
                channels=channels,
                current_end=int(end),
                next_start=int(starts[position + 1]) if position + 1 < len(starts) else None,
                tick_rate=tick_rate,
            )

            fuel_used = None
            fuel = channels.get("FuelLevel")
            if fuel is not None and end > start:
                value = float(fuel[start] - fuel[end])
                if math.isfinite(value) and value >= -0.1:
                    fuel_used = max(0.0, value)

            invalid_reason = None
            if not complete:
                status = "OUT_LAP" if pit_samples else "PARTIAL"
            elif pit_samples:
                status = "INVALID"
                invalid_reason = "pit road samples present"
            elif on_track_ratio < 0.995:
                status = "INVALID"
                invalid_reason = "off-track samples present"
            elif official_time is None or official_time <= 0:
                status = "INVALID"
                invalid_reason = "iRacing did not publish a valid completed lap time"
            else:
                status = "VALID"

            segments.append(
                LapSegment(
                    lap_number=lap_number,
                    start_idx=int(start),
                    end_idx=int(end),
                    complete=complete,
                    valid=status == "VALID",
                    status=status,
                    lap_time_s=official_time,
                    start_pct=start_pct,
                    end_pct=end_pct,
                    on_track_ratio=on_track_ratio,
                    pit_samples=pit_samples,
                    fuel_used_l=fuel_used,
                    invalid_reason=invalid_reason,
                )
            )
        return segments

    def _completed_lap_time(
        self,
        channels: dict[str, np.ndarray[Any, Any]],
        current_end: int,
        next_start: Optional[int],
        tick_rate: int,
    ) -> Optional[float]:
        values = channels.get("LapLastLapTime")
        if values is None or next_start is None:
            return None

        before = float(values[current_end])
        completed = channels.get("LapCompleted")
        completed_before = float(completed[current_end]) if completed is not None else None
        search_end = min(len(values), next_start + max(tick_rate * 2, 30))
        window = values[next_start:search_end].astype(float, copy=False)
        for offset, candidate in enumerate(window):
            if not math.isfinite(float(candidate)) or candidate <= 0:
                continue
            if before <= 0 or abs(float(candidate) - before) > 0.001:
                return float(candidate)
            # Equal consecutive lap times are valid when the completion counter
            # advances. Require current-lap elapsed time to agree so an invalid
            # lap cannot reuse a stale published time.
            if completed is not None and math.isfinite(completed_before):
                after = float(completed[next_start + offset])
                elapsed = channels.get("LapCurrentLapTime")
                measured = float(elapsed[current_end]) if elapsed is not None else math.nan
                tolerance = max(0.02, 2.0 / max(1, tick_rate))
                if after > completed_before and math.isfinite(measured) and abs(measured - float(candidate)) <= tolerance:
                    return float(candidate)
        return None


# ---------------------------------------------------------------------------
# Repository/cache
# ---------------------------------------------------------------------------


def file_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)


_FILE_HASH_LOCK = threading.RLock()
_FILE_HASH_CACHE: OrderedDict[tuple[str, int, int], str] = OrderedDict()


def file_sha256(path: Path) -> str:
    """Return a cached content hash so copied IBTs are not stored twice."""

    signature = file_signature(path)
    with _FILE_HASH_LOCK:
        cached = _FILE_HASH_CACHE.get(signature)
        if cached is not None:
            _FILE_HASH_CACHE.move_to_end(signature)
            return cached

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _FILE_HASH_LOCK:
        _FILE_HASH_CACHE[signature] = value
        _FILE_HASH_CACHE.move_to_end(signature)
        while len(_FILE_HASH_CACHE) > max(8, MAX_CACHE_FILES * 4):
            _FILE_HASH_CACHE.popitem(last=False)
    return value


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _list_raw_ibt_files(limit: Optional[int] = None) -> list[Path]:
    """Return IBT files without applying the minimum-lap visibility filter."""

    files: list[Path] = []
    for directory in TELEMETRY_DIRS:
        if not directory.exists():
            continue
        files.extend(Path(value) for value in glob.glob(str(directory / "*.[iI][bB][tT]")))
    unique = {str(path.resolve()): path for path in files if path.is_file()}
    ordered = sorted(unique.values(), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return ordered[:limit] if limit is not None else ordered


class TelemetryRepository:
    def __init__(self, max_files: int = MAX_CACHE_FILES):
        self.max_files = max_files
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, int, int], ParsedTelemetry] = OrderedDict()

    def load(self, path: Path) -> ParsedTelemetry:
        try:
            signature = file_signature(path)
        except PermissionError as exc:
            raise TelemetryFileBusy(path) from exc
        with self._lock:
            cached = self._cache.get(signature)
            if cached is not None:
                self._cache.move_to_end(signature)
                return cached

        try:
            parsed = IBTReader(path).parse()
        except PermissionError as exc:
            raise TelemetryFileBusy(path) from exc
        with self._lock:
            self._cache[signature] = parsed
            self._cache.move_to_end(signature)
            while len(self._cache) > self.max_files:
                self._cache.popitem(last=False)
        return parsed


repository = TelemetryRepository()

_FILE_VISIBILITY_LOCK = threading.RLock()
_FILE_VISIBILITY_CACHE: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
_FILE_METADATA_INDEX = FileMetadataIndex(Path(os.getenv("IRACING_FILE_INDEX_PATH", str(MEMORY_DB_PATH.parent / "telemetry-file-index.sqlite3"))))


def cached_file_visibility(path: Path) -> Optional[dict[str, Any]]:
    signature = file_signature(path)
    with _FILE_VISIBILITY_LOCK:
        cached = _FILE_VISIBILITY_CACHE.get(signature)
    if cached is not None:
        return dict(cached)
    try:
        return _FILE_METADATA_INDEX.get(signature)
    except (OSError, sqlite3.Error, ValueError):
        return None


def quick_file_visibility(path: Path) -> dict[str, Any]:
    """Read only the small IBT header/YAML; never scan sample channels for a list."""
    metadata: dict[str, Any] = {}
    try:
        cached = cached_file_visibility(path)
        if cached is not None:
            return cached
        reader = IBTReader(path)
        with path.open("rb") as handle:
            header = handle.read(reader.HEADER_SIZE)
            if len(header) != reader.HEADER_SIZE:
                raise IBTFormatError("IBT header is truncated")
            dimensions = struct.unpack("<10i", header[:40])
            length, offset = dimensions[4:6]
            if dimensions[0] <= 0 or dimensions[2] <= 0 or length <= 0 or length > 8 * 1024 * 1024 or offset < 0 or offset + length > reader.file_size:
                raise IBTFormatError("IBT session header contains invalid dimensions")
            handle.seek(offset)
            session_text = handle.read(length).decode("latin-1", errors="replace")
        safe_text, _ = sanitize_iracing_session_yaml(session_text)
        try:
            session_info = yaml.load(safe_text, Loader=_YAML_LOADER) or {}
        except yaml.YAMLError:
            session_info = {}
        metadata = reader._extract_metadata(session_info, session_text)
        payload = {"eligible": None, "analyzable": None, "comparison_ready": None,
                   "status": "PENDING", "metadata_pending": True, "reason": "파일을 선택하면 랩과 정상 구간을 분석합니다.", "error": None,
                   "detected_laps": None, "complete_laps": None, "valid_laps": None, **metadata}
    except PermissionError:
        # Unlocking need not change size/mtime. Never persist this transient state.
        return waiting_file_visibility()
    except (OSError, ValueError, struct.error) as exc:
        payload = {"eligible": False, "analyzable": False, "comparison_ready": False,
                   "status": "ERROR", "metadata_pending": False, "reason": str(exc), "error": str(exc),
                   "detected_laps": None, "complete_laps": None, "valid_laps": None, **metadata}
    try:
        _FILE_METADATA_INDEX.put(file_signature(path), payload)
    except (OSError, sqlite3.Error):
        logger.debug("File index update failed", exc_info=True)
    return payload


def telemetry_file_visibility(path: Path, telemetry: Optional[ParsedTelemetry] = None) -> dict[str, Any]:
    """Return availability and the reason comparison may be limited."""

    try:
        signature = file_signature(path)
        cached = cached_file_visibility(path)
        if telemetry is None and cached is not None and not cached.get("metadata_pending"):
            return cached
        telemetry = telemetry if telemetry is not None else repository.load(path)
        metadata = telemetry.metadata or {}
        detected_laps = len(telemetry.laps)
        complete_laps = sum(1 for lap in telemetry.laps if lap.complete)
        valid_laps = sum(1 for lap in telemetry.laps if lap.valid)
        clean = clean_segment_context(telemetry) if not valid_laps else {"coaching_ready": False, "segments": []}
        payload = {
            "eligible": valid_laps >= 1,
            "analyzable": valid_laps >= 1 or clean["coaching_ready"],
            "coaching_ready": valid_laps >= 1 or clean["coaching_ready"],
            "clean_segment_count": len(clean["segments"]),
            "metadata_pending": False,
            "comparison_ready": valid_laps >= 2,
            "status": "READY" if valid_laps >= 2 else "PARTIAL" if valid_laps == 1 else "COACHING_ONLY" if clean["coaching_ready"] else "UNAVAILABLE",
            "reason": None if valid_laps >= 2 else "유효 랩 1개: 로컬 랩 비교는 제한되지만 단일 랩 분석과 Garage61 비교는 가능합니다." if valid_laps == 1 else "유효한 완주 랩이 없습니다. 아웃랩·불완전 랩·무효 랩을 확인하세요.",
            "detected_laps": detected_laps,
            "complete_laps": complete_laps,
            "valid_laps": valid_laps,
            "minimum_required_laps": MIN_DISPLAY_LAPS,
            "minimum_required_valid_laps": MIN_DISPLAY_VALID_LAPS,
            "car_name": metadata.get("car_name") or "Unknown Car",
            "car_path": metadata.get("car_path") or "",
            "track_name": metadata.get("track_name") or "Unknown Track",
            "track_path": metadata.get("track_path") or "",
            "track_config": metadata.get("track_config") or "",
            "session_type": metadata.get("session_type"),
            "session_duration_s": metadata.get("session_duration_s"),
            "suggested_session_mode": metadata.get("suggested_session_mode"),
            "error": None,
        }
        if not valid_laps and clean["coaching_ready"]:
            payload["reason"] = clean["reason"]
    except PermissionError:
        return waiting_file_visibility()
    except Exception as exc:
        payload = {
            "eligible": False,
            "analyzable": False,
            "comparison_ready": False,
            "status": "ERROR",
            "metadata_pending": False,
            "reason": str(exc),
            "detected_laps": 0,
            "complete_laps": 0,
            "valid_laps": 0,
            "minimum_required_laps": MIN_DISPLAY_LAPS,
            "minimum_required_valid_laps": MIN_DISPLAY_VALID_LAPS,
            "car_name": "",
            "car_path": "",
            "track_name": "",
            "track_path": "",
            "track_config": "",
            "error": str(exc),
        }
        logger.info("IBT visibility check skipped %s: %s", path.name, exc)

    with _FILE_VISIBILITY_LOCK:
        _FILE_VISIBILITY_CACHE[signature] = dict(payload)
        _FILE_VISIBILITY_CACHE.move_to_end(signature)
        while len(_FILE_VISIBILITY_CACHE) > max(100, FILE_SCAN_LIMIT * 2):
            _FILE_VISIBILITY_CACHE.popitem(last=False)
    try:
        _FILE_METADATA_INDEX.put(signature, payload)
    except (OSError, sqlite3.Error):
        logger.debug("File index update failed", exc_info=True)
    return payload


def list_ibt_files(limit: int = 50) -> list[Path]:
    """Expose recent recordings, including limited and unreadable files."""
    return _list_raw_ibt_files(limit)


def resolve_telemetry_file(filename: Optional[str] = None) -> Optional[Path]:
    files = _list_raw_ibt_files(None if filename else FILE_SCAN_LIMIT)
    if filename:
        wanted = Path(filename).name
        return next((path for path in files if path.name == wanted), None)
    return next((path for path in files if telemetry_file_visibility(path).get("analyzable")), None)


shared_sync_state: dict[str, Any] = {
    "last_analysis_time": 0.0,
    "analysis_result": None,
    "file_signature": None,
}


def shared_state_summary() -> dict[str, Any]:
    # Status polls and the event stream must not carry the multi-megabyte last result.
    return {"last_analysis_time": shared_sync_state["last_analysis_time"],
            "file_signature": shared_sync_state["file_signature"],
            "analysis_available": shared_sync_state["analysis_result"] is not None}


class LongTermMemoryStore:
    """SQLite-backed personal race-engineer memory.

    The database stores compact deterministic analysis outputs rather than raw
    60 Hz telemetry. The original IBT remains the source of truth and is read
    again when detailed traces are needed.
    """

    SCHEMA_VERSION = 3

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        logger.info("Long-term memory database: %s", self.path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _loads(value: Optional[str], default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        if column not in self._columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS setups (
                    setup_hash TEXT PRIMARY KEY,
                    car_name TEXT,
                    setup_name TEXT,
                    setup_json TEXT NOT NULL,
                    setup_summary_json TEXT NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    file_modified_at REAL NOT NULL,
                    recorded_at REAL NOT NULL,
                    analyzed_at REAL NOT NULL,
                    analysis_version TEXT NOT NULL,
                    session_mode TEXT NOT NULL,
                    pit_rule TEXT NOT NULL,
                    car_name TEXT,
                    car_path TEXT,
                    track_name TEXT,
                    track_config TEXT,
                    track_temp_c REAL,
                    air_temp_c REAL,
                    setup_hash TEXT,
                    setup_name TEXT,
                    best_lap_s REAL,
                    valid_lap_count INTEGER NOT NULL DEFAULT 0,
                    complete_lap_count INTEGER NOT NULL DEFAULT 0,
                    out_lap_count INTEGER NOT NULL DEFAULT 0,
                    classification TEXT,
                    classification_confidence REAL,
                    setup_decision TEXT,
                    target_lap INTEGER,
                    reference_lap INTEGER,
                    user_feedback TEXT,
                    issue_tags_json TEXT NOT NULL DEFAULT '[]',
                    out_lap_json TEXT NOT NULL DEFAULT '{}',
                    memory_summary_json TEXT NOT NULL DEFAULT '{}',
                    analysis_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(file_hash, session_mode),
                    FOREIGN KEY(setup_hash) REFERENCES setups(setup_hash)
                );

                CREATE TABLE IF NOT EXISTS laps (
                    session_id TEXT NOT NULL,
                    lap_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    lap_time_s REAL,
                    delta_to_file_best_s REAL,
                    start_pct REAL,
                    end_pct REAL,
                    on_track_ratio REAL,
                    fuel_used_l REAL,
                    invalid_reason TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(session_id, lap_number),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS corner_metrics (
                    session_id TEXT NOT NULL,
                    lap_number INTEGER NOT NULL,
                    corner_id TEXT NOT NULL,
                    label TEXT,
                    speed_class TEXT,
                    entry_pct REAL,
                    apex_pct REAL,
                    exit_pct REAL,
                    delta_s REAL,
                    issue_type TEXT,
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, lap_number, corner_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS driver_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    feedback_text TEXT NOT NULL,
                    parsed_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS experiment_plans (
                    plan_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    filename TEXT NOT NULL,
                    session_mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    session_id TEXT,
                    decision TEXT,
                    candidate_id TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS experiment_results (
                    result_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    plan_id TEXT,
                    baseline_session_id TEXT,
                    candidate_session_id TEXT,
                    session_mode TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    reason TEXT,
                    median_delta_s REAL,
                    best_delta_s REAL,
                    stdev_delta_s REAL,
                    pace_slope_delta_s_per_lap REAL,
                    track_temp_delta_c REAL,
                    setup_diff_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES experiment_plans(plan_id) ON DELETE SET NULL,
                    FOREIGN KEY(baseline_session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
                    FOREIGN KEY(candidate_session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
                );


                CREATE TABLE IF NOT EXISTS setup_sensitivity_events (
                    event_id TEXT PRIMARY KEY,
                    result_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    car_name TEXT,
                    track_name TEXT,
                    track_config TEXT,
                    session_mode TEXT NOT NULL,
                    setting TEXT NOT NULL,
                    setting_label TEXT,
                    direction INTEGER NOT NULL,
                    baseline_value REAL,
                    candidate_value REAL,
                    delta_value REAL,
                    unit TEXT,
                    verdict TEXT NOT NULL,
                    median_delta_s REAL,
                    best_delta_s REAL,
                    track_temp_delta_c REAL,
                    confidence REAL NOT NULL,
                    confounded INTEGER NOT NULL DEFAULT 0,
                    effects_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(result_id, setting),
                    FOREIGN KEY(result_id) REFERENCES experiment_results(result_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_context
                    ON sessions(car_name, track_name, track_config, analyzed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_setup
                    ON sessions(setup_hash, analyzed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_mode
                    ON sessions(session_mode, analyzed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_corner_issue
                    ON corner_metrics(issue_type, speed_class);
                CREATE INDEX IF NOT EXISTS idx_experiment_results_context
                    ON experiment_results(verdict, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_setup_sensitivity_context
                    ON setup_sensitivity_events(car_name, setting, direction, created_at DESC);
                """
            )
            # Upgrade databases created by v2.1 without discarding plans.
            for column, declaration in [
                ("session_id", "TEXT"),
                ("decision", "TEXT"),
                ("candidate_id", "TEXT"),
            ]:
                self._ensure_column(connection, "experiment_plans", column, declaration)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            # Backfill older A/B results into the v3 sensitivity table. Existing
            # experiment_results already contain the exact setup diff and verdict, so
            # no raw telemetry is required for this migration.
            legacy_rows = connection.execute(
                """
                SELECT r.result_id, r.session_mode, r.setup_diff_json, r.result_json,
                       s.car_name, s.track_name, s.track_config
                FROM experiment_results r
                LEFT JOIN sessions s ON s.session_id=r.baseline_session_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM setup_sensitivity_events e WHERE e.result_id=r.result_id
                )
                ORDER BY r.created_at ASC
                """
            ).fetchall()
            for row in legacy_rows:
                comparison = self._loads(row[3], {})
                setup_diff = self._loads(row[2], {})
                full_setup_diff = setup_diff.get("full_setup") if isinstance(setup_diff, dict) else {}
                if not isinstance(full_setup_diff, dict):
                    full_setup_diff = {}
                try:
                    self._store_setup_sensitivity_events(
                        connection,
                        result_id=str(row[0]),
                        comparison=comparison,
                        baseline={"session": {"car_name": row[4], "track_name": row[5], "track_config": row[6]}},
                        candidate={},
                        session_mode=str(row[1] or "BASELINE_3LAP"),
                        full_setup_diff=full_setup_diff,
                    )
                except Exception as exc:
                    logger.debug("Sensitivity backfill skipped for %s: %s", row[0], exc)

    def import_legacy_plans(self, legacy_path: Path) -> int:
        if not legacy_path.exists() or legacy_path.resolve() == self.path.resolve():
            return 0
        imported = 0
        try:
            legacy = sqlite3.connect(legacy_path, timeout=5.0)
            rows = legacy.execute(
                "SELECT plan_id, created_at, filename, session_mode, source, plan_json FROM experiment_plans"
            ).fetchall()
            legacy.close()
            with self._lock, self._connect() as connection:
                for row in rows:
                    payload = self._loads(row[5], {})
                    before = connection.total_changes
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO experiment_plans
                        (plan_id, created_at, filename, session_mode, source, plan_json,
                         session_id, decision, candidate_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row[0], row[1], row[2], row[3], row[4], row[5],
                            payload.get("session_id"), payload.get("decision"), payload.get("candidate_id"),
                        ),
                    )
                    imported += int(connection.total_changes > before)
        except Exception as exc:
            logger.info("Legacy plan import skipped: %s", exc)
        return imported

    def status(self) -> dict[str, Any]:
        counts = {
            "sessions": 0,
            "setups": 0,
            "laps": 0,
            "corner_metrics": 0,
            "driver_feedback": 0,
            "experiment_plans": 0,
            "experiment_results": 0,
            "setup_sensitivity_events": 0,
        }
        latest_at: Optional[float] = None
        with self._lock, self._connect() as connection:
            for table in counts:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            row = connection.execute("SELECT MAX(analyzed_at) FROM sessions").fetchone()
            latest_at = float(row[0]) if row and row[0] is not None else None
            schema_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
        database_bytes = self.path.stat().st_size if self.path.exists() else 0
        wal_path = Path(f"{self.path}-wal")
        shm_path = Path(f"{self.path}-shm")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        shm_bytes = shm_path.stat().st_size if shm_path.exists() else 0
        return {
            "enabled": True,
            "provider": "LOCAL_SQLITE",
            "path": str(self.path),
            "directory": str(self.path.parent),
            "exists": self.path.exists(),
            "size_bytes": database_bytes + wal_bytes + shm_bytes,
            "database_file_bytes": database_bytes,
            "wal_file_bytes": wal_bytes,
            "shared_memory_file_bytes": shm_bytes,
            "schema_version": int(schema_row[0]) if schema_row else self.SCHEMA_VERSION,
            "counts": counts,
            "latest_analysis_at": latest_at,
            "raw_telemetry_stored": False,
            "storage_policy": "COMPACT_ANALYSIS_ONLY",
        }

    def assigned_modes(self, file_hash: str) -> list[str]:
        """Return stored session classifications for one IBT, newest first."""
        if not file_hash:
            return []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_mode, MAX(analyzed_at) AS latest_at
                FROM sessions
                WHERE file_hash=?
                GROUP BY session_mode
                ORDER BY latest_at DESC
                """,
                (file_hash,),
            ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def session_exists(self, session_id: Optional[str]) -> bool:
        if not session_id:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id=? LIMIT 1",
                (session_id,),
            ).fetchone()
        return row is not None

    def save_analysis(
        self,
        snapshot: dict[str, Any],
        replace_existing_mode: bool = False,
    ) -> dict[str, Any]:
        session = snapshot["session"]
        setup = snapshot["setup"]
        now = float(session["analyzed_at"])
        target_mode = str(session["session_mode"])
        file_hash = str(session["file_hash"])
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_modes = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT session_mode FROM sessions WHERE file_hash=?",
                    (file_hash,),
                ).fetchall()
                if row[0]
            ]
            conflicting_modes = [mode for mode in existing_modes if mode != target_mode]
            if conflicting_modes and not replace_existing_mode:
                raise ValueError(
                    "SESSION_MODE_CONFLICT: "
                    + ", ".join(conflicting_modes)
                    + f" -> {target_mode}"
                )
            if replace_existing_mode and conflicting_modes:
                connection.execute(
                    "DELETE FROM sessions WHERE file_hash=? AND session_mode<>?",
                    (file_hash, target_mode),
                )
            connection.execute(
                """
                INSERT INTO setups
                (setup_hash, car_name, setup_name, setup_json, setup_summary_json,
                 first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(setup_hash) DO UPDATE SET
                    car_name=excluded.car_name,
                    setup_name=excluded.setup_name,
                    setup_json=excluded.setup_json,
                    setup_summary_json=excluded.setup_summary_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    setup["setup_hash"], session.get("car_name"), session.get("setup_name"),
                    self._json(setup.get("setup_json", {})),
                    self._json(setup.get("setup_summary", {})), now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO sessions
                (session_id, file_hash, filename, file_path, file_size_bytes,
                 file_modified_at, recorded_at, analyzed_at, analysis_version,
                 session_mode, pit_rule, car_name, car_path, track_name, track_config,
                 track_temp_c, air_temp_c, setup_hash, setup_name, best_lap_s,
                 valid_lap_count, complete_lap_count, out_lap_count, classification,
                 classification_confidence, setup_decision, target_lap, reference_lap,
                 user_feedback, issue_tags_json, out_lap_json, memory_summary_json,
                 analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash, session_mode) DO UPDATE SET
                    filename=excluded.filename,
                    file_path=excluded.file_path,
                    file_size_bytes=excluded.file_size_bytes,
                    file_modified_at=excluded.file_modified_at,
                    analyzed_at=excluded.analyzed_at,
                    analysis_version=excluded.analysis_version,
                    pit_rule=excluded.pit_rule,
                    track_temp_c=excluded.track_temp_c,
                    air_temp_c=excluded.air_temp_c,
                    setup_hash=excluded.setup_hash,
                    setup_name=excluded.setup_name,
                    best_lap_s=excluded.best_lap_s,
                    valid_lap_count=excluded.valid_lap_count,
                    complete_lap_count=excluded.complete_lap_count,
                    out_lap_count=excluded.out_lap_count,
                    classification=excluded.classification,
                    classification_confidence=excluded.classification_confidence,
                    setup_decision=excluded.setup_decision,
                    target_lap=excluded.target_lap,
                    reference_lap=excluded.reference_lap,
                    user_feedback=excluded.user_feedback,
                    issue_tags_json=excluded.issue_tags_json,
                    out_lap_json=excluded.out_lap_json,
                    memory_summary_json=excluded.memory_summary_json,
                    analysis_json=excluded.analysis_json
                """,
                (
                    session["session_id"], session["file_hash"], session["filename"],
                    session["file_path"], session["file_size_bytes"],
                    session["file_modified_at"], session["recorded_at"], now,
                    session["analysis_version"], session["session_mode"], session["pit_rule"],
                    session.get("car_name"), session.get("car_path"), session.get("track_name"),
                    session.get("track_config"), session.get("track_temp_c"),
                    session.get("air_temp_c"), setup["setup_hash"], session.get("setup_name"),
                    session.get("best_lap_s"), session.get("valid_lap_count", 0),
                    session.get("complete_lap_count", 0), session.get("out_lap_count", 0),
                    session.get("classification"), session.get("classification_confidence"),
                    session.get("setup_decision"), session.get("target_lap"),
                    session.get("reference_lap"), session.get("user_feedback", ""),
                    self._json(session.get("issue_tags", [])),
                    self._json(snapshot.get("out_lap", {})),
                    self._json(snapshot.get("memory_summary", {})),
                    self._json(snapshot.get("analysis", {})),
                ),
            )
            connection.execute("DELETE FROM laps WHERE session_id=?", (session["session_id"],))
            for lap in snapshot.get("laps", []):
                connection.execute(
                    """
                    INSERT INTO laps
                    (session_id, lap_number, status, lap_time_s, delta_to_file_best_s,
                     start_pct, end_pct, on_track_ratio, fuel_used_l, invalid_reason, metrics_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, lap_number) DO UPDATE SET
                        status=excluded.status,
                        lap_time_s=excluded.lap_time_s,
                        delta_to_file_best_s=excluded.delta_to_file_best_s,
                        start_pct=excluded.start_pct,
                        end_pct=excluded.end_pct,
                        on_track_ratio=excluded.on_track_ratio,
                        fuel_used_l=excluded.fuel_used_l,
                        invalid_reason=excluded.invalid_reason,
                        metrics_json=excluded.metrics_json
                    """,
                    (
                        session["session_id"], lap.get("lap_number"), lap.get("status"),
                        lap.get("lap_time_s"), lap.get("delta_to_file_best_s"),
                        lap.get("start_pct"), lap.get("end_pct"), lap.get("on_track_ratio"),
                        lap.get("fuel_used_l"), lap.get("invalid_reason"), self._json(lap),
                    ),
                )
            connection.execute("DELETE FROM corner_metrics WHERE session_id=?", (session["session_id"],))
            for corner in snapshot.get("corner_metrics", []):
                connection.execute(
                    """
                    INSERT INTO corner_metrics
                    (session_id, lap_number, corner_id, label, speed_class, entry_pct,
                     apex_pct, exit_pct, delta_s, issue_type, metrics_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, lap_number, corner_id) DO UPDATE SET
                        label=excluded.label,
                        speed_class=excluded.speed_class,
                        entry_pct=excluded.entry_pct,
                        apex_pct=excluded.apex_pct,
                        exit_pct=excluded.exit_pct,
                        delta_s=excluded.delta_s,
                        issue_type=excluded.issue_type,
                        metrics_json=excluded.metrics_json
                    """,
                    (
                        session["session_id"], corner.get("lap_number"), corner.get("id"),
                        corner.get("label"), corner.get("speed_class"), corner.get("entry_pct"),
                        corner.get("apex_pct"), corner.get("exit_pct"), corner.get("delta_s"),
                        corner.get("issue_type"), self._json(corner),
                    ),
                )
            feedback = str(session.get("user_feedback") or "").strip()
            if feedback:
                feedback_id = hashlib.sha256(
                    f"{session['session_id']}|{feedback}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT OR IGNORE INTO driver_feedback
                    (feedback_id, session_id, created_at, feedback_text, parsed_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (feedback_id, session["session_id"], now, feedback, "{}"),
                )
            connection.commit()
        return {
            "session_id": session["session_id"],
            "setup_hash": setup["setup_hash"],
            "saved": True,
            "saved_this_request": True,
            "replaced_existing_mode": bool(replace_existing_mode and conflicting_modes),
            "removed_modes": conflicting_modes if replace_existing_mode else [],
            "assigned_mode": target_mode,
            "assigned_modes": [target_mode],
            "path": str(self.path),
        }

    def save(self, plan: dict[str, Any]) -> None:
        """Compatibility wrapper used by the v2.1 planner."""
        self.save_plan(plan)

    def save_plan(self, plan: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO experiment_plans
                (plan_id, created_at, filename, session_mode, source, plan_json,
                 session_id, decision, candidate_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["plan_id"], float(plan["created_at"]), str(plan.get("filename") or ""),
                    str(plan.get("session_mode") or ""), str(plan.get("source") or ""),
                    self._json(plan), plan.get("session_id"), plan.get("decision"),
                    plan.get("candidate_id"),
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.recent_plans(limit)

    def recent_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT plan_json FROM experiment_plans ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._loads(row[0], {}) for row in rows]

    def recent_sessions(
        self,
        limit: int = 20,
        car_name: Optional[str] = None,
        track_name: Optional[str] = None,
        session_mode: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if car_name:
            clauses.append("car_name=?")
            values.append(car_name)
        if track_name:
            clauses.append("track_name=?")
            values.append(track_name)
        if session_mode:
            clauses.append("session_mode=?")
            values.append(session_mode)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(int(limit))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT session_id, file_hash, filename, recorded_at, analyzed_at, session_mode,
                       car_name, track_name, track_config, track_temp_c, setup_hash,
                       setup_name, best_lap_s, valid_lap_count, classification,
                       classification_confidence, setup_decision, issue_tags_json,
                       memory_summary_json, user_feedback
                FROM sessions
                {where}
                ORDER BY analyzed_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._session_row(row) for row in rows]

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            laps = connection.execute(
                "SELECT metrics_json FROM laps WHERE session_id=? ORDER BY lap_number",
                (session_id,),
            ).fetchall()
            corners = connection.execute(
                """
                SELECT metrics_json FROM corner_metrics
                WHERE session_id=? ORDER BY lap_number, corner_id
                """,
                (session_id,),
            ).fetchall()
            feedback = connection.execute(
                """
                SELECT created_at, feedback_text, parsed_json FROM driver_feedback
                WHERE session_id=? ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
            setup_row = connection.execute(
                """
                SELECT setup_hash, car_name, setup_name, setup_json,
                       setup_summary_json, first_seen_at, last_seen_at
                FROM setups WHERE setup_hash=?
                """,
                (row["setup_hash"],),
            ).fetchone()
            plans = connection.execute(
                """
                SELECT plan_json FROM experiment_plans
                WHERE session_id=? ORDER BY created_at DESC
                """,
                (session_id,),
            ).fetchall()
            results = connection.execute(
                """
                SELECT result_id, created_at, plan_id, baseline_session_id,
                       candidate_session_id, session_mode, verdict, reason,
                       median_delta_s, best_delta_s, stdev_delta_s,
                       pace_slope_delta_s_per_lap, track_temp_delta_c,
                       setup_diff_json, result_json
                FROM experiment_results
                WHERE baseline_session_id=? OR candidate_session_id=?
                ORDER BY created_at DESC
                """,
                (session_id, session_id),
            ).fetchall()
        payload = dict(row)
        for field, default in [
            ("issue_tags_json", []), ("out_lap_json", {}),
            ("memory_summary_json", {}), ("analysis_json", {}),
        ]:
            payload[field.removesuffix("_json")] = self._loads(payload.pop(field), default)
        payload["laps"] = [self._loads(item[0], {}) for item in laps]
        payload["corner_metrics"] = [self._loads(item[0], {}) for item in corners]
        payload["driver_feedback_history"] = [
            {
                "created_at": item[0],
                "feedback_text": item[1],
                "parsed": self._loads(item[2], {}),
            }
            for item in feedback
        ]
        if setup_row is not None:
            payload["setup"] = {
                "setup_hash": setup_row[0],
                "car_name": setup_row[1],
                "setup_name": setup_row[2],
                "setup_json": self._loads(setup_row[3], {}),
                "setup_summary": self._loads(setup_row[4], {}),
                "first_seen_at": setup_row[5],
                "last_seen_at": setup_row[6],
            }
        else:
            payload["setup"] = None
        payload["experiment_plans"] = [self._loads(item[0], {}) for item in plans]
        payload["experiment_results"] = [
            {
                "result_id": item[0],
                "created_at": item[1],
                "plan_id": item[2],
                "baseline_session_id": item[3],
                "candidate_session_id": item[4],
                "session_mode": item[5],
                "verdict": item[6],
                "reason": item[7],
                "median_delta_s": item[8],
                "best_delta_s": item[9],
                "stdev_delta_s": item[10],
                "pace_slope_delta_s_per_lap": item[11],
                "track_temp_delta_c": item[12],
                "setup_diff": self._loads(item[13], {}),
                "result": self._loads(item[14], {}),
            }
            for item in results
        ]
        return payload

    def _session_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["issue_tags"] = self._loads(payload.pop("issue_tags_json", None), [])
        payload["memory_summary"] = self._loads(payload.pop("memory_summary_json", None), {})
        return payload

    @staticmethod
    def _token_set(text: str) -> set[str]:
        return {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", text or "")
            if len(token) >= 2
        }

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, len(left | right))

    def similar_sessions(
        self,
        current: dict[str, Any],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        car_name = current.get("car_name")
        track_name = current.get("track_name")
        if not car_name or not track_name:
            return []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, filename, recorded_at, analyzed_at, session_mode,
                       car_name, track_name, track_config, track_temp_c, setup_hash,
                       setup_name, best_lap_s, valid_lap_count, classification,
                       classification_confidence, setup_decision, issue_tags_json,
                       memory_summary_json, user_feedback
                FROM sessions
                WHERE car_name=? AND track_name=? AND session_id<>? AND file_hash<>?
                ORDER BY analyzed_at DESC
                LIMIT 120
                """,
                (
                    car_name,
                    track_name,
                    str(current.get("session_id") or ""),
                    str(current.get("file_hash") or ""),
                ),
            ).fetchall()

        current_tags = set(current.get("issue_tags") or [])
        current_feedback = self._token_set(str(current.get("user_feedback") or ""))
        current_temp = current.get("track_temp_c")
        current_out_lap = current.get("out_lap_aggregate") or {}
        now = time.time()
        scored: list[dict[str, Any]] = []
        for row in rows:
            item = self._session_row(row)
            score = 0.45  # exact vehicle + track filter
            reasons = ["동일 차량·트랙"]
            if (item.get("track_config") or "") == (current.get("track_config") or ""):
                score += 0.12
                reasons.append("동일 레이아웃")
            if item.get("session_mode") == current.get("session_mode"):
                score += 0.08
                reasons.append("동일 세션 모드")
            if item.get("setup_hash") and item.get("setup_hash") == current.get("setup_hash"):
                score += 0.12
                reasons.append("동일 셋업")
            if current_temp is not None and item.get("track_temp_c") is not None:
                delta = abs(float(current_temp) - float(item["track_temp_c"]))
                temp_score = 0.10 * max(0.0, 1.0 - delta / 12.0)
                score += temp_score
                if delta <= 3.0:
                    reasons.append("유사 노면 온도")
            if item.get("classification") == current.get("classification"):
                score += 0.06
                reasons.append("동일 원인 분류")
            item_tags = set(item.get("issue_tags") or [])
            tag_similarity = self._jaccard(current_tags, item_tags)
            score += 0.12 * tag_similarity
            if tag_similarity >= 0.25:
                reasons.append("유사 코너 증상")
            feedback_similarity = self._jaccard(
                current_feedback, self._token_set(str(item.get("user_feedback") or ""))
            )
            score += 0.04 * feedback_similarity
            historical_out_lap = item.get("memory_summary", {}).get("out_lap_aggregate", {}) or {}
            current_steering = current_out_lap.get("steering_work_deg_median")
            historical_steering = historical_out_lap.get("steering_work_deg_median")
            if current_steering and historical_steering:
                relative_delta = abs(float(current_steering) - float(historical_steering)) / max(
                    abs(float(current_steering)), 1.0
                )
                if relative_delta <= 0.20:
                    score += 0.025
                    reasons.append("유사 아웃랩 조향 작업량")
            current_temp_gain = current_out_lap.get("surface_temp_delta_c_median")
            historical_temp_gain = historical_out_lap.get("surface_temp_delta_c_median")
            if current_temp_gain is not None and historical_temp_gain is not None:
                if abs(float(current_temp_gain) - float(historical_temp_gain)) <= 2.0:
                    score += 0.025
                    reasons.append("유사 아웃랩 온도 상승")
            age_days = max(0.0, (now - float(item.get("analyzed_at") or now)) / 86400.0)
            score += 0.01 * max(0.0, 1.0 - age_days / 180.0)
            item["similarity_score"] = round(min(score, 1.0), 3)
            item["similarity_reasons"] = reasons
            scored.append(item)
        scored.sort(key=lambda item: (item["similarity_score"], item.get("analyzed_at") or 0), reverse=True)
        selected = scored[: max(1, int(limit))]
        self._attach_experiment_outcomes(selected)
        return selected

    def _attach_experiment_outcomes(self, sessions: list[dict[str, Any]]) -> None:
        ids = [str(item.get("session_id")) for item in sessions if item.get("session_id")]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT result_id, created_at, baseline_session_id, candidate_session_id,
                       verdict, reason, median_delta_s, best_delta_s,
                       setup_diff_json, result_json
                FROM experiment_results
                WHERE baseline_session_id IN ({placeholders})
                   OR candidate_session_id IN ({placeholders})
                ORDER BY created_at DESC
                """,
                ids + ids,
            ).fetchall()
        by_session: dict[str, list[dict[str, Any]]] = {value: [] for value in ids}
        seen_results: set[str] = set()
        for row in rows:
            if str(row[0]) in seen_results:
                continue
            seen_results.add(str(row[0]))
            stored_diff = self._loads(row[8], {})
            stored_result = self._loads(row[9], {})
            full_diff = stored_diff.get("full_setup", stored_diff)
            eligibility = setup_learning_eligibility(stored_result, full_diff if isinstance(full_diff, dict) else {})
            payload = {
                "result_id": row[0], "created_at": row[1], "baseline_session_id": row[2],
                "candidate_session_id": row[3], "verdict": row[4], "reason": row[5],
                "median_delta_s": row[6], "best_delta_s": row[7],
                "setup_diff": full_diff,
                "result": stored_result,
                "learning_eligibility": eligibility,
            }
            for key in [row[2], row[3]]:
                if key in by_session:
                    by_session[key].append(payload)
        for item in sessions:
            item["experiment_outcomes"] = by_session.get(str(item.get("session_id")), [])[:3]

    @staticmethod
    def _setup_diff(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        left = baseline.get("session", {}).get("setup", {}) or {}
        right = candidate.get("session", {}).get("setup", {}) or {}
        keys = sorted(set(left) | set(right))
        return {
            key: {"baseline": left.get(key), "candidate": right.get(key)}
            for key in keys
            if left.get(key) != right.get(key)
        }

    @staticmethod
    def _flatten_setup(value: Any, prefix: str = "") -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                flattened.update(LongTermMemoryStore._flatten_setup(child, path))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                path = f"{prefix}[{index}]"
                flattened.update(LongTermMemoryStore._flatten_setup(child, path))
        else:
            flattened[prefix or "value"] = value
        return flattened

    def _stored_setup_diff(
        self,
        connection: sqlite3.Connection,
        baseline_session_id: Optional[str],
        candidate_session_id: Optional[str],
    ) -> dict[str, Any]:
        if not baseline_session_id or not candidate_session_id:
            return {}

        def load_setup(session_id: str) -> dict[str, Any]:
            row = connection.execute(
                """
                SELECT p.setup_json
                FROM sessions s
                JOIN setups p ON p.setup_hash=s.setup_hash
                WHERE s.session_id=?
                """,
                (session_id,),
            ).fetchone()
            return self._loads(row[0], {}) if row else {}

        left = self._flatten_setup(load_setup(baseline_session_id))
        right = self._flatten_setup(load_setup(candidate_session_id))
        differences: dict[str, Any] = {}
        for key in sorted(set(left) | set(right)):
            if left.get(key) != right.get(key):
                differences[key] = {
                    "baseline": left.get(key),
                    "candidate": right.get(key),
                }
        return differences

    def infer_setup_increment(self, car_name: Optional[str], setting: str) -> Optional[dict[str, Any]]:
        """Infer a Garage increment conservatively from locally stored setup history.

        The IBT contains the current setup value but not the legal menu grid.  We
        only expose a numeric inferred target when the same car has enough distinct
        historical setup values that align to a stable grid.  Otherwise callers
        must keep the recommendation directional (one Garage click).
        """
        if not car_name or not setting:
            return None
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT setup_summary_json
                FROM setups
                WHERE car_name=?
                ORDER BY last_seen_at DESC
                LIMIT 250
                """,
                (car_name,),
            ).fetchall()

        values: list[float] = []
        for row in rows:
            summary = self._loads(row[0], {})
            value = summary.get(setting)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                values.append(numeric)

        unique = sorted({round(value, 4) for value in values})
        if len(unique) < 4:
            return None
        deltas = [round(unique[index + 1] - unique[index], 4) for index in range(len(unique) - 1)]
        deltas = [delta for delta in deltas if delta > 1e-6]
        if len(deltas) < 3:
            return None

        candidate = min(deltas)
        tolerance = max(1e-4, abs(candidate) * 0.02)
        adjacent_support = sum(1 for delta in deltas if abs(delta - candidate) <= tolerance)
        if adjacent_support < 2:
            return None

        origin = unique[0]
        aligned = 0
        for value in unique:
            increments = (value - origin) / candidate
            if abs(increments - round(increments)) <= 0.03:
                aligned += 1
        alignment = aligned / len(unique)
        if alignment < 0.80:
            return None

        confidence = min(0.88, 0.58 + 0.05 * adjacent_support + 0.12 * alignment)
        return {
            "step": candidate,
            "confidence": round(confidence, 3),
            "unique_values": len(unique),
            "samples": len(values),
            "adjacent_support": adjacent_support,
            "grid_alignment": round(alignment, 3),
            "source": "LOCAL_SETUP_HISTORY",
        }

    def _store_setup_sensitivity_events(
        self,
        connection: sqlite3.Connection,
        *,
        result_id: str,
        comparison: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        session_mode: str,
        full_setup_diff: dict[str, Any],
    ) -> dict[str, Any]:
        changes = canonical_setup_changes(full_setup_diff)
        eligibility = setup_learning_eligibility(comparison, full_setup_diff)
        if not eligibility["eligible"]:
            # Re-running an A/B with corrected conditions must also invalidate
            # any previously learned attribution for the same result.
            connection.execute("DELETE FROM setup_sensitivity_events WHERE result_id=?", (result_id,))
            return {"stored_events": 0, "events": [], "learning_eligibility": eligibility,
                    "reason": " ".join(eligibility["reasons"])}
        if not changes:
            return {"stored_events": 0, "events": [], "reason": "학습 가능한 셋업 항목 차이가 없습니다."}

        confounded = eligibility["confounded"]
        verdict = str(comparison.get("verdict") or "INCONCLUSIVE").upper()
        base_conf = 0.78 if verdict in {"KEEP", "REVERT"} else 0.38
        temp_delta = comparison.get("track_temp_delta_c")
        if temp_delta is not None:
            base_conf -= min(0.18, max(0.0, abs(float(temp_delta)) - 1.0) * 0.05)
        if confounded:
            base_conf -= 0.24
        baseline_laps = int((comparison.get("baseline") or {}).get("valid_laps") or 0)
        candidate_laps = int((comparison.get("candidate") or {}).get("valid_laps") or 0)
        if min(baseline_laps, candidate_laps) >= 5:
            base_conf += 0.08
        elif min(baseline_laps, candidate_laps) >= 3:
            base_conf += 0.04
        ab_quality = comparison.get("ab_quality") or {}
        effect_vs_noise = ab_quality.get("effect_vs_noise_ratio")
        if effect_vs_noise is not None:
            if float(effect_vs_noise) >= 1.0:
                base_conf += 0.05
            elif float(effect_vs_noise) < 0.5:
                base_conf -= 0.08
        confidence = round(max(0.15, min(0.92, base_conf)), 3)

        session = baseline.get("session", {}) or {}
        effects = {
            "telemetry": comparison.get("effect_profile") or {},
            "performance_axes": comparison.get("performance_axes") or {},
            "ab_quality": comparison.get("ab_quality") or {},
            "learning_eligibility": eligibility,
            "conditions_quality": comparison.get("conditions_quality") or {},
            "goal": comparison.get("goal"),
        }
        stored: list[dict[str, Any]] = []
        for change in changes:
            event_id = hashlib.sha256(
                f"{result_id}|{change['setting']}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT OR REPLACE INTO setup_sensitivity_events
                (event_id, result_id, created_at, car_name, track_name, track_config,
                 session_mode, setting, setting_label, direction, baseline_value,
                 candidate_value, delta_value, unit, verdict, median_delta_s,
                 best_delta_s, track_temp_delta_c, confidence, confounded, effects_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, result_id, time.time(), session.get("car_name"),
                    session.get("track_name"), session.get("track_config"), session_mode,
                    change["setting"], change["label"], int(change["direction"]),
                    change.get("baseline_value"), change.get("candidate_value"),
                    change.get("delta_value"), change.get("unit"), verdict,
                    comparison.get("median_delta_s"), comparison.get("best_delta_s"),
                    comparison.get("track_temp_delta_c"), confidence, int(confounded),
                    self._json(effects),
                ),
            )
            stored.append({**change, "event_id": event_id, "confidence": confidence, "confounded": confounded})
        return {
            "stored_events": len(stored),
            "events": stored,
            "confounded": confounded,
            "policy": "한 번에 한 셋업 항목만 바꾼 A/B 결과를 강한 개인화 근거로 사용합니다.",
        }

    def setup_sensitivity_profile(
        self,
        car_name: Optional[str],
        track_name: Optional[str] = None,
        limit: int = 300,
    ) -> dict[str, Any]:
        if not car_name:
            return {"status": "NO_CAR", "event_count": 0, "settings": []}
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.event_id, e.created_at, e.car_name, e.track_name, e.track_config,
                       e.setting, e.setting_label, e.direction, e.baseline_value, e.candidate_value,
                       e.delta_value, e.unit, e.verdict, e.median_delta_s, e.best_delta_s,
                       e.confidence, e.confounded, e.effects_json, r.result_json, r.setup_diff_json
                FROM setup_sensitivity_events e
                JOIN experiment_results r ON r.result_id=e.result_id
                WHERE e.car_name=?
                ORDER BY e.created_at DESC
                LIMIT ?
                """,
                (car_name, max(20, int(limit))),
            ).fetchall()

        events = [dict(row) for row in rows]
        for event in events:
            stored_diff = self._loads(event.pop("setup_diff_json", "{}"), {})
            stored_result = self._loads(event.pop("result_json", "{}"), {})
            full_diff = stored_diff.get("full_setup", stored_diff)
            event["learning_eligible"] = setup_learning_eligibility(stored_result, full_diff if isinstance(full_diff, dict) else {})["eligible"]
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for item in events:
            grouped[(str(item.get("setting") or ""), int(item.get("direction") or 0))].append(item)

        summaries: list[dict[str, Any]] = []
        for (setting, direction), items in grouped.items():
            decisive = [item for item in items if str(item.get("verdict") or "").upper() in {"KEEP", "REVERT"} and not item.get("confounded") and item.get("learning_eligible")]
            keeps = [item for item in decisive if str(item.get("verdict") or "").upper() == "KEEP"]
            reverts = [item for item in decisive if str(item.get("verdict") or "").upper() == "REVERT"]
            inconclusive = [item for item in items if str(item.get("verdict") or "").upper() == "INCONCLUSIVE"]
            keep_rate = len(keeps) / len(decisive) if decisive else None
            weighted = [
                (float(item["median_delta_s"]), float(item.get("confidence") or 0.5))
                for item in decisive if item.get("median_delta_s") is not None
            ]
            weighted_delta = (
                sum(value * weight for value, weight in weighted) / max(1e-9, sum(weight for _, weight in weighted))
                if weighted else None
            )
            current_track_events = sum(1 for item in items if track_name and item.get("track_name") == track_name)
            tier = "NO_DECISIVE_DATA"
            if len(decisive) >= 4:
                tier = "STRONG"
            elif len(decisive) >= 2:
                tier = "LEARNING"
            elif len(decisive) == 1:
                tier = "EARLY"
            summaries.append({
                "setting": setting,
                "label": items[0].get("setting_label") or setting,
                "unit": items[0].get("unit"),
                "direction": direction,
                "direction_label": "증가" if direction > 0 else "감소" if direction < 0 else "변화 없음",
                "event_count": len(items),
                "decisive_count": len(decisive),
                "keep_count": len(keeps),
                "revert_count": len(reverts),
                "inconclusive_count": len(inconclusive),
                "keep_rate": round_or_none(keep_rate, 3),
                "weighted_median_delta_s": round_or_none(weighted_delta, 4),
                "mean_confidence": round_or_none(float(np.mean([float(item.get("confidence") or 0.0) for item in items])), 3),
                "current_track_events": current_track_events,
                "tier": tier,
                "recent": [
                    {
                        "created_at": item.get("created_at"), "track_name": item.get("track_name"),
                        "verdict": item.get("verdict"), "median_delta_s": item.get("median_delta_s"),
                        "delta_value": item.get("delta_value"), "confidence": item.get("confidence"),
                        "confounded": bool(item.get("confounded")),
                    }
                    for item in items[:3]
                ],
            })
        summaries.sort(
            key=lambda item: (
                int(item.get("decisive_count") or 0),
                abs(float(item.get("weighted_median_delta_s") or 0.0)),
                float(item.get("mean_confidence") or 0.0),
            ),
            reverse=True,
        )
        decisive_total = sum(int(item.get("decisive_count") or 0) for item in summaries)
        return {
            "status": "READY" if events else "NO_DATA",
            "car_name": car_name,
            "track_name": track_name,
            "event_count": len(events),
            "decisive_event_count": decisive_total,
            "settings": summaries[:16],
            "learning_stage": (
                "MATURE" if decisive_total >= 12 else
                "ESTABLISHED" if decisive_total >= 6 else
                "LEARNING" if decisive_total >= 2 else
                "COLD_START"
            ),
        }

    def driver_fingerprint(self, car_name: Optional[str], session_limit: int = 30) -> dict[str, Any]:
        if not car_name:
            return {"status": "NO_CAR", "confidence": 0.0, "weaknesses": [], "strengths": []}
        with self._lock, self._connect() as connection:
            session_rows = connection.execute(
                """
                SELECT session_id, track_name, analyzed_at
                FROM sessions
                WHERE car_name=?
                ORDER BY analyzed_at DESC
                LIMIT ?
                """,
                (car_name, max(5, int(session_limit))),
            ).fetchall()
            session_ids = [str(row[0]) for row in session_rows]
            if not session_ids:
                return {"status": "NO_DATA", "car_name": car_name, "confidence": 0.0, "weaknesses": [], "strengths": []}
            placeholders = ",".join("?" for _ in session_ids)
            corner_rows = connection.execute(
                f"""
                SELECT cm.session_id, cm.speed_class, cm.issue_type, cm.delta_s, cm.metrics_json
                FROM corner_metrics cm
                WHERE cm.session_id IN ({placeholders})
                """,
                session_ids,
            ).fetchall()
            lap_rows = connection.execute(
                f"""
                SELECT session_id, lap_time_s
                FROM laps
                WHERE session_id IN ({placeholders}) AND status='VALID' AND lap_time_s IS NOT NULL
                """,
                session_ids,
            ).fetchall()

        issue_rows: list[dict[str, Any]] = []
        speed_totals: Counter[str] = Counter()
        speed_issue_counts: Counter[str] = Counter()
        issue_counts: Counter[tuple[str, str]] = Counter()
        issue_sessions: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in corner_rows:
            speed = str(row[1] or "UNKNOWN")
            issue = str(row[2] or "")
            delta = float(row[3] or 0.0)
            speed_totals[speed] += 1
            if delta >= 0.02 and issue not in {"GAIN", "CONSISTENT", "NO_DELTA", "NO_REFERENCE"}:
                speed_issue_counts[speed] += 1
                issue_counts[(issue, speed)] += 1
                issue_sessions[(issue, speed)].add(str(row[0]))
                issue_rows.append({"issue_type": issue, "speed_class": speed, "delta_s": delta})

        weaknesses: list[dict[str, Any]] = []
        total_sessions = len(session_ids)
        for (issue, speed), count in issue_counts.items():
            support_sessions = len(issue_sessions[(issue, speed)])
            if count < 2:
                continue
            avg_loss = float(np.mean([item["delta_s"] for item in issue_rows if item["issue_type"] == issue and item["speed_class"] == speed]))
            weaknesses.append({
                "issue_type": issue,
                "issue_label": coach_issue_label(issue),
                "speed_class": speed,
                "occurrences": count,
                "session_support": support_sessions,
                "session_support_ratio": round(support_sessions / max(1, total_sessions), 3),
                "mean_loss_s": round_or_none(avg_loss, 3),
            })
        weaknesses.sort(
            key=lambda item: (
                float(item.get("session_support_ratio") or 0.0),
                float(item.get("mean_loss_s") or 0.0),
                int(item.get("occurrences") or 0),
            ), reverse=True,
        )

        strengths: list[dict[str, Any]] = []
        for speed, total in speed_totals.items():
            if total < 8:
                continue
            issue_rate = speed_issue_counts[speed] / max(1, total)
            if issue_rate <= 0.45:
                strengths.append({
                    "type": "SPEED_CLASS_REPEATABILITY",
                    "speed_class": speed,
                    "label": f"{speed_class_label(speed)} 코너 재현성",
                    "issue_rate": round(issue_rate, 3),
                    "sample_count": total,
                    "score": round(1.0 - issue_rate, 3),
                })
        strengths.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)

        by_session: dict[str, list[float]] = defaultdict(list)
        for row in lap_rows:
            by_session[str(row[0])].append(float(row[1]))
        stdevs = [float(np.std(values)) for values in by_session.values() if len(values) >= 2]
        median_lap_stdev = float(np.median(stdevs)) if stdevs else None
        if median_lap_stdev is not None:
            strengths.append({
                "type": "LAP_REPEATABILITY",
                "label": "유효 랩 페이스 재현성",
                "score": round(max(0.0, min(1.0, 1.0 - median_lap_stdev / 1.0)), 3),
                "median_stdev_s": round_or_none(median_lap_stdev, 3),
                "sample_count": len(stdevs),
            })

        tracks = {str(row[1]) for row in session_rows if row[1]}
        data_score = min(1.0, len(session_ids) / 12.0)
        coverage_score = min(1.0, len(corner_rows) / 250.0)
        confidence = round(0.65 * data_score + 0.35 * coverage_score, 3)
        return {
            "status": "READY" if len(session_ids) >= 2 else "EARLY",
            "car_name": car_name,
            "session_count": len(session_ids),
            "track_count": len(tracks),
            "corner_sample_count": len(corner_rows),
            "median_valid_lap_stdev_s": round_or_none(median_lap_stdev, 3),
            "confidence": confidence,
            "confidence_label": "HIGH" if confidence >= 0.75 else "MEDIUM" if confidence >= 0.45 else "LOW",
            "weaknesses": weaknesses[:6],
            "strengths": sorted(strengths, key=lambda item: float(item.get("score") or 0.0), reverse=True)[:4],
        }

    def latest_plan_id_for_session(self, session_id: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.plan_id
                FROM experiment_plans p
                LEFT JOIN experiment_results r ON r.plan_id=p.plan_id
                WHERE p.session_id=? AND p.decision='SETUP_TEST'
                ORDER BY (r.result_id IS NULL) DESC, p.created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def file_analysis_index(self) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT filename, session_mode, analyzed_at, best_lap_s,
                       classification, setup_decision, valid_lap_count
                FROM sessions
                ORDER BY analyzed_at DESC
                """
            ).fetchall()
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            mode = str(row["session_mode"] or "")
            item = output.setdefault(
                str(row["filename"]),
                {
                    "analyzed": True,
                    "analysis_count": 0,
                    "session_modes": [],
                    "assigned_mode": mode or None,
                    "has_mode_conflict": False,
                    "last_analyzed_at": row["analyzed_at"],
                    "best_lap_s": row["best_lap_s"],
                    "classification": row["classification"],
                    "setup_decision": row["setup_decision"],
                    "valid_lap_count": row["valid_lap_count"],
                },
            )
            item["analysis_count"] += 1
            if mode and mode not in item["session_modes"]:
                item["session_modes"].append(mode)
            item["has_mode_conflict"] = len(item["session_modes"]) > 1
        return output

    def save_experiment_result(
        self,
        comparison: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        session_mode: str,
        plan_id: Optional[str] = None,
    ) -> dict[str, Any]:
        baseline_id = baseline.get("memory", {}).get("session_id")
        candidate_id = candidate.get("memory", {}).get("session_id")
        result_id = hashlib.sha256(
            f"{baseline_id}|{candidate_id}|{session_mode}|{plan_id or ''}".encode("utf-8")
        ).hexdigest()
        with self._lock, self._connect() as connection:
            full_setup_diff = self._stored_setup_diff(connection, baseline_id, candidate_id)
            setup_diff = {
                "full_setup": full_setup_diff,
                "summary": self._setup_diff(baseline, candidate),
                "changed_field_count": len(full_setup_diff),
            }
            connection.execute(
                """
                INSERT OR REPLACE INTO experiment_results
                (result_id, created_at, plan_id, baseline_session_id, candidate_session_id,
                 session_mode, verdict, reason, median_delta_s, best_delta_s,
                 stdev_delta_s, pace_slope_delta_s_per_lap, track_temp_delta_c,
                 setup_diff_json, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id, time.time(), plan_id, baseline_id, candidate_id, session_mode,
                    comparison.get("verdict"), comparison.get("reason"),
                    comparison.get("median_delta_s"), comparison.get("best_delta_s"),
                    comparison.get("stdev_delta_s"), comparison.get("pace_slope_delta_s_per_lap"),
                    comparison.get("track_temp_delta_c"), self._json(setup_diff),
                    self._json(comparison),
                ),
            )
            learning_update = self._store_setup_sensitivity_events(
                connection,
                result_id=result_id,
                comparison=comparison,
                baseline=baseline,
                candidate=candidate,
                session_mode=session_mode,
                full_setup_diff=full_setup_diff,
            )
            connection.commit()
        profile = self.setup_sensitivity_profile(
            baseline.get("session", {}).get("car_name"),
            baseline.get("session", {}).get("track_name"),
        )
        return {
            "saved": True,
            "result_id": result_id,
            "path": str(self.path),
            "baseline_session_id": baseline_id,
            "candidate_session_id": candidate_id,
            "setup_diff": setup_diff,
            "learning_update": learning_update,
            "sensitivity_profile": profile,
        }


memory_store_error: Optional[str] = None
try:
    memory_store: Optional[LongTermMemoryStore] = LongTermMemoryStore(MEMORY_DB_PATH)
    plan_store: Optional[LongTermMemoryStore] = memory_store
    for legacy_plan_path in (_legacy_plan_db_paths() if os.getenv("IRACING_IMPORT_LEGACY", "1") != "0" else []):
        if legacy_plan_path.exists() and legacy_plan_path.resolve() != MEMORY_DB_PATH.resolve():
            imported_plans = memory_store.import_legacy_plans(legacy_plan_path)
            if imported_plans:
                logger.info(
                    "Imported %s legacy experiment plans from %s",
                    imported_plans,
                    legacy_plan_path,
                )
except Exception as exc:
    memory_store = None
    plan_store = None
    memory_store_error = str(exc)
    logger.warning("Long-term memory persistence disabled: %s", exc)


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def regex_value(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def round_or_none(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def format_lap_time(seconds: Optional[float]) -> Optional[str]:
    if seconds is None or not math.isfinite(seconds):
        return None
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}:{remainder:06.3f}"


def smooth(values: np.ndarray[Any, Any], window: int) -> np.ndarray[Any, Any]:
    if window <= 1 or len(values) < 3:
        return values.astype(float, copy=True)
    window = min(window, len(values) if len(values) % 2 else len(values) - 1)
    window = max(3, window | 1)
    pad = window // 2
    padded = np.pad(values.astype(float), (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=float) / window
    return np.convolve(padded, kernel, mode="valid")


def contiguous_groups(indices: np.ndarray[Any, Any], max_gap: int = 1, min_len: int = 1) -> list[tuple[int, int]]:
    if len(indices) == 0:
        return []
    groups: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw in indices[1:]:
        current = int(raw)
        if current - previous <= max_gap:
            previous = current
            continue
        if previous - start + 1 >= min_len:
            groups.append((start, previous))
        start = previous = current
    if previous - start + 1 >= min_len:
        groups.append((start, previous))
    return groups


def nearest_index(grid: np.ndarray[Any, Any], value: float) -> int:
    return int(np.clip(np.searchsorted(grid, value), 0, len(grid) - 1))


def downsample_indices(length: int, points: int) -> np.ndarray[Any, Any]:
    if length <= points:
        return np.arange(length, dtype=int)
    return np.unique(np.linspace(0, length - 1, points, dtype=int))


def visualization_indices(target: dict[str, Any], reference: Optional[dict[str, Any]], points: int, start: int = 0, end: Optional[int] = None) -> np.ndarray:
    """Keep state/input events and extrema in addition to evenly spaced points."""
    end = len(target["distance_pct"]) - 1 if end is None else end
    length = end - start + 1
    if length <= points:
        return np.arange(start, end + 1, dtype=int)
    selected = set((downsample_indices(length, points) + start).tolist())
    bucket_edges = np.linspace(start, end + 1, max(12, points // 8) + 1, dtype=int)
    for grid in (target, reference):
        if grid is None:
            continue
        for key in ("speed_kph", "Brake", "Throttle", "SteeringWheelAngle", "Gear", "BrakeABSactive", "time_s"):
            if key not in grid:
                continue
            values = np.asarray(grid[key], dtype=float)
            valid = np.isfinite(values[start:end + 1])
            gaps = np.flatnonzero(valid[1:] != valid[:-1]) + start
            for index in gaps:
                selected.update((int(index), int(index + 1)))
            if key in {"Gear", "BrakeABSactive"}:
                changes = np.flatnonzero((values[start + 1:end + 1] != values[start:end]) & valid[1:] & valid[:-1]) + start
                for index in changes:
                    selected.update((int(index), int(index + 1)))
            elif key in {"Brake", "Throttle"}:
                for threshold in (0.05, 0.08, 0.2, 0.95):
                    changes = np.flatnonzero((values[start + 1:end + 1] >= threshold) != (values[start:end] >= threshold)) + start
                    for index in changes:
                        selected.update((int(index), int(index + 1)))
            if key != "time_s":
                for left, right in zip(bucket_edges[:-1], bucket_edges[1:]):
                    segment = values[left:right]
                    if len(segment) and np.any(np.isfinite(segment)):
                        selected.update((left + int(np.nanargmin(segment)), left + int(np.nanargmax(segment))))
    return np.asarray(sorted(selected), dtype=int)


_CORNER_DEFINITIONS: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()
_CORNER_DEFINITIONS_LOCK = threading.RLock()
_CORNER_DEFINITION_STORE = CornerDefinitionStore(Path(os.getenv(
    "IRACING_CORNER_DEFINITIONS_PATH", str(MEMORY_DB_PATH.parent / "corner-definitions.json"))))


def _corner_identity_persistent(identity: tuple[Any, ...]) -> bool:
    # Only real iRacing car and track ids are stable enough to keep on disk.
    return bool(identity[0] and identity[3])


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


class TelemetryAnalyzer:
    def __init__(self, telemetry: ParsedTelemetry, grid_points: int = DEFAULT_GRID_POINTS):
        self.telemetry = telemetry
        self.channels = telemetry.channels
        self.track_length_m = telemetry.metadata.get("track_length_m") or 1000.0
        self.grid_points = min(25000, max(500, grid_points, int(math.ceil(self.track_length_m / 2.0)) + 1))
        self.valid_laps = [lap for lap in telemetry.laps if lap.valid and lap.lap_time_s]
        # A parsed file is immutable, so lap grids and per-corner metrics are kept
        # with it and reused by every request for that file. _grid_ids maps
        # id(grid) -> (grid, key); only those exact grid objects are memoized.
        try:
            shared = vars(telemetry).setdefault("_analysis_cache", {})
        except TypeError:
            shared = {}
        cache = shared.setdefault(self.grid_points, {"grids": {}, "metrics": {}, "ids": {}})
        self._grid_cache: dict[int, dict[str, np.ndarray[Any, Any]]] = cache["grids"]
        self._metric_cache: dict[tuple[Any, ...], dict[str, Any]] = cache["metrics"]
        self._grid_ids: dict[int, tuple[Any, tuple[Any, ...]]] = cache["ids"]

    def _clean_context(self) -> dict[str, Any]:
        if not hasattr(self, "_clean_segment_context"):
            self._clean_segment_context = clean_segment_context(self.telemetry)
        return self._clean_segment_context

    def _coaching_grid(self, lap: LapSegment) -> dict[str, Any]:
        cached = self._grid_cache.get(lap.lap_number)
        if cached is not None and self._grid_ids.get(id(cached), (None, (None,)))[1][0] == "clean":
            return cached
        grid = mask_grid_to_clean_windows(self._lap_grid(lap), self._clean_context()["lap_windows"].get(lap.lap_number, []))
        self._grid_cache[lap.lap_number] = grid
        self._grid_ids[id(grid)] = (grid, ("clean", lap.lap_number))
        return grid


    def analyze(self, request: AnalyzeRequest) -> dict[str, Any]:
        request = request_with_structured_feedback(request)
        policy = session_policy(request.session_mode)
        data_quality = self._data_quality(request.session_mode)
        target, reference, lap_selection = self._select_laps(request.target_lap, request.reference_lap)
        lap_table = self._lap_table(lap_selection)

        comparison: Optional[dict[str, Any]] = None
        corners: list[dict[str, Any]] = []
        repetition: dict[str, Any] = {
            "status": "INSUFFICIENT_LAPS",
            "repeated_setup_patterns": [],
            "candidate_setup_patterns": [],
        }
        visualizations: dict[str, Any] = {"track_path": [], "trace": [], "markers": []}
        clean_corners: list[dict[str, Any]] = []
        confirmed_names: dict[str, dict[str, Any]] = {}

        if target:
            target_grid = self._lap_grid(target) if target.valid else self._coaching_grid(target)
            reference_grid = self._lap_grid(reference) if reference else None
            corner_defs = self._detect_corners(reference_grid or target_grid)
            comparison = self._comparison(target, reference, target_grid, reference_grid) if target.valid else {
                "status": "COACHING_ONLY", "target_lap": target.lap_number, "target_time_s": None,
                "reference_lap": None, "total_delta_s": None, "gap_to_session_best_s": None,
                "scope": "CLEAN_SEGMENTS_ONLY", "reason": self._clean_context()["reason"],
            }
            corners = self._corner_analysis(target_grid, reference_grid, corner_defs)
            confirmed_names = _CORNER_DEFINITION_STORE.names_for(
                corner_defs, self.telemetry.metadata.get("track_platform_id"), self.telemetry.metadata.get("track_config"))
            repetition = self._repetition_analysis(corner_defs, request.session_mode)
            visualizations = self._visualizations(target_grid, reference_grid, corners)
            for lap in sorted((lap for lap in self.telemetry.laps if lap.lap_number in self._clean_context()["lap_windows"]),
                              key=lambda lap: sum(window["distance_m"] for window in self._clean_context()["lap_windows"][lap.lap_number]), reverse=True)[:6]:
                measured = self._corner_analysis(self._coaching_grid(lap), None, corner_defs)
                for corner in measured:
                    if corner.get("measurement_usable"):
                        clean_corners.append({**corner, "source": "CLEAN_SEGMENT", "scope": "COACHING_ONLY",
                                              "lap_number": lap.lap_number, "official_valid_lap": False})

        external_reference = self._external_reference_analysis(
            target,
            request.external_reference_files,
        )
        vehicle = self._vehicle_diagnostics(target)
        classification = self._classify(
            request.session_mode, data_quality, corners, vehicle, repetition
        )
        structured_feedback = self._structured_feedback(request, corners + clean_corners, external_reference)
        setup_recommendation = self._setup_recommendation(
            request.session_mode,
            classification,
            corners,
            vehicle,
            repetition,
            external_reference,
            request=request,
            feedback=structured_feedback,
        )
        coaching_priorities = self._coaching_priorities(
            corners,
            comparison,
            external_reference,
        )
        dynamic_controls = self._dynamic_control_opportunities(
            corners,
            self._lap_grid(target) if target else None,
        )

        external_gap = (external_reference.get("aggregate") or {}).get("gap_to_reference_median_s")
        setup_assist_available = bool(setup_recommendation.get("experimental_candidates"))
        classification["performance_objective"] = "CLOSE_EXTERNAL_REFERENCE_GAP"
        classification["classification_gate_removed"] = True
        classification["parallel_assessment"] = {
            "external_gap_s": external_gap,
            "driver_improvement_required": bool(external_gap is not None and float(external_gap) > 0.03),
            "setup_assist_opportunity": setup_assist_available,
            "external_reference_decision_ready": bool(external_reference.get("decision_ready")),
            "note": (
                "DRIVER_LIMITED 분류는 셋업 후보 생성을 차단하지 않습니다. "
                "외부 기준과의 성능 격차를 줄일 가능성이 있으면 별도의 Setup Assist A/B 후보를 생성합니다."
            ),
        }

        briefing = self._deterministic_briefing(
            request=request,
            data_quality=data_quality,
            comparison=comparison,
            corners=corners,
            vehicle=vehicle,
            classification=classification,
            setup_recommendation=setup_recommendation,
            coaching_priorities=coaching_priorities,
            dynamic_controls=dynamic_controls,
            external_reference=external_reference,
        )

        result: dict[str, Any] = {
            "status": "success",
            "analysis_version": APP_VERSION,
            "file": {
                "name": self.telemetry.path.name,
                "size_bytes": self.telemetry.path.stat().st_size,
                "modified_at": self.telemetry.path.stat().st_mtime,
                "tick_rate_hz": self.telemetry.tick_rate,
                "records": self.telemetry.record_count,
                "duration_s": round_or_none(self.telemetry.record_count / self.telemetry.tick_rate, 1),
            },
            "session": self._session_summary(),
            "session_policy": {
                "session_mode": request.session_mode.value,
                **policy,
                "timed_laps_available": len(self.valid_laps),
                "out_laps_detected": sum(
                    1 for lap in self.telemetry.laps if lap.status == "OUT_LAP"
                ),
            },
            "data_quality": data_quality,
            "clean_segments": {key: value for key, value in self._clean_context().items() if key != "lap_windows"},
            "laps": lap_table,
            "lap_selection": lap_selection,
            "comparison": comparison,
            "external_reference": external_reference,
            "performance": {
                "classification": classification,
                "repetition": repetition,
                "corners": corners,
                "top_losses": [corner for corner in corners if (corner.get("delta_s") or 0) > 0][:5],
                "top_gains": sorted(
                    [corner for corner in corners if (corner.get("delta_s") or 0) < 0],
                    key=lambda item: item.get("delta_s", 0),
                )[:3],
                "external_top_losses": external_reference.get("top_losses", [])[:5],
            },
            "vehicle": vehicle,
            "dynamic_controls": dynamic_controls,
            "setup_recommendation": setup_recommendation,
            "coaching": {"priorities": coaching_priorities},
            "visualizations": visualizations,
            "briefing": briefing,
            "user_feedback": request.user_feedback.strip(),
            "structured_feedback": structured_feedback,
        }
        result["coaching"]["clean_segment_corners"] = clean_corners
        # Confirmed official numbers or names replace "자동 구간 N" wherever a corner is shown.
        for corner in [*corners, *clean_corners, *(external_reference.get("corners") or []), *(external_reference.get("top_losses") or [])]:
            named = confirmed_names.get(corner.get("id"))
            call = corner_names.spoken_call(named.get("number"), named.get("name"), named.get("spoken")) if named else None
            if call:
                corner.update(official_turn_number=named.get("number"), turn_name=named.get("name"), label=call)
        result["performance"]["corner_names_confirmed"] = bool(confirmed_names)
        return json_safe(result)


    def _structured_feedback(self, request: AnalyzeRequest, local_corners: list[dict[str, Any]], external: dict[str, Any]) -> dict[str, Any]:
        selected = bool(request.feedback_phase or request.feedback_speed or request.feedback_symptom)
        external_corners = external.get("corners") or external.get("top_losses") or []
        corners = external_corners if external_corners else local_corners
        observations = []
        for corner in corners:
            if corner.get("measurement_usable") is False:
                continue
            target = corner.get("target") or {}
            reference = corner.get("reference") or {}
            speed = target.get("min_speed_kph")
            if speed is None:
                continue
            band = corner.get("speed_class") or ("LOW" if float(speed) < 105 else "MEDIUM" if float(speed) < 165 else "HIGH")
            if request.feedback_speed and band != request.feedback_speed:
                continue
            phase = request.feedback_phase or "MID_CORNER"
            keys = {"ENTRY": ("brake_start_pct", "brake_release_pct", "peak_brake", "abs_duty_pct"), "MID_CORNER": ("min_speed_kph", "avg_abs_steer_deg", "steering_efficiency", "coast_time_s"), "EXIT": ("full_throttle_pct", "exit_speed_kph", "throttle_relifts", "steering_corrections")}[phase]
            metrics = {key: {"target": target.get(key), "reference": reference.get(key)} for key in keys if target.get(key) is not None}
            if not metrics:
                continue
            observations.append({"corner_id": corner.get("id"), "corner": corner.get("label"), "label": corner.get("label"), "phase": phase, "speed_band": band, "metrics": metrics, "evidence": list(corner.get("evidence") or []), "delta_s": corner.get("delta_s"), "source": corner.get("source") or ("CLEAN_SEGMENT" if not getattr(self, "valid_laps", [True]) else "GARAGE61" if external_corners else "LOCAL"), "scope": corner.get("scope") or ("COACHING_ONLY" if not getattr(self, "valid_laps", [True]) else "VALID_LAP")})
        observations.sort(key=lambda item: float(item.get("delta_s") or 0), reverse=True)
        unique_observations = {}
        for observation in observations:
            unique_observations.setdefault(observation.get("corner_id") or observation.get("label"), observation)
        observations = list(unique_observations.values())
        symptom = request.feedback_symptom
        hypotheses = {
            "UNDERSTEER": "언더스티어 체감은 진입 속도·브레이크 릴리즈·조향량 또는 셋업 균형과 관련될 수 있습니다. 현재 관측만으로 원인을 확정하지 않습니다.",
            "OVERSTEER": "오버스티어 체감은 페달 전환·조향 교정 또는 셋업 균형과 관련될 수 있습니다. 현재 관측만으로 원인을 확정하지 않습니다.",
            "BRAKING": "제동 체감은 제동 시작·최대 압력·릴리즈와 관련될 수 있습니다. ABS 채널이 없으면 ABS 개입을 판단하지 않습니다.",
            "KERB": "연석 불안정은 주행선·연석 진입 속도·서스펜션과 관련될 수 있습니다. 단일 랩의 체감만으로 바텀아웃을 확정하지 않습니다.",
        }
        return {"phase": request.feedback_phase, "speed": request.feedback_speed, "symptom": symptom, "setup_mode": request.setup_mode,
                "status": "NOT_SELECTED" if not selected else "MATCHED" if observations else "NO_MATCH",
                "source": "GARAGE61" if external_corners else "LOCAL", "observations": observations[:3] if selected else [],
                "speed_band_definition_kph": {"LOW": "<105", "MEDIUM": "105–164.9", "HIGH": ">=165"},
                "hypothesis": hypotheses.get(symptom, "선택한 구간의 입력 차이를 확인하고 반복 주행으로 체감과 관측이 일치하는지 검증합니다.") if selected else None,
                "next_run": ("탐색 후보가 있으면 한 항목만 바꾸고 평소 페이스로 1~2랩 달려 체감을 확인하세요. 랩타임을 일정하게 맞출 필요는 없습니다. 속도 효과는 별도 A/B로 확인합니다." if request.setup_mode == "EXPLORE" else "가장 손실이 큰 해당 코너에서 입력 한 가지만 바꾸고 유효 랩 3개를 기록하세요. 셋업을 바꾸면 한 항목만 바꾸고 A/B 중앙값과 산포를 비교하세요.") if observations and selected else "선택 조건에 맞는 코너 관측이 없습니다. 추가 유효 랩을 기록하거나 속도 구간을 변경하세요." if selected else None}


    def build_memory_snapshot(
        self,
        result: dict[str, Any],
        request: AnalyzeRequest,
    ) -> dict[str, Any]:
        """Build the compact payload persisted by the personal-memory database."""

        file_hash = file_sha256(self.telemetry.path)
        setup_hash = canonical_hash(
            {
                "car_path": self.telemetry.metadata.get("car_path"),
                "setup": self.telemetry.setup,
            }
        )
        session_id = hashlib.sha256(
            f"{file_hash}|{request.session_mode.value}".encode("utf-8")
        ).hexdigest()
        session = result.get("session", {})
        quality = result.get("data_quality", {})
        comparison = result.get("comparison") or {}
        classification = result.get("performance", {}).get("classification", {})
        setup_recommendation = result.get("setup_recommendation", {})
        top_losses = result.get("performance", {}).get("top_losses", [])[:5]
        external_reference = result.get("external_reference", {}) or {}
        external_top_losses = external_reference.get("top_losses", [])[:5]

        issue_tags: set[str] = set()
        label = classification.get("label")
        if label:
            issue_tags.add(str(label))
        for corner in top_losses:
            issue_type = corner.get("issue_type")
            speed_class = corner.get("speed_class")
            corner_id = corner.get("id")
            if issue_type:
                issue_tags.add(str(issue_type))
            if speed_class:
                issue_tags.add(str(speed_class))
            if corner_id and issue_type:
                issue_tags.add(f"{corner_id}_{issue_type}")
        for corner in external_top_losses:
            if corner.get("issue_type"):
                issue_tags.add(f"EXTERNAL_{corner.get('issue_type')}")
        if result.get("vehicle", {}).get("ride_height", {}).get("bottoming_detected"):
            issue_tags.add("BOTTOMING")

        external_compact = {
            "status": external_reference.get("status"),
            "selected_files": external_reference.get("selected_files", []),
            "selected_reference_count": external_reference.get("selected_reference_count", 0),
            "decision_reference_count": external_reference.get("decision_reference_count", 0),
            "unique_driver_count": external_reference.get("unique_driver_count", 0),
            "decision_ready": external_reference.get("decision_ready", False),
            "aggregate": external_reference.get("aggregate", {}),
            "top_losses": [
                {
                    "corner_id": item.get("id"),
                    "speed_class": item.get("speed_class"),
                    "issue_type": item.get("issue_type"),
                    "delta_s": item.get("delta_s"),
                    "reference_consensus": item.get("reference_consensus", {}),
                    "evidence": item.get("evidence", [])[:4],
                }
                for item in external_top_losses
            ],
        }

        compact_analysis = {
            "file": result.get("file"),
            "session": session,
            "session_policy": result.get("session_policy"),
            "data_quality": quality,
            "comparison": result.get("comparison"),
            "lap_selection": {
                key: value
                for key, value in (result.get("lap_selection") or {}).items()
                if key != "profiles"
            },
            "external_reference": external_compact,
            "performance": {
                "classification": classification,
                "repetition": result.get("performance", {}).get("repetition"),
                "top_losses": top_losses,
                "top_gains": result.get("performance", {}).get("top_gains", [])[:3],
            },
            "vehicle": result.get("vehicle"),
            "dynamic_controls": result.get("dynamic_controls"),
            "setup_recommendation": setup_recommendation,
            "coaching": result.get("coaching"),
            "user_feedback": request.user_feedback.strip(),
        }

        out_lap = self._out_lap_fingerprint()
        memory_summary = {
            "best_lap_s": comparison.get("file_best_time_s") or session.get("session_best_time_s"),
            "best_lap": comparison.get("file_best_time") or session.get("session_best_time"),
            "classification": label,
            "classification_confidence": classification.get("confidence"),
            "setup_decision": setup_recommendation.get("decision"),
            "target_lap": comparison.get("target_lap"),
            "reference_lap": comparison.get("reference_lap"),
            "lap_selection": {
                key: value
                for key, value in (result.get("lap_selection") or {}).items()
                if key not in {"profiles", "policy_note"}
            },
            "total_delta_s": comparison.get("total_delta_s"),
            "recoverable_time_s": comparison.get("recoverable_time_s"),
            "top_issues": [
                {
                    "corner_id": item.get("id"),
                    "speed_class": item.get("speed_class"),
                    "issue_type": item.get("issue_type"),
                    "delta_s": item.get("delta_s"),
                    "evidence": item.get("evidence", [])[:4],
                    "driver_action": item.get("driver_action"),
                }
                for item in top_losses
            ],
            "setup_adjustments": setup_recommendation.get("adjustments", []),
            "setup_test_candidates": setup_recommendation.get("test_candidates", []),
            "external_reference": external_compact,
            "out_lap_aggregate": out_lap.get("aggregate", {}),
        }

        file_stat = self.telemetry.path.stat()
        return {
            "session": {
                "session_id": session_id,
                "file_hash": file_hash,
                "filename": self.telemetry.path.name,
                "file_path": str(self.telemetry.path.resolve()),
                "file_size_bytes": file_stat.st_size,
                "file_modified_at": file_stat.st_mtime,
                "recorded_at": file_stat.st_mtime,
                "analyzed_at": time.time(),
                "analysis_version": str(result.get("analysis_version") or APP_VERSION),
                "session_mode": request.session_mode.value,
                "pit_rule": request.pit_rule.value,
                "car_name": session.get("car_name"),
                "car_path": session.get("car_path"),
                "track_name": session.get("track_name"),
                "track_config": session.get("track_config"),
                "track_temp_c": session.get("track_temp_c"),
                "air_temp_c": session.get("air_temp_c"),
                "setup_name": session.get("setup_name"),
                "best_lap_s": comparison.get("file_best_time_s") or session.get("session_best_time_s"),
                "valid_lap_count": quality.get("valid_laps", 0),
                "complete_lap_count": quality.get("complete_laps", 0),
                "out_lap_count": quality.get("out_laps", 0),
                "classification": label,
                "classification_confidence": classification.get("confidence"),
                "setup_decision": setup_recommendation.get("decision"),
                "target_lap": comparison.get("target_lap"),
                "reference_lap": comparison.get("reference_lap"),
                "user_feedback": request.user_feedback.strip(),
                "issue_tags": sorted(issue_tags),
            },
            "setup": {
                "setup_hash": setup_hash,
                "setup_json": self.telemetry.setup,
                "setup_summary": session.get("setup", {}),
            },
            "laps": result.get("laps", []),
            "corner_metrics": self._memory_corner_metrics(),
            "out_lap": out_lap,
            "memory_summary": memory_summary,
            "analysis": compact_analysis,
        }

    def _memory_corner_metrics(self) -> list[dict[str, Any]]:
        """Persist each valid lap's corner metrics against the file-best lap."""

        if not self.valid_laps:
            return []
        reference = min(
            self.valid_laps,
            key=lambda lap: float(lap.lap_time_s or float("inf")),
        )
        reference_grid = self._lap_grid(reference)
        definitions = self._detect_corners(reference_grid)
        rows: list[dict[str, Any]] = []
        for lap in self.valid_laps[:200]:
            grid = self._lap_grid(lap)
            corners = self._corner_analysis(grid, reference_grid, definitions)
            for corner in corners:
                row = dict(corner)
                row["lap_number"] = lap.lap_number
                row["reference_lap"] = reference.lap_number
                row["lap_time_s"] = round_or_none(lap.lap_time_s, 4)
                rows.append(row)
        return rows

    def _out_lap_fingerprint(self) -> dict[str, Any]:
        """Summarize warm-up behavior without storing raw telemetry samples."""

        out_laps = [lap for lap in self.telemetry.laps if lap.status == "OUT_LAP"]
        if not out_laps:
            return {"available": False, "runs": []}

        speed = self.channels.get("Speed")
        brake = self.channels.get("Brake")
        throttle = self.channels.get("Throttle")
        steering = self.channels.get("SteeringWheelAngle")
        abs_active = self.channels.get("BrakeABSactive")
        session_time = self.channels.get("SessionTime")
        tire_temp_channels = [
            name
            for name in [
                "LFtempL", "LFtempM", "LFtempR", "RFtempL", "RFtempM", "RFtempR",
                "LRtempL", "LRtempM", "LRtempR", "RRtempL", "RRtempM", "RRtempR",
            ]
            if name in self.channels
        ]
        pressure_channels = [
            name for name in ["LFpressure", "RFpressure", "LRpressure", "RRpressure"]
            if name in self.channels
        ]

        def endpoint_delta(names: list[str], start: int, end: int) -> Optional[float]:
            if not names:
                return None
            window = max(1, int(self.telemetry.tick_rate))
            start_values: list[float] = []
            end_values: list[float] = []
            for name in names:
                values = self.channels[name].astype(float, copy=False)
                start_values.extend(values[start : min(end + 1, start + window)].tolist())
                end_values.extend(values[max(start, end - window + 1) : end + 1].tolist())
            if not start_values or not end_values:
                return None
            return float(np.nanmedian(end_values) - np.nanmedian(start_values))

        fingerprints: list[dict[str, Any]] = []
        for lap in out_laps:
            start, end = lap.start_idx, lap.end_idx
            if end <= start:
                continue
            count = end - start + 1
            dt = 1.0 / max(1, self.telemetry.tick_rate)
            if session_time is not None:
                duration = float(session_time[end] - session_time[start])
            else:
                duration = count * dt
            lap_brake = brake[start : end + 1].astype(float, copy=False) if brake is not None else np.zeros(count)
            lap_speed = speed[start : end + 1].astype(float, copy=False) if speed is not None else np.zeros(count)
            lap_throttle = throttle[start : end + 1].astype(float, copy=False) if throttle is not None else np.zeros(count)
            lap_steering = steering[start : end + 1].astype(float, copy=False) if steering is not None else np.zeros(count)
            brake_groups = contiguous_groups(
                np.flatnonzero(lap_brake > 0.08),
                max_gap=max(1, int(self.telemetry.tick_rate * 0.08)),
                min_len=max(1, int(self.telemetry.tick_rate * 0.05)),
            )
            abs_ratio: Optional[float] = None
            braking_mask = lap_brake > 0.08
            if abs_active is not None and np.any(braking_mask):
                lap_abs = abs_active[start : end + 1].astype(float, copy=False) > 0.5
                abs_ratio = float(np.mean(lap_abs[braking_mask]))
            speed_mps = lap_speed
            brake_energy_proxy = float(np.sum(lap_brake * np.square(speed_mps) * dt))
            steering_work_deg = float(np.sum(np.abs(np.diff(np.degrees(lap_steering)))))
            final_start = start + int(count * 0.8)
            final_speed = (
                float(np.nanmean(speed[final_start : end + 1]) * 3.6)
                if speed is not None and end >= final_start
                else None
            )
            fingerprints.append(
                {
                    "lap_number": lap.lap_number,
                    "duration_s": round_or_none(duration, 2),
                    "brake_events": len(brake_groups),
                    "brake_energy_proxy": round_or_none(brake_energy_proxy, 1),
                    "full_throttle_ratio": round_or_none(float(np.mean(lap_throttle >= 0.95)), 3),
                    "steering_work_deg": round_or_none(steering_work_deg, 1),
                    "abs_duty_while_braking_pct": round_or_none(abs_ratio * 100.0, 1)
                    if abs_ratio is not None
                    else None,
                    "final_20pct_mean_speed_kph": round_or_none(final_speed, 1),
                    "surface_temp_delta_c": round_or_none(
                        endpoint_delta(tire_temp_channels, start, end), 2
                    ),
                    "pressure_delta_kpa": round_or_none(
                        endpoint_delta(pressure_channels, start, end), 2
                    ),
                }
            )

        return {
            "available": bool(fingerprints),
            "runs": fingerprints,
            "aggregate": {
                "brake_events_median": round_or_none(
                    float(np.median([item["brake_events"] for item in fingerprints])), 1
                )
                if fingerprints
                else None,
                "steering_work_deg_median": round_or_none(
                    float(np.median([item["steering_work_deg"] for item in fingerprints])), 1
                )
                if fingerprints
                else None,
                "surface_temp_delta_c_median": round_or_none(
                    mean_finite([item.get("surface_temp_delta_c") for item in fingerprints]), 2
                ),
                "pressure_delta_kpa_median": round_or_none(
                    mean_finite([item.get("pressure_delta_kpa") for item in fingerprints]), 2
                ),
            },
        }

    # ------------------------------------------------------------------
    # Quality/session/laps
    # ------------------------------------------------------------------

    def _session_summary(self) -> dict[str, Any]:
        metadata = self.telemetry.metadata
        track_temp = self.channels.get("TrackTempCrew")
        if track_temp is None:
            track_temp = self.channels.get("TrackTemp")
        air_temp = self.channels.get("AirTemp")
        setup_summary = extract_setup_summary(self.telemetry.setup)
        session_best = self._session_best_time()
        conditions = telemetry_reference_conditions(self.telemetry)
        return {
            "car_name": metadata.get("car_name"),
            "car_path": metadata.get("car_path"),
            "track_name": metadata.get("track_name"),
            "track_config": metadata.get("track_config"),
            "track_platform_id": metadata.get("track_platform_id"),
            "track_path": metadata.get("track_path"),
            "track_length_m": round_or_none(metadata.get("track_length_m"), 1),
            "track_turns": metadata.get("track_turns"),
            "track_version": metadata.get("track_version"),
            "setup_name": metadata.get("setup_name"),
            "setup_modified": metadata.get("setup_modified"),
            "setup_passed_tech": metadata.get("setup_passed_tech"),
            "track_temp_c": round_or_none(float(np.nanmedian(track_temp)) if track_temp is not None else None, 1),
            "air_temp_c": round_or_none(float(np.nanmedian(air_temp)) if air_temp is not None else None, 1),
            "environment": conditions,
            "relative_humidity_pct": conditions.get("relative_humidity_pct"),
            "air_pressure_hpa": conditions.get("air_pressure_hpa"),
            "air_density_kg_m3": conditions.get("air_density_kg_m3"),
            "wind_speed_kph": conditions.get("wind_speed_kph"),
            "wind_direction_deg": conditions.get("wind_direction_deg"),
            "precipitation_pct": conditions.get("precipitation_pct"),
            "weather": conditions.get("weather"),
            "session_best_time_s": round_or_none(session_best, 4),
            "session_best_time": format_lap_time(session_best),
            "setup": setup_summary,
            "setup_capabilities": vehicle_setup_capabilities(metadata, setup_summary),
        }

    def _data_quality(
        self,
        session_mode: SessionMode = SessionMode.BASELINE_3LAP,
    ) -> dict[str, Any]:
        policy = session_policy(session_mode)
        available = set(self.channels)
        missing_core = sorted(CORE_CHANNELS - available)
        feature_support = {
            feature: required.issubset(available) for feature, required in FEATURE_CHANNELS.items()
        }
        valid_count = len(self.valid_laps)
        complete_count = sum(1 for lap in self.telemetry.laps if lap.complete)
        out_lap_count = sum(1 for lap in self.telemetry.laps if lap.status == "OUT_LAP")
        invalid_count = sum(1 for lap in self.telemetry.laps if lap.status == "INVALID")
        dynamic_channels: dict[str, str] = {}
        for name in [
            "LFtempCL",
            "RFtempCL",
            "LRtempCL",
            "RRtempCL",
            "LFwearL",
            "RFwearL",
            "LRwearL",
            "RRwearL",
        ]:
            values = self.channels.get(name)
            if values is None:
                dynamic_channels[name] = "MISSING"
            elif float(np.nanmax(values) - np.nanmin(values)) < 1e-5:
                dynamic_channels[name] = "STATIC_PIT_SNAPSHOT"
            else:
                dynamic_channels[name] = "DYNAMIC"

        target_laps = max(1, int(policy["quality_target_laps"]))
        core_score = 0.60 * (1.0 - len(missing_core) / max(1, len(CORE_CHANNELS)))
        feature_score = 0.20 * (sum(feature_support.values()) / max(1, len(feature_support)))
        lap_score = 0.20 * min(valid_count / target_laps, 1.0)
        score = max(0.0, min(1.0, core_score + feature_score + lap_score))

        messages: list[str] = []
        if missing_core:
            state = "MISSING"
            messages.append("필수 채널 누락: " + ", ".join(missing_core))
        elif valid_count < int(policy["minimum_analysis_laps"]):
            state = "MISSING"
            messages.append("분석 가능한 유효 계측랩이 없습니다.")
        elif session_mode == SessionMode.QUALIFYING and valid_count < 2:
            state = "PARTIAL"
            messages.append(
                "퀄리파잉은 아웃랩 1바퀴와 계측랩 최대 2바퀴를 정상 구조로 처리합니다. "
                "현재 유효 계측랩은 1개이므로 단일 랩 진단만 수행합니다."
            )
        elif valid_count < target_laps:
            state = "PARTIAL"
            if session_mode == SessionMode.ENDURANCE:
                messages.append(
                    f"유효 계측랩 {valid_count}개입니다. 롱런 추세 신뢰도를 위해 {target_laps}개 이상을 권장합니다."
                )
            else:
                messages.append(
                    f"유효 계측랩 {valid_count}개입니다. {policy['label']} 판정 목표는 {target_laps}개입니다."
                )
        elif not all(feature_support.values()):
            state = "PARTIAL"
            messages.append("일부 고급 진단 채널이 없어 해당 기능의 신뢰도가 제한됩니다.")
        else:
            state = "VALID"
            if session_mode == SessionMode.QUALIFYING:
                messages.append(
                    "퀄리파잉 계측랩 2개 기준을 충족했습니다. 두 핫랩의 거리 델타와 코너 입력을 직접 비교합니다."
                )
            else:
                messages.append("핵심 채널과 해당 세션 모드의 표본 기준이 확보되었습니다.")

        if session_mode == SessionMode.QUALIFYING and valid_count >= 2:
            messages.append(
                "두 계측랩만으로 셋업을 확정하지는 않습니다. 다만 내부 약한 패턴이나 Garage61의 2개 이상 빠른 기준 컨센서스가 있으면 EXPERIMENTAL A/B 후보는 생성할 수 있습니다."
            )

        static_snapshots = [name for name, status in dynamic_channels.items() if status == "STATIC_PIT_SNAPSHOT"]
        if static_snapshots:
            messages.append("카커스 온도·마모 채널은 주행 중 갱신되지 않는 피트 스냅샷으로 분리했습니다.")

        clean = self._clean_context()
        coaching_ready = bool(clean["coaching_ready"] and not missing_core)
        if coaching_ready:
            messages.append(f"무효·미완주 랩에서 정상 구간 {len(clean['segments'])}개를 코칭에 활용합니다. 공식 페이스와 A/B 표본에는 포함하지 않습니다.")
            if state == "MISSING" and not missing_core:
                state = "PARTIAL"

        return {
            "state": state,
            "score": round(score, 2),
            "session_mode": session_mode.value,
            "expected_run_structure": policy["expected_run_structure"],
            "required_timed_laps": policy["quality_target_laps"],
            "recommended_timed_laps": policy["recommended_timed_laps"],
            "timed_lap_comparison_ready": valid_count >= int(policy["comparison_laps"]),
            "analysis_ready": valid_count >= int(policy["minimum_analysis_laps"]) and not missing_core,
            "coaching_ready": coaching_ready or (valid_count >= 1 and not missing_core),
            "clean_segment_count": len(clean["segments"]),
            "analysis_scope": "VALID_LAPS" if valid_count else "CLEAN_SEGMENTS_ONLY" if coaching_ready else "UNAVAILABLE",
            "missing_core_channels": missing_core,
            "feature_support": feature_support,
            "valid_laps": valid_count,
            "complete_laps": complete_count,
            "out_laps": out_lap_count,
            "invalid_laps": invalid_count,
            "setup_decision_ready": valid_count >= int(policy["setup_repeat_laps"]) and not missing_core,
            "setup_evidence_scope": (
                "QUALIFYING_LIMITED_CURRENT_RUN"
                if session_mode == SessionMode.QUALIFYING
                else "STANDARD_REPEATABILITY"
            ),
            "channel_count": len(self.telemetry.variables),
            "loaded_channel_count": len(self.channels),
            "dynamic_channel_status": dynamic_channels,
            "messages": messages,
        }

    def _lap_reproducibility_profiles(self) -> dict[str, Any]:
        """Estimate whether each valid lap is supported by a similar pace/trace peer.

        This is intentionally conservative: a one-off fastest lap is not treated as the
        default setup-analysis target when a slightly slower, repeatable cluster exists.
        Manual lap selection always overrides this policy.
        """

        if not self.valid_laps:
            return {
                "pace_tolerance_s": None,
                "trace_tolerance_s": None,
                "profiles": [],
                "repeatable_lap_count": 0,
            }

        lap_times = np.array([float(lap.lap_time_s or 0.0) for lap in self.valid_laps], dtype=float)
        median_time = float(np.median(lap_times))
        # About 0.5% of lap time, bounded so short/long circuits behave sensibly.
        pace_tolerance_s = float(max(0.35, min(1.50, median_time * 0.005)))
        # Residual cumulative-delta RMS after removing total lap-time delta.
        trace_tolerance_s = float(max(0.12, min(0.45, median_time * 0.0015)))

        profiles: dict[int, dict[str, Any]] = {}
        for lap in self.valid_laps:
            profiles[lap.lap_number] = {
                "lap_number": lap.lap_number,
                "lap_time_s": round_or_none(lap.lap_time_s, 4),
                "peer_laps": [],
                "support_count": 0,
                "pace_peer_count": 0,
                "nearest_time_delta_s": None,
                "trace_rms_to_peers_s": None,
                "reproducibility_status": "ISOLATED",
                "reproducible": False,
            }

        trace_rms_values: dict[int, list[float]] = {lap.lap_number: [] for lap in self.valid_laps}
        time_deltas: dict[int, list[float]] = {lap.lap_number: [] for lap in self.valid_laps}

        for left_idx, left in enumerate(self.valid_laps):
            left_time = float(left.lap_time_s or 0.0)
            left_grid = self._lap_grid(left)
            for right in self.valid_laps[left_idx + 1 :]:
                right_time = float(right.lap_time_s or 0.0)
                time_delta_s = abs(left_time - right_time)
                time_deltas[left.lap_number].append(time_delta_s)
                time_deltas[right.lap_number].append(time_delta_s)
                if time_delta_s > pace_tolerance_s:
                    continue

                profiles[left.lap_number]["pace_peer_count"] += 1
                profiles[right.lap_number]["pace_peer_count"] += 1

                right_grid = self._lap_grid(right)
                left_elapsed = left_grid.get("time_s")
                right_elapsed = right_grid.get("time_s")
                if left_elapsed is None or right_elapsed is None or len(left_elapsed) < 10 or len(right_elapsed) != len(left_elapsed):
                    # Pace similarity is still useful if the trace cannot be evaluated.
                    residual_rms_s = math.inf
                else:
                    delta = np.asarray(left_elapsed, dtype=float) - np.asarray(right_elapsed, dtype=float)
                    supported = np.isfinite(delta)
                    supported &= np.asarray(left_grid.get("_coverage", np.ones(len(delta), dtype=bool)))
                    supported &= np.asarray(right_grid.get("_coverage", np.ones(len(delta), dtype=bool)))
                    linear_total_delta = (left_time - right_time) * np.asarray(left_grid["distance_pct"])
                    residual = delta[supported] - linear_total_delta[supported]
                    residual_rms_s = float(np.sqrt(np.mean(np.square(residual)))) if np.mean(supported) >= .95 else math.inf

                if residual_rms_s > trace_tolerance_s:
                    continue

                profiles[left.lap_number]["peer_laps"].append(right.lap_number)
                profiles[right.lap_number]["peer_laps"].append(left.lap_number)
                trace_rms_values[left.lap_number].append(residual_rms_s)
                trace_rms_values[right.lap_number].append(residual_rms_s)

        repeatable_lap_count = 0
        for lap in self.valid_laps:
            profile = profiles[lap.lap_number]
            peers = sorted(set(int(value) for value in profile["peer_laps"]))
            profile["peer_laps"] = peers
            profile["support_count"] = len(peers)
            if time_deltas[lap.lap_number]:
                profile["nearest_time_delta_s"] = round_or_none(min(time_deltas[lap.lap_number]), 3)
            if trace_rms_values[lap.lap_number]:
                profile["trace_rms_to_peers_s"] = round_or_none(
                    float(np.median(trace_rms_values[lap.lap_number])), 3
                )
            if len(peers) >= 2:
                profile["reproducibility_status"] = "STRONG"
                profile["reproducible"] = True
                repeatable_lap_count += 1
            elif len(peers) == 1:
                profile["reproducibility_status"] = "SUPPORTED"
                profile["reproducible"] = True
                repeatable_lap_count += 1

        return {
            "pace_tolerance_s": round_or_none(pace_tolerance_s, 3),
            "trace_tolerance_s": round_or_none(trace_tolerance_s, 3),
            "profiles": [profiles[lap.lap_number] for lap in sorted(self.valid_laps, key=lambda item: item.lap_number)],
            "repeatable_lap_count": repeatable_lap_count,
        }

    def _lap_table(self, lap_selection: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        valid_times = [lap.lap_time_s for lap in self.valid_laps if lap.lap_time_s]
        best = min(valid_times) if valid_times else None
        profile_by_lap = {
            int(item.get("lap_number")): item
            for item in (lap_selection or {}).get("profiles", [])
            if item.get("lap_number") is not None
        }
        rows: list[dict[str, Any]] = []
        for lap in self.telemetry.laps:
            delta = lap.lap_time_s - best if best and lap.lap_time_s and lap.valid else None
            profile = profile_by_lap.get(lap.lap_number, {})
            roles: list[str] = []
            if (lap_selection or {}).get("target_lap") == lap.lap_number:
                roles.append("TARGET")
            if (lap_selection or {}).get("reference_lap") == lap.lap_number:
                roles.append("REFERENCE")
            rows.append(
                {
                    "lap_number": lap.lap_number,
                    "status": lap.status,
                    "lap_time_s": round_or_none(lap.lap_time_s, 4),
                    "lap_time": format_lap_time(lap.lap_time_s),
                    "delta_to_file_best_s": round_or_none(delta, 3),
                    "is_file_best": bool(best and lap.valid and lap.lap_time_s and abs(lap.lap_time_s - best) < 1e-4),
                    "start_pct": round(lap.start_pct, 4),
                    "end_pct": round(lap.end_pct, 4),
                    "on_track_ratio": round(lap.on_track_ratio, 4),
                    "fuel_used_l": round_or_none(lap.fuel_used_l, 3),
                    "invalid_reason": lap.invalid_reason,
                    "reproducible": bool(profile.get("reproducible", False)),
                    "reproducibility_status": profile.get("reproducibility_status"),
                    "reproducibility_support": int(profile.get("support_count", 0) or 0),
                    "reproducibility_peer_laps": profile.get("peer_laps", []),
                    "reproducibility_trace_rms_s": profile.get("trace_rms_to_peers_s"),
                    "selection_roles": roles,
                }
            )
        return rows

    def _select_laps(
        self,
        requested_target: Optional[int],
        requested_reference: Optional[int],
    ) -> tuple[Optional[LapSegment], Optional[LapSegment], dict[str, Any]]:
        if not self.valid_laps:
            windows = self._clean_context()["lap_windows"]
            eligible = [lap for lap in self.telemetry.laps if lap.lap_number in windows]
            if eligible:
                target = next((lap for lap in eligible if lap.lap_number == requested_target), None)
                if target is None:
                    target = max(eligible, key=lambda lap: sum(window["distance_m"] for window in windows[lap.lap_number]))
                return replace(target, lap_time_s=None), None, {
                    "strategy": "CLEAN_SEGMENT_COACHING", "valid_lap_count": 0,
                    "target_lap": target.lap_number, "target_time_s": None, "reference_lap": None,
                    "target_mode": "MANUAL" if requested_target == target.lap_number else "AUTO",
                    "reference_mode": "UNAVAILABLE", "target_reason": "CLEAN_SEGMENTS_ONLY",
                    "reference_reason": "NO_VALID_LAP", "profiles": [], "official_valid_lap": False,
                    "policy_note": self._clean_context()["reason"],
                }
            return None, None, {
                "strategy": "FASTEST_REPRODUCIBLE",
                "target_lap": None,
                "reference_lap": None,
                "target_mode": "AUTO",
                "reference_mode": "AUTO",
                "target_reason": "NO_VALID_LAP",
                "reference_reason": "NO_VALID_LAP",
                "profiles": [],
            }

        by_number = {lap.lap_number: lap for lap in self.valid_laps}
        reproducibility = self._lap_reproducibility_profiles()
        profile_by_lap = {
            int(item["lap_number"]): item for item in reproducibility.get("profiles", [])
        }
        repeatable_laps = [
            lap
            for lap in self.valid_laps
            if profile_by_lap.get(lap.lap_number, {}).get("reproducible")
        ]

        requested_target_lap = by_number.get(requested_target) if requested_target is not None else None
        if requested_target_lap is not None:
            target = requested_target_lap
            target_mode = "MANUAL"
            target_reason = "USER_SELECTED"
        elif repeatable_laps:
            target = min(repeatable_laps, key=lambda lap: (lap.lap_time_s or math.inf, lap.lap_number))
            target_mode = "AUTO"
            target_reason = "FASTEST_REPRODUCIBLE"
        else:
            target = min(self.valid_laps, key=lambda lap: (lap.lap_time_s or math.inf, lap.lap_number))
            target_mode = "AUTO"
            target_reason = "FASTEST_VALID_FALLBACK"

        requested_reference_lap = by_number.get(requested_reference) if requested_reference is not None else None
        if requested_reference_lap is not None and requested_reference_lap.lap_number != target.lap_number:
            reference = requested_reference_lap
            reference_mode = "MANUAL"
            reference_reason = "USER_SELECTED"
        else:
            reference = None
            reference_mode = "AUTO"
            target_profile = profile_by_lap.get(target.lap_number, {})
            direct_peer_numbers = set(int(value) for value in target_profile.get("peer_laps", []))
            direct_peers = [
                by_number[number]
                for number in direct_peer_numbers
                if number in by_number and number != target.lap_number
            ]
            if direct_peers:
                reference = min(direct_peers, key=lambda lap: (lap.lap_time_s or math.inf, lap.lap_number))
                reference_reason = "FASTEST_SUPPORTING_PEER"
            else:
                repeatable_alternatives = [
                    lap for lap in repeatable_laps if lap.lap_number != target.lap_number
                ]
                if repeatable_alternatives:
                    reference = min(
                        repeatable_alternatives,
                        key=lambda lap: (lap.lap_time_s or math.inf, lap.lap_number),
                    )
                    reference_reason = "SECOND_FASTEST_REPRODUCIBLE"
                else:
                    alternatives = [
                        lap for lap in self.valid_laps if lap.lap_number != target.lap_number
                    ]
                    reference = (
                        min(alternatives, key=lambda lap: (lap.lap_time_s or math.inf, lap.lap_number))
                        if alternatives
                        else None
                    )
                    reference_reason = "FASTEST_ALTERNATIVE_FALLBACK" if reference else "NO_ALTERNATIVE_LAP"

        target_profile = profile_by_lap.get(target.lap_number, {})
        reference_profile = profile_by_lap.get(reference.lap_number, {}) if reference else {}
        selection = {
            "strategy": "FASTEST_REPRODUCIBLE",
            "valid_lap_count": len(self.valid_laps),
            "repeatable_lap_count": int(reproducibility.get("repeatable_lap_count", 0) or 0),
            "pace_tolerance_s": reproducibility.get("pace_tolerance_s"),
            "trace_tolerance_s": reproducibility.get("trace_tolerance_s"),
            "target_lap": target.lap_number,
            "target_time_s": round_or_none(target.lap_time_s, 4),
            "target_mode": target_mode,
            "target_reason": target_reason,
            "target_support_count": int(target_profile.get("support_count", 0) or 0),
            "target_peer_laps": target_profile.get("peer_laps", []),
            "target_reproducibility_status": target_profile.get("reproducibility_status"),
            "reference_lap": reference.lap_number if reference else None,
            "reference_time_s": round_or_none(reference.lap_time_s, 4) if reference else None,
            "reference_mode": reference_mode,
            "reference_reason": reference_reason,
            "reference_support_count": int(reference_profile.get("support_count", 0) or 0),
            "reference_peer_laps": reference_profile.get("peer_laps", []),
            "reference_reproducibility_status": reference_profile.get("reproducibility_status"),
            "profiles": reproducibility.get("profiles", []),
            "policy_note": (
                "Automatic setup analysis uses the fastest VALID lap that is supported by a similar pace and distance-normalized trace peer. "
                "A one-off isolated file-best lap is skipped when a repeatable fast cluster exists. Manual selection overrides this policy."
            ),
        }
        return target, reference, selection

    def _session_best_time(self) -> Optional[float]:
        values = self.channels.get("LapBestLapTime")
        if values is None:
            return None
        valid = values[np.isfinite(values) & (values > 0)]
        return float(np.nanmedian(valid)) if len(valid) else None

    # ------------------------------------------------------------------
    # Distance-normalized laps and comparison
    # ------------------------------------------------------------------

    def _lap_grid(self, lap: LapSegment) -> dict[str, np.ndarray[Any, Any]]:
        cached = self._grid_cache.get(lap.lap_number)
        if cached is not None:
            return cached

        start, end = lap.start_idx, lap.end_idx
        raw_distance = self.channels["LapDistPct"][start : end + 1].astype(float)
        order = np.argsort(raw_distance)
        distance = raw_distance[order]
        unique = np.r_[True, np.diff(distance) > 1e-7]
        distance = distance[unique]
        grid = np.linspace(0.0, 1.0, self.grid_points)

        output: dict[str, np.ndarray[Any, Any]] = {"distance_pct": grid}
        source_times = self.channels.get("SessionTime")
        sample_times = source_times[start : end + 1].astype(float)[order][unique] if source_times is not None else None
        channel_names = [
            "SessionTime",
            "Speed",
            "Throttle",
            "Brake",
            "BrakeABSactive",
            "SteeringWheelAngle",
            "YawRate",
            "LatAccel",
            "LongAccel",
            "VertAccel",
            "Gear",
            "Lat",
            "Lon",
            "LFrideHeight",
            "RFrideHeight",
            "LRrideHeight",
            "RRrideHeight",
            "CFSRrideHeight",
        ]
        for name in channel_names:
            values = self.channels.get(name)
            if values is None:
                continue
            raw = values[start : end + 1].astype(float)[order][unique]
            if len(raw) == 0:
                continue
            output[name] = interpolate_observed(distance, raw, grid, self.track_length_m, sample_times, stepped=name in {"Gear", "BrakeABSactive"})

        session_time = output.get("SessionTime")
        if session_time is None:
            output["time_s"] = np.full_like(grid, np.nan)
        else:
            # Use the real lap origin. Missing first/last samples stay missing;
            # forcing their times onto 0/official duration distorts every corner.
            current_time = self.channels.get("LapCurrentLapTime")
            origin_offset = float(current_time[start]) if current_time is not None and math.isfinite(float(current_time[start])) else 0.0
            source_origin = float(source_times[start]) if source_times is not None else 0.0
            elapsed = session_time - source_origin + origin_offset
            output["time_s"] = elapsed

        if "Speed" in output:
            output["speed_kph"] = output["Speed"] * 3.6
        output["_coverage"] = np.isfinite(output["time_s"]) & np.isfinite(output.get("speed_kph", np.full(len(grid), np.nan)))
        output["_channel_support"] = {key: np.isfinite(value) for key, value in output.items() if isinstance(value, np.ndarray) and key != "distance_pct" and not key.startswith("_")}
        self._grid_cache[lap.lap_number] = output
        self._grid_ids[id(output)] = (output, ("lap", lap.lap_number))
        return output

    def _comparison(
        self,
        target: LapSegment,
        reference: Optional[LapSegment],
        target_grid: dict[str, np.ndarray[Any, Any]],
        reference_grid: Optional[dict[str, np.ndarray[Any, Any]]],
    ) -> dict[str, Any]:
        session_best = self._session_best_time()
        comparison: dict[str, Any] = {
            "target_lap": target.lap_number,
            "target_time_s": round_or_none(target.lap_time_s, 4),
            "target_time": format_lap_time(target.lap_time_s),
            "reference_lap": reference.lap_number if reference else None,
            "reference_time_s": round_or_none(reference.lap_time_s, 4) if reference else None,
            "reference_time": format_lap_time(reference.lap_time_s) if reference else None,
            "total_delta_s": round_or_none(
                (target.lap_time_s or 0) - (reference.lap_time_s or 0), 4
            )
            if reference
            else None,
            "session_best_time_s": round_or_none(session_best, 4),
            "session_best_time": format_lap_time(session_best),
            "gap_to_session_best_s": round_or_none((target.lap_time_s or 0) - session_best, 4)
            if session_best
            else None,
            "session_best_trace_available": bool(
                session_best
                and any(
                    lap.lap_time_s and abs(lap.lap_time_s - session_best) <= 0.05
                    for lap in self.valid_laps
                )
            ),
        }

        optimal = self._personal_optimal()
        comparison.update(optimal)
        if reference_grid is not None:
            delta = target_grid["time_s"] - reference_grid["time_s"]
            finite_delta = delta[np.isfinite(delta)]
            comparison["delta_min_s"] = round_or_none(float(np.min(finite_delta)), 3) if len(finite_delta) else None
            comparison["delta_max_s"] = round_or_none(float(np.max(finite_delta)), 3) if len(finite_delta) else None
        return comparison

    def _personal_optimal(self, segments: int = 20) -> dict[str, Any]:
        if not self.valid_laps:
            return {
                "file_best_time_s": None,
                "theoretical_optimal_time_s": None,
                "reproducible_optimal_time_s": None,
            }
        grids = [self._lap_grid(lap) for lap in self.valid_laps]
        lap_times = np.array([lap.lap_time_s for lap in self.valid_laps], dtype=float)
        best_time = float(np.min(lap_times))
        boundaries = np.linspace(0, self.grid_points - 1, segments + 1, dtype=int)
        segment_times = np.full((len(grids), segments), np.inf, dtype=float)
        boundary_speeds = np.full((len(grids), segments + 1), np.nan, dtype=float)
        for lap_idx, grid in enumerate(grids):
            times = grid["time_s"].copy()
            coverage = np.asarray(grid.get("_coverage", np.isfinite(times)), dtype=bool).copy()
            # Official lap timing anchors optimal calculation only. The real
            # graph endpoints remain unobserved; interior holes stay blocked.
            times[0], times[-1] = 0.0, float(self.valid_laps[lap_idx].lap_time_s)
            coverage[0] = coverage[-1] = True
            speeds = grid.get("speed_kph", np.full(self.grid_points, np.nan))
            for segment in range(segments):
                start, end = boundaries[segment], boundaries[segment + 1]
                duration = times[end] - times[start]
                if np.all(coverage[start:end + 1]) and math.isfinite(float(duration)) and duration >= 0:
                    segment_times[lap_idx, segment] = duration
            boundary_speeds[lap_idx] = speeds[boundaries]

        theoretical = float(np.sum(np.min(segment_times, axis=0)))
        if not math.isfinite(theoretical):
            return {"file_best_time_s": round(best_time, 4), "file_best_time": format_lap_time(best_time),
                    "theoretical_optimal_time_s": None, "reproducible_optimal_time_s": None,
                    "recoverable_time_s": None, "optimal_status": "INCOMPLETE_SECTOR_COVERAGE",
                    "optimal_endpoint_basis": "OFFICIAL_LAP_TIMES"}

        # Dynamic programming penalizes switching between segments whose boundary
        # speeds are physically incompatible. This is more realistic than simply
        # concatenating every fastest micro-sector.
        costs = segment_times[:, 0].copy()
        parents = np.full((segments, len(grids)), -1, dtype=int)
        for segment in range(1, segments):
            next_costs = np.full(len(grids), np.inf)
            for current_lap in range(len(grids)):
                for previous_lap in range(len(grids)):
                    speed_mismatch = abs(
                        boundary_speeds[previous_lap, segment]
                        - boundary_speeds[current_lap, segment]
                    )
                    if not math.isfinite(float(speed_mismatch)):
                        if current_lap != previous_lap:
                            continue
                        speed_mismatch = 0.0
                    penalty = max(0.0, speed_mismatch - 4.0) * 0.006
                    candidate = costs[previous_lap] + penalty + segment_times[current_lap, segment]
                    if candidate < next_costs[current_lap]:
                        next_costs[current_lap] = candidate
                        parents[segment, current_lap] = previous_lap
            costs = next_costs
        reproducible = float(np.min(costs))
        reproducible = max(theoretical, min(best_time, reproducible)) if math.isfinite(reproducible) else None

        return {
            "file_best_time_s": round(best_time, 4),
            "file_best_time": format_lap_time(best_time),
            "theoretical_optimal_time_s": round(theoretical, 4),
            "theoretical_optimal_time": format_lap_time(theoretical),
            "reproducible_optimal_time_s": round(reproducible, 4) if reproducible is not None else None,
            "reproducible_optimal_time": format_lap_time(reproducible),
            "recoverable_time_s": round(max(0.0, best_time - reproducible), 3) if reproducible is not None else None,
            "optimal_status": "AVAILABLE" if reproducible is not None else "INCOMPLETE_TRANSITION_COVERAGE",
            "optimal_endpoint_basis": "OFFICIAL_LAP_TIMES",
        }

    # ------------------------------------------------------------------
    # Corner detection and losses
    # ------------------------------------------------------------------

    def _detect_corners(self, grid: dict[str, np.ndarray[Any, Any]]) -> list[dict[str, int]]:
        metadata = self.telemetry.metadata
        identity = tuple(str(metadata.get(key) or "") for key in ("car_platform_id", "car_path", "car_name", "track_platform_id", "track_path", "track_name", "track_config", "track_version")) + (round(self.track_length_m),)
        n = len(grid["distance_pct"])
        with _CORNER_DEFINITIONS_LOCK:
            saved = _CORNER_DEFINITIONS.get(identity)
            if saved is None and _corner_identity_persistent(identity):
                saved = _CORNER_DEFINITION_STORE.get(identity)
                if saved is not None:
                    _CORNER_DEFINITIONS[identity] = saved
        if saved is not None:
            return [{**item, **{key: nearest_index(grid["distance_pct"], item[f"{key}_pct"]) for key in ("entry", "apex", "exit")}} for item in saved]

        # A clean local lap anchors segment identity. Changing the comparison
        # driver cannot renumber or move all segments. Invalid laps never register.
        canonical = min(self.valid_laps, key=lambda lap: lap.lap_time_s or math.inf) if getattr(self, "valid_laps", []) else None
        source = self._lap_grid(canonical) if canonical is not None else grid
        speed = source.get("speed_kph")
        if speed is None:
            return []
        source_n = len(speed)
        spacing = self.track_length_m / max(1, source_n - 1)
        metres = lambda value: max(1, int(round(value / spacing)))
        coverage = np.asarray(source.get("_coverage", np.isfinite(speed)), dtype=bool)
        coverage &= np.asarray(source.get("_analysis_coverage", np.ones(source_n, dtype=bool)), dtype=bool)
        speed_smooth = smooth(speed, max(3, metres(12) | 1))
        brake = source.get("Brake", np.zeros(source_n))
        throttle = source.get("Throttle", np.ones(source_n))
        steering = np.abs(source.get("SteeringWheelAngle", np.zeros(source_n)))
        candidates: list[dict[str, Any]] = []
        lat, lon = source.get("Lat"), source.get("Lon")
        geometry = None
        if lat is not None and lon is not None:
            valid_geo = coverage & np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 1e-7) & (np.abs(lon) > 1e-7)
            if np.mean(valid_geo) > 0.90:
                # GPS heading change per metre includes flat-out high-speed turns.
                x = np.radians(lon) * 6371000.0 * math.cos(math.radians(float(np.nanmedian(lat))))
                y = np.radians(lat) * 6371000.0
                x, y = smooth(x, max(3, metres(18) | 1)), smooth(y, max(3, metres(18) | 1))
                dx, dy = np.gradient(x), np.gradient(y)
                heading = np.arctan2(dy, dx)
                turns = np.abs(np.arctan2(np.sin(np.diff(heading)), np.cos(np.diff(heading))))
                geometry = smooth(np.r_[turns, turns[-1]] / np.maximum(np.hypot(dx, dy), spacing * .25), max(3, metres(18) | 1))
                curved = valid_geo & np.isfinite(geometry) & (geometry > 0.0018)
                for entry, exit_idx in contiguous_groups(np.flatnonzero(curved), max_gap=metres(20), min_len=metres(25)):
                    # Split a complex into separated curvature peaks instead of
                    # flattening hundreds of metres into one low-speed minimum.
                    peaks = [index for index in range(entry + 1, exit_idx) if geometry[index] >= geometry[index - 1] and geometry[index] > geometry[index + 1]]
                    chosen = []
                    for index in sorted(peaks, key=lambda i: geometry[i], reverse=True):
                        if all(abs(index - existing) >= metres(70) for existing in chosen):
                            chosen.append(index)
                    if not chosen:
                        chosen = [entry + int(np.nanargmax(geometry[entry:exit_idx + 1]))]
                    chosen.sort()
                    for index, apex in enumerate(chosen):
                        left = entry if index == 0 else (chosen[index - 1] + apex) // 2
                        right = exit_idx if index == len(chosen) - 1 else (apex + chosen[index + 1]) // 2
                        candidates.append({"entry": max(0, left - metres(45)), "apex": apex, "exit": min(source_n - 1, right + metres(60)), "source": "GPS_CURVATURE"})

        brake_groups = contiguous_groups(np.flatnonzero((brake > .08) & coverage), max_gap=metres(12), min_len=max(2, metres(8)))
        for entry, brake_end in brake_groups:
            stop = min(source_n - 1, brake_end + metres(120))
            segment = speed_smooth[entry:stop + 1]
            if not np.any(np.isfinite(segment)):
                continue
            apex = entry + int(np.nanargmin(segment))
            matching = next((item for item in candidates if item["entry"] <= apex <= item["exit"] or abs(item["apex"] - apex) < metres(55)), None)
            if matching is not None:
                matching["entry"] = min(matching["entry"], entry)
                continue
            exit_idx = min(source_n - 1, apex + metres(140))
            recovery = np.flatnonzero((throttle[apex:exit_idx + 1] > .95) & (steering[apex:exit_idx + 1] < .08))
            if len(recovery):
                exit_idx = max(apex + metres(25), apex + int(recovery[0]))
            candidates.append({"entry": entry, "apex": apex, "exit": exit_idx, "source": "BRAKING_METERS"})

        if geometry is None:
            loaded = (steering > .07) | (np.abs(source.get("LatAccel", np.zeros(source_n))) > 6.0)
            for entry, exit_idx in contiguous_groups(np.flatnonzero(loaded & coverage), max_gap=metres(15), min_len=metres(30)):
                if any(item["entry"] <= (entry + exit_idx) // 2 <= item["exit"] for item in candidates):
                    continue
                candidates.append({"entry": entry, "apex": entry + int(np.nanargmin(speed_smooth[entry:exit_idx + 1])), "exit": exit_idx, "source": "LOAD_METERS"})
        candidates.sort(key=lambda item: item["apex"])
        finalized = []
        for item in candidates:
            if finalized and item["apex"] - finalized[-1]["apex"] < metres(35):
                finalized[-1]["exit"] = max(finalized[-1]["exit"], item["exit"])
                continue
            finalized.append(item)
        for previous, current in zip(finalized[:-1], finalized[1:]):
            if previous["exit"] >= current["entry"]:
                boundary = (previous["apex"] + current["apex"]) // 2
                previous["exit"] = min(previous["exit"], boundary)
                current["entry"] = max(current["entry"], boundary + 1)
        normalized = []
        edge = metres(10)
        for item in finalized:
            entry, apex, exit_idx = item["entry"], item["apex"], min(source_n - 1, item["exit"])
            # The grid end points at the start/finish line are never observed. Trim
            # a corner that reaches them instead of silently dropping it.
            if entry <= edge:
                covered = np.flatnonzero(coverage[entry:edge + 1])
                entry += int(covered[0]) if len(covered) else 0
            if exit_idx >= source_n - 1 - edge:
                covered = np.flatnonzero(coverage[source_n - 1 - edge:exit_idx + 1])
                exit_idx = source_n - 1 - edge + int(covered[-1]) if len(covered) else exit_idx
            if entry >= apex or exit_idx <= apex or not np.all(coverage[entry:exit_idx + 1]):
                continue
            normalized.append({"id": f"C{len(normalized) + 1}", "source": item["source"], **{f"{key}_pct": float(source["distance_pct"][index]) for key, index in (("entry", entry), ("apex", apex), ("exit", exit_idx))}})
        if canonical is not None and normalized:
            with _CORNER_DEFINITIONS_LOCK:
                _CORNER_DEFINITIONS.setdefault(identity, normalized)
                while len(_CORNER_DEFINITIONS) > 64:
                    _CORNER_DEFINITIONS.popitem(last=False)
                if _corner_identity_persistent(identity):
                    try:
                        _CORNER_DEFINITION_STORE.put(identity, normalized)
                    except OSError:
                        logger.debug("Corner definitions were not saved", exc_info=True)
        return [{**item, **{key: nearest_index(grid["distance_pct"], item[f"{key}_pct"]) for key in ("entry", "apex", "exit")}} for item in normalized]

    def _corner_analysis(
        self,
        target: dict[str, np.ndarray[Any, Any]],
        reference: Optional[dict[str, np.ndarray[Any, Any]]],
        definitions: list[dict[str, int]],
    ) -> list[dict[str, Any]]:
        n = len(target["distance_pct"])
        delta = target["time_s"] - reference["time_s"] if reference else np.zeros(n)
        results: list[dict[str, Any]] = []

        for number, definition in enumerate(definitions, start=1):
            entry = definition["entry"]
            apex = definition["apex"]
            exit_idx = definition["exit"]
            def supported(grid: Optional[dict[str, Any]]) -> bool:
                if grid is None:
                    return False
                mask = np.asarray(grid.get("_coverage", np.isfinite(grid["time_s"]) & np.isfinite(grid.get("speed_kph", np.full(n, np.nan)))), dtype=bool)
                mask = mask & np.asarray(grid.get("_analysis_coverage", np.ones(n, dtype=bool)), dtype=bool)
                return bool(np.all(mask[entry:exit_idx + 1]))
            target_supported = supported(target)
            reference_supported = supported(reference)
            measurement_usable = target_supported and (reference is None or reference_supported)
            local_delta = float(delta[exit_idx] - delta[entry]) if reference and measurement_usable else None
            target_metrics = self._corner_metrics(target, entry, apex, exit_idx)
            reference_metrics = self._corner_metrics(reference, entry, apex, exit_idx) if reference else None
            evidence, issue_type, driver_action = self._corner_evidence(
                local_delta,
                target_metrics,
                reference_metrics,
            )
            if not measurement_usable:
                evidence, issue_type, driver_action = ["관측되지 않은 구간이 포함되어 시간 손실과 조작 원인을 판단하지 않습니다."], "DATA_GAP", "같은 구간의 끊김 없는 주행 데이터를 기록하세요."
            elif reference is not None and not (target_metrics.get("inputs_usable") and (reference_metrics or {}).get("inputs_usable")):
                evidence, issue_type, driver_action = [f"구간 시간 차이 {local_delta:+.3f}초", "입력 채널 결측으로 제동·스로틀·조향 코칭을 제한합니다."], "INPUT_GAP", "입력 채널이 포함된 기준 랩으로 비교하세요."
            apex_speed = target_metrics["min_speed_kph"] or 0.0
            speed_class = "LOW" if apex_speed < 105 else "MEDIUM" if apex_speed < 165 else "HIGH"
            severity = (
                "HIGH"
                if local_delta is not None and local_delta >= 0.10
                else "MEDIUM"
                if local_delta is not None and local_delta >= 0.04
                else "LOW"
            )
            result = {
                "id": definition.get("id") or f"C{number}",
                "label": f"자동 구간 {number}",
                "segment_source": definition.get("source", "AUTOMATIC"),
                "official_turn_number": None,
                "measurement_usable": measurement_usable,
                "coaching_usable": measurement_usable and bool(target_metrics.get("inputs_usable")) and (reference is None or bool((reference_metrics or {}).get("inputs_usable"))),
                "entry_pct": round(float(target["distance_pct"][entry]), 7),
                "apex_pct": round(float(target["distance_pct"][apex]), 7),
                "exit_pct": round(float(target["distance_pct"][exit_idx]), 7),
                "distance_m": round(float(target["distance_pct"][apex] * self.track_length_m), 1),
                "speed_class": speed_class,
                "delta_s": round_or_none(local_delta, 3),
                "severity": severity,
                "issue_type": issue_type,
                "evidence": evidence,
                "driver_action": driver_action,
                "target": target_metrics,
                "reference": reference_metrics,
            }
            results.append(result)

        # The UI and briefing consume the largest losses first while the map still
        # uses distance ordering through its markers.
        return sorted(results, key=lambda item: item.get("delta_s") or -999, reverse=True)

    def _corner_metrics(
        self,
        grid: Optional[dict[str, np.ndarray[Any, Any]]],
        entry: int,
        apex: int,
        exit_idx: int,
    ) -> Optional[dict[str, Any]]:
        if grid is None:
            return None
        # getattr: partial analyzers built without __init__ simply skip the memo.
        registered = getattr(self, "_grid_ids", {}).get(id(grid))
        if registered is None or registered[0] is not grid:
            return self._compute_corner_metrics(grid, entry, apex, exit_idx)
        key = (registered[1], int(entry), int(apex), int(exit_idx))
        cached = self._metric_cache.get(key)
        if cached is None:
            cached = self._metric_cache[key] = self._compute_corner_metrics(grid, entry, apex, exit_idx)
        return dict(cached)

    def _compute_corner_metrics(
        self,
        grid: dict[str, np.ndarray[Any, Any]],
        entry: int,
        apex: int,
        exit_idx: int,
    ) -> dict[str, Any]:
        n = len(grid["distance_pct"])
        padding = max(1, int(round(75.0 / max(1.0, self.track_length_m) * (n - 1))))
        pre_entry = max(0, entry - padding)
        post_exit = min(n - 1, exit_idx + padding)
        valid_coverage = np.asarray(grid.get("_coverage", np.ones(n, dtype=bool)), dtype=bool) & np.asarray(grid.get("_analysis_coverage", np.ones(n, dtype=bool)), dtype=bool)
        def channel(name: str) -> np.ndarray:
            values = np.asarray(grid.get(name, np.full(n, np.nan)), dtype=float).copy()
            values[~valid_coverage] = np.nan
            support = grid.get("_channel_support", {}).get(name)
            if support is not None:
                values[~np.asarray(support, dtype=bool)] = np.nan
            return values
        brake, throttle, steering = channel("Brake"), channel("Throttle"), channel("SteeringWheelAngle")
        yaw_rate, lat_accel, speed = channel("YawRate"), channel("LatAccel"), channel("speed_kph")
        abs_values = channel("BrakeABSactive") if "BrakeABSactive" in grid else None
        abs_active = abs_values > 0.5 if abs_values is not None else None

        braking = np.flatnonzero(brake[pre_entry : apex + 1] > 0.08)
        brake_start = pre_entry + int(braking[0]) if len(braking) else None
        brake_release_candidates = np.flatnonzero(brake[entry : min(n, apex + padding)] > 0.05)
        brake_release = entry + int(brake_release_candidates[-1]) if len(brake_release_candidates) else None
        throttle_first_candidates = np.flatnonzero(throttle[apex : post_exit + 1] > 0.20)
        throttle_first = apex + int(throttle_first_candidates[0]) if len(throttle_first_candidates) else None
        full_throttle_candidates = np.flatnonzero(throttle[apex : post_exit + 1] > 0.95)
        full_throttle = apex + int(full_throttle_candidates[0]) if len(full_throttle_candidates) else None

        corner_slice = slice(entry, exit_idx + 1)
        entry_slice = slice(entry, apex + 1)
        exit_slice = slice(apex, exit_idx + 1)
        time_axis = channel("time_s")
        time_steps = np.diff(time_axis, prepend=time_axis[0])
        coast_mask = (
            (throttle[entry_slice] < 0.05)
            & (brake[entry_slice] < 0.05)
            & (speed[entry_slice] > 50)
        )
        braking_mask = brake[entry_slice] > 0.08
        valid_abs = braking_mask & np.isfinite(abs_values[entry_slice]) if abs_values is not None else None
        abs_duty = float(np.mean(abs_active[entry_slice][valid_abs])) if valid_abs is not None and np.any(valid_abs) else None

        throttle_relifts = int(
            np.count_nonzero(
                (throttle[apex:exit_idx] > 0.50)
                & (throttle[apex + 1 : exit_idx + 1] < 0.25)
            )
        ) if exit_idx - apex > 1 else 0

        steering_segment = steering[corner_slice]
        corrections = 0
        if len(steering_segment) > 6:
            filtered_steering = smooth(steering_segment, min(11, len(steering_segment) | 1))
            steering_derivative = np.diff(filtered_steering)
            signs = np.sign(steering_derivative)
            extrema = (np.flatnonzero(signs[:-1] * signs[1:] < 0) + 1).tolist()
            significant_extrema: list[int] = []
            for index in extrema:
                if not significant_extrema or abs(
                    filtered_steering[index] - filtered_steering[significant_extrema[-1]]
                ) > 0.035:
                    significant_extrema.append(index)
            corrections = max(0, len(significant_extrema) - 1)

        steering_load = float(np.nanmean(np.abs(steering_segment))) if np.any(np.isfinite(steering_segment)) else None
        yaw_load = float(np.nanmean(np.abs(yaw_rate[corner_slice]))) if np.any(np.isfinite(yaw_rate[corner_slice])) else None
        efficiency_ratios = np.abs(yaw_rate[corner_slice]) / (np.abs(steering_segment) + 0.05)
        steering_efficiency = float(np.nanmean(efficiency_ratios)) if np.any(np.isfinite(efficiency_ratios)) else None

        def pct_at(index: Optional[int]) -> Optional[float]:
            return round(float(grid["distance_pct"][index]), 5) if index is not None else None

        return {
            "brake_start_pct": pct_at(brake_start),
            "brake_release_pct": pct_at(brake_release),
            "throttle_first_pct": pct_at(throttle_first),
            "full_throttle_pct": pct_at(full_throttle),
            "inputs_usable": bool(np.all(valid_coverage[corner_slice]) and all(np.all(np.isfinite(values[corner_slice])) for values in (brake, throttle, steering))),
            "measurement_usable": bool(np.all(valid_coverage[corner_slice]) and np.all(np.isfinite(speed[corner_slice])) and np.all(np.isfinite(time_axis[corner_slice]))),
            "distance_resolution_m": round_or_none(self.track_length_m / max(1, n - 1), 2),
            "peak_brake": round_or_none(float(np.nanmax(brake[entry_slice])), 3) if np.any(np.isfinite(brake[entry_slice])) else None,
            "abs_duty_pct": round_or_none(abs_duty * 100.0 if abs_duty is not None else None, 1),
            "coast_time_s": round_or_none(float(np.sum(time_steps[entry_slice][coast_mask])), 3) if np.all(np.isfinite(throttle[entry_slice])) and np.all(np.isfinite(brake[entry_slice])) and np.all(np.isfinite(time_steps[entry_slice])) else None,
            "min_speed_kph": round_or_none(float(np.nanmin(speed[corner_slice])), 1) if np.any(np.isfinite(speed[corner_slice])) else None,
            "exit_speed_kph": round_or_none(float(speed[exit_idx]), 1),
            "avg_abs_steer_deg": round_or_none(math.degrees(steering_load), 1) if steering_load is not None else None,
            "avg_abs_yaw_rate": round(yaw_load, 3) if yaw_load is not None else None,
            "peak_lat_g": round_or_none(float(np.nanmax(np.abs(lat_accel[corner_slice]))) / 9.80665, 2)
            if np.any(np.isfinite(lat_accel[corner_slice]))
            else None,
            "steering_efficiency": round(steering_efficiency, 3) if steering_efficiency is not None else None,
            "throttle_relifts": throttle_relifts if np.all(np.isfinite(throttle[exit_slice])) else None,
            "steering_corrections": corrections if np.all(np.isfinite(steering_segment)) else None,
        }

    def _corner_evidence(
        self,
        delta_s: Optional[float],
        target: dict[str, Any],
        reference: Optional[dict[str, Any]],
    ) -> tuple[list[str], str, str]:
        if reference is None:
            return ["비교 랩이 없어 절대 지표만 표시합니다."], "NO_REFERENCE", "동일 조건의 추가 유효 랩을 기록하십시오."

        length = self.track_length_m
        evidence: list[str] = []
        issue_type = "CONSISTENT"
        driver_action = "현재 입력 패턴을 유지하십시오."

        min_speed_delta = (target.get("min_speed_kph") or 0) - (reference.get("min_speed_kph") or 0)
        if abs(min_speed_delta) >= 1.0:
            evidence.append(f"최저속도 {min_speed_delta:+.1f} km/h")

        brake_distance = pct_distance(
            target.get("brake_start_pct"), reference.get("brake_start_pct"), length
        )
        full_throttle_distance = pct_distance(
            target.get("full_throttle_pct"), reference.get("full_throttle_pct"), length
        )
        coast_delta = (target.get("coast_time_s") or 0) - (reference.get("coast_time_s") or 0)
        abs_delta = target["abs_duty_pct"] - reference["abs_duty_pct"] if target.get("abs_duty_pct") is not None and reference.get("abs_duty_pct") is not None else None
        steering_delta = (target.get("avg_abs_steer_deg") or 0) - (reference.get("avg_abs_steer_deg") or 0)
        efficiency_target = target.get("steering_efficiency")
        efficiency_reference = reference.get("steering_efficiency")

        if brake_distance is not None and abs(brake_distance) >= 3:
            evidence.append(f"제동 시작 {brake_distance:+.0f} m")
        if full_throttle_distance is not None and abs(full_throttle_distance) >= 3:
            evidence.append(f"풀스로틀 복귀 {full_throttle_distance:+.0f} m")
        if abs(coast_delta) >= 0.03:
            evidence.append(f"코스팅 {coast_delta:+.2f} s")
        if abs_delta is not None and abs(abs_delta) >= 5:
            evidence.append(f"ABS 듀티 {abs_delta:+.1f}%p")
        if abs(steering_delta) >= 2:
            evidence.append(f"평균 조향량 {steering_delta:+.1f}°")

        if delta_s is None:
            return evidence or ["비교 델타를 계산할 수 없습니다."], "NO_DELTA", "추가 유효 랩을 기록하십시오."
        if delta_s <= -0.02:
            return evidence or [f"기준 랩 대비 {-delta_s:.3f}초 이득"], "GAIN", "이 구간의 입력 순서를 기준 패턴으로 저장하십시오."
        if delta_s < 0.02:
            return evidence or ["기준 랩과 차이가 작습니다."], "CONSISTENT", "변경보다 재현성 유지가 우선입니다."

        if full_throttle_distance is not None and full_throttle_distance > 5:
            issue_type = "EXIT"
            driver_action = f"조향을 먼저 풀고 풀스로틀 복귀를 약 {full_throttle_distance:.0f} m 앞당기는 것을 우선 시험하십시오."
        elif min_speed_delta < -2.0 and coast_delta > 0.02:
            issue_type = "MID_CORNER"
            driver_action = f"불필요한 코스팅을 줄여 최저속도를 약 {abs(min_speed_delta):.1f} km/h 회복하십시오."
        elif brake_distance is not None and brake_distance < -6 and min_speed_delta < -1:
            issue_type = "ENTRY"
            driver_action = f"제동 시작을 약 {abs(brake_distance):.0f} m 늦추고 릴리즈를 더 연속적으로 만드십시오."
        elif abs_delta is not None and abs_delta > 8 and min_speed_delta < 0:
            issue_type = "BRAKING"
            driver_action = "초기 피크 제동보다 ABS 개입 직전의 일정한 압력과 부드러운 릴리즈를 우선하십시오."
        elif (
            steering_delta > 3
            and efficiency_target is not None
            and efficiency_reference is not None
            and efficiency_target < efficiency_reference * 0.95
        ):
            issue_type = "BALANCE_SUSPECT"
            driver_action = "같은 속도에서 조향을 더 추가하지 말고 진입 속도와 브레이크 릴리즈를 먼저 재현해 셋업 문제인지 분리하십시오."
        else:
            issue_type = "DRIVER_VARIANCE"
            driver_action = "제동 종료·최저속도·스로틀 복귀 순서를 기준 랩과 동일하게 재현하십시오."

        evidence.insert(0, f"구간 손실 +{delta_s:.3f}초")
        return evidence, issue_type, driver_action


    def _repetition_analysis(
        self,
        definitions: list[dict[str, int]],
        session_mode: SessionMode = SessionMode.BASELINE_3LAP,
    ) -> dict[str, Any]:
        policy = session_policy(session_mode)
        required_laps = int(policy["setup_repeat_laps"])
        if len(self.valid_laps) < 2 or not definitions:
            return {
                "status": "INSUFFICIENT_LAPS",
                "valid_laps": len(self.valid_laps),
                "required_laps": required_laps,
                "repeated_setup_patterns": [],
                "candidate_setup_patterns": [],
            }

        fastest = min(self.valid_laps, key=lambda lap: lap.lap_time_s or math.inf)
        reference_grid = self._lap_grid(fastest)
        counts: dict[str, dict[str, Any]] = {}
        compared_laps = 0
        for lap in self.valid_laps:
            if lap.lap_number == fastest.lap_number:
                continue
            compared_laps += 1
            analyzed = self._corner_analysis(self._lap_grid(lap), reference_grid, definitions)
            for corner in analyzed:
                if corner.get("issue_type") != "BALANCE_SUSPECT" or (corner.get("delta_s") or 0) < 0.02:
                    continue
                entry = counts.setdefault(
                    corner["id"],
                    {
                        "corner": corner["label"],
                        "corner_id": corner["id"],
                        "apex_pct": corner["apex_pct"],
                        "occurrences": 0,
                        "laps": [],
                        "losses_s": [],
                        "evidence": [],
                    },
                )
                entry["occurrences"] += 1
                entry["laps"].append(lap.lap_number)
                entry["losses_s"].append(float(corner.get("delta_s") or 0.0))
                entry["evidence"].extend(corner.get("evidence", []))

        required_occurrences = max(2, math.ceil(max(1, compared_laps) * 0.60))
        candidates: list[dict[str, Any]] = []
        repeated: list[dict[str, Any]] = []
        for raw in counts.values():
            item = dict(raw)
            losses = item.pop("losses_s", [])
            item["occurrence_ratio"] = round(item["occurrences"] / max(1, compared_laps), 3)
            item["mean_loss_s"] = round_or_none(float(np.mean(losses)) if losses else None, 3)
            item["max_loss_s"] = round_or_none(max(losses) if losses else None, 3)
            item["evidence"] = list(dict.fromkeys(item["evidence"]))[:6]
            candidates.append(item)
            if session_mode != SessionMode.QUALIFYING and item["occurrences"] >= required_occurrences:
                repeated.append(dict(item))

        candidates.sort(
            key=lambda item: (item.get("occurrence_ratio") or 0.0, item.get("mean_loss_s") or 0.0),
            reverse=True,
        )
        repeated.sort(
            key=lambda item: (item.get("occurrence_ratio") or 0.0, item.get("mean_loss_s") or 0.0),
            reverse=True,
        )

        if session_mode == SessionMode.QUALIFYING:
            return {
                "status": "QUALIFYING_LIMITED",
                "valid_laps": len(self.valid_laps),
                "required_laps": 2,
                "reference_lap": fastest.lap_number,
                "compared_laps": compared_laps,
                "required_occurrences": 2,
                "repeated_setup_patterns": [],
                "candidate_setup_patterns": candidates,
                "note": (
                    "두 퀄리파잉 핫랩 사이의 밸런스 신호는 확정 근거가 아니라 A/B 실험 후보로만 사용합니다. "
                    "외부 기준 또는 별도 세션에서 같은 방향이 반복되면 신뢰도를 올립니다."
                ),
            }

        return {
            "status": "READY" if len(self.valid_laps) >= required_laps else "PARTIAL_REPEATABILITY",
            "valid_laps": len(self.valid_laps),
            "required_laps": required_laps,
            "reference_lap": fastest.lap_number,
            "compared_laps": compared_laps,
            "required_occurrences": required_occurrences,
            "repeated_setup_patterns": repeated,
            "candidate_setup_patterns": candidates,
        }

    def _coaching_priorities(
        self,
        corners: list[dict[str, Any]],
        comparison: Optional[dict[str, Any]],
        external_reference: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        priorities: list[dict[str, Any]] = []
        external_losses = (external_reference or {}).get("top_losses", [])
        if external_losses:
            for corner in external_losses:
                delta = corner.get("delta_s")
                if delta is None or delta < 0.02:
                    continue
                consensus = corner.get("reference_consensus", {}) or {}
                priorities.append(
                    {
                        "rank": len(priorities) + 1,
                        "corner": corner["label"],
                        "distance_m": corner["distance_m"],
                        "loss_s": delta,
                        "issue_type": corner["issue_type"],
                        "action": corner["driver_action"],
                        "evidence": [
                            f"Garage61 기준 {consensus.get('reference_count', 0)}개 중 손실 방향 일치 {round((consensus.get('loss_agreement_ratio') or 0) * 100)}%",
                            *corner.get("evidence", []),
                        ][:7],
                        "source": "GARAGE61_REFERENCE_CONSENSUS",
                    }
                )
                if len(priorities) >= 3:
                    return priorities

        for corner in corners:
            delta = corner.get("delta_s")
            if delta is None or delta < 0.02:
                continue
            priorities.append(
                {
                    "rank": len(priorities) + 1,
                    "corner": corner["label"],
                    "distance_m": corner["distance_m"],
                    "loss_s": delta,
                    "issue_type": corner["issue_type"],
                    "action": corner["driver_action"],
                    "evidence": corner["evidence"],
                    "source": "PERSONAL_LAP_COMPARISON",
                }
            )
            if len(priorities) >= 3:
                break

        if not priorities and comparison:
            priorities.append(
                {
                    "rank": 1,
                    "corner": "전체 랩",
                    "distance_m": None,
                    "loss_s": None,
                    "issue_type": "CONSISTENCY",
                    "action": "현재 베스트 랩의 입력 패턴을 연속 3랩 재현하여 셋업 판정 표본을 확보하십시오.",
                    "evidence": ["유의미한 단일 코너 손실이 검출되지 않았습니다."],
                    "source": "PERSONAL_LAP_COMPARISON",
                }
            )
        return priorities

    def _external_reference_analysis(
        self,
        target: Optional[LapSegment],
        requested_files: Iterable[str],
    ) -> dict[str, Any]:
        selected_names = list(
            dict.fromkeys(Path(str(name)).name for name in requested_files if str(name).strip())
        )[:EXTERNAL_REFERENCE_MAX_SELECTION]
        base: dict[str, Any] = {
            "status": "NOT_SELECTED" if not selected_names else "NO_VALID_REFERENCE",
            "directory": str(EXTERNAL_REFERENCE_DIR),
            "selected_files": selected_names,
            "selected_reference_count": 0,
            "decision_reference_count": 0,
            "unique_driver_count": 0,
            "decision_ready": False,
            "strong_consensus": False,
            "consensus_tier": "DISPLAY_ONLY",
            "setup_conclusion_allowed": False,
            "aggregation_method": "CONDITION_WEIGHTED_DISTANCE_NORMALIZED_ROBUST_MEDIAN",
            "minimum_references_for_setup": EXTERNAL_REFERENCE_MIN_DECISION,
            "strong_consensus_count": EXTERNAL_REFERENCE_STRONG_DECISION,
            "single_reference_policy": "DISPLAY_ONLY_NO_SETUP_CONCLUSION",
            "two_reference_policy": "PROVISIONAL_SETUP_ASSIST_CONFIDENCE_CAPPED",
            "references": [],
            "aggregate": {},
            "corners": [],
            "top_losses": [],
            "line_comparison": {
                "status": "NOT_AVAILABLE",
                "max_corners": RACING_LINE_COMPARE_MAX_CORNERS,
                "corners": [],
            },
            "visualizations": {"trace": []},
            "warnings": [],
            "errors": [],
        }
        if target is None or not selected_names:
            return base

        current_conditions = telemetry_reference_conditions(self.telemetry, target)
        loaded: list[ExternalReferenceLap] = []
        for name in selected_names:
            path = resolve_external_reference_file(name)
            if path is None:
                base["errors"].append(f"외부 기준 CSV를 찾을 수 없습니다: {name}")
                continue
            try:
                reference = external_reference_repository.load(path)
                if not external_reference_compatible(reference.metadata, self.telemetry.metadata):
                    base["errors"].append(f"차량·트랙·레이아웃 불일치: {name}")
                    continue
                loaded.append(reference)
            except Exception as exc:
                base["errors"].append(f"{name}: {exc}")

        if not loaded:
            return base

        target_time = float(target.lap_time_s) if target.valid and target.lap_time_s else None
        reference_rows: list[dict[str, Any]] = []
        grids: list[dict[str, np.ndarray[Any, Any]]] = []
        faster_indices: list[int] = []
        decision_eligible_indices: list[int] = []
        for index, reference in enumerate(loaded):
            lap_time = float(reference.metadata["lap_time_s"])
            gap = target_time - lap_time if target_time is not None else None
            faster = gap is not None and gap >= 0.03
            condition = reference_condition_similarity(reference.metadata, current_conditions)
            if faster:
                faster_indices.append(index)
                if (
                    float(condition.get("score") or 0.0) >= GARAGE61_CONDITION_SCORE_MIN
                    and bool(condition.get("setup_compatible", True))
                ):
                    decision_eligible_indices.append(index)
            grids.append(reference.grid(self.grid_points, self.track_length_m))
            reference_rows.append(
                {
                    **reference.metadata,
                    "lap_time": format_lap_time(lap_time),
                    "gap_from_target_s": round_or_none(gap, 4),
                    "faster_than_target": faster,
                    "sample_count": int(len(reference.distance_pct)),
                    "condition_similarity": condition,
                }
            )

        decision_ready = len(decision_eligible_indices) >= EXTERNAL_REFERENCE_MIN_DECISION
        display_eligible_indices = [
            idx for idx, row in enumerate(reference_rows)
            if float((row.get("condition_similarity") or {}).get("score") or 0.0) >= 0.45
            and bool((row.get("condition_similarity") or {}).get("setup_compatible", True))
        ]
        pool_indices = (
            decision_eligible_indices if decision_ready
            else display_eligible_indices if display_eligible_indices
            else list(range(len(loaded)))
        )
        # When a large library is selected, condition similarity is the first
        # gate. This keeps the median representative of the current session
        # instead of letting very different fuel/weather laps dominate it.
        pool_indices.sort(
            key=lambda idx: (
                -float((reference_rows[idx].get("condition_similarity") or {}).get("score") or 0.0),
                float(reference_rows[idx].get("lap_time_s") or math.inf),
            )
        )
        pool_grids = [grids[index] for index in pool_indices]
        pool_rows = [reference_rows[index] for index in pool_indices]

        # API-enriched references carry richer condition/quality metadata. Use
        # it to weight the consensus rather than treating a marginally matched
        # lap exactly the same as a near-identical-condition lap.
        raw_weights: list[float] = []
        for row in pool_rows:
            condition = row.get("condition_similarity") or {}
            condition_score = float(condition.get("score") or 0.5)
            coverage_score = float(condition.get("coverage") or 0.0)
            quality_score = float(row.get("reference_quality_score") or 1.0)
            recency_score = float(row.get("reference_recency_score") or 0.7)
            api_bonus = 1.04 if str(row.get("metadata_source") or "").upper() == "GARAGE61_API" else 1.0
            weight = (
                (0.30 + 0.70 * max(0.0, min(1.0, condition_score)))
                * (0.70 + 0.30 * max(0.0, min(1.0, coverage_score)))
                * (0.80 + 0.20 * max(0.0, min(1.0, quality_score)))
                * (0.90 + 0.10 * max(0.0, min(1.0, recency_score)))
                * api_bonus
            )
            raw_weights.append(max(0.05, weight))
        pool_weights = np.asarray(raw_weights or [1.0] * len(pool_rows), dtype=float)
        if len(pool_weights) and float(np.sum(pool_weights)) > 0:
            pool_weights = pool_weights / float(np.sum(pool_weights))
        for idx, row in enumerate(pool_rows):
            row["analysis_weight"] = round_or_none(float(pool_weights[idx]), 4) if idx < len(pool_weights) else None

        unique_drivers = len(
            {
                _context_token(item.get("driver_name"))
                for item in pool_rows
                if item.get("driver_name")
            }
        )
        strong_consensus = bool(
            decision_ready
            and len(pool_rows) >= EXTERNAL_REFERENCE_STRONG_DECISION
            and unique_drivers >= 2
        )
        consensus_tier = (
            "STRONG" if strong_consensus else
            "PROVISIONAL" if decision_ready else
            "DISPLAY_ONLY"
        )

        aggregate_grid: dict[str, np.ndarray[Any, Any]] = {
            "distance_pct": np.linspace(0.0, 1.0, self.grid_points)
        }
        aggregate_keys = [
            "time_s", "Speed", "speed_kph", "Throttle", "Brake", "SteeringWheelAngle",
            "YawRate", "LatAccel", "LongAccel", "Gear", "BrakeABSactive", "Lat", "Lon",
        ]
        distribution_grids: dict[str, dict[str, np.ndarray[Any, Any]]] = {}
        for key in aggregate_keys:
            pairs = [(grid[key], pool_weights[idx]) for idx, grid in enumerate(pool_grids) if key in grid]
            if pairs:
                stack = np.vstack([pair[0] for pair in pairs])
                key_weights = np.asarray([pair[1] for pair in pairs], dtype=float)
                aggregate_grid[key] = _weighted_quantile_stack(stack, key_weights, 0.50)
                distribution_grids[key] = {
                    "p25": _weighted_quantile_stack(stack, key_weights, 0.25),
                    "p75": _weighted_quantile_stack(stack, key_weights, 0.75),
                    "min": np.where(np.any(np.isfinite(stack), axis=0), np.min(np.where(np.isfinite(stack), stack, np.inf), axis=0), np.nan),
                    "max": np.where(np.any(np.isfinite(stack), axis=0), np.max(np.where(np.isfinite(stack), stack, -np.inf), axis=0), np.nan),
                }
        aggregate_grid["_coverage"] = np.isfinite(aggregate_grid.get("time_s", np.full(self.grid_points, np.nan))) & np.isfinite(aggregate_grid.get("speed_kph", np.full(self.grid_points, np.nan)))
        aggregate_grid["_channel_support"] = {key: np.isfinite(value) for key, value in aggregate_grid.items() if isinstance(value, np.ndarray) and not key.startswith("_") and key != "distance_pct"}

        target_grid = self._lap_grid(target) if target.valid else self._coaching_grid(target)
        straight_performance = self._straight_performance_diagnostics(
            target_grid=target_grid,
            reference_grid=aggregate_grid,
            distribution_grids=distribution_grids,
            reference_count=len(pool_rows),
            unique_driver_count=unique_drivers,
            condition_similarity=float(np.median([
                float((item.get("condition_similarity") or {}).get("score") or 0.0)
                for item in pool_rows
            ])) if pool_rows else 0.5,
            strong_consensus=strong_consensus,
        )
        definitions = self._detect_corners(
            aggregate_grid if "speed_kph" in aggregate_grid else target_grid
        )
        aggregate_corners = self._corner_analysis(target_grid, aggregate_grid, definitions)
        per_reference_corners = [
            {corner["id"]: corner for corner in self._corner_analysis(target_grid, grid, definitions)}
            for grid in pool_grids
        ]

        for corner in aggregate_corners:
            pairs = [
                (mapping.get(corner["id"]), pool_rows[index])
                for index, mapping in enumerate(per_reference_corners)
            ]
            pairs = [(item, row) for item, row in pairs if item is not None]
            measured_pairs = [(item, row) for item, row in pairs if item.get("delta_s") is not None and item.get("measurement_usable", True)]
            issue_counts: dict[str, int] = {}
            for item, _ in measured_pairs:
                issue = str(item.get("issue_type") or "")
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
            dominant_issue = max(issue_counts, key=issue_counts.get) if issue_counts else corner.get("issue_type")
            # Delta, evidence and action describe the same weighted time curve.
            # Per-lap losses below are a separate uncertainty distribution.
            losses = [float(item["delta_s"]) for item, _ in measured_pairs]
            loss_weights = np.asarray([float(row.get("analysis_weight") or 0.0) for _, row in measured_pairs])
            loss_agreement = (
                sum(1 for value in losses if value >= 0.02) / len(losses) if losses else 0.0
            )
            issue_agreement = (
                issue_counts.get(str(dominant_issue), 0) / len(measured_pairs) if measured_pairs else 0.0
            )
            condition_scores = [
                float((row.get("condition_similarity") or {}).get("score") or 0.0)
                for _, row in measured_pairs
            ]
            corner_drivers = {
                _context_token(row.get("driver_name"))
                for _, row in measured_pairs
                if row.get("driver_name")
            }
            corner["reference_consensus"] = {
                "reference_count": len(measured_pairs),
                "selected_reference_count": len(pairs),
                "unique_driver_count": len(corner_drivers),
                "loss_agreement_ratio": round(loss_agreement, 3),
                "dominant_issue_type": dominant_issue,
                "issue_agreement_ratio": round(issue_agreement, 3),
                "aggregate_curve_loss_s": corner.get("delta_s"),
                "median_loss_s": round_or_none(float(_weighted_quantile_stack(np.asarray(losses)[:, None], loss_weights, .5)[0]), 3) if losses else None,
                "loss_p25_s": round_or_none(float(_weighted_quantile_stack(np.asarray(losses)[:, None], loss_weights, .25)[0]), 3) if losses else None,
                "loss_p75_s": round_or_none(float(_weighted_quantile_stack(np.asarray(losses)[:, None], loss_weights, .75)[0]), 3) if losses else None,
                "loss_range_s": [
                    round_or_none(min(losses), 3) if losses else None,
                    round_or_none(max(losses), 3) if losses else None,
                ],
                "condition_similarity_median": round_or_none(float(np.median(condition_scores)), 3)
                if condition_scores else None,
                "consensus_tier": consensus_tier,
            }

        aggregate_corners.sort(key=lambda item: item.get("delta_s") or -999.0, reverse=True)
        top_losses = [item for item in aggregate_corners if (item.get("delta_s") or 0.0) >= 0.02][:8]
        reference_times = [float(item["lap_time_s"]) for item in pool_rows]
        median_reference_time = float(_weighted_quantile_stack(np.asarray(reference_times)[:, None], pool_weights, .50)[0]) if reference_times else None
        gap_to_reference = target_time - median_reference_time if target_time is not None and median_reference_time is not None else None
        condition_scores = [
            float((item.get("condition_similarity") or {}).get("score") or 0.0)
            for item in pool_rows
        ]
        metadata_coverages = [
            float((item.get("condition_similarity") or {}).get("coverage") or 0.0)
            for item in pool_rows
        ]

        trace: list[dict[str, Any]] = []
        if "time_s" in aggregate_grid:
            delta = target_grid["time_s"] - aggregate_grid["time_s"]
            indices = visualization_indices(target_grid, aggregate_grid, VISUAL_GRID_POINTS)
            speed_dist = distribution_grids.get("speed_kph") or {}
            for index in indices:
                trace.append(
                    {
                        "distance_pct": round(float(target_grid["distance_pct"][index]), 5),
                        "target_speed_kph": round_or_none(float(target_grid["speed_kph"][index]), 2) if "speed_kph" in target_grid else None,
                        "reference_speed_kph": round_or_none(float(aggregate_grid["speed_kph"][index]), 2) if "speed_kph" in aggregate_grid else None,
                        "reference_speed_p25_kph": round_or_none(float(speed_dist["p25"][index]), 2) if "p25" in speed_dist else None,
                        "reference_speed_p75_kph": round_or_none(float(speed_dist["p75"][index]), 2) if "p75" in speed_dist else None,
                        "delta_s": round_or_none(float(delta[index]), 4) if target.valid else None,
                        **comparison_input_channels(target_grid, aggregate_grid, index),
                    }
                )

        def _range(field: str) -> list[Optional[float]]:
            values: list[float] = []
            for item in pool_rows:
                try:
                    value = float(item.get(field))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    values.append(value)
            return [round_or_none(min(values), 2), round_or_none(max(values), 2)] if values else [None, None]

        representative_index = int(np.argmin(np.abs(np.asarray(reference_times) - median_reference_time))) if reference_times else 0
        representative_grid = pool_grids[representative_index] if pool_rows else aggregate_grid
        line_corners = self._corner_analysis(target_grid, representative_grid, definitions)
        consensus_by_id = {corner["id"]: corner.get("reference_consensus", {}) for corner in aggregate_corners}
        for corner in line_corners:
            corner["reference_consensus"] = consensus_by_id.get(corner["id"], {})
        line_comparison = self._external_racing_line_comparison(
            target_grid=target_grid,
            # Independently median Lat/Lon may form a line nobody actually drove.
            reference_grid=representative_grid,
            corners=line_corners,
            reference_count=len(pool_rows),
            unique_driver_count=unique_drivers,
            consensus_tier=consensus_tier,
        )
        representative_file = loaded[pool_indices[representative_index]].path.name if pool_rows else None
        line_comparison["reference_type"] = "GARAGE61_REPRESENTATIVE_ACTUAL_LAP"
        line_comparison["reference_file"] = representative_file
        line_comparison["note"] = "라인은 종합 페달·시간 곡선이 아니라 조건을 통과한 대표 실주행 랩과 비교합니다."
        self._external_line_grid = representative_grid
        self._external_line_source = {
            "type": "GARAGE61_REPRESENTATIVE_ACTUAL_LAP",
            "reference_file": representative_file,
            "driver_name": pool_rows[representative_index].get("driver_name") if pool_rows else None,
            "lap_time": format_lap_time(reference_times[representative_index]) if reference_times else None,
            "chart_basis": "AGGREGATE" if len(pool_rows) > 1 else "ACTUAL_LAP",
        }
        self._external_detail_grid = aggregate_grid
        self._external_detail_distributions = distribution_grids
        self._external_detail_support = {key: np.sum(np.vstack([np.isfinite(grid[key]) for grid in pool_grids if key in grid]), axis=0) for key in aggregate_keys if any(key in grid for grid in pool_grids)}
        self._external_reference_label = "Garage61 실주행 기준" if len(pool_rows) == 1 else f"Garage61 조건 가중 종합 기준 · {len(pool_rows)}랩"

        warnings: list[str] = []
        if len(loaded) == 1:
            warnings.append(
                "외부 기준 CSV가 1개뿐이므로 그래프·드라이빙 코칭에만 사용하며 셋업 결론에는 사용하지 않습니다."
            )
        if len(decision_eligible_indices) < EXTERNAL_REFERENCE_MIN_DECISION:
            warnings.append(
                f"현재 대상보다 빠르면서 조건 유사도 {GARAGE61_CONDITION_SCORE_MIN:.2f} 이상인 기준이 {len(decision_eligible_indices)}개입니다. 셋업 후보에는 최소 {EXTERNAL_REFERENCE_MIN_DECISION}개가 필요합니다."
            )
        elif not strong_consensus:
            warnings.append(
                f"빠른 기준 {len(pool_rows)}개로 임시 컨센서스를 사용합니다. {EXTERNAL_REFERENCE_STRONG_DECISION}개 이상과 서로 다른 드라이버 2명 이상이면 신뢰도가 더 높아집니다."
            )
        if decision_ready and unique_drivers < 2:
            warnings.append(
                "셋업 판단에 사용된 CSV가 여러 개지만 동일 드라이버 표본입니다. 서로 다른 드라이버 기준보다 신뢰도를 낮게 적용합니다."
            )
        if metadata_coverages and float(np.median(metadata_coverages)) < 0.35:
            warnings.append(
                "Garage61 메타데이터가 부족해 조건 가중치가 제한됩니다. API reference library를 새로 수집하면 기준 선택 품질이 향상됩니다."
            )

        base.update(
            {
                "status": "READY",
                "selected_reference_count": len(loaded),
                "decision_reference_count": len(decision_eligible_indices),
                "faster_reference_count": len(faster_indices),
                "unique_driver_count": unique_drivers,
                "decision_ready": decision_ready,
                "strong_consensus": strong_consensus,
                "consensus_tier": consensus_tier,
                "setup_conclusion_allowed": decision_ready,
                "current_conditions": current_conditions,
                "references": reference_rows,
                "aggregate": {
                    "target_lap": target.lap_number,
                    "pace_eligible": bool(target.valid),
                    "coaching_scope": "VALID_LAP" if target.valid else "CLEAN_SEGMENTS_ONLY",
                    "target_time_s": round_or_none(target_time, 4),
                    "target_time": format_lap_time(target_time),
                    "reference_median_time_s": round_or_none(median_reference_time, 4),
                    "reference_median_time": format_lap_time(median_reference_time),
                    "gap_to_reference_median_s": round_or_none(gap_to_reference, 4),
                    "fastest_reference_time_s": round_or_none(min(reference_times) if reference_times else None, 4),
                    "slowest_reference_time_s": round_or_none(max(reference_times) if reference_times else None, 4),
                    "reference_time_spread_s": round_or_none(
                        (max(reference_times) - min(reference_times)) if len(reference_times) > 1 else 0.0,
                        4,
                    ),
                    "time_aggregation": "CONDITION_WEIGHTED_MEDIAN",
                    "representative_reference_file": representative_file,
                    "reference_label": self._external_reference_label,
                    "reference_pool": "CONDITION_QUALIFIED_FASTER" if decision_ready else "SELECTED_DISPLAY_POOL",
                    "condition_similarity_median": round_or_none(float(np.median(condition_scores)), 3)
                    if condition_scores else None,
                    "condition_similarity_range": [
                        round_or_none(min(condition_scores), 3) if condition_scores else None,
                        round_or_none(max(condition_scores), 3) if condition_scores else None,
                    ],
                    "track_temp_range_c": _range("track_temp_c"),
                    "air_temp_range_c": _range("air_temp_c"),
                    "fuel_range_l": _range("fuel_l"),
                    "metadata_enriched_count": sum(1 for item in pool_rows if item.get("metadata_enriched")),
                    "api_enriched_count": sum(1 for item in pool_rows if str(item.get("metadata_source") or "").upper() == "GARAGE61_API"),
                    "reference_analysis_weights": [round_or_none(float(value), 4) for value in pool_weights.tolist()],
                    "consensus_tier": consensus_tier,
                },
                "corners": aggregate_corners,
                "top_losses": top_losses,
                "line_comparison": line_comparison,
                "performance_diagnostics": {
                    "straight_line": straight_performance,
                },
                "visualizations": {"trace": trace,
                                   "track_path": gps_path(target_grid),
                                   "reference_track_path": gps_path(representative_grid),
                                   "reference_line_source": self._external_line_source},
                "warnings": warnings,
            }
        )
        return base

    def _external_racing_line_comparison(
        self,
        target_grid: dict[str, np.ndarray[Any, Any]],
        reference_grid: dict[str, np.ndarray[Any, Any]],
        corners: list[dict[str, Any]],
        reference_count: int,
        unique_driver_count: int,
        consensus_tier: str,
    ) -> dict[str, Any]:
        """Return at most two corners where a line difference and speed loss coexist.

        The purpose is coaching, not exhaustive GPS inspection.  A corner is
        eligible only when the target is measurably slower *and* the target
        path departs from the condition-weighted Garage61 median line.  The
        output uses local metre coordinates so the browser can draw a compact
        SVG without exposing raw GPS traces to any remote service.
        """
        base: dict[str, Any] = {
            "status": "NO_QUALIFYING_CORNERS",
            "max_corners": RACING_LINE_COMPARE_MAX_CORNERS,
            "reference_type": "GARAGE61_CONDITION_WEIGHTED_MEDIAN_LINE",
            "reference_count": int(reference_count),
            "unique_driver_count": int(unique_driver_count),
            "consensus_tier": consensus_tier,
            "thresholds": {
                "minimum_corner_loss_s": round_or_none(RACING_LINE_COMPARE_MIN_LOSS_S, 3),
                "minimum_speed_deficit_kph": round_or_none(RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH, 1),
                "minimum_p75_line_gap_m": round_or_none(RACING_LINE_COMPARE_MIN_P75_GAP_M, 2),
                "minimum_peak_line_gap_m": round_or_none(RACING_LINE_COMPARE_MIN_PEAK_GAP_M, 2),
            },
            "corners": [],
            "note": (
                "모든 코너를 보여주지 않고, 속도 손실과 라인 차이가 동시에 확인된 개선 가치 상위 2개만 표시합니다."
            ),
        }
        required = ("Lat", "Lon", "speed_kph", "distance_pct")
        if not all(key in target_grid and key in reference_grid for key in required):
            base["status"] = "MISSING_GPS_OR_SPEED"
            base["note"] = "본인 또는 Garage61 기준에 Lat/Lon/Speed 채널이 없어 라인 비교를 만들 수 없습니다."
            return base

        target_lat = np.asarray(target_grid["Lat"], dtype=float)
        target_lon = np.asarray(target_grid["Lon"], dtype=float)
        ref_lat = np.asarray(reference_grid["Lat"], dtype=float)
        ref_lon = np.asarray(reference_grid["Lon"], dtype=float)
        distance = np.asarray(target_grid["distance_pct"], dtype=float)
        target_speed = np.asarray(target_grid["speed_kph"], dtype=float)
        ref_speed = np.asarray(reference_grid["speed_kph"], dtype=float)
        n = len(distance)
        if n < 100:
            base["status"] = "INSUFFICIENT_POINTS"
            return base

        valid = (
            np.isfinite(target_lat) & np.isfinite(target_lon)
            & np.isfinite(ref_lat) & np.isfinite(ref_lon)
            & (np.abs(target_lat) > 1e-7) & (np.abs(target_lon) > 1e-7)
            & (np.abs(ref_lat) > 1e-7) & (np.abs(ref_lon) > 1e-7)
        )
        if np.count_nonzero(valid) < int(n * 0.60):
            base["status"] = "INSUFFICIENT_GPS_COVERAGE"
            base["note"] = "GPS 유효 구간이 부족해 라인 차이를 안정적으로 계산하지 않았습니다."
            return base

        lat0 = float(np.nanmedian(ref_lat[valid]))
        lon0 = float(np.nanmedian(ref_lon[valid]))
        earth_r = 6371000.0
        cos_lat = math.cos(math.radians(lat0))

        def to_xy(lat: np.ndarray[Any, Any], lon: np.ndarray[Any, Any]) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
            x = earth_r * cos_lat * np.radians(lon - lon0)
            y = earth_r * np.radians(lat - lat0)
            return x.astype(float), y.astype(float)

        tx, ty = to_xy(target_lat, target_lon)
        rx, ry = to_xy(ref_lat, ref_lon)

        # Remove a possible constant coordinate-system translation.  This does
        # not remove actual racing-line shape differences within a corner.
        offset_x = float(np.nanmedian((tx - rx)[valid]))
        offset_y = float(np.nanmedian((ty - ry)[valid]))
        tx = tx - offset_x
        ty = ty - offset_y

        # Smooth only for the diagram/line-gap calculation. Telemetry metrics
        # elsewhere continue to use the original unsmoothed traces.
        window = max(5, min(17, (int(n * 0.004) | 1)))
        txs, tys = smooth(tx, window), smooth(ty, window)
        rxs, rys = smooth(rx, window), smooth(ry, window)
        tangent_x = np.gradient(rxs)
        tangent_y = np.gradient(rys)
        tangent_norm = np.hypot(tangent_x, tangent_y)
        tangent_norm = np.where(tangent_norm > 1e-6, tangent_norm, 1.0)
        tangent_x = tangent_x / tangent_norm
        tangent_y = tangent_y / tangent_norm
        dx = txs - rxs
        dy = tys - rys
        lateral = dx * tangent_y - dy * tangent_x
        abs_lateral = np.abs(lateral)

        def idx_for_pct(value: Any) -> Optional[int]:
            try:
                pct = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(pct):
                return None
            return int(np.clip(np.searchsorted(distance, pct), 0, n - 1))

        def marker_at(pct: Any, x: np.ndarray[Any, Any], y: np.ndarray[Any, Any], speed: np.ndarray[Any, Any]) -> Optional[dict[str, Any]]:
            index = idx_for_pct(pct)
            if index is None or not (math.isfinite(float(x[index])) and math.isfinite(float(y[index]))):
                return None
            return {
                "x_m": round_or_none(float(x[index]), 3),
                "y_m": round_or_none(float(y[index]), 3),
                "speed_kph": round_or_none(float(speed[index]), 1),
                "distance_pct": round_or_none(float(distance[index]), 5),
            }

        candidates: list[dict[str, Any]] = []
        for corner in corners:
            if not corner.get("measurement_usable", True):
                continue
            entry = idx_for_pct(corner.get("entry_pct"))
            apex = idx_for_pct(corner.get("apex_pct"))
            exit_idx = idx_for_pct(corner.get("exit_pct"))
            if entry is None or apex is None or exit_idx is None or exit_idx <= entry + 5:
                continue

            margin_points = max(3, int(round((45.0 / max(500.0, float(self.track_length_m))) * n)))
            start = max(0, entry - margin_points)
            stop = min(n - 1, exit_idx + margin_points)
            sl = slice(start, stop + 1)
            local_valid = valid[sl] & np.isfinite(abs_lateral[sl]) & np.isfinite(target_speed[sl]) & np.isfinite(ref_speed[sl])
            if np.count_nonzero(local_valid) < 12:
                continue

            gaps = abs_lateral[sl][local_valid]
            speed_deficits = (ref_speed[sl] - target_speed[sl])[local_valid]
            median_speed_deficit = float(np.nanmedian(speed_deficits))
            p75_speed_deficit = float(np.nanpercentile(speed_deficits, 75))
            p75_gap = float(np.nanpercentile(gaps, 75))
            peak_gap = float(np.nanpercentile(gaps, 95))
            mean_gap = float(np.nanmean(gaps))
            delta_s = float(corner.get("delta_s") or 0.0)
            target_metrics = corner.get("target") or {}
            ref_metrics = corner.get("reference") or {}
            min_speed_deficit = float((ref_metrics.get("min_speed_kph") or 0.0) - (target_metrics.get("min_speed_kph") or 0.0))
            exit_speed_deficit = float((ref_metrics.get("exit_speed_kph") or 0.0) - (target_metrics.get("exit_speed_kph") or 0.0))

            speed_gate = (
                p75_speed_deficit >= RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH
                or min_speed_deficit >= RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH
            )
            line_gate = (
                p75_gap >= RACING_LINE_COMPARE_MIN_P75_GAP_M
                or peak_gap >= RACING_LINE_COMPARE_MIN_PEAK_GAP_M
            )
            if delta_s < RACING_LINE_COMPARE_MIN_LOSS_S or not speed_gate or not line_gate:
                continue

            consensus = corner.get("reference_consensus") or {}
            loss_agreement = float(consensus.get("loss_agreement_ratio") or 0.0)
            # With several external references, require at least weak agreement
            # that this really is a slower corner. A single reference remains
            # displayable but is clearly labelled by the consensus tier.
            if reference_count >= 2 and loss_agreement < 0.40:
                continue

            # Determine which third of the corner contains the largest useful
            # line deviation; this becomes a single coaching phrase rather than
            # dozens of point labels.
            edges = [entry, entry + max(1, (exit_idx - entry) // 3), entry + max(2, 2 * (exit_idx - entry) // 3), exit_idx + 1]
            phase_names = ["진입", "에이펙스", "탈출"]
            phase_scores: list[float] = []
            for a, b in zip(edges[:-1], edges[1:]):
                if b <= a:
                    phase_scores.append(0.0)
                    continue
                phase_scores.append(float(np.nanmean(abs_lateral[a:b])))
            phase = phase_names[int(np.argmax(phase_scores))] if phase_scores else "코너"

            brake_delta_m = pct_distance(target_metrics.get("brake_start_pct"), ref_metrics.get("brake_start_pct"), self.track_length_m)
            throttle_delta_m = pct_distance(target_metrics.get("full_throttle_pct"), ref_metrics.get("full_throttle_pct"), self.track_length_m)

            local_indices = np.arange(start, stop + 1)
            eligible_indices = local_indices[np.isfinite(abs_lateral[sl])]
            if len(eligible_indices) == 0:
                continue
            max_gap_idx = int(eligible_indices[int(np.nanargmax(abs_lateral[eligible_indices]))])

            sample_indices = downsample_indices(stop - start + 1, RACING_LINE_COMPARE_POINTS) + start
            target_points: list[dict[str, Any]] = []
            reference_points: list[dict[str, Any]] = []
            for idx in sample_indices:
                if not valid[idx]:
                    continue
                target_points.append({
                    "x_m": round_or_none(float(txs[idx]), 3),
                    "y_m": round_or_none(float(tys[idx]), 3),
                    "speed_kph": round_or_none(float(target_speed[idx]), 1),
                    "distance_pct": round_or_none(float(distance[idx]), 5),
                })
                reference_points.append({
                    "x_m": round_or_none(float(rxs[idx]), 3),
                    "y_m": round_or_none(float(rys[idx]), 3),
                    "speed_kph": round_or_none(float(ref_speed[idx]), 1),
                    "distance_pct": round_or_none(float(distance[idx]), 5),
                })

            if len(target_points) < 12 or len(reference_points) < 12:
                continue

            score = (
                max(0.0, delta_s) * 6.0
                + max(0.0, p75_speed_deficit) * 0.08
                + min(3.0, max(p75_gap, peak_gap * 0.6)) * 0.24
                + min(1.0, loss_agreement) * 0.45
            )
            candidates.append({
                "id": corner.get("id"),
                "label": corner.get("label"),
                "distance_m": corner.get("distance_m"),
                "speed_class": corner.get("speed_class"),
                "issue_type": corner.get("issue_type"),
                "delta_s": round_or_none(delta_s, 3),
                "priority_score": round_or_none(score, 3),
                "focus_phase": phase,
                "metrics": {
                    "mean_line_gap_m": round_or_none(mean_gap, 2),
                    "p75_line_gap_m": round_or_none(p75_gap, 2),
                    "peak_line_gap_m": round_or_none(peak_gap, 2),
                    "median_speed_deficit_kph": round_or_none(median_speed_deficit, 1),
                    "p75_speed_deficit_kph": round_or_none(p75_speed_deficit, 1),
                    "min_speed_deficit_kph": round_or_none(min_speed_deficit, 1),
                    "exit_speed_deficit_kph": round_or_none(exit_speed_deficit, 1),
                    "brake_start_delta_m": round_or_none(brake_delta_m, 1) if brake_delta_m is not None else None,
                    "full_throttle_delta_m": round_or_none(throttle_delta_m, 1) if throttle_delta_m is not None else None,
                    "loss_agreement_ratio": round_or_none(loss_agreement, 3),
                },
                "coaching_note": (
                    f"{phase}에서 Garage61 기준 라인과 차이가 크고 속도 손실이 함께 발생합니다. "
                    "한 번에 전체 라인을 바꾸지 말고 그림의 가장 큰 간격 구간부터 기준 라인에 가깝게 A/B 주행하십시오."
                ),
                "target_points": target_points,
                "reference_points": reference_points,
                "max_gap_connector": {
                    "target": {
                        "x_m": round_or_none(float(txs[max_gap_idx]), 3),
                        "y_m": round_or_none(float(tys[max_gap_idx]), 3),
                    },
                    "reference": {
                        "x_m": round_or_none(float(rxs[max_gap_idx]), 3),
                        "y_m": round_or_none(float(rys[max_gap_idx]), 3),
                    },
                    "gap_m": round_or_none(float(abs_lateral[max_gap_idx]), 2),
                    "distance_pct": round_or_none(float(distance[max_gap_idx]), 5),
                },
                "markers": {
                    "target": {
                        "brake": marker_at(target_metrics.get("brake_start_pct"), txs, tys, target_speed),
                        "apex": marker_at(corner.get("apex_pct"), txs, tys, target_speed),
                        "throttle": marker_at(target_metrics.get("full_throttle_pct"), txs, tys, target_speed),
                    },
                    "reference": {
                        "brake": marker_at(ref_metrics.get("brake_start_pct"), rxs, rys, ref_speed),
                        "apex": marker_at(corner.get("apex_pct"), rxs, rys, ref_speed),
                        "throttle": marker_at(ref_metrics.get("full_throttle_pct"), rxs, rys, ref_speed),
                    },
                },
            })

        candidates.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
        selected = candidates[:RACING_LINE_COMPARE_MAX_CORNERS]
        base["corners"] = selected
        base["eligible_corner_count"] = len(candidates)
        base["status"] = "READY" if selected else "NO_QUALIFYING_CORNERS"
        if selected:
            base["note"] = (
                f"개선 가치가 큰 {len(selected)}개 코너만 표시합니다. 라인 차이만 있는 코너나 속도 손실만 있는 코너는 제외합니다."
            )
        return base


    def _straight_performance_diagnostics(
        self,
        target_grid: dict[str, np.ndarray[Any, Any]],
        reference_grid: dict[str, np.ndarray[Any, Any]],
        distribution_grids: dict[str, dict[str, np.ndarray[Any, Any]]],
        reference_count: int,
        unique_driver_count: int,
        condition_similarity: float,
        strong_consensus: bool,
    ) -> dict[str, Any]:
        """Separate corner-exit loss from genuine late-straight speed/drag loss.

        A top-speed deficit is only promoted to an aero/setup signal when the
        target and reference are both essentially full throttle and straight,
        the speed gap grows *after* the corner exit, and the same behaviour is
        repeated on multiple long straights.  Importantly, lack of enough
        measurable straights is kept distinct from a measured "no bottleneck"
        result so the UI never interprets missing evidence as proof of absence.
        """
        required = ("speed_kph", "Throttle", "Brake", "SteeringWheelAngle")
        minimum_straights_for_repetition = 2
        recommended_straights_for_strong_diagnosis = 3
        if not all(key in target_grid and key in reference_grid for key in required):
            return {
                "status": "MISSING_CHANNELS",
                "diagnosis": "INSUFFICIENT_STRAIGHT_DATA",
                "data_sufficient": False,
                "data_quality": "INSUFFICIENT",
                "required_straights": minimum_straights_for_repetition,
                "recommended_straights": recommended_straights_for_strong_diagnosis,
                "eligible_straights": 0,
                "aero_drag_suspect": False,
                "persistent_top_speed_opportunity": False,
                "segments": [],
            }

        distance = target_grid["distance_pct"]
        target_speed = target_grid["speed_kph"].astype(float)
        ref_speed = reference_grid["speed_kph"].astype(float)
        target_throttle = target_grid["Throttle"].astype(float)
        ref_throttle = reference_grid["Throttle"].astype(float)
        target_brake = target_grid["Brake"].astype(float)
        ref_brake = reference_grid["Brake"].astype(float)
        target_steer = np.abs(target_grid["SteeringWheelAngle"].astype(float))
        ref_steer = np.abs(reference_grid["SteeringWheelAngle"].astype(float))

        # iRacing steering is radians. Five degrees is tight enough to exclude
        # most loaded corners while keeping small straight-line corrections.
        steer_limit = math.radians(5.0)
        straight_mask = (
            (target_throttle >= 0.94)
            & (ref_throttle >= 0.94)
            & (target_brake <= 0.03)
            & (ref_brake <= 0.03)
            & (target_steer <= steer_limit)
            & (ref_steer <= steer_limit)
            & (ref_speed >= 120.0)
        )
        point_distance_m = self.track_length_m / max(1, len(distance) - 1)
        min_points = max(4, int(round(150.0 / max(1e-6, point_distance_m))))
        groups = contiguous_groups(np.flatnonzero(straight_mask), max_gap=2, min_len=min_points)

        segments: list[dict[str, Any]] = []
        for number, (start_idx, end_idx) in enumerate(groups, start=1):
            length_points = end_idx - start_idx + 1
            distance_m = length_points * point_distance_m
            if distance_m < 150.0:
                continue

            def at(frac: float) -> int:
                return min(end_idx, max(start_idx, start_idx + int(round((length_points - 1) * frac))))

            i15, i35, i70, i90 = at(0.15), at(0.35), at(0.70), at(0.90)
            entry_gap = float(ref_speed[i15] - target_speed[i15])
            mid_gap = float(ref_speed[i35] - target_speed[i35])
            late_slice = slice(i70, max(i70 + 1, i90 + 1))
            late_gap = float(np.nanmedian(ref_speed[late_slice] - target_speed[late_slice]))
            end_gap = float(ref_speed[i90] - target_speed[i90])
            gap_growth = late_gap - entry_gap
            target_gain = float(target_speed[i90] - target_speed[i15])
            reference_gain = float(ref_speed[i90] - ref_speed[i15])
            acceleration_gain_deficit = reference_gain - target_gain
            target_max = float(np.nanmax(target_speed[start_idx : end_idx + 1]))
            reference_max = float(np.nanmax(ref_speed[start_idx : end_idx + 1]))
            max_speed_deficit = reference_max - target_max

            # Distribution support: if the target remains below the reference
            # p25 late in the straight, the signal is not being driven by one
            # unusually fast reference lap.
            speed_dist = distribution_grids.get("speed_kph") or {}
            below_reference_p25 = None
            if "p25" in speed_dist:
                ref_p25 = speed_dist["p25"]
                below_reference_p25 = float(
                    np.mean(target_speed[late_slice] < (ref_p25[late_slice] - 0.5))
                )

            classification = "MATCHED"
            if distance_m >= 220.0 and late_gap >= 2.5 and gap_growth >= 1.8 and acceleration_gain_deficit >= 1.5:
                classification = "DRAG_LIMITED" if entry_gap <= 2.5 else "MIXED_EXIT_AND_DRAG"
            elif entry_gap >= 3.0 and gap_growth < 1.8:
                classification = "EXIT_LIMITED"
            elif late_gap >= 2.0 and gap_growth >= 1.2:
                classification = "POSSIBLE_DRAG"

            segments.append(
                {
                    "id": f"S{number}",
                    "start_pct": round(float(distance[start_idx]), 5),
                    "end_pct": round(float(distance[end_idx]), 5),
                    "distance_m": round(distance_m, 1),
                    "classification": classification,
                    "entry_speed_deficit_kph": round(entry_gap, 2),
                    "mid_speed_deficit_kph": round(mid_gap, 2),
                    "late_speed_deficit_kph": round(late_gap, 2),
                    "end_speed_deficit_kph": round(end_gap, 2),
                    "gap_growth_kph": round(gap_growth, 2),
                    "acceleration_gain_deficit_kph": round(acceleration_gain_deficit, 2),
                    "target_max_speed_kph": round(target_max, 2),
                    "reference_max_speed_kph": round(reference_max, 2),
                    "max_speed_deficit_kph": round(max_speed_deficit, 2),
                    "target_full_throttle_pct": round(float(np.mean(target_throttle[start_idx:end_idx + 1] >= 0.94)) * 100.0, 1),
                    "reference_full_throttle_pct": round(float(np.mean(ref_throttle[start_idx:end_idx + 1] >= 0.94)) * 100.0, 1),
                    "below_reference_p25_ratio": round_or_none(below_reference_p25, 3),
                }
            )

        eligible = [item for item in segments if float(item.get("distance_m") or 0.0) >= 220.0]
        drag_segments = [
            item for item in eligible
            if item.get("classification") in {"DRAG_LIMITED", "MIXED_EXIT_AND_DRAG"}
        ]
        possible_drag = [
            item for item in eligible
            if item.get("classification") in {"DRAG_LIMITED", "MIXED_EXIT_AND_DRAG", "POSSIBLE_DRAG"}
        ]
        exit_segments = [item for item in eligible if item.get("classification") == "EXIT_LIMITED"]
        persistent_top_speed_segments = [
            item for item in eligible
            if float(item.get("late_speed_deficit_kph") or 0.0) >= 4.0
            and float(item.get("max_speed_deficit_kph") or 0.0) >= 4.0
        ]
        drag_ratio = len(drag_segments) / len(eligible) if eligible else 0.0
        possible_drag_ratio = len(possible_drag) / len(eligible) if eligible else 0.0
        persistent_top_speed_ratio = len(persistent_top_speed_segments) / len(eligible) if eligible else 0.0

        # Always calculate observed straight-line medians when there are eligible
        # straights. Previously these values were only populated when a problem
        # segment existed, which made a valid "no bottleneck" result appear as
        # "-- km/h" and look like missing data.
        all_late_gaps = [float(item["late_speed_deficit_kph"]) for item in eligible]
        all_growths = [float(item["gap_growth_kph"]) for item in eligible]
        all_max_gaps = [float(item["max_speed_deficit_kph"]) for item in eligible]

        problem_late_source = possible_drag if possible_drag else persistent_top_speed_segments
        problem_late_gaps = [float(item["late_speed_deficit_kph"]) for item in problem_late_source]
        problem_growths = [float(item["gap_growth_kph"]) for item in possible_drag]
        problem_max_source = possible_drag if possible_drag else persistent_top_speed_segments
        problem_max_gaps = [float(item["max_speed_deficit_kph"]) for item in problem_max_source]
        p25_support = [
            float(item["below_reference_p25_ratio"])
            for item in (possible_drag if possible_drag else persistent_top_speed_segments)
            if item.get("below_reference_p25_ratio") is not None
        ]

        data_sufficient = len(eligible) >= minimum_straights_for_repetition
        data_quality = (
            "GOOD" if len(eligible) >= recommended_straights_for_strong_diagnosis else
            "LIMITED" if data_sufficient else
            "INSUFFICIENT"
        )

        repeated_drag = data_sufficient and len(drag_segments) >= 2 and drag_ratio >= 0.45
        broad_drag = data_sufficient and len(possible_drag) >= 3 and possible_drag_ratio >= 0.60
        distribution_support = (float(np.median(p25_support)) >= 0.55) if p25_support else False
        aero_drag_suspect = bool(repeated_drag and (strong_consensus or distribution_support or broad_drag))
        persistent_top_speed_opportunity = bool(
            data_sufficient
            and len(persistent_top_speed_segments) >= 2
            and persistent_top_speed_ratio >= 0.45
            and (strong_consensus or unique_driver_count >= 3)
        )

        confidence = 0.0
        if possible_drag and data_sufficient:
            confidence = 0.40
            confidence += min(0.18, 0.06 * len(drag_segments))
            confidence += min(0.12, max(0.0, float(np.median(problem_growths)) - 1.5) * 0.035)
            confidence += 0.08 if strong_consensus else 0.0
            confidence += 0.05 if unique_driver_count >= 3 else 0.0
            confidence += 0.05 * max(0.0, min(1.0, condition_similarity))
            confidence = min(0.90, confidence)

        if not data_sufficient:
            diagnosis = "INSUFFICIENT_STRAIGHT_DATA"
            status = "INSUFFICIENT_STRAIGHT_DATA"
        elif aero_drag_suspect:
            diagnosis = "AERO_DRAG_SUSPECT"
            status = "READY"
        elif persistent_top_speed_opportunity:
            diagnosis = "PERSISTENT_TOP_SPEED_DEFICIT"
            status = "READY"
        elif len(exit_segments) >= 2 and not possible_drag:
            diagnosis = "EXIT_SPEED_LIMITED"
            status = "READY"
        elif possible_drag:
            diagnosis = "MIXED"
            status = "READY"
        else:
            diagnosis = "NO_STRAIGHT_BOTTLENECK"
            status = "READY"

        metric_scope = "PROBLEM_SEGMENTS" if (problem_late_gaps or problem_growths or problem_max_gaps) else "ALL_ELIGIBLE_STRAIGHTS"
        displayed_late_gaps = problem_late_gaps if problem_late_gaps else all_late_gaps
        displayed_growths = problem_growths if problem_growths else all_growths
        displayed_max_gaps = problem_max_gaps if problem_max_gaps else all_max_gaps

        return {
            "status": status,
            "diagnosis": diagnosis,
            "data_sufficient": data_sufficient,
            "data_quality": data_quality,
            "required_straights": minimum_straights_for_repetition,
            "recommended_straights": recommended_straights_for_strong_diagnosis,
            "detected_straight_segments": len(segments),
            "eligible_straights": len(eligible),
            "drag_support_count": len(drag_segments),
            "possible_drag_count": len(possible_drag),
            "exit_limited_count": len(exit_segments),
            "persistent_top_speed_count": len(persistent_top_speed_segments),
            "drag_support_ratio": round(drag_ratio, 3),
            "persistent_top_speed_ratio": round(persistent_top_speed_ratio, 3),
            "aero_drag_suspect": aero_drag_suspect,
            "persistent_top_speed_opportunity": persistent_top_speed_opportunity,
            "confidence": round(confidence, 2),
            "metric_scope": metric_scope,
            "median_late_speed_deficit_kph": round_or_none(float(np.median(displayed_late_gaps)) if displayed_late_gaps else None, 2),
            "median_gap_growth_kph": round_or_none(float(np.median(displayed_growths)) if displayed_growths else None, 2),
            "median_max_speed_deficit_kph": round_or_none(float(np.median(displayed_max_gaps)) if displayed_max_gaps else None, 2),
            "overall_median_late_speed_deficit_kph": round_or_none(float(np.median(all_late_gaps)) if all_late_gaps else None, 2),
            "overall_median_gap_growth_kph": round_or_none(float(np.median(all_growths)) if all_growths else None, 2),
            "overall_median_max_speed_deficit_kph": round_or_none(float(np.median(all_max_gaps)) if all_max_gaps else None, 2),
            "reference_count": reference_count,
            "unique_driver_count": unique_driver_count,
            "condition_similarity": round(condition_similarity, 3),
            "segments": segments,
        }

    @staticmethod
    def _high_speed_stability_risk(external_reference: dict[str, Any]) -> dict[str, Any]:
        risks: list[dict[str, Any]] = []
        for corner in external_reference.get("corners", []) or []:
            if corner.get("speed_class") != "HIGH" or float(corner.get("delta_s") or 0.0) < 0.03:
                continue
            consensus = corner.get("reference_consensus", {}) or {}
            if float(consensus.get("loss_agreement_ratio") or 0.0) < 0.60:
                continue
            target = corner.get("target") or {}
            reference = corner.get("reference") or {}
            correction_excess = int(target.get("steering_corrections") or 0) - int(reference.get("steering_corrections") or 0)
            relift_excess = int(target.get("throttle_relifts") or 0) - int(reference.get("throttle_relifts") or 0)
            yaw_target = target.get("avg_abs_yaw_rate")
            yaw_reference = reference.get("avg_abs_yaw_rate")
            yaw_excess = (
                yaw_target is not None and yaw_reference is not None
                and float(yaw_target) > float(yaw_reference) * 1.08
            )
            if correction_excess >= 1 or relift_excess >= 1 or yaw_excess:
                risks.append(
                    {
                        "corner": corner.get("label"),
                        "delta_s": corner.get("delta_s"),
                        "correction_excess": correction_excess,
                        "relift_excess": relift_excess,
                    }
                )
        return {
            "risk_count": len(risks),
            "high_speed_stability_risk": len(risks) >= 2,
            "corners": risks[:4],
        }

    def _external_setup_candidates(
        self,
        session_mode: SessionMode,
        external_reference: dict[str, Any],
        setup: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not external_reference.get("decision_ready"):
            return []

        reference_count = int(external_reference.get("decision_reference_count") or 0)
        unique_drivers = int(external_reference.get("unique_driver_count") or 0)
        strong_consensus = bool(external_reference.get("strong_consensus"))
        condition_similarity = float((external_reference.get("aggregate") or {}).get("condition_similarity_median") or 0.5)
        confidence_cap = float(session_policy(session_mode)["setup_confidence_cap"])
        if not strong_consensus:
            confidence_cap = min(confidence_cap, 0.62)

        candidates: list[dict[str, Any]] = []
        straight = ((external_reference.get("performance_diagnostics") or {}).get("straight_line") or {})
        stability = self._high_speed_stability_risk(external_reference)
        rear_wing = setup.get("rear_wing_angle_deg")

        # ------------------------------------------------------------------
        # 1) Straight-line / aero drag opportunity
        # ------------------------------------------------------------------
        if straight.get("aero_drag_suspect") or straight.get("persistent_top_speed_opportunity"):
            strict_drag = bool(straight.get("aero_drag_suspect"))
            support = int(straight.get("drag_support_count") or 0)
            persistent_support = int(straight.get("persistent_top_speed_count") or 0)
            eligible = int(straight.get("eligible_straights") or 0)
            late_gap = float(straight.get("median_late_speed_deficit_kph") or 0.0)
            growth = float(straight.get("median_gap_growth_kph") or 0.0)
            max_gap = float(straight.get("median_max_speed_deficit_kph") or 0.0)
            confidence = float(straight.get("confidence") or 0.55)
            if not strict_drag:
                confidence = min(confidence, 0.56)
            confidence += 0.04 if strong_consensus else 0.0
            if stability.get("high_speed_stability_risk"):
                confidence -= 0.12
            confidence = min(confidence_cap, max(0.40, confidence))

            if rear_wing is not None and float(rear_wing) >= 1.0:
                adjustment = make_adjustment(
                    setting="rear_wing_angle_deg",
                    label="Rear wing angle",
                    original=float(rear_wing),
                    value=float(rear_wing) - 1.0,
                    unit="deg",
                    confidence=confidence,
                    evidence=(
                        (
                            f"Garage61 빠른 기준 {reference_count}개에서 장거리 풀스로틀 {support}/{eligible}개 구간이 직선 후반 속도차 증가 패턴을 보임. "
                            if strict_drag else
                            f"Garage61 빠른 기준 {reference_count}개에서 장거리 풀스로틀 {persistent_support}/{eligible}개 구간의 후반/최고속 부족이 반복됨. "
                        )
                        + f"후반 중앙 속도 부족 {late_gap:.1f} km/h, 직선 내 속도차 증가 {growth:+.1f} km/h, 최고속 중앙 부족 {max_gap:.1f} km/h"
                    ),
                    tradeoff=(
                        "직선 공기저항은 줄지만 고속 코너 후방 다운포스·제동 안정성이 감소할 수 있음"
                        + (". 현재 고속 안정성 위험 신호가 있어 특히 보수적으로 검증해야 함" if stability.get("high_speed_stability_risk") else "")
                    ),
                    validation=(
                        "Rear wing을 1단계만 낮추고 동일 조건 A/B에서 장거리 직선 후반 속도, 최고속, 전체 랩타임과 "
                        "고속 코너 스로틀 리프트·조향 보정을 동시에 비교"
                    ),
                )
            else:
                adjustment = make_directional_adjustment(
                    setting="rear_wing_angle_deg",
                    label="Rear wing angle",
                    original=rear_wing,
                    direction="DECREASE",
                    step_text="Garage 1 click decrease",
                    unit="deg",
                    confidence=confidence,
                    evidence=(
                        f"장거리 풀스로틀 {(support if strict_drag else persistent_support)}/{eligible}개 구간에서 직선 후반 속도 부족이 반복됨 "
                        f"(후반 {late_gap:.1f} km/h, gap growth {growth:+.1f} km/h)"
                    ),
                    tradeoff="고속 코너 후방 다운포스와 제동 안정성이 감소할 수 있음",
                    validation="Garage에서 Rear wing을 정확히 1 click 낮춘 뒤 동일 조건 A/B 검증",
                )

            adjustment.update(
                {
                    "tier": "SETUP_ASSIST",
                    "source": "GARAGE61_STRAIGHT_CONSENSUS",
                    "experimental": True,
                    "category": "AERO_DRAG" if strict_drag else "TOP_SPEED_OPPORTUNITY",
                    "priority_score": round(min(1.0, (0.55 if strict_drag else 0.48) + late_gap / 20.0 + max(0.0, growth) / 25.0), 3),
                    "expected_effect": "직선 후반 가속 유지와 최고속 개선",
                    "performance_support": straight,
                    "safety_gate": stability,
                    "reference_support": {
                        "reference_count": reference_count,
                        "unique_driver_count": unique_drivers,
                        "condition_similarity_median": round_or_none(condition_similarity, 3),
                        "consensus_tier": "STRONG" if strong_consensus else "PROVISIONAL",
                    },
                }
            )
            if adjustment.get("validator", {}).get("passed", True):
                candidates.append(adjustment)

            # Excess toe also creates drag, but the IBT does not expose a
            # universal per-car click size/sign convention. Surface it as a
            # lower-priority directional hypothesis rather than inventing a
            # numeric value.
            front_toe = setup.get("front_toe_mm")
            if front_toe is not None and abs(float(front_toe)) >= 0.20:
                toe_candidate = make_directional_adjustment(
                    setting="front_toe_mm",
                    label="Front total toe",
                    original=float(front_toe),
                    direction="TOWARD_ZERO",
                    step_text="Garage 1 click toward 0",
                    unit="mm",
                    confidence=min(confidence_cap, max(0.38, confidence - 0.18)),
                    evidence=(
                        "직선 후반 속도 부족이 반복되며 현재 Front total toe가 0에서 떨어져 있음. "
                        "Toe drag가 부가 요인인지 1-click A/B로 분리 가능"
                    ),
                    tradeoff="직진 저항은 줄 수 있으나 턴인 응답과 제동 안정성이 달라질 수 있음",
                    validation="Rear wing 후보와 동시에 바꾸지 말고 별도 run에서 Front total toe만 1 click 0 방향으로 변경",
                )
                toe_candidate.update(
                    {
                        "tier": "SECONDARY_HYPOTHESIS",
                        "source": "GARAGE61_STRAIGHT_CONSENSUS",
                        "experimental": True,
                        "category": "AERO_DRAG_SECONDARY",
                        "priority_score": round(max(0.0, float(adjustment.get("priority_score") or 0.0) - 0.22), 3),
                        "expected_effect": "직선 rolling/aero drag의 부가 감소 가능성",
                    }
                )
                candidates.append(toe_candidate)

        # ------------------------------------------------------------------
        # 2) Corner balance / traction opportunities
        # ------------------------------------------------------------------
        rear_arb = setup.get("rear_arb_blades")
        front_arb = setup.get("front_arb_blades")
        for corner in external_reference.get("top_losses", [])[:8]:
            delta = float(corner.get("delta_s") or 0.0)
            consensus = corner.get("reference_consensus", {}) or {}
            agreement = float(consensus.get("loss_agreement_ratio") or 0.0)
            if delta < 0.03 or agreement < 0.60:
                continue
            target = corner.get("target") or {}
            reference = corner.get("reference") or {}
            steer_excess = (target.get("avg_abs_steer_deg") or 0.0) - (reference.get("avg_abs_steer_deg") or 0.0)
            target_eff = target.get("steering_efficiency")
            reference_eff = reference.get("steering_efficiency")
            full_throttle_delay = pct_distance(
                target.get("full_throttle_pct"), reference.get("full_throttle_pct"), self.track_length_m
            )
            min_speed_deficit = (reference.get("min_speed_kph") or 0.0) - (target.get("min_speed_kph") or 0.0)
            target_relifts = int(target.get("throttle_relifts") or 0)
            ref_relifts = int(reference.get("throttle_relifts") or 0)
            target_corr = int(target.get("steering_corrections") or 0)
            ref_corr = int(reference.get("steering_corrections") or 0)

            base_confidence = 0.42 + 0.055 * max(0, reference_count - 2) + 0.09 * agreement
            base_confidence += min(0.08, max(0.0, delta - 0.03) * 0.40)
            base_confidence += 0.05 if unique_drivers >= 2 else -0.025
            base_confidence += 0.06 * max(-0.5, min(0.5, condition_similarity - 0.5))
            confidence = min(confidence_cap, base_confidence)

            rotation_deficit = (
                steer_excess >= 2.0
                and target_eff is not None
                and reference_eff is not None
                and float(target_eff) < float(reference_eff) * 0.97
                and min_speed_deficit >= 1.0
            )
            traction_deficit = (
                full_throttle_delay is not None
                and full_throttle_delay >= 8.0
                and (corner.get("issue_type") == "EXIT" or target_relifts > ref_relifts)
            )
            unstable_rotation = target_corr > ref_corr and (
                target_relifts > ref_relifts
                or (
                    target.get("avg_abs_yaw_rate") is not None
                    and reference.get("avg_abs_yaw_rate") is not None
                    and float(target["avg_abs_yaw_rate"]) > float(reference["avg_abs_yaw_rate"]) * 1.08
                )
            )

            adjustment: Optional[dict[str, Any]] = None
            if traction_deficit and rear_arb is not None and float(rear_arb) > 1.0:
                adjustment = make_adjustment(
                    setting="rear_arb_blades",
                    label="Rear ARB blades",
                    original=float(rear_arb),
                    value=float(rear_arb) - 1.0,
                    unit="blade",
                    confidence=confidence,
                    evidence=(
                        f"{corner['label']} 손실 {delta:.3f}초, 빠른 기준 손실방향 일치 {agreement * 100:.0f}%, "
                        f"풀스로틀 복귀가 약 {full_throttle_delay:.0f}m 늦음"
                    ),
                    tradeoff="탈출 트랙션과 연석 순응성은 좋아질 수 있으나 미드코너 회전성이 감소할 수 있음",
                    validation="Rear ARB -1만 적용하고 풀스로틀 복귀·출구속도·미드코너 손실을 A/B 비교",
                )
                category = "EXIT_TRACTION"
                expected = "코너 탈출 트랙션과 풀스로틀 복귀 개선"
            elif rotation_deficit and not traction_deficit:
                if rear_arb is not None:
                    adjustment = make_adjustment(
                        setting="rear_arb_blades",
                        label="Rear ARB blades",
                        original=float(rear_arb),
                        value=float(rear_arb) + 1.0,
                        unit="blade",
                        confidence=confidence,
                        evidence=(
                            f"{corner['label']} 손실 {delta:.3f}초, 조향량 +{steer_excess:.1f}°인데 yaw/steering 효율이 기준보다 낮고 "
                            f"최저속도가 {min_speed_deficit:.1f} km/h 낮음"
                        ),
                        tradeoff="회전성은 좋아질 수 있으나 저속 탈출 트랙션과 연석 순응성이 감소할 수 있음",
                        validation="Rear ARB +1만 적용하고 조향효율·최저속도·출구 트랙션을 A/B 비교",
                    )
                    category = "ROTATION_DEFICIT"
                    expected = "진입/미드코너 회전 반응 개선"
                elif front_arb is not None and float(front_arb) > 1.0:
                    adjustment = make_adjustment(
                        setting="front_arb_blades",
                        label="Front ARB blades",
                        original=float(front_arb),
                        value=float(front_arb) - 1.0,
                        unit="blade",
                        confidence=max(0.35, confidence - 0.05),
                        evidence=f"{corner['label']}에서 조향량 대비 회전 응답 부족이 반복됨",
                        tradeoff="기계적 전륜 그립은 증가할 수 있으나 응답이 둔해지고 차체 롤이 늘 수 있음",
                        validation="Front ARB -1만 적용하고 같은 코너의 최저속도·조향량과 고속 안정성을 A/B 비교",
                    )
                    category = "ROTATION_DEFICIT"
                    expected = "전륜 기계적 그립과 회전 응답 개선"
            elif unstable_rotation and rear_arb is not None and float(rear_arb) > 1.0:
                adjustment = make_adjustment(
                    setting="rear_arb_blades",
                    label="Rear ARB blades",
                    original=float(rear_arb),
                    value=float(rear_arb) - 1.0,
                    unit="blade",
                    confidence=max(0.36, confidence - 0.04),
                    evidence=(
                        f"{corner['label']}에서 기준보다 조향 보정/스로틀 리프트가 많고 구간 손실 {delta:.3f}초가 반복됨"
                    ),
                    tradeoff="후방 안정성은 좋아질 수 있으나 미드코너 회전성이 감소할 수 있음",
                    validation="Rear ARB -1 후 조향 보정·스로틀 리프트·코너 손실을 함께 비교",
                )
                category = "REAR_STABILITY"
                expected = "후방 안정성과 자신감 있는 스로틀 복귀 개선"
            else:
                category = ""
                expected = ""

            if adjustment and adjustment.get("validator", {}).get("passed"):
                adjustment.update(
                    {
                        "tier": "SETUP_ASSIST",
                        "source": "GARAGE61_CORNER_CONSENSUS",
                        "experimental": True,
                        "category": category,
                        "priority_score": round(min(1.0, 0.45 + delta * 1.7 + agreement * 0.18), 3),
                        "expected_effect": expected,
                        "corner_id": corner.get("id"),
                        "reference_support": {
                            "reference_count": reference_count,
                            "unique_driver_count": unique_drivers,
                            "loss_agreement_ratio": round(agreement, 3),
                            "median_corner_loss_s": round_or_none(delta, 3),
                            "condition_similarity_median": round_or_none(condition_similarity, 3),
                            "consensus_tier": "STRONG" if strong_consensus else "PROVISIONAL",
                        },
                    }
                )
                candidates.append(adjustment)

            # Differential preload is useful for persistent rotation deficit,
            # but Garage increments vary by car. Keep it directional and below
            # the ARB candidate rather than inventing Nm.
            if rotation_deficit and setup.get("diff_preload_nm") is not None:
                diff_candidate = make_directional_adjustment(
                    setting="diff_preload_nm",
                    label="Differential preload",
                    original=float(setup["diff_preload_nm"]),
                    direction="DECREASE",
                    step_text="Garage 1 click decrease",
                    unit="Nm",
                    confidence=max(0.32, min(confidence_cap, confidence - 0.14)),
                    evidence=(
                        f"{corner['label']}에서 조향량 증가 대비 회전 효율과 최저속도가 동시에 부족함. "
                        "Preload가 회전 저항의 보조 원인인지 분리 시험 가능"
                    ),
                    tradeoff="진입/미드 회전은 좋아질 수 있으나 구동 안정성과 코너 탈출 거동이 달라질 수 있음",
                    validation="ARB 후보와 동시에 바꾸지 말고 Diff preload만 1 click 낮춰 별도 A/B",
                )
                diff_candidate.update(
                    {
                        "tier": "SECONDARY_HYPOTHESIS",
                        "source": "GARAGE61_CORNER_CONSENSUS",
                        "experimental": True,
                        "category": "DIFF_ROTATION",
                        "priority_score": round(max(0.0, 0.35 + delta * 1.2 + agreement * 0.12), 3),
                        "expected_effect": "진입/미드코너 differential locking 감소에 따른 회전 보조",
                        "corner_id": corner.get("id"),
                    }
                )
                candidates.append(diff_candidate)

            # Brake bias can be a useful entry-balance lever, but the click
            # size is car-specific. Only surface it when the external cohort
            # shows an entry/braking balance pattern, and keep it below the
            # mechanical/Aero primary candidate.
            if corner.get("issue_type") in {"ENTRY", "BRAKING", "BALANCE_SUSPECT"} and setup.get("brake_bias_pct") is not None:
                if unstable_rotation:
                    bb_direction = "INCREASE"
                    bb_step = "Garage 1 click forward"
                    bb_effect = "제동/진입 후방 안정성 증가"
                    bb_tradeoff = "진입 회전이 감소하고 앞타이어/ABS 부담이 증가할 수 있음"
                elif rotation_deficit and not traction_deficit:
                    bb_direction = "DECREASE"
                    bb_step = "Garage 1 click rearward"
                    bb_effect = "트레일 브레이킹 구간 회전 보조"
                    bb_tradeoff = "후륜 잠김/진입 불안정 위험이 증가할 수 있음"
                else:
                    bb_direction = ""
                    bb_step = ""
                    bb_effect = ""
                    bb_tradeoff = ""
                if bb_direction:
                    bb_candidate = make_directional_adjustment(
                        setting="brake_bias_pct",
                        label="Brake bias",
                        original=float(setup["brake_bias_pct"]),
                        direction=bb_direction,
                        step_text=bb_step,
                        unit="%",
                        confidence=max(0.30, min(confidence_cap, confidence - 0.18)),
                        evidence=(
                            f"{corner['label']}의 {corner.get('issue_type')} 손실이 빠른 기준에서 {agreement * 100:.0f}% 방향 일치. "
                            "Brake bias가 진입 밸런스에 미치는 영향을 한 click로 분리 검증"
                        ),
                        tradeoff=bb_tradeoff,
                        validation="ARB/Diff 후보와 동시에 바꾸지 말고 Brake bias만 1 click 변경해 제동 안정성·회전·ABS 사용을 비교",
                    )
                    bb_candidate.update(
                        {
                            "tier": "SECONDARY_HYPOTHESIS",
                            "source": "GARAGE61_CORNER_CONSENSUS",
                            "experimental": True,
                            "category": "BRAKE_BALANCE",
                            "priority_score": round(max(0.0, 0.31 + delta * 1.0 + agreement * 0.10), 3),
                            "expected_effect": bb_effect,
                            "corner_id": corner.get("id"),
                        }
                    )
                    candidates.append(bb_candidate)

        # ------------------------------------------------------------------
        # 3) High-speed stability can justify *more* wing if there is no
        # repeated drag deficit. This is the inverse aero case.
        # ------------------------------------------------------------------
        if (
            stability.get("high_speed_stability_risk")
            and straight.get("status") == "READY"
            and bool(straight.get("data_sufficient", True))
            and not straight.get("aero_drag_suspect")
            and not straight.get("persistent_top_speed_opportunity")
            and rear_wing is not None
        ):
            confidence = min(confidence_cap, 0.48 + 0.06 * min(3, int(stability.get("risk_count") or 0)))
            adjustment = make_adjustment(
                setting="rear_wing_angle_deg",
                label="Rear wing angle",
                original=float(rear_wing),
                value=float(rear_wing) + 1.0,
                unit="deg",
                confidence=confidence,
                evidence=(
                    f"고속 코너 {stability.get('risk_count', 0)}곳에서 빠른 기준보다 조향 보정/스로틀 리프트가 반복되며 "
                    f"유효 직선 {int(straight.get('eligible_straights') or 0)}개 비교에서 반복 drag deficit은 검출되지 않음"
                ),
                tradeoff="고속 안정성은 좋아질 수 있으나 최고속과 직선 가속이 감소함",
                validation="Rear wing +1 후 고속 코너 리프트·보정 감소량과 직선 최고속 손실을 동시에 검증",
            )
            if adjustment.get("validator", {}).get("passed"):
                adjustment.update(
                    {
                        "tier": "SETUP_ASSIST",
                        "source": "GARAGE61_HIGH_SPEED_STABILITY",
                        "experimental": True,
                        "category": "HIGH_SPEED_STABILITY",
                        "priority_score": round(min(1.0, 0.52 + 0.07 * int(stability.get("risk_count") or 0)), 3),
                        "expected_effect": "고속 코너 후방 안정성과 스로틀 유지율 개선",
                        "safety_gate": stability,
                    }
                )
                candidates.append(adjustment)

        # Keep the strongest hypothesis for a setting/direction. A setting may
        # not be recommended both up and down in the same run.
        def direction_key(item: dict[str, Any]) -> str:
            original = item.get("original_value")
            value = item.get("value")
            if isinstance(original, (int, float)) and isinstance(value, (int, float)):
                return "UP" if float(value) > float(original) else "DOWN"
            return str(item.get("direction") or "")

        candidates.sort(
            key=lambda item: (
                float(item.get("priority_score") or 0.0),
                float(item.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        seen_settings: dict[str, str] = {}
        for candidate in candidates:
            setting = str(candidate.get("setting") or "")
            direction = direction_key(candidate)
            if setting in seen_settings:
                # Do not emit conflicting directions or duplicate candidates.
                continue
            selected.append(candidate)
            seen_settings[setting] = direction
            if len(selected) >= 5:
                break
        return selected


    # ------------------------------------------------------------------
    # Vehicle diagnostics
    # ------------------------------------------------------------------

    def _vehicle_diagnostics(self, target: Optional[LapSegment]) -> dict[str, Any]:
        return {
            "tires": self._tire_diagnostics(target),
            "ride_height": self._ride_height_diagnostics(),
            "aero": self._aero_diagnostics(),
            "fuel": self._fuel_summary(),
            "controls": self._control_summary(),
        }

    def _tire_diagnostics(self, target: Optional[LapSegment]) -> dict[str, Any]:
        tires: dict[str, Any] = {}
        if target is None:
            return {"status": "NO_VALID_LAP", "wheels": tires}

        end = target.end_idx
        start = max(target.start_idx, end - self.telemetry.tick_rate)
        window = slice(start, end + 1)
        for wheel in ("LF", "RF", "LR", "RR"):
            surface = [self._median_channel(f"{wheel}temp{position}", window) for position in ("L", "M", "R")]
            carcass = [self._median_channel(f"{wheel}tempC{position}", window) for position in ("L", "M", "R")]
            wear = [self._median_channel(f"{wheel}wear{position}", window) for position in ("L", "M", "R")]
            pressure = self._median_channel(f"{wheel}pressure", window)
            cold_pressure = self._median_channel(f"{wheel}coldPressure", window)
            carcass_dynamic = self._channel_is_dynamic(f"{wheel}tempCL")
            wear_dynamic = self._channel_is_dynamic(f"{wheel}wearL")
            tires[wheel] = {
                "surface_lmr_c": [round_or_none(value, 1) for value in surface],
                "surface_spread_c": round_or_none(max_finite(surface) - min_finite(surface), 1)
                if finite_values(surface)
                else None,
                "carcass_lmr_c": [round_or_none(value, 1) for value in carcass],
                "carcass_status": "DYNAMIC" if carcass_dynamic else "PIT_SNAPSHOT",
                "wear_lmr_pct_remaining": [round_or_none(value * 100, 1) if value is not None else None for value in wear],
                "wear_status": "DYNAMIC" if wear_dynamic else "PIT_SNAPSHOT",
                "hot_pressure_kpa": round_or_none(pressure, 1),
                "cold_pressure_kpa": round_or_none(cold_pressure, 1),
                "pressure_gain_kpa": round_or_none(pressure - cold_pressure, 1)
                if pressure is not None and cold_pressure is not None
                else None,
            }

        front_pressure = mean_finite([tires["LF"]["hot_pressure_kpa"], tires["RF"]["hot_pressure_kpa"]])
        rear_pressure = mean_finite([tires["LR"]["hot_pressure_kpa"], tires["RR"]["hot_pressure_kpa"]])
        lap_trend: list[dict[str, Any]] = []
        for lap in self.valid_laps:
            lap_end = lap.end_idx
            lap_start = max(lap.start_idx, lap_end - self.telemetry.tick_rate)
            lap_window = slice(lap_start, lap_end + 1)
            surface_values = [
                self._median_channel(f"{wheel}temp{position}", lap_window)
                for wheel in ("LF", "RF", "LR", "RR")
                for position in ("L", "M", "R")
            ]
            pressure_values = [
                self._median_channel(f"{wheel}pressure", lap_window)
                for wheel in ("LF", "RF", "LR", "RR")
            ]
            lap_trend.append(
                {
                    "lap_number": lap.lap_number,
                    "lap_time_s": round_or_none(lap.lap_time_s, 4),
                    "mean_surface_temp_c": round_or_none(mean_finite(surface_values), 1),
                    "mean_hot_pressure_kpa": round_or_none(mean_finite(pressure_values), 1),
                }
            )

        temp_slope = self._trend_slope(
            [item["lap_number"] for item in lap_trend],
            [item.get("mean_surface_temp_c") for item in lap_trend],
        )
        pressure_slope = self._trend_slope(
            [item["lap_number"] for item in lap_trend],
            [item.get("mean_hot_pressure_kpa") for item in lap_trend],
        )
        valid_lap_count = len(self.valid_laps)
        if valid_lap_count < 3:
            recommendation_reason = (
                f"유효 랩이 {valid_lap_count}개이고 차량별 목표 열간압력 범위가 IBT에 없어 "
                "공기압 변경을 확정하지 않습니다."
            )
        else:
            recommendation_reason = (
                "랩별 온도·압력 추세는 계산했지만 차량별 목표 열간압력 범위와 Garage 상한이 "
                "IBT에 없어 수치 변경은 보류합니다."
            )
        return {
            "status": "VALID" if all(tires[wheel]["hot_pressure_kpa"] is not None for wheel in tires) else "PARTIAL",
            "measurement": "one-second median immediately before the selected lap finish",
            "wheels": tires,
            "front_rear_pressure_delta_kpa": round_or_none(front_pressure - rear_pressure, 1)
            if front_pressure is not None and rear_pressure is not None
            else None,
            "lap_trend": lap_trend,
            "surface_temp_slope_c_per_lap": round_or_none(temp_slope, 2),
            "hot_pressure_slope_kpa_per_lap": round_or_none(pressure_slope, 2),
            "recommendation": "HOLD_PRESSURE",
            "reason": recommendation_reason,
        }

    def _median_channel(self, name: str, window: slice) -> Optional[float]:
        values = self.channels.get(name)
        if values is None:
            return None
        selected = values[window].astype(float)
        selected = selected[np.isfinite(selected)]
        return float(np.median(selected)) if len(selected) else None

    def _channel_is_dynamic(self, name: str) -> bool:
        values = self.channels.get(name)
        if values is None:
            return False
        finite = values[np.isfinite(values.astype(float))].astype(float)
        return bool(len(finite) and float(np.max(finite) - np.min(finite)) >= 1e-5)

    @staticmethod
    def _trend_slope(x_values: list[Any], y_values: list[Optional[float]]) -> Optional[float]:
        points = [
            (float(x), float(y))
            for x, y in zip(x_values, y_values)
            if y is not None and math.isfinite(float(y))
        ]
        if len(points) < 2:
            return None
        x = np.array([point[0] for point in points], dtype=float)
        y = np.array([point[1] for point in points], dtype=float)
        if float(np.ptp(x)) <= 1e-9:
            return None
        slope, _ = np.polyfit(x, y, 1)
        return float(slope)

    def _ride_height_diagnostics(self) -> dict[str, Any]:
        speed = self.channels.get("Speed")
        surface = self.channels.get("PlayerTrackSurface")
        pit = self.channels.get("OnPitRoad")
        if speed is None:
            return {"status": "MISSING"}
        mask = speed * 3.6 >= 80.0
        if surface is not None:
            mask &= surface == 3
        if pit is not None:
            mask &= ~pit.astype(bool)
        if not np.any(mask):
            return {"status": "NO_HIGH_SPEED_SAMPLES"}

        channels = ["CFSRrideHeight", "LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]
        stats: dict[str, Any] = {}
        for name in channels:
            values = self.channels.get(name)
            if values is None:
                continue
            mm = values[mask].astype(float) * 1000.0
            stats[name] = {
                "minimum_mm": round_or_none(float(np.min(mm)), 1),
                "p0_1_mm": round_or_none(float(np.quantile(mm, 0.001)), 1),
                "p1_mm": round_or_none(float(np.quantile(mm, 0.01)), 1),
                "median_mm": round_or_none(float(np.median(mm)), 1),
            }

        center = self.channels.get("CFSRrideHeight")
        front_avg = None
        if self.channels.get("LFrideHeight") is not None and self.channels.get("RFrideHeight") is not None:
            front_avg = (self.channels["LFrideHeight"] + self.channels["RFrideHeight"]) / 2.0
        bottoming_mask = np.zeros(len(speed), dtype=bool)
        if center is not None:
            bottoming_mask |= (center * 1000.0 <= 3.0) & mask
        if front_avg is not None:
            bottoming_mask |= (front_avg * 1000.0 <= 5.0) & mask
        events = contiguous_groups(np.flatnonzero(bottoming_mask), max_gap=self.telemetry.tick_rate // 5, min_len=2)
        affected_valid_laps = [
            lap.lap_number
            for lap in self.valid_laps
            if np.any(bottoming_mask[lap.start_idx : lap.end_idx + 1])
        ]
        return {
            "status": "VALID",
            "bottoming_detected": bool(events),
            "bottoming_events": len(events),
            "affected_valid_laps": affected_valid_laps,
            "threshold_note": "center splitter <=3 mm or front axle mean <=5 mm above 80 km/h",
            "channels": stats,
        }

    def _aero_diagnostics(self) -> dict[str, Any]:
        required = ["Speed", "LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]
        if not all(name in self.channels for name in required):
            return {"status": "MISSING"}
        speed_kph = self.channels["Speed"] * 3.6
        mask = speed_kph >= 200.0
        surface = self.channels.get("PlayerTrackSurface")
        pit = self.channels.get("OnPitRoad")
        if surface is not None:
            mask &= surface == 3
        if pit is not None:
            mask &= ~pit.astype(bool)
        if not np.any(mask):
            return {"status": "NO_200_KPH_SAMPLES"}

        front = (self.channels["LFrideHeight"][mask] + self.channels["RFrideHeight"][mask]) * 500.0
        rear = (self.channels["LRrideHeight"][mask] + self.channels["RRrideHeight"][mask]) * 500.0
        pitch = rear - front
        setup_summary = extract_setup_summary(self.telemetry.setup)
        return {
            "status": "VALID",
            "front_ride_height_median_mm": round(float(np.median(front)), 1),
            "rear_ride_height_median_mm": round(float(np.median(rear)), 1),
            "aero_pitch_median_mm": round(float(np.median(pitch)), 1),
            "aero_pitch_p10_mm": round(float(np.quantile(pitch, 0.10)), 1),
            "aero_pitch_p90_mm": round(float(np.quantile(pitch, 0.90)), 1),
            "minimum_splitter_height_high_speed_mm": round_or_none(
                float(np.min(self.channels["CFSRrideHeight"][mask]) * 1000.0)
                if "CFSRrideHeight" in self.channels
                else None,
                1,
            ),
            "setup_calculated_front_height_mm": setup_summary.get("front_rh_at_speed_mm"),
            "setup_calculated_rear_height_mm": setup_summary.get("rear_rh_at_speed_mm"),
            "rear_wing_angle_deg": setup_summary.get("rear_wing_angle_deg"),
        }

    def _fuel_summary(self) -> dict[str, Any]:
        values = [lap.fuel_used_l for lap in self.valid_laps if lap.fuel_used_l is not None and lap.fuel_used_l > 0]
        median = float(np.median(values)) if values else None
        return {
            "fuel_per_valid_lap_l": round_or_none(median, 3),
            "sample_laps": len(values),
            "qualifying_3_lap_estimate_l": round_or_none(median * 3.0, 1) if median else None,
            "note": (
                "퀄리파잉 기본 구조는 아웃랩 1 + 계측랩 2, 총 3바퀴입니다. "
                "연료량은 정보 표시용이며 셋업 변경 엔진의 입력으로 사용하지 않습니다."
            ),
        }

    def _control_summary(self) -> dict[str, Any]:
        return {
            "brake_bias_pct": round_or_none(self._last_finite("dcBrakeBias"), 2),
            "abs_setting": round_or_none(self._last_finite("dcABS"), 0),
            "tc_setting": round_or_none(self._last_finite("dcTractionControl"), 0),
            "throttle_shape": round_or_none(self._last_finite("dcThrottleShape"), 0),
        }

    def _last_finite(self, channel: str) -> Optional[float]:
        values = self.channels.get(channel)
        if values is None:
            return None
        finite = values[np.isfinite(values.astype(float))]
        return float(finite[-1]) if len(finite) else None

    # ------------------------------------------------------------------
    # Classification and setup rules
    # ------------------------------------------------------------------

    def _classify(
        self,
        session_mode: SessionMode,
        data_quality: dict[str, Any],
        corners: list[dict[str, Any]],
        vehicle: dict[str, Any],
        repetition: dict[str, Any],
    ) -> dict[str, Any]:
        policy = session_policy(session_mode)
        valid_laps = int(data_quality["valid_laps"])
        reasons: list[str] = []
        minimum_comparison_laps = int(policy["comparison_laps"])
        minimum_setup_laps = int(policy["setup_repeat_laps"])
        bottoming_laps = sorted(
            set(vehicle.get("ride_height", {}).get("affected_valid_laps", []))
        )
        repeated_bottoming = len(bottoming_laps) >= 2

        if valid_laps < minimum_comparison_laps:
            if session_mode == SessionMode.QUALIFYING:
                reasons.append(
                    f"유효 계측랩 {valid_laps}개입니다. 퀄리파잉 단일 랩 진단은 가능하지만 두 핫랩 직접 비교에는 2개가 필요합니다."
                )
            else:
                reasons.append(
                    f"유효 랩 {valid_laps}개로 동일 현상의 반복 여부를 판정할 수 없습니다."
                )
            return {
                "label": "INSUFFICIENT_DATA",
                "confidence": round(min(0.50, 0.28 + valid_laps * 0.14), 2),
                "reasons": reasons,
                "minimum_valid_laps": minimum_comparison_laps,
                "session_mode": session_mode.value,
                "scope": "SINGLE_TIMED_LAP" if valid_laps == 1 else "NO_TIMED_LAP",
            }

        if session_mode == SessionMode.QUALIFYING:
            if repeated_bottoming:
                reasons.append(
                    "고속 최저 차고 임계값 도달이 두 계측랩 모두에서 반복되어 객관적인 셋업 제한 신호로 판정했습니다."
                )
                return {
                    "label": "SETUP_LIMITED",
                    "confidence": min(0.68, float(policy["setup_confidence_cap"])),
                    "reasons": reasons,
                    "minimum_valid_laps": 2,
                    "session_mode": session_mode.value,
                    "scope": "QUALIFYING_OBJECTIVE_REPEAT",
                }

            reasons.append(
                "아웃랩 이후 두 계측랩을 정상 퀄리파잉 표본으로 인정하고 직접 비교했습니다."
            )
            positive_losses = [
                corner for corner in corners if (corner.get("delta_s") or 0) >= 0.02
            ]
            if positive_losses:
                reasons.append(
                    "현재 손실은 두 핫랩 사이의 제동·최저속도·스로틀 복귀 차이로 설명되므로 우선 드라이빙 테스트로 분류합니다."
                )
            else:
                reasons.append(
                    "두 핫랩 사이에 반복적인 객관적 차량 이상이 없어 셋업 변경 근거가 부족합니다."
                )
            reasons.append(
                "비객관적 밸런스 문제의 셋업 확정에는 동일 차량·트랙의 별도 퀄리파잉 세션 A/B 근거가 추가로 필요합니다."
            )
            return {
                "label": "DRIVER_LIMITED",
                "confidence": 0.62,
                "reasons": reasons,
                "minimum_valid_laps": 2,
                "session_mode": session_mode.value,
                "scope": "QUALIFYING_TWO_LAP",
            }

        if valid_laps < minimum_setup_laps:
            reasons.append(
                f"유효 랩 {valid_laps}개로 셋업 반복성 판정 기준 {minimum_setup_laps}개를 충족하지 못했습니다."
            )
            positive_losses = [corner for corner in corners if (corner.get("delta_s") or 0) >= 0.02]
            if positive_losses:
                reasons.append("검출된 손실은 우선 드라이빙 입력 차이로 취급합니다.")
            return {
                "label": "INSUFFICIENT_DATA",
                "confidence": round(min(0.58, 0.28 + valid_laps * 0.12), 2),
                "reasons": reasons,
                "minimum_valid_laps": minimum_setup_laps,
                "session_mode": session_mode.value,
                "scope": "REPEATABILITY_NOT_READY",
            }

        repeated_patterns = repetition.get("repeated_setup_patterns", [])
        if repeated_bottoming or repeated_patterns:
            if repeated_bottoming:
                reasons.append(
                    "고속 최저 차고 임계값 도달이 유효 랩 "
                    + ", ".join(str(lap) for lap in bottoming_laps)
                    + "에서 반복되었습니다."
                )
            if repeated_patterns:
                reasons.append(
                    "동일 코너의 조향량 대비 요 응답 저하가 여러 유효 랩에서 반복되었습니다."
                )
            return {
                "label": "SETUP_LIMITED",
                "confidence": 0.78 if repeated_bottoming else 0.72,
                "reasons": reasons,
                "minimum_valid_laps": minimum_setup_laps,
                "session_mode": session_mode.value,
                "scope": "STANDARD_REPEATABILITY",
            }

        reasons.append("손실이 코너별 입력 시점 차이로 설명되며 반복적인 차량 응답 이상이 부족합니다.")
        return {
            "label": "DRIVER_LIMITED",
            "confidence": 0.72,
            "reasons": reasons,
            "minimum_valid_laps": minimum_setup_laps,
            "session_mode": session_mode.value,
            "scope": "STANDARD_REPEATABILITY",
        }


    def _setup_recommendation(
        self,
        session_mode: SessionMode,
        classification: dict[str, Any],
        corners: list[dict[str, Any]],
        vehicle: dict[str, Any],
        repetition: dict[str, Any],
        external_reference: Optional[dict[str, Any]] = None,
        *,
        request: Optional[AnalyzeRequest] = None,
        feedback: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        setup = extract_setup_summary(self.telemetry.setup)
        label = classification["label"]
        confirmed: list[dict[str, Any]] = []
        experimental: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        policy = session_policy(session_mode)

        bottoming_laps = sorted(set(vehicle.get("ride_height", {}).get("affected_valid_laps", [])))
        repeated_bottoming = len(bottoming_laps) >= 2
        repeated_patterns = repetition.get("repeated_setup_patterns", []) or []
        candidate_patterns = repetition.get("candidate_setup_patterns", []) or []

        if repeated_bottoming:
            current = setup.get("front_ride_height_mm")
            if current is not None:
                adjustment = make_adjustment(
                    setting="front_ride_height",
                    label="Front ride height",
                    original=float(current),
                    value=float(current) + 1.0,
                    unit="mm",
                    confidence=min(0.78, float(policy["setup_confidence_cap"])),
                    evidence="고속 최저 차고 임계값 도달이 복수 유효 랩에서 반복됨",
                    tradeoff="공기저항과 전륜 다운포스 효율이 소폭 악화될 수 있음",
                    validation="Front ride height를 1 mm만 올리고 동일 구간 splitter p0.1 높이와 랩타임을 A/B 비교",
                )
                if adjustment.get("validator", {}).get("passed"):
                    adjustment.update(
                        {
                            "tier": "CONFIRMED_TEST",
                            "source": "CURRENT_SESSION_REPEATABILITY",
                            "experimental": False,
                        }
                    )
                    confirmed.append(adjustment)
                else:
                    rejected.append(
                        {"setting": adjustment["setting"], "reason": "; ".join(adjustment["validator"]["messages"])}
                    )
        elif repeated_patterns:
            rear_arb = setup.get("rear_arb_blades")
            if rear_arb is not None:
                adjustment = make_adjustment(
                    setting="rear_arb_blades",
                    label="Rear ARB blades",
                    original=float(rear_arb),
                    value=float(rear_arb) + 1.0,
                    unit="blade",
                    confidence=min(0.68, float(policy["setup_confidence_cap"])),
                    evidence="복수 유효 랩에서 조향량 증가 대비 요 응답 저하가 같은 코너에 반복됨",
                    tradeoff="저속 탈출 트랙션과 연석 순응성이 감소할 수 있음",
                    validation="Rear ARB를 1단계만 올리고 동일 코너의 조향효율·최저속도·스로틀 복귀를 A/B 비교",
                )
                if adjustment.get("validator", {}).get("passed"):
                    adjustment.update(
                        {
                            "tier": "CONFIRMED_TEST",
                            "source": "CURRENT_SESSION_REPEATABILITY",
                            "experimental": False,
                        }
                    )
                    confirmed.append(adjustment)

        # A single occurrence is no longer discarded. It remains explicitly
        # experimental until an A/B run, external consensus, or later memory confirms it.
        repeated_ids = {str(item.get("corner_id") or item.get("corner")) for item in repeated_patterns}
        partial_patterns = [
            item
            for item in candidate_patterns
            if str(item.get("corner_id") or item.get("corner")) not in repeated_ids
        ]
        if partial_patterns and not any(item.get("setting") == "rear_arb_blades" for item in confirmed):
            pattern = partial_patterns[0]
            rear_arb = setup.get("rear_arb_blades")
            mean_loss = float(pattern.get("mean_loss_s") or 0.0)
            ratio = float(pattern.get("occurrence_ratio") or 0.0)
            if rear_arb is not None and mean_loss >= 0.03:
                confidence = 0.38 + 0.16 * min(1.0, ratio) + min(0.06, mean_loss * 0.35)
                confidence = min(float(policy["setup_confidence_cap"]), confidence, 0.60)
                adjustment = make_adjustment(
                    setting="rear_arb_blades",
                    label="Rear ARB blades",
                    original=float(rear_arb),
                    value=float(rear_arb) + 1.0,
                    unit="blade",
                    confidence=confidence,
                    evidence=(
                        f"{pattern.get('corner')}에서 조향 대비 요 응답 저하가 {pattern.get('occurrences', 1)}/"
                        f"{max(1, repetition.get('compared_laps', 1))} 비교 랩에 나타남; 평균 손실 {mean_loss:.3f}초"
                    ),
                    tradeoff="회전성이 좋아질 수 있으나 저속 탈출 트랙션과 연석 순응성이 감소할 수 있음",
                    validation="확정 변경이 아니라 실험 후보입니다. Rear ARB를 1단계만 올려 같은 코너와 전체 랩을 A/B 비교",
                )
                if adjustment.get("validator", {}).get("passed"):
                    adjustment.update(
                        {
                            "tier": "EXPERIMENTAL",
                            "source": "CURRENT_SESSION_PARTIAL_PATTERN",
                            "experimental": True,
                            "corner_id": pattern.get("corner_id"),
                        }
                    )
                    experimental.append(adjustment)

        # One-lap bottoming evidence is also retained as an experiment rather
        # than silently disappearing, but it is never promoted to confirmed.
        if (
            len(bottoming_laps) == 1
            and not any(item.get("setting") == "front_ride_height" for item in confirmed)
        ):
            current = setup.get("front_ride_height_mm")
            if current is not None:
                adjustment = make_adjustment(
                    setting="front_ride_height",
                    label="Front ride height",
                    original=float(current),
                    value=float(current) + 1.0,
                    unit="mm",
                    confidence=min(0.48, float(policy["setup_confidence_cap"])),
                    evidence=f"유효 랩 {bottoming_laps[0]}에서 고속 최저 차고 임계값 도달이 검출됨",
                    tradeoff="공기저항과 전륜 다운포스 효율이 소폭 악화될 수 있음",
                    validation="단일 랩 근거이므로 1 mm A/B 테스트 후 바텀아웃 이벤트와 랩타임이 함께 개선되는지 확인",
                )
                if adjustment.get("validator", {}).get("passed"):
                    adjustment.update(
                        {
                            "tier": "EXPERIMENTAL",
                            "source": "CURRENT_SESSION_SINGLE_OBJECTIVE_EVENT",
                            "experimental": True,
                        }
                    )
                    experimental.append(adjustment)

        external_candidates = self._external_setup_candidates(
            session_mode,
            external_reference or {},
            setup,
        )
        experimental.extend(external_candidates)

        exploration = setup_exploration_candidate(
            request, setup, feedback or {}, len(self.valid_laps),
            occupied_settings={str(item.get("setting")) for item in confirmed + experimental},
        )
        if exploration.get("candidate"):
            experimental.append(exploration["candidate"])

        # Confirmed current-session evidence has priority. For experimental
        # candidates, keep only the strongest direction for each setting.
        confirmed_settings = {str(item.get("setting")) for item in confirmed}
        experimental = [item for item in experimental if str(item.get("setting")) not in confirmed_settings]
        experimental.sort(
            key=lambda item: (
                1 if str(item.get("source") or "").startswith("GARAGE61_") else 0,
                float(item.get("priority_score") or 0.0),
                float(item.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        selected_experimental: list[dict[str, Any]] = []
        used_settings: set[str] = set()
        for candidate in experimental:
            setting = str(candidate.get("setting") or "")
            if setting in used_settings:
                rejected.append(
                    {
                        "setting": setting,
                        "reason": "같은 셋업 항목에 상충하거나 중복되는 실험 후보가 있어 더 강한 근거 하나만 유지했습니다.",
                    }
                )
                continue
            selected_experimental.append(candidate)
            used_settings.add(setting)
            if len(selected_experimental) >= 4:
                break

        all_candidates = (confirmed + selected_experimental)[:4]
        decision = "TEST" if all_candidates else "HOLD"
        if confirmed:
            decision_basis = "CONFIRMED_REPEATABILITY"
        elif any(str(item.get("source") or "").startswith("GARAGE61_") for item in selected_experimental):
            decision_basis = "SETUP_ASSIST_EXTERNAL_CONSENSUS"
        elif any(item.get("tier") == "EXPLORATORY" for item in selected_experimental):
            decision_basis = "DRIVER_FEEDBACK_EXPLORATION"
        elif selected_experimental:
            decision_basis = "EXPERIMENTAL_CURRENT_SESSION"
        else:
            decision_basis = "NO_ACTIONABLE_SETUP_EVIDENCE"

        if session_mode == SessionMode.QUALIFYING:
            validation_plan = [
                "한 번의 A/B 테스트에서는 한 항목만 변경합니다.",
                "기준 셋업과 후보 셋업을 각각 별도 run으로 기록하며 각 run은 아웃랩 1바퀴 + 계측랩 최대 2바퀴입니다.",
                "최고 랩과 문제 코너를 자연 편차와 함께 비교합니다. 차이가 편차 안이면 미확정이며, 악화가 확인되면 원복합니다.",
            ]
        elif session_mode == SessionMode.ENDURANCE:
            validation_plan = [
                "한 번의 A/B 테스트에서는 한 항목만 변경합니다.",
                "각 셋업으로 유효 랩 5개 이상을 기록하고 중앙값·편차·페이스 열화율을 비교합니다.",
                "중앙 페이스·편차·열화율과 운전 용이성을 함께 비교합니다. 효과가 자연 편차 안이면 추가 표본을 확보하고, 악화가 확인되면 원복합니다.",
            ]
        else:
            validation_plan = [
                "한 번의 A/B 테스트에서는 한 항목만 변경합니다.",
                "각 셋업으로 유효 랩 3개를 기록하고 중앙값과 편차를 비교합니다.",
                "중앙 페이스·편차·문제 코너와 운전 용이성을 함께 비교합니다. 효과가 자연 편차 안이면 실패 대신 미확정이며, 악화가 확인되면 원복합니다.",
            ]

        if selected_experimental:
            validation_plan.insert(
                0,
                "실험 후보는 한 단계 변경 후 확인할 가설입니다. 속도·일관성 개선을 확정하려면 A/B 결과를 확인합니다.",
            )
        if exploration.get("candidate"):
            validation_plan.insert(0, "탐색은 유효랩 1개부터 가능합니다. 한 항목만 바꾸고 평소 페이스로 1~2랩 달려 체감과 문제 코너를 확인하세요. 일정한 랩타임은 요구하지 않습니다.")
            validation_plan.insert(1, "체감이 좋아져도 속도 개선은 아직 미확정입니다. 명백히 더 불안하거나 운전하기 어려우면 원복하고, 효과 확인은 별도 A/B 표본으로 진행하세요.")
        if not all_candidates:
            if label == "INSUFFICIENT_DATA":
                rejected.append(
                    {
                        "setting": "all",
                        "reason": "현재 랩 표본과 외부 기준 컨센서스에서 검증 가능한 수치 후보를 만들 근거가 부족합니다.",
                    }
                )
            elif label == "DRIVER_LIMITED":
                rejected.append(
                    {
                        "setting": "all",
                        "reason": "드라이빙 손실은 존재하지만 현재 셋업 값으로 직접 검증 가능한 차량 응답 패턴이 아직 없습니다.",
                    }
                )
            else:
                rejected.append(
                    {"setting": "all", "reason": "등록된 셋업 단계 규칙으로 검증 가능한 변경값을 만들 수 없습니다."}
                )

        return {
            "decision": decision,
            "decision_basis": decision_basis,
            "setup_mode": request.setup_mode if request else "EVIDENCE",
            "exploration": {key: value for key, value in exploration.items() if key != "candidate"},
            "session_mode": session_mode.value,
            "classification_gate_removed": True,
            "adjustments": confirmed,
            "confirmed_adjustments": confirmed,
            "experimental_candidates": selected_experimental,
            "test_candidates": all_candidates,
            "rejected": rejected,
            "validation_plan": validation_plan,
            "performance_diagnosis": (external_reference or {}).get("performance_diagnostics", {}),
            "optimization_policy": {
                "objective": "MINIMIZE_GAP_TO_CONDITION_MATCHED_FAST_REFERENCE",
                "one_change_at_a_time": True,
                "numeric_values_only_when_increment_registered": True,
                "direction_only_when_step_unknown": True,
                "max_visible_candidates": 4,
                "driver_limited_does_not_block_setup_assist": True,
            },
            "external_reference_policy": {
                "minimum_faster_references": EXTERNAL_REFERENCE_MIN_DECISION,
                "single_reference_can_trigger_setup": False,
                "decision_ready": bool((external_reference or {}).get("decision_ready")),
            },
        }


    def _dynamic_control_opportunities(
        self,
        corners: list[dict[str, Any]],
        target_grid: Optional[dict[str, np.ndarray[Any, Any]]],
    ) -> list[dict[str, Any]]:
        opportunities: list[dict[str, Any]] = []
        if target_grid is None:
            return opportunities

        controls = self._control_summary()
        distance = target_grid["distance_pct"]
        throttle = target_grid.get("Throttle")
        brake = target_grid.get("Brake")
        steering = target_grid.get("SteeringWheelAngle")
        if throttle is None or brake is None or steering is None:
            return opportunities

        point_distance_m = self.track_length_m / max(1, len(distance) - 1)
        lookback_points = max(20, int(len(distance) * 0.16))

        for corner in sorted(corners, key=lambda item: item.get("distance_m") or 0):
            target = corner.get("target") or {}
            brake_start = target.get("brake_start_pct")
            peak_brake = target.get("peak_brake")
            if (not corner.get("measurement_usable", True)
                    or brake_start is None or peak_brake is None
                    or not math.isfinite(brake_start) or not math.isfinite(peak_brake)
                    or peak_brake < 0.45):
                continue

            brake_index = nearest_index(distance, float(brake_start))
            circular_indices = np.mod(
                np.arange(brake_index - lookback_points, brake_index, dtype=int),
                len(distance),
            )
            straight_mask = (
                (throttle[circular_indices] >= 0.97)
                & (brake[circular_indices] <= 0.02)
                & (np.abs(steering[circular_indices]) <= 0.08)
            )
            groups = contiguous_groups(np.flatnonzero(straight_mask), max_gap=1, min_len=3)
            if not groups:
                continue
            longest = max((end - start + 1 for start, end in groups), default=0)
            straight_distance_m = longest * point_distance_m

            # In-car control changes are suggested only when the driver has a
            # genuinely stable full-throttle approach before the braking event.
            if straight_distance_m < 150.0:
                continue

            opportunities.append(
                {
                    "corner": corner["label"],
                    "distance_m": corner["distance_m"],
                    "approach_full_throttle_m": round(straight_distance_m, 1),
                    "current_brake_bias_pct": controls.get("brake_bias_pct"),
                    "recommendation": "HOLD",
                    "reason": "긴 풀스로틀 접근과 강한 제동이 있어 인카 조정 시험에는 적합하지만, 현재 파일에는 브레이크 바이어스 A/B 비교가 없습니다.",
                }
            )
            if len(opportunities) >= 2:
                break
        return opportunities

    # ------------------------------------------------------------------
    # Visualization and briefing
    # ------------------------------------------------------------------

    def _visualizations(
        self,
        target: dict[str, np.ndarray[Any, Any]],
        reference: Optional[dict[str, np.ndarray[Any, Any]]],
        corners: list[dict[str, Any]],
    ) -> dict[str, Any]:
        n = len(target["distance_pct"])
        delta = target["time_s"] - reference["time_s"] if reference else np.zeros(n)
        local_loss = np.gradient(delta, target["distance_pct"], edge_order=1) / n
        local_loss = np.clip(local_loss, -0.02, 0.02)
        indices = visualization_indices(target, reference, VISUAL_GRID_POINTS)

        track_path: list[dict[str, Any]] = []
        lat = target.get("Lat")
        lon = target.get("Lon")
        if lat is not None and lon is not None:
            valid_geo = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 1e-7) & (np.abs(lon) > 1e-7)
            for index in indices:
                if not valid_geo[index]:
                    track_path.append({"lat": None, "lon": None, "distance_pct": round(float(target["distance_pct"][index]), 7)})
                    continue
                track_path.append(
                    {
                        "lat": round(float(lat[index]), 8),
                        "lon": round(float(lon[index]), 8),
                        "distance_pct": round(float(target["distance_pct"][index]), 5),
                        "speed_kph": round(float(target.get("speed_kph", np.zeros(n))[index]), 1),
                        "brake": round(float(target.get("Brake", np.zeros(n))[index]), 3),
                        "throttle": round(float(target.get("Throttle", np.zeros(n))[index]), 3),
                        "delta_s": round(float(delta[index]), 3),
                        "local_loss_s": round(float(local_loss[index]), 5),
                    }
                )

        trace: list[dict[str, Any]] = []
        for index in indices:
            trace.append(
                {
                    "distance_pct": round(float(target["distance_pct"][index]), 5),
                    "target_speed_kph": round_or_none(float(target["speed_kph"][index]), 1) if "speed_kph" in target else None,
                    "reference_speed_kph": round_or_none(float(reference["speed_kph"][index]), 1)
                    if reference and "speed_kph" in reference
                    else None,
                    **comparison_input_channels(target, reference, index),
                    "delta_s": round_or_none(float(delta[index]), 4) if reference else None,
                }
            )

        markers = [
            {
                "id": corner["id"],
                "label": corner["label"],
                "entry_pct": corner["entry_pct"],
                "apex_pct": corner["apex_pct"],
                "exit_pct": corner["exit_pct"],
                "delta_s": corner["delta_s"],
            }
            for corner in sorted(corners, key=lambda item: item["apex_pct"])
        ]
        return {"track_path": track_path, "reference_track_path": gps_path(reference),
                "reference_line_source": {"type": "LOCAL_ACTUAL_LAP", "chart_basis": "ACTUAL_LAP"} if reference is not None else None,
                "trace": trace, "markers": markers,
                "track_length_m": self.track_length_m,
                "analysis_spacing_m": round_or_none(self.track_length_m / max(1, n - 1), 2),
                "visualization_method": "EVENTS_EXTREMA_AND_DISTANCE",
                "segment_note": "자동 검출 구간이며 공식 트랙 턴 번호가 아닙니다."}

    def _deterministic_briefing(
        self,
        request: AnalyzeRequest,
        data_quality: dict[str, Any],
        comparison: Optional[dict[str, Any]],
        corners: list[dict[str, Any]],
        vehicle: dict[str, Any],
        classification: dict[str, Any],
        setup_recommendation: dict[str, Any],
        coaching_priorities: list[dict[str, Any]],
        dynamic_controls: list[dict[str, Any]],
        external_reference: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        summary_parts: list[str] = []
        if comparison and comparison.get("reference_lap") is not None:
            delta = comparison.get("total_delta_s")
            direction = "빨랐습니다" if delta is not None and delta < 0 else "느렸습니다"
            summary_parts.append(
                f"랩 {comparison['target_lap']}은 랩 {comparison['reference_lap']}보다 {abs(delta):.3f}초 {direction}."
            )
        external_aggregate = (external_reference or {}).get("aggregate", {}) or {}
        external_gap = external_aggregate.get("gap_to_reference_median_s")
        if external_gap is not None:
            summary_parts.append(
                f"선택한 Garage61 기준 중앙값 대비 {float(external_gap):+.3f}초 차이입니다."
            )
        if coaching_priorities:
            first = coaching_priorities[0]
            if first.get("loss_s") is not None:
                summary_parts.append(
                    f"최우선 개선 지점은 {first['corner']}의 {first['loss_s']:.3f}초 손실입니다."
                )
        summary_parts.append(
            "셋업 판정은 "
            + (
                "보류되었습니다."
                if setup_recommendation["decision"] == "HOLD"
                else "A/B 테스트 후보가 생성되었습니다."
            )
        )

        sections = [
            {
                "title": "데이터 신뢰성",
                "items": data_quality["messages"],
            },
            {
                "title": "세션 목표",
                "items": session_objective_items(request, vehicle),
            },
            {
                "title": "랩타임 비교",
                "items": comparison_items(comparison),
            },
            {
                "title": "Garage61 외부 기준",
                "items": (
                    [
                        f"선택 {external_reference.get('selected_reference_count', 0)}개 / 셋업 판단용 빠른 기준 {external_reference.get('decision_reference_count', 0)}개",
                        f"외부 기준 중앙 랩타임 {external_aggregate.get('reference_median_time') or '--'} / 대상 대비 {external_gap:+.3f}초"
                        if external_gap is not None else "외부 기준과의 랩타임 차이를 계산할 수 없습니다.",
                        *(external_reference.get("warnings", [])[:2]),
                    ]
                    if external_reference and external_reference.get("status") == "READY"
                    else ["호환 Garage61 CSV 기준을 선택하지 않았습니다."]
                ),
            },
            {
                "title": "가장 큰 시간 손실",
                "items": [
                    f"{priority['corner']} ({priority.get('distance_m')} m): {priority['action']}"
                    for priority in coaching_priorities
                ]
                or ["비교 가능한 국소 손실이 없습니다."],
            },
            {
                "title": "드라이빙과 셋업 원인 분리",
                "items": classification["reasons"],
            },
            {
                "title": "타이어",
                "items": [vehicle.get("tires", {}).get("reason", "타이어 데이터가 없습니다.")],
            },
            {
                "title": "차고와 에어로",
                "items": ride_aero_items(vehicle),
            },
            {
                "title": "인카 조정 포인트",
                "items": [
                    f"{item['corner']}: {item['reason']}" for item in dynamic_controls
                ]
                or ["검증 가능한 긴 제동 구간이 부족합니다."],
            },
            {
                "title": "셋업 결정",
                "items": setup_decision_items(setup_recommendation),
            },
            {
                "title": "다음 주행 검증 계획",
                "items": setup_recommendation.get("validation_plan", []),
            },
        ]
        return {
            "source": "DETERMINISTIC",
            "summary": " ".join(summary_parts),
            "sections": sections,
            "session_mode": request.session_mode.value,
            "pit_rule": request.pit_rule.value,
        }


# ---------------------------------------------------------------------------
# Setup metadata/rules helpers
# ---------------------------------------------------------------------------


def gps_path(grid: Optional[dict[str, Any]], start: int = 0, end: Optional[int] = None) -> list[dict[str, Any]]:
    """Observed GPS in the common geographic frame; nulls keep gaps unconnected.

    Keep the analysis grid for geometry: pedal-based decimation can cut a corner.
    Never subtract an offset or independently aggregate latitude and longitude.
    """
    if grid is None or "Lat" not in grid or "Lon" not in grid:
        return []
    stop = len(grid["distance_pct"]) if end is None else end + 1
    path = []
    for index in range(start, stop):
        lat, lon = float(grid["Lat"][index]), float(grid["Lon"][index])
        valid = math.isfinite(lat) and math.isfinite(lon) and abs(lat) > 1e-7 and abs(lon) > 1e-7
        path.append({"lat": round(lat, 8) if valid else None,
                     "lon": round(lon, 8) if valid else None,
                     "distance_pct": round(float(grid["distance_pct"][index]), 7)})
    return path


def comparison_detail_payload(telemetry: ParsedTelemetry, payload: ComparisonDetailRequest) -> dict[str, Any]:
    if payload.end_pct <= payload.start_pct:
        raise HTTPException(status_code=422, detail="end_pct는 start_pct보다 커야 합니다.")
    if payload.reference_mode == "SINGLE" and len(payload.external_reference_files) != 1:
        raise HTTPException(status_code=422, detail="SINGLE 비교에는 기준 CSV 한 개를 선택하세요.")
    analyzer = TelemetryAnalyzer(telemetry)
    target, local_reference, _ = analyzer._select_laps(payload.target_lap, payload.reference_lap)
    if target is None:
        raise HTTPException(status_code=422, detail="비교할 유효 대상 랩이 없습니다.")
    target_grid = analyzer._coaching_grid(target) if not target.valid else analyzer._lap_grid(target)
    reference_grid = analyzer._lap_grid(local_reference) if local_reference and payload.reference_mode == "LOCAL" else None
    distributions = {}
    support = {}
    reference_label = f"내 기준 랩 {local_reference.lap_number}" if reference_grid is not None else "비교 기준 없음"
    line_grid = reference_grid
    line_source = {"type": "LOCAL_ACTUAL_LAP", "label": reference_label, "chart_basis": "ACTUAL_LAP"} if reference_grid is not None else None
    if payload.reference_mode in {"AGGREGATE", "SINGLE"}:
        external = analyzer._external_reference_analysis(target, payload.external_reference_files)
        if external.get("status") != "READY":
            raise HTTPException(status_code=422, detail="사용 가능한 Garage61 기준 랩이 없습니다.")
        reference_grid = analyzer._external_detail_grid
        distributions = analyzer._external_detail_distributions
        support = analyzer._external_detail_support
        reference_label = analyzer._external_reference_label
        line_grid = analyzer._external_line_grid
        line_source = analyzer._external_line_source
    distance = target_grid["distance_pct"]
    start = int(np.searchsorted(distance, payload.start_pct, side="left"))
    end = min(len(distance) - 1, int(np.searchsorted(distance, payload.end_pct, side="right")))
    indices = visualization_indices(target_grid, reference_grid, payload.points, start, end)
    rows = []
    for index in indices:
        row = {
            "distance_pct": round(float(distance[index]), 7),
            "target_speed_kph": round_or_none(float(target_grid["speed_kph"][index]), 2) if "speed_kph" in target_grid else None,
            "reference_speed_kph": round_or_none(float(reference_grid["speed_kph"][index]), 2) if reference_grid is not None and "speed_kph" in reference_grid else None,
            "delta_s": round_or_none(float(target_grid["time_s"][index] - reference_grid["time_s"][index]), 4) if reference_grid is not None and target.valid else None,
            **comparison_input_channels(target_grid, reference_grid, index),
        }
        for name, key, scale in (("speed", "speed_kph", 1), ("brake", "Brake", 1), ("throttle", "Throttle", 1), ("steering_deg", "SteeringWheelAngle", 180 / math.pi)):
            for quantile in ("p25", "p75"):
                value = distributions.get(key, {}).get(quantile)
                row[f"reference_{name}_{quantile}{'_kph' if name == 'speed' else ''}"] = round_or_none(float(value[index]) * scale, 3) if value is not None else None
            if key in support:
                row[f"reference_{name}_support_count"] = int(support[key][index])
        rows.append(row)
    path = gps_path(target_grid, start, end)
    reference_path = gps_path(line_grid, start, end)
    target_mask = target_grid.get("_coverage", np.isfinite(target_grid["time_s"]))[start:end + 1]
    reference_mask = reference_grid.get("_coverage", np.isfinite(reference_grid["time_s"]))[start:end + 1] if reference_grid is not None else None
    quality = {
        "analysis_spacing_m": round(analyzer.track_length_m / max(1, len(distance) - 1), 2),
        "target_coverage_ratio": round(float(np.mean(target_mask)), 4),
        "reference_coverage_ratio": round(float(np.mean(reference_mask)), 4) if reference_mask is not None else None,
        "max_interpolation_gap_m": 25,
        "max_interpolation_gap_s": .5,
        "extrapolation": False,
        "trace_points": len(rows),
        "coaching_scope": "VALID_LAP" if target.valid else "CLEAN_SEGMENTS_ONLY",
        "pace_eligible": bool(target.valid),
    }
    return json_safe({"trace": rows, "track_path": path, "reference_track_path": reference_path,
                      "reference_line_source": line_source, "quality": quality, "reference_label": reference_label,
                      "start_pct": payload.start_pct, "end_pct": payload.end_pct,
                      "target_lap": target.lap_number, "reference_lap": local_reference.lap_number if reference_grid is not None and payload.reference_mode == "LOCAL" else None})


def nested_get(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_setup_summary(setup: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "front_arb_blades": parse_number(nested_get(setup, "Chassis", "FrontBrakesLights", "ArbBlades")),
        "rear_arb_blades": parse_number(nested_get(setup, "Chassis", "Rear", "ArbBlades")),
        "rear_wing_angle_deg": parse_number(nested_get(setup, "Chassis", "Rear", "RearWingAngle")),
        "front_toe_mm": parse_number(nested_get(setup, "Chassis", "FrontBrakesLights", "TotalToeIn")),
        "front_ride_height_mm": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftFront", "RideHeight")),
                parse_number(nested_get(setup, "Chassis", "RightFront", "RideHeight")),
            ]
        ),
        "rear_ride_height_mm": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftRear", "RideHeight")),
                parse_number(nested_get(setup, "Chassis", "RightRear", "RideHeight")),
            ]
        ),
        "front_spring_n_per_mm": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftFront", "SpringRate")),
                parse_number(nested_get(setup, "Chassis", "RightFront", "SpringRate")),
            ]
        ),
        "rear_spring_n_per_mm": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftRear", "SpringRate")),
                parse_number(nested_get(setup, "Chassis", "RightRear", "SpringRate")),
            ]
        ),
        "front_camber_deg": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftFront", "Camber")),
                parse_number(nested_get(setup, "Chassis", "RightFront", "Camber")),
            ]
        ),
        "rear_camber_deg": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftRear", "Camber")),
                parse_number(nested_get(setup, "Chassis", "RightRear", "Camber")),
            ]
        ),
        "rear_toe_mm": mean_finite(
            [
                parse_number(nested_get(setup, "Chassis", "LeftRear", "ToeIn")),
                parse_number(nested_get(setup, "Chassis", "RightRear", "ToeIn")),
            ]
        ),
        "diff_preload_nm": parse_number(nested_get(setup, "Chassis", "Differential", "DiffPreload")),
        "brake_bias_pct": parse_number(nested_get(setup, "Chassis", "InCarAdjustments", "BrakePressureBias")),
        "abs_setting": parse_number(nested_get(setup, "Chassis", "InCarAdjustments", "AbsSetting")),
        "tc_setting": parse_number(nested_get(setup, "Chassis", "InCarAdjustments", "TcSetting")),
        "starting_pressure_kpa": parse_number(nested_get(setup, "TiresAero", "LeftFront", "StartingPressure")),
        "front_rh_at_speed_mm": parse_number(nested_get(setup, "TiresAero", "AeroBalanceCalc", "FrontRhAtSpeed")),
        "rear_rh_at_speed_mm": parse_number(nested_get(setup, "TiresAero", "AeroBalanceCalc", "RearRhAtSpeed")),
        "front_downforce_pct": parse_number(nested_get(setup, "TiresAero", "AeroBalanceCalc", "FrontDownforce")),
    }
    return supplement_setup_summary(setup, summary, parse_number)


def make_adjustment(
    setting: str,
    label: str,
    original: float,
    value: float,
    unit: str,
    confidence: float,
    evidence: str,
    tradeoff: str,
    validation: str,
) -> dict[str, Any]:
    rule = ADJUSTMENT_RULES.get(setting)
    validator_messages: list[str] = []
    passed = math.isfinite(original) and math.isfinite(value) and abs(value - original) > 1e-9
    if not passed:
        validator_messages.append("original/value must be finite and different")
    if rule and passed:
        step = rule.get("step")
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if step:
            increments = (value - original) / step
            if abs(increments - round(increments)) > 1e-6:
                passed = False
                validator_messages.append(f"change must use {step:g} {unit} increments")
        if minimum is not None and value < minimum - 1e-9:
            passed = False
            validator_messages.append(f"value is below configured minimum {minimum:g} {unit}")
        if maximum is not None and value > maximum + 1e-9:
            passed = False
            validator_messages.append(f"value is above configured maximum {maximum:g} {unit}")
        if passed:
            validator_messages.append("current value and configured increment validated")
            if maximum is None:
                validator_messages.append("Garage maximum is not encoded in this IBT; verify before applying")
    elif not rule:
        passed = False
        validator_messages.append("no adjustment rule is registered for this setting")

    return {
        "setting": setting,
        "label": label,
        "original_value": round(original, 2),
        "value": round(value, 2),
        "unit": unit,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "tradeoff": tradeoff,
        "validation": validation,
        "status": "TEST_ONLY" if passed else "REJECTED_BY_VALIDATOR",
        "validator": {
            "passed": passed,
            "messages": validator_messages,
            "legal_range_fully_verified": bool(rule and rule.get("maximum") is not None),
        },
    }


def make_directional_adjustment(
    setting: str,
    label: str,
    original: Optional[float],
    direction: str,
    step_text: str,
    unit: str,
    confidence: float,
    evidence: str,
    tradeoff: str,
    validation: str,
) -> dict[str, Any]:
    """Create a useful setup hypothesis without fabricating a numeric click size.

    Some iRacing Garage controls differ by car and the IBT only contains the
    current value, not the legal menu values.  In that case the optimizer still
    says which control and direction to test, but explicitly labels the next
    value as one Garage click rather than inventing a number.
    """
    original_value = round(float(original), 2) if original is not None and math.isfinite(float(original)) else None
    return {
        "setting": setting,
        "label": label,
        "original_value": original_value,
        "value": None,
        "direction": direction,
        "step_text": step_text,
        "unit": unit,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "tradeoff": tradeoff,
        "validation": validation,
        "status": "TEST_ONLY_DIRECTIONAL",
        "validator": {
            "passed": True,
            "messages": [
                "per-car Garage increment is not encoded in the IBT",
                "apply exactly one Garage click in the recommended direction",
            ],
            "legal_range_fully_verified": False,
            "numeric_value_verified": False,
        },
    }


def setup_exploration_candidate(
    request: Optional[AnalyzeRequest],
    setup: dict[str, Any],
    feedback: dict[str, Any],
    valid_laps: int,
    occupied_settings: set[str],
) -> dict[str, Any]:
    """Offer one reversible comfort hypothesis without claiming a setup diagnosis."""
    result: dict[str, Any] = {"status": "DISABLED", "minimum_valid_laps": 1,
        "requires_consistent_laptimes": False, "effect_confirmed": False}
    if request is None or request.setup_mode != "EXPLORE":
        return result
    result["status"] = "NEEDS_FEEDBACK"
    result["reason"] = "구간·코너 속도·증상을 선택하고 분석을 갱신하면 탐색 후보를 확인할 수 있습니다."
    if not all((request.feedback_phase, request.feedback_speed, request.feedback_symptom)):
        return result
    clean_observations = [item for item in feedback.get("observations") or [] if item.get("scope") == "COACHING_ONLY"]
    if valid_laps < 1 and not clean_observations:
        return {**result, "status": "NEEDS_VALID_LAP", "reason": "완주한 유효랩 1개가 필요합니다. 일정한 여러 랩은 요구하지 않습니다."}
    observations = feedback.get("observations") or []
    if feedback.get("status") != "MATCHED" or not observations:
        return {**result, "status": "NO_MATCHING_CORNER", "reason": "선택한 속도 구간의 코너 측정이 없습니다. 속도 구간을 바꾸거나 해당 코너가 포함된 랩을 선택하세요."}

    if valid_laps < 1:
        observations = clean_observations
        result.update({"minimum_valid_laps": 0, "source_scope": "COACHING_ONLY", "official_laps_required_for_effect_confirmation": True})

    # These are conventional one-step balance hypotheses, not inferred causes.
    # Read actual current values and retain the existing registered step checks.
    symptom = request.feedback_symptom
    if symptom == "UNDERSTEER":
        options = [("front_arb_blades", -1, "Front ARB blades", "blade"),
                   ("rear_arb_blades", 1, "Rear ARB blades", "blade")]
        if request.feedback_phase == "ENTRY":
            options.insert(0, ("diff_preload_nm", -1, "Differential preload", "Nm"))
        category, effect = "ROTATION_DEFICIT", "코너에서 앞이 덜 밀리는지 체감 확인"
        tradeoff = "앞 ARB를 낮추면 롤이 늘 수 있고, 뒤 ARB를 올리면 탈출 트랙션·뒤 안정성이 감소할 수 있습니다."
    elif symptom == "OVERSTEER":
        options = [("rear_arb_blades", -1, "Rear ARB blades", "blade")]
        if request.feedback_speed == "HIGH" and request.feedback_phase != "ENTRY":
            options.insert(0, ("rear_wing_angle_deg", 1, "Rear wing angle", "deg"))
        category, effect = "REAR_STABILITY", "뒤가 덜 흐르고 조향 수정이 편해지는지 체감 확인"
        tradeoff = "뒤 ARB를 낮추면 회전성이 줄 수 있고, 뒤 윙을 올리면 공기저항이 늘 수 있습니다."
    elif symptom == "KERB":
        options = [("rear_arb_blades", -1, "Rear ARB blades", "blade"),
                   ("front_arb_blades", -1, "Front ARB blades", "blade")]
        category, effect = "KERB_COMPLIANCE", "같은 연석에서 차가 덜 튀고 다루기 편해지는지 확인"
        tradeoff = "롤과 밸런스가 바뀔 수 있습니다. 연석을 더 크게 타거나 진입 속도를 동시에 바꾸지 마세요."
    else:
        return {**result, "status": "NEEDS_BRAKING_DETAIL", "reason": "제동 불안만으로 바이어스 방향을 정할 수 없습니다. 앞바퀴 잠김인지 뒤가 흐르는지 추가 피드백에 적고, 우선 제동·ABS 측정을 확인하세요."}

    observed = observations[0]
    phase_label = {"ENTRY": "진입", "MID_CORNER": "코너 중간", "EXIT": "탈출"}[request.feedback_phase]
    symptom_label = {"UNDERSTEER": "언더스티어", "OVERSTEER": "오버스티어", "KERB": "연석 불안"}[symptom]
    conflicts = []
    for setting, direction, label, unit in options:
        current = setup.get(setting)
        if current is None or not math.isfinite(float(current)):
            continue
        if setting in occupied_settings:
            conflicts.append(setting)
            continue
        rule = ADJUSTMENT_RULES.get(setting)
        numeric_click = setting in {"front_arb_blades", "rear_arb_blades"} and rule is not None
        adjustment_args = dict(
            setting=setting, label=label, original=float(current),
            unit=unit, confidence=0.35,
            evidence=f"드라이버가 {phase_label} {symptom_label}를 선택했습니다. {observed.get('label') or observed.get('corner_id') or '해당 코너'} 측정 구간을 대상으로 체감을 시험합니다. 측정값이 증상이나 셋업 원인을 입증한 것은 아닙니다.",
            tradeoff=tradeoff,
            validation="현재 값을 기록하고 한 항목만 변경해 평소 페이스로 1~2랩 달리세요. 체감·조향 수정·문제 코너를 비교하고 더 불안하면 원복하세요. 속도 효과는 별도 A/B로 확인합니다.",
        )
        if numeric_click:
            candidate = make_adjustment(**adjustment_args, value=float(current) + direction * float(rule["step"]))
        else:
            candidate = make_directional_adjustment(**adjustment_args, direction="INCREASE" if direction > 0 else "DECREASE",
                step_text="Garage에서 한 클릭만 " + ("올리기" if direction > 0 else "내리기"))
        if setting == "diff_preload_nm":
            candidate["tradeoff"] = "프리로드를 낮추면 진입 회전이 쉬워질 수 있지만 제동 안정성과 탈출 트랙션이 나빠질 수 있습니다. 파워·코스트 잠금각까지 함께 바꾸지 마세요."
        if not candidate["validator"]["passed"]:
            continue
        candidate.update({"tier": "EXPLORATORY", "source": "DRIVER_FEEDBACK_EXPLORATION",
            "experimental": True, "category": category, "expected_effect": effect,
            "corner_id": observed.get("corner_id"), "evidence_strength": "LOW",
            "effect_confirmed": False, "confidence_cap": 0.40,
            "phase": request.feedback_phase, "speed_band": request.feedback_speed,
            "source_scope": observed.get("scope") or "VALID_LAP", "vehicle_capability_source": "OBSERVED_EMBEDDED_SETUP",
            "hypothesis_only": True, "conflicting_existing_settings": conflicts,
            "priority_score": 0.35, "minimum_observation_laps": 0 if valid_laps < 1 else 1,
            "minimum_clean_corners": 1 if valid_laps < 1 else 0})
        return {**result, "status": "AVAILABLE", "reason": "일정한 랩타임 없이도 선택한 증상의 작은 변경을 시험할 수 있습니다.", "candidate": candidate}
    if conflicts:
        return {**result, "status": "EXISTING_CANDIDATE", "conflicting_settings": conflicts,
                "reason": "지원되는 설정에 기존 시험 후보가 있습니다. 기존 후보를 먼저 시험하세요."}
    return {**result, "status": "NO_SUPPORTED_SETTING", "reason": "현재 셋업 값과 등록된 변경 단계로 만들 수 있는 후보가 없습니다. 셋업 원본과 Garage 조정 범위를 확인하세요."}


def cap_exploratory_setup_candidates(recommendation: dict[str, Any]) -> dict[str, Any]:
    # Personal history may rank a hypothesis, but cannot turn reported feel into
    # a verified effect or a repeated current-session observation.
    updated = dict(recommendation)
    for key in ("experimental_candidates", "test_candidates"):
        candidates = []
        for raw in recommendation.get(key) or []:
            item = dict(raw)
            if item.get("source") == "DRIVER_FEEDBACK_EXPLORATION":
                item.update({"confidence": min(0.40, float(item.get("confidence") or 0)),
                    "priority_score": min(0.40, float(item.get("priority_score") or 0)),
                    "tier": "EXPLORATORY", "experimental": True, "effect_confirmed": False})
            candidates.append(item)
        updated[key] = candidates
    return updated


def materialize_directional_numeric_targets(
    recommendation: dict[str, Any],
    memory: Optional[LongTermMemoryStore],
    car_name: Optional[str],
) -> dict[str, Any]:
    """Fill directional candidates with a numeric target only when locally learned safely.

    Directional candidates remain useful without a numeric value.  This pass avoids
    blank UI targets while allowing the app to learn a per-car increment from the
    user's own stored setup history when the observed setup grid is sufficiently
    consistent.
    """
    if not recommendation:
        return recommendation

    updated = dict(recommendation)
    candidates: list[dict[str, Any]] = []
    for item in recommendation.get("test_candidates", []) or []:
        candidate = dict(item)
        value = candidate.get("value")
        if value not in (None, ""):
            candidate.setdefault("target_display", None)
            candidates.append(candidate)
            continue

        direction = str(candidate.get("direction") or "").upper()
        original = candidate.get("original_value")
        inferred = None
        if memory is not None and direction in {"INCREASE", "DECREASE", "TOWARD_ZERO"}:
            try:
                inferred = memory.infer_setup_increment(car_name, str(candidate.get("setting") or ""))
            except Exception as exc:
                logger.debug("Could not infer setup increment for %s: %s", candidate.get("setting"), exc)

        if inferred and original is not None:
            try:
                original_number = float(original)
                step = float(inferred["step"])
                if direction == "INCREASE":
                    target = original_number + step
                elif direction == "DECREASE":
                    target = original_number - step
                else:
                    if original_number > 0:
                        target = max(0.0, original_number - step)
                    elif original_number < 0:
                        target = min(0.0, original_number + step)
                    else:
                        target = original_number
                if math.isfinite(target) and abs(target - original_number) > 1e-9:
                    candidate["value"] = round(target, 4)
                    candidate["numeric_target_source"] = inferred["source"]
                    candidate["numeric_target_estimated"] = True
                    candidate["inferred_increment"] = inferred
                    validator = dict(candidate.get("validator") or {})
                    messages = list(validator.get("messages") or [])
                    messages = [message for message in messages if "increment is not encoded" not in str(message)]
                    messages.insert(
                        0,
                        f"Garage increment inferred from local {car_name or 'car'} setup history: {step:g} {candidate.get('unit') or ''}".strip(),
                    )
                    messages.append("numeric target is inferred, not an iRacing legal-range guarantee; verify one Garage click before applying")
                    validator.update(
                        {
                            "passed": True,
                            "messages": messages,
                            "numeric_value_verified": False,
                            "numeric_value_inferred": True,
                            "legal_range_fully_verified": False,
                        }
                    )
                    candidate["validator"] = validator
            except (TypeError, ValueError, KeyError):
                pass

        if candidate.get("value") in (None, ""):
            step_text = str(candidate.get("step_text") or "").strip()
            if not step_text:
                direction_labels = {
                    "INCREASE": "Garage 1 click increase",
                    "DECREASE": "Garage 1 click decrease",
                    "TOWARD_ZERO": "Garage 1 click toward 0",
                }
                candidate["step_text"] = direction_labels.get(direction, "Garage 1 click change")
            candidate["numeric_target_estimated"] = False

        candidates.append(candidate)

    updated["test_candidates"] = candidates
    return updated


def pct_distance(target_pct: Optional[float], reference_pct: Optional[float], length_m: float) -> Optional[float]:
    if target_pct is None or reference_pct is None:
        return None
    return (target_pct - reference_pct) * length_m


def finite_values(values: Iterable[Optional[float]]) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def mean_finite(values: Iterable[Optional[float]]) -> Optional[float]:
    finite = finite_values(values)
    return sum(finite) / len(finite) if finite else None


def min_finite(values: Iterable[Optional[float]]) -> float:
    finite = finite_values(values)
    return min(finite) if finite else math.nan


def max_finite(values: Iterable[Optional[float]]) -> float:
    finite = finite_values(values)
    return max(finite) if finite else math.nan


def comparison_items(comparison: Optional[dict[str, Any]]) -> list[str]:
    if not comparison:
        return ["비교 가능한 유효 랩이 없습니다."]
    items = [
        f"대상 랩 {comparison['target_lap']}: {comparison.get('target_time')}",
    ]
    if comparison.get("reference_lap") is not None:
        delta = comparison.get("total_delta_s")
        items.append(
            f"기준 랩 {comparison['reference_lap']}: {comparison.get('reference_time')} (대상-기준 {delta:+.3f}초)"
        )
    if comparison.get("reproducible_optimal_time"):
        items.append(
            f"재현 가능한 개인 최적: {comparison['reproducible_optimal_time']} / 회수 가능 {comparison.get('recoverable_time_s', 0):.3f}초"
        )
    if comparison.get("session_best_time") and not comparison.get("session_best_trace_available"):
        items.append(
            f"세션 메타데이터 베스트 {comparison['session_best_time']}의 원본 트레이스는 이 IBT 구간에 없어 코너별 역산에서 제외했습니다."
        )
    return items


def ride_aero_items(vehicle: dict[str, Any]) -> list[str]:
    ride = vehicle.get("ride_height", {})
    aero = vehicle.get("aero", {})
    items: list[str] = []
    if ride.get("status") == "VALID":
        items.append(
            "바텀아웃: " + ("검출" if ride.get("bottoming_detected") else "미검출")
            + f" ({ride.get('bottoming_events', 0)} events)"
        )
    if aero.get("status") == "VALID":
        items.append(
            f"200 km/h 이상 중앙값 에어로 피치: {aero.get('aero_pitch_median_mm')} mm, 고속 최소 스플리터 높이: {aero.get('minimum_splitter_height_high_speed_mm')} mm"
        )
    return items or ["차고 또는 에어로 채널이 부족합니다."]


def setup_decision_items(recommendation: dict[str, Any]) -> list[str]:
    decision = recommendation.get("decision")
    items = [f"결정: {decision} / 근거 계층: {recommendation.get('decision_basis', '--')}"]
    confirmed = recommendation.get("confirmed_adjustments", recommendation.get("adjustments", [])) or []
    experimental = recommendation.get("experimental_candidates", []) or []

    def target_text(adjustment: dict[str, Any]) -> str:
        if adjustment.get("value") is not None:
            return f"{adjustment.get('value')} {adjustment.get('unit', '')}".strip()
        return str(adjustment.get("step_text") or adjustment.get("direction") or "Garage 1 click")

    for adjustment in confirmed:
        items.append(
            f"확정 A/B 후보 · {adjustment['label']}: {adjustment.get('original_value')} → {target_text(adjustment)}"
        )
    for adjustment in experimental:
        category = adjustment.get("category") or "SETUP_ASSIST"
        items.append(
            f"실험 A/B 후보 · [{category}] {adjustment['label']}: {adjustment.get('original_value')} → {target_text(adjustment)} / 근거점수 {round(float(adjustment.get('confidence') or 0) * 100)}%"
        )
    if not confirmed and not experimental:
        items.append("현재 근거에서 검증 가능한 셋업 변경 후보가 없습니다.")
    return items


_SETUP_MEMORY_KEY_HINTS: dict[str, tuple[str, ...]] = {
    key: tuple(meta.get("hints") or ()) for key, meta in PERSONALIZATION_SETTING_META.items()
}


def speed_class_label(value: str) -> str:
    return {"LOW": "저속", "MEDIUM": "중속", "HIGH": "고속"}.get(str(value or "").upper(), str(value or "일반"))


def coach_issue_label(issue: str) -> str:
    mapping = {
        "ENTRY": "코너 진입", "BRAKING": "제동", "MID_CORNER": "미드코너 속도",
        "EXIT": "탈출 가속", "BALANCE_SUSPECT": "회전/밸런스", "DRIVER_VARIANCE": "입력 재현성",
        "GAIN": "강점 구간", "CONSISTENT": "재현 구간",
    }
    return mapping.get(str(issue or "").upper(), str(issue or "일반"))


def setup_profile_match(profile: dict[str, Any], setting: str, direction: int) -> Optional[dict[str, Any]]:
    for item in profile.get("settings", []) or []:
        if str(item.get("setting") or "") == setting and int(item.get("direction") or 0) == int(direction):
            return item
    return None


def reinforce_setup_with_sensitivity_profile(
    recommendation: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    if not profile or not profile.get("settings"):
        recommendation["personal_sensitivity"] = {"used": False, "stage": profile.get("learning_stage") if profile else "COLD_START"}
        return recommendation
    updated = dict(recommendation)
    candidates = [dict(item) for item in (updated.get("experimental_candidates") or [])]
    matches = 0
    for candidate in candidates:
        setting = str(candidate.get("setting") or "")
        direction = _candidate_direction_sign(candidate)
        history = setup_profile_match(profile, setting, direction)
        if not history or int(history.get("decisive_count") or 0) < 1:
            continue
        matches += 1
        keep_rate = history.get("keep_rate")
        decisive = int(history.get("decisive_count") or 0)
        weight = min(0.12, 0.035 * decisive)
        adjustment = 0.0
        if keep_rate is not None:
            adjustment = weight * (float(keep_rate) - 0.5) * 2.0
        candidate["confidence"] = round(max(0.20, min(0.95, float(candidate.get("confidence") or 0.0) + adjustment)), 2)
        candidate["priority_score"] = round(max(0.0, min(1.0, float(candidate.get("priority_score") or candidate.get("confidence") or 0.0) + adjustment)), 3)
        candidate["personal_sensitivity_support"] = {
            "decisive_count": decisive,
            "keep_count": history.get("keep_count"),
            "revert_count": history.get("revert_count"),
            "keep_rate": keep_rate,
            "weighted_median_delta_s": history.get("weighted_median_delta_s"),
            "tier": history.get("tier"),
        }
    candidates.sort(key=lambda item: (float(item.get("priority_score") or 0.0), float(item.get("confidence") or 0.0)), reverse=True)
    updated["experimental_candidates"] = candidates
    confirmed = [dict(item) for item in (updated.get("confirmed_adjustments") or updated.get("adjustments") or [])]
    updated["test_candidates"] = (confirmed + candidates)[:4]
    updated["personal_sensitivity"] = {
        "used": matches > 0,
        "matched_candidates": matches,
        "stage": profile.get("learning_stage"),
        "event_count": profile.get("event_count", 0),
        "decisive_event_count": profile.get("decisive_event_count", 0),
    }
    return updated


def _candidate_direction_sign(candidate: dict[str, Any]) -> int:
    original = candidate.get("original_value")
    value = candidate.get("value")
    try:
        if original is not None and value is not None:
            delta = float(value) - float(original)
            return 1 if delta > 0 else -1 if delta < 0 else 0
    except (TypeError, ValueError):
        pass
    direction = str(candidate.get("direction") or "").upper()
    if direction == "INCREASE":
        return 1
    if direction == "DECREASE":
        return -1
    if direction == "TOWARD_ZERO":
        try:
            numeric = float(original)
            return -1 if numeric > 0 else 1 if numeric < 0 else 0
        except (TypeError, ValueError):
            return 0
    return 0


def _outcome_direction_for_setting(outcome: dict[str, Any], setting: str) -> int:
    hints = _SETUP_MEMORY_KEY_HINTS.get(setting, ())
    if not hints:
        return 0
    signs: list[int] = []
    for raw_key, change in (outcome.get("setup_diff") or {}).items():
        key = str(raw_key or "").casefold()
        if not any(hint in key for hint in hints):
            continue
        if not isinstance(change, dict):
            continue
        left = parse_number(change.get("baseline"))
        right = parse_number(change.get("candidate"))
        if left is None or right is None or abs(right - left) <= 1e-9:
            continue
        signs.append(1 if right > left else -1)
    if not signs:
        return 0
    total = sum(signs)
    return 1 if total > 0 else -1 if total < 0 else signs[0]


def reinforce_setup_recommendation_with_memory(
    recommendation: dict[str, Any],
    similar_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-rank setup hypotheses with the user's own completed A/B outcomes.

    External references answer *where performance is missing*. Personal KEEP / REVERT
    history answers *which control direction has actually worked for this user*.
    Memory therefore adjusts evidence/priority but never creates a numeric change by
    itself and never overrides the one-change-at-a-time safety policy.
    """
    candidates = list(recommendation.get("experimental_candidates") or [])
    if not candidates or not similar_sessions:
        recommendation["memory_reinforcement"] = {
            "used": False,
            "matching_outcomes": 0,
        }
        return recommendation

    total_matches = 0
    for candidate in candidates:
        setting = str(candidate.get("setting") or "")
        direction = _candidate_direction_sign(candidate)
        if not setting or direction == 0:
            continue
        support = 0.0
        revert = 0.0
        matching = 0
        notes: list[str] = []
        seen_outcomes: set[str] = set()
        for session in similar_sessions[:5]:
            similarity = float(session.get("similarity_score") or 0.5)
            for outcome in session.get("experiment_outcomes", [])[:3]:
                result_id = str(outcome.get("result_id") or "")
                # The same A/B is attached to both baseline and candidate sessions.
                if not result_id or result_id in seen_outcomes:
                    continue
                seen_outcomes.add(result_id)
                raw_diff = outcome.get("setup_diff") or {}
                full_diff = raw_diff.get("full_setup", raw_diff)
                eligibility = setup_learning_eligibility(outcome.get("result") or {}, full_diff if isinstance(full_diff, dict) else {})
                if not eligibility["eligible"]:
                    continue
                historical_direction = _outcome_direction_for_setting({**outcome, "setup_diff": full_diff}, setting)
                if historical_direction == 0:
                    continue
                verdict = str(outcome.get("verdict") or "").upper()
                same_direction = historical_direction == direction
                if verdict == "KEEP" and same_direction:
                    support += 0.09 * similarity
                    matching += 1
                    notes.append("유사 세션 KEEP 이력")
                elif verdict == "REVERT" and same_direction:
                    revert += 0.13 * similarity
                    matching += 1
                    notes.append("유사 세션 REVERT 이력")
                elif verdict == "KEEP" and not same_direction:
                    revert += 0.04 * similarity
                    matching += 1
                    notes.append("반대 방향 KEEP 이력")
        if matching:
            total_matches += matching
            old_conf = float(candidate.get("confidence") or 0.0)
            old_priority = float(candidate.get("priority_score") or old_conf)
            candidate["confidence"] = round(max(0.20, min(0.95, old_conf + support - revert)), 2)
            candidate["priority_score"] = round(max(0.0, min(1.0, old_priority + support - revert)), 3)
            candidate["memory_support"] = {
                "matching_outcomes": matching,
                "positive_weight": round(support, 3),
                "negative_weight": round(revert, 3),
                "notes": list(dict.fromkeys(notes))[:4],
            }

    candidates.sort(
        key=lambda item: (
            float(item.get("priority_score") or 0.0),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    recommendation["experimental_candidates"] = candidates
    confirmed = list(recommendation.get("confirmed_adjustments") or recommendation.get("adjustments") or [])
    recommendation["test_candidates"] = (confirmed + candidates)[:4]
    recommendation["memory_reinforcement"] = {
        "used": total_matches > 0,
        "matching_outcomes": total_matches,
        "policy": "KEEP boosts same-direction candidates; REVERT suppresses them; memory never invents a Garage value",
    }
    return recommendation


def session_objective_items(request: AnalyzeRequest, vehicle: dict[str, Any]) -> list[str]:
    if request.session_mode == SessionMode.QUALIFYING:
        return [
            "정상 run 구조는 아웃랩 1바퀴 + 계측랩 최대 2바퀴이며, 유효 계측랩 3개를 요구하지 않습니다.",
            "계측랩 1은 기준 기록 확보, 계측랩 2는 가장 큰 손실 코너 1~2곳의 개선에 사용합니다.",
            "두 계측랩만으로 셋업을 확정하지는 않지만, 내부 약한 패턴이나 Garage61의 2개 이상 빠른 기준 컨센서스가 있으면 1-step A/B 실험 후보는 생성할 수 있습니다.",
        ]
    if request.session_mode == SessionMode.ENDURANCE:
        tires = vehicle.get("tires", {})
        items = [
            "평가 우선순위는 랩타임 중앙값, 랩당 페이스 열화율, 타이어 온도·압력 추세입니다.",
        ]
        if request.pit_rule == PitRule.CONCURRENT:
            items.append("동시 교체 규정으로 표시되었지만, 실제 교체 시간과 마모 비용은 IBT에 없어 자동 결론을 생성하지 않습니다.")
        elif request.pit_rule == PitRule.SEQUENTIAL:
            items.append("순차 교체 규정으로 표시되었지만, 실제 추가 피트 시간은 IBT에 없어 외부 규정값 입력 전 전략을 확정하지 않습니다.")
        if tires.get("surface_temp_slope_c_per_lap") is not None:
            items.append(
                f"표면 온도 추세: {tires['surface_temp_slope_c_per_lap']:+.2f} °C/lap, "
                f"열간압력 추세: {(tires.get('hot_pressure_slope_kpa_per_lap') or 0):+.2f} kPa/lap."
            )
        return items
    return [
        "3-Lap 베이스는 유효 랩 3개의 중앙값과 입력 편차를 기준으로 셋업 문제를 분리합니다.",
        "반복 패턴은 확정 근거로 사용하고, 1회 패턴은 폐기하지 않고 낮은 신뢰도의 A/B 실험 후보로 분리합니다.",
    ]


def _metric_delta(target: dict[str, Any], reference: dict[str, Any], key: str) -> Optional[float]:
    left = target.get(key)
    right = reference.get(key)
    if left is None or right is None:
        return None
    try:
        return float(left) - float(right)
    except (TypeError, ValueError):
        return None


def detailed_corner_diagnosis(corner: dict[str, Any], track_length_m: float, source: str) -> dict[str, Any]:
    target = corner.get("target") or {}
    reference = corner.get("reference") or {}
    delta_s = float(corner.get("delta_s") or 0.0)
    full_throttle_m = pct_distance(target.get("full_throttle_pct"), reference.get("full_throttle_pct"), track_length_m)
    brake_start_m = pct_distance(target.get("brake_start_pct"), reference.get("brake_start_pct"), track_length_m)
    brake_release_m = pct_distance(target.get("brake_release_pct"), reference.get("brake_release_pct"), track_length_m)
    coast_delta = _metric_delta(target, reference, "coast_time_s")
    abs_delta = _metric_delta(target, reference, "abs_duty_pct")
    min_speed_delta = _metric_delta(target, reference, "min_speed_kph")
    exit_speed_delta = _metric_delta(target, reference, "exit_speed_kph")
    steer_delta = _metric_delta(target, reference, "avg_abs_steer_deg")
    efficiency_delta = _metric_delta(target, reference, "steering_efficiency")
    correction_delta = _metric_delta(target, reference, "steering_corrections")
    relift_delta = _metric_delta(target, reference, "throttle_relifts")

    cause = "DRIVER_VARIANCE"
    action = "완벽히 같은 랩을 강제하지 말고, 가장 큰 차이가 나는 입력 순서 하나만 의식적으로 조정해 반복 방향이 있는지 확인하십시오."
    success = ["같은 방향의 구간 손실이 2랩 이상에서 감소", "조향 수정 횟수 증가 없음"]
    do_not_change = "재현성 자체를 선행 과제로 만들지 않습니다. 셋업 후보가 있으면 driveability A/B도 병행할 수 있습니다."

    if full_throttle_m is not None and full_throttle_m >= 5.0:
        cause = "THROTTLE_RETURN_LATE"
        action = f"조향을 먼저 풀고 풀스로틀 지점을 약 {min(12.0, max(3.0, full_throttle_m * 0.65)):.0f} m 앞당기십시오."
        success = ["풀스로틀 복귀 3 m 이상 앞당김", "탈출 속도 +1.5 km/h 이상", "스로틀 재리프트 증가 없음"]
    elif brake_release_m is not None and brake_release_m >= 4.0 and (min_speed_delta or 0.0) <= -1.0:
        cause = "BRAKE_RELEASE_LATE"
        action = f"브레이킹 시작점은 유지하고 릴리즈 시작을 약 {min(10.0, max(3.0, brake_release_m * 0.65)):.0f} m 앞당겨 트레일 브레이크를 부드럽게 줄이십시오."
        success = ["최저속도 +1.5 km/h 이상", "평균 조향량 증가 없음", "ABS 듀티 증가 ≤5%p"]
    elif abs_delta is not None and abs_delta >= 8.0 and (min_speed_delta or 0.0) < 0.0:
        cause = "OVER_BRAKING_ABS"
        action = "브레이킹 시작점은 유지하고 초기 피크 압력을 약간 낮춘 뒤 릴리즈를 연속적으로 만드십시오."
        success = ["ABS 듀티 5%p 이상 감소", "최저속도 저하 없음", "제동 거리 악화 없음"]
    elif coast_delta is not None and coast_delta >= 0.04 and (min_speed_delta or 0.0) <= -1.0:
        cause = "EXCESS_COASTING"
        action = "브레이크를 완전히 놓고 기다리는 시간을 줄이고 트레일 브레이크 또는 초기 스로틀로 하중을 이어가십시오."
        success = ["코스팅 0.04초 이상 감소", "최저속도 +1.5 km/h 이상", "조향 수정 증가 없음"]
    elif brake_start_m is not None and brake_start_m <= -6.0 and (min_speed_delta or 0.0) <= -1.0:
        cause = "BRAKE_POINT_EARLY"
        action = f"현재 피크 제동은 유지하면서 제동 시작점을 약 {min(8.0, max(3.0, abs(brake_start_m) * 0.55)):.0f} m 늦춰보십시오."
        success = ["제동 시작 3 m 이상 지연", "최저속도 +1 km/h 이상", "ABS 증가 ≤5%p"]
    elif (relift_delta or 0.0) >= 1.0 or (correction_delta or 0.0) >= 1.0:
        cause = "EXIT_INSTABILITY"
        action = "스로틀을 한 번에 열기보다 조향 해제와 함께 연속적으로 올려 재리프트와 카운터 조향을 줄이십시오."
        success = ["스로틀 재리프트 1회 이상 감소", "조향 수정 감소", "탈출 속도 저하 없음"]
    elif (steer_delta or 0.0) >= 3.0 and (efficiency_delta is None or efficiency_delta < 0.0):
        cause = "ROTATION_DEFICIT"
        action = "추가 조향으로 버티지 말고 브레이크 릴리즈와 진입 속도를 조정해 같은 회전을 더 적은 조향으로 만드십시오."
        success = ["평균 조향량 2° 이상 감소", "최저속도 유지 또는 증가", "steering efficiency 개선"]
    elif str(corner.get("issue_type") or "") == "MID_CORNER":
        cause = "EXCESS_COASTING"
        action = "미드코너에서 입력 공백을 줄이고 최저속도를 유지하는 데 집중하십시오."
        success = ["최저속도 +1 km/h 이상", "코스팅 감소", "탈출 스로틀 지연 없음"]

    consensus = corner.get("reference_consensus") or {}
    repeatability = float(consensus.get("loss_agreement_ratio") or 0.0) if source.startswith("GARAGE61") else 0.55
    data_confidence = 0.55
    if source.startswith("GARAGE61"):
        count = int(consensus.get("reference_count") or 0)
        condition = float(consensus.get("condition_similarity_median") or 0.0)
        data_confidence = min(0.95, 0.45 + min(0.20, count * 0.04) + 0.25 * condition)
    loss_score = min(1.0, max(0.0, delta_s) / 0.22)
    meta = COACH_ROOT_CAUSE_META.get(cause, COACH_ROOT_CAUSE_META["DRIVER_VARIANCE"])
    priority_score = 0.42 * loss_score + 0.23 * repeatability + 0.20 * data_confidence + 0.15 * float(meta.get("difficulty") or 0.75)
    # Generic variance is uncertainty, not a prerequisite skill gate. It may still be
    # shown, but should not crowd out a concrete telemetry or setup-assist hypothesis.
    if cause == "DRIVER_VARIANCE":
        priority_score = min(0.62, priority_score * 0.68)
    recovery_low = max(0.01, delta_s * (0.25 + 0.15 * repeatability))
    recovery_high = max(recovery_low, delta_s * (0.55 + 0.15 * repeatability))
    evidence = list(corner.get("evidence") or [])[:5]
    if brake_release_m is not None and abs(brake_release_m) >= 3:
        evidence.append(f"브레이크 릴리즈 위치 {brake_release_m:+.0f} m")
    if exit_speed_delta is not None and abs(exit_speed_delta) >= 1.0:
        evidence.append(f"탈출 속도 {exit_speed_delta:+.1f} km/h")
    return {
        "corner_id": corner.get("id"), "corner": corner.get("label"),
        "distance_m": corner.get("distance_m"), "speed_class": corner.get("speed_class"),
        "loss_s": round_or_none(delta_s, 3), "source": source,
        "root_cause": cause, "root_cause_label": meta.get("label"), "phase": meta.get("phase"),
        "action": action, "do_not_change": do_not_change, "success_criteria": success,
        "repeatability": round(repeatability, 3), "data_confidence": round(data_confidence, 3),
        "priority_score": round(priority_score, 3),
        "estimated_recovery_s": [round(recovery_low, 3), round(recovery_high, 3)],
        "evidence": list(dict.fromkeys(evidence))[:7],
    }


def _fingerprint_support_for_cause(fingerprint: dict[str, Any], diagnosis: dict[str, Any]) -> float:
    issue_map = {
        "BRAKE_RELEASE_LATE": {"ENTRY", "BRAKING", "DRIVER_VARIANCE"},
        "BRAKE_POINT_EARLY": {"ENTRY", "BRAKING"},
        "OVER_BRAKING_ABS": {"BRAKING"},
        "EXCESS_COASTING": {"MID_CORNER", "DRIVER_VARIANCE"},
        "THROTTLE_RETURN_LATE": {"EXIT"},
        "EXIT_INSTABILITY": {"EXIT", "BALANCE_SUSPECT"},
        "ROTATION_DEFICIT": {"BALANCE_SUSPECT", "ENTRY"},
        "DRIVER_VARIANCE": {"DRIVER_VARIANCE"},
    }
    wanted = issue_map.get(str(diagnosis.get("root_cause") or ""), set())
    speed = str(diagnosis.get("speed_class") or "")
    best = 0.0
    for weakness in fingerprint.get("weaknesses", []) or []:
        if str(weakness.get("issue_type") or "") not in wanted:
            continue
        if speed and str(weakness.get("speed_class") or "") != speed:
            continue
        best = max(best, float(weakness.get("session_support_ratio") or 0.0))
    return best


def current_session_variability(result: dict[str, Any]) -> dict[str, Any]:
    """Estimate the driver's natural lap-to-lap variation without treating it as a gate.

    Two laps are enough to produce a LOW-confidence estimate. Three or more progressively
    increase confidence. The value is used as an uncertainty band for coaching/A-B
    interpretation, never as a requirement that the driver must first become perfectly
    repeatable.
    """
    times = [
        float(lap.get("lap_time_s"))
        for lap in (result.get("laps") or [])
        if str(lap.get("status") or "") == "VALID" and lap.get("lap_time_s") is not None
    ]
    if not times:
        return {
            "valid_laps": 0, "median_s": None, "stdev_s": None, "mad_s": None,
            "robust_sigma_s": None, "natural_variability_s": None,
            "confidence": 0.0, "confidence_label": "NO_DATA", "consistency_score": None,
        }
    arr = np.asarray(times, dtype=float)
    median = float(np.median(arr))
    stdev = float(np.std(arr)) if len(arr) > 1 else 0.0
    mad = float(np.median(np.abs(arr - median))) if len(arr) > 1 else 0.0
    robust_sigma = float(1.4826 * mad)
    # With two laps, MAD can understate natural variation, so blend with standard deviation.
    if len(arr) == 2:
        robust_sigma = max(robust_sigma, stdev)
    natural = max(0.03, robust_sigma)
    confidence = min(1.0, max(0.20, (len(arr) - 1) / 4.0))
    label = "HIGH" if len(arr) >= 5 else "MEDIUM" if len(arr) >= 3 else "LOW"
    # 0.45% of lap time is a practical scale for a visually understandable consistency score.
    reference_band = max(0.25, median * 0.0045)
    consistency_score = max(0.0, min(1.0, 1.0 - natural / reference_band))
    return {
        "valid_laps": len(arr),
        "median_s": round_or_none(median, 4),
        "stdev_s": round_or_none(stdev, 4),
        "mad_s": round_or_none(mad, 4),
        "robust_sigma_s": round_or_none(robust_sigma, 4),
        "natural_variability_s": round_or_none(natural, 4),
        "confidence": round(confidence, 3),
        "confidence_label": label,
        "consistency_score": round(consistency_score, 3),
        "interpretation": "재현성은 분석 통과조건이 아니라 현재 자연 편차/신뢰도 보정값으로 사용합니다.",
    }


def build_closed_loop_coach(
    result: dict[str, Any],
    sensitivity_profile: dict[str, Any],
    fingerprint: dict[str, Any],
) -> dict[str, Any]:
    external = result.get("external_reference") or {}
    external_losses = external.get("top_losses") or []
    personal_losses = result.get("performance", {}).get("top_losses", []) or []
    track_length = float(result.get("session", {}).get("track_length_m") or 0.0)
    source = "GARAGE61_REFERENCE_CONSENSUS" if external_losses else "PERSONAL_LAP_COMPARISON"
    candidates = external_losses if external_losses else personal_losses
    diagnoses: list[dict[str, Any]] = []
    for corner in candidates[:8]:
        if float(corner.get("delta_s") or 0.0) < 0.02:
            continue
        item = detailed_corner_diagnosis(corner, track_length, source)
        personal_support = _fingerprint_support_for_cause(fingerprint, item)
        if personal_support > 0:
            item["priority_score"] = round(min(1.0, float(item["priority_score"]) + 0.08 * personal_support), 3)
            item["personal_history_support"] = round(personal_support, 3)
            item["evidence"].append(f"개인 이력에서 같은 유형 반복 {round(personal_support * 100)}% 수준")
        diagnoses.append(item)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in diagnoses:
        groups[(str(item.get("root_cause")), str(item.get("speed_class")))].append(item)
    focuses: list[dict[str, Any]] = []
    for (cause, speed), items in groups.items():
        items.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
        lead = dict(items[0])
        total_loss = sum(float(item.get("loss_s") or 0.0) for item in items)
        repeat = float(np.mean([float(item.get("repeatability") or 0.0) for item in items]))
        lead["affected_corners"] = [item.get("corner") for item in items]
        lead["corner_count"] = len(items)
        lead["total_observed_loss_s"] = round(total_loss, 3)
        lead["repeatability"] = round(repeat, 3)
        score = min(1.0, max(float(item.get("priority_score") or 0.0) for item in items) + min(0.12, 0.04 * (len(items) - 1)))
        if cause == "DRIVER_VARIANCE":
            score = min(0.64, score)
        lead["priority_score"] = round(score, 3)
        lead["estimated_recovery_s"] = [
            round(sum(float((item.get("estimated_recovery_s") or [0, 0])[0]) for item in items), 3),
            round(sum(float((item.get("estimated_recovery_s") or [0, 0])[1]) for item in items), 3),
        ]
        focuses.append(lead)
    focuses.sort(key=lambda item: (float(item.get("priority_score") or 0.0), float(item.get("total_observed_loss_s") or 0.0)), reverse=True)

    setup_candidates = [dict(item) for item in (result.get("setup_recommendation", {}).get("test_candidates") or [])]
    setup_focus = setup_candidates[0] if setup_candidates else None
    exploratory_focus = next((item for item in setup_candidates if item.get("tier") == "EXPLORATORY"), None)
    if exploratory_focus and not any(item.get("tier") != "EXPLORATORY" and float(item.get("confidence") or 0) >= 0.50 for item in setup_candidates):
        setup_focus = exploratory_focus
    setup_score = float((setup_focus or {}).get("priority_score") or (setup_focus or {}).get("confidence") or 0.0)
    driving_focus = focuses[0] if focuses else None
    driving_score = float((driving_focus or {}).get("priority_score") or 0.0)
    variability = current_session_variability(result)

    policy = result.get("session_policy") or {}
    mode = str(policy.get("session_mode") or "BASELINE_3LAP")
    driving_required_laps = 2
    driving_recommended_laps = 2 if mode == "QUALIFYING" else 3
    ab_required_laps = 2 if mode == "QUALIFYING" else 3
    ab_recommended_laps = 2 if mode == "QUALIFYING" else 5

    setup_ready = bool(setup_focus and float(setup_focus.get("confidence") or 0.0) >= 0.50)
    generic_variance_focus = bool(driving_focus and str(driving_focus.get("root_cause") or "") == "DRIVER_VARIANCE")
    driveability_categories = {
        "EXIT_TRACTION", "ROTATION_DEFICIT", "REAR_STABILITY", "BRAKE_BALANCE",
        "DIFF_ROTATION", "HIGH_SPEED_STABILITY", "HIGH_SPEED_AERO",
    }
    setup_is_driveability = str((setup_focus or {}).get("category") or "") in driveability_categories
    choose_setup = bool(
        setup_ready
        and (
            not driving_focus
            or setup_score >= driving_score + 0.08
            or (generic_variance_focus and setup_is_driveability and setup_score >= max(0.42, driving_score - 0.12))
        )
    )

    if setup_focus and setup_focus.get("tier") == "EXPLORATORY":
        change_text = f"{setup_focus.get('label')}: {setup_focus.get('original_value')} → {setup_focus.get('value')} {setup_focus.get('unit') or ''}"
        next_run = {
            "mode": "SETUP_EXPLORATION", "mode_label": "셋업 탐색 런", "title": change_text,
            "primary_instruction": "한 항목만 바꾸고 평소 페이스로 1~2랩 달리며 운전하기 편해지는지 확인하세요. 일정한 랩타임은 요구하지 않습니다. 속도 개선은 아직 미확정입니다.",
            "change": setup_focus, "required_valid_laps": 0 if setup_focus.get("source_scope") == "COACHING_ONLY" else 1, "recommended_valid_laps": 2,
            "required_clean_corners": 1 if setup_focus.get("source_scope") == "COACHING_ONLY" else 0,
            "effect_confirmation_minimum_laps": ab_required_laps,
            "effect_confirmation_recommended_laps": ab_recommended_laps,
            "lock": ["다른 셋업·연료 조건 유지", "라인과 브레이킹 스타일을 동시에 바꾸지 않기"],
            "success_criteria": ["같은 문제 코너에서 체감과 조향 수정이 더 편해지는지 확인", "더 불안하거나 다루기 어려워지면 원복", "체감 개선과 속도 효과 확정은 별도로 기록"],
            "after_run": f"새 IBT에서 코너 입력을 확인하세요. A/B 효과 판정은 각 셋업 유효랩 {ab_required_laps}개부터이며 자연 편차도 함께 계산합니다. 표본 부족은 실패가 아니라 미확정입니다.",
        }
    elif choose_setup:
        target = setup_focus.get("value")
        target_text = (
            f"{target} {setup_focus.get('unit') or ''}".strip()
            if target not in (None, "") else str(setup_focus.get("step_text") or "Garage 1 click")
        )
        original = setup_focus.get("original_value")
        change_text = f"{setup_focus.get('label')}: {original} → {target_text}"
        next_run = {
            "mode": "SETUP_AB", "mode_label": "셋업 A/B 런", "title": change_text,
            "primary_instruction": "추천된 셋업 항목 하나만 변경하고 평소 운전 방식으로 달리십시오. 완벽한 재현성은 요구하지 않으며 A/B가 자연 편차를 함께 계산합니다.",
            "change": setup_focus,
            "required_valid_laps": ab_required_laps,
            "recommended_valid_laps": ab_recommended_laps,
            "lock": ["다른 셋업 항목 변경 금지", "의도적으로 새로운 라인/브레이킹 스타일을 동시에 실험하지 않기"],
            "success_criteria": [str(setup_focus.get("validation") or "자연 편차보다 큰 PACE/CONSISTENCY/DRIVEABILITY 변화를 확인")],
            "after_run": "새 IBT를 같은 세션 유형으로 저장한 뒤 로컬 A/B에서 A=현재 IBT, B=새 IBT를 선택합니다. 평균 페이스뿐 아니라 일관성과 운전 용이성도 함께 판정합니다.",
        }
    elif driving_focus and not generic_variance_focus:
        next_run = {
            "mode": "DRIVING_FOCUS", "mode_label": "드라이빙 집중 런",
            "title": driving_focus.get("root_cause_label"),
            "primary_instruction": driving_focus.get("action"), "change": None,
            "required_valid_laps": driving_required_laps,
            "recommended_valid_laps": driving_recommended_laps,
            "focus_corners": driving_focus.get("affected_corners") or [driving_focus.get("corner")],
            "lock": ["셋업 변경 금지", "한 번에 이 입력 패턴만 의도적으로 수정"],
            "success_criteria": driving_focus.get("success_criteria") or [],
            "after_run": "새 IBT를 다시 분석해 같은 방향의 손실이 줄었는지 확인합니다. 완벽히 동일한 랩은 요구하지 않습니다.",
        }
    elif generic_variance_focus:
        next_run = {
            "mode": "PATTERN_OBSERVATION", "mode_label": "패턴 관찰 런",
            "title": "재현성을 먼저 고치는 대신 반복 손실의 방향을 찾기",
            "primary_instruction": "완벽히 같은 랩을 만들려고 하지 말고 평소처럼 달리면서 가장 큰 손실 코너의 입력 한 가지만 의식하십시오. 반복되는 방향이 확인되면 다음 분석이 구체적 코칭 또는 셋업 Assist로 전환됩니다.",
            "change": None,
            "required_valid_laps": driving_required_laps,
            "recommended_valid_laps": driving_recommended_laps,
            "focus_corners": driving_focus.get("affected_corners") or [driving_focus.get("corner")],
            "lock": ["셋업은 일단 유지", "랩타임을 억지로 맞추지 않기"],
            "success_criteria": ["동일 코너에서 같은 방향의 손실/개선 패턴 2랩 이상 확인", "자연 편차 범위를 프로그램이 갱신"],
            "after_run": "새 IBT를 분석하면 현재 자연 편차와 반복 손실을 다시 계산합니다. 재현성 부족 자체는 실패로 처리하지 않습니다.",
        }
    else:
        next_run = {
            "mode": "DATA_COLLECTION", "mode_label": "표본 확보 런", "title": "현재 운전 패턴의 자연 편차 측정",
            "primary_instruction": "셋업을 유지하고 평소 페이스로 달리십시오. 프로그램이 자연 편차를 학습해 다음 드라이빙/셋업 효과 판정 기준으로 사용합니다.",
            "change": None,
            "required_valid_laps": driving_required_laps,
            "recommended_valid_laps": driving_recommended_laps,
            "lock": ["큰 셋업 변경 금지"],
            "success_criteria": [f"유효랩 {driving_required_laps}개 이상", "반복되는 코너 손실 방향 확인"],
            "after_run": "새 IBT를 분석해 다음 원인/셋업 후보를 계산합니다.",
        }

    sensitivity_count = int(sensitivity_profile.get("decisive_event_count") or 0)
    fingerprint_conf = float(fingerprint.get("confidence") or 0.0)
    learning_progress = min(1.0, 0.55 * min(1.0, sensitivity_count / 8.0) + 0.45 * fingerprint_conf)
    workflow = [
        {"step": 1, "key": "DRIVE", "label": "주행", "status": "DONE" if int(policy.get("timed_laps_available") or 0) >= 1 else "NEEDED", "detail": f"유효 계측랩 {policy.get('timed_laps_available', 0)}개"},
        {"step": 2, "key": "DIAGNOSE", "label": "원인·우선순위", "status": "DONE" if driving_focus or setup_focus else "LIMITED", "detail": driving_focus.get("root_cause_label") if driving_focus else "표본 기반 원인 탐색 중"},
        {"step": 3, "key": "NEXT_RUN", "label": "다음 런", "status": "READY", "detail": next_run.get("mode_label")},
        {"step": 4, "key": "AB_VERIFY", "label": "A/B 검증", "status": "READY" if next_run.get("mode") == "SETUP_AB" else "OPTIONAL", "detail": f"저장된 A/B {sensitivity_profile.get('event_count', 0)}건"},
        {"step": 5, "key": "LEARN", "label": "개인화 학습", "status": "ACTIVE" if (sensitivity_count or fingerprint.get("session_count")) else "COLD_START", "detail": f"학습도 {round(learning_progress * 100)}%"},
    ]
    return {
        "version": "CLOSED_LOOP_V2_VARIANCE_AWARE",
        "workflow": workflow,
        "diagnoses": diagnoses[:8],
        "ranked_focuses": focuses[:5],
        "top_focus": driving_focus,
        "top_setup_candidate": setup_focus,
        "next_run": next_run,
        "natural_variability": variability,
        "personalization": {
            "learning_progress": round(learning_progress, 3),
            "learning_stage": sensitivity_profile.get("learning_stage", "COLD_START"),
            "sensitivity_event_count": sensitivity_profile.get("event_count", 0),
            "decisive_sensitivity_count": sensitivity_count,
            "fingerprint_confidence": fingerprint_conf,
            "fingerprint_confidence_label": fingerprint.get("confidence_label", "LOW"),
        },
        "policy": {
            "one_change_at_a_time": True,
            "reproducibility_is_confidence_modifier_not_gate": True,
            "low_consistency_does_not_block_setup_assist": True,
            "natural_variability_used_for_ab": True,
            "driving_minimum_valid_laps": driving_required_laps,
            "driving_recommended_valid_laps": driving_recommended_laps,
            "setup_ab_minimum_valid_laps": ab_required_laps,
            "setup_ab_recommended_valid_laps": ab_recommended_laps,
            "setup_ab_requires_same_vehicle_track_layout": True,
            "personalization_strength_increases_with_repeated_decisive_ab": True,
            "inconclusive_or_confounded_tests_are_not_strong_learning": True,
        },
    }


# ---------------------------------------------------------------------------
# Explicit GCP AI features
# ---------------------------------------------------------------------------


def ai_runtime_info() -> dict[str, Any]:
    return {
        "enabled": ai_client is not None,
        "provider": "GCP Vertex AI",
        "model": GEMINI_MODEL,
        "billing_mode": "TOKEN_USAGE_ON_EXPLICIT_ACTION",
        "notice": "GCP AI 표시 버튼을 명시적으로 실행할 때만 입력/출력 토큰을 사용합니다.",
    }


def memory_runtime_info() -> dict[str, Any]:
    if memory_store is None:
        return {
            "enabled": False,
            "provider": "LOCAL_SQLITE",
            "path": str(MEMORY_DB_PATH),
            "error": memory_store_error or "SQLite memory store is unavailable",
            "raw_telemetry_stored": False,
            "storage_policy": "COMPACT_ANALYSIS_ONLY",
        }
    try:
        return memory_store.status()
    except Exception as exc:
        return {
            "enabled": False,
            "provider": "LOCAL_SQLITE",
            "path": str(MEMORY_DB_PATH),
            "error": str(exc),
            "raw_telemetry_stored": False,
            "storage_policy": "COMPACT_ANALYSIS_ONLY",
        }


def ai_usage_record(
    *,
    purpose: str,
    requested: bool,
    used: bool = False,
    response: Any = None,
    status: str = "NOT_REQUESTED",
    error: Optional[str] = None,
) -> dict[str, Any]:
    tokens: dict[str, int] = {}
    usage = getattr(response, "usage_metadata", None) if response is not None else None
    for source_name, output_name in [
        ("prompt_token_count", "input_tokens"),
        ("candidates_token_count", "output_tokens"),
        ("total_token_count", "total_tokens"),
        ("cached_content_token_count", "cached_input_tokens"),
        ("thoughts_token_count", "reasoning_tokens"),
    ]:
        value = getattr(usage, source_name, None) if usage is not None else None
        if value is not None:
            try:
                tokens[output_name] = int(value)
            except (TypeError, ValueError):
                pass
    return {
        "requested": requested,
        "used": used,
        "status": status,
        "provider": "GCP Vertex AI",
        "model": GEMINI_MODEL,
        "purpose": purpose,
        "billing": "TOKEN_BILLED_WHEN_USED",
        "tokens": tokens,
        "error": error,
    }


def structured_generation_config(schema: type[BaseModel], temperature: float = 0.1) -> Any:
    if genai_types is None:
        return None
    try:
        fields = getattr(genai_types.GenerateContentConfig, "model_fields", {})
        kwargs: dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": temperature,
        }
        if "response_schema" in fields:
            kwargs["response_schema"] = schema
        elif "response_json_schema" in fields:
            kwargs["response_json_schema"] = schema.model_json_schema()
        return genai_types.GenerateContentConfig(**kwargs)
    except Exception:
        return None


def parse_structured_response(text: str, schema: type[BaseModel]) -> BaseModel:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    if not stripped.startswith("{"):
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            stripped = match.group(0)
    return schema.model_validate_json(stripped)


def ai_memory_context(result: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    """Return a token-efficient, privacy-limited view of local history."""

    memory = result.get("memory", {}) or {}
    sessions: list[dict[str, Any]] = []
    for item in memory.get("similar_sessions", [])[:limit]:
        summary = item.get("memory_summary", {}) or {}
        sessions.append(
            {
                "session_id": item.get("session_id"),
                "recorded_at": item.get("recorded_at"),
                "session_mode": item.get("session_mode"),
                "track_config": item.get("track_config"),
                "track_temp_c": item.get("track_temp_c"),
                "setup_hash_prefix": str(item.get("setup_hash") or "")[:12],
                "setup_name": item.get("setup_name"),
                "best_lap_s": item.get("best_lap_s"),
                "valid_lap_count": item.get("valid_lap_count"),
                "classification": item.get("classification"),
                "setup_decision": item.get("setup_decision"),
                "similarity_score": item.get("similarity_score"),
                "similarity_reasons": item.get("similarity_reasons", []),
                "top_issues": summary.get("top_issues", [])[:3],
                "out_lap_aggregate": summary.get("out_lap_aggregate", {}),
                "user_feedback": item.get("user_feedback"),
                "experiment_outcomes": [
                    {
                        "verdict": outcome.get("verdict"),
                        "reason": outcome.get("reason"),
                        "median_delta_s": outcome.get("median_delta_s"),
                        "best_delta_s": outcome.get("best_delta_s"),
                        "setup_diff": outcome.get("setup_diff", {}),
                    }
                    for outcome in item.get("experiment_outcomes", [])[:2]
                ],
            }
        )
    return {
        "source": "LOCAL_SQLITE_LONG_TERM_MEMORY",
        "available": bool(sessions),
        "similar_session_count": len(sessions),
        "current_out_lap_aggregate": (
            result.get("memory", {}).get("current_out_lap", {}).get("aggregate", {})
        ),
        "similar_sessions": sessions,
        "raw_telemetry_included": False,
    }


def ai_external_reference_context(result: dict[str, Any]) -> dict[str, Any]:
    """Return an anonymized, compact external-reference summary for GCP.

    Driver names, lap IDs, filenames and raw Garage61 traces stay local.
    """
    external = result.get("external_reference", {}) or {}
    return {
        "status": external.get("status"),
        "selected_reference_count": external.get("selected_reference_count", 0),
        "decision_reference_count": external.get("decision_reference_count", 0),
        "unique_driver_count": external.get("unique_driver_count", 0),
        "decision_ready": external.get("decision_ready", False),
        "aggregation_method": external.get("aggregation_method"),
        "aggregate": external.get("aggregate", {}),
        "top_losses": [
            {
                "corner_id": item.get("id"),
                "speed_class": item.get("speed_class"),
                "issue_type": item.get("issue_type"),
                "delta_s": item.get("delta_s"),
                "reference_consensus": item.get("reference_consensus", {}),
                "target": item.get("target", {}),
                "reference": item.get("reference", {}),
            }
            for item in external.get("top_losses", [])[:3]
        ],
        "performance_diagnostics": {
            "straight_line": ((external.get("performance_diagnostics") or {}).get("straight_line") or {}),
        },
        "raw_external_telemetry_included": False,
        "identifiers_included": False,
    }


async def add_ai_briefing(result: dict[str, Any], request: AnalyzeRequest) -> dict[str, Any]:
    result["ai_usage"] = ai_usage_record(
        purpose="ENGINEER_BRIEFING",
        requested=request.include_ai,
        status="NOT_REQUESTED" if not request.include_ai else "PENDING",
    )
    if not request.include_ai:
        return result
    if ai_client is None:
        result["ai_usage"] = ai_usage_record(
            purpose="ENGINEER_BRIEFING",
            requested=True,
            status="UNAVAILABLE",
            error="GCP Vertex AI client is not connected",
        )
        return result

    payload = {
        "session": result.get("session"),
        "session_policy": result.get("session_policy"),
        "data_quality": result.get("data_quality"),
        "comparison": result.get("comparison"),
        "classification": result.get("performance", {}).get("classification"),
        "top_losses": result.get("performance", {}).get("top_losses", [])[:3],
        "vehicle": result.get("vehicle"),
        "setup_recommendation": result.get("setup_recommendation"),
        "external_reference": ai_external_reference_context(result),
        "user_feedback": request.user_feedback,
        "voice_review": result.get("voice_review"),
        "long_term_memory": ai_memory_context(result),
    }
    prompt = (
        "You are a race-engineering briefing editor. Explain only the supplied deterministic JSON. "
        "Do not invent telemetry, corner names, setup values, legal ranges, lap-time gains, or fuel recommendations. "
        "Use long_term_memory only as historical evidence and clearly distinguish current-session facts from past patterns. "
        "external_reference contains only anonymized local aggregate metrics; do not infer driver identity or raw Garage61 data. "
        "For QUALIFYING, the normal run is one out-lap plus up to two timed laps; never request three timed laps. "
        "The setup_recommendation object is authoritative. Return Korean JSON with keys summary and sections.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    def call() -> Any:
        return ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=structured_generation_config(AIBriefingSchema, 0.12),
        )

    try:
        response = await asyncio.to_thread(call)
        briefing = parse_structured_response(response.text, AIBriefingSchema).model_dump()
        briefing["source"] = "GCP_VERTEX_AI_BRIEFING"
        result["briefing"] = briefing
        result["ai_usage"] = ai_usage_record(
            purpose="ENGINEER_BRIEFING",
            requested=True,
            used=True,
            response=response,
            status="SUCCESS",
        )
    except Exception as exc:
        logger.warning("AI briefing failed; deterministic briefing retained: %s", exc)
        result["ai_usage"] = ai_usage_record(
            purpose="ENGINEER_BRIEFING",
            requested=True,
            status="FAILED_LOCAL_FALLBACK",
            error=str(exc),
        )
    return result


def planner_required_samples(mode: SessionMode) -> dict[str, Any]:
    if mode == SessionMode.QUALIFYING:
        return {
            "new_runs": 1,
            "out_laps_per_run": 1,
            "timed_laps_per_run": 2,
            "comparison_method": "BEST_LAP_AND_TARGET_CORNER",
            "note": "각 run은 아웃랩 1바퀴 + 계측랩 최대 2바퀴입니다.",
        }
    if mode == SessionMode.ENDURANCE:
        return {
            "new_runs": 1,
            "out_laps_per_run": 1,
            "timed_laps_per_run": 5,
            "comparison_method": "MEDIAN_PACE_AND_DEGRADATION",
            "note": "후보 셋업으로 유효 계측랩 5개 이상을 권장합니다.",
        }
    return {
        "new_runs": 1,
        "out_laps_per_run": 1,
        "timed_laps_per_run": 3,
        "comparison_method": "MEDIAN_AND_VARIABILITY",
        "note": "후보 셋업으로 유효 계측랩 3개를 기록합니다.",
    }


def build_planner_evidence(
    result: dict[str, Any], request: ExperimentPlanRequest
) -> list[dict[str, Any]]:
    policy = result.get("session_policy", {})
    quality = result.get("data_quality", {})
    classification = result.get("performance", {}).get("classification", {})
    evidence: list[dict[str, Any]] = [
        {
            "id": "SESSION_SAMPLE",
            "type": "SESSION",
            "summary": (
                f"{request.session_mode.value}: 유효 계측랩 {quality.get('valid_laps', 0)}개, "
                f"목표 {policy.get('quality_target_laps')}개, 아웃랩 {quality.get('out_laps', 0)}개"
            ),
            "data": {
                "expected_run_structure": policy.get("expected_run_structure"),
                "valid_laps": quality.get("valid_laps"),
                "comparison_ready": quality.get("timed_lap_comparison_ready"),
            },
        },
        {
            "id": "CLASSIFICATION",
            "type": "CLASSIFICATION",
            "summary": f"{classification.get('label')}: {' '.join(classification.get('reasons', []))}",
            "data": classification,
        },
    ]
    comparison = result.get("comparison")
    if comparison:
        evidence.append(
            {
                "id": "LAP_COMPARISON",
                "type": "LAP_COMPARISON",
                "summary": (
                    f"대상 {comparison.get('target_lap')} / 기준 {comparison.get('reference_lap')} / "
                    f"델타 {comparison.get('total_delta_s')}초"
                ),
                "data": comparison,
            }
        )
    external = ai_external_reference_context(result)
    if external.get("status") == "READY":
        aggregate = external.get("aggregate", {}) or {}
        evidence.append(
            {
                "id": "EXTERNAL_REFERENCE",
                "type": "EXTERNAL_REFERENCE_CONSENSUS",
                "summary": (
                    f"Garage61 익명 기준 {external.get('selected_reference_count', 0)}개, "
                    f"셋업 판단용 빠른 기준 {external.get('decision_reference_count', 0)}개, "
                    f"기준 중앙값 대비 {aggregate.get('gap_to_reference_median_s')}초"
                ),
                "data": external,
            }
        )
    for corner in result.get("performance", {}).get("top_losses", [])[:3]:
        evidence.append(
            {
                "id": f"CORNER_{corner.get('id')}",
                "type": "CORNER_LOSS",
                "summary": (
                    f"{corner.get('label')} {corner.get('issue_type')} 손실 {corner.get('delta_s')}초; "
                    + "; ".join(corner.get("evidence", []))
                ),
                "data": corner,
            }
        )
    ride = result.get("vehicle", {}).get("ride_height", {})
    evidence.extend(
        [
            {
                "id": "RIDE_HEIGHT",
                "type": "VEHICLE",
                "summary": (
                    f"바텀아웃 {ride.get('bottoming_detected')}, events {ride.get('bottoming_events')}, "
                    f"affected laps {ride.get('affected_valid_laps')}"
                ),
                "data": ride,
            },
            {
                "id": "LOCAL_SETUP_DECISION",
                "type": "SETUP",
                "summary": f"로컬 셋업 결정 {result.get('setup_recommendation', {}).get('decision')}",
                "data": result.get("setup_recommendation", {}),
            },
        ]
    )
    if request.user_feedback.strip():
        evidence.append(
            {
                "id": "DRIVER_FEEDBACK",
                "type": "DRIVER_FEEDBACK",
                "summary": request.user_feedback.strip(),
                "data": {"text": request.user_feedback.strip()},
            }
        )
    for index, historical in enumerate(
        result.get("memory", {}).get("similar_sessions", [])[:5],
        start=1,
    ):
        summary = historical.get("memory_summary", {}) or {}
        outcomes = historical.get("experiment_outcomes", []) or []
        outcome_text = ""
        if outcomes:
            latest = outcomes[0]
            changed_settings = list((latest.get("setup_diff") or {}).keys())[:3]
            change_text = f"; 변경 {', '.join(changed_settings)}" if changed_settings else ""
            outcome_text = (
                f"; 최근 A/B 결과 {latest.get('verdict')} "
                f"(best {latest.get('best_delta_s')}s, median {latest.get('median_delta_s')}s)"
                f"{change_text}"
            )
        evidence.append(
            {
                "id": f"HISTORY_{index}",
                "type": "LONG_TERM_MEMORY",
                "summary": (
                    f"유사도 {historical.get('similarity_score')}: "
                    f"{historical.get('session_mode')} / {historical.get('track_temp_c')}°C / "
                    f"best {historical.get('best_lap_s')}s / "
                    f"{historical.get('classification')} / setup {historical.get('setup_decision')}"
                    f"{outcome_text}"
                ),
                "data": {
                    "session_id": historical.get("session_id"),
                    "similarity_score": historical.get("similarity_score"),
                    "similarity_reasons": historical.get("similarity_reasons", []),
                    "top_issues": summary.get("top_issues", [])[:3],
                    "experiment_outcomes": outcomes[:2],
                },
            }
        )
    return evidence


def driving_candidate(
    corner: dict[str, Any],
    track_length: float,
    required_samples: dict[str, Any],
    confidence: float,
) -> dict[str, Any]:
    corner_id = str(corner.get("id"))
    loss = float(corner.get("delta_s") or 0.0)
    target = corner.get("target") or {}
    reference = corner.get("reference") or {}
    success_rules: list[dict[str, Any]] = [
        {
            "metric": f"{corner_id}.corner_delta_s",
            "operator": "<=",
            "target": round(max(0.02, loss * 0.40), 3),
            "unit": "s",
            "description": f"{corner.get('label')} 손실을 현재 {loss:.3f}초에서 축소",
        }
    ]
    if target.get("full_throttle_pct") is not None and reference.get("full_throttle_pct") is not None:
        delay = (float(target["full_throttle_pct"]) - float(reference["full_throttle_pct"])) * track_length
        if delay > 5.0:
            success_rules.append(
                {
                    "metric": f"{corner_id}.full_throttle_delay_m",
                    "operator": "<=",
                    "target": round(max(5.0, delay * 0.35), 1),
                    "unit": "m",
                    "description": f"풀스로틀 복귀 지연 {delay:.1f}m 축소",
                }
            )
    if target.get("min_speed_kph") is not None and reference.get("min_speed_kph") is not None:
        deficit = float(reference["min_speed_kph"]) - float(target["min_speed_kph"])
        if deficit > 1.0:
            success_rules.append(
                {
                    "metric": f"{corner_id}.min_speed_deficit_kph",
                    "operator": "<=",
                    "target": round(max(1.0, deficit * 0.40), 1),
                    "unit": "km/h",
                    "description": f"최저속도 부족분 {deficit:.1f}km/h 축소",
                }
            )
    return {
        "id": f"DRIVING_{corner_id}_{corner.get('issue_type')}",
        "decision": "DRIVING_TEST",
        "title": f"{corner.get('label')} {corner.get('issue_type')} 입력 테스트",
        "hypothesis_seed": f"{corner.get('label')} 손실은 셋업보다 입력 순서 차이를 먼저 검증할 가치가 있습니다.",
        "evidence_ids": ["SESSION_SAMPLE", "CLASSIFICATION", "LAP_COMPARISON", f"CORNER_{corner_id}"],
        "action": {
            "type": "DRIVING",
            "corner_id": corner_id,
            "instruction": corner.get("driver_action"),
            "setup_change": None,
        },
        "success_rules": success_rules[:3],
        "revert_rules": [
            {
                "metric": f"{corner_id}.corner_delta_s",
                "operator": ">=",
                "target": round(loss + 0.03, 3),
                "unit": "s",
                "description": "해당 코너 손실이 현재보다 악화되면 입력 변경을 원복",
            },
            {
                "metric": "target_lap_delta_s",
                "operator": ">=",
                "target": 0.10,
                "unit": "s",
                "description": "전체 랩이 기준보다 0.10초 이상 느려지면 원복",
            },
        ],
        "required_samples": dict(required_samples),
        "confidence_cap": min(0.82, max(0.45, confidence + 0.08)),
    }


def setup_candidate(
    adjustment: dict[str, Any],
    mode: SessionMode,
    required_samples: dict[str, Any],
) -> dict[str, Any]:
    setting = str(adjustment.get("setting"))
    if mode == SessionMode.QUALIFYING:
        success_rules = [
            {"metric": "candidate_best_lap_delta_s", "operator": "<=", "target": -0.05, "unit": "s", "description": "후보 최고 랩이 기준보다 최소 0.05초 개선"},
            {"metric": "target_corner_delta_s", "operator": "<=", "target": 0.0, "unit": "s", "description": "문제 코너가 기준보다 악화되지 않음"},
        ]
        revert_rules = [
            {"metric": "candidate_best_lap_delta_s", "operator": ">=", "target": 0.05, "unit": "s", "description": "후보 최고 랩이 기준보다 0.05초 이상 느리면 원복"}
        ]
    elif mode == SessionMode.ENDURANCE:
        success_rules = [
            {"metric": "candidate_median_delta_s", "operator": "<=", "target": -0.05, "unit": "s", "description": "후보 중앙 페이스가 최소 0.05초 개선"},
            {"metric": "pace_slope_delta_s_per_lap", "operator": "<=", "target": 0.02, "unit": "s/lap", "description": "랩당 열화율 악화가 0.02초/lap 이내"},
        ]
        revert_rules = [
            {"metric": "pace_slope_delta_s_per_lap", "operator": ">", "target": 0.03, "unit": "s/lap", "description": "열화율 악화가 0.03초/lap을 넘으면 원복"}
        ]
    else:
        success_rules = [
            {"metric": "candidate_median_delta_s", "operator": "<=", "target": -0.05, "unit": "s", "description": "후보 중앙값이 최소 0.05초 개선"},
            {"metric": "candidate_stdev_delta_s", "operator": "<=", "target": 0.05, "unit": "s", "description": "랩타임 편차 악화가 0.05초 이내"},
        ]
        revert_rules = [
            {"metric": "candidate_median_delta_s", "operator": ">=", "target": 0.05, "unit": "s", "description": "후보 중앙값이 0.05초 이상 느리면 원복"}
        ]
    if setting == "front_ride_height":
        success_rules.append(
            {"metric": "bottoming_events", "operator": "==", "target": 0, "unit": "events", "description": "고속 바텀아웃 이벤트 제거"}
        )
    evidence_ids = ["SESSION_SAMPLE", "CLASSIFICATION", "LOCAL_SETUP_DECISION", "RIDE_HEIGHT"]
    if str(adjustment.get("source") or "").startswith("GARAGE61_"):
        evidence_ids.append("EXTERNAL_REFERENCE")
    if adjustment.get("category") in {"AERO_DRAG", "TOP_SPEED_OPPORTUNITY"}:
        success_rules.append(
            {"metric": "straight_late_speed_deficit_kph", "operator": "<=", "target": 2.0, "unit": "km/h", "description": "장거리 직선 후반 속도 부족을 2 km/h 이내로 축소"}
        )
    return {
        "id": f"SETUP_{setting}",
        "decision": "SETUP_TEST",
        "title": f"{adjustment.get('label')} 1-step A/B 테스트",
        "hypothesis_seed": adjustment.get("evidence"),
        "evidence_ids": evidence_ids,
        "action": {
            "type": "SETUP",
            "corner_id": None,
            "instruction": adjustment.get("validation"),
            "setup_change": {
                "setting": setting,
                "label": adjustment.get("label"),
                "original_value": adjustment.get("original_value"),
                "value": adjustment.get("value"),
                "direction": adjustment.get("direction"),
                "step_text": adjustment.get("step_text"),
                "target_display": adjustment.get("target_display"),
                "numeric_target_source": adjustment.get("numeric_target_source"),
                "numeric_target_estimated": adjustment.get("numeric_target_estimated", False),
                "unit": adjustment.get("unit"),
                "tradeoff": adjustment.get("tradeoff"),
                "validator": adjustment.get("validator"),
            },
        },
        "success_rules": success_rules[:3],
        "revert_rules": revert_rules,
        "required_samples": dict(required_samples),
        "confidence_cap": min(
            float(adjustment.get("confidence") or 0.6),
            float(session_policy(mode)["setup_confidence_cap"]),
        ),
    }


def build_experiment_candidates(
    result: dict[str, Any], request: ExperimentPlanRequest
) -> list[dict[str, Any]]:
    required = planner_required_samples(request.session_mode)
    track_length = float(result.get("session", {}).get("track_length_m") or 1000.0)
    confidence = float(result.get("performance", {}).get("classification", {}).get("confidence") or 0.5)
    candidates = [
        driving_candidate(corner, track_length, required, confidence)
        for corner in result.get("performance", {}).get("top_losses", [])[:3]
        if float(corner.get("delta_s") or 0.0) >= 0.02
    ]
    setup_adjustments = result.get("setup_recommendation", {}).get("test_candidates", [])[:4]
    candidates.extend(
        setup_candidate(adjustment, request.session_mode, required)
        for adjustment in setup_adjustments
    )
    policy = session_policy(request.session_mode)
    candidates.append(
        {
            "id": "HOLD_COLLECT_MORE_DATA",
            "decision": "HOLD",
            "title": "현재 셋업 유지 및 표본 확보",
            "hypothesis_seed": "현재 근거로는 수치 변경보다 통제된 추가 표본이 안전합니다.",
            "evidence_ids": ["SESSION_SAMPLE", "CLASSIFICATION", "LOCAL_SETUP_DECISION"],
            "action": {
                "type": "HOLD",
                "corner_id": None,
                "instruction": "셋업을 유지하고 세션 모드에 맞는 계측랩을 기록한 뒤 다시 평가합니다.",
                "setup_change": None,
            },
            "success_rules": [
                {
                    "metric": "valid_timed_laps",
                    "operator": ">=",
                    "target": int(policy["quality_target_laps"]),
                    "unit": "laps",
                    "description": "세션 모드에 필요한 유효 계측랩 확보",
                }
            ],
            "revert_rules": [],
            "required_samples": dict(required),
            "confidence_cap": 0.55 if setup_adjustments else 0.90,
        }
    )
    return candidates


def materialize_experiment_plan(
    selection: AIPlannerSelectionSchema,
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    request: ExperimentPlanRequest,
) -> dict[str, Any]:
    candidate = {item["id"]: item for item in candidates}.get(selection.selected_candidate_id)
    if candidate is None:
        raise ValueError("AI selected an unknown experiment candidate")
    if selection.decision != candidate["decision"]:
        raise ValueError("AI decision does not match the selected local candidate")
    if request.session_mode == SessionMode.QUALIFYING and int(candidate["required_samples"]["timed_laps_per_run"]) > 2:
        raise ValueError("Qualifying plan requested more than two timed laps")

    evidence_map = {item["id"]: item for item in evidence}
    evidence_ids = [item for item in selection.evidence_ids if item in evidence_map]
    for required_id in candidate.get("evidence_ids", []):
        if required_id in evidence_map and required_id not in evidence_ids:
            evidence_ids.append(required_id)
    if not evidence_ids:
        raise ValueError("AI plan contains no valid evidence references")

    return {
        "plan_id": str(uuid.uuid4()),
        "created_at": time.time(),
        "source": "GCP_VERTEX_AI_EXPERIMENT_PLANNER",
        "model": GEMINI_MODEL,
        "session_id": result.get("memory", {}).get("session_id"),
        "filename": result.get("file", {}).get("name"),
        "session_mode": request.session_mode.value,
        "decision": candidate["decision"],
        "candidate_id": candidate["id"],
        "title": candidate["title"],
        "hypothesis": selection.hypothesis.strip(),
        "rationale": selection.rationale.strip(),
        "confidence": round(min(float(selection.confidence), float(candidate["confidence_cap"])), 2),
        "evidence_ids": evidence_ids,
        "evidence": [evidence_map[item] for item in evidence_ids],
        "action": candidate["action"],
        "success_rules": candidate["success_rules"],
        "revert_rules": candidate["revert_rules"],
        "required_samples": candidate["required_samples"],
        "memory_context": {
            "source": "LOCAL_SQLITE_LONG_TERM_MEMORY",
            "similar_sessions_used": len(
                [item for item in evidence_ids if item.startswith("HISTORY_")]
            ),
            "available_similar_sessions": result.get("memory", {}).get(
                "similar_session_count", 0
            ),
            "history_evidence_ids": [
                item for item in evidence_ids if item.startswith("HISTORY_")
            ],
        },
        "guardrails": [
            "원본 60Hz 텔레메트리는 GCP에 전송하지 않고 로컬 계산 결과와 제한된 후보만 전송합니다.",
            "AI는 로컬 엔진이 생성한 후보 중 하나만 선택하며 셋업 수치를 새로 만들 수 없습니다.",
            "퀄리파잉에서는 아웃랩 1 + 계측랩 최대 2 구조를 초과할 수 없습니다.",
            "과거 세션은 현재 조건과의 유사도 및 실제 A/B 결과가 있는 경우에만 보조 근거로 사용합니다.",
            "Garage61 외부 기준은 원본 CSV나 드라이버 식별자를 GCP에 보내지 않고 로컬에서 계산한 익명 컨센서스만 사용합니다.",
            "단일 외부 CSV는 셋업 결론을 만들 수 없으며 최소 2개의 더 빠른 호환 기준이 필요합니다.",
        ],
    }


async def generate_ai_experiment_plan(request: ExperimentPlanRequest) -> dict[str, Any]:
    request = request_with_structured_feedback(request)
    if ai_client is None:
        raise HTTPException(
            status_code=503,
            detail="GCP Vertex AI가 연결되지 않아 AI Setup Experiment Planner를 실행할 수 없습니다.",
        )
    result = await load_analysis(
        AnalyzeRequest(
            session_mode=request.session_mode,
            pit_rule=request.pit_rule,
            user_feedback=request.user_feedback,
            feedback_phase=request.feedback_phase,
            feedback_speed=request.feedback_speed,
            feedback_symptom=request.feedback_symptom,
            setup_mode=request.setup_mode,
            target_lap=request.target_lap,
            reference_lap=request.reference_lap,
            include_ai=False,
            filename=request.filename,
            external_reference_files=request.external_reference_files,
        )
    )
    assigned_modes = result.get("memory", {}).get("assigned_modes", []) or []
    if request.session_mode.value not in assigned_modes:
        raise HTTPException(
            status_code=409,
            detail=(
                "선택한 텔레메트리의 세션 유형이 DB에 확정되지 않았습니다. "
                "상단에서 퀄리파잉·3-Lap 베이스·인듀런스 중 하나를 먼저 선택하십시오."
            ),
        )
    evidence = build_planner_evidence(result, request)
    candidates = build_experiment_candidates(result, request)
    payload = {
        "session": result.get("session"),
        "session_policy": result.get("session_policy"),
        "data_quality": result.get("data_quality"),
        "classification": result.get("performance", {}).get("classification"),
        "user_feedback": request.user_feedback.strip(),
        "evidence_catalog": evidence,
        "allowed_candidates": candidates,
        "external_reference": ai_external_reference_context(result),
        "long_term_memory": ai_memory_context(result),
    }
    prompt = (
        "You are the AI Setup Experiment Planner for iRacing. Select exactly one allowed candidate for the next controlled test. "
        "Do not alter candidate actions, setup numbers, success rules, revert rules, or sample counts. "
        "Prefer a driving test when evidence is ambiguous. Select SETUP_TEST only when an allowed SETUP candidate exists. "
        "Use LONG_TERM_MEMORY evidence to avoid repeating failed experiments, but do not claim causality from one historical run. "
        "When a past A/B result is KEEP or REVERT under similar conditions, cite its HISTORY evidence ID in evidence_ids. "
        "For QUALIFYING, the full run is one out-lap plus no more than two timed laps; never request three timed laps. "
        "Use only supplied evidence IDs and write hypothesis/rationale in Korean. Return JSON matching the schema.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    def call() -> Any:
        return ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=structured_generation_config(AIPlannerSelectionSchema, 0.08),
        )

    try:
        response = await asyncio.to_thread(call)
        selection = parse_structured_response(response.text, AIPlannerSelectionSchema)
        plan = materialize_experiment_plan(selection, candidates, evidence, result, request)
        persisted = False
        if plan_store is not None:
            try:
                await asyncio.to_thread(plan_store.save, plan)
                persisted = True
            except Exception as exc:
                logger.warning("Could not persist experiment plan: %s", exc)
        return {
            "status": "success",
            "plan": plan,
            "persisted": persisted,
            "ai_usage": ai_usage_record(
                purpose="SETUP_EXPERIMENT_PLANNER",
                requested=True,
                used=True,
                response=response,
                status="SUCCESS",
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI experiment planner failed")
        raise HTTPException(status_code=502, detail=f"GCP AI 실험 계획 생성 실패: {exc}") from exc


# ---------------------------------------------------------------------------
# Experiment comparison
# ---------------------------------------------------------------------------


def experiment_run_observations(telemetry: ParsedTelemetry) -> dict[str, Any]:
    analyzer = TelemetryAnalyzer(telemetry)
    lap_conditions = [telemetry_reference_conditions(telemetry, lap) for lap in analyzer.valid_laps]
    environment = telemetry_reference_conditions(telemetry)
    unstable_conditions: list[str] = []
    if lap_conditions:
        for key in set().union(*(set(values) for values in lap_conditions)):
            values = [row.get(key) for row in lap_conditions]
            finite = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(float(value))]
            if key in {"weather_declared_wet", "tire_compound", "track_wetness_code"}:
                known = [value for value in values if value is not None]
                environment[key] = known[0] if len(known) == len(values) and len(set(known)) == 1 else None
                if len(set(known)) > 1:
                    unstable_conditions.append(key)
            elif finite:
                environment[key] = float(np.median(finite))
        fuel_medians = [_finite_channel_median(telemetry, "FuelLevel", lap) for lap in analyzer.valid_laps]
        finite_fuel = [value for value in fuel_medians if value is not None]
        environment["fuel_l"] = float(np.median(finite_fuel)) if finite_fuel else None
    environment["unstable_conditions"] = unstable_conditions
    return {"environment": environment, "corner_metrics": analyzer._memory_corner_metrics()}


def experiment_metrics(result: dict[str, Any], session_mode: SessionMode) -> dict[str, Any]:
    valid_laps = [
        lap for lap in result.get("laps", [])
        if lap.get("status") == "VALID" and lap.get("lap_time_s")
    ]
    valid_laps.sort(key=lambda lap: lap.get("lap_number") or 0)
    times = np.array([lap["lap_time_s"] for lap in valid_laps], dtype=float)
    lap_numbers = np.array([lap["lap_number"] for lap in valid_laps], dtype=float)
    pace_slope = None
    if len(times) >= 3 and float(np.ptp(lap_numbers)) > 0:
        pace_slope = float(np.polyfit(lap_numbers, times, 1)[0])
    tail_count = min(3, len(times))
    tail_median = float(np.median(times[-tail_count:])) if tail_count else None
    median = float(np.median(times)) if len(times) else None
    stdev = float(np.std(times)) if len(times) > 1 else None
    mad = float(np.median(np.abs(times - median))) if len(times) > 1 and median is not None else None
    robust_sigma = float(1.4826 * mad) if mad is not None else None
    if len(times) == 2 and stdev is not None:
        robust_sigma = max(float(robust_sigma or 0.0), stdev)
    q25, q75 = (np.percentile(times, [25, 75]) if len(times) > 1 else [None, None])
    iqr = float(q75 - q25) if q25 is not None and q75 is not None else None
    tire_data = result.get("vehicle", {}).get("tires", {})
    attempts: list[dict[str, Any]] = []
    excluded_laps = 0
    lap_rows = result.get("laps", []) or []
    for position, lap in enumerate(lap_rows):
        status = str(lap.get("status") or "")
        reason = str(lap.get("invalid_reason") or "").casefold()
        if status == "OUT_LAP" or "pit road" in reason:
            excluded_laps += 1
            continue
        if status == "PARTIAL":
            start, end = lap.get("start_pct"), lap.get("end_pct")
            recording_cutoff = (position == 0 and start is not None and float(start) > 0.02) or (position == len(lap_rows) - 1 and end is not None and float(end) < 0.98)
            offtrack = lap.get("on_track_ratio") is not None and float(lap["on_track_ratio"]) < 0.995
            if recording_cutoff and not offtrack and not reason:
                excluded_laps += 1
                continue
        if status in {"VALID", "INVALID", "PARTIAL"}:
            attempts.append(lap)
    successful = sum(1 for lap in attempts if lap.get("status") == "VALID")
    offtrack_attempts = sum(1 for lap in attempts if "off-track" in str(lap.get("invalid_reason") or "").casefold()
                            or (lap.get("on_track_ratio") is not None and float(lap["on_track_ratio"]) < 0.995))
    longest_streak = streak = 0
    for lap in attempts:
        streak = streak + 1 if lap.get("status") == "VALID" else 0
        longest_streak = max(longest_streak, streak)
    return {
        "objective": session_mode.value,
        "valid_laps": len(times),
        "best_s": round_or_none(float(np.min(times)) if len(times) else None, 4),
        "median_s": round_or_none(median, 4),
        "tail_median_s": round_or_none(tail_median, 4),
        "stdev_s": round_or_none(stdev, 4),
        "mad_s": round_or_none(mad, 4),
        "robust_sigma_s": round_or_none(robust_sigma, 4),
        "iqr_s": round_or_none(iqr, 4),
        "pace_slope_s_per_lap": round_or_none(pace_slope, 4),
        "surface_temp_slope_c_per_lap": tire_data.get("surface_temp_slope_c_per_lap"),
        "hot_pressure_slope_kpa_per_lap": tire_data.get("hot_pressure_slope_kpa_per_lap"),
        "track_temp_c": result.get("session", {}).get("track_temp_c"),
        "setup_name": result.get("session", {}).get("setup_name"),
        "attempted_laps": len(attempts),
        "successful_laps": successful,
        "excluded_out_or_cutoff_laps": excluded_laps,
        "success_rate": round_or_none(successful / len(attempts), 4) if attempts else None,
        "offtrack_laps": offtrack_attempts,
        "offtrack_rate": round_or_none(offtrack_attempts / len(attempts), 4) if attempts else None,
        "longest_valid_streak": longest_streak,
    }


def _representative_corners(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in result.get("performance", {}).get("corners", []) or []
        if item.get("id") and isinstance(item.get("target"), dict)
    ]


def experiment_effect_profile(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    def run_corners(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        measurements = result.get("ab_corner_metrics") or []
        if not measurements:
            return _representative_corners(result), 1 if _representative_corners(result) else 0
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        lap_numbers: set[int] = set()
        for corner in measurements:
            if corner.get("id") and isinstance(corner.get("target"), dict):
                grouped[str(corner["id"])].append(corner)
                if corner.get("lap_number") is not None:
                    lap_numbers.add(int(corner["lap_number"]))
        aggregated = []
        for rows in grouped.values():
            first = dict(rows[0])
            keys = set().union(*(set(row["target"]) for row in rows))
            metrics = {}
            for key in keys:
                numeric = [float(row["target"][key]) for row in rows if isinstance(row["target"].get(key), (int, float)) and math.isfinite(float(row["target"][key]))]
                metrics[key] = float(np.median(numeric)) if numeric else None
            first["target"] = metrics
            first["observation_laps"] = len({row.get("lap_number") for row in rows})
            aggregated.append(first)
        return aggregated, len(lap_numbers)

    base_corners, baseline_runs = run_corners(baseline)
    candidate_corners, candidate_runs = run_corners(candidate)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unused = set(range(len(candidate_corners)))
    # Corner boundaries move slightly when the pace/setup changes. Match by apex
    # distance rather than assuming C1/C2 numbering stayed perfectly identical.
    for left in sorted(base_corners, key=lambda item: float(item.get("apex_pct") or 0.0)):
        left_apex = float(left.get("apex_pct") or 0.0)
        choices = [
            (abs(float(candidate_corners[index].get("apex_pct") or 0.0) - left_apex), index)
            for index in unused
        ]
        if not choices:
            continue
        distance, chosen = min(choices)
        if distance > 0.025:
            continue
        unused.remove(chosen)
        pairs.append((left, candidate_corners[chosen]))

    rows: list[dict[str, Any]] = []
    track_length = float(baseline.get("session", {}).get("track_length_m") or 0.0)
    for left, right in pairs:
        corner_id = str(left.get("id") or right.get("id") or "")
        lm = left.get("target") or {}
        rm = right.get("target") or {}
        def delta(key: str) -> Optional[float]:
            a, b = lm.get(key), rm.get(key)
            if a is None or b is None:
                return None
            try:
                return float(b) - float(a)
            except (TypeError, ValueError):
                return None
        full_throttle_shift = None
        brake_release_shift = None
        if track_length > 0:
            full_throttle_shift = pct_distance(rm.get("full_throttle_pct"), lm.get("full_throttle_pct"), track_length)
            brake_release_shift = pct_distance(rm.get("brake_release_pct"), lm.get("brake_release_pct"), track_length)
        rows.append({
            "corner_id": corner_id,
            "label": left.get("label"),
            "speed_class": left.get("speed_class"),
            "min_speed_delta_kph": round_or_none(delta("min_speed_kph"), 2),
            "exit_speed_delta_kph": round_or_none(delta("exit_speed_kph"), 2),
            "abs_duty_delta_pct": round_or_none(delta("abs_duty_pct"), 2),
            "coast_time_delta_s": round_or_none(delta("coast_time_s"), 3),
            "avg_steer_delta_deg": round_or_none(delta("avg_abs_steer_deg"), 2),
            "steering_efficiency_delta": round_or_none(delta("steering_efficiency"), 3),
            "steering_corrections_delta": round_or_none(delta("steering_corrections"), 2),
            "throttle_relifts_delta": round_or_none(delta("throttle_relifts"), 2),
            "full_throttle_shift_m": round_or_none(full_throttle_shift, 1),
            "brake_release_shift_m": round_or_none(brake_release_shift, 1),
        })

    def median_metric(key: str) -> Optional[float]:
        values = [float(item[key]) for item in rows if item.get(key) is not None]
        return round_or_none(float(np.median(values)) if values else None, 3)

    by_speed: dict[str, dict[str, Any]] = {}
    for speed in ("LOW", "MEDIUM", "HIGH"):
        speed_rows = [item for item in rows if item.get("speed_class") == speed]
        if not speed_rows:
            continue
        def med(key: str) -> Optional[float]:
            vals = [float(item[key]) for item in speed_rows if item.get(key) is not None]
            return round_or_none(float(np.median(vals)) if vals else None, 3)
        by_speed[speed] = {
            "corner_count": len(speed_rows),
            "min_speed_delta_kph": med("min_speed_delta_kph"),
            "exit_speed_delta_kph": med("exit_speed_delta_kph"),
            "abs_duty_delta_pct": med("abs_duty_delta_pct"),
            "coast_time_delta_s": med("coast_time_delta_s"),
            "avg_steer_delta_deg": med("avg_steer_delta_deg"),
            "full_throttle_shift_m": med("full_throttle_shift_m"),
        }
    return {
        "corner_count": len(rows),
        "baseline_observation_laps": baseline_runs,
        "candidate_observation_laps": candidate_runs,
        "aggregation": "ACROSS_VALID_LAPS_MEDIAN" if baseline.get("ab_corner_metrics") and candidate.get("ab_corner_metrics") else "REPRESENTATIVE_LAP_ONLY",
        "overall_corner_deltas": {
            "min_speed_kph": median_metric("min_speed_delta_kph"),
            "exit_speed_kph": median_metric("exit_speed_delta_kph"),
            "abs_duty_pct": median_metric("abs_duty_delta_pct"),
            "coast_time_s": median_metric("coast_time_delta_s"),
            "avg_steer_deg": median_metric("avg_steer_delta_deg"),
            "steering_efficiency": median_metric("steering_efficiency_delta"),
            "steering_corrections": median_metric("steering_corrections_delta"),
            "throttle_relifts": median_metric("throttle_relifts_delta"),
            "full_throttle_shift_m": median_metric("full_throttle_shift_m"),
            "brake_release_shift_m": median_metric("brake_release_shift_m"),
        },
        "by_speed_class": by_speed,
        "corner_changes": rows,
    }


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _ab_driveability_axis(effect_profile: dict[str, Any]) -> dict[str, Any]:
    metrics = effect_profile.get("overall_corner_deltas") or {}
    components: list[tuple[str, float]] = []
    scales = {
        "steering_corrections": (1.0, -1.0),
        "throttle_relifts": (1.0, -1.0),
    }
    for key, (scale, sign) in scales.items():
        value = metrics.get(key)
        if value is None:
            continue
        components.append((key, _clamp(sign * float(value) / scale)))
    if not components:
        return {"status": "UNAVAILABLE", "score": None, "evidence_count": 0, "metrics": metrics}
    score = float(np.mean([value for _, value in components]))
    status = "IMPROVED" if score >= 0.20 else "WORSENED" if score <= -0.20 else "NEUTRAL"
    return {
        "status": status,
        "score": round(score, 3),
        "evidence_count": len(components),
        "metrics": metrics,
        "aggregation": effect_profile.get("aggregation", "REPRESENTATIVE_LAP_ONLY"),
        "baseline_observation_laps": effect_profile.get("baseline_observation_laps", 0),
        "candidate_observation_laps": effect_profile.get("candidate_observation_laps", 0),
    }


def _ab_axis(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


def experiment_conditions_quality(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    def environment(result: dict[str, Any]) -> dict[str, Any]:
        session = result.get("session") or {}
        values = dict(session.get("environment") or {})
        for key in ("track_temp_c", "fuel_l", "weather_declared_wet", "tire_compound", "weight_penalty_kg", "power_adjust_pct"):
            if values.get(key) is None and session.get(key) is not None:
                values[key] = session[key]
        return values

    left, right = environment(baseline), environment(candidate)
    specifications = [
        ("fuel_l", "연료량", 3.0, True), ("track_temp_c", "노면 온도", 3.0, True),
        ("weather_declared_wet", "건조/젖은 노면", 0.0, True), ("tire_compound", "타이어 컴파운드", 0.0, True),
        ("weight_penalty_kg", "추가 중량", 0.5, True), ("power_adjust_pct", "출력 조정", 0.2, True),
        ("air_temp_c", "기온", 5.0, False), ("wind_speed_kph", "풍속", 10.0, False),
        ("wind_direction_deg", "풍향", 45.0, False),
        ("track_wetness_code", "노면 수분 단계", 0.0, False),
    ]
    checks = []
    for key, label, tolerance, required in specifications:
        a, b = left.get(key), right.get(key)
        known = isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(float(a)) and math.isfinite(float(b))
        delta = abs(float(b) - float(a)) if known else None
        if key == "wind_direction_deg" and delta is not None:
            delta = min(delta % 360.0, 360.0 - delta % 360.0)
        status = "UNKNOWN" if not known else "MISMATCHED" if delta > tolerance else "MATCHED"
        checks.append({"key": key, "label": label, "baseline": a, "candidate": b, "status": status, "tolerance": tolerance, "required": required})
    reasons = [f"{item['label']}: 조건 차이" for item in checks if item["status"] == "MISMATCHED"]
    if left.get("unstable_conditions") or right.get("unstable_conditions"):
        reasons.append("런 도중 노면 또는 타이어 조건이 변했습니다.")
    unknowns = [f"{item['label']}: 정보 없음" for item in checks if item["status"] == "UNKNOWN" and item["required"]]
    status = "MISMATCHED" if reasons else "UNKNOWN" if unknowns else "MATCHED"
    return {"status": status, "comparable": status == "MATCHED", "checks": checks,
            "reasons": reasons + unknowns, "baseline": left, "candidate": right,
            "observation_allowed": True, "setup_effect_confirmation_allowed": status == "MATCHED"}


def compare_experiments(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    session_mode: SessionMode,
    goal: Literal["COMFORT", "PACE", "ENDURANCE"] = "COMFORT",
    driver_feedback: Optional[dict[str, Any]] = None,
    setup_evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    base = experiment_metrics(baseline, session_mode)
    cand = experiment_metrics(candidate, session_mode)
    conditions_quality = experiment_conditions_quality(baseline, candidate)
    temp_delta = None
    if base.get("track_temp_c") is not None and cand.get("track_temp_c") is not None:
        temp_delta = cand["track_temp_c"] - base["track_temp_c"]

    median_delta = (
        cand["median_s"] - base["median_s"]
        if cand.get("median_s") is not None and base.get("median_s") is not None else None
    )
    best_delta = (
        cand["best_s"] - base["best_s"]
        if cand.get("best_s") is not None and base.get("best_s") is not None else None
    )
    variability_delta = (
        cand["stdev_s"] - base["stdev_s"]
        if cand.get("stdev_s") is not None and base.get("stdev_s") is not None else None
    )
    robust_variability_delta = (
        cand["robust_sigma_s"] - base["robust_sigma_s"]
        if cand.get("robust_sigma_s") is not None and base.get("robust_sigma_s") is not None else None
    )
    pace_slope_delta = (
        cand["pace_slope_s_per_lap"] - base["pace_slope_s_per_lap"]
        if cand.get("pace_slope_s_per_lap") is not None and base.get("pace_slope_s_per_lap") is not None else None
    )

    required_laps = 5 if goal == "ENDURANCE" else 2 if session_mode == SessionMode.QUALIFYING else 3
    recommended_laps = 5 if goal == "ENDURANCE" else 2 if session_mode == SessionMode.QUALIFYING else 5
    base_noise = float(base.get("robust_sigma_s") or base.get("stdev_s") or 0.03)
    cand_noise = float(cand.get("robust_sigma_s") or cand.get("stdev_s") or 0.03)
    natural_variability = max(0.03, base_noise, cand_noise)
    effect_threshold = max(0.05, natural_variability * 0.50)
    effect_vs_noise = (
        abs(float(median_delta)) / natural_variability
        if median_delta is not None and natural_variability > 1e-9 else None
    )
    consistency_threshold = max(0.03, natural_variability * 0.15)

    pace_status = "NEUTRAL"
    if median_delta is not None:
        if median_delta <= -effect_threshold:
            pace_status = "IMPROVED"
        elif median_delta >= effect_threshold:
            pace_status = "WORSENED"
    consistency_status = "NEUTRAL"
    if robust_variability_delta is not None:
        if robust_variability_delta <= -consistency_threshold:
            consistency_status = "IMPROVED"
        elif robust_variability_delta >= consistency_threshold:
            consistency_status = "WORSENED"

    effect_profile = experiment_effect_profile(baseline, candidate)
    driveability = _ab_driveability_axis(effect_profile)
    success_delta = cand["success_rate"] - base["success_rate"] if cand["success_rate"] is not None and base["success_rate"] is not None else None
    offtrack_delta = cand["offtrack_rate"] - base["offtrack_rate"] if cand["offtrack_rate"] is not None and base["offtrack_rate"] is not None else None
    stability_status = "UNAVAILABLE" if success_delta is None else "WORSENED" if success_delta <= -0.10 or (offtrack_delta is not None and offtrack_delta >= 0.10) else "IMPROVED" if success_delta >= 0.10 or (offtrack_delta is not None and offtrack_delta <= -0.10) else "NEUTRAL"
    performance_axes = {
        "pace": _ab_axis(
            pace_status,
            median_delta_s=round_or_none(median_delta, 4),
            threshold_s=round_or_none(effect_threshold, 4),
            effect_vs_noise_ratio=round_or_none(effect_vs_noise, 3),
        ),
        "consistency": _ab_axis(
            consistency_status,
            baseline_natural_variability_s=round_or_none(base_noise, 4),
            candidate_natural_variability_s=round_or_none(cand_noise, 4),
            delta_s=round_or_none(robust_variability_delta, 4),
            threshold_s=round_or_none(consistency_threshold, 4),
        ),
        "driveability": driveability,
        "stability": _ab_axis(stability_status, success_rate_delta=round_or_none(success_delta, 4),
                              offtrack_rate_delta=round_or_none(offtrack_delta, 4),
                              baseline_success_rate=base["success_rate"], candidate_success_rate=cand["success_rate"],
                              baseline_attempted_laps=base["attempted_laps"], candidate_attempted_laps=cand["attempted_laps"]),
    }

    sample_min = min(int(base.get("valid_laps") or 0), int(cand.get("valid_laps") or 0))
    sample_confidence = "HIGH" if sample_min >= 5 else "MEDIUM" if sample_min >= 3 else "LOW"
    if base["valid_laps"] < required_laps or cand["valid_laps"] < required_laps:
        verdict = "INCONCLUSIVE"
        if goal == "ENDURANCE":
            reason = "ENDURANCE 목표의 효과 확인에는 각 셋업으로 유효 랩 5개가 필요합니다. 짧은 런의 체감은 별도로 기록할 수 있습니다."
        elif session_mode == SessionMode.QUALIFYING:
            reason = "퀄리파잉은 각 셋업의 유효 계측랩 2개로 비교합니다. 현재 표본이 부족합니다."
        else:
            reason = f"A/B는 각 셋업에 최소 유효랩 {required_laps}개가 필요합니다. 권장 표본은 {recommended_laps}개입니다."
    elif not conditions_quality["comparable"]:
        verdict = "INCONCLUSIVE"
        reason = "주행 조건이 다르거나 정보가 부족해 셋업 효과는 미확정입니다. 관측 결과와 체감은 별도로 확인할 수 있습니다."
    elif session_mode == SessionMode.QUALIFYING and goal != "ENDURANCE":
        qual_threshold = max(0.05, natural_variability * 0.35)
        if best_delta is not None and best_delta <= -qual_threshold and consistency_status != "WORSENED":
            verdict = "KEEP"
            reason = "최고 랩 개선폭이 현재 자연 편차 기준을 넘고 일관성도 악화되지 않았습니다."
        elif best_delta is not None and best_delta >= qual_threshold:
            verdict = "REVERT"
            reason = "최고 랩 악화폭이 현재 자연 편차 기준을 넘었습니다."
        elif driveability.get("status") == "IMPROVED" and consistency_status != "WORSENED":
            verdict = "KEEP"
            reason = "순수 최고 랩 차이는 작지만 운전 용이성이 개선되고 재현성 악화가 없어 KEEP 가치가 있습니다."
        else:
            verdict = "INCONCLUSIVE"
            reason = "현재 자연 편차보다 효과가 작아 추가 표본이 필요합니다."
    elif session_mode == SessionMode.ENDURANCE or goal == "ENDURANCE":
        slope_worsened = pace_slope_delta is not None and pace_slope_delta > 0.02
        if pace_status == "IMPROVED" and not slope_worsened and consistency_status != "WORSENED":
            verdict = "KEEP"
            reason = "중앙 페이스가 자연 편차 기준을 넘어 개선됐고 롱런 열화/일관성도 허용 범위입니다."
        elif pace_status == "WORSENED" or (pace_slope_delta is not None and pace_slope_delta > 0.03):
            verdict = "REVERT"
            reason = "중앙 페이스 또는 랩당 열화율이 의미 있게 악화되었습니다."
        elif consistency_status == "IMPROVED" and driveability.get("status") != "WORSENED":
            verdict = "KEEP"
            reason = "평균 페이스 차이는 자연 편차 안이지만 랩 편차가 줄어 장거리 driveability 개선으로 판단합니다."
        else:
            verdict = "INCONCLUSIVE"
            reason = "PACE/CONSISTENCY/DRIVEABILITY 중 우위가 충분히 분리되지 않아 추가 표본이 필요합니다."
    else:
        if pace_status == "IMPROVED" and consistency_status != "WORSENED":
            verdict = "KEEP"
            reason = "후보 셋업의 중앙 페이스 개선이 현재 자연 편차 기준을 넘고 일관성도 악화되지 않았습니다."
        elif pace_status == "WORSENED":
            verdict = "REVERT"
            reason = "후보 셋업의 중앙 페이스 악화가 현재 자연 편차 기준을 넘었습니다."
        elif consistency_status == "IMPROVED" and driveability.get("status") != "WORSENED":
            verdict = "KEEP"
            reason = "평균 페이스 차이는 자연 편차 안이지만 랩 편차가 의미 있게 줄어 DRIVEABILITY KEEP으로 판단합니다."
        elif driveability.get("status") == "IMPROVED" and consistency_status != "WORSENED":
            verdict = "KEEP"
            reason = "평균 페이스 차이는 작지만 코너 입력/탈출 지표가 개선되고 일관성 악화가 없어 DRIVEABILITY KEEP으로 판단합니다."
        elif consistency_status == "WORSENED" and driveability.get("status") == "WORSENED":
            verdict = "REVERT"
            reason = "평균 페이스 차이는 작지만 일관성과 운전 용이성이 함께 악화되었습니다."
        else:
            verdict = "INCONCLUSIVE"
            reason = "차이가 현재 자연 편차 안에 있어 추가 표본이 필요합니다."

    # Subjective preference is useful immediately, but does not establish a
    # causal setup effect or bypass the objective sample/condition requirements.
    subjective = dict(driver_feedback or {})
    preference = "PREFER_CANDIDATE" if subjective.get("change") == "IMPROVED" else "PREFER_BASELINE" if subjective.get("change") == "WORSENED" else "NO_PREFERENCE"
    subjective.update({"status": "REPORTED" if driver_feedback else "NOT_REPORTED", "preference": preference, "effect_confirmed": False})
    repeated_driveability = driveability.get("aggregation") == "ACROSS_VALID_LAPS_MEDIAN" and min(int(driveability.get("baseline_observation_laps") or 0), int(driveability.get("candidate_observation_laps") or 0)) >= required_laps
    objective_driveability = repeated_driveability and driveability.get("status") in {"IMPROVED", "WORSENED"}
    sufficient = sample_min >= required_laps and conditions_quality["comparable"]
    if sufficient and goal == "COMFORT":
        if stability_status == "WORSENED" or (objective_driveability and driveability["status"] == "WORSENED"):
            verdict, reason = "REVERT", "완주 안정성 또는 반복 랩의 입력 안정성이 악화되었습니다."
        elif (stability_status == "IMPROVED" or consistency_status == "IMPROVED" or (objective_driveability and driveability["status"] == "IMPROVED")) and pace_status != "WORSENED":
            verdict, reason = "KEEP", "페이스 악화 없이 반복 주행의 안정성·일관성·입력 편의 지표가 개선되었습니다."
        else:
            verdict, reason = "INCONCLUSIVE", "편의 개선을 확인할 반복 관측이 부족합니다. 속도 차이와 체감 선호는 별도로 기록합니다."
        if subjective.get("change") == "WORSENED" and verdict == "KEEP":
            verdict, reason = "INCONCLUSIVE", "측정 지표와 운전자 체감이 상충합니다. 선호를 기록하고 추가 탐색으로 확인하세요."
    if sufficient and goal == "PACE":
        pace_improved = (best_delta is not None and best_delta <= -max(0.05, natural_variability * 0.35)) if session_mode == SessionMode.QUALIFYING else pace_status == "IMPROVED"
        pace_worsened = (best_delta is not None and best_delta >= max(0.05, natural_variability * 0.35)) if session_mode == SessionMode.QUALIFYING else pace_status == "WORSENED"
        if pace_improved and stability_status != "WORSENED" and consistency_status != "WORSENED":
            verdict, reason = "KEEP", "완주 안정성·일관성 악화 없이 페이스 개선이 자연 편차 기준을 넘었습니다."
        elif pace_worsened or stability_status == "WORSENED":
            verdict, reason = "REVERT", "페이스 또는 완주 안정성이 악화되었습니다."
        else:
            verdict, reason = "INCONCLUSIVE", "페이스 개선이 자연 편차에서 분리되지 않아 추가 표본이 필요합니다."
    if sufficient and stability_status == "WORSENED" and verdict == "KEEP":
        verdict, reason = "REVERT", "유효 랩 페이스와 별개로 완주 성공률·오프트랙 안정성이 악화되었습니다."
    # A representative lap's input differences cannot confirm run-level comfort.
    qualifying_pace_confirmed = goal == "PACE" and session_mode == SessionMode.QUALIFYING and best_delta is not None and best_delta <= -max(0.05, natural_variability * 0.35)
    if verdict == "KEEP" and pace_status != "IMPROVED" and not qualifying_pace_confirmed and consistency_status != "IMPROVED" and stability_status != "IMPROVED" and not objective_driveability:
        verdict, reason = "INCONCLUSIVE", "대표 랩 한 개의 입력 차이만으로 운전 편의 개선을 확정할 수 없습니다."
    result = {
        "verdict": verdict,
        "reason": reason,
        "goal": goal,
        "subjective_feedback": subjective,
        "conditions_quality": conditions_quality,
        "effect_confirmed": sufficient and verdict in {"KEEP", "REVERT"},
        "objective": session_mode.value,
        "minimum_valid_laps_per_file": required_laps,
        "recommended_valid_laps_per_file": recommended_laps,
        "baseline": base,
        "candidate": cand,
        "median_delta_s": round_or_none(median_delta, 4),
        "best_delta_s": round_or_none(best_delta, 4),
        "stdev_delta_s": round_or_none(variability_delta, 4),
        "robust_variability_delta_s": round_or_none(robust_variability_delta, 4),
        "pace_slope_delta_s_per_lap": round_or_none(pace_slope_delta, 4),
        "track_temp_delta_c": round_or_none(temp_delta, 1),
        "effect_profile": effect_profile,
        "performance_axes": performance_axes,
        "ab_quality": {
            "natural_variability_s": round_or_none(natural_variability, 4),
            "effect_threshold_s": round_or_none(effect_threshold, 4),
            "effect_vs_noise_ratio": round_or_none(effect_vs_noise, 3),
            "sample_confidence": sample_confidence,
            "minimum_laps": required_laps,
            "recommended_laps": recommended_laps,
            "policy": "재현성은 통과조건이 아니라 자연 편차/효과크기 판정에 사용합니다.",
        },
    }
    if setup_evidence:
        result.update(setup_evidence)
    full_diff = (result.get("setup_diff") or {}).get("full_setup") or {}
    attribution = setup_learning_eligibility(result, full_diff)
    if attribution["logical_change_count"] != 1 or attribution["incomplete_changes"]:
        result["effect_confirmed"] = False
        if result["verdict"] in {"KEEP", "REVERT"}:
            result["observed_verdict"] = result["verdict"]
            result["verdict"] = "INCONCLUSIVE"
            result["reason"] = "관측 결과는 기록할 수 있지만, 변경 항목이 없거나 여러 항목을 바꾸면 개별 셋업 효과는 미확정입니다."
    result["learning_eligibility"] = setup_learning_eligibility(result, full_diff)
    return result


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


live_telemetry = LiveTelemetry()
# Pit facts learned from driven stops live beside the database (seeded by scripts/learn_pit_history.py).
live_telemetry.pit_knowledge = PitKnowledge(os.getenv("IRACING_PIT_KNOWLEDGE_PATH") or MEMORY_DB_PATH.parent / "pit-knowledge.json")
live_telemetry.race_history = RaceHistory(os.getenv("IRACING_RACE_HISTORY_PATH") or MEMORY_DB_PATH.parent / "race-history.json")


def last_analysed_session(identity: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The most recent analysed session with this car at this track: its best lap, track temperature and,
    where the corners of the track are confirmed, the corners that cost most. For the formation lap briefing."""
    if memory_store is None or not identity.get("car_path") or not identity.get("track_name"):
        return None
    try:
        with memory_store._connect() as db:
            row = db.execute(
                "SELECT best_lap_s, track_temp_c, recorded_at, analysis_json FROM sessions WHERE car_path=? AND track_name=? "
                "AND COALESCE(track_config,'')=? AND best_lap_s IS NOT NULL ORDER BY recorded_at DESC LIMIT 1",
                (identity["car_path"], identity["track_name"], identity.get("track_config") or "")).fetchone()
            sessions = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE car_path=? AND track_name=? AND COALESCE(track_config,'')=?",
                (identity["car_path"], identity["track_name"], identity.get("track_config") or "")).fetchone()[0]
        if not row:
            return {"sessions": sessions}        # looked, and there is nothing: not the same as being unable to look
        saved = _CORNER_DEFINITION_STORE.track_names(identity.get("track_id"), identity.get("track_config"))
        points = (saved or {}).get("points") or [] if (saved or {}).get("confirmed") else []
        losses = []
        for loss in (json.loads(row[3] or "{}").get("performance", {}).get("top_losses") or [])[:3]:
            apex = loss.get("apex_pct")
            near = min(points, key=lambda point: abs(point["pct"] - apex), default=None) if isinstance(apex, (int, float)) else None
            call = corner_names.spoken_call(near.get("number"), near.get("name"), near.get("spoken")) if near and abs(near["pct"] - apex) <= .012 else None
            if call and call not in losses:
                losses.append(call)
        return {"best_lap_s": row[0], "track_temp_c": row[1], "at": row[2], "losses": losses, "sessions": sessions}
    except Exception as exc:
        logger.warning("Could not read the last session at this track: %s", exc)
        return None


live_telemetry.history_lookup = last_analysed_session
def live_corner_definitions(identity: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    definitions = _CORNER_DEFINITION_STORE.find(identity.get("car_id"), identity.get("track_id"), identity.get("track_config"))
    if not definitions:
        return definitions
    names = _CORNER_DEFINITION_STORE.names_for(definitions, identity.get("track_id"), identity.get("track_config"))
    return [{**definition, "call": corner_names.spoken_call(**names[definition["id"]]) if definition["id"] in names else None}
            for definition in definitions]


live_telemetry.corners.lookup = live_corner_definitions


def warm_external_references() -> None:
    try:
        for path in _external_reference_paths():
            external_reference_validation(path)
    except Exception:
        logger.exception("Checking the Garage61 reference laps in the background failed")


_CORNER_SUGGESTION_DISK = JsonCache(MEMORY_DB_PATH.parent / "corner-suggestions.json", limit=1000)


def corner_names_payload(telemetry: ParsedTelemetry) -> dict[str, Any]:
    analyzer = TelemetryAnalyzer(telemetry)
    metadata = telemetry.metadata
    track_id, config = metadata.get("track_platform_id"), metadata.get("track_config")
    base = {"track_id": track_id, "track_name": metadata.get("track_name"), "track_config": config}
    if not analyzer.valid_laps:
        return {**base, "status": "NO_VALID_LAP", "corners": []}
    grid = analyzer._lap_grid(min(analyzer.valid_laps, key=lambda lap: lap.lap_time_s or math.inf))
    definitions = analyzer._detect_corners(grid)
    cache = vars(telemetry).setdefault("_analysis_cache", {})
    if "corner_suggestion" not in cache:
        # Fitting the lap onto the official map took up to 5 s on a long track; kept on disk per file.
        disk_key = JsonCache.key(telemetry.path, track_id, CornerDefinitionStore.VERSION) if telemetry.path.is_file() else None
        kept = _CORNER_SUGGESTION_DISK.get(disk_key) if disk_key else None
        if kept is None:
            kept = (corner_names.suggest(definitions, grid["distance_pct"], grid.get("Lat", []), grid.get("Lon", []), track_id)
                    if track_id and "Lat" in grid and "Lon" in grid else {"status": "NO_GPS", "corners": {}})
            if disk_key:
                _CORNER_SUGGESTION_DISK.put(disk_key, kept)
        cache["corner_suggestion"] = kept
    suggestion = cache["corner_suggestion"]
    saved = _CORNER_DEFINITION_STORE.names_for(definitions, track_id, config)
    corners = []
    for order, definition in enumerate(definitions, start=1):
        proposed, kept = suggestion["corners"].get(definition["id"], {}), saved.get(definition["id"], {})
        corners.append({"id": definition["id"], "order": order, "entry_pct": definition["entry_pct"], "apex_pct": definition["apex_pct"],
                        "exit_pct": definition["exit_pct"], "number": kept.get("number"), "name": kept.get("name"), "spoken": kept.get("spoken"),
                        "call": corner_names.spoken_call(kept.get("number"), kept.get("name"), kept.get("spoken")) if kept else None,
                        "suggested_number": proposed.get("number"), "suggested_name": proposed.get("name"), "labels": proposed.get("labels", [])})
    confirmed = bool((_CORNER_DEFINITION_STORE.track_names(track_id, config) or {}).get("confirmed"))
    return {**base, "status": "CONFIRMED" if confirmed else suggestion["status"], "suggestion_status": suggestion["status"],
            "kind": suggestion.get("kind"), "fit_median_m": suggestion.get("fit_median_m"), "confirmed": confirmed, "corners": corners}
voice_note_store = VoiceNoteStore(os.getenv("IRACING_VOICE_DB_PATH", str(MEMORY_DB_PATH)))
voice_coach = VoiceCoach(live_telemetry, voice_note_store, ai_client, GEMINI_VOICE_MODEL)


@asynccontextmanager
async def app_lifespan(_app):
    live_telemetry.start()
    # The Garage61 laps are checked in the background as the app starts, so the first file opened
    # does not wait for them; after the first run this only reads the kept answers.
    threading.Thread(target=warm_external_references, name="garage61-check", daemon=True).start()
    try:
        yield
    finally:
        live_telemetry.stop()


app = FastAPI(title="iRacing Telemetry Setup Tuner", version=APP_VERSION, lifespan=app_lifespan)
# The API can spend cloud credit and read personal notes: local screen origins only.
# CORS_ORIGINS / IRACING_API_HOST widen this deliberately (e.g. a second PC).
DEFAULT_CORS_ORIGINS = ",".join(f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (24157, 3001, 3000))


@app.exception_handler(TelemetryFileBusy)
async def telemetry_file_busy_response(_request, exc):
    return JSONResponse(status_code=409, headers={'Retry-After': '2'},
                        content={'detail': IBT_FILE_WAIT_MESSAGE, 'code': 'TELEMETRY_FILE_BUSY',
                                 'retryable': True, 'filename': exc.path.name})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Log-File-Name"],
)
register_voice_routes(app, voice_coach, resolve_telemetry_file, repository, TelemetryAnalyzer, AnalyzeRequest)


async def load_analysis(request: AnalyzeRequest) -> dict[str, Any]:
    request = request_with_structured_feedback(request)
    path = await asyncio.to_thread(resolve_telemetry_file, request.filename)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "분석 가능한 유효 완주 랩이 있는 .ibt 파일이 없습니다."
            ),
        )
    try:
        telemetry = await asyncio.to_thread(repository.load, path)
        await asyncio.to_thread(telemetry_file_visibility, path, telemetry)
        def analyze_and_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
            analyzer = TelemetryAnalyzer(telemetry)
            local_result = analyzer.analyze(request)
            return local_result, analyzer.build_memory_snapshot(local_result, request)

        result, snapshot = await asyncio.to_thread(analyze_and_snapshot)
        snapshot_session = snapshot.get("session", {})
        file_hash = str(snapshot_session.get("file_hash") or "")
        target_mode = str(snapshot_session.get("session_mode") or request.session_mode.value)
        memory_payload: dict[str, Any] = {
            "enabled": memory_store is not None,
            "saved": False,
            "saved_this_request": False,
            "persist_requested": bool(request.persist_memory),
            "replace_existing_mode": bool(request.replace_existing_mode),
            "path": str(MEMORY_DB_PATH),
            "session_id": snapshot_session.get("session_id"),
            "assigned_mode": None,
            "assigned_modes": [],
            "similar_sessions": [],
            "similar_session_count": 0,
            "current_out_lap": snapshot.get("out_lap", {}),
            "raw_telemetry_stored": False,
            "storage_policy": "COMPACT_ANALYSIS_ONLY",
        }
        if memory_store is not None:
            try:
                existing_modes = await asyncio.to_thread(memory_store.assigned_modes, file_hash)
                if (
                    request.persist_memory
                    and existing_modes
                    and target_mode not in existing_modes
                    and not request.replace_existing_mode
                ):
                    existing_labels = ", ".join(existing_modes)
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"이 텔레메트리는 이미 {existing_labels} 유형으로 DB에 저장되어 있습니다. "
                            f"{target_mode} 유형으로 변경하려면 변경 확인 후 다시 실행하십시오."
                        ),
                    )

                if request.persist_memory:
                    saved = await asyncio.to_thread(
                        memory_store.save_analysis,
                        snapshot,
                        request.replace_existing_mode,
                    )
                    memory_payload.update(saved)
                    existing_modes = await asyncio.to_thread(memory_store.assigned_modes, file_hash)
                    memory_payload["saved_this_request"] = True

                current_context = {
                    **snapshot_session,
                    "setup_hash": snapshot.get("setup", {}).get("setup_hash"),
                    "out_lap_aggregate": snapshot.get("out_lap", {}).get("aggregate", {}),
                }
                similar = await asyncio.to_thread(
                    memory_store.similar_sessions,
                    current_context,
                    5,
                )
                memory_payload["enabled"] = True
                memory_payload["assigned_modes"] = existing_modes
                memory_payload["assigned_mode"] = existing_modes[0] if existing_modes else None
                memory_payload["saved"] = target_mode in existing_modes
                memory_payload["similar_sessions"] = similar
                memory_payload["similar_session_count"] = len(similar)
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("Could not persist long-term memory: %s", exc)
                memory_payload["error"] = str(exc)
        result["memory"] = memory_payload
        result["setup_recommendation"] = reinforce_setup_recommendation_with_memory(
            result.get("setup_recommendation", {}) or {},
            memory_payload.get("similar_sessions", []) or [],
        )
        result["setup_recommendation"] = await asyncio.to_thread(
            materialize_directional_numeric_targets,
            result.get("setup_recommendation", {}) or {},
            memory_store,
            snapshot_session.get("car_name"),
        )
        sensitivity_profile: dict[str, Any] = {"status": "DISABLED", "event_count": 0, "settings": [], "learning_stage": "COLD_START"}
        fingerprint: dict[str, Any] = {"status": "DISABLED", "confidence": 0.0, "weaknesses": [], "strengths": []}
        if memory_store is not None:
            try:
                sensitivity_profile, fingerprint = await asyncio.gather(
                    asyncio.to_thread(
                        memory_store.setup_sensitivity_profile,
                        snapshot_session.get("car_name"),
                        snapshot_session.get("track_name"),
                    ),
                    asyncio.to_thread(
                        memory_store.driver_fingerprint,
                        snapshot_session.get("car_name"),
                        30,
                    ),
                )
            except Exception as exc:
                logger.warning("Could not build personalization profile: %s", exc)
        result["setup_recommendation"] = reinforce_setup_with_sensitivity_profile(
            result.get("setup_recommendation", {}) or {},
            sensitivity_profile,
        )
        result["setup_recommendation"] = cap_exploratory_setup_candidates(result["setup_recommendation"])
        result["memory"]["setup_sensitivity"] = sensitivity_profile
        result["memory"]["driver_fingerprint"] = fingerprint
        result["closed_loop_coach"] = build_closed_loop_coach(
            result,
            sensitivity_profile,
            fingerprint,
        )
        try:
            result["voice_review"] = await asyncio.to_thread(
                review_notes, voice_note_store, TelemetryAnalyzer(telemetry), result, request)
        except Exception:
            logger.exception("Could not link voice notes; file analysis retained")
            result["voice_review"] = {"notes": [], "pending": [], "error": "대화 기록 연결을 완료하지 못했습니다. 기록은 유지됩니다."}
        result = await add_ai_briefing(result, request)
        shared_sync_state["last_analysis_time"] = time.time()
        shared_sync_state["analysis_result"] = result
        shared_sync_state["file_signature"] = telemetry.signature
        return result
    except HTTPException:
        raise
    except PermissionError as exc:
        raise TelemetryFileBusy(path) from exc
    except IBTFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Telemetry analysis failed")
        raise HTTPException(status_code=500, detail=f"텔레메트리 분석 실패: {exc}") from exc


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "ai_enabled": ai_client is not None,
        "ai": ai_runtime_info(),
        "memory": memory_runtime_info(),
        "telemetry_dirs": [str(path) for path in TELEMETRY_DIRS],
        "file_visibility": {
            "minimum_detected_laps": MIN_DISPLAY_LAPS,
            "minimum_valid_laps": MIN_DISPLAY_VALID_LAPS,
            "scan_limit": FILE_SCAN_LIMIT,
        },
        "external_references": {
            "directory": str(EXTERNAL_REFERENCE_DIR),
            "csv_files": len(_external_reference_paths()),
            "max_selection": EXTERNAL_REFERENCE_MAX_SELECTION,
            "minimum_for_setup_conclusion": EXTERNAL_REFERENCE_MIN_DECISION,
            "aggregation": "DISTANCE_NORMALIZED_MEDIAN_CONSENSUS",
        },
    }


@app.get("/api/files")
async def get_files(limit: int = Query(default=20, ge=1, le=100), fast: bool = Query(default=True)) -> dict[str, Any]:
    files = await asyncio.to_thread(list_ibt_files, limit)
    memory_index = (
        await asyncio.to_thread(memory_store.file_analysis_index)
        if memory_store is not None
        else {}
    )
    payload_files: list[dict[str, Any]] = []
    for path in files:
        visibility = await asyncio.to_thread(quick_file_visibility if fast else telemetry_file_visibility, path)
        payload_files.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "modified_at": path.stat().st_mtime,
                **{key: visibility.get(key) for key in ("eligible", "analyzable", "comparison_ready", "coaching_ready", "clean_segment_count", "metadata_pending", "status", "reason", "error", "retryable")},
                "lap_count": visibility.get("detected_laps", 0),
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
        "memory_enabled": memory_store is not None,
        "minimum_display_laps": MIN_DISPLAY_LAPS,
        "minimum_display_valid_laps": MIN_DISPLAY_VALID_LAPS,
        "filter_policy": "SHOW_ALL_WITH_AVAILABILITY_REASON",
    }


@app.get("/api/external-references")
async def get_external_references(filename: Optional[str] = None) -> dict[str, Any]:
    path = await asyncio.to_thread(resolve_telemetry_file, filename)
    telemetry: Optional[ParsedTelemetry] = None
    if filename and path is None:
        raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
    if path is not None:
        telemetry = await asyncio.to_thread(repository.load, path)
    payload = await asyncio.to_thread(list_external_reference_metadata, telemetry)
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
            garage61_reference_health, telemetry, path.name, payload
        )
    return payload


# ---------------------------------------------------------------------------
# Settings entered on the screen: folders, the Google key, the Garage61 token
# ---------------------------------------------------------------------------


class AppSettingsBody(BaseModel):
    telemetry_dirs: Optional[list[str]] = Field(default=None, max_length=12)
    gcp_project_id: Optional[str] = Field(default=None, max_length=120)
    gcp_location: Optional[str] = Field(default=None, max_length=60)
    speech_region: Optional[str] = Field(default=None, max_length=20)


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=20000)


def _reload_settings() -> None:
    """Take the saved settings into the running app: folders, project, key and the clients built on them."""
    global TELEMETRY_DIRS, ai_client
    app_settings.apply_environment(force=True)
    TELEMETRY_DIRS = _configured_telemetry_dirs()
    ai_client = _connect_ai()
    voice_coach.ai_client = ai_client
    speech = getattr(voice_coach, "speech", None)
    if speech is not None and hasattr(speech, "_clients"):
        with speech._client_lock:
            speech._clients.clear()          # they hold the old credentials
        speech.recognition_state = speech.synthesis_state = "UNTESTED"


def app_settings_view() -> dict[str, Any]:
    settings = app_settings.load()
    added = {str(Path(item).expanduser()) for item in settings["telemetry_dirs"]}
    folders = [{"path": str(path), "exists": path.exists(), "added": str(path) in added}
               for path in TELEMETRY_DIRS if path != APP_DIR]
    token, _ = _garage61_api_token_local()
    key = app_settings.key_summary()
    radio, spotter = MEMORY_DB_PATH.parent / "radio_audio", Path(os.getenv("IRACING_SPOTTER_AUDIO_DIR") or app_settings.spotter_audio_dir())
    return {
        "version": APP_VERSION,
        "data_dir": str(MEMORY_DB_PATH.parent),
        "settings": settings,
        "telemetry_folders": folders,
        # Whose key it is may be shown; the key never leaves the data folder.
        "google": {"key_present": key["present"], "key_project_id": key["project_id"], "key_account": key["account"],
                   "project_id": os.getenv("GCP_PROJECT_ID", "") or None, "ai_enabled": ai_client is not None,
                   "ai_switched_off": os.getenv("IRACING_AI_ENABLED", "1").lower() in {"0", "false", "off"}},
        "garage61": {"token_present": bool(token)},
        "audio": {"radio_clips": sum(1 for _ in radio.glob("*.mp3")) if radio.is_dir() else 0,
                  "spotter_clips": sum(1 for _ in spotter.glob("*.mp3")) if spotter.is_dir() else 0},
    }


@app.get("/api/settings")
def get_app_settings():
    return app_settings_view()


@app.put("/api/settings")
def put_app_settings(body: AppSettingsBody):
    values = body.model_dump(exclude_none=True)
    if "telemetry_dirs" in values:
        folders = []
        for item in values["telemetry_dirs"]:
            text = item.strip().strip('"')
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                raise HTTPException(status_code=422, detail=f"폴더는 전체 경로로 입력하세요: {text}")
            if not path.is_dir():
                raise HTTPException(status_code=422, detail=f"폴더를 찾을 수 없습니다: {text}")
            folders.append(str(path))
        values["telemetry_dirs"] = folders
    for name in ("gcp_project_id", "gcp_location", "speech_region"):
        if name in values:
            values[name] = values[name].strip()
            if not re.fullmatch(r"[A-Za-z0-9._:-]*", values[name]):
                raise HTTPException(status_code=422, detail="영문·숫자·하이픈만 입력할 수 있습니다.")
    for name, fallback in (("gcp_location", "global"), ("speech_region", "us")):
        if name in values and not values[name]:
            values[name] = fallback
    app_settings.save(values)
    _reload_settings()
    return app_settings_view()


@app.put("/api/settings/google-key")
def put_google_key(body: SecretBody):
    try:
        app_settings.store_key(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _reload_settings()
    return app_settings_view()


@app.delete("/api/settings/google-key")
def delete_google_key():
    app_settings.remove_key()
    _reload_settings()
    return app_settings_view()


@app.put("/api/settings/garage61-token")
def put_garage61_token(body: SecretBody):
    token = body.value.strip().removeprefix("Authorization:").strip().removeprefix("Bearer ").strip()
    if not token or len(token) > 4000 or any(ch.isspace() for ch in token):
        raise HTTPException(status_code=422, detail="토큰 형식을 확인하세요.")
    GARAGE61_API_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    GARAGE61_API_TOKEN_FILE.write_text(token, encoding="utf-8")
    return app_settings_view()


@app.delete("/api/settings/garage61-token")
def delete_garage61_token():
    try:
        GARAGE61_API_TOKEN_FILE.unlink()
    except OSError:
        pass
    return app_settings_view()


# A driven session as a file to send to whoever improves the app (see session_log).
@app.get("/api/session-logs")
def list_session_logs(limit: int = Query(default=20, ge=1, le=100)):
    try:
        return {"sessions": session_log.sessions(voice_note_store, limit), "send_available": bool(app_settings.log_upload_url())}
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class SessionLogSend(BaseModel):
    questions: bool = False
    note: str = Field(default="", max_length=500)


@app.post("/api/session-logs/{run_id}/send")
def send_session_log(run_id: str, body: SessionLogSend):
    """Send one session's log to the maker of the app. Only ever on the driver's own click."""
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", run_id):
        raise HTTPException(status_code=422, detail="세션을 다시 선택하세요.")
    try:
        found = session_log.export(voice_note_store, run_id, APP_VERSION, include_questions=body.questions)
        if not found:
            raise HTTPException(status_code=404, detail="그 세션의 기록이 없습니다.")
        session_log.send(app_settings.log_upload_url(), found[0], found[1], body.note, APP_VERSION)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"sent": True, "name": found[0]}


@app.get("/api/session-logs/{run_id}/export")
def export_session_log(run_id: str, questions: bool = False):
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", run_id):
        raise HTTPException(status_code=422, detail="세션을 다시 선택하세요.")
    try:
        found = session_log.export(voice_note_store, run_id, APP_VERSION, include_questions=questions)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=404, detail="그 세션의 기록이 없습니다.")
    name, data = found
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Log-File-Name": name})


@app.get("/api/garage61/api/status")
async def get_garage61_api_status() -> dict[str, Any]:
    return {"status": "success", "api": await asyncio.to_thread(garage61_api_connection_status)}


@app.get("/api/garage61/collector/status")
async def get_garage61_collector_status(filename: Optional[str] = None) -> dict[str, Any]:
    path = await asyncio.to_thread(resolve_telemetry_file, filename)
    if filename and path is None:
        raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
    if path is None:
        return {
            "status": "success",
            "health": None,
            "job": await asyncio.to_thread(garage61_collector.active_job),
        }
    telemetry = await asyncio.to_thread(repository.load, path)
    references = await asyncio.to_thread(list_external_reference_metadata, telemetry)
    return await asyncio.to_thread(
        garage61_collector_status_payload, telemetry, path.name, references
    )


@app.post("/api/garage61/collector/start")
async def start_garage61_collector(payload: Garage61CollectRequest) -> dict[str, Any]:
    if not GARAGE61_AUTO_COLLECT_ENABLED:
        raise HTTPException(status_code=503, detail="Garage61 자동 수집이 비활성화되어 있습니다.")
    path = await asyncio.to_thread(resolve_telemetry_file, payload.filename)
    if path is None:
        raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
    telemetry = await asyncio.to_thread(repository.load, path)
    references = await asyncio.to_thread(list_external_reference_metadata, telemetry)
    health = await asyncio.to_thread(garage61_reference_health, telemetry, path.name, references)

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
        and float(age_hours) * 60.0 < GARAGE61_COLLECTOR_COOLDOWN_MIN
    ):
        return {
            "status": "success",
            "health": health,
            "job": {
                "state": "SKIPPED_COOLDOWN",
                "message": f"동일 조건을 최근 {GARAGE61_COLLECTOR_COOLDOWN_MIN:.0f}분 이내 수집하여 재수집을 생략했습니다.",
                "busy": False,
            },
        }

    request_payload = dict(health["context"])
    request_payload.update(
        {
            "force": bool(payload.force),
            "headless": bool(payload.headless),
            "target_count": int(payload.target_count or GARAGE61_TARGET_REFERENCES),
            "max_wall_seconds": GARAGE61_COLLECTOR_MAX_WALL_SECONDS,
            "navigation_timeout_ms": GARAGE61_NAVIGATION_TIMEOUT_MS,
            "action_timeout_ms": GARAGE61_ACTION_TIMEOUT_MS,
            "settle_ms": GARAGE61_SETTLE_MS,
            "retry_count": GARAGE61_RETRY_COUNT,
            "collector_mode": payload.collector_mode,
        }
    )
    job = await asyncio.to_thread(garage61_collector.start, request_payload)
    return {"status": "success", "health": health, "job": job}


@app.post("/api/garage61/collector/cancel")
async def cancel_garage61_collector() -> dict[str, Any]:
    job = await asyncio.to_thread(garage61_collector.cancel)
    return {"status": "success", "job": job}


@app.get("/api/status")
async def get_status(fast: bool = Query(default=True)) -> dict[str, Any]:
    if fast:
        files = await asyncio.to_thread(list_ibt_files, 1)
        path = files[0] if files else None
        visibility = await asyncio.to_thread(quick_file_visibility, path) if path else None
        return {
            "telemetry": {"file_name": path.name, "file_signature": list(file_signature(path)),
                          **visibility} if path else None,
            "shared_state": shared_state_summary(),
            "ai_enabled": ai_client is not None, "ai": ai_runtime_info(), "memory": memory_runtime_info(),
            "listing_policy": "HEADER_AND_PERSISTENT_INDEX_FIRST",
        }
    path = await asyncio.to_thread(resolve_telemetry_file)
    if path is None:
        return {
            "telemetry": None,
            "shared_state": shared_state_summary(),
            "ai_enabled": ai_client is not None,
            "ai": ai_runtime_info(),
            "memory": memory_runtime_info(),
        }

    try:
        telemetry = await asyncio.to_thread(repository.load, path)
        analyzer = TelemetryAnalyzer(telemetry)
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
            "shared_state": shared_state_summary(),
            "ai_enabled": ai_client is not None,
            "ai": ai_runtime_info(),
            "memory": memory_runtime_info(),
        }
    except PermissionError as exc:
        raise TelemetryFileBusy(path) from exc
    except Exception as exc:
        logger.exception("Status load failed")
        raise HTTPException(status_code=500, detail=f"상태 조회 실패: {exc}") from exc


@app.get("/api/channels")
async def get_channels(filename: Optional[str] = None) -> dict[str, Any]:
    path = await asyncio.to_thread(resolve_telemetry_file, filename)
    if path is None:
        raise HTTPException(status_code=404, detail=".ibt 파일이 없습니다.")
    telemetry = await asyncio.to_thread(repository.load, path)
    quality = TelemetryAnalyzer(telemetry)._data_quality()
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
    return await load_analysis(payload)


@app.post("/api/comparison-detail")
async def get_comparison_detail(payload: ComparisonDetailRequest) -> dict[str, Any]:
    path = resolve_telemetry_file(payload.filename)
    if path is None:
        raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
    telemetry = await asyncio.to_thread(repository.load, path)
    return await asyncio.to_thread(comparison_detail_payload, telemetry, payload)


@app.get("/api/corner-names")
async def get_corner_names(filename: str) -> dict[str, Any]:
    path = await asyncio.to_thread(resolve_telemetry_file, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
    telemetry = await asyncio.to_thread(repository.load, path)
    return await asyncio.to_thread(corner_names_payload, telemetry)


@app.put("/api/corner-names")
async def put_corner_names(payload: CornerNamesRequest) -> dict[str, Any]:
    path = await asyncio.to_thread(resolve_telemetry_file, payload.filename)
    if path is None:
        raise HTTPException(status_code=404, detail="선택한 IBT 파일을 찾을 수 없습니다.")
    telemetry = await asyncio.to_thread(repository.load, path)
    current = await asyncio.to_thread(corner_names_payload, telemetry)
    positions = {corner["id"]: corner["apex_pct"] for corner in current["corners"]}
    points = []
    for corner in payload.corners:
        number, name, spoken = ((value or "").strip() or None for value in (corner.number, corner.name, corner.spoken))
        if corner.id in positions and (number or name or spoken):
            points.append({"pct": positions[corner.id], "number": number, "name": name, "spoken": spoken})
    try:
        await asyncio.to_thread(_CORNER_DEFINITION_STORE.set_track_names, current["track_id"], current["track_config"], points)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(corner_names_payload, telemetry)


@app.post("/api/ai/experiment-plan")
async def create_ai_experiment_plan(payload: ExperimentPlanRequest) -> dict[str, Any]:
    return await generate_ai_experiment_plan(payload)


@app.get("/api/experiment-plans")
async def get_experiment_plans(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    if plan_store is None:
        return {"plans": [], "persistence": "DISABLED"}
    plans = await asyncio.to_thread(plan_store.recent, limit)
    return {"plans": plans, "persistence": "SQLITE"}


@app.get("/api/memory/status")
async def get_memory_status() -> dict[str, Any]:
    return memory_runtime_info()


@app.get("/api/memory/sessions")
async def get_memory_sessions(
    limit: int = Query(default=20, ge=1, le=200),
    car_name: Optional[str] = None,
    track_name: Optional[str] = None,
    session_mode: Optional[SessionMode] = None,
) -> dict[str, Any]:
    if memory_store is None:
        return {
            "sessions": [],
            "memory": memory_runtime_info(),
        }
    sessions = await asyncio.to_thread(
        memory_store.recent_sessions,
        limit,
        car_name,
        track_name,
        session_mode.value if session_mode else None,
    )
    return {
        "sessions": sessions,
        "memory": memory_runtime_info(),
    }


@app.get("/api/memory/sessions/{session_id}")
async def get_memory_session(session_id: str) -> dict[str, Any]:
    if memory_store is None:
        raise HTTPException(status_code=503, detail="장기 기억 DB가 비활성화되어 있습니다.")
    session = await asyncio.to_thread(memory_store.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="저장된 세션을 찾을 수 없습니다.")
    return {"session": session, "memory": memory_runtime_info()}


@app.post("/api/experiments/compare")
async def compare_setup_experiments(payload: ExperimentRequest) -> dict[str, Any]:
    baseline_path, candidate_path = await asyncio.gather(
        asyncio.to_thread(resolve_telemetry_file, payload.baseline_file),
        asyncio.to_thread(resolve_telemetry_file, payload.candidate_file),
    )
    if baseline_path is None or candidate_path is None:
        raise HTTPException(status_code=404, detail="기준 또는 후보 IBT 파일을 찾을 수 없습니다.")
    if baseline_path == candidate_path:
        raise HTTPException(status_code=400, detail="서로 다른 두 파일을 선택하십시오.")

    baseline_telemetry, candidate_telemetry = await asyncio.gather(
        asyncio.to_thread(repository.load, baseline_path),
        asyncio.to_thread(repository.load, candidate_path),
    )

    def comparison_identity(telemetry: ParsedTelemetry) -> tuple[str, str, str]:
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
        load_analysis(baseline_request),
        load_analysis(candidate_request),
    )
    baseline_run, candidate_run = await asyncio.gather(
        asyncio.to_thread(experiment_run_observations, baseline_telemetry),
        asyncio.to_thread(experiment_run_observations, candidate_telemetry),
    )
    for result, run in ((baseline, baseline_run), (candidate, candidate_run)):
        result["session"]["environment"] = run["environment"]
        result["session"]["track_temp_c"] = run["environment"].get("track_temp_c")
        result["ab_corner_metrics"] = run["corner_metrics"]
    setup_evidence = experiment_setup_evidence(baseline_telemetry, candidate_telemetry)
    comparison = compare_experiments(
        baseline, candidate, payload.session_mode, goal=payload.goal,
        driver_feedback=payload.driver_feedback.model_dump() if payload.driver_feedback else None,
        setup_evidence=setup_evidence,
    )
    comparison["trial_id"] = payload.trial_id
    matched_conditions = comparison["conditions_quality"]
    comparison["conditions"] = {"baseline": matched_conditions["baseline"], "candidate": matched_conditions["candidate"],
                                "differences": {item["key"]: {"baseline": item["baseline"], "candidate": item["candidate"]}
                                                for item in matched_conditions["checks"] if item["status"] == "MISMATCHED"}}
    memory_record: dict[str, Any] = {
        "saved": False,
        "path": str(MEMORY_DB_PATH),
        "reason": "장기 기억 DB가 비활성화되어 있습니다.",
    }
    if memory_store is not None:
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
                    memory_store.latest_plan_id_for_session,
                    baseline_session_id,
                )
                memory_record = await asyncio.to_thread(
                    memory_store.save_experiment_result,
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


@app.get("/api/events")
async def telemetry_events() -> StreamingResponse:
    async def event_stream() -> Any:
        last_key = previous_key = None
        while True:
            try:
                payload = await get_status(fast=True)
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
                yield f"data: {json.dumps(json_safe(payload), ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def loopback_sockets(port: int) -> list:
    """Listening sockets on 127.0.0.1 and, where the PC has IPv6, on ::1.

    The screen and the launcher ask for "localhost". Windows tries ::1 first; with the server on
    127.0.0.1 alone, every new connection waited about 0.2 s for that to fail (measured 2026-10-14).
    """
    import socket
    found = []
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((address, port))
            sock.set_inheritable(True)
            found.append(sock)
        except OSError:
            if family == socket.AF_INET:
                raise                      # the address the app has always used must be there
            sock.close()                   # no IPv6 on this PC: localhost falls back to 127.0.0.1 as before
    return found


if __name__ == "__main__":
    port = int(os.getenv("PORT", "24158"))
    host = os.getenv("IRACING_API_HOST", "").strip()
    if host:
        uvicorn.run(app, host=host, port=port)
    else:
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port)).run(sockets=loopback_sockets(port))
