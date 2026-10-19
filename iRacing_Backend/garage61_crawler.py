from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def _configure_utf8_stdio() -> None:
    """Prevent Windows CP949 console encoding from aborting collector logic.

    Garage61/iRacing names can contain characters such as ü/ö/ł.  The collector
    writes status messages to a redirected stdout log on Windows; if that stream
    inherits CP949, a normal status print can raise UnicodeEncodeError *before*
    the browser action runs.  Reconfigure when supported and still keep all
    later status writes exception-safe.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def _safe_console_line(text: str) -> None:
    """Best-effort logging: console encoding must never change collector state."""
    line = str(text) + "\n"
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
        return
    except (UnicodeEncodeError, UnicodeError):
        pass
    except Exception:
        return
    try:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(line.encode("utf-8", errors="backslashreplace"))
            buffer.flush()
    except Exception:
        pass


_configure_utf8_stdio()

CRAWLER_BUILD = "2.6.13"

ULID_RE = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", re.IGNORECASE)
LAP_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\.(\d{3})\b")
GARAGE61_CSV_RE = re.compile(
    r"^Garage 61 - (?P<driver>.+?) - (?P<car>.+?) - (?P<track>.+?) - "
    r"(?P<time>\d{2}\.\d{2}\.\d{3}) - (?P<lap_id>[0-9A-HJKMNP-TV-Z]{26})\.csv$",
    re.IGNORECASE,
)

WEATHER_LABELS = ["Partly cloudy", "Mostly cloudy", "Overcast", "Clear"]
TRACK_USAGE_LABELS = [
    "Moderately low usage",
    "Moderately high usage",
    "Slight usage",
    "Low usage",
    "Moderate usage",
    "High usage",
    "Clean",
]
COMPASS_DEGREES = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}
SKY_CODES = {"clear": 0, "partly cloudy": 1, "mostly cloudy": 2, "overcast": 3}
TERMINAL_STATES = {"COMPLETE", "PARTIAL", "FAILED", "CANCELLED"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_reference_dir() -> Path:
    configured = os.getenv("IRACING_EXTERNAL_REFERENCE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    import app_settings
    return app_settings.data_dir() / "Garage61_References"


def default_profile_dir(reference_dir: Path) -> Path:
    return reference_dir.parent / "Garage61_Browser_Profile"


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def name_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def words(value: Any) -> list[str]:
    return [item for item in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(item) >= 2]


TRACK_VENUE_GENERIC_WORDS = {
    "speedway", "international", "circuit", "raceway", "track", "the",
}


def _ascii_match_text(value: Any) -> str:
    """Normalize labels for deterministic Garage61 picker comparisons.

    This is deliberately stricter than the old fuzzy token matcher. Accents and
    dash variants are normalized, but layout text (including one-character A/B)
    is preserved so National A cannot match National B.
    """

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = re.sub(r"\s*[-/]\s*", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return normalize_space(text)


def _track_label_parts(value: Any) -> tuple[str, str]:
    """Split the final ``Venue (Layout)`` suffix used by Garage61.

    The final parenthesized group is treated as the layout. This keeps labels
    such as ``National A``/``National B`` and ``... - Handling`` distinct.
    """

    text = normalize_space(str(value or ""))
    match = re.match(r"^(.*)\s+\(([^()]*)\)\s*$", text)
    if not match:
        return text, ""
    return normalize_space(match.group(1)), normalize_space(match.group(2))


def _layout_key(value: Any) -> str:
    # Unlike words(), do not drop one-character tokens. A/B suffixes are part of
    # the exact layout identity on Garage61.
    return _ascii_match_text(value)


def _venue_signature(value: Any) -> str:
    tokens = [
        token
        for token in _ascii_match_text(value).split()
        if token not in TRACK_VENUE_GENERIC_WORDS
    ]
    return " ".join(tokens)


def _expected_track_label(desired: str, secondary: str = "") -> str:
    venue, inline_layout = _track_label_parts(desired)
    layout = normalize_space(secondary) or inline_layout
    if layout:
        return f"{venue} ({layout})"
    return venue


# iRacing does not populate TrackConfigName for every layout. In those cases
# TrackName/track_path still contains the internal layout identifier. Keep this
# table deliberately explicit: guessing a layout is worse than stopping, because
# a wrong layout makes LapDistPct comparisons invalid.
TRACK_PATH_LAYOUT_ALIASES: dict[tuple[str, str], str] = {
    ("autodromo nazionale monza", "monza full"): "Grand Prix",
    ("hockenheimring baden wurttemberg", "hockenheim gp"): "Grand Prix",
    ("fuji speedway", "fuji no chicane"): "No Chicane",
    ("fuji international speedway", "fuji no chicane"): "No Chicane",
    ("circuit des 24 heures du mans", "lemans full"): "24 Heures du Mans",
}


def _resolved_track_config(request: dict[str, Any]) -> tuple[str, str]:
    """Return (layout, source) using explicit config first, then safe path aliases.

    ``track_config`` remains authoritative when iRacing supplies it.  When it is
    blank, only a known exact ``(track_name, track_path)`` alias is allowed.
    Unknown ambiguous layouts remain blank and the picker will refuse to guess.
    """

    explicit = normalize_space(str(request.get("track_config") or ""))
    if explicit:
        return explicit, "track_config"

    venue_key = _ascii_match_text(request.get("track_name"))
    path_key = _ascii_match_text(request.get("track_path"))
    alias = TRACK_PATH_LAYOUT_ALIASES.get((venue_key, path_key))
    if alias:
        return alias, "track_path_alias"
    return "", "unresolved"


def _track_match_quality(text: str, desired: str, secondary: str = "") -> float:
    """Return a strict track/layout match score or -1 for an unsafe match.

    Priority:
      100: full Garage61 display label exact after harmless normalization
       95: venue exact + layout exact
       90: venue alias signature exact + layout exact

    If the IBT supplies a layout, a different or missing Garage61 layout is
    always rejected. If the IBT does not supply a layout, an option that has a
    layout suffix is rejected rather than guessed.
    """

    option_venue, option_layout = _track_label_parts(text)
    desired_venue, desired_inline_layout = _track_label_parts(desired)
    wanted_layout = normalize_space(secondary) or desired_inline_layout

    expected = _expected_track_label(desired, secondary)
    if _ascii_match_text(text) == _ascii_match_text(expected):
        return 100.0

    if wanted_layout:
        if not option_layout or _layout_key(option_layout) != _layout_key(wanted_layout):
            return -1.0
    elif option_layout:
        # No config in the IBT means we cannot safely choose among layouts.
        return -1.0

    if _ascii_match_text(option_venue) == _ascii_match_text(desired_venue):
        return 95.0

    option_signature = _venue_signature(option_venue)
    desired_signature = _venue_signature(desired_venue)
    if option_signature and option_signature == desired_signature:
        # Handles known display-name aliases such as "Fuji Speedway" vs
        # "Fuji International Speedway" without relaxing the layout match.
        return 90.0

    return -1.0


def safe_component(value: Any) -> str:
    text = re.sub(r"[<>:\"/\\|?*]+", " ", str(value or "Unknown"))
    return normalize_space(text).strip(" .")[:120] or "Unknown"


def parse_lap_time(value: str) -> float | None:
    match = LAP_TIME_RE.search(value or "")
    if not match:
        return None
    return int(match.group(1)) * 60.0 + int(match.group(2)) + int(match.group(3)) / 1000.0


def format_filename_lap_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}.{remainder:06.3f}"


def first_float(pattern: str, text: str, flags: int = 0) -> float | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def parse_percentages(text: str) -> list[tuple[float, int]]:
    output: list[tuple[float, int]] = []
    for match in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%", text):
        try:
            output.append((float(match.group(1)), match.start()))
        except ValueError:
            continue
    return output


def atomic_json_write(
    path: Path,
    payload: dict[str, Any],
    *,
    replace_retries: int = 12,
    direct_retries: int = 4,
) -> None:
    """Persist JSON without letting transient Windows file locks kill collection.

    The backend polls ``*.status.json`` while the crawler updates it.  On Windows,
    antivirus/indexers or a reader can briefly deny ``os.replace`` with WinError 5.
    Use a per-write temp file, retry replacement with backoff, then fall back to a
    direct write.  StatusWriter additionally treats persistence as best-effort so a
    UI status-file race can never abort browser automation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    token = f"{os.getpid()}-{time.time_ns()}"
    temp = path.with_name(f".{path.name}.{token}.tmp")
    last_error: OSError | None = None

    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

        for attempt in range(max(1, int(replace_retries))):
            try:
                os.replace(temp, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(min(0.025 * (2 ** min(attempt, 5)), 0.60))
            except OSError as exc:
                last_error = exc
                time.sleep(min(0.025 * (2 ** min(attempt, 5)), 0.60))

        # A few Windows tools open files without delete sharing.  Replacing the
        # target can fail even though overwriting it is allowed, so use a bounded
        # direct-write fallback rather than terminating the collector.
        for attempt in range(max(1, int(direct_retries))):
            try:
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(min(0.05 * (attempt + 1), 0.30))
            except OSError as exc:
                last_error = exc
                time.sleep(min(0.05 * (attempt + 1), 0.30))
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass

    if last_error is not None:
        raise last_error
    raise OSError(f"Could not write JSON file: {path}")


def load_json(path: Optional[Path]) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


class StatusWriter:
    def __init__(self, path: Optional[Path], job_id: Optional[str] = None) -> None:
        self.path = path
        self.job_id = job_id
        self.started_at = time.time()
        self.last: dict[str, Any] = {}

    def update(self, state: str, message: str, progress: Optional[float] = None, **extra: Any) -> None:
        now = time.time()
        payload = {
            **self.last,
            "job_id": self.job_id or self.last.get("job_id"),
            "state": state,
            "message": message,
            "started_at": self.last.get("started_at", self.started_at),
            "updated_at": now,
            "elapsed_s": round(now - self.started_at, 1),
            **extra,
        }
        if progress is not None:
            payload["progress"] = max(0.0, min(1.0, float(progress)))
        if state in TERMINAL_STATES:
            payload["finished_at"] = now
            payload["busy"] = False
        else:
            payload["busy"] = True
        self.last = payload
        if self.path is not None:
            try:
                atomic_json_write(self.path, payload)
            except OSError as exc:
                # The status file is observability only. A transient Windows
                # lock must never stop row scanning or close Chromium. The next
                # status update will retry persistence automatically.
                _safe_console_line(
                    f"[STATUS_IO_WARNING] status persistence skipped; collector continues: {exc}"
                )
        _safe_console_line(f"[{state}] {message}")


def parse_row(raw: dict[str, Any], source_url: str) -> dict[str, Any] | None:
    text = normalize_space(str(raw.get("text") or ""))
    extra = " ".join(
        normalize_space(str(value or ""))
        for value in (raw.get("titles") or []) + (raw.get("aria") or [])
    )
    combined = normalize_space(text + " " + extra)
    lap_time_s = parse_lap_time(combined)
    if lap_time_s is None:
        return None

    hrefs = [str(value or "") for value in raw.get("hrefs") or []]
    attrs = [str(value or "") for value in raw.get("attrs") or []]
    identity_blob = " ".join([combined, *hrefs, *attrs])
    lap_match = ULID_RE.search(identity_blob)
    lap_id = lap_match.group(0).upper() if lap_match else None

    links = [normalize_space(str(value or "")) for value in raw.get("link_texts") or []]
    driver_name = None
    for value in links:
        if not value or LAP_TIME_RE.search(value):
            continue
        if value.casefold() in {"setup", "ghost", "analyze", "me", "teammates", "following"}:
            continue
        if re.fullmatch(r"\d+", value):
            continue
        driver_name = value
        break

    weather = next((label for label in WEATHER_LABELS if label.casefold() in combined.casefold()), None)
    temperatures = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*°\s*C", combined, re.IGNORECASE)]
    track_temp_c = temperatures[0] if len(temperatures) >= 1 else None
    air_temp_c = temperatures[1] if len(temperatures) >= 2 else None

    wind_match = re.search(
        r"(\d+(?:\.\d+)?)\s*km\s*/?\s*h\s*,?\s*([NSEW]{1,3})\b",
        combined,
        re.IGNORECASE,
    )
    wind_speed_kph = float(wind_match.group(1)) if wind_match else None
    wind_direction = wind_match.group(2).upper() if wind_match else None

    pressure_hpa = first_float(r"(\d+(?:\.\d+)?)\s*hPa\b", combined, re.IGNORECASE)
    air_density = first_float(r"(\d+(?:\.\d+)?)\s*kg\s*/\s*m(?:³|3)\b", combined, re.IGNORECASE)
    fuel_l = first_float(r"(\d+(?:\.\d+)?)\s*L\b", combined, re.IGNORECASE)

    percentages = parse_percentages(combined)
    humidity_pct = None
    precipitation_pct = None
    track_wetness_pct = None
    pressure_pos = combined.lower().find("hpa")
    density_match = re.search(r"kg\s*/\s*m(?:³|3)", combined, re.IGNORECASE)
    density_pos = density_match.end() if density_match else -1
    if pressure_pos >= 0:
        before_pressure = [value for value, pos in percentages if pos < pressure_pos]
        if before_pressure:
            humidity_pct = before_pressure[-1]
    if density_pos >= 0:
        after_density = [value for value, pos in percentages if pos > density_pos]
        if after_density:
            precipitation_pct = after_density[0]
        if len(after_density) >= 2:
            track_wetness_pct = after_density[1]

    track_usage = next((label for label in TRACK_USAGE_LABELS if label.casefold() in combined.casefold()), None)
    season_match = re.search(r"\b(20\d{2}S\d+)\b", combined)
    rank_match = re.match(r"^\s*(\d{1,4})\b", text)

    return {
        "lap_id": lap_id,
        "driver_name": driver_name,
        "lap_time_s": round(lap_time_s, 3),
        "lap_time": f"{int(lap_time_s // 60)}:{lap_time_s % 60:06.3f}",
        "rank": int(rank_match.group(1)) if rank_match else None,
        "season": season_match.group(1) if season_match else None,
        "weather": weather,
        "track_temp_c": track_temp_c,
        "air_temp_c": air_temp_c,
        "wind_speed_kph": wind_speed_kph,
        "wind_direction": wind_direction,
        "wind_direction_deg": COMPASS_DEGREES.get(wind_direction) if wind_direction else None,
        "relative_humidity_pct": humidity_pct,
        "air_pressure_hpa": pressure_hpa,
        "air_density_kg_m3": air_density,
        "precipitation_pct": precipitation_pct,
        "track_wetness_pct": track_wetness_pct,
        "weather_declared_wet": (track_wetness_pct is not None and track_wetness_pct > 1.0) or (precipitation_pct is not None and precipitation_pct > 0.0),
        "skies_code": SKY_CODES.get(weather.casefold()) if weather else None,
        "track_usage": track_usage,
        "fuel_l": fuel_l,
        "source": "GARAGE61_RENDERED_WEB_UI",
        "source_url": source_url,
        "scraped_at": utc_now_iso(),
        "raw_text": text,
        "setup_access": str(raw.get("setup_access") or "NONE"),
        "setup_available": str(raw.get("setup_access") or "NONE").upper() == "AVAILABLE",
        "setup_href": str(raw.get("setup_href") or "") or None,
        "analyze_present": bool(raw.get("analyze_present")),
        "_hrefs": hrefs,
        "_link_texts": links,
    }


def bind_rows_to_local_csv(rows: list[dict[str, Any]], reference_dir: Path) -> int:
    candidates: list[dict[str, Any]] = []
    for path in reference_dir.glob("Garage 61 - *.csv"):
        match = GARAGE61_CSV_RE.match(path.name)
        if not match:
            continue
        time_text = match.group("time").replace(".", ":", 1)
        lap_time = parse_lap_time(time_text)
        candidates.append({
            "lap_id": match.group("lap_id").upper(),
            "driver": name_token(match.group("driver")),
            "lap_time_s": lap_time,
        })
    bound = 0
    for row in rows:
        if row.get("lap_id"):
            continue
        driver = name_token(row.get("driver_name"))
        lap_time = row.get("lap_time_s")
        if not driver or lap_time is None:
            continue
        matches = [
            item for item in candidates
            if item["driver"] == driver
            and item["lap_time_s"] is not None
            and abs(float(item["lap_time_s"]) - float(lap_time)) <= 0.002
        ]
        if len(matches) == 1:
            row["lap_id"] = matches[0]["lap_id"]
            row["lap_id_binding"] = "LOCAL_CSV_DRIVER_TIME"
            bound += 1
    return bound


def load_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 2, "laps": {}, "context_scans": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"schema_version": 2, "laps": {}, "context_scans": {}}
    if not isinstance(payload, dict):
        return {"schema_version": 2, "laps": {}, "context_scans": {}}
    if not isinstance(payload.get("laps"), dict):
        payload["laps"] = {}
    if not isinstance(payload.get("context_scans"), dict):
        payload["context_scans"] = {}
    return payload


