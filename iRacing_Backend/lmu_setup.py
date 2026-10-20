"""Read-only summaries of explicitly supported LMU recording setup metadata.

The numeric ``value`` is an exporter setting index, not a physical measurement.
Display strings are preserved without deriving units, per-lap state or effects.
"""
from __future__ import annotations

import json
import math
import re

from lmu_telemetry import LmuTelemetryFormatError, _quote


MAX_SETUP_BYTES = 256 * 1024
MAX_ENTRIES = 512
CONTEXT = {"CarName": "car_name", "CarClass": "car_class",
           "TrackName": "track_name", "TrackLayout": "track_layout"}

# Names verified in the native exporter. Do not expose arbitrary VM_* metadata.
_VEHICLE_NAMES = """
ANTILOCKBRAKESYSTEMMAP ANTILOCK_BRAKES BRAKE_BALANCE BRAKE_DUCTS BRAKE_DUCTS_REAR
BRAKE_MIGRATION BRAKE_PRESSURE DIFF_COAST DIFF_POWER DIFF_PRELOAD DIFF_PUMP
ELECTRIC_MOTOR_MAP ENGINE_BOOST ENGINE_BRAKEMAP ENGINE_MIXTURE FRONT_ANTISWAY
FRONT_DIFF_COAST FRONT_DIFF_POWER FRONT_DIFF_PRELOAD FRONT_DIFF_PUMP
FRONT_TIRE_COMPOUND FRONT_TOEIN FRONT_TOEOFFSET FRONT_WHEEL_TRACK FRONT_WING
FUEL_CAPACITY FUEL_LEVEL GEAR_AUTODOWNSHIFT GEAR_AUTOUPSHIFT GEAR_FINAL GEAR_REVERSE
HANDBRAKE_PRESSURE HANDFRONTBRAKE_PRESSURE LEFT_CASTER LEFT_FENDER_FLARE LEFT_TRACK_BAR
NUM_PITSTOPS OIL_RADIATOR RATIO_SET REAR_ANTISWAY REAR_TIRE_COMPOUND REAR_TOEIN
REAR_TOEOFFSET REAR_WHEEL_TRACK REAR_WING REGEN_LEVEL REV_LIMITER RIGHT_CASTER
RIGHT_FENDER_FLARE RIGHT_TRACK_BAR STEER_LOCK TORQUE_SPLIT TRACTIONCONTROLMAP
TRACTIONCONTROLPOWERCUTMAP TRACTIONCONTROLSLIPANGLEMAP TRACTION_CONTROL VIRTUAL_ENERGY
WATER_RADIATOR WEIGHT_DISTRIB WEIGHT_LATERAL WEIGHT_VERTICAL WEIGHT_WEDGE
""".split()
_VEHICLE_NAMES += [f"CHASSIS_ADJ_{n:02d}" for n in range(12)]
_VEHICLE_NAMES += [f"GEAR_{n}" for n in range(1, 10)]
_VEHICLE_NAMES += [f"GEAR_UPSHIFT_RPM_{n}" for n in range(1, 9)]
_VEHICLE_NAMES += [f"PITSTOP_{n}" for n in range(1, 4)]
_VEHICLE_NAMES += [f"{axle}_3RD_{kind}" for axle in ("FRONT", "REAR")
                   for kind in ("FASTBUMP", "FASTREBOUND", "PACKERS", "SLOWBUMP",
                                "SLOWREBOUND", "SPRING", "TENDERSPRING", "TENDERSPRINGTRAVEL")]
_WHEEL_LABELS = {"BRAKEDISC": "브레이크 디스크", "BRAKEPAD": "브레이크 패드",
                 "CAMBER": "캠버", "COMPOUND": "타이어 컴파운드", "FASTBUMP": "고속 범프",
                 "FASTREBOUND": "고속 리바운드", "PACKERS": "패커", "PRESSURE": "타이어 압력",
                 "RIDEHEIGHT": "차고", "SLOWBUMP": "저속 범프", "SLOWREBOUND": "저속 리바운드",
                 "SPRING": "스프링", "SRUBBER": "스프링 러버", "TENDERSPRING": "텐더 스프링",
                 "TENDERSPRINGTRAVEL": "텐더 스프링 트래블"}
_POSITIONS = {"FL": "앞 왼쪽", "FR": "앞 오른쪽", "RL": "뒤 왼쪽", "RR": "뒤 오른쪽"}
LABELS = {f"VM_{name}": name.replace("_", " ") for name in _VEHICLE_NAMES}
LABELS.update({f"WM_{kind}-W_{position}": f"{label} · {_POSITIONS[position]}"
               for kind, label in _WHEEL_LABELS.items() for position in _POSITIONS})
