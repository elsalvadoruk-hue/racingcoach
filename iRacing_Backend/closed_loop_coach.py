"""Deterministic coaching from measured corner losses and eligible A/B evidence."""
from __future__ import annotations
from collections import defaultdict
from typing import Any, Optional
import numpy as np
from analysis_models import AnalyzeRequest, PitRule, SessionMode
from analysis_numeric import parse_number, pct_distance, round_or_none
from setup_evidence import PERSONALIZATION_SETTING_META, setup_learning_eligibility
from practice_metrics import success_criteria


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


_SETUP_MEMORY_KEY_HINTS: dict[str, tuple[str, ...]] = {
    key: tuple(meta.get("hints") or ()) for key, meta in PERSONALIZATION_SETTING_META.items()
}


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
        action = f"브레이킹 시작점은 유지하고 브레이크를 완전히 놓는 지점을 약 {min(10.0, max(3.0, brake_release_m * 0.65)):.0f} m 앞당기되 압력을 부드럽게 줄이십시오."
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
        success = ["평균 조향량 2° 이상 감소", "최저속도 유지 또는 증가", "조향 대비 회전량 증가"]
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
        "action": action, "do_not_change": do_not_change, "success_criteria": success_criteria(cause) or success,
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
            "after_run": f"새 주행 기록에서 코너 입력을 확인하세요. A/B 효과 판정은 각 셋업 유효랩 {ab_required_laps}개부터이며 자연 편차도 함께 계산합니다. 표본 부족은 실패가 아니라 미확정입니다.",
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
            "after_run": "새 주행 기록을 같은 세션 유형으로 저장한 뒤 로컬 A/B에서 A=현재 기록, B=새 기록을 선택합니다. 평균 페이스뿐 아니라 일관성과 운전 용이성도 함께 판정합니다.",
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
            "after_run": "새 주행 기록을 다시 분석해 같은 방향의 손실이 줄었는지 확인합니다. 완벽히 동일한 랩은 요구하지 않습니다.",
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
            "after_run": "새 주행 기록을 분석하면 현재 자연 편차와 반복 손실을 다시 계산합니다. 재현성 부족 자체는 실패로 처리하지 않습니다.",
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
            "after_run": "새 주행 기록을 분석해 다음 원인/셋업 후보를 계산합니다.",
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