def save_catalog(path: Path, catalog: dict[str, Any]) -> None:
    catalog["schema_version"] = 2
    catalog["updated_at"] = utc_now_iso()
    atomic_json_write(path, catalog)


def write_sidecars(reference_dir: Path, catalog: dict[str, Any]) -> int:
    laps = catalog.get("laps") or {}
    written = 0
    for csv_path in reference_dir.glob("Garage 61 - *.csv"):
        match = GARAGE61_CSV_RE.match(csv_path.name)
        if not match:
            continue
        lap_id = match.group("lap_id").upper()
        metadata = laps.get(lap_id)
        if not isinstance(metadata, dict):
            continue
        sidecar = csv_path.with_suffix(".meta.json")
        payload = {key: value for key, value in metadata.items() if not str(key).startswith("_")}
        payload["lap_id"] = lap_id
        payload["csv_name"] = csv_path.name
        sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1
    return written


def import_downloaded_csvs(download_dir: Path, reference_dir: Path) -> int:
    if not download_dir.exists():
        return 0
    reference_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in download_dir.glob("Garage 61 - *.csv"):
        if not GARAGE61_CSV_RE.match(source.name):
            continue
        target = reference_dir / source.name
        if target.exists() and target.stat().st_size == source.stat().st_size:
            continue
        shutil.copy2(source, target)
        copied += 1
    return copied


def local_lap_ids(reference_dir: Path) -> set[str]:
    output: set[str] = set()
    for path in reference_dir.glob("Garage 61 - *.csv"):
        match = GARAGE61_CSV_RE.match(path.name)
        if match:
            output.add(match.group("lap_id").upper())
    return output


def _car_label_matches(option: str, desired: str) -> bool:
    option_token = name_token(option)
    desired_token = name_token(desired)
    if option_token and desired_token and option_token == desired_token:
        return True
    ignored = {"gt3", "evo", "car", "the"}
    wanted = [token for token in words(desired) if token not in ignored]
    if not wanted:
        return False
    value = normalize_space(option).casefold()
    required = max(1, math.ceil(len(wanted) * 0.75))
    return sum(1 for token in wanted if token in value) >= required


def local_context_lap_ids(reference_dir: Path, request: dict[str, Any]) -> set[str]:
    """Return only cached Garage61 CSV IDs for the current IBT car/track/layout.

    v2.6.3 counted every CSV in the shared reference directory. Once the cache
    contained several other circuits, that could incorrectly satisfy the target
    count and stop a new Hockenheim/Fuji collection before downloading anything.
    """

    track_name = str(request.get("track_name") or "").strip()
    track_config, _track_config_source = _resolved_track_config(request)
    car_name = str(request.get("car_name") or "").strip()
    output: set[str] = set()
    for path in reference_dir.glob("Garage 61 - *.csv"):
        match = GARAGE61_CSV_RE.match(path.name)
        if not match:
            continue
        if car_name and not _car_label_matches(match.group("car"), car_name):
            continue
        if track_name and _track_match_quality(match.group("track"), track_name, track_config) < 90.0:
            continue
        output.add(match.group("lap_id").upper())
    return output


def numeric_similarity(left: Any, right: Any, full_delta: float, zero_delta: float) -> Optional[float]:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    delta = abs(a - b)
    if delta <= full_delta:
        return 1.0
    if delta >= zero_delta:
        return 0.0
    return 1.0 - (delta - full_delta) / max(1e-9, zero_delta - full_delta)


def condition_score(row: dict[str, Any], current: dict[str, Any]) -> float:
    specs = [
        ("track_temp_c", 0.28, 2.5, 15.0),
        ("air_temp_c", 0.12, 2.5, 12.0),
        ("fuel_l", 0.30, 3.0, 35.0),
        ("relative_humidity_pct", 0.06, 10.0, 45.0),
        ("air_pressure_hpa", 0.04, 5.0, 35.0),
        ("air_density_kg_m3", 0.04, 0.02, 0.12),
        ("wind_speed_kph", 0.06, 5.0, 30.0),
        ("precipitation_pct", 0.10, 1.0, 20.0),
    ]
    weighted = 0.0
    total = 0.0
    for key, weight, full_delta, zero_delta in specs:
        score = numeric_similarity(row.get(key), current.get(key), full_delta, zero_delta)
        if score is None:
            continue
        weighted += weight * score
        total += weight
    current_wet = current.get("weather_declared_wet")
    row_wet = row.get("weather_declared_wet")
    if current_wet is not None and row_wet is not None:
        weighted += 0.22 * (1.0 if bool(current_wet) == bool(row_wet) else 0.0)
        total += 0.22
    if total <= 0:
        return 0.5
    return max(0.0, min(1.0, weighted / total))


def rank_rows(rows: list[dict[str, Any]], request: dict[str, Any], target_count: int) -> list[dict[str, Any]]:
    current = request.get("conditions") or {}
    current_best = request.get("current_best_lap_s")
    ranked: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        similarity = condition_score(item, current)
        item["condition_similarity_score"] = round(similarity, 3)
        lap_time = item.get("lap_time_s")
        faster = False
        if current_best is not None and lap_time is not None:
            try:
                faster = float(lap_time) <= float(current_best) - 0.03
            except (TypeError, ValueError):
                faster = False
        item["faster_than_current_best"] = faster
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            0 if item.get("faster_than_current_best") else 1,
            -float(item.get("condition_similarity_score") or 0.0),
            float(item.get("lap_time_s") or math.inf),
        )
    )
    selected: list[dict[str, Any]] = []
    seen_drivers: set[str] = set()
    desired_pool = max(target_count * 3, target_count + 4)
    for item in ranked:
        driver = name_token(item.get("driver_name"))
        if driver and driver in seen_drivers:
            continue
        selected.append(item)
        if driver:
            seen_drivers.add(driver)
        if len(selected) >= desired_pool:
            break
    if len(selected) < desired_pool:
        for item in ranked:
            if item in selected:
                continue
            selected.append(item)
            if len(selected) >= desired_pool:
                break
    return selected


