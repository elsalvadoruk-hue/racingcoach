"""Observed reference conditions and strict vehicle/track/layout compatibility."""
from __future__ import annotations
import math
import re
from typing import Any, Optional
import numpy as np
from telemetry_types import ParsedTelemetry, LapSegment
from analysis_numeric import round_or_none


def _finite_channel_median(
    telemetry: ParsedTelemetry,
    name: str,
    target: Optional["LapSegment"] = None,
) -> Optional[float]:
    values = telemetry.channels.get(name)
    if values is None:
        return None
    if target is not None and len(values):
        start = max(0, int(target.start_idx))
        end = min(len(values), int(target.end_idx) + 1)
        if end > start:
            values = values[start:end]
    # A lap needs only its own samples. Converting the entire recording first
    # repeatedly copied every other lap's float32 channels during condition
    # matching and indexing; slicing first keeps the same float64 median.
    numeric = values.astype(float, copy=False)
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
