"""Pure setup evidence rules shared by analysis and persistence migrations."""
from __future__ import annotations
import math
import re
from typing import Any, Optional
import numpy as np


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


def canonical_setup_changes(full_setup_diff: dict[str, Any]) -> list[dict[str, Any]]:
    """Map flattened iRacing setup differences to stable canonical controls.

    Kept independent of application startup so schema migration can backfill
    old A/B records without opening other stores.
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


def speed_class_label(value: str) -> str:
    return {"LOW": "저속", "MEDIUM": "중속", "HIGH": "고속"}.get(str(value or "").upper(), str(value or "일반"))


def coach_issue_label(issue: str) -> str:
    mapping = {
        "ENTRY": "코너 진입", "BRAKING": "제동", "MID_CORNER": "미드코너 속도",
        "EXIT": "탈출 가속", "BALANCE_SUSPECT": "회전/밸런스", "DRIVER_VARIANCE": "입력 재현성",
        "GAIN": "강점 구간", "CONSISTENT": "재현 구간",
    }
    return mapping.get(str(issue or "").upper(), str(issue or "일반"))
