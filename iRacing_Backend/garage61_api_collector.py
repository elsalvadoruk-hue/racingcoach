from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

from garage61_auth import TOKEN_MISSING_MESSAGE, PERMISSION_MESSAGE, authentication_error

API_BASE = os.getenv("GARAGE61_API_BASE", "https://garage61.net/api/v1").rstrip("/")
USER_AGENT = "iRacing-Setup-Tuner/2.8.0 Garage61-API"
_active_status: Optional["StatusWriter"] = None

TERMINAL_STATES = {"COMPLETE", "PARTIAL", "FAILED", "CANCELLED", "SKIPPED_CACHE_SUFFICIENT", "SKIPPED_NO_TOKEN"}
REQUIRED_CSV_COLUMNS = {"Speed", "LapDistPct", "Brake", "Throttle", "SteeringWheelAngle"}
CSV_NAME_RE = re.compile(
    r"^Garage 61 - (?P<driver>.+?) - (?P<car>.+?) - (?P<track>.+?) - "
    r"(?P<time>\d{2}\.\d{2}\.\d{3}) - (?P<lap_id>[^.]+)\.csv$",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    temp = path.with_name(f".{path.name}.{os.getpid()}-{time.time_ns()}.tmp")
    temp.write_text(data, encoding="utf-8")
    last: Optional[Exception] = None
    for attempt in range(8):
        try:
            os.replace(temp, path)
            return
        except OSError as exc:
            last = exc
            time.sleep(min(0.04 * (attempt + 1), 0.3))
    try:
        path.write_text(data, encoding="utf-8")
        if temp.exists():
            temp.unlink()
        return
    except OSError:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        if last:
            raise last
        raise


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


class StatusWriter:
    def __init__(self, path: Path, job_id: str) -> None:
        self.path = path
        self.job_id = job_id
        self.started_at = time.time()
        initial = load_json(path)
        self.last: dict[str, Any] = initial if initial.get("job_id") == job_id else {}

    def update(self, state: str, message: str, progress: float, **extra: Any) -> None:
        now = time.time()
        payload = {
            **self.last,
            "job_id": self.job_id,
            "collector_mode": "API",
            "state": state,
            "message": message,
            "started_at": self.last.get("started_at", self.started_at),
            "updated_at": now,
            "elapsed_s": round(now - self.started_at, 1),
            "progress": max(0.0, min(1.0, float(progress))),
            **extra,
        }
        if state in TERMINAL_STATES:
            payload["finished_at"] = now
            payload["busy"] = False
        else:
            payload["busy"] = True
        self.last = payload
        try:
            atomic_json_write(self.path, payload)
        except OSError as exc:
            print(f"[STATUS_IO_WARNING] {exc}", flush=True)
        print(f"[{state}] {message}", flush=True)


class Garage61ApiError(RuntimeError):
    def __init__(self, status: int, message: str, trace: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.trace = trace


def _token_from_sources(reference_dir: Path, explicit_file: Optional[Path]) -> tuple[str, str]:
    env = os.getenv("GARAGE61_API_TOKEN", "").strip()
    if env:
        return env.removeprefix("Bearer ").strip(), "environment"
    candidates = []
    if explicit_file:
        candidates.append(explicit_file)
    configured = os.getenv("GARAGE61_API_TOKEN_FILE", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(reference_dir.parent / "garage61_api_token.txt")
    candidates.append(Path(__file__).resolve().parent / "garage61_api_token.txt")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            token = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            continue
        token = token.removeprefix("Authorization:").strip().removeprefix("Bearer ").strip()
        if token:
            return token, str(path)
    return "", ""


def retry_delay(headers: Any, payload: Any, attempt: int, now: Optional[float] = None) -> float:
    """Honor both Garage61's JSON wait and the standard Retry-After header."""
    waits = []
    details = payload.get("details") if isinstance(payload, dict) else None
    raw = details.get("retryAfterSeconds") if isinstance(details, dict) else None
    try:
        seconds = float(raw)
        if math.isfinite(seconds) and seconds >= 0:
            waits.append(seconds)
    except (TypeError, ValueError):
        pass
    header = headers.get("Retry-After") if headers is not None else None
    if header:
        try:
            seconds = float(header)
        except (TypeError, ValueError):
            try:
                seconds = parsedate_to_datetime(header).timestamp() - (time.time() if now is None else now)
            except (TypeError, ValueError, OverflowError):
                seconds = math.nan
        if math.isfinite(seconds):
            waits.append(max(0.0, seconds))
    return max(waits) if waits else 1.2 * (2 ** attempt)


def wait_for_retry(delay: float, attempt: int) -> None:
    # Short sleeps let the collector status heartbeat keep advancing during long
    # server-directed waits. The manager can still cancel the subprocess.
    remaining = delay
    previous = dict(_active_status.last) if _active_status is not None else None
    while remaining > 0:
        if _active_status is not None:
            _active_status.update("RATE_LIMIT_WAIT", f"Garage61 요청 제한 · {math.ceil(remaining)}초 후 재시도", float(_active_status.last.get("progress") or 0.0), retry_after_s=math.ceil(remaining), retry_attempt=attempt + 1)
        pause = min(remaining, 30.0)
        time.sleep(pause)
        remaining -= pause
    if _active_status is not None and previous:
        extras = {key: value for key, value in previous.items() if key not in {"state", "message", "progress", "updated_at", "elapsed_s", "busy", "finished_at"}}
        extras["retry_after_s"] = 0
        _active_status.update(previous.get("state", "DOWNLOADING"), previous.get("message", "Garage61 재시도"), float(previous.get("progress") or 0.0), **extras)


def _request(path: str, token: str, query: Optional[dict[str, Any]] = None, timeout: float = 60.0) -> bytes:
    url = f"{API_BASE}/{path.lstrip('/')}"
    if query:
        normalized: dict[str, str] = {}
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                normalized[key] = ",".join(str(item) for item in value)
            elif isinstance(value, bool):
                normalized[key] = "true" if value else "false"
            else:
                normalized[key] = str(value)
        if normalized:
            url += "?" + urllib.parse.urlencode(normalized)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/csv;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = body
            payload = {}
            trace = exc.headers.get("X-G61-Trace", "")
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    message = str(payload.get("message") or body)
                    trace = str(payload.get("trace") or trace)
            except json.JSONDecodeError:
                pass
            if exc.code in {429, 502, 503, 504} and attempt < 3:
                wait_for_retry(retry_delay(exc.headers, payload, attempt), attempt)
                continue
            raise Garage61ApiError(exc.code, message, trace) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(1.0 * (2 ** attempt))
                continue
            raise RuntimeError(f"Garage61 API network error: {exc}") from exc
    raise RuntimeError("Garage61 API request failed after retries")


def api_json(path: str, token: str, query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    raw = _request(path, token, query=query)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Garage61 API returned invalid JSON for {path}") from exc
    return value if isinstance(value, dict) else {}


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def words(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())


def safe_component(value: Any) -> str:
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1F]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text[:110] or "Unknown"


def score_name(candidate: str, desired: str) -> float:
    a = normalize(candidate)
    b = normalize(desired)
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    if min(len(a), len(b)) >= 8 and (a in b or b in a):
        return 92.0
    wa = set(words(candidate))
    wb = set(words(desired))
    if not wa or not wb:
        return 0.0
    overlap = len(wa & wb) / max(1, len(wb))
    return 75.0 * overlap


def resolve_content(
    items: list[dict[str, Any]],
    desired_name: str,
    desired_variant: str = "",
    desired_platform_id: Any = None,
) -> dict[str, Any]:
    iracing_items = [item for item in items if str(item.get("platform") or "iracing").casefold() == "iracing"]
    if iracing_items:
        items = iracing_items
    platform_id = str(desired_platform_id or "").strip()
    if platform_id:
        exact = [item for item in items if str(item.get("platform_id") or "").strip() == platform_id]
        if len(exact) == 1:
            return dict(exact[0])
        if exact:
            items = exact
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        base = score_name(str(item.get("name") or ""), desired_name)
        if desired_variant:
            variant = str(item.get("variant") or "")
            variant_score = score_name(variant, desired_variant)
            if normalize(variant) == normalize(desired_variant):
                variant_score = 100.0
            base = base * 0.72 + variant_score * 0.28
            if variant_score < 55.0:
                base -= 35.0
        ranked.append((base, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    if not ranked or ranked[0][0] < 70.0:
        return {}
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 2.0 and desired_variant:
        first_v = normalize(ranked[0][1].get("variant"))
        second_v = normalize(ranked[1][1].get("variant"))
        if first_v != second_v:
            return {}
    return dict(ranked[0][1])


def resolve_track(
    items: list[dict[str, Any]],
    desired_name: str,
    desired_variant: str,
    track_path: str,
    desired_platform_id: Any = None,
) -> dict[str, Any]:
    iracing_items = [item for item in items if str(item.get("platform") or "iracing").casefold() == "iracing"]
    if iracing_items:
        items = iracing_items
    platform_id = str(desired_platform_id or "").strip()
    if platform_id:
        exact = [item for item in items if str(item.get("platform_id") or "").strip() == platform_id]
        if len(exact) == 1:
            return dict(exact[0])
        if exact:
            items = exact
    if desired_variant:
        return resolve_content(items, desired_name, desired_variant, desired_platform_id)

    venue_candidates = [
        item for item in items
        if score_name(str(item.get("name") or ""), desired_name) >= 70.0
    ]
    if len(venue_candidates) == 1:
        return dict(venue_candidates[0])
    if not venue_candidates:
        return {}

    # iRacing sometimes omits TrackConfigName. Use the internal TrackName only
    # as a tie-breaker; if it remains ambiguous, fail safely instead of choosing
    # a wrong layout that would contaminate the reference library.
    path_words = set(words(track_path))
    generic = set(words(desired_name)) | {"full", "track", "circuit", "speedway", "raceway"}
    path_specific = path_words - generic
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in venue_candidates:
        variant_words = set(words(item.get("variant")))
        overlap = len(path_specific & variant_words)
        denominator = max(1, len(variant_words))
        score = 100.0 * overlap / denominator
        if normalize(item.get("variant")) and normalize(item.get("variant")) in normalize(track_path):
            score += 50.0
        ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    if ranked and ranked[0][0] >= 35.0 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 10.0):
        return dict(ranked[0][1])
    return {}


def maybe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def norm_pct(value: Any) -> Optional[float]:
    number = maybe_float(value)
    if number is None:
        return None
    if abs(number) <= 1.5:
        number *= 100.0
    return max(0.0, min(100.0, number))


def api_lap_metadata(lap: dict[str, Any]) -> dict[str, Any]:
    driver = lap.get("driver") if isinstance(lap.get("driver"), dict) else {}
    car = lap.get("car") if isinstance(lap.get("car"), dict) else {}
    track = lap.get("track") if isinstance(lap.get("track"), dict) else {}
    season = lap.get("season") if isinstance(lap.get("season"), dict) else {}
    wind_dir = maybe_float(lap.get("windDir"))
    air_pressure = maybe_float(lap.get("airPressure"))
    clouds = maybe_float(lap.get("clouds"))
    cloud_code = int(round(clouds)) if clouds is not None else None
    cloud_labels = {1: "Clear", 2: "Partly cloudy", 3: "Mostly cloudy", 4: "Overcast"}
    return {
        "source_type": "GARAGE61_API",
        "api_version": "v1",
        "lap_id": str(lap.get("id") or "").upper(),
        "driver_name": " ".join(part for part in [str(driver.get("firstName") or "").strip(), str(driver.get("lastName") or "").strip()] if part).strip() or str(driver.get("slug") or "Unknown Driver"),
        "driver_slug": driver.get("slug"),
        "driver_id": driver.get("id"),
        "driver_rating": lap.get("driverRating"),
        "car_name": car.get("name"),
        "car_id": car.get("id"),
        "car_platform_id": car.get("platform_id"),
        "track_name": track.get("name"),
        "track_config": track.get("variant") or "",
        "track_id": track.get("id"),
        "track_platform_id": track.get("platform_id"),
        "lap_time_s": maybe_float(lap.get("lapTime")),
        "start_time": lap.get("startTime"),
        "season_id": season.get("id"),
        "season_name": season.get("name"),
        "season_short_name": season.get("shortName"),
        "event_id": lap.get("event"),
        "event_type": lap.get("eventType"),
        "session_type": lap.get("sessionType"),
        "lap_number": lap.get("lapNumber"),
        "sector_times_s": [
            maybe_float(item.get("sectorTime"))
            for item in list(lap.get("sectors") or [])
            if isinstance(item, dict) and maybe_float(item.get("sectorTime")) is not None
        ],
        "clean": bool(lap.get("clean")),
        "joker": bool(lap.get("joker")),
        "discontinuity": bool(lap.get("discontinuity")),
        "missing": bool(lap.get("missing")),
        "incomplete": bool(lap.get("incomplete")),
        "offtrack": bool(lap.get("offtrack")),
        "pitlane": bool(lap.get("pitlane")),
        "pit_in": bool(lap.get("pitIn")),
        "pit_out": bool(lap.get("pitOut")),
        "track_temp_c": maybe_float(lap.get("trackTemp")),
        "track_usage_pct": maybe_float(lap.get("trackUsage")),
        "track_wetness_pct": maybe_float(lap.get("trackWetness")),
        "air_temp_c": maybe_float(lap.get("airTemp")),
        "skies_code": (cloud_code - 1) if cloud_code is not None else None,
        "clouds_code_api": cloud_code,
        "weather": cloud_labels.get(cloud_code),
        "air_density_kg_m3": maybe_float(lap.get("airDensity")),
        "air_pressure_hpa": air_pressure / 100.0 if air_pressure is not None else None,
        "wind_speed_kph": (maybe_float(lap.get("windVel")) or 0.0) * 3.6 if maybe_float(lap.get("windVel")) is not None else None,
        "wind_direction_deg": math.degrees(wind_dir) % 360.0 if wind_dir is not None else None,
        "relative_humidity_pct": norm_pct(lap.get("relativeHumidity")),
        "fog_level_pct": norm_pct(lap.get("fogLevel")),
        "precipitation_pct": norm_pct(lap.get("precipitation")),
        "weather_declared_wet": (maybe_float(lap.get("trackWetness")) or 0.0) > 0.0 or (norm_pct(lap.get("precipitation")) or 0.0) > 0.1,
        "fuel_l": maybe_float(lap.get("fuelLevel")),
        "fuel_used_l": maybe_float(lap.get("fuelUsed")),
        "fuel_added_l": maybe_float(lap.get("fuelAdded")),
        "weight_penalty_kg": maybe_float(lap.get("weightPenalty")),
        "power_adjust_pct": maybe_float(lap.get("powerAdjust")),
        "tire_compound": lap.get("tireCompound"),
        "ghost_available": bool(lap.get("ghostAvailable")),
        "can_view_telemetry": bool(lap.get("canViewTelemetry")),
        "can_view_setup": bool(lap.get("canViewSetup")),
        "setup_available": bool(lap.get("canViewSetup")),
        "setup_access": "AVAILABLE" if bool(lap.get("canViewSetup")) else "LOCKED",
        "source_url": f"{API_BASE}/laps/{str(lap.get('id') or '')}",
        "api_metadata_at": utc_now_iso(),
    }


def numeric_similarity(a: Any, b: Any, full_delta: float, zero_delta: float) -> Optional[float]:
    left, right = maybe_float(a), maybe_float(b)
    if left is None or right is None:
        return None
    delta = abs(left - right)
    if delta <= full_delta:
        return 1.0
    if delta >= zero_delta:
        return 0.0
    return 1.0 - (delta - full_delta) / max(1e-9, zero_delta - full_delta)


def circular_similarity(a: Any, b: Any) -> Optional[float]:
    left, right = maybe_float(a), maybe_float(b)
    if left is None or right is None:
        return None
    delta = abs(((left - right + 180.0) % 360.0) - 180.0)
    return max(0.0, 1.0 - delta / 180.0)


def condition_score(meta: dict[str, Any], current: dict[str, Any]) -> tuple[float, float, list[str]]:
    specs = [
        ("track_temp_c", 0.22, 2.0, 15.0, "노면온도"),
        ("air_temp_c", 0.08, 2.0, 12.0, "기온"),
        ("fuel_l", 0.20, 3.0, 35.0, "연료"),
        ("fuel_used_l", 0.04, 0.35, 2.0, "랩 연료소모"),
        ("relative_humidity_pct", 0.04, 8.0, 45.0, "습도"),
        ("fog_level_pct", 0.02, 5.0, 35.0, "안개"),
        ("air_pressure_hpa", 0.035, 5.0, 35.0, "기압"),
        ("air_density_kg_m3", 0.045, 0.015, 0.12, "공기밀도"),
        ("wind_speed_kph", 0.055, 4.0, 30.0, "풍속"),
        ("precipitation_pct", 0.10, 0.5, 15.0, "강수"),
    ]
    weighted = 0.0
    total = 0.0
    available = 0
    reasons: list[str] = []
    for key, weight, full, zero, label in specs:
        score = numeric_similarity(meta.get(key), current.get(key), full, zero)
        if score is None:
            continue
        available += 1
        weighted += weight * score
        total += weight
        if score >= 0.85:
            reasons.append(f"{label} 유사")
        elif score <= 0.25:
            reasons.append(f"{label} 차이 큼")
    wind_dir_score = circular_similarity(meta.get("wind_direction_deg"), current.get("wind_direction_deg"))
    if wind_dir_score is not None:
        available += 1
        weighted += 0.035 * wind_dir_score
        total += 0.035
    current_wet = current.get("weather_declared_wet")
    if current_wet is not None:
        candidate_wet = bool(meta.get("weather_declared_wet"))
        wet_score = 1.0 if candidate_wet == bool(current_wet) else 0.0
        available += 1
        weighted += 0.16 * wet_score
        total += 0.16
        reasons.append("Dry/Wet 일치" if wet_score else "Dry/Wet 불일치")
    current_weight = current.get("weight_penalty_kg")
    if current_weight is not None:
        s = numeric_similarity(meta.get("weight_penalty_kg"), current_weight, 0.1, 20.0)
        if s is not None:
            available += 1
            weighted += 0.045 * s
            total += 0.045
            if s < 0.5:
                reasons.append("BOP 중량 차이")
    current_power = current.get("power_adjust_pct")
    if current_power is not None:
        s = numeric_similarity(meta.get("power_adjust_pct"), current_power, 0.05, 5.0)
        if s is not None:
            available += 1
            weighted += 0.045 * s
            total += 0.045
            if s < 0.5:
                reasons.append("BOP 출력 차이")
    current_tire = current.get("tire_compound")
    # PlayerTireCompound is an index into the SDK's per-car DriverTires list,
    # not Garage61's tireCompound enum. An unverified mapping is unknown.
    if (current.get("tire_compound_source") != "IRACING_SDK"
            and current_tire is not None and meta.get("tire_compound") is not None):
        available += 1
        s = 1.0 if str(meta.get("tire_compound")) == str(current_tire) else 0.0
        weighted += 0.05 * s
        total += 0.05
        if not s:
            reasons.append("타이어 컴파운드 차이")
    raw = weighted / total if total else 0.5
    expected_fields = [
        "track_temp_c", "air_temp_c", "fuel_l", "fuel_used_l",
        "relative_humidity_pct", "fog_level_pct", "air_pressure_hpa",
        "air_density_kg_m3", "wind_speed_kph", "wind_direction_deg",
        "precipitation_pct",
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
    adjusted = 0.50 + (raw - 0.50) * (0.50 + 0.50 * coverage)
    return max(0.0, min(1.0, adjusted)), coverage, reasons[:8]


def recency_score(value: Any) -> float:
    if not value:
        return 0.5
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.5
    return max(0.0, min(1.0, math.exp(-days / 180.0)))


def rank_candidates(laps: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
    current = request.get("conditions") if isinstance(request.get("conditions"), dict) else {}
    best = maybe_float(request.get("current_best_lap_s"))
    output: list[dict[str, Any]] = []
    for lap in laps:
        meta = api_lap_metadata(lap)
        if not meta.get("lap_id") or not meta.get("can_view_telemetry"):
            continue
        if not meta.get("clean") or meta.get("incomplete") or meta.get("discontinuity") or meta.get("missing") or meta.get("pitlane"):
            continue
        lap_time = maybe_float(meta.get("lap_time_s"))
        if lap_time is None:
            continue
        similarity, coverage, reasons = condition_score(meta, current)
        faster = bool(best is not None and lap_time <= best - 0.03)
        if best is None:
            pace_score = 0.5
            gain = None
        else:
            gain = best - lap_time
            pace_score = max(0.0, min(1.0, 0.50 + gain / 8.0))
        rating = maybe_float(meta.get("driver_rating")) or 0.0
        rating_score = max(0.0, min(1.0, math.log1p(max(0.0, rating)) / math.log1p(8000.0)))
        quality = 1.0
        if meta.get("offtrack") or meta.get("pit_in") or meta.get("pit_out"):
            quality -= 0.35
        score = 0.61 * similarity + 0.20 * pace_score + 0.10 * recency_score(meta.get("start_time")) + 0.05 * quality + 0.04 * rating_score
        if faster:
            score += 0.03
        meta.update(
            {
                "condition_similarity_score": round(similarity, 4),
                "condition_similarity_coverage": round(coverage, 4),
                "condition_similarity_reasons": reasons,
                "faster_than_current_best": faster,
                "pace_gap_to_current_best_s": round(gain, 4) if gain is not None else None,
                "reference_selection_score": round(max(0.0, min(1.0, score)), 4),
                "reference_quality_score": round(quality, 3),
                "reference_recency_score": round(recency_score(meta.get("start_time")), 3),
            }
        )
        output.append(meta)
    output.sort(
        key=lambda item: (
            0 if item.get("faster_than_current_best") else 1,
            -float(item.get("reference_selection_score") or 0.0),
            float(item.get("lap_time_s") or math.inf),
        )
    )
    return output


def filename_for(meta: dict[str, Any]) -> str:
    lap_time = float(meta["lap_time_s"])
    minutes = int(lap_time // 60)
    seconds = int(lap_time % 60)
    millis = int(round((lap_time - math.floor(lap_time)) * 1000.0))
    if millis >= 1000:
        seconds += 1
        millis -= 1000
    if seconds >= 60:
        minutes += 1
        seconds -= 60
    track = safe_component(meta.get("track_name") or "Unknown Track")
    config = safe_component(meta.get("track_config") or "")
    if config and normalize(config) not in normalize(track):
        track = f"{track} ({config})"
    return (
        f"Garage 61 - {safe_component(meta.get('driver_name'))} - "
        f"{safe_component(meta.get('car_name'))} - {track} - "
        f"{minutes:02d}.{seconds:02d}.{millis:03d} - {str(meta.get('lap_id')).upper()}.csv"
    )


def validate_csv(raw: bytes) -> dict[str, Any]:
    if len(raw) < 5000:
        raise ValueError("CSV payload is unexpectedly small")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    missing = sorted(REQUIRED_CSV_COLUMNS - fields)
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))
    count = 0
    min_dist = math.inf
    max_dist = -math.inf
    speed_values: list[float] = []
    position_on_track = 0
    for row in reader:
        dist = maybe_float(row.get("LapDistPct"))
        if dist is None:
            continue
        count += 1
        min_dist = min(min_dist, dist)
        max_dist = max(max_dist, dist)
        speed = maybe_float(row.get("Speed"))
        if speed is not None and len(speed_values) < 2000:
            speed_values.append(speed)
        if str(row.get("PositionType") or "") == "3":
            position_on_track += 1
    coverage = max_dist - min_dist if count else 0.0
    if count < 300 or coverage < 0.90:
        raise ValueError(f"incomplete telemetry: samples={count}, coverage={coverage:.3f}")
    speed_p95 = None
    speed_unit = "m/s"
    if speed_values:
        ordered = sorted(speed_values)
        speed_p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        if speed_p95 > 130.0:
            speed_unit = "km/h_suspected"
    return {
        "csv_validated": True,
        "sample_count": count,
        "distance_coverage": round(coverage, 4),
        "telemetry_columns": sorted(fields),
        "speed_p95_raw": round(speed_p95, 3) if speed_p95 is not None else None,
        "speed_unit_detected": speed_unit,
        "position_on_track_ratio": round(position_on_track / count, 4) if count and "PositionType" in fields else None,
    }


def load_catalog(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not payload:
        payload = {}
    if not isinstance(payload.get("laps"), dict):
        payload["laps"] = {}
    if not isinstance(payload.get("context_scans"), dict):
        payload["context_scans"] = {}
    payload["schema_version"] = max(3, int(payload.get("schema_version") or 0))
    return payload


def save_catalog(path: Path, catalog: dict[str, Any]) -> None:
    catalog["schema_version"] = 3
    catalog["updated_at"] = utc_now_iso()
    atomic_json_write(path, catalog)


def sidecar_write(csv_path: Path, metadata: dict[str, Any]) -> None:
    payload = {k: v for k, v in metadata.items() if not str(k).startswith("_")}
    payload["csv_name"] = csv_path.name
    atomic_json_write(csv_path.with_suffix(".meta.json"), payload)


def local_lap_ids(reference_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in reference_dir.glob("Garage 61 - *.csv"):
        match = CSV_NAME_RE.match(path.name)
        if match:
            ids.add(match.group("lap_id").upper())
    return ids


def query_passes(request: dict[str, Any], track_id: int, car_id: int) -> list[tuple[str, dict[str, Any]]]:
    conditions = request.get("conditions") if isinstance(request.get("conditions"), dict) else {}
    base: dict[str, Any] = {
        "tracks": track_id,
        "cars": car_id,
        "group": "driver",
        "limit": 1000,
        "age": -4,
        # Full normal laps only. Explicit metric rounding makes server-side
        # condition boundaries match the values presented by Garage61.
        "lapTypes": 1,
        "round": "metric",
    }
    setup_type = request.get("session_setup_type")
    if setup_type in {1, 2, "1", "2"}:
        base["sessionSetupTypes"] = int(setup_type)
    best = maybe_float(request.get("current_best_lap_s"))
    if best is not None:
        base["maxLapTime"] = max(1.0, best - 0.03)
    dry = conditions.get("weather_declared_wet") is False
    wet = conditions.get("weather_declared_wet") is True

    def ranged(key_min: str, key_max: str, value: Any, delta: float, params: dict[str, Any]) -> None:
        num = maybe_float(value)
        if num is not None:
            params[key_min] = num - delta
            params[key_max] = num + delta

    strict = dict(base)
    ranged("minConditionsTrackTemp", "maxConditionsTrackTemp", conditions.get("track_temp_c"), 6.0, strict)
    ranged("minConditionsAirTemp", "maxConditionsAirTemp", conditions.get("air_temp_c"), 5.0, strict)
    ranged("minFuel", "maxFuel", conditions.get("fuel_l"), 15.0, strict)
    ranged("minFuelUsed", "maxFuelUsed", conditions.get("fuel_used_l"), 0.9, strict)

    humidity_pct = maybe_float(conditions.get("relative_humidity_pct"))
    if humidity_pct is not None:
        humidity = max(0.0, min(1.0, humidity_pct / 100.0))
        strict["minConditionsRelativeHumidity"] = max(0.0, humidity - 0.12)
        strict["maxConditionsRelativeHumidity"] = min(1.0, humidity + 0.12)
    fog_pct = maybe_float(conditions.get("fog_level_pct"))
    if fog_pct is not None:
        fog = max(0.0, min(1.0, fog_pct / 100.0))
        strict["minConditionsFogLevel"] = max(0.0, fog - 0.12)
        strict["maxConditionsFogLevel"] = min(1.0, fog + 0.12)
    wind_kph = maybe_float(conditions.get("wind_speed_kph"))
    if wind_kph is not None:
        wind_mps = max(0.0, wind_kph / 3.6)
        strict["minConditionsWindVel"] = max(0.0, wind_mps - 2.0)
        strict["maxConditionsWindVel"] = wind_mps + 2.0
    precipitation_pct = maybe_float(conditions.get("precipitation_pct"))
    if precipitation_pct is not None:
        precip = max(0.0, min(1.0, precipitation_pct / 100.0))
        strict["minConditionsPrecipitation"] = max(0.0, precip - 0.02)
        strict["maxConditionsPrecipitation"] = min(1.0, precip + 0.02)
    sky_code = maybe_float(conditions.get("skies_code"))
    if sky_code is not None:
        api_cloud = int(max(1, min(4, round(sky_code) + 1)))
        strict["minConditionsCloud"] = api_cloud
        strict["maxConditionsCloud"] = api_cloud

    if dry:
        strict["maxConditionsTrackWetness"] = 0
        strict["maxConditionsPrecipitation"] = min(float(strict.get("maxConditionsPrecipitation", 1.0)), 0.01)
    elif wet:
        strict["minConditionsTrackWetness"] = 1
    current_weight = maybe_float(conditions.get("weight_penalty_kg"))
    if current_weight is not None:
        strict["minWeightPenalty"] = current_weight - 0.01
        strict["maxWeightPenalty"] = current_weight + 0.01
    current_power = maybe_float(conditions.get("power_adjust_pct"))
    if current_power is not None:
        strict["minPowerAdjust"] = current_power - 0.01
        strict["maxPowerAdjust"] = current_power + 0.01

    moderate = dict(base)
    ranged("minConditionsTrackTemp", "maxConditionsTrackTemp", conditions.get("track_temp_c"), 12.0, moderate)
    ranged("minConditionsAirTemp", "maxConditionsAirTemp", conditions.get("air_temp_c"), 10.0, moderate)
    ranged("minFuel", "maxFuel", conditions.get("fuel_l"), 30.0, moderate)
    ranged("minFuelUsed", "maxFuelUsed", conditions.get("fuel_used_l"), 1.8, moderate)
    if humidity_pct is not None:
        humidity = max(0.0, min(1.0, humidity_pct / 100.0))
        moderate["minConditionsRelativeHumidity"] = max(0.0, humidity - 0.25)
        moderate["maxConditionsRelativeHumidity"] = min(1.0, humidity + 0.25)
    if fog_pct is not None:
        fog = max(0.0, min(1.0, fog_pct / 100.0))
        moderate["minConditionsFogLevel"] = max(0.0, fog - 0.25)
        moderate["maxConditionsFogLevel"] = min(1.0, fog + 0.25)
    if wind_kph is not None:
        wind_mps = max(0.0, wind_kph / 3.6)
        moderate["minConditionsWindVel"] = max(0.0, wind_mps - 4.0)
        moderate["maxConditionsWindVel"] = wind_mps + 4.0
    if precipitation_pct is not None:
        precip = max(0.0, min(1.0, precipitation_pct / 100.0))
        moderate["minConditionsPrecipitation"] = max(0.0, precip - 0.08)
        moderate["maxConditionsPrecipitation"] = min(1.0, precip + 0.08)
    if sky_code is not None:
        api_cloud = int(max(1, min(4, round(sky_code) + 1)))
        moderate["minConditionsCloud"] = max(1, api_cloud - 1)
        moderate["maxConditionsCloud"] = min(4, api_cloud + 1)
    if dry:
        moderate["maxConditionsTrackWetness"] = 2
    if current_weight is not None:
        moderate["minWeightPenalty"] = current_weight - 2.0
        moderate["maxWeightPenalty"] = current_weight + 2.0
    if current_power is not None:
        moderate["minPowerAdjust"] = current_power - 0.25
        moderate["maxPowerAdjust"] = current_power + 0.25

    broad = dict(base)
    broad["age"] = -4

    pace_relaxed = dict(moderate)
    if best is not None:
        pace_relaxed["maxLapTime"] = best + 1.5

    return [("STRICT", strict), ("MODERATE", moderate), ("BROAD", broad), ("PACE_RELAXED", pace_relaxed)]


def run(request_path: Path, status_path: Path, reference_dir: Path, token_file: Optional[Path]) -> int:
    request = load_json(request_path)
    job_id = str(request.get("job_id") or "")
    global _active_status
    status = StatusWriter(status_path, job_id)
    status.last.update({key: request.get(key) for key in ("filename", "context_key", "created_at", "target_count")})
    _active_status = status
    reference_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = reference_dir / "garage61_catalog.json"
    target_count = max(3, min(24, int(request.get("target_count") or 8)))
    context_key = str(request.get("context_key") or "")

    token, token_source = _token_from_sources(reference_dir, token_file)
    if not token:
        status.update(
            "SKIPPED_NO_TOKEN",
            TOKEN_MISSING_MESSAGE,
            1.0,
            token_configured=False,
        )
        return 0

    try:
        status.update("AUTHENTICATING", "Garage61 API 인증을 확인합니다.", 0.05, token_configured=True)
        me = api_json("me", token)
        permissions = list(me.get("apiPermissions") or [])
        if "driving_data" not in permissions:
            status.update("FAILED", PERMISSION_MESSAGE, 1.0, error_code="API_PERMISSION_DENIED")
            return 4
        status.update(
            "RESOLVING_IDS",
            "IBT 차량/트랙을 Garage61 API ID로 매핑합니다.",
            0.12,
            api_user_slug=me.get("slug"),
            api_subscription_plan=me.get("subscriptionPlan"),
            api_permissions=permissions,
            token_source="configured" if token_source else "",
        )

        cache_path = reference_dir / "garage61_api_content_cache.json"
        cache = load_json(cache_path)
        cache_at = maybe_float(cache.get("cached_at_epoch"))
        cache_fresh = cache_at is not None and time.time() - cache_at < 86400
        if cache_fresh and isinstance(cache.get("cars"), list) and isinstance(cache.get("tracks"), list):
            cars = cache["cars"]
            tracks = cache["tracks"]
        else:
            cars = list(api_json("cars", token).get("items") or [])
            tracks = list(api_json("tracks", token).get("items") or [])
            atomic_json_write(cache_path, {"cached_at": utc_now_iso(), "cached_at_epoch": time.time(), "cars": cars, "tracks": tracks})

        car = resolve_content(
            cars,
            str(request.get("car_name") or ""),
            desired_platform_id=request.get("car_platform_id"),
        )
        track = resolve_track(
            tracks,
            str(request.get("track_name") or ""),
            str(request.get("resolved_track_config") or request.get("track_config") or ""),
            str(request.get("track_path") or ""),
            request.get("track_platform_id"),
        )
        if not car:
            raise RuntimeError(f"Garage61 car ID mapping failed: {request.get('car_name')}")
        if not track:
            raise RuntimeError(
                f"Garage61 track/layout ID mapping failed: {request.get('track_name')} / {request.get('resolved_track_config') or request.get('track_config')}"
            )

        track_id = int(track["id"])
        car_id = int(car["id"])
        status.update(
            "QUERYING_LAPS",
            f"Garage61 API에서 {track.get('name')} · {track.get('variant')} / {car.get('name')} 랩을 조회합니다.",
            0.20,
            resolved_track_id=track_id,
            resolved_car_id=car_id,
            resolved_track_name=track.get("name"),
            resolved_track_config=track.get("variant"),
            resolved_car_name=car.get("name"),
        )

        merged: dict[str, dict[str, Any]] = {}
        pass_stats: list[dict[str, Any]] = []
        desired_pool = max(24, target_count * 4)
        for idx, (label, params) in enumerate(query_passes(request, track_id, car_id)):
            payload = api_json("laps", token, params)
            items = [item for item in list(payload.get("items") or []) if isinstance(item, dict)]
            for item in items:
                lap_id = str(item.get("id") or "").upper()
                if lap_id:
                    merged[lap_id] = item
            visible = sum(1 for item in merged.values() if item.get("canViewTelemetry"))
            pass_stats.append({"name": label, "returned": len(items), "total": payload.get("total"), "visible_telemetry_merged": visible})
            status.update(
                "QUERYING_LAPS",
                f"API 후보 조회 · {label} · {len(items)}개 / 누적 {len(merged)}개",
                0.24 + 0.05 * idx,
                query_passes=pass_stats,
                scanned_rows=len(merged),
            )
            if label != "PACE_RELAXED" and visible >= desired_pool:
                break

        ranked = rank_candidates(list(merged.values()), request)
        if not ranked:
            status.update(
                "PARTIAL",
                "Garage61 API에서 현재 조건에 사용할 수 있는 공개 telemetry 후보를 찾지 못했습니다.",
                1.0,
                scanned_rows=len(merged),
                candidate_pool=0,
                downloaded_count=0,
                query_passes=pass_stats,
            )
            return 0

        status.update(
            "RANKING",
            f"{len(ranked)}개 telemetry 후보를 조건·페이스·최신성·품질로 정렬했습니다.",
            0.46,
            scanned_rows=len(merged),
            candidate_pool=len(ranked),
            setup_available_rows=sum(1 for item in ranked if item.get("setup_available")),
            query_passes=pass_stats,
        )

        catalog = load_catalog(catalog_path)
        catalog_laps = catalog.setdefault("laps", {})
        existing_ids = local_lap_ids(reference_dir)
        downloaded = 0
        cached_used = 0
        failures: list[dict[str, Any]] = []
        selected: list[dict[str, Any]] = []
        seen_drivers: set[str] = set()

        # Favor unique drivers first. Keep a reserve pool so a failed download does not stop collection.
        for item in ranked:
            driver_key = normalize(item.get("driver_slug") or item.get("driver_name"))
            if driver_key and driver_key in seen_drivers:
                continue
            selected.append(item)
            if driver_key:
                seen_drivers.add(driver_key)
            if len(selected) >= max(target_count * 3, target_count + 8):
                break
        if len(selected) < target_count:
            for item in ranked:
                if item not in selected:
                    selected.append(item)
                if len(selected) >= target_count * 3:
                    break

        context_ids: list[str] = []
        for index, meta in enumerate(selected):
            if len(context_ids) >= target_count:
                break
            lap_id = str(meta["lap_id"]).upper()
            filename = filename_for(meta)
            path = reference_dir / filename
            progress = 0.50 + 0.42 * min(1.0, (index + 1) / max(1, target_count))
            if lap_id in existing_ids:
                # Preserve the existing CSV filename if it differs, but still enrich metadata from the API.
                existing_path = next((p for p in reference_dir.glob("Garage 61 - *.csv") if lap_id in p.name.upper()), None)
                if existing_path is not None:
                    path = existing_path
                previous = catalog_laps.get(lap_id) if isinstance(catalog_laps.get(lap_id), dict) else {}
                enriched = {**previous, **meta, "csv_name": path.name, "api_enriched_at": utc_now_iso()}
                catalog_laps[lap_id] = enriched
                sidecar_write(path, enriched)
                context_ids.append(lap_id)
                cached_used += 1
                status.update("DOWNLOADING", f"API telemetry 확보 중 · {len(context_ids)}/{target_count} · 캐시 재사용", progress, downloaded_count=downloaded, cached_csv_count=cached_used)
                continue
            try:
                raw = _request(f"laps/{lap_id}/csv", token, timeout=90.0)
                validation = validate_csv(raw)
                lap_time_for_rate = maybe_float(meta.get("lap_time_s"))
                if lap_time_for_rate and lap_time_for_rate > 0:
                    validation["sample_rate_hz_est"] = round(float(validation.get("sample_count") or 0) / lap_time_for_rate, 2)
                temp = path.with_name(f".{path.name}.{os.getpid()}.part")
                temp.write_bytes(raw)
                os.replace(temp, path)
                enriched = {
                    **meta,
                    **validation,
                    "csv_name": path.name,
                    "csv_size_bytes": len(raw),
                    "csv_sha256": hashlib.sha256(raw).hexdigest(),
                    "downloaded_at": utc_now_iso(),
                    "metadata_enriched": True,
                    "metadata_source": "GARAGE61_API",
                }
                catalog_laps[lap_id] = enriched
                sidecar_write(path, enriched)
                existing_ids.add(lap_id)
                context_ids.append(lap_id)
                downloaded += 1
                status.update("DOWNLOADING", f"API telemetry 확보 중 · {len(context_ids)}/{target_count}", progress, downloaded_count=downloaded, cached_csv_count=cached_used)
            except Exception as exc:
                failures.append({"lap_id": lap_id, "driver_name": meta.get("driver_name"), "lap_time_s": meta.get("lap_time_s"), "reason": str(exc)[:400]})
                status.update("DOWNLOADING", f"API telemetry 확보 중 · {len(context_ids)}/{target_count} · 1건 실패 후 다음 후보 진행", progress, downloaded_count=downloaded, download_failures=failures[-5:])

        scan = {
            "source_type": "GARAGE61_API",
            "api_version": "v1",
            "api_user_slug": me.get("slug"),
            "api_subscription_plan": me.get("subscriptionPlan"),
            "raw_row_count": len(merged),
            "parsed_row_count": len(ranked),
            "linked_lap_id_count": len(ranked),
            "downloaded_count": downloaded,
            "cached_used_count": cached_used,
            "context_csv_count": len(context_ids),
            "scraped_at": utc_now_iso(),
            "partial": len(context_ids) < target_count,
            "car_name": request.get("car_name"),
            "car_id": car_id,
            "car_platform_id": request.get("car_platform_id"),
            "track_name": request.get("track_name"),
            "track_platform_id": request.get("track_platform_id"),
            "track_config": request.get("resolved_track_config") or request.get("track_config"),
            "track_id": track_id,
            "conditions": request.get("conditions") or {},
            "query_passes": pass_stats,
            "candidate_pool": len(ranked),
            "setup_available_rows": sum(1 for item in ranked if item.get("setup_available")),
            "selected_lap_ids": context_ids,
        }
        catalog["last_scan"] = scan
        if context_key:
            catalog.setdefault("context_scans", {})[context_key] = scan
        catalog["api_identity"] = {
            "user_slug": me.get("slug"),
            "subscription_plan": me.get("subscriptionPlan"),
            "api_permissions": permissions,
            "checked_at": utc_now_iso(),
        }
        save_catalog(catalog_path, catalog)

        local_count = len(list(reference_dir.glob("Garage 61 - *.csv")))
        terminal = "COMPLETE" if len(context_ids) >= target_count else "PARTIAL"
        message = (
            f"Garage61 API 기준 데이터 확보 완료 · 현재 컨텍스트 {len(context_ids)}개"
            if terminal == "COMPLETE"
            else f"Garage61 API 기준 데이터 일부 확보 · 현재 컨텍스트 {len(context_ids)}개"
        )
        status.update(
            terminal,
            message,
            1.0,
            scanned_rows=len(merged),
            parsed_rows=len(ranked),
            candidate_pool=len(ranked),
            distinct_drivers=len({normalize(item.get("driver_slug") or item.get("driver_name")) for item in ranked}),
            setup_available_rows=sum(1 for item in ranked if item.get("setup_available")),
            downloaded_count=downloaded,
            cached_csv_count=cached_used,
            context_csv_count=len(context_ids),
            local_csv_count=local_count,
            download_failures=failures,
            catalog_path=str(catalog_path),
            query_passes=pass_stats,
            resolved_track_id=track_id,
            resolved_car_id=car_id,
        )
        return 0
    except Garage61ApiError as exc:
        auth = authentication_error(exc.status)
        status.update(
            "FAILED",
            auth.get("message") or f"Garage61 API 오류 HTTP {exc.status}: {str(exc)[:240]}",
            1.0,
            api_http_status=exc.status,
            api_trace=exc.trace,
            error_code=auth.get("error_code", "GARAGE61_API_HTTP_ERROR"),
        )
        return 4
    except Exception as exc:
        status.update("FAILED", f"Garage61 API 수집 실패: {str(exc)[:400]}", 1.0, error_code="GARAGE61_API_COLLECTOR_ERROR")
        return 5


def main() -> int:
    parser = argparse.ArgumentParser(description="Garage61 API telemetry collector")
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args()
    request = load_json(args.request_file)
    if not request:
        print("Invalid request file", file=sys.stderr)
        return 2
    return run(args.request_file, args.status_file, args.reference_dir, args.token_file)


if __name__ == "__main__":
    raise SystemExit(main())
