"""Setup extraction and bounded, evidence-based adjustment candidates."""
from __future__ import annotations
import logging
import math
from typing import Any, Iterable, Optional
from analysis_models import AnalyzeRequest
from analysis_numeric import parse_number, mean_finite
from memory_store import LongTermMemoryStore
from setup_profiles import supplement_setup_summary
logger = logging.getLogger('iRacingSetupTuner')


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
        validator_messages.append("현재값과 목표값은 숫자이고 서로 달라야 합니다")
    if rule and passed:
        step = rule.get("step")
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if step:
            increments = (value - original) / step
            if abs(increments - round(increments)) > 1e-6:
                passed = False
                validator_messages.append(f"{step:g} {unit} 단위로만 바꿀 수 있습니다")
        if minimum is not None and value < minimum - 1e-9:
            passed = False
            validator_messages.append(f"설정 최소값 {minimum:g} {unit}보다 작습니다")
        if maximum is not None and value > maximum + 1e-9:
            passed = False
            validator_messages.append(f"설정 최대값 {maximum:g} {unit}보다 큽니다")
        if passed and maximum is None:
            validator_messages.append("주행 파일에 Garage 최대값이 없어 적용 전에 Garage에서 범위를 확인하세요")
    elif not rule:
        passed = False
        validator_messages.append("이 항목은 조정 규칙이 등록되어 있지 않습니다")

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