LABELS.update({"VM_ANTILOCKBRAKESYSTEMMAP": "ABS 맵", "VM_BRAKE_BALANCE": "브레이크 밸런스",
               "VM_BRAKE_PRESSURE": "브레이크 입력 설정", "VM_DIFF_PRELOAD": "디퍼렌셜 프리로드",
               "VM_FRONT_ANTISWAY": "앞 안티롤바", "VM_REAR_ANTISWAY": "뒤 안티롤바",
               "VM_FRONT_TIRE_COMPOUND": "앞 타이어 컴파운드", "VM_REAR_TIRE_COMPOUND": "뒤 타이어 컴파운드",
               "VM_FUEL_CAPACITY": "연료 용량", "VM_FUEL_LEVEL": "연료 설정",
               "VM_FRONT_TOEIN": "앞 토", "VM_REAR_TOEIN": "뒤 토",
               "VM_FRONT_WING": "앞 윙", "VM_REAR_WING": "뒤 윙",
               "VM_STEER_LOCK": "스티어링 록", "VM_TRACTIONCONTROLMAP": "TC 맵",
               "VM_TRACTIONCONTROLPOWERCUTMAP": "TC 출력 제한 맵",
               "VM_TRACTIONCONTROLSLIPANGLEMAP": "TC 슬립 앵글 맵"})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate setup field")
        result[key] = value
    return result


def _finite(value):
    # These are setting indices/ranges. Keep them exactly representable in JS.
    return type(value) in (int, float) and abs(value) <= 2**53 - 1 and math.isfinite(value)


def _display(value, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return None
    if any(ord(c) < 32 or ord(c) == 127 or c in "\\@<>" for c in value):
        return None
    # A slash may occur in physical units; paths and URLs are not display values.
    if "/" in re.sub(r"/(?:mm|cm|m|s|h|kg)\b", "", value):
        return None
    return value.strip()


def parse_setup(raw):
    """Parse the exporter object through a closed setting/field allowlist."""
    empty = {"status": "UNAVAILABLE", "reason": "SETUP_MISSING", "items": [],
             "counts": {"AVAILABLE": 0, "UNAVAILABLE": 0}, "rejected_entries": 0}
    if raw is None or raw == "":
        return empty
    if not isinstance(raw, str):
        return empty | {"reason": "SETUP_TOO_LARGE_OR_UNSUPPORTED"}
    try:
        if len(raw.encode("utf-8")) > MAX_SETUP_BYTES:
            return empty | {"reason": "SETUP_TOO_LARGE_OR_UNSUPPORTED"}
        data = json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, RecursionError, UnicodeError):
        return empty | {"reason": "SETUP_INVALID_JSON"}
    if not isinstance(data, dict) or len(data) > MAX_ENTRIES:
        return empty | {"reason": "SETUP_UNSUPPORTED_SCHEMA"}
    items, rejected = [], 0
    for key, entry in data.items():
        if key in ("symmetric", "gearGraph"):
            continue  # Not setting entries within this summary's scope.
        if key not in LABELS:
            rejected += 1
            continue
        if not isinstance(entry, dict):
            rejected += 1
            items.append({"key": key, "label": LABELS[key], "available": None,
                          "value": None, "stringValue": None, "minValue": None,
                          "maxValue": None, "status": "UNAVAILABLE", "reason": "INVALID_SETTING"})
            continue
        item = {"key": key, "label": LABELS[key]}
        item["available"] = entry.get("available") if type(entry.get("available")) is bool else None
        for field in ("value", "minValue", "maxValue"):
            value = entry.get(field)
            item[field] = value if _finite(value) else None
        display = entry.get("stringValue")
        no_value = isinstance(display, str) and display.strip().casefold() in ("n/a", "na", "not available")
        item["stringValue"] = None if no_value else _display(display)
        lower, upper, index = (item[field] for field in ("minValue", "maxValue", "value"))
        invalid = (None in (lower, upper, index) or lower > upper or not lower <= index <= upper
                   or item["available"] is None or (item["stringValue"] is None and not no_value))
        reason = ("INVALID_SETTING" if invalid else "SETTING_NOT_AVAILABLE"
                  if item["available"] is not True else "DISPLAY_NOT_AVAILABLE" if no_value else None)
        item.update(status="UNAVAILABLE" if reason else "AVAILABLE", reason=reason)
        rejected += int(invalid)
        items.append(item)
    if not items:
        return empty | {"reason": "SETUP_UNSUPPORTED_SCHEMA", "rejected_entries": rejected}
    counts = {state: sum(item["status"] == state for item in items)
              for state in ("AVAILABLE", "UNAVAILABLE")}
    return {"status": "PARTIAL" if rejected else "AVAILABLE", "reason": None,
            "items": sorted(items, key=lambda row: row["key"]), "counts": counts,
            "rejected_entries": rejected}


