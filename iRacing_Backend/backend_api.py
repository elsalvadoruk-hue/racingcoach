"""Application composition and the backward-compatible backend_api entry point.

Readers, persistence, analysis and HTTP handlers have independent modules.
Runtime service bindings keep existing integrations and patched stores consistent.
"""
from __future__ import annotations
from application_runtime import (DEFAULT_CORS_ORIGINS, telemetry_file_busy_response,
    new_analysis_state, analysis_state_summary, managed_lifespan, create_application)
from analysis_routes import AnalysisRoutesServices, register_routes as register_analysis_routes
from reference_routes import ReferenceRoutesServices, register_routes as register_reference_routes
from settings_routes import SettingsRoutesServices, register_routes as register_settings_routes
from practice_routes import PracticeRoutesServices, register_routes as register_practice_routes
from memory_routes import MemoryRoutesServices, register_routes as register_memory_routes
from workspace_routes import WorkspaceRoutesServices, register_routes as register_workspace_routes
from update_routes import UpdateRoutesServices, register_routes as register_update_routes
import app_update
from api_route_models import (
    AppSettingsBody,
    SecretBody,
    SessionLogSend,
    PracticeStartRequest,
    DrillStartRequest,
    PracticeCloseRequest,
)
from analysis_external import ExternalAnalysis
from analysis_setup import SetupAnalysis
from analysis_vehicle import VehicleAnalysis
from analysis_corners import CornerAnalysis
from handling import HandlingHistory
from analysis_laps import LapAnalysis
from racing_geometry import gps_path
from closed_loop_coach import (
    COACH_ROOT_CAUSE_META,
    _SETUP_MEMORY_KEY_HINTS,
    setup_profile_match,
    reinforce_setup_with_sensitivity_profile,
    _candidate_direction_sign,
    _outcome_direction_for_setting,
    reinforce_setup_recommendation_with_memory,
    session_objective_items,
    _metric_delta,
    detailed_corner_diagnosis,
    _fingerprint_support_for_cause,
    current_session_variability,
    build_closed_loop_coach,
)
from setup_analysis import (
    ADJUSTMENT_RULES,
    nested_get,
    extract_setup_summary,
    make_adjustment,
    make_directional_adjustment,
    setup_exploration_candidate,
    cap_exploratory_setup_candidates,
    materialize_directional_numeric_targets,
    comparison_items,
    ride_aero_items,
    setup_decision_items,
)
from reference_conditions import (
    _finite_channel_median,
    _normalize_percent,
    telemetry_reference_conditions,
    _numeric_similarity,
    _circular_direction_similarity,
    reference_condition_similarity,
    _context_token,
    _TRACK_GENERIC_WORDS,
    _track_identity,
    _TELEMETRY_TRACK_CONFIG_ALIASES,
    _resolved_telemetry_track_config,
    _identity_id,
    external_reference_compatibility,
    external_reference_compatible,
)
from analysis_config import (
    DEFAULT_GRID_POINTS,
    VISUAL_GRID_POINTS,
    EXTERNAL_REFERENCE_MAX_SELECTION,
    EXTERNAL_REFERENCE_MIN_DECISION,
    EXTERNAL_REFERENCE_STRONG_DECISION,
    GARAGE61_CONDITION_SCORE_MIN,
    RACING_LINE_COMPARE_MAX_CORNERS,
    RACING_LINE_COMPARE_MIN_LOSS_S,
    RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH,
    RACING_LINE_COMPARE_MIN_P75_GAP_M,
    RACING_LINE_COMPARE_MIN_PEAK_GAP_M,
    RACING_LINE_COMPARE_POINTS,
)
from analysis_models import (
    SessionMode,
    PitRule,
    SESSION_POLICIES,
    session_policy,
    AnalyzeRequest,
    ComparisonDetailRequest,
    OverlayExtraLap,
    LapOverlayRequest,
    request_with_structured_feedback,
    Garage61CollectRequest,
    CornerName,
    CornerNamesRequest,
    ExperimentDriverFeedback,
    ExperimentRequest,
    BriefingSectionSchema,
    AIBriefingSchema,
    ExperimentPlanRequest,
    AIPlannerSelectionSchema,
)
from telemetry_repository import (
    TelemetryRepository as _TelemetryRepository,
    ExternalReferenceRepository as _ExternalReferenceRepository,
)
from garage61_reader import Garage61ReferenceReader as _Garage61ReferenceReader
from ibt_reader import (
    IBTFormatError,
    IBT_FILE_WAIT_MESSAGE,
    TelemetryFileBusy,
    _BARE_QUESTION_MARK_VALUE,
    sanitize_iracing_session_yaml,
    IBTReader,
)
from memory_store import LongTermMemoryStore
from setup_evidence import (
    PERSONALIZATION_SETTING_META,
    canonical_setup_changes,
    setup_learning_eligibility,
    speed_class_label,
    coach_issue_label,
)
from telemetry_channels import IRSDK_DTYPES, CORE_CHANNELS, ANALYSIS_CHANNELS, FEATURE_CHANNELS
from telemetry_types import VariableHeader, LapSegment, ParsedTelemetry, ExternalReferenceLap
from analysis_numeric import (
    safe_int,
    parse_number,
    regex_value,
    round_or_none,
    format_lap_time,
    smooth,
    contiguous_groups,
    nearest_index,
    downsample_indices,
    visualization_indices,
    json_safe,
    interpolate_observed,
    _weighted_quantile_stack,
    comparison_input_channels,
    file_signature,
    canonical_hash,
    pct_distance,
    finite_values,
    mean_finite,
    min_finite,
    max_finite,
)

import asyncio
import csv
import glob
import hashlib
import json
import logging
import math
import statistics
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
from overlay_control import OverlayController, routes as overlay_routes

# The data folder and the settings entered on the screen, before anything reads its configuration.
# A database path given from outside means a test or a tool, not the driver's install (lmu_car_models scan).
_DATABASE_GIVEN = bool(os.getenv('IRACING_TUNER_DB_PATH', '').strip())
app_settings.apply_environment()

from garage61_collector_manager import Garage61CollectorManager
from analysis_support import (
    CornerDefinitionStore,
    FileMetadataIndex,
    JsonCache,
    clean_segment_context,
    mask_grid_to_clean_windows,
    session_context,
    suggested_session_mode,
)
from setup_profiles import supplement_setup_summary, vehicle_setup_capabilities
from live_telemetry import LiveTelemetry
from live_pit import PitKnowledge
from live_formation import RaceHistory
from timing_log import TimingLog
from traffic_log import TrafficLog
from practice import PracticeStore
from practice_metrics import KEPT_METRICS, success_criteria
from corner_measurements import CornerMeasurements
from drill_history import DrillHistory
import pit_service
from live_drill import drill_plan, warmup_crossings
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
APP_VERSION = "2.0.0"   # the first version made to be shared was 1.0.0 (2026-10-12); earlier numbers were internal