def raw_row_script() -> str:
    return r"""
() => {
  const out = [];
  const seen = new Set();
  const lapTimeRe = /\b\d{1,2}:\d{2}\.\d{3}\b/;

  function visible(el) {
    if (!el || !(el instanceof Element)) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 && r.width > 0 && r.height > 0;
  }

  function norm(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function looksLikeLapRow(el) {
    if (!visible(el)) return false;
    const text = norm(el.innerText || '');
    if (!lapTimeRe.test(text)) return false;
    if (!/\bAnalyze\b/i.test(text)) return false;
    if (text.length < 20 || text.length > 2200) return false;
    const r = el.getBoundingClientRect();
    // Garage61 currently renders each lap as a wide DIV rather than a semantic
    // table row. Keep this broad enough for responsive layouts but reject page
    // containers that span many laps.
    if (r.width < Math.max(520, window.innerWidth * 0.42)) return false;
    if (r.height < 28 || r.height > 220) return false;
    const times = text.match(new RegExp(lapTimeRe.source, 'g')) || [];
    if (times.length > 3) return false;
    return true;
  }

  function minimalLapRowFrom(el) {
    let cur = el;
    let best = null;
    for (let depth = 0; cur && depth < 12; depth += 1, cur = cur.parentElement) {
      if (looksLikeLapRow(cur)) {
        best = cur;
        break; // first matching ancestor is the smallest usable row container
      }
      if (cur === document.body) break;
    }
    return best;
  }

  function actionControl(root, label) {
    const nodes = [...root.querySelectorAll('button, a, [role="button"]')].filter(visible);
    const lowered = label.toLowerCase();
    for (const node of nodes) {
      const blob = [
        node.innerText || '',
        node.getAttribute('aria-label') || '',
        node.getAttribute('title') || '',
        node.getAttribute('data-tooltip-content') || '',
      ].join(' ').toLowerCase();
      if (!blob.includes(lowered)) continue;
      const html = (node.outerHTML || '').toLowerCase();
      const style = getComputedStyle(node);
      const disabled = Boolean(node.disabled) || node.getAttribute('aria-disabled') === 'true' || style.pointerEvents === 'none';
      const locked = /\block(?:ed)?\b|lock[-_ ]?(?:closed)?|lucide-lock|fa-lock|mdi-lock/.test(html + ' ' + blob);
      const wrench = /wrench|spanner|screwdriver|tool|lucide-wrench|fa-wrench|mdi-wrench/.test(html + ' ' + blob);
      return {
        present: true,
        disabled,
        locked,
        wrench,
        href: node.getAttribute('href') || '',
        tag: node.tagName || '',
        class_name: node.getAttribute('class') || '',
      };
    }
    return {present:false, disabled:false, locked:false, wrench:false, href:'', tag:'', class_name:''};
  }

  function addCandidate(el) {
    if (!el || seen.has(el) || !looksLikeLapRow(el)) return;
    seen.add(el);
    const text = norm(el.innerText || '');
    const links = [...el.querySelectorAll('a[href]')];
    const marked = [...el.querySelectorAll('[title], [aria-label], [data-tooltip-content], [data-id], [data-lap-id]')];
    const setup = actionControl(el, 'Setup');
    const analyze = actionControl(el, 'Analyze');
    let setupAccess = 'NONE';
    if (setup.present) {
      if (setup.disabled || setup.locked) setupAccess = 'LOCKED';
      else if (setup.wrench || setup.href || setup.tag === 'BUTTON' || setup.tag === 'A') setupAccess = 'AVAILABLE';
      else setupAccess = 'UNKNOWN';
    }
    const r = el.getBoundingClientRect();
    out.push({
      text,
      hrefs: links.map(a => a.href || ''),
      link_texts: links.map(a => norm(a.innerText || '')),
      titles: marked.map(n => n.getAttribute('title') || n.getAttribute('data-tooltip-content') || '').filter(Boolean),
      aria: marked.map(n => n.getAttribute('aria-label') || '').filter(Boolean),
      attrs: marked.flatMap(n => [n.getAttribute('data-id'), n.getAttribute('data-lap-id')]).filter(Boolean),
      setup_access: setupAccess,
      setup_href: setup.href || '',
      analyze_present: Boolean(analyze.present && !analyze.disabled),
      rect: {left:r.left, top:r.top, width:r.width, height:r.height},
      tag: el.tagName || '',
      role: el.getAttribute('role') || '',
      class_name: el.getAttribute('class') || '',
    });
  }

  // 1) Semantic rows when Garage61 happens to render them.
  for (const el of document.querySelectorAll('tr, [role="row"]')) {
    addCandidate(el);
  }

  // 2) Current Garage61 UI: lap rows are DIV-based. Find the leaf text node
  // containing the lap time and walk upward to the smallest wide ancestor that
  // also contains the Analyze control. This avoids mistaking the entire results
  // list/page for one row and works even when unrelated role=row elements exist.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const value = norm(node.nodeValue || '');
    if (!lapTimeRe.test(value)) continue;
    const row = minimalLapRowFrom(node.parentElement);
    addCandidate(row);
  }

  // Stable top-to-bottom ordering helps scrolling/ranking diagnostics.
  out.sort((a, b) => (a.rect?.top || 0) - (b.rect?.top || 0));
  return out;
}
"""


def row_probe_script() -> str:
    return r"""
() => {
  const lapTimeRe = /\b\d{1,2}:\d{2}\.\d{3}\b/;
  const visible = el => {
    if (!el || !(el instanceof Element)) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  let lapTimeTextNodes = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (lapTimeRe.test(String(node.nodeValue || '').trim())) lapTimeTextNodes += 1;
  }
  const analyzeControls = [...document.querySelectorAll('button, a, [role="button"]')].filter(el => {
    if (!visible(el)) return false;
    const blob = [el.innerText || '', el.getAttribute('aria-label') || '', el.getAttribute('title') || ''].join(' ');
    return /\bAnalyze\b/i.test(blob);
  }).length;
  return {
    url: location.href,
    lap_time_text_nodes: lapTimeTextNodes,
    analyze_controls: analyzeControls,
    semantic_rows: [...document.querySelectorAll('tr, [role="row"]')].filter(visible).length,
    visible_divs: [...document.querySelectorAll('div')].filter(visible).length,
  };
}
"""


def scroll_once(page: Any) -> bool:
    return bool(page.evaluate(
        r"""
() => {
  const all = [...document.querySelectorAll('*')].filter(el => {
    const s = getComputedStyle(el);
    return ['auto','scroll'].includes(s.overflowY) && el.scrollHeight > el.clientHeight + 120;
  });
  const target = all.sort((a,b) => (b.clientHeight*b.clientWidth) - (a.clientHeight*a.clientWidth))[0];
  if (target) {
    const before = target.scrollTop;
    target.scrollBy(0, Math.max(300, target.clientHeight * 0.75));
    return target.scrollTop !== before;
  }
  const before = window.scrollY;
  window.scrollBy(0, Math.max(500, window.innerHeight * 0.75));
  return window.scrollY !== before;
}
"""
    ))


def retry_action(
    action: Callable[[], Any],
    attempts: int,
    status: StatusWriter,
    state: str,
    label: str,
    deadline: float,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, attempts) + 1):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Overall collector deadline exceeded while {label}")
        try:
            status.update(state, f"{label} · 시도 {attempt}/{attempts}", attempt=attempt)
            return action()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            wait_s = min(30.0, 4.0 * (2 ** (attempt - 1)))
            status.update("RETRY_WAIT", f"Garage61 응답 지연: {label} 재시도 전 {wait_s:.0f}초 대기", retry_in_s=wait_s, error=str(exc))
            time.sleep(wait_s)
    raise RuntimeError(f"{label} failed after {attempts} attempt(s): {last_error}")


def wait_for_login(page: Any, status: StatusWriter, deadline: float, settle_ms: int) -> None:
    while time.monotonic() < deadline:
        # The current Garage61 lap-search screen is labelled "Laps". If its
        # track/car pickers are already visible, the persistent profile is
        # authenticated regardless of older navigation labels.
        try:
            if _looks_like_laps_page(page):
                return
        except Exception:
            pass
        body = normalize_space(page.locator("body").inner_text(timeout=30_000))
        lowered = body.casefold()
        logged_in_hints = [
            "dashboard", "search", "laps", "analysis", "analyze",
            "teams", "teammates", "following", "now racing",
        ]
        login_hints = ["log in", "sign in", "로그인"]
        if any(value in lowered for value in logged_in_hints) and not (
            lowered.count("log in") > 2 or lowered.count("sign in") > 2
        ):
            return
        if any(value in lowered for value in login_hints):
            status.update("WAITING_FOR_LOGIN", "Garage61 브라우저에서 로그인이 필요합니다. 로그인 후 자동으로 계속합니다.", progress=0.06)
        page.wait_for_timeout(max(1000, settle_ms))
    raise TimeoutError("Garage61 login wait exceeded overall deadline")


def _picker_probe(page: Any) -> list[dict[str, Any]]:
    """Read the *actual* Garage61 track/car selectors from their top-row inputs.

    The live Garage61 UI keeps two always-present blank ``input`` elements in the
    selector row.  Older probes walked generic DIV wrappers and could therefore
    choose the large parent that contains *both* Track and Car.  That made the
    selected track read back as ``Track + Car`` and the car read back as Track.

    v2.6.9 anchors each selector to its own top-row input first, then walks only
    that input's ancestors to recover the selected label.  This keeps Track and
    Car isolated even when Garage61 changes wrapper roles/classes.
    """

    return page.evaluate(
        r"""
() => {
  const path = (location.pathname || '').toLowerCase();
  const routeLooks = path.includes('/app/laps') || path === '/laps' || path.startsWith('/laps/');
  const bodyLower = (document.body.innerText || '').toLowerCase();
  const jumpVisible = [...document.querySelectorAll('input')].some(el => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    const hint = ((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0 && hint.includes('jump to driver');
  });
  if (!routeLooks && !(jumpVisible && bodyLower.includes('personal bests') && bodyLower.includes('current season'))) {
    return [];
  }
  for (const el of document.querySelectorAll('[data-g61-picker-slot]')) {
    el.removeAttribute('data-g61-picker-slot');
  }

  const vw = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 &&
           r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
  };
  const clean = (value) => ((value || '') + '').replace(/\s+/g, ' ').trim();
  const isNoise = (text) => {
    const t = clean(text).toLowerCase();
    return !t || t === '×' || t === 'x' || t === '▾' || t === '⌄' ||
           t.includes('jump to driver') || t === 'filter:' || t === 'laps';
  };

  // The two Garage61 selector editors are the visible blank inputs in the first
  // row.  Jump-to-driver lives lower on the page and is explicitly excluded.
  const editors = [...document.querySelectorAll('input, [role="combobox"]')]
    .filter(el => {
      if (!visible(el)) return false;
      const r = el.getBoundingClientRect();
      if (r.top < 40 || r.top > 105 || r.width < 120) return false;
      const hint = clean((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
      if (hint.includes('jump to driver')) return false;
      return true;
    })
    .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);

  // Keep at most one editor per side.  Garage61 can expose the same element via
  // both INPUT and role=combobox selectors.
  const sideEditors = [];
  for (const el of editors) {
    const r = el.getBoundingClientRect();
    const side = (r.left + r.width / 2) < vw / 2 ? 0 : 1;
    if (!sideEditors[side]) sideEditors[side] = el;
  }

  const fallbackZone = (slot) => slot === 0
    ? {left: Math.max(8, vw * 0.008), top: 48, width: vw * 0.46, height: 58}
    : {left: vw * 0.505, top: 48, width: vw * 0.47, height: 58};

  const textInsideZone = (zone) => {
    const candidates = [];
    for (const el of document.querySelectorAll('span,div,p,label')) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.top < zone.top - 8 || r.bottom > zone.top + zone.height + 8) continue;
      const cx = r.left + r.width / 2;
      if (cx < zone.left || cx > zone.left + zone.width) continue;
      const text = clean(el.innerText || el.textContent || '');
      if (isNoise(text) || text.length > 180) continue;
      // Reject parent containers containing both selectors or unrelated row text.
      if (text.includes('\n')) continue;
      const lower = text.toLowerCase();
      if (lower.includes('personal bests') || lower.includes('current season') || lower.startsWith('bop:')) continue;
      candidates.push({text, area: r.width * r.height, width: r.width});
    }
    candidates.sort((a, b) => (b.text.length - a.text.length) || (a.area - b.area));
    return candidates.length ? candidates[0].text : '';
  };

  const build = (slot) => {
    const editor = sideEditors[slot] || null;
    let zone = fallbackZone(slot);
    let clickable = editor;
    let label = '';

    if (editor) {
      const er = editor.getBoundingClientRect();
      // Walk upward only from THIS editor.  Choose the smallest visible ancestor
      // shaped like one selector and containing useful text.
      let node = editor;
      const ancestors = [];
      for (let depth = 0; depth < 8 && node; depth += 1, node = node.parentElement) {
        if (!visible(node)) continue;
        const r = node.getBoundingClientRect();
        if (r.top < 35 || r.top > 105 || r.height < 26 || r.height > 72) continue;
        if (r.width < Math.max(180, er.width * 0.85) || r.width > vw * 0.49) continue;
        const text = clean(node.innerText || node.textContent || '');
        ancestors.push({node, r, text, area: r.width * r.height});
      }
      ancestors.sort((a, b) => a.area - b.area);
      const withText = ancestors.find(item => !isNoise(item.text) && item.text.length <= 180);
      const chosen = withText || ancestors[0];
      if (chosen) {
        clickable = chosen.node;
        zone = {left: chosen.r.left, top: chosen.r.top, width: chosen.r.width, height: chosen.r.height};
        label = withText ? withText.text : '';
      } else {
        zone = {left: Math.max(0, er.left - 18), top: Math.max(40, er.top - 18), width: er.width + 70, height: Math.max(36, er.height + 28)};
      }
    }

    if (!label) label = textInsideZone(zone);
    if (!clickable) {
      const x = zone.left + Math.min(zone.width * 0.25, 180);
      const y = zone.top + zone.height / 2;
      clickable = document.elementFromPoint(x, y);
    }
    if (clickable) clickable.setAttribute('data-g61-picker-slot', String(slot));
    return {
      slot,
      tag: clickable ? clickable.tagName : '',
      role: clickable ? (clickable.getAttribute('role') || '') : '',
      text: clean(label),
      left: Math.round(zone.left),
      top: Math.round(zone.top),
      width: Math.round(zone.width),
      height: Math.round(zone.height),
      center_x: Math.round(zone.left + zone.width / 2),
      center_y: Math.round(zone.top + zone.height / 2),
      editor_left: editor ? Math.round(editor.getBoundingClientRect().left) : null,
      editor_width: editor ? Math.round(editor.getBoundingClientRect().width) : null,
    };
  };

  const track = build(0);
  const car = build(1);
  return [track, car];
}
"""
    )