def setup_report(reader):
    """Return bounded setup fields from one recording; never return raw JSON."""
    with reader._connection() as con:
        tables = reader._tables(con)
        table = _quote(reader._table(tables, "metadata"))
        count = con.execute(f"SELECT count(*) FROM {table} WHERE key = ?", ["CarSetup"]).fetchone()[0]
        if count > 1:
            raise LmuTelemetryFormatError("셋업 메타데이터가 중복되어 비교할 수 없습니다.")
        rows = con.execute(f"SELECT value FROM {table} WHERE key = ? "
                           "AND octet_length(encode(value)) <= ? LIMIT 1",
                           ["CarSetup", MAX_SETUP_BYTES]).fetchall()
        report = parse_setup(rows[0][0] if rows else None)
        if count and not rows:
            report["reason"] = "SETUP_TOO_LARGE_OR_UNSUPPORTED"
        context = {}
        for source, target in CONTEXT.items():
            values = con.execute(f"SELECT value FROM {table} WHERE key = ? LIMIT 2", [source]).fetchall()
            context[target] = _display(values[0][0], 200) if len(values) == 1 else None
    return report | {"context": context, "scope": "RECORDING_METADATA",
                     "units": "EXPORTER_DISPLAY_UNVERIFIED", "conditions": "NOT_COMPARED",
                     "reference_status": "VALIDITY_UNVERIFIED"}


def compare_setups(reference, target):
    """Compare recorded fields without equating setting indices with physics."""
    a, b = reference["context"], target["context"]
    mismatches = [key for key in CONTEXT.values() if a.get(key) and b.get(key) and a[key] != b[key]]
    missing = [key for key in CONTEXT.values() if not a.get(key) or not b.get(key)]
    compatibility = {"status": "MISMATCH" if mismatches else "UNKNOWN" if missing else "METADATA_MATCH",
                     "mismatched_fields": mismatches, "missing_fields": missing,
                     "vehicle_model": "UNVERIFIED", "conditions": "NOT_COMPARED"}
    result = {"status": "UNAVAILABLE", "reason": None, "compatibility": compatibility,
              "reference": reference, "target": target, "rows": [],
              "counts": {"CHANGED": 0, "UNCHANGED": 0, "UNKNOWN": 0},
              "scope": "RECORDING_METADATA", "effect": "NOT_EVALUATED"}
    if mismatches:
        return result | {"reason": "METADATA_MISMATCH"}
    if any(report["status"] == "UNAVAILABLE" for report in (reference, target)):
        return result | {"reason": "SETUP_UNAVAILABLE"}
    left = {row["key"]: row for row in reference["items"]}
    right = {row["key"]: row for row in target["items"]}
    for key in sorted(left.keys() | right.keys()):
        r, t = left.get(key), right.get(key)
        if not r or not t or r["status"] != "AVAILABLE" or t["status"] != "AVAILABLE":
            state, reason = "UNKNOWN", "ITEM_MISSING_OR_UNAVAILABLE"
        elif any(r[field] != t[field] for field in ("minValue", "maxValue")):
            state, reason = "UNKNOWN", "SETTING_RANGE_CHANGED"
        elif r["value"] != t["value"] and r["stringValue"] == t["stringValue"]:
            state, reason = "UNKNOWN", "VALUE_CHANGED_WITH_SAME_DISPLAY"
        elif r["value"] == t["value"] and r["stringValue"] != t["stringValue"]:
            state, reason = "UNKNOWN", "DISPLAY_TEXT_CHANGED"
        elif r["value"] != t["value"]:
            state, reason = "CHANGED", "RECORDED_VALUE_AND_DISPLAY_CHANGED"
        else:
            state, reason = "UNCHANGED", None
        result["counts"][state] += 1
        result["rows"].append({"key": key, "label": (r or t)["label"], "state": state,
                               "reason": reason, "reference": r, "target": t})
    result["status"] = ("PARTIAL" if missing or result["counts"]["UNKNOWN"]
                        or any(x["status"] == "PARTIAL" for x in (reference, target)) else "AVAILABLE")
    return result