def _configured_external_reference_dir() -> Path:
    configured = os.getenv("IRACING_EXTERNAL_REFERENCE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return MEMORY_DB_PATH.parent / "Garage61_References"


EXTERNAL_REFERENCE_DIR = _configured_external_reference_dir()
EXTERNAL_REFERENCE_DISPLAY_LIMIT = max(3, min(EXTERNAL_REFERENCE_MAX_SELECTION, int(os.getenv("IRACING_EXTERNAL_REFERENCE_DISPLAY_LIMIT", "9"))))
EXTERNAL_REFERENCE_RECOMMENDED_COHORT = max(2, min(EXTERNAL_REFERENCE_DISPLAY_LIMIT, int(os.getenv("IRACING_EXTERNAL_REFERENCE_COHORT", "5"))))
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


# ---------------------------------------------------------------------------
# IBT parsing
# ---------------------------------------------------------------------------


# The IBT embeds current setup values, but not every Garage minimum/maximum.
# These rules validate only changes whose increments are known by this app.

# Canonical setup controls used by the closed-loop personalization engine.
# The hints match flattened iRacing setup paths already stored in SQLite.


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


# iRacing does not populate TrackConfigName for every circuit layout.  The
# Garage61 collector already resolves these exact internal track-path aliases
# before selecting a track.  The reference reader must use the same identity
# logic; otherwise a successfully downloaded CSV can be rejected immediately
# after collection (for example ``lemans full`` vs ``24 Heures du Mans``).


class Garage61ReferenceReader(_Garage61ReferenceReader):
    """Compatibility entry point using the application's configured metadata."""

    def __init__(self, path: Path):
        super().__init__(path, metadata_lookup=garage61_reference_metadata)


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


class ExternalReferenceRepository(_ExternalReferenceRepository):
    def __init__(self, max_files: int = 12):
        super().__init__(max_files, reader_factory=lambda path: Garage61ReferenceReader(path))


external_reference_repository = ExternalReferenceRepository()


def waiting_file_visibility():
    return {'eligible': False, 'analyzable': False, 'comparison_ready': False,
            'coaching_ready': False, 'status': 'WAITING_FILE', 'retryable': True,
            'metadata_pending': True, 'reason': IBT_FILE_WAIT_MESSAGE, 'error': None,
            'detected_laps': None, 'complete_laps': None, 'valid_laps': None}


# ---------------------------------------------------------------------------
# Repository/cache
# ---------------------------------------------------------------------------


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


def recording_reader(path: Path):
    """IBT files, or LMU recordings presented as parsed IBT files (lmu_ibt)."""
    if Path(path).suffix.casefold() == ".duckdb":
        from lmu_ibt import LmuIbtReader
        return LmuIbtReader(path)
    return IBTReader(path)


class TelemetryRepository(_TelemetryRepository):
    def __init__(self, max_files: int = MAX_CACHE_FILES):
        super().__init__(max_files, reader_factory=recording_reader)


repository = TelemetryRepository()

_FILE_VISIBILITY_LOCK = threading.RLock()
_FILE_VISIBILITY_CACHE: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
_FILE_METADATA_INDEX = FileMetadataIndex(Path(os.getenv("IRACING_FILE_INDEX_PATH", str(MEMORY_DB_PATH.parent / "telemetry-file-index.sqlite3"))))


def _lmu_model_car(info: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Recordings indexed before a livery's model was known still carry the livery's key."""
    if not info or not str(info.get('car_path') or '').startswith('lmu-car-') or not info.get('car_name'):
        return info
    from lmu_car_models import car_key
    car_id, name = car_key(info['car_name'])
    return {**info, 'car_path': car_id, 'car_name': name, **({'car_id': car_id} if 'car_id' in info else {})}


def cached_file_visibility(path: Path) -> Optional[dict[str, Any]]:
    signature = file_signature(path)
    with _FILE_VISIBILITY_LOCK:
        cached = _FILE_VISIBILITY_CACHE.get(signature)
    if cached is not None:
        return _lmu_model_car(dict(cached))
    try:
        return _lmu_model_car(_FILE_METADATA_INDEX.get(signature))
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
            "car_platform_id": metadata.get("car_platform_id"),
            "track_name": metadata.get("track_name") or "Unknown Track",
            "track_path": metadata.get("track_path") or "",
            "track_platform_id": metadata.get("track_platform_id"),
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
    import lmu_recordings
    if lmu_recordings.is_lmu_name(filename):
        path = lmu_recordings.resolve(filename)
        if path is not None and lmu_recordings.recording_busy(path):
            raise TelemetryFileBusy(path)
        return path
    files = _list_raw_ibt_files(None if filename else FILE_SCAN_LIMIT)
    if filename:
        wanted = Path(filename).name
        return next((path for path in files if path.name == wanted), None)
    return next((path for path in files if telemetry_file_visibility(path).get("analyzable")), None)


shared_sync_state: dict[str, Any] = new_analysis_state()


def shared_state_summary() -> dict[str, Any]:
    return analysis_state_summary(shared_sync_state)


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


_CORNER_DEFINITIONS: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()
_CORNER_DEFINITIONS_LOCK = threading.RLock()
_CORNER_DEFINITION_STORE = CornerDefinitionStore(Path(os.getenv(
    "IRACING_CORNER_DEFINITIONS_PATH", str(MEMORY_DB_PATH.parent / "corner-definitions.json"))))


def _corner_identity_persistent(identity: tuple[Any, ...]) -> bool:
    # Only real iRacing car and track ids are stable enough to keep on disk.
    return bool(identity[0] and identity[3])


class TelemetryAnalyzer(ExternalAnalysis, SetupAnalysis, VehicleAnalysis, CornerAnalysis, LapAnalysis, CornerMeasurements):
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
        self._raw_lap_cache = shared.setdefault('raw_laps', {})
        self._handling_cache = shared.setdefault('handling', {})


    def _name_corners(self, corners: list[dict[str, Any]], definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Confirmed numbers/names replace "자동 구간 N", as in analyze()."""
        metadata = self.telemetry.metadata
        names = _CORNER_DEFINITION_STORE.names_for(definitions, metadata.get("track_platform_id"), metadata.get("track_config"))
        for corner in corners:
            named = names.get(corner.get("id"))
            call = corner_names.spoken_call(named.get("number"), named.get("name"), named.get("spoken")) if named else None
            if call:
                corner.update(official_turn_number=named.get("number"), turn_name=named.get("name"), label=call)
        return corners

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
        result["performance"]["corner_consistency"] = self._corner_consistency(corners, (lap_selection or {}).get("target_lap"))
        result['handling'] = self._handling_analysis(corners)
        if result['handling'] is not None:
            try:
                history = HandlingHistory(os.getenv('IRACING_HANDLING_HISTORY_PATH') or app_settings.data_dir() / 'handling-history.json')
                result['handling']['repeated'] = history.update(self.telemetry.metadata, self.telemetry.signature, result['handling'])
            except (OSError, ValueError, TypeError, KeyError):
                logger.warning('Could not save handling history', exc_info=True)
                result['handling']['repeated'] = []
        return json_safe(result)


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


    # ------------------------------------------------------------------
    # Quality/session/laps
    # ------------------------------------------------------------------


    # Where a lap's time goes (RaceStudio 3's brake/coast/full-throttle/corner lap statistics).
    TIME_USE_BRAKE = 0.08        # brake pedal above this is braking (same as the corner analysis)
    TIME_USE_FULL = 0.95         # throttle at or above this, off the brake, is full throttle
    TIME_USE_OFF = 0.05          # throttle below this, off the brake, is coasting
    TIME_USE_CORNER_G = 0.5      # lateral acceleration above this many g is cornering (counted on its own)
    TIME_USE_GAP_S = 0.5         # a gap in the samples longer than this is not counted


    # ------------------------------------------------------------------
    # Distance-normalized laps and comparison
    # ------------------------------------------------------------------


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


    # ------------------------------------------------------------------
    # Vehicle diagnostics
    # ------------------------------------------------------------------


    # G-G diagram (RaceStudio 3's scatter layout of lateral against longitudinal acceleration).
    GG_BIN_DEG = 10                  # the grip envelope is the GG_ENVELOPE_PCT-th percentile in each direction
    GG_ENVELOPE_PCT = 98
    GG_MIN_SPEED_MS = 15.0           # standing and crawling samples say nothing about grip
    GG_SAMPLE_POINTS = 1500


    # Damper velocity histograms (RaceStudio 3's suspension analysis). Positive is compression (bump):
    # in iRacing files the velocity is the rate of the shock deflection, which grows as the nose dives.
    DAMPER_WHEELS = (("LF", "LFshockVel"), ("RF", "RFshockVel"), ("LR", "LRshockVel"), ("RR", "RRshockVel"))
    DAMPER_LIMIT_MM_S = 200          # faster than this falls into the end bins
    DAMPER_BIN_MM_S = 10
    DAMPER_SLOW_MM_S = 50            # a viewing split into slow and fast movement, not the car's damper knee


    # ------------------------------------------------------------------
    # Classification and setup rules
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Visualization and briefing
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Setup metadata/rules helpers
# ---------------------------------------------------------------------------


def comparison_detail_payload(telemetry: ParsedTelemetry, payload: ComparisonDetailRequest, *, reference_telemetry=None, suppress_local_reference=False) -> dict[str, Any]:
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
    if payload.reference_mode == 'LOCAL' and reference_telemetry is not None:
        from workspace_personal_reference import validate_personal_reference
        reference_analyzer = TelemetryAnalyzer(reference_telemetry)
        local_reference = next((lap for lap in reference_telemetry.laps if lap.lap_number == payload.reference_lap), None)
        validate_personal_reference(telemetry, target, reference_telemetry, local_reference)
        reference_grid = _on_distance_axis(reference_analyzer._lap_grid(local_reference), target_grid['distance_pct'])
    elif payload.reference_mode == 'LOCAL' and payload.reference_lap == target.lap_number and target.valid:
        local_reference, reference_grid = target, target_grid
    if suppress_local_reference and payload.reference_mode == 'LOCAL':
        local_reference, reference_grid = None, None
    distributions = {}
    support = {}
    reference_label = f"내 기준 랩 {local_reference.lap_number}" if reference_grid is not None else "비교 기준 없음"
    line_grid = reference_grid
    line_source = {"type": "LOCAL_ACTUAL_LAP", "label": reference_label, "chart_basis": "ACTUAL_LAP"} if reference_grid is not None else None
    if reference_telemetry is not None and reference_grid is not None:
        reference_label = f'내 기준 랩 {local_reference.lap_number} · {reference_telemetry.path.name}'
        line_source.update(label=reference_label, reference_file=reference_telemetry.path.name)
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


# Expert view: several laps of one file on one distance axis (MoTeC-style overlay). Values are the lap grids
# the analysis already uses (observed samples interpolated on ~2 m spacing, gaps stay None).
OVERLAY_CHANNELS = (
    ("speed", "speed_kph", "속도", "km/h", 1.0, 1),
    ("throttle", "Throttle", "스로틀", "%", 100.0, 1),
    ("brake", "Brake", "브레이크", "%", 100.0, 1),
    ("steering", "SteeringWheelAngle", "조향", "°", 180.0 / math.pi, 1),
    ("gear", "Gear", "기어", "", 1.0, 0),
    ("rpm", "RPM", "RPM", "rpm", 1.0, 0),
    ("lat_g", "LatAccel", "횡G (왼쪽 코너 +)", "g", 1.0 / 9.80665, 2),
    ("long_g", "LongAccel", "종G (가속 +)", "g", 1.0 / 9.80665, 2),
)


def _same_track(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Same track and layout. iRacing gives every layout its own track ID, so two IDs decide it alone
    (an IBT of Le Mans has an empty layout name where Garage61 writes '24 Heures du Mans'); without IDs
    the track and layout names must both match."""
    def name(value: Any) -> str:
        return " ".join(str(value or "").casefold().split())
    if left.get("track_platform_id") and right.get("track_platform_id"):
        return str(left["track_platform_id"]) == str(right["track_platform_id"])
    return name(left.get("track_name")) == name(right.get("track_name")) and name(left.get("track_config")) == name(right.get("track_config"))


def _on_distance_axis(grid: dict[str, Any], distance: np.ndarray) -> dict[str, np.ndarray]:
    """A lap grid of another file or a Garage61 lap moved onto this file's distance axis. Both grids are dense
    and even (about 2 m), so a value between two source points is interpolated only when both are measured."""
    source = np.asarray(grid["distance_pct"], dtype=float)
    if len(source) == len(distance) and np.allclose(source, distance):
        return grid
    right = np.clip(np.searchsorted(source, distance, side="left"), 1, len(source) - 1)
    left = right - 1
    span = np.maximum(source[right] - source[left], 1e-12)
    weight = np.clip((distance - source[left]) / span, 0.0, 1.0)
    moved: dict[str, np.ndarray] = {"distance_pct": distance}
    for key, values in grid.items():
        if key == "distance_pct" or key.startswith("_") or not isinstance(values, np.ndarray) or len(values) != len(source):
            continue
        values = np.asarray(values, dtype=float)
        a, b = values[left], values[right]
        stepped = key in {"Gear", "BrakeABSactive"}
        out = np.where(weight < 0.5, a, b) if stepped else a + (b - a) * weight
        out[~(np.isfinite(a) & np.isfinite(b))] = np.nan
        out[(distance < source[0]) | (distance > source[-1])] = np.nan
        moved[key] = out
    return moved


def _file_label(path: Path) -> str:
    """'mclaren720sgt3_roadamerica full 2026-07-24 18-38-59.ibt' -> '07-24 18:38'."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})", path.stem)
    return f"{match[2]}-{match[3]} {match[4]}:{match[5]}" if match else path.stem


def lap_overlay_payload(telemetry: ParsedTelemetry, payload: LapOverlayRequest) -> dict[str, Any]:
    if payload.end_pct <= payload.start_pct:
        raise HTTPException(status_code=422, detail="end_pct는 start_pct보다 커야 합니다.")
    analyzer = TelemetryAnalyzer(telemetry)
    by_number = {lap.lap_number: lap for lap in telemetry.laps if lap.lap_time_s}
    own = [by_number[number] for number in dict.fromkeys(payload.laps) if number in by_number]
    if not own and not payload.extra:
        raise HTTPException(status_code=422, detail="겹쳐 볼 수 있는 완주 랩이 없습니다.")
    # Own laps keep their number as the key, so the screen's lap chips stay as they were.
    entries: list[dict[str, Any]] = [{"key": str(lap.lap_number), "lap": lap.lap_number, "source": "OWN", "file": None,
                                      "label": f"랩 {lap.lap_number}", "lap_time_s": lap.lap_time_s, "valid": bool(lap.valid),
                                      "grid": analyzer._lap_grid(lap)} for lap in own]
    # A lap that cannot be shown is left out with its reason; the other laps are still drawn.
    skipped: list[dict[str, Any]] = []
    for extra in payload.extra:
        key = f"ibt:{extra.filename}#{extra.lap}" if extra.source == "IBT" else f"g61:{extra.filename}"
        if extra.source == "IBT":
            path = resolve_telemetry_file(extra.filename)
            if path is None:
                skipped.append({"key": key, "reason": "주행 파일을 찾을 수 없습니다."})
                continue
            other = repository.load(path)
            if not _same_track(other.metadata, telemetry.metadata):
                skipped.append({"key": key, "reason": "같은 트랙·레이아웃의 주행 파일이 아닙니다."})
                continue
            lap = next((item for item in other.laps if item.lap_number == extra.lap and item.lap_time_s), None)
            if lap is None:
                skipped.append({"key": key, "reason": f"그 파일에 완주한 랩 {extra.lap}이 없습니다."})
                continue
            entries.append({"key": f"ibt:{path.name}#{lap.lap_number}", "lap": lap.lap_number, "source": "IBT", "file": path.name,
                            "label": f"{_file_label(path)} · 랩 {lap.lap_number}", "lap_time_s": lap.lap_time_s,
                            "valid": bool(lap.valid), "grid": TelemetryAnalyzer(other)._lap_grid(lap),
                            "car_name": other.metadata.get("car_name")})
        else:
            path = resolve_external_reference_file(extra.filename)
            if path is None:
                skipped.append({"key": key, "reason": "Garage61 랩을 찾을 수 없습니다."})
                continue
            reference = external_reference_repository.load(path)
            if not _same_track(reference.metadata, telemetry.metadata):
                skipped.append({"key": key, "reason": "같은 트랙·레이아웃의 Garage61 랩이 아닙니다."})
                continue
            entries.append({"key": f"g61:{path.name}", "lap": None, "source": "GARAGE61", "file": path.name,
                            "label": f"Garage61 · {reference.metadata.get('driver_name') or '드라이버'}",
                            "lap_time_s": reference.metadata.get("lap_time_s"), "valid": bool(reference.metadata.get("clean", True)),
                            "grid": reference.grid(analyzer.grid_points, analyzer.track_length_m),
                            "car_name": reference.metadata.get("car_name")})
    if not entries:
        raise HTTPException(status_code=422, detail=(skipped[0]["reason"] if skipped else "겹쳐 볼 수 있는 완주 랩이 없습니다."))
    keys = {entry["key"] for entry in entries}
    base = next((entry for entry in entries if entry["key"] == payload.base_key), None)
    if base is None and payload.base_lap is not None and str(payload.base_lap) in keys:
        base = next(entry for entry in entries if entry["key"] == str(payload.base_lap))
    if base is None:
        ranked = [entry for entry in entries if entry["source"] == "OWN" and entry["valid"]] or entries
        base = min(ranked, key=lambda entry: entry["lap_time_s"] or math.inf)
    distance = np.asarray(base["grid"]["distance_pct"], dtype=float)
    for entry in entries:
        entry["grid"] = _on_distance_axis(entry["grid"], distance)
    start = int(np.searchsorted(distance, payload.start_pct, side="left"))
    end = min(len(distance) - 1, int(np.searchsorted(distance, payload.end_pct, side="right")))
    indices = visualization_indices(base["grid"], None, payload.points, start, end)
    base_time = np.asarray(base["grid"]["time_s"], dtype=float)

    def column(values: Optional[np.ndarray], scale: float, digits: int) -> Optional[list[Optional[float]]]:
        if values is None:
            return None
        picked = np.asarray(values, dtype=float)[indices] * scale
        if not np.any(np.isfinite(picked)):
            return None
        return [round_or_none(float(value), digits) for value in picked]

    series: dict[str, dict[str, Any]] = {}
    available: set[str] = set()
    for entry in entries:
        grid = entry["grid"]
        values_by_channel: dict[str, Any] = {}
        for key, channel, _label, _unit, scale, digits in OVERLAY_CHANNELS:
            values = column(grid.get(channel), scale, digits)
            if values is not None:
                values_by_channel[key] = values
                available.add(key)
        delta = column(np.asarray(grid["time_s"], dtype=float) - base_time, 1.0, 3)
        if delta is not None:
            values_by_channel["delta"] = delta
            available.add("delta")
        series[entry["key"]] = values_by_channel
    channels = [{"key": "delta", "label": f"시간 차이 ({base['label']} 기준, + 느림)", "unit": "s"}] if "delta" in available else []
    channels += [{"key": key, "label": label, "unit": unit} for key, _c, label, unit, _s, _d in OVERLAY_CHANNELS if key in available]
    return json_safe({
        "base_lap": base["lap"] if base["source"] == "OWN" else None,
        "base_key": base["key"],
        "laps": [{key: entry.get(key) for key in ("key", "lap", "source", "file", "label", "valid", "car_name")}
                 | {"lap_time_s": round_or_none(entry["lap_time_s"], 3)} for entry in entries],
        "distance_pct": [round(float(distance[index]), 6) for index in indices],
        "track_length_m": round_or_none(analyzer.track_length_m, 1),
        "channels": channels,
        "series": series,
        "skipped": skipped,
        "start_pct": payload.start_pct, "end_pct": payload.end_pct,
    })


def file_laps_payload(telemetry: ParsedTelemetry) -> dict[str, Any]:
    """The finished laps of a file, for picking one to overlay."""
    return {"laps": [{"lap_number": lap.lap_number, "lap_time_s": round_or_none(lap.lap_time_s, 3), "valid": bool(lap.valid),
                      "status": lap.status, "complete": bool(lap.complete), "invalid_reason": lap.invalid_reason}
                     for lap in telemetry.laps if lap.lap_time_s],
            "track_name": telemetry.metadata.get("track_name"), "track_config": telemetry.metadata.get("track_config"),
            "car_name": telemetry.metadata.get("car_name")}


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
    """Keep legacy callers local after retiring non-voice AI requests."""
    result["ai_usage"] = ai_usage_record(
        purpose="ENGINEER_BRIEFING",
        requested=request.include_ai,
        status="REMOVED" if request.include_ai else "NOT_REQUESTED",
        error="음성 외 AI 분석 요청은 종료되었습니다. 로컬 분석 결과를 제공합니다." if request.include_ai else None,
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
    """Retired endpoint: existing plans remain readable in the local store."""
    raise HTTPException(
        status_code=410,
        detail="음성 외 AI 분석 요청은 종료되었습니다. 로컬 셋업 후보와 A/B 비교를 사용하세요.",
    )


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


# One live connection for whichever sim runs: iRacing, or LMU through its SDK adapter.
from game_sdk import default_game_sdk
live_telemetry = LiveTelemetry(sdk_factory=default_game_sdk if os.name == 'nt' else None)
# Pit facts learned from driven stops live beside the database (seeded by scripts/learn_pit_history.py).
live_telemetry.pit_knowledge = PitKnowledge(os.getenv("IRACING_PIT_KNOWLEDGE_PATH") or MEMORY_DB_PATH.parent / "pit-knowledge.json")
live_telemetry.race_history = RaceHistory(os.getenv("IRACING_RACE_HISTORY_PATH") or MEMORY_DB_PATH.parent / "race-history.json")
live_telemetry.timing_log = TimingLog(os.getenv("IRACING_TIMING_LOG_DIR") or MEMORY_DB_PATH.parent / "timing-logs")
live_telemetry.traffic_log = TrafficLog(os.getenv("IRACING_TRAFFIC_LOG_DIR") or MEMORY_DB_PATH.parent / "traffic-logs")
# One driving focus at a time, checked on the next run of the same car/track/layout (practice.py).
practice_store = PracticeStore(os.getenv("IRACING_PRACTICE_PATH") or MEMORY_DB_PATH.parent / "practice-tasks.json")
live_telemetry.drill.history = DrillHistory(os.getenv('IRACING_DRILL_HISTORY_PATH') or MEMORY_DB_PATH.parent / 'practice-drills.jsonl')


def refresh_live_practice():
    """Copy persisted task configuration; no store reads on the SDK thread."""
    from copy import deepcopy
    with practice_store.lock:
        tasks = deepcopy([task for task in practice_store.data['tasks'] if task.get('status') == 'ACTIVE'])
    with live_telemetry.lock:
        active_ids = {task['id'] for task in tasks}
        if live_telemetry.drill.plan and live_telemetry.drill.plan.get('task_id') not in active_ids:
            live_telemetry.drill.stop('TASK_CHANGED')
        live_telemetry.practice.configure(tasks, live_telemetry.drill.history)
        live_telemetry.practice.select(live_telemetry.identity, live_telemetry.run_id)


refresh_live_practice()


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
    if not definitions and identity.get("game") == "lmu":
        import lmu_corner_registry
        return lmu_corner_registry.lookup(identity)
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
            # LMU recordings fit their path to the matching iRacing map (lmu_ibt.official_track_id).
            map_id = str(metadata.get("official_track_id") or track_id or "")
            kept = (corner_names.suggest(definitions, grid["distance_pct"], grid.get("Lat", []), grid.get("Lon", []), map_id)
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
    base["official_track_id"] = metadata.get("official_track_id")
    return {**base, "status": "CONFIRMED" if confirmed else suggestion["status"], "suggestion_status": suggestion["status"],
            "kind": suggestion.get("kind"), "fit_median_m": suggestion.get("fit_median_m"), "confirmed": confirmed, "corners": corners}
voice_note_store = VoiceNoteStore(os.getenv("IRACING_VOICE_DB_PATH", str(MEMORY_DB_PATH)))
voice_coach = VoiceCoach(live_telemetry, voice_note_store, ai_client, GEMINI_VOICE_MODEL)
from overlay_reference import OverlayReference, GridReference


def load_task_overlay_reference(selection):
    """Recreate the exact analysis reference, on the overlay worker only."""
    from practice import identity as practice_identity
    path = resolve_telemetry_file(selection.get('file') or '')
    telemetry = repository.load(path)
    if selection.get('identity') != practice_identity(telemetry.metadata):
        raise ValueError('과제 기준 파일의 차량·트랙이 다릅니다.')
    analyzer = TelemetryAnalyzer(telemetry)
    if selection.get('source') == 'PERSONAL':
        lap = next((lap for lap in analyzer.valid_laps if lap.lap_number == selection.get('lap')), None)
        if lap is None:
            raise ValueError('과제의 내 기준 랩을 찾지 못했습니다.')
        return GridReference(analyzer._lap_grid(lap), lap.lap_time_s)
    files = selection.get('files') or []
    target = next((lap for lap in telemetry.laps if lap.lap_number == selection.get('target_lap')), None)
    if not files or target is None:
        raise ValueError('과제의 Garage61 기준 정보가 없습니다.')
    external = analyzer._external_reference_analysis(target, files)
    if (external.get('status') != 'READY' or external.get('selected_files') != files
            or external.get('selected_reference_count') != len(files) or external.get('errors')):
        raise ValueError('과제에서 선택한 Garage61 기준을 모두 읽지 못했습니다.')
    grid = getattr(analyzer, '_external_detail_grid', None)
    if grid is None:
        raise ValueError('과제의 Garage61 비교 곡선이 없습니다.')
    return GridReference(grid, external['aggregate']['reference_median_time_s'])

overlay_controller = OverlayController(reference=OverlayReference(
    EXTERNAL_REFERENCE_DIR, lambda path: Garage61ReferenceReader(path).parse(), pinned_loader=load_task_overlay_reference))


@asynccontextmanager
async def app_lifespan(_app):
    # Sends finished drives only while the driver's consent is recorded (session_log_auto).
    from session_log_auto import AutoUploader
    uploader = AutoUploader(voice_note_store, APP_VERSION, current_run=lambda: live_telemetry.run_id)
    # LMU: one car per model, whatever the livery. Before the live coach and the stores load.
    # Tests run on a temporary database; they never read the driver's recordings unless given a folder.
    scan = not _DATABASE_GIVEN or bool(os.getenv("LMU_TELEMETRY_DIRS"))
    try:
        import lmu_car_models
        from lmu_telemetry import list_lmu_telemetry_files   # includes the imported recordings
        moved = await asyncio.to_thread(lmu_car_models.prepare, MEMORY_DB_PATH.parent,
                                        list_lmu_telemetry_files() if scan else [])
        if moved:
            logger.info('LMU saved data moved from livery to model keys: %s', moved)
    except Exception:
        logger.exception('LMU car models were not prepared; liveries stay separate this run')
    async with managed_lifespan(live_telemetry, overlay_controller, lambda: warm_background()):
        uploader.start()
        try:
            yield
        finally:
            uploader.stop()


app = create_application(APP_VERSION, app_lifespan, overlay_controller)
register_voice_routes(app, voice_coach, resolve_telemetry_file, repository, TelemetryAnalyzer, AnalyzeRequest)


async def load_analysis(request: AnalyzeRequest, *, update_current_analysis: bool = True) -> dict[str, Any]:
    """Analyze one file; internal A/B calculations leave the displayed analysis and practice untouched."""
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
        # The automatic lap library is independent of manually assigned analysis
        # session types. It stores compact observed conditions, never raw samples.
        try:
            from workspace_personal_reference import index_telemetry
            await asyncio.to_thread(index_telemetry, telemetry)
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning('Personal reference indexing failed: %s', exc)
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
        result["practice_focuses"] = practice_focus_sets(result, sensitivity_profile, fingerprint)
        result["pit_service"] = await asyncio.to_thread(pit_service_payload, telemetry, path.name)
        if update_current_analysis:
            try:
                result["practice"] = await asyncio.to_thread(practice_store.record, result)
                refresh_live_practice()
            except Exception:
                logger.exception("Could not check the practice focus; file analysis retained")
                result["practice"] = {"active": None, "check": None, "history": {"by_cause": [], "recent": []}, "error": "연습 과제 확인을 완료하지 못했습니다."}
        try:
            result["voice_review"] = await asyncio.to_thread(
                review_notes, voice_note_store, TelemetryAnalyzer(telemetry), result, request)
        except Exception:
            logger.exception("Could not link voice notes; file analysis retained")
            result["voice_review"] = {"notes": [], "pending": [], "error": "대화 기록 연결을 완료하지 못했습니다. 기록은 유지됩니다."}
        result = await add_ai_briefing(result, request)
        if update_current_analysis:
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


# ---------------------------------------------------------------------------
# Settings entered on the screen: folders, the Google key, the Garage61 token
# ---------------------------------------------------------------------------


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


# A driven session as a file to send to whoever improves the app (see session_log).


def pit_service_payload(telemetry: ParsedTelemetry, file_name: str) -> dict[str, Any]:
    """Concurrent or sequential tyre changes, as this file's stops showed (pit_service). An official series
    keeps what it showed, so the pit wall can plan tyres in its next race without being told."""
    try:
        found = pit_service.detect(telemetry.channels)
    except Exception:
        logger.exception("Could not read the pit stops of %s", file_name)
        return {"rule": None, "stops": [], "reason": "ERROR", "series_id": None, "remembered": None}
    series_id = str((telemetry.session_info.get("WeekendInfo") or {}).get("SeriesID") or "").strip()
    series_id = None if series_id in ("", "0") else series_id
    knowledge = live_telemetry.pit_knowledge
    if found["rule"] and series_id:
        knowledge.remember_series_rule(series_id, found["rule"], file_name, len(found["stops"]))
    return {**found, "series_id": series_id, "remembered": knowledge.series_rule(series_id) if series_id else None}


def practice_focus_sets(result: dict[str, Any], sensitivity_profile: dict[str, Any], fingerprint: dict[str, Any]) -> dict[str, list]:
    """Practice focuses ranked against the driver's own reference lap and, when this analysis has them,
    against the Garage61 fast laps, so the driver can pick which the "one thing" is measured against."""
    coach = (result.get("closed_loop_coach") or {}).get("ranked_focuses") or []
    if not (result.get("external_reference") or {}).get("top_losses"):
        return {"PERSONAL": coach, "GARAGE61": []}
    personal = build_closed_loop_coach({**result, "external_reference": {}}, sensitivity_profile, fingerprint)
    return {"PERSONAL": personal.get("ranked_focuses") or [], "GARAGE61": coach}


def drill_lap_time(result: dict[str, Any]) -> Optional[float]:
    """The lap the task was set on, else the median valid lap: it decides how long the tyres take to warm."""
    target = (result.get("comparison") or {}).get("target_time_s")
    if isinstance(target, (int, float)) and target > 0:
        return float(target)
    times = [lap.get("lap_time_s") for lap in result.get("laps") or [] if lap.get("status") == "VALID"]
    times = [value for value in times if isinstance(value, (int, float)) and value > 0]
    return float(statistics.median(times)) if times else None


def drill_fuel(result: dict[str, Any], filename: str, warm_laps: int = 2) -> Optional[dict[str, Any]]:
    """Fuel to put in for the drill. Each run back to the saved point restores the fuel it was saved with,
    so the attempts run on what is in the tank at the save: aim for the task lap's fuel there, which means
    that lap's starting fuel plus the warm-up laps (whole litres, rounded up). None when unknown."""
    target_lap = (result.get("comparison") or {}).get("target_lap")
    used = [lap.get("fuel_used_l") for lap in result.get("laps") or [] if lap.get("status") == "VALID"]
    used = [value for value in used if isinstance(value, (int, float)) and value > 0]
    path = resolve_telemetry_file(filename)
    if target_lap is None or not used or path is None:
        return None
    telemetry = repository.load(path)
    lap = next((item for item in telemetry.laps if item.lap_number == target_lap), None)
    fuel = telemetry.channels.get("FuelLevel")
    if lap is None or fuel is None:
        return None
    start = float(fuel[lap.start_idx])
    if not math.isfinite(start) or start <= 0:
        return None
    per_lap = float(statistics.median(used))
    return {"lap": target_lap, "lap_start_l": round(start, 1), "per_lap_l": round(per_lap, 2),
            "warm_laps": warm_laps, "suggest_l": int(math.ceil(start + warm_laps * per_lap))}


_handlers = register_analysis_routes(app, AnalysisRoutesServices(
    get_app_version=lambda: APP_VERSION,
    get_external_reference_dir=lambda: EXTERNAL_REFERENCE_DIR,
    get_external_reference_max_selection=lambda: EXTERNAL_REFERENCE_MAX_SELECTION,
    get_external_reference_min_decision=lambda: EXTERNAL_REFERENCE_MIN_DECISION,
    get_file_scan_limit=lambda: FILE_SCAN_LIMIT,
    get_min_display_laps=lambda: MIN_DISPLAY_LAPS,
    get_min_display_valid_laps=lambda: MIN_DISPLAY_VALID_LAPS,
    get_telemetry_dirs=lambda: TELEMETRY_DIRS,
    get_telemetry_analyzer=lambda: TelemetryAnalyzer,
    get_telemetry_file_busy=lambda: TelemetryFileBusy,
    _external_reference_paths=lambda *args, **kwargs: _external_reference_paths(*args, **kwargs),
    get_ai_client=lambda: ai_client,
    ai_runtime_info=lambda *args, **kwargs: ai_runtime_info(*args, **kwargs),
    comparison_detail_payload=lambda *args, **kwargs: comparison_detail_payload(*args, **kwargs),
    file_laps_payload=lambda *args, **kwargs: file_laps_payload(*args, **kwargs),
    file_signature=lambda *args, **kwargs: file_signature(*args, **kwargs),
    get_status=lambda *args, **kwargs: get_status(*args, **kwargs),
    json_safe=lambda *args, **kwargs: json_safe(*args, **kwargs),
    lap_overlay_payload=lambda *args, **kwargs: lap_overlay_payload(*args, **kwargs),
    list_ibt_files=lambda *args, **kwargs: list_ibt_files(*args, **kwargs),
    load_analysis=lambda *args, **kwargs: load_analysis(*args, **kwargs),
    memory_runtime_info=lambda *args, **kwargs: memory_runtime_info(*args, **kwargs),
    get_memory_store=lambda: memory_store,
    quick_file_visibility=lambda *args, **kwargs: quick_file_visibility(*args, **kwargs),
    get_repository=lambda: repository,
    resolve_telemetry_file=lambda *args, **kwargs: resolve_telemetry_file(*args, **kwargs),
    shared_state_summary=lambda *args, **kwargs: shared_state_summary(*args, **kwargs),
    telemetry_file_visibility=lambda *args, **kwargs: telemetry_file_visibility(*args, **kwargs),
))
health = _handlers.health
get_files = _handlers.get_files
get_status = _handlers.get_status
get_channels = _handlers.get_channels
analyze_telemetry = _handlers.analyze_telemetry
get_lap_overlay = _handlers.get_lap_overlay
get_file_laps = _handlers.get_file_laps
get_comparison_detail = _handlers.get_comparison_detail
telemetry_events = _handlers.telemetry_events


_handlers = register_reference_routes(app, ReferenceRoutesServices(
    get_garage61_action_timeout_ms=lambda: GARAGE61_ACTION_TIMEOUT_MS,
    get_garage61_auto_collect_enabled=lambda: GARAGE61_AUTO_COLLECT_ENABLED,
    get_garage61_collector_cooldown_min=lambda: GARAGE61_COLLECTOR_COOLDOWN_MIN,
    get_garage61_collector_max_wall_seconds=lambda: GARAGE61_COLLECTOR_MAX_WALL_SECONDS,
    get_garage61_navigation_timeout_ms=lambda: GARAGE61_NAVIGATION_TIMEOUT_MS,
    get_garage61_retry_count=lambda: GARAGE61_RETRY_COUNT,
    get_garage61_settle_ms=lambda: GARAGE61_SETTLE_MS,
    get_garage61_target_references=lambda: GARAGE61_TARGET_REFERENCES,
    get_parsed_telemetry=lambda: ParsedTelemetry,
    garage61_api_connection_status=lambda *args, **kwargs: garage61_api_connection_status(*args, **kwargs),
    get_garage61_collector=lambda: garage61_collector,
    garage61_collector_status_payload=lambda *args, **kwargs: garage61_collector_status_payload(*args, **kwargs),
    garage61_reference_health=lambda *args, **kwargs: garage61_reference_health(*args, **kwargs),
    list_external_reference_metadata=lambda *args, **kwargs: list_external_reference_metadata(*args, **kwargs),
    get_repository=lambda: repository,
    resolve_telemetry_file=lambda *args, **kwargs: resolve_telemetry_file(*args, **kwargs),
))
get_external_references = _handlers.get_external_references
get_garage61_api_status = _handlers.get_garage61_api_status
get_garage61_collector_status = _handlers.get_garage61_collector_status
start_garage61_collector = _handlers.start_garage61_collector
cancel_garage61_collector = _handlers.cancel_garage61_collector


_handlers = register_settings_routes(app, SettingsRoutesServices(
    get_app_version=lambda: APP_VERSION,
    get_garage61_api_token_file=lambda: GARAGE61_API_TOKEN_FILE,
    _reload_settings=lambda *args, **kwargs: _reload_settings(*args, **kwargs),
    app_settings_view=lambda *args, **kwargs: app_settings_view(*args, **kwargs),
    get_voice_note_store=lambda: voice_note_store,
))
get_app_settings = _handlers.get_app_settings
put_app_settings = _handlers.put_app_settings
put_google_key = _handlers.put_google_key
delete_google_key = _handlers.delete_google_key
put_garage61_token = _handlers.put_garage61_token
delete_garage61_token = _handlers.delete_garage61_token
list_session_logs = _handlers.list_session_logs
send_session_log = _handlers.send_session_log
export_session_log = _handlers.export_session_log


# The update window: the program from the GitHub releases and the voice clips this version needs.
UVICORN_SERVER = None                      # set when run as the app, so a restart can stop it cleanly
restart_requested = threading.Event()


def voice_folders() -> dict[str, Path]:
    """Where the packed voice paths live in this installation, as the coach and spotter look them up."""
    return {"data/radio_audio/": Path(os.getenv("IRACING_RADIO_AUDIO_DIR") or MEMORY_DB_PATH.parent / "radio_audio"),
            "data/spotter_audio/": Path(os.getenv("IRACING_SPOTTER_AUDIO_DIR") or app_settings.spotter_audio_dir())}


def request_restart() -> bool:
    """Stop the server so the launcher that supervises it applies the staged update and starts it again."""
    if os.getenv(app_update.RESTART_ENV) != "1" or UVICORN_SERVER is None:
        return False
    restart_requested.set()
    # The reply reaches the screen first; an open event stream must not keep the old version running.
    threading.Timer(.5, lambda: setattr(UVICORN_SERVER, "should_exit", True)).start()
    threading.Timer(15, lambda: os._exit(app_update.RESTART_EXIT)).start()
    return True


app_updater = app_update.AppUpdater(APP_VERSION)
voice_sync = app_update.VoiceSync(voice_folders)
get_update_status = register_update_routes(app, UpdateRoutesServices(
    app_updater=app_updater, voice_sync=voice_sync, request_restart=request_restart))


_handlers = register_practice_routes(app, PracticeRoutesServices(
    get_corner_definition_store=lambda: _CORNER_DEFINITION_STORE,
    corner_names_payload=lambda *args, **kwargs: corner_names_payload(*args, **kwargs),
    drill_fuel=lambda *args, **kwargs: drill_fuel(*args, **kwargs),
    drill_lap_time=lambda *args, **kwargs: drill_lap_time(*args, **kwargs),
    get_live_telemetry=lambda: live_telemetry,
    get_practice_store=lambda: practice_store,
    refresh_live_practice=lambda *args, **kwargs: refresh_live_practice(*args, **kwargs),
    get_repository=lambda: repository,
    resolve_telemetry_file=lambda *args, **kwargs: resolve_telemetry_file(*args, **kwargs),
    get_shared_sync_state=lambda: shared_sync_state,
    warmup_crossings=lambda *args, **kwargs: warmup_crossings(*args, **kwargs),
))
start_practice = _handlers.start_practice
start_drill = _handlers.start_drill
skip_drill_warmup = _handlers.skip_drill_warmup
drill_status = _handlers.drill_status
stop_drill = _handlers.stop_drill
practice_drills = _handlers.practice_drills
close_practice = _handlers.close_practice
get_corner_names = _handlers.get_corner_names
put_corner_names = _handlers.put_corner_names


_handlers = register_memory_routes(app, MemoryRoutesServices(
    get_memory_db_path=lambda: MEMORY_DB_PATH,
    get_parsed_telemetry=lambda: ParsedTelemetry,
    compare_experiments=lambda *args, **kwargs: compare_experiments(*args, **kwargs),
    experiment_run_observations=lambda *args, **kwargs: experiment_run_observations(*args, **kwargs),
    experiment_setup_evidence=lambda *args, **kwargs: experiment_setup_evidence(*args, **kwargs),
    generate_ai_experiment_plan=lambda *args, **kwargs: generate_ai_experiment_plan(*args, **kwargs),
    load_analysis=lambda *args, **kwargs: load_analysis(*args, **kwargs),
    memory_runtime_info=lambda *args, **kwargs: memory_runtime_info(*args, **kwargs),
    get_memory_store=lambda: memory_store,
    get_plan_store=lambda: plan_store,
    get_repository=lambda: repository,
    resolve_telemetry_file=lambda *args, **kwargs: resolve_telemetry_file(*args, **kwargs),
))
create_ai_experiment_plan = _handlers.create_ai_experiment_plan
get_experiment_plans = _handlers.get_experiment_plans
get_memory_status = _handlers.get_memory_status
get_memory_sessions = _handlers.get_memory_sessions
get_memory_session = _handlers.get_memory_session
compare_setup_experiments = _handlers.compare_setup_experiments


_handlers = register_workspace_routes(app, WorkspaceRoutesServices(
    get_telemetry_analyzer=lambda: TelemetryAnalyzer,
    get_ibt_reader=lambda: IBTReader,
    get_repository=lambda: repository,
    get_external_reference_repository=lambda: external_reference_repository,
    resolve_telemetry_file=lambda *args, **kwargs: resolve_telemetry_file(*args, **kwargs),
    resolve_external_reference_file=lambda *args, **kwargs: resolve_external_reference_file(*args, **kwargs),
    telemetry_file_visibility=lambda *args, **kwargs: telemetry_file_visibility(*args, **kwargs),
    quick_file_visibility=lambda *args, **kwargs: quick_file_visibility(*args, **kwargs),
    list_ibt_files=lambda *args, **kwargs: list_ibt_files(*args, **kwargs),
    list_external_reference_metadata=lambda *args, **kwargs: list_external_reference_metadata(*args, **kwargs),
    comparison_detail_payload=lambda *args, **kwargs: comparison_detail_payload(*args, **kwargs),
    file_laps_payload=lambda *args, **kwargs: file_laps_payload(*args, **kwargs),
    lap_overlay_payload=lambda *args, **kwargs: lap_overlay_payload(*args, **kwargs),
    load_analysis=lambda *args, **kwargs: load_analysis(*args, **kwargs),
    compare_setup_experiments=lambda *args, **kwargs: compare_setup_experiments(*args, **kwargs),
    build_closed_loop_coach=lambda *args, **kwargs: build_closed_loop_coach(*args, **kwargs),
    extract_setup_summary=lambda *args, **kwargs: extract_setup_summary(*args, **kwargs),
    setup_exploration_candidate=lambda *args, **kwargs: setup_exploration_candidate(*args, **kwargs),
    json_safe=lambda *args, **kwargs: json_safe(*args, **kwargs),
    align_reference_grid=lambda *args, **kwargs: _on_distance_axis(*args, **kwargs),
))
workspace_request = _handlers.workspace_request
# LMU recordings run through the same analysis workspace (lmu_routes).
app.state.workspace_request = workspace_request


def import_lmu_corner_names() -> int:
    """Carry corner numbers confirmed on the earlier LMU screen into the shared store."""
    from lmu_irsdk import stable_id
    legacy = Path(__file__).resolve().parent.parent / "logs" / "lmu-corner-names.json"
    try:
        tracks = json.loads(legacy.read_text(encoding="utf-8")).get("tracks") or {}
    except (OSError, ValueError):
        return 0
    imported = 0
    for entry in tracks.values():
        name, layout = entry.get("track_name"), entry.get("track_layout")
        if not entry.get("confirmed") or not name or (layout and layout != name):
            continue
        track_id = stable_id("track", name)
        if _CORNER_DEFINITION_STORE.track_names(track_id, ""):
            continue
        points = [{"pct": point["apex_pct"], "number": point.get("number"), "name": point.get("name"),
                   "spoken": point.get("spoken")} for point in entry.get("points") or []
                  if isinstance(point.get("apex_pct"), (int, float))]
        if points:
            _CORNER_DEFINITION_STORE.set_track_names(track_id, "", points)
            imported += 1
    return imported



def warm_background() -> None:
    try:
        import_lmu_corner_names()
    except (OSError, ValueError):
        logger.warning("Importing LMU corner numbers failed", exc_info=True)
    warm_external_references()
app.state.telemetry_file_visibility = telemetry_file_visibility


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
    UVICORN_SERVER = uvicorn.Server(uvicorn.Config(app, host=host or "127.0.0.1", port=port, timeout_graceful_shutdown=3))
    UVICORN_SERVER.run(sockets=None if host else loopback_sockets(port))
    raise SystemExit(app_update.RESTART_EXIT if restart_requested.is_set() else 0)