def _looks_like_laps_page(page: Any) -> bool:
    """Return True only for the real Garage61 Laps/Search page.

    v2.6.9 treated ``len(_picker_probe()) >= 2`` as sufficient.  The probe has
    fallback left/right screen zones, so Dashboard could be misread as two
    pickers (for example SPAN/H1 "Dashboard").  That caused filter typing to run
    on Dashboard and Ctrl+A to select the whole page.  Route/page identity is now
    verified independently before any picker code is allowed to run.
    """

    try:
        return bool(page.evaluate(
            r"""
() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 &&
           r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
  };
  const path = (location.pathname || '').toLowerCase();
  const routeLooks = path.includes('/app/laps') || path === '/laps' || path.startsWith('/laps/');
  const body = (document.body.innerText || '').toLowerCase();
  const inputs = [...document.querySelectorAll('input')].filter(visible);
  const jump = inputs.some(el => ((el.getAttribute('placeholder') || '') + ' ' +
                                  (el.getAttribute('aria-label') || '')).toLowerCase().includes('jump to driver'));
  const filterRow = body.includes('personal bests') && body.includes('current season');
  const lapCount = body.includes('laps:');
  const topEditors = [...document.querySelectorAll('input,[role="combobox"]')].filter(el => {
    if (!visible(el)) return false;
    const r = el.getBoundingClientRect();
    const hint = ((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    return r.top >= 40 && r.top <= 110 && r.width >= 120 && !hint.includes('jump to driver');
  }).length;
  const breadcrumb = [...document.querySelectorAll('a,span,div')].some(el => {
    if (!visible(el)) return false;
    const r = el.getBoundingClientRect();
    const text = (el.textContent || '').trim().toLowerCase();
    return text === 'laps' && r.top < 60 && r.left > 180;
  });

  // A Laps route still needs at least one piece of Laps UI. This prevents a
  // transient SPA shell from being treated as ready.  Conversely, a fully
  // rendered Laps page can be accepted even before the URL observer settles.
  if (routeLooks && (jump || filterRow || topEditors >= 2 || breadcrumb || lapCount)) return true;
  return jump && filterRow && (breadcrumb || lapCount || topEditors >= 2);
}
"""
        ))
    except Exception:
        return False


def _wait_for_laps_page(
    page: Any,
    status: StatusWriter,
    deadline: float,
    settle_ms: int,
    label: str,
    max_wait_s: float = 60.0,
) -> bool:
    """Wait patiently for Garage61's slow Laps UI to render."""

    local_deadline = min(deadline, time.monotonic() + max(8.0, float(max_wait_s)))
    last_report = 0.0
    while time.monotonic() < local_deadline:
        if _looks_like_laps_page(page):
            return True
        now = time.monotonic()
        if now - last_report >= 8.0:
            status.update(
                "NAVIGATING",
                f"{label} · Garage61 Laps 화면 로딩 대기",
                url=getattr(page, "url", ""),
            )
            last_report = now
        page.wait_for_timeout(max(900, min(2500, settle_ms)))
    return False

def navigate_to_search(page: Any, status: StatusWriter, retry_count: int, deadline: float, settle_ms: int) -> None:
    """Reach Garage61's real Laps page before any picker interaction.

    The public SPA route ``/app/laps`` is used first because it is more robust
    than depending on sidebar text/wrapper structure.  Sidebar Search remains a
    fallback.  The function never returns while still on Dashboard.
    """

    if _looks_like_laps_page(page):
        return

    def goto_laps() -> None:
        last_error: Optional[Exception] = None

        # Primary path: navigate to the normal Garage61 Laps UI route. This is
        # page navigation only; no hidden/private data endpoint is used.
        try:
            status.update("NAVIGATING", "Garage61 Laps 화면 직접 이동", url=getattr(page, "url", ""))
            page.goto("https://garage61.net/app/laps", wait_until="domcontentloaded", timeout=180_000)
            if _wait_for_laps_page(page, status, deadline, settle_ms, "Laps 직접 경로 이동", 90.0):
                return
        except Exception as exc:
            last_error = exc

        # Fallback: use the actual sidebar anchor. Prefer its href when exposed
        # so a nested SPAN/text node cannot swallow the click.
        try:
            link = page.locator('a').filter(has_text=re.compile(r"^\s*Search\s*$", re.IGNORECASE)).first
            if link.count() > 0:
                href = normalize_space(link.get_attribute("href") or "")
                status.update("NAVIGATING", "Garage61 Search 메뉴 이동", url=getattr(page, "url", ""), href=href)
                if href:
                    if href.startswith("/"):
                        href = "https://garage61.net" + href
                    page.goto(href, wait_until="domcontentloaded", timeout=180_000)
                else:
                    link.click(timeout=30_000, force=True)
                if _wait_for_laps_page(page, status, deadline, settle_ms, "Search 메뉴 이동", 90.0):
                    return
        except Exception as exc:
            last_error = exc

        # Last UI fallback: click exact visible Search text, then verify the page.
        try:
            loc = page.get_by_text(re.compile(r"^Search$", re.IGNORECASE), exact=True).first
            if loc.count() > 0:
                status.update("NAVIGATING", "Garage61 Search 텍스트 클릭", url=getattr(page, "url", ""))
                loc.click(timeout=30_000, force=True)
                if _wait_for_laps_page(page, status, deadline, settle_ms, "Search 클릭 이동", 90.0):
                    return
        except Exception as exc:
            last_error = exc

        raise RuntimeError(f"Garage61 Laps page could not be reached; still at {getattr(page, 'url', '')}: {last_error}")

    retry_action(goto_laps, retry_count, status, "NAVIGATING", "Dashboard → Laps 화면 이동", deadline)
    if not _looks_like_laps_page(page):
        raise RuntimeError(f"Garage61 navigation returned before Laps was ready: {getattr(page, 'url', '')}")
    status.update(
        "APPLYING_FILTERS",
        "Garage61 Laps 화면 확인 완료 · IBT 차량/트랙 필터 준비",
        progress=0.10,
        url=getattr(page, "url", ""),
    )

def _visible_top_controls(page: Any) -> list[dict[str, Any]]:
    # Backward-compatible helper used by diagnostics/tests.
    return _picker_probe(page)


def _locator_from_control(page: Any, control: dict[str, Any]) -> Any:
    slot = int(control.get("slot", 0))
    return page.locator(f'[data-g61-picker-slot="{slot}"]').first


def _option_score(text: str, desired: str, secondary: str = "", kind: str = "track") -> float:
    if kind == "track":
        return _track_match_quality(text, desired, secondary)

    value = normalize_space(text).casefold()
    desired_words = words(desired)
    if not value:
        return -1.0
    score = 0.0
    token = name_token(value)
    desired_token = name_token(desired)
    if desired_token and desired_token == token:
        score += 20.0
    if desired_token and desired_token in token:
        score += 8.0
    for word in desired_words:
        if word in value:
            score += 2.5
    return score


def _match_picker_text(text: str, desired: str, secondary: str = "", kind: str = "track") -> bool:
    value = normalize_space(text).casefold()
    if not value:
        return False
    if kind == "track":
        return _track_match_quality(text, desired, secondary) >= 90.0

    ignored = {"gt3", "evo", "car", "the"}
    tokens = [word for word in words(desired) if word not in ignored]
    required = max(1, math.ceil(len(tokens) * 0.60)) if tokens else 0
    hits = sum(1 for word in tokens if word in value)
    return hits >= required


def current_picker_state(page: Any) -> dict[str, Any]:
    controls = _picker_probe(page)
    return {
        "controls": controls,
        "track": controls[0].get("text") if len(controls) >= 1 else None,
        "car": controls[1].get("text") if len(controls) >= 2 else None,
    }


def _mark_preexisting_inputs(page: Any) -> None:
    try:
        page.evaluate(
            r"""
() => {
  for (const el of document.querySelectorAll('input[data-g61-preexisting-input]')) {
    el.removeAttribute('data-g61-preexisting-input');
  }
  for (const el of document.querySelectorAll('input')) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    if (s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0) {
      el.setAttribute('data-g61-preexisting-input', '1');
    }
  }
}
"""
        )
    except Exception:
        pass


