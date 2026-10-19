"""Setup conclusions, visualization payloads and deterministic briefing."""

from __future__ import annotations
import math
from typing import Any, Iterable, Optional
import numpy as np
from telemetry_types import LapSegment
from analysis_models import AnalyzeRequest, SessionMode, session_policy

from analysis_config import EXTERNAL_REFERENCE_MIN_DECISION, VISUAL_GRID_POINTS
from analysis_numeric import comparison_input_channels, contiguous_groups, nearest_index, round_or_none, visualization_indices
from setup_analysis import comparison_items, extract_setup_summary, make_adjustment, ride_aero_items, setup_decision_items, setup_exploration_candidate
from closed_loop_coach import session_objective_items
from racing_geometry import gps_path


class SetupAnalysis:
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
                    validation="앞 차고를 1 mm만 올리고 같은 구간의 스플리터 높이(하위 10%)와 랩타임을 A/B 비교",
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
                    validation="리어 ARB를 1단계만 올리고 동일 코너의 조향효율·최저속도·스로틀 복귀를 A/B 비교",
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
                    validation="확정 변경이 아니라 실험 후보입니다. 리어 ARB를 1단계만 올려 같은 코너와 전체 랩을 A/B 비교",
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
            # dict.get evaluates its default even when the channel exists.
            # Resolve channels once instead of allocating three arrays per point.
            zeros = np.zeros(n) if any(key not in target for key in ("speed_kph", "Brake", "Throttle")) else None
            speeds = target.get("speed_kph", zeros)
            brakes = target.get("Brake", zeros)
            throttles = target.get("Throttle", zeros)
            for index in indices:
                if not valid_geo[index]:
                    track_path.append({"lat": None, "lon": None, "distance_pct": round(float(target["distance_pct"][index]), 7)})
                    continue
                track_path.append(
                    {
                        "lat": round(float(lat[index]), 8),
                        "lon": round(float(lon[index]), 8),
                        "distance_pct": round(float(target["distance_pct"][index]), 5),
                        "speed_kph": round(float(speeds[index]), 1),
                        "brake": round(float(brakes[index]), 3),
                        "throttle": round(float(throttles[index]), 3),
                        "delta_s": round(float(delta[index]), 3),
                        "local_loss_s": round(float(local_loss[index]), 5),
                        "time_s": round_or_none(float(target["time_s"][index]), 3) if "time_s" in target else None,
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
                "analysis_grid_points": n,
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