def _close_open_picker(page: Any, settle_ms: int = 500) -> None:
    """Close any stale Garage61 picker before touching the other selector.

    Garage61 keeps the search input focused while a picker is open.  If the
    track picker fails and the car phase starts immediately, keyboard/fill
    events can otherwise land in the still-open track search field.
    """

    for _ in range(2):
        try:
            page.keyboard.press("Escape")
        except Exception:
            break
        try:
            page.wait_for_timeout(max(180, min(500, settle_ms // 2)))
        except Exception:
            pass


def _control_overlap(candidate_box: dict[str, Any], control: dict[str, Any]) -> bool:
    try:
        left = float(candidate_box.get("x", 0.0))
        width = float(candidate_box.get("width", 0.0))
        right = left + width
        c_left = float(control.get("left", 0.0))
        c_width = float(control.get("width", 0.0))
        c_right = c_left + c_width
        overlap = max(0.0, min(right, c_right) - max(left, c_left))
        return overlap >= min(max(width, 1.0), max(c_width, 1.0)) * 0.45
    except Exception:
        return False


def _picker_search_input(page: Any, control: Optional[dict[str, Any]] = None) -> Any | None:
    """Return only the search input belonging to the picker we just opened.

    v2.6.5 accepted whichever editable input remained focused.  When the track
    dropdown stayed open after a failed retry, the subsequent car query was
    therefore written into the track box.  Geometry isolation prevents a left
    track input from ever being used for the right car picker (and vice versa).
    """

    inputs = page.locator('input:visible')
    focused = None
    new_input = None
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        try:
            if not candidate.is_visible() or not candidate.is_editable():
                continue
            placeholder = normalize_space(candidate.get_attribute("placeholder") or "").casefold()
            aria = normalize_space(candidate.get_attribute("aria-label") or "").casefold()
            if "jump to driver" in placeholder or "jump to driver" in aria:
                continue
            if control is not None:
                box = candidate.bounding_box()
                if not box or not _control_overlap(box, control):
                    continue
            if candidate.evaluate("el => document.activeElement === el"):
                focused = candidate
                break
            if candidate.get_attribute("data-g61-preexisting-input") != "1":
                new_input = candidate
        except Exception:
            continue
    return focused or new_input


def _active_element_is_in_control(page: Any, control: dict[str, Any]) -> bool:
    try:
        return bool(page.evaluate(
            r"""
(control) => {
  const el = document.activeElement;
  if (!el) return false;
  const r = el.getBoundingClientRect();
  if (!r || r.width <= 0 || r.height <= 0) return false;
  const left = Math.max(r.left, Number(control.left || 0));
  const right = Math.min(r.right, Number(control.left || 0) + Number(control.width || 0));
  const overlap = Math.max(0, right - left);
  return overlap >= Math.min(r.width, Number(control.width || 0)) * 0.45;
}
""",
            control,
        ))
    except Exception:
        return False



def _picker_zone(page: Any, slot: int, control: Optional[dict[str, Any]] = None) -> dict[str, float]:
    """Return a deterministic top-row zone for the Garage61 track/car picker.

    Garage61's current Laps UI turns the selected-value box itself into the
    search editor.  It is not always exposed as a new input element, so input
    discovery cannot be a prerequisite for typing.  The track picker is the
    left half of the selector row and the car picker is the right half.
    """

    try:
        if control and float(control.get("width") or 0) >= 180:
            left = float(control.get("left") or 0)
            top = float(control.get("top") or 0)
            width = float(control.get("width") or 0)
            height = float(control.get("height") or 0)
            if width > 0 and height > 0:
                return {"left": left, "top": top, "width": width, "height": height}
    except Exception:
        pass

    viewport = page.evaluate(
        "() => ({width: Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0), "
        "height: Math.max(document.documentElement.clientHeight || 0, window.innerHeight || 0)})"
    )
    vw = float(viewport.get("width") or 1440)
    # Current Garage61 Laps layout: two large selectors share the first row.
    if slot == 0:
        return {"left": max(8.0, vw * 0.008), "top": 48.0, "width": vw * 0.46, "height": 58.0}
    return {"left": vw * 0.505, "top": 48.0, "width": vw * 0.47, "height": 58.0}


def _find_picker_editor(page: Any, slot: int, control: Optional[dict[str, Any]] = None) -> Any | None:
    """Find the editable element in the requested top-row picker zone.

    Unlike the older implementation, pre-existing inputs are allowed.  On the
    live Garage61 UI the *same* selected-value element becomes editable when
    the picker opens; it therefore existed before the click and was wrongly
    rejected by v2.6.6.
    """

    zone = _picker_zone(page, slot, control)
    try:
        selector = page.evaluate(
            r"""
({slot, zone}) => {
  for (const el of document.querySelectorAll('[data-g61-picker-editor]')) {
    el.removeAttribute('data-g61-picker-editor');
  }
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 &&
           r.width > 0 && r.height > 0 && r.bottom > 0;
  };
  const overlap = (r) => {
    const l = Math.max(r.left, Number(zone.left || 0));
    const rr = Math.min(r.right, Number(zone.left || 0) + Number(zone.width || 0));
    return Math.max(0, rr - l) >= Math.min(r.width, Number(zone.width || 0)) * 0.35;
  };
  const nodes = [...document.querySelectorAll('input,textarea,[contenteditable="true"],[role="combobox"]')];
  let best = null;
  let bestScore = -1e9;
  for (const el of nodes) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.top > 150 || r.bottom < 25 || !overlap(r)) continue;
    const ph = ((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    if (ph.includes('jump to driver')) continue;
    let score = 0;
    if (document.activeElement === el) score += 200;
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') score += 80;
    if (el.getAttribute('contenteditable') === 'true') score += 50;
    if (el.getAttribute('role') === 'combobox') score += 40;
    score += Math.min(80, r.width / 10);
    score -= Math.abs((r.top + r.bottom) / 2 - 78) * 0.25;
    if (score > bestScore) { best = el; bestScore = score; }
  }
  if (!best) return null;
  best.setAttribute('data-g61-picker-editor', String(slot));
  return {tag: best.tagName, value: best.value || best.innerText || '', left: best.getBoundingClientRect().left};
}
""",
            {"slot": slot, "zone": zone},
        )
        if selector:
            loc = page.locator(f'[data-g61-picker-editor="{slot}"]').first
            if loc.count() > 0 and loc.is_visible():
                return loc
    except Exception:
        pass
    return None


def _direct_type_picker_query(
    page: Any,
    slot: int,
    control: Optional[dict[str, Any]],
    query: str,
    settle_ms: int,
    action_timeout_ms: int,
) -> bool:
    """Type directly into the live Garage61 track/car selector.

    This is intentionally independent from DOM input discovery.  The live UI
    visibly supports clicking the selected track box and typing into that same
    box.  We first focus the left/right picker by its own geometry, then use an
    editor if exposed; otherwise Playwright keyboard input is sent to the
    focused picker.  This also prevents a car query from ever targeting the
    track side because each call receives an explicit slot.
    """

    # Hard route guard. Never send Ctrl+A/type events to Dashboard/body.
    if not _looks_like_laps_page(page):
        return False

    zone = _picker_zone(page, slot, control)
    # Click toward the label/search area rather than the small dropdown arrow.
    x = float(zone["left"]) + min(max(90.0, float(zone["width"]) * 0.20), float(zone["width"]) * 0.42)
    y = float(zone["top"]) + float(zone["height"]) * 0.52
    try:
        page.mouse.click(x, y)
        page.wait_for_timeout(max(350, min(900, settle_ms)))
    except Exception:
        return False

    editor = _find_picker_editor(page, slot, control)
    if editor is not None:
        try:
            editor.click(timeout=action_timeout_ms)
        except Exception:
            pass
        # fill() is the most reliable path for React-controlled input fields.
        try:
            if editor.is_editable():
                editor.fill(query, timeout=action_timeout_ms)
                page.wait_for_timeout(max(700, settle_ms))
                return True
        except Exception:
            pass
        try:
            editor.press("Control+A", timeout=action_timeout_ms)
            editor.type(query, delay=35, timeout=action_timeout_ms)
            page.wait_for_timeout(max(700, settle_ms))
            return True
        except Exception:
            pass

    # Garage61 may keep the picker as a framework div and route keyboard input
    # to an internal editor.  Direct keyboard typeahead still works once the
    # left/right selector itself has been clicked.
    try:
        page.keyboard.press("Control+A")
        page.keyboard.type(query, delay=40)
        page.wait_for_timeout(max(700, settle_ms))
        return True
    except Exception:
        return False


def _option_query_candidates(desired: str, secondary: str = "", kind: str = "track") -> list[str]:
    generic = {
        "speedway", "international", "circuit", "raceway", "track", "gt3",
        "evo", "the", "de", "des", "du", "da", "do", "del", "di",
    }
    tokens = [word for word in words(desired) if word not in generic]
    if kind == "track":
        # Garage61's picker responds more reliably to a short venue query
        # (e.g. "hockenheimring" or "fuji") than to a full label containing
        # parentheses.  The actual option click remains strict Track+Layout.
        output: list[str] = []
        if tokens:
            output.append(tokens[0])
        if len(tokens) >= 2:
            output.append(" ".join(tokens[:2]))
        output.extend([normalize_space(desired), _expected_track_label(desired, secondary)])
        if secondary:
            output.append(normalize_space(secondary))
    else:
        output = [normalize_space(desired)]
        if tokens:
            output.append(tokens[0])
    deduped: list[str] = []
    seen: set[str] = set()
    for value in output:
        key = normalize_space(value).casefold()
        if value and key and key not in seen:
            deduped.append(value)
            seen.add(key)
    return deduped


def _visible_option_points(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        r"""
() => {
  const selectors = [
    '[role="option"]', '[role="menuitem"]', 'li',
    '[data-radix-collection-item]', '[cmdk-item]',
    '[role="listbox"] button', '[role="menu"] button',
    '[role="listbox"] a', '[role="menu"] a'
  ];
  const nodes = [...document.querySelectorAll(selectors.join(','))];
  const out = [];
  const seen = new Set();
  for (const el of nodes) {
    if (seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity || 1) === 0) continue;
    if (r.width < 30 || r.height < 18 || r.bottom < 0 || r.top > window.innerHeight) continue;
    const text = ((el.innerText || el.textContent || '') + '').trim();
    if (!text || text.length > 320) continue;
    out.push({text, x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width, height: r.height});
  }
  return out;
}
"""
    )


def _click_exact_text_option(
    page: Any,
    desired: str,
    secondary: str,
    action_timeout_ms: int,
    kind: str = "track",
) -> bool:
    """Click an exact visible option before falling back to scored DOM scans."""

    expected = _expected_track_label(desired, secondary) if kind == "track" else normalize_space(desired)
    if not expected:
        return False
    try:
        locator = page.get_by_text(expected, exact=True)
        for index in range(min(locator.count(), 12)):
            candidate = locator.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                box = candidate.bounding_box()
                if not box or box.get("width", 0) < 20 or box.get("height", 0) < 14:
                    continue
                candidate.click(timeout=action_timeout_ms)
                page.wait_for_timeout(min(max(900, action_timeout_ms // 40), 2200))
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _click_best_option(
    page: Any,
    desired: str,
    secondary: str,
    action_timeout_ms: int,
    kind: str = "track",
) -> bool:
    points = _visible_option_points(page)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in points:
        score = _option_score(str(item.get("text") or ""), desired, secondary, kind)
        if score >= 0:
            scored.append((score, item))
    if not scored:
        return False
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score = scored[0][0]
    threshold = 90.0 if kind == "track" else 5.0
    if best_score < threshold:
        return False

    # Duplicate DOM wrappers with the same label are harmless. Distinct labels
    # sharing the same best score are ambiguous and must not be guessed.
    top_labels = {
        _ascii_match_text(str(item.get("text") or ""))
        for score, item in scored
        if abs(score - best_score) < 1e-9
    }
    if len(top_labels) > 1:
        return False

    best = scored[0][1]
    try:
        page.mouse.click(float(best["x"]), float(best["y"]))
        page.wait_for_timeout(min(max(900, action_timeout_ms // 40), 2200))
        return True
    except Exception:
        return False


def _save_filter_diagnostics(page: Any, status: StatusWriter, label: str, request: dict[str, Any]) -> list[str]:
    if status.path is None:
        return []
    stem = status.path.with_suffix("")
    png = Path(str(stem) + f".{label}.png")
    jsn = Path(str(stem) + f".{label}.json")
    saved: list[str] = []
    try:
        page.screenshot(path=str(png), full_page=False)
        saved.append(str(png))
    except Exception:
        pass
    try:
        payload = {
            "url": page.url,
            "request": request,
            "picker_state": current_picker_state(page),
            "visible_inputs": page.evaluate(
                r"""
() => [...document.querySelectorAll('input')].map(el => {
  const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
  return {visible:s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0,
          placeholder:el.getAttribute('placeholder')||'', aria:el.getAttribute('aria-label')||'',
          value:el.value||'', left:Math.round(r.left), top:Math.round(r.top), width:Math.round(r.width), height:Math.round(r.height)};
}).filter(x=>x.visible)
"""
            ),
            "active_element": page.evaluate(r"""() => { const el=document.activeElement; if(!el) return null; const r=el.getBoundingClientRect(); return {tag:el.tagName, role:el.getAttribute('role')||'', placeholder:el.getAttribute('placeholder')||'', value:el.value||el.innerText||'', left:Math.round(r.left), top:Math.round(r.top), width:Math.round(r.width), height:Math.round(r.height)}; }"""),
            "picker_zones": {"track": _picker_zone(page, 0), "car": _picker_zone(page, 1)},
            "visible_options": _visible_option_points(page)[:120],
            "layout_resolution": {
                "raw_track_config": str(request.get("track_config") or ""),
                "track_path": str(request.get("track_path") or ""),
                "resolved_track_config": _resolved_track_config(request)[0],
                "source": _resolved_track_config(request)[1],
            },
            "strict_track_match": {
                "expected_label": _expected_track_label(
                    str(request.get("track_name") or ""),
                    _resolved_track_config(request)[0],
                ),
                "candidates": [
                    {
                        "text": item.get("text"),
                        "score": _track_match_quality(
                            str(item.get("text") or ""),
                            str(request.get("track_name") or ""),
                            _resolved_track_config(request)[0],
                        ),
                    }
                    for item in _visible_option_points(page)[:120]
                ],
            },
        }
        atomic_json_write(jsn, payload)
        saved.append(str(jsn))
    except Exception:
        pass
    return saved


def _click_picker_slot(page: Any, control: dict[str, Any], slot: int, timeout_ms: int) -> bool:
    """Open a top picker without depending on the Chromium window being focused."""

    try:
        locator = page.locator(f'[data-g61-picker-slot="{slot}"]').first
        if locator.count() > 0:
            locator.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    try:
        x = float(control.get('center_x') or (float(control.get('left', 0)) + float(control.get('width', 0)) / 2.0))
        y = float(control.get('center_y') or (float(control.get('top', 0)) + float(control.get('height', 0)) / 2.0))
        if x > 0 and y > 0:
            page.mouse.click(x, y)
            return True
    except Exception:
        pass
    return False


def _clear_car_selection(
    page: Any,
    control: Optional[dict[str, Any]],
    settle_ms: int,
    action_timeout_ms: int,
) -> bool:
    """Reset Garage61's car picker to an empty/All cars state before selecting.

    Garage61 can retain one or more car chips from the previous search.  Typing a
    new car without clearing those chips can create a multi-car filter.  Prefer
    the right-side clear/remove button inside the car selector; fall back to the
    focused editor + Backspace behavior used by common React multiselects.
    """

    zone = _picker_zone(page, 1, control)
    try:
        point = page.evaluate(
            r"""
({zone}) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 && r.width > 0 && r.height > 0;
  };
  const out = [];
  for (const el of document.querySelectorAll('button,[role="button"],span,div')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < Number(zone.left) || cx > Number(zone.left) + Number(zone.width)) continue;
    if (cy < Number(zone.top) - 6 || cy > Number(zone.top) + Number(zone.height) + 6) continue;
    const text = ((el.innerText || el.textContent || '') + '').trim();
    const aria = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).toLowerCase();
    const exactX = text === '×' || text === 'x';
    const semantic = /clear|remove|delete|reset/.test(aria);
    if (!exactX && !semantic) continue;
    // Prefer the rightmost clear button: Garage61 exposes an outer clear X near
    // the selector edge and may also show an X inside each selected chip.
    out.push({x: cx, y: cy, right: r.right, width: r.width, height: r.height});
  }
  out.sort((a,b) => b.right - a.right);
  return out.length ? out[0] : null;
}
""",
            {"zone": zone},
        )
        if point:
            page.mouse.click(float(point["x"]), float(point["y"]))
            page.wait_for_timeout(max(500, min(1200, settle_ms)))
            return True
    except Exception:
        pass

    # Fallback: focus the car editor itself.  In Garage61/React multiselects an
    # empty Backspace removes the active chip; doing it twice also handles a
    # residual search string before the chip.
    try:
        x = float(zone["left"]) + min(max(90.0, float(zone["width"]) * 0.20), float(zone["width"]) * 0.42)
        y = float(zone["top"]) + float(zone["height"]) * 0.52
        page.mouse.click(x, y)
        page.wait_for_timeout(250)
        editor = _find_picker_editor(page, 1, control)
        if editor is not None:
            try:
                editor.click(timeout=action_timeout_ms)
            except Exception:
                pass
            try:
                editor.press("Control+A", timeout=action_timeout_ms)
                editor.press("Backspace", timeout=action_timeout_ms)
                editor.press("Backspace", timeout=action_timeout_ms)
                page.wait_for_timeout(max(500, min(1200, settle_ms)))
                return True
            except Exception:
                pass
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(max(500, min(1200, settle_ms)))
        return True
    except Exception:
        return False


def choose_picker_value(
    page: Any,
    kind: str,
    desired: str,
    secondary: str,
    status: StatusWriter,
    settle_ms: int,
    action_timeout_ms: int,
) -> bool:
    slot = 0 if kind == "track" else 1

    # Car filters are sticky on Garage61.  Start each car selection from an
    # explicit empty state so an old car chip cannot survive and turn the query
    # into a multi-car filter.
    if kind == "car":
        _close_open_picker(page, settle_ms)
        pre_state = current_picker_state(page)
        pre_controls = pre_state.get("controls") or []
        pre_control = pre_controls[1] if len(pre_controls) > 1 else None
        _clear_car_selection(page, pre_control, settle_ms, action_timeout_ms)
        _close_open_picker(page, settle_ms)

    # Every attempt starts from a closed selector state.  This is essential for
    # retry isolation: a failed track search must never remain focused when the
    # car selector is handled next.
    _close_open_picker(page, settle_ms)
    state_before = current_picker_state(page)
    current_text = state_before.get(kind)
    if _match_picker_text(str(current_text or ""), desired, secondary, kind):
        return True
    controls = state_before.get("controls") or []
    if len(controls) <= slot:
        return False

    _mark_preexisting_inputs(page)
    control = controls[slot]
    if not _click_picker_slot(page, control, slot, action_timeout_ms):
        return False
    page.wait_for_timeout(max(900, settle_ms))

    queries = _option_query_candidates(desired, secondary, kind)
    for query_index, query in enumerate(queries):
        # v2.6.8: the Garage61 selector itself becomes the search field.  Do not
        # require discovery of a newly-created input; focus the explicit left
        # (track) or right (car) selector and type into it directly.
        typed = _direct_type_picker_query(
            page,
            slot,
            control,
            query,
            settle_ms,
            action_timeout_ms,
        )
        if not typed:
            continue

        # First try the literal label. This is the normal path for tracks such
        # as Hockenheimring Baden-Württemberg (Grand Prix) and exact car names.
        clicked = _click_exact_text_option(page, desired, secondary, action_timeout_ms, kind)
        if not clicked:
            clicked = _click_best_option(page, desired, secondary, action_timeout_ms, kind)
        if not clicked:
            continue

        page.wait_for_timeout(max(1500, settle_ms))
        _close_open_picker(page, settle_ms)
        state_after = current_picker_state(page)
        if _match_picker_text(str(state_after.get(kind) or ""), desired, secondary, kind):
            return True

        # The clicked option did not become the selected top-level value. Reopen
        # only the same picker; never let focus leak into the other selector.
        state_retry = current_picker_state(page)
        retry_controls = state_retry.get("controls") or []
        if len(retry_controls) <= slot:
            break
        control = retry_controls[slot]
        _mark_preexisting_inputs(page)
        if not _click_picker_slot(page, control, slot, action_timeout_ms):
            break
        page.wait_for_timeout(max(700, settle_ms))

    # Final keyboard fallback is permitted only when focus is still inside the
    # intended selector. Selection is accepted only after read-back validation.
    if _active_element_is_in_control(page, control):
        try:
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_timeout(max(1200, settle_ms))
            _close_open_picker(page, settle_ms)
            state_after = current_picker_state(page)
            if _match_picker_text(str(state_after.get(kind) or ""), desired, secondary, kind):
                return True
        except Exception:
            pass

    _close_open_picker(page, settle_ms)
    return False


def apply_ibt_filters(
    page: Any,
    request: dict[str, Any],
    status: StatusWriter,
    retry_count: int,
    deadline: float,
    settle_ms: int,
    action_timeout_ms: int,
) -> dict[str, Any]:
    track_name = str(request.get("track_name") or "").strip()
    track_config, track_config_source = _resolved_track_config(request)
    car_name = str(request.get("car_name") or "").strip()
    result = {
        "track": False,
        "car": False,
        "before": current_picker_state(page),
        "resolved_track_config": track_config,
        "track_config_source": track_config_source,
    }

    if track_name:
        if track_config_source == "track_path_alias":
            status.update(
                "APPLYING_FILTERS",
                f"레이아웃 보정: {request.get('track_path')} → {track_config}",
                requested_track=track_name,
                requested_track_path=request.get("track_path"),
                resolved_track_config=track_config,
            )
        def set_track() -> bool:
            success = choose_picker_value(page, "track", track_name, track_config, status, settle_ms, action_timeout_ms)
            if not success:
                raise RuntimeError("Track picker did not confirm the requested value")
            return True
        try:
            result["track"] = bool(retry_action(set_track, retry_count, status, "APPLYING_FILTERS", f"트랙 필터: {track_name} {track_config}".strip(), deadline))
        except Exception as exc:
            _close_open_picker(page, settle_ms)
            diagnostics = _save_filter_diagnostics(page, status, "track_filter_failed", request)
            status.update(
                "FILTER_WARNING",
                "트랙 필터 자동 설정 실패. 차량 필터는 실행하지 않고 사용자 확인 단계로 전환합니다.",
                error=str(exc),
                diagnostics=diagnostics,
            )

        # Never continue to the car picker while the requested track is still
        # unconfirmed.  v2.6.5 did so, which allowed the car name to be typed
        # into a stale track search field after three failed track retries.
        if not result["track"]:
            _close_open_picker(page, settle_ms)
            result["after"] = current_picker_state(page)
            return result

    if car_name:
        def set_car() -> bool:
            success = choose_picker_value(page, "car", car_name, "", status, settle_ms, action_timeout_ms)
            if not success:
                raise RuntimeError("Car picker did not confirm the requested value")
            return True
        try:
            result["car"] = bool(retry_action(set_car, retry_count, status, "APPLYING_FILTERS", f"차량 필터: {car_name}", deadline))
        except Exception as exc:
            diagnostics = _save_filter_diagnostics(page, status, "car_filter_failed", request)
            status.update("FILTER_WARNING", "차량 필터 자동 설정 실패. 잘못된 차량을 크롤링하지 않고 사용자 확인 단계로 전환합니다.", error=str(exc), diagnostics=diagnostics)

    page.wait_for_timeout(max(settle_ms * 2, 3000))
    result["after"] = current_picker_state(page)
    return result


def page_matches_requested_context(page: Any, request: dict[str, Any]) -> bool:
    """Verify the selected track/car controls themselves, not whole page text.

    v2.6.0 searched the entire document body. An open dropdown can contain the
    requested track name even when the selected track is still the old one,
    which could falsely validate the context and scrape the wrong circuit.
    """

    try:
        state = current_picker_state(page)
    except Exception:
        return False
    track_name = str(request.get("track_name") or "").strip()
    track_config, _track_config_source = _resolved_track_config(request)
    car_name = str(request.get("car_name") or "").strip()
    track_ok = not track_name or _match_picker_text(str(state.get("track") or ""), track_name, track_config, "track")
    car_ok = not car_name or _match_picker_text(str(state.get("car") or ""), car_name, "", "car")
    return bool(track_ok and car_ok)


def wait_for_requested_context(
    page: Any,
    request: dict[str, Any],
    status: StatusWriter,
    deadline: float,
    settle_ms: int,
) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    if page_matches_requested_context(page, request):
        return
    diagnostics = _save_filter_diagnostics(page, status, "waiting_user_filter", request)
    status.update(
        "WAITING_FOR_USER_FILTER",
        "Garage61 차량/트랙 자동 필터 확인에 실패했습니다. 열린 Chromium에서 현재 IBT의 차량·트랙·레이아웃을 선택하면 자동으로 계속합니다.",
        progress=0.16,
        requested_car=request.get("car_name"),
        requested_track=request.get("track_name"),
        requested_config=request.get("track_config"),
        requested_track_path=request.get("track_path"),
        resolved_config=_resolved_track_config(request)[0],
        resolved_config_source=_resolved_track_config(request)[1],
        picker_state=current_picker_state(page),
        diagnostics=diagnostics,
    )
    while time.monotonic() < deadline:
        if page_matches_requested_context(page, request):
            status.update("WAITING_RESULTS", "차량/트랙 조건을 확인했습니다. 결과 로딩을 계속합니다.", progress=0.18, picker_state=current_picker_state(page))
            return
        page.wait_for_timeout(max(1200, settle_ms))
    raise TimeoutError("Garage61 requested car/track filters were not confirmed before deadline")

def collect_rows_slow(
    page: Any,
    source_url: str,
    max_rows: int,
    scroll_rounds: int,
    delay_ms: int,
    target_count: int,
    request: dict[str, Any],
    status: StatusWriter,
    deadline: float,
) -> tuple[list[dict[str, Any]], int]:
    rows: dict[str, dict[str, Any]] = {}
    parsed_cache: list[dict[str, Any]] = []
    stagnant = 0
    for round_index in range(max(1, scroll_rounds)):
        if time.monotonic() >= deadline:
            break
        before = len(rows)
        try:
            raw_items = page.evaluate(raw_row_script())
        except Exception:
            raw_items = []
        for raw in raw_items:
            key_blob = " ".join([str(raw.get("text") or ""), *[str(x) for x in raw.get("hrefs") or []]])
            lap_match = ULID_RE.search(key_blob)
            key = lap_match.group(0).upper() if lap_match else normalize_space(str(raw.get("text") or ""))[:240]
            rows[key] = raw
            if len(rows) >= max_rows:
                break
        if len(rows) == before:
            stagnant += 1
        else:
            stagnant = 0

        parsed_cache = [item for raw in rows.values() if (item := parse_row(raw, source_url))]
        unique_drivers = len({name_token(item.get("driver_name")) for item in parsed_cache if item.get("driver_name")})
        probe = {}
        if not parsed_cache:
            try:
                probe = page.evaluate(row_probe_script()) or {}
            except Exception:
                probe = {}
        status.update(
            "SCANNING_ROWS",
            f"Garage61 결과 수집 중 · {len(parsed_cache)}랩 / {unique_drivers}드라이버",
            progress=min(0.62, 0.22 + 0.40 * ((round_index + 1) / max(1, scroll_rounds))),
            scanned_rows=len(parsed_cache),
            raw_row_candidates=len(raw_items),
            distinct_drivers=unique_drivers,
            scroll_round=round_index + 1,
            row_probe=probe,
        )
        enough_pool = len(parsed_cache) >= max(target_count * 4, 24) and unique_drivers >= max(3, min(target_count, 6))
        if len(rows) >= max_rows or (enough_pool and stagnant >= 2) or stagnant >= 5:
            break
        moved = scroll_once(page)
        page.wait_for_timeout(max(500, delay_ms))
        if not moved and stagnant >= 2:
            break
    return parsed_cache, len(rows)


def _candidate_analyze_href(item: dict[str, Any]) -> Optional[str]:
    hrefs = [str(value or "") for value in item.get("_hrefs") or []]
    link_texts = [str(value or "") for value in item.get("_link_texts") or []]
    for text, href in zip(link_texts, hrefs):
        if href and "analy" in text.casefold():
            return href
    for href in hrefs:
        lower = href.casefold()
        if "analy" in lower or "/lap" in lower:
            return href
    return None


def _candidate_direct_csv_href(item: dict[str, Any]) -> Optional[str]:
    for href in item.get("_hrefs") or []:
        lower = str(href or "").casefold()
        if lower.endswith(".csv") or "csv" in lower and ("download" in lower or "export" in lower):
            return str(href)
    return None


def _save_download(download: Any, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        download.save_as(str(target))
        return target.exists() and target.stat().st_size > 1000
    except Exception:
        return False


def _constructed_csv_name(item: dict[str, Any], request: dict[str, Any]) -> Optional[str]:
    lap_id = item.get("lap_id")
    lap_time_s = item.get("lap_time_s")
    if not lap_id or lap_time_s is None:
        return None
    driver = safe_component(item.get("driver_name") or "Unknown Driver")
    car = safe_component(request.get("car_name") or "Unknown Car")
    track = safe_component(request.get("track_name") or "Unknown Track")
    config = safe_component(request.get("track_config") or "")
    if config and config.casefold() not in track.casefold():
        track = f"{track} ({config})"
    return f"Garage 61 - {driver} - {car} - {track} - {format_filename_lap_time(float(lap_time_s))} - {str(lap_id).upper()}.csv"


def _reset_result_scroll(page: Any) -> None:
    try:
        page.evaluate(
            r"""
() => {
  const all = [...document.querySelectorAll('*')].filter(el => {
    const s = getComputedStyle(el);
    return ['auto','scroll'].includes(s.overflowY) && el.scrollHeight > el.clientHeight + 120;
  });
  const target = all.sort((a,b) => (b.clientHeight*b.clientWidth) - (a.clientHeight*a.clientWidth))[0];
  if (target) target.scrollTop = 0;
  window.scrollTo(0, 0);
}
"""
        )
    except Exception:
        pass


def _matching_rendered_row(page: Any, item: dict[str, Any]) -> Any | None:
    """Locate a rendered Garage61 lap row in the current DIV-based UI.

    v2.6.10 only searched ``tr`` / ``role=row``. Garage61 currently renders the
    visible lap list as ordinary DIV containers, so scanning could succeed only
    on older/alternate layouts and downloads could never rediscover a row. Mark
    the smallest wide ancestor containing exact lap time + driver + Analyze.
    """

    driver = normalize_space(str(item.get("driver_name") or ""))
    lap_time = normalize_space(str(item.get("lap_time") or ""))
    try:
        found = page.evaluate(
            r"""
({driver, lapTime}) => {
  for (const old of document.querySelectorAll('[data-g61-target-lap-row]')) {
    old.removeAttribute('data-g61-target-lap-row');
  }
  const visible = el => {
    if (!el || !(el instanceof Element)) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 && r.width > 0 && r.height > 0;
  };
  const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
  const driverNeedle = norm(driver).toLocaleLowerCase();
  const timeNeedle = norm(lapTime);

  function qualifies(el) {
    if (!visible(el)) return false;
    const text = norm(el.innerText || '');
    if (timeNeedle && !text.includes(timeNeedle)) return false;
    if (driverNeedle && !text.toLocaleLowerCase().includes(driverNeedle)) return false;
    if (!/\bAnalyze\b/i.test(text)) return false;
    const r = el.getBoundingClientRect();
    if (r.width < Math.max(520, window.innerWidth * 0.42)) return false;
    if (r.height < 28 || r.height > 220) return false;
    const times = text.match(/\b\d{1,2}:\d{2}\.\d{3}\b/g) || [];
    return times.length >= 1 && times.length <= 3;
  }

  // Anchor on an exact lap-time text node, then choose the smallest qualifying
  // ancestor. This is resilient to Garage61's non-semantic DIV rows.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const value = norm(node.nodeValue || '');
    if (timeNeedle && value !== timeNeedle && !value.includes(timeNeedle)) continue;
    let cur = node.parentElement;
    for (let depth = 0; cur && depth < 12; depth += 1, cur = cur.parentElement) {
      if (qualifies(cur)) {
        cur.setAttribute('data-g61-target-lap-row', '1');
        return true;
      }
      if (cur === document.body) break;
    }
  }
  return false;
}
""",
            {"driver": driver, "lapTime": lap_time},
        )
        if found:
            row = page.locator('[data-g61-target-lap-row="1"]').first
            if row.count() and row.is_visible():
                return row
    except Exception:
        pass

    # Compatibility fallback for semantic table variants.
    selectors = ['tr:visible', '[role="row"]:visible']
    for selector in selectors:
        rows = page.locator(selector)
        for index in range(min(rows.count(), 500)):
            row = rows.nth(index)
            try:
                row_text = normalize_space(row.inner_text(timeout=1200))
            except Exception:
                continue
            if lap_time and lap_time not in row_text:
                continue
            if driver and driver.casefold() not in row_text.casefold():
                continue
            return row
    return None


def _find_matching_row_with_scroll(
    page: Any,
    item: dict[str, Any],
    settle_ms: int,
    max_rounds: int = 90,
) -> Any | None:
    """Locate a selected Garage61 row even when the virtual list has scrolled away.

    Candidate ranking is done after scanning many rows. By the time downloads
    start, the desired row may no longer be rendered. Reset the main scroll area
    and walk the normal rendered list until driver + exact lap time reappears.
    """

    _reset_result_scroll(page)
    page.wait_for_timeout(max(500, min(1800, settle_ms)))
    stagnant = 0
    for _ in range(max(1, max_rounds)):
        row = _matching_rendered_row(page, item)
        if row is not None:
            try:
                row.scroll_into_view_if_needed(timeout=10_000)
            except Exception:
                pass
            return row
        moved = scroll_once(page)
        if moved:
            stagnant = 0
        else:
            stagnant += 1
        page.wait_for_timeout(max(450, min(1500, settle_ms // 2)))
        if stagnant >= 3:
            break
    return None


def _mark_analyze_menu_button(row: Any) -> bool:
    """Mark the small dropdown button immediately to the right of Analyze.

    Garage61 exposes Export to CSV from this arrow menu, not from the Analyze
    button itself. Geometry is used because the arrow button has little/no text.
    """

    try:
        return bool(row.evaluate(
            r"""
(el) => {
  for (const old of el.querySelectorAll('[data-g61-analyze-menu-button]')) {
    old.removeAttribute('data-g61-analyze-menu-button');
  }
  function visible(node) {
    const r = node.getBoundingClientRect();
    const s = getComputedStyle(node);
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) !== 0 && r.width > 0 && r.height > 0;
  }
  const controls = [...el.querySelectorAll('button, [role="button"], a')].filter(visible);
  const analyze = controls.find(node => {
    const blob = [node.innerText || '', node.getAttribute('aria-label') || '', node.getAttribute('title') || ''].join(' ').trim().toLowerCase();
    return /(^|\s)analyze($|\s)/.test(blob);
  });
  if (!analyze) return false;
  const ar = analyze.getBoundingClientRect();
  const ay = ar.top + ar.height / 2;
  const candidates = controls.filter(node => {
    if (node === analyze) return false;
    const r = node.getBoundingClientRect();
    const cy = r.top + r.height / 2;
    const blob = [node.innerText || '', node.getAttribute('aria-label') || '', node.getAttribute('title') || ''].join(' ').toLowerCase();
    if (/setup|ghost/.test(blob)) return false;
    return r.left >= ar.right - 3 && r.left <= ar.right + 95 && Math.abs(cy - ay) <= Math.max(24, ar.height * 0.8) && r.width <= 90;
  }).sort((a,b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  if (!candidates.length) return false;
  candidates[0].setAttribute('data-g61-analyze-menu-button', '1');
  return true;
}
"""
        ))
    except Exception:
        return False


def _visible_exact_text(page: Any, text: str) -> Any | None:
    locator = page.get_by_text(re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE))
    for index in range(min(locator.count(), 40)):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _target_from_download(
    download: Any,
    item: dict[str, Any],
    request: dict[str, Any],
    reference_dir: Path,
) -> Path | None:
    suggested = Path(str(getattr(download, "suggested_filename", "") or "")).name
    match = GARAGE61_CSV_RE.match(suggested)
    if match:
        item["lap_id"] = match.group("lap_id").upper()
        item["downloaded_csv_name"] = suggested
        item["download_identity_source"] = "GARAGE61_SUGGESTED_FILENAME"
        return reference_dir / suggested

    constructed = _constructed_csv_name(item, request)
    if constructed:
        item["downloaded_csv_name"] = constructed
        item["download_identity_source"] = "CRAWLER_CONSTRUCTED_FILENAME"
        return reference_dir / constructed
    return None


def _download_from_row_menu(
    search_page: Any,
    item: dict[str, Any],
    request: dict[str, Any],
    reference_dir: Path,
    action_timeout_ms: int,
    settle_ms: int,
) -> tuple[bool, str]:
    row = _find_matching_row_with_scroll(search_page, item, settle_ms)
    if row is None:
        return False, "Matching Laps row could not be rendered"
    if not _mark_analyze_menu_button(row):
        return False, "Analyze dropdown arrow was not found next to Analyze"

    arrow = row.locator('[data-g61-analyze-menu-button="1"]').first
    try:
        arrow.click(timeout=action_timeout_ms)
    except Exception as exc:
        return False, f"Analyze dropdown arrow click failed: {exc}"

    # The current Garage61 UI exposes Copy lap ID / Export to CSV / View event.
    # Wait for the exact visible menu entry rather than treating Analyze itself
    # as a download action.
    deadline = time.monotonic() + max(8.0, action_timeout_ms / 1000.0)
    export_control = None
    while time.monotonic() < deadline:
        export_control = _visible_exact_text(search_page, "Export to CSV")
        if export_control is not None:
            break
        search_page.wait_for_timeout(500)
    if export_control is None:
        try:
            search_page.keyboard.press("Escape")
        except Exception:
            pass
        return False, "Analyze dropdown opened but Export to CSV was not visible"

    try:
        with search_page.expect_download(timeout=action_timeout_ms) as download_info:
            export_control.click(timeout=action_timeout_ms)
        download = download_info.value
        target = _target_from_download(download, item, request, reference_dir)
        if target is None:
            return False, f"Downloaded file name could not be linked to a Garage61 lap: {getattr(download, 'suggested_filename', '')}"
        if _save_download(download, target):
            return True, f"Analyze arrow → Export to CSV · {target.name}"
        return False, "Garage61 CSV download completed but save failed"
    except Exception as exc:
        return False, f"Export to CSV did not produce a usable download: {exc}"
    finally:
        try:
            search_page.keyboard.press("Escape")
        except Exception:
            pass


def download_candidate_csv(
    context: Any,
    search_page: Any,
    item: dict[str, Any],
    request: dict[str, Any],
    reference_dir: Path,
    action_timeout_ms: int,
    navigation_timeout_ms: int,
    settle_ms: int,
) -> tuple[bool, str]:
    # If the row already has a Lap ID, avoid opening Garage61 again when this
    # exact context CSV is cached.
    existing_name = _constructed_csv_name(item, request)
    if existing_name:
        target = reference_dir / existing_name
        if target.exists() and target.stat().st_size > 1000:
            return True, "already cached"

    # Primary v2.6.4 path: the actual current Garage61 Laps UI shown by the user.
    success, reason = _download_from_row_menu(
        search_page,
        item,
        request,
        reference_dir,
        action_timeout_ms,
        settle_ms,
    )
    if success:
        return True, reason

    # Conservative fallback for older Garage61 builds where Analyze has a normal
    # href and the page itself exposes an explicit CSV/export control.
    analyze = _candidate_analyze_href(item)
    if not analyze:
        return False, reason
    created_page = context.new_page()
    try:
        created_page.goto(analyze, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
        created_page.wait_for_timeout(max(1200, settle_ms))
        candidates = created_page.locator('a:visible, button:visible, [role="button"]:visible')
        best = None
        best_score = -1
        for index in range(min(candidates.count(), 300)):
            node = candidates.nth(index)
            try:
                node_text = normalize_space(node.inner_text(timeout=1200)).casefold()
            except Exception:
                continue
            score = (8 if "csv" in node_text else 0) + (4 if "download" in node_text else 0) + (3 if "export" in node_text else 0)
            if score > best_score:
                best_score = score
                best = node
        if best is None or best_score < 4:
            return False, reason + "; Analyze page fallback has no CSV control"
        with created_page.expect_download(timeout=action_timeout_ms) as download_info:
            best.click(timeout=action_timeout_ms)
        download = download_info.value
        target = _target_from_download(download, item, request, reference_dir)
        if target is not None and _save_download(download, target):
            return True, f"Analyze page fallback · {target.name}"
        return False, reason + "; Analyze page fallback save failed"
    except Exception as exc:
        return False, reason + f"; Analyze page fallback failed: {exc}"
    finally:
        try:
            created_page.close()
        except Exception:
            pass


def update_catalog_from_rows(
    catalog_path: Path,
    rows: list[dict[str, Any]],
    request: dict[str, Any],
    source_url: str,
    raw_row_count: int,
    downloaded_count: int,
    partial: bool,
) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    laps = catalog.setdefault("laps", {})
    anonymous = catalog.setdefault("unlinked_rows", [])
    linked = 0
    for item in rows:
        clean = {key: value for key, value in item.items() if not str(key).startswith("_")}
        lap_id = clean.get("lap_id")
        if lap_id:
            previous = laps.get(lap_id) if isinstance(laps.get(lap_id), dict) else {}
            laps[lap_id] = {**previous, **clean}
            linked += 1
        else:
            anonymous.append(clean)
    catalog["unlinked_rows"] = anonymous[-500:]
    catalog["last_source_url"] = source_url
    scan = {
        "raw_row_count": raw_row_count,
        "parsed_row_count": len(rows),
        "linked_lap_id_count": linked,
        "downloaded_count": downloaded_count,
        "scraped_at": utc_now_iso(),
        "partial": bool(partial),
        "car_name": request.get("car_name"),
        "track_name": request.get("track_name"),
        "track_config": request.get("track_config"),
        "conditions": request.get("conditions") or {},
    }
    catalog["last_scan"] = scan
    context_key = str(request.get("context_key") or "").strip()
    if context_key:
        catalog.setdefault("context_scans", {})[context_key] = scan
    save_catalog(catalog_path, catalog)
    return catalog


def run_auto(args: argparse.Namespace, request: dict[str, Any], status: StatusWriter) -> int:
    reference_dir = args.reference_dir.expanduser()
    reference_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = (args.profile_dir or default_profile_dir(reference_dir)).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = reference_dir / "garage61_catalog.json"
    deadline = time.monotonic() + max(120, int(args.max_wall_seconds))
    target_count = max(3, min(12, int(request.get("target_count") or 8)))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        status.update("FAILED", "Playwright가 설치되어 있지 않습니다. install_garage61_crawler.bat을 먼저 실행하십시오.", progress=1.0)
        return 2

    imported_before = 0 if args.no_import_downloads else import_downloaded_csvs(args.downloads_dir.expanduser(), reference_dir)
    status.update(
        "STARTING_BROWSER",
        "Garage61 전용 브라우저를 시작합니다. 사이트가 느려도 기존 분석 앱은 계속 사용할 수 있습니다.",
        progress=0.03,
        imported_downloads=imported_before,
        target_count=target_count,
        context_key=request.get("context_key"),
    )

    parsed: list[dict[str, Any]] = []
    raw_count = 0
    downloaded = 0
    download_failures: list[dict[str, Any]] = []
    source_url = ""
    partial = False
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            slow_mo=max(0, int(args.slow_mo_ms)),
        )
        context.set_default_timeout(max(15_000, int(args.action_timeout_ms)))
        page = context.pages[0] if context.pages else context.new_page()

        try:
            def goto_start() -> None:
                page.goto(args.start_url, wait_until="domcontentloaded", timeout=int(args.navigation_timeout_ms))
                page.wait_for_timeout(max(1500, int(args.settle_ms)))
            retry_action(goto_start, int(args.retry_count), status, "NAVIGATING", "Garage61 초기 화면 로드", deadline)
            wait_for_login(page, status, deadline, int(args.settle_ms))
            navigate_to_search(page, status, int(args.retry_count), deadline, int(args.settle_ms))
            status.update("WAITING_RESULTS", "Garage61 Search 화면 안정화 대기", progress=0.12)
            page.wait_for_timeout(max(3000, int(args.settle_ms) * 2))

            filter_result = apply_ibt_filters(
                page,
                request,
                status,
                int(args.retry_count),
                deadline,
                int(args.settle_ms),
                int(args.action_timeout_ms),
            )
            wait_for_requested_context(page, request, status, deadline, int(args.settle_ms))
            source_url = page.url
            status.update(
                "WAITING_RESULTS",
                "필터 적용 후 결과 렌더링을 기다립니다. Garage61이 느리면 이 단계가 길어질 수 있습니다.",
                progress=0.18,
                filter_result=filter_result,
            )
            page.wait_for_timeout(max(4000, int(args.settle_ms) * 2))

            parsed, raw_count = collect_rows_slow(
                page,
                source_url,
                max(40, int(args.max_rows)),
                max(1, int(args.scroll_rounds)),
                max(500, int(args.delay_ms)),
                target_count,
                request,
                status,
                deadline,
            )
            local_bindings = bind_rows_to_local_csv(parsed, reference_dir)
            ranked = rank_rows(parsed, request, target_count)
            setup_available_rows = sum(1 for item in parsed if item.get("setup_available"))
            status.update(
                "RANKING",
                f"{len(parsed)}개 랩에서 현재 IBT 조건과 가까운 기준 후보를 선별했습니다.",
                progress=0.66,
                parsed_rows=len(parsed),
                candidate_pool=len(ranked),
                setup_available_rows=setup_available_rows,
                local_csv_bindings=local_bindings,
            )

            cached_ids = local_context_lap_ids(reference_dir, request)
            needed = max(0, target_count - len(cached_ids))
            # Try a few extra candidates because some Analyze pages may not expose
            # a CSV button or may time out. Downloads remain strictly sequential.
            attempts_budget = min(len(ranked), max(target_count * 2, needed + 4))
            for index, item in enumerate(ranked[:attempts_budget]):
                if time.monotonic() >= deadline:
                    partial = True
                    break
                lap_id = str(item.get("lap_id") or "").upper()
                if lap_id and lap_id in cached_ids:
                    continue
                if len(cached_ids) >= target_count:
                    break
                status.update(
                    "DOWNLOADING",
                    f"Telemetry CSV 확보 중 · {len(cached_ids)}/{target_count}",
                    progress=min(0.90, 0.68 + 0.22 * ((index + 1) / max(1, attempts_budget))),
                    cached_csv_count=len(cached_ids),
                    target_count=target_count,
                    current_driver=item.get("driver_name"),
                    current_lap_time=item.get("lap_time"),
                )
                success = False
                reason = ""
                for download_attempt in range(1, max(1, int(args.retry_count)) + 1):
                    try:
                        success, reason = download_candidate_csv(
                            context,
                            page,
                            item,
                            request,
                            reference_dir,
                            int(args.action_timeout_ms),
                            int(args.navigation_timeout_ms),
                            int(args.settle_ms),
                        )
                        if success:
                            break
                    except Exception as exc:
                        reason = str(exc)
                    if download_attempt < int(args.retry_count):
                        page.wait_for_timeout(min(15_000, 2000 * download_attempt))
                if success:
                    downloaded += 1
                    # The Garage61 suggested filename may reveal the Lap ID only
                    # after Export to CSV. Re-scan the current context cache rather
                    # than relying on the pre-download lap_id variable.
                    cached_ids = local_context_lap_ids(reference_dir, request)
                else:
                    download_failures.append({
                        "lap_id": item.get("lap_id"),
                        "driver_name": item.get("driver_name"),
                        "lap_time": item.get("lap_time"),
                        "reason": reason,
                    })
                page.wait_for_timeout(max(1000, int(args.settle_ms) // 2))

        except Exception as exc:
            partial = bool(parsed)
            status.update(
                "PARTIAL" if partial else "FAILED",
                "Garage61 수집이 시간 초과/페이지 오류로 중단되었습니다. 확보된 캐시와 메타데이터는 유지합니다." if partial else "Garage61 자동 수집에 실패했습니다.",
                progress=0.94 if partial else 1.0,
                error=str(exc),
                parsed_rows=len(parsed),
                downloaded_count=downloaded,
            )
        finally:
            imported_after = 0 if args.no_import_downloads else import_downloaded_csvs(args.downloads_dir.expanduser(), reference_dir)
            catalog = update_catalog_from_rows(
                catalog_path,
                parsed,
                request,
                source_url or page.url,
                raw_count,
                downloaded,
                partial,
            )
            sidecars = write_sidecars(reference_dir, catalog)
            total_local = len(list(reference_dir.glob("Garage 61 - *.csv")))
            context_local = len(local_context_lap_ids(reference_dir, request))
            current_state = status.last.get("state")
            if current_state not in {"FAILED", "PARTIAL"}:
                state = "COMPLETE" if context_local >= target_count else "PARTIAL"
                message = (
                    f"Garage61 기준 데이터 보충 완료 · 현재 차량/트랙 CSV {context_local}개"
                    if state == "COMPLETE"
                    else f"Garage61 검색은 완료했지만 현재 차량/트랙 CSV가 목표에 못 미쳤습니다 · {context_local}/{target_count}개"
                )
                status.update(
                    state,
                    message,
                    progress=1.0,
                    parsed_rows=len(parsed),
                    downloaded_count=downloaded,
                    imported_downloads=imported_before + imported_after,
                    sidecars_written=sidecars,
                    local_csv_count=total_local,
                    context_csv_count=context_local,
                    cached_csv_count=context_local,
                    target_count=target_count,
                    download_failures=download_failures[-20:],
                    catalog_path=str(catalog_path),
                )
            else:
                # Preserve the failure state but attach final local-cache details.
                status.update(
                    current_state,
                    status.last.get("message") or "Garage61 collection finished with errors.",
                    progress=1.0,
                    parsed_rows=len(parsed),
                    downloaded_count=downloaded,
                    imported_downloads=imported_before + imported_after,
                    sidecars_written=sidecars,
                    local_csv_count=total_local,
                    context_csv_count=context_local,
                    cached_csv_count=context_local,
                    target_count=target_count,
                    download_failures=download_failures[-20:],
                    catalog_path=str(catalog_path),
                    error=status.last.get("error"),
                )
            try:
                context.close()
            except Exception:
                pass
    return 0 if status.last.get("state") in {"COMPLETE", "PARTIAL"} else 1


def run_manual(args: argparse.Namespace, status: StatusWriter) -> int:
    reference_dir = args.reference_dir.expanduser()
    reference_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = (args.profile_dir or default_profile_dir(reference_dir)).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = reference_dir / "garage61_catalog.json"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright가 설치되어 있지 않습니다.")
        print("  py -m pip install playwright")
        print("  py -m playwright install chromium")
        return 2

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.start_url, wait_until="domcontentloaded", timeout=int(args.navigation_timeout_ms))
        if not args.headless:
            print("\n1) 열린 브라우저에서 Garage61에 로그인")
            print("2) Search에서 차량/트랙/Telemetry 등 원하는 필터 설정")
            print("3) 결과 목록이 표시되면 이 창으로 돌아와 Enter")
            input("준비되면 Enter > ")
        source_url = page.url
        parsed, raw_count = collect_rows_slow(
            page, source_url, max(20, args.max_rows), max(1, args.scroll_rounds),
            max(200, args.delay_ms), 5, {}, status, time.monotonic() + 3600,
        )
        imported = 0 if args.no_import_downloads else import_downloaded_csvs(args.downloads_dir.expanduser(), reference_dir)
        local_bindings = bind_rows_to_local_csv(parsed, reference_dir)
        catalog = update_catalog_from_rows(catalog_path, parsed, {}, source_url, raw_count, 0, False)
        sidecars = write_sidecars(reference_dir, catalog)
        print("\n완료")
        print(f"  파싱된 랩          : {len(parsed)}")
        print(f"  로컬 CSV 보조 연결 : {local_bindings}")
        print(f"  Downloads CSV 복사 : {imported}")
        print(f"  sidecar JSON 생성  : {sidecars}")
        context.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Garage61 rendered UI collector with IBT-driven automatic mode.")
    parser.add_argument("--reference-dir", type=Path, default=default_reference_dir())
    parser.add_argument("--profile-dir", type=Path, default=None)
    parser.add_argument("--start-url", default="https://garage61.net/app")
    parser.add_argument("--max-rows", type=int, default=240)
    parser.add_argument("--scroll-rounds", type=int, default=45)
    parser.add_argument("--delay-ms", type=int, default=1300)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--no-import-downloads", action="store_true")
    parser.add_argument("--downloads-dir", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--request-file", type=Path, default=None)
    parser.add_argument("--status-file", type=Path, default=None)
    parser.add_argument("--max-wall-seconds", type=int, default=600)
    parser.add_argument("--navigation-timeout-ms", type=int, default=180000)
    parser.add_argument("--action-timeout-ms", type=int, default=60000)
    parser.add_argument("--settle-ms", type=int, default=2400)
    parser.add_argument("--retry-count", type=int, default=3)
    parser.add_argument("--slow-mo-ms", type=int, default=40)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    request = load_json(args.request_file)
    status = StatusWriter(args.status_file, str(request.get("job_id") or "") or None)
    if args.auto:
        if not request:
            status.update("FAILED", "Auto mode requires --request-file.", progress=1.0)
            return 2
        return run_auto(args, request, status)
    return run_manual(args, status)


if __name__ == "__main__":
    raise SystemExit(main())
