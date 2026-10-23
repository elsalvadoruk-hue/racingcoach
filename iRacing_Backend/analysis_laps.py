"""Lap selection, reproducibility, time grids and comparison baselines."""

from __future__ import annotations
import math
from typing import Any, Iterable, Optional
import numpy as np
from telemetry_types import LapSegment
from analysis_models import AnalyzeRequest, SessionMode, session_policy

from dataclasses import replace
from analysis_numeric import canonical_hash, format_lap_time, interpolate_observed, round_or_none
from analysis_support import clean_segment_context, mask_grid_to_clean_windows
from telemetry_channels import CORE_CHANNELS, FEATURE_CHANNELS
from setup_analysis import extract_setup_summary
from setup_profiles import vehicle_setup_capabilities
from reference_conditions import telemetry_reference_conditions
from corner_events import raw_lap


# Only these native channels feed corner events and handling comparisons.
# Other callers of raw_lap still receive every eligible channel by default.
_CORNER_RAW_CHANNELS = ('Speed', 'SteeringWheelAngle', 'YawRate', 'LatAccel', 'Brake', 'Throttle', 'BrakeABSactive')


class LapAnalysis:
    def _raw_lap_for(self, grid):
        number = grid.get('_lap_number')
        registered = getattr(self, '_grid_ids', {}).get(id(grid))
        if number is None or registered is None or registered[0] is not grid or registered[1][0] != 'lap':
            return None
        cache = self._raw_lap_cache
        if number not in cache:
            lap = next((lap for lap in self.telemetry.laps if lap.lap_number == number), None)
            cache[number] = raw_lap(self, lap, requested=_CORNER_RAW_CHANNELS) if lap is not None else None
        return cache[number]

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
            "setup_hash": canonical_hash(self.telemetry.setup) if self.telemetry.setup else None,
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

    def _lap_time_use(self, lap: "LapSegment") -> Optional[dict[str, Any]]:
        """Seconds and shares of a lap spent braking, coasting, at part and full throttle, and cornering.

        The first four split the lap; cornering overlaps them. Without brake or throttle the lap gets None
        (nothing is guessed); without lateral acceleration only cornering is left out."""
        brake, throttle = self.channels.get("Brake"), self.channels.get("Throttle")
        session_time = self.channels.get("SessionTime")
        start, end = lap.start_idx, lap.end_idx
        if brake is None or throttle is None or end <= start:
            return None
        if session_time is not None:
            steps = np.diff(session_time[start:end + 2].astype(float))
            if len(steps) < end - start + 1:
                steps = np.r_[steps, np.median(steps) if len(steps) else 0.0]
        else:
            steps = np.full(end - start + 1, 1.0 / max(1, self.telemetry.tick_rate))
        steps = np.where(np.isfinite(steps) & (steps > 0) & (steps <= self.TIME_USE_GAP_S), steps, 0.0)
        lap_brake = brake[start:end + 1].astype(float)
        lap_throttle = throttle[start:end + 1].astype(float)
        known = np.isfinite(lap_brake) & np.isfinite(lap_throttle)
        braking = known & (lap_brake > self.TIME_USE_BRAKE)
        free = known & ~braking
        full = free & (lap_throttle >= self.TIME_USE_FULL)
        coast = free & (lap_throttle < self.TIME_USE_OFF)
        part = free & ~full & ~coast
        total = float(np.sum(steps[known]))
        if total <= 0:
            return None
        seconds = {name: float(np.sum(steps[mask])) for name, mask in
                   (("brake", braking), ("coast", coast), ("part_throttle", part), ("full_throttle", full))}
        lateral = self.channels.get("LatAccel")
        if lateral is not None:
            lap_lateral = np.abs(lateral[start:end + 1].astype(float))
            seconds["corner"] = float(np.sum(steps[known & np.isfinite(lap_lateral) & (lap_lateral >= self.TIME_USE_CORNER_G * 9.80665)]))
        return {
            "seconds": {name: round(value, 2) for name, value in seconds.items()},
            "share_pct": {name: round(value / total * 100.0, 1) for name, value in seconds.items()},
            "counted_s": round(total, 2),
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
                    "time_use": self._lap_time_use(lap) if lap.complete else None,
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
        return float(np.min(valid)) if len(valid) else None

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

        source_path = getattr(self.telemetry, "path", None)
        output: dict[str, np.ndarray[Any, Any]] = {
            "distance_pct": grid, "_lap_number": lap.lap_number,
            "_game": self.telemetry.metadata.get("game") or "iracing",
            "_source_file": str(source_path.resolve()) if source_path is not None else None,
        }
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
            "RPM",
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
            # iRacing keeps the previous lap's time in LapCurrentLapTime for 1-2 s after the line (seen in
            # every file checked on 2026-10-15: Road America, VIR, Monza, Spa, Long Beach). Trust it only when it
            # has already restarted; otherwise the time since the line comes from the distance into the lap.
            current_time = self.channels.get("LapCurrentLapTime")
            lap_clock = float(current_time[start]) if current_time is not None else math.nan
            speed_channel = self.channels.get("Speed")
            start_speed = float(speed_channel[start]) if speed_channel is not None else math.nan
            if math.isfinite(lap_clock) and 0.0 <= lap_clock < 1.0:
                origin_offset = lap_clock
            elif math.isfinite(float(raw_distance[0])) and 0.0 <= float(raw_distance[0]) < 0.05 and math.isfinite(start_speed) and start_speed > 1.0:
                origin_offset = min(1.0, float(raw_distance[0]) * self.track_length_m / start_speed)
            else:
                origin_offset = 0.0
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
        comparison.update(self._rolling_best())
        if reference_grid is not None:
            delta = target_grid["time_s"] - reference_grid["time_s"]
            finite_delta = delta[np.isfinite(delta)]
            comparison["delta_min_s"] = round_or_none(float(np.min(finite_delta)), 3) if len(finite_delta) else None
            comparison["delta_max_s"] = round_or_none(float(np.max(finite_delta)), 3) if len(finite_delta) else None
        return comparison

    def _rolling_best(self) -> dict[str, Any]:
        """The fastest lap actually driven from any point to the same point a lap later (RaceStudio 3's best
        rolling time), over two back-to-back valid laps. Lap times anchor each lap's ends, as for the optimal;
        a stretch with a hole in the recording is not used."""
        empty = {"rolling_best_time_s": None, "rolling_best_time": None, "rolling_best_start_pct": None,
                 "rolling_best_laps": None, "rolling_best_gain_s": None}
        laps = sorted(self.valid_laps, key=lambda lap: lap.lap_number)
        if len(laps) < 2:
            return empty
        grids = {lap.lap_number: self._lap_grid(lap) for lap in laps}
        best_time = float(min(lap.lap_time_s for lap in laps))
        found: Optional[tuple[float, float, int, int]] = None
        for first, second in zip(laps, laps[1:]):
            if second.lap_number != first.lap_number + 1:
                continue
            ga, gb = grids[first.lap_number], grids[second.lap_number]
            ta, tb = ga["time_s"].astype(float).copy(), gb["time_s"].astype(float).copy()
            ca = np.asarray(ga.get("_coverage", np.isfinite(ta)), dtype=bool).copy()
            cb = np.asarray(gb.get("_coverage", np.isfinite(tb)), dtype=bool).copy()
            ta[0], ta[-1], tb[0], tb[-1] = 0.0, float(first.lap_time_s), 0.0, float(second.lap_time_s)
            ca[0] = ca[-1] = cb[0] = cb[-1] = True
            tail_known = np.logical_and.accumulate(ca[::-1])[::-1]      # first lap observed from here to its end
            head_known = np.logical_and.accumulate(cb)                  # second lap observed from its start to here
            rolling = (float(first.lap_time_s) - ta) + tb
            usable = tail_known & head_known & np.isfinite(rolling)
            if not np.any(usable):
                continue
            index = int(np.flatnonzero(usable)[np.argmin(rolling[usable])])
            if found is None or rolling[index] < found[0]:
                found = (float(rolling[index]), float(ga["distance_pct"][index]), first.lap_number, second.lap_number)
        if found is None:
            return empty
        time, start_pct, lap_a, lap_b = found
        return {"rolling_best_time_s": round(time, 4), "rolling_best_time": format_lap_time(time),
                "rolling_best_start_pct": round(start_pct, 4), "rolling_best_laps": [lap_a, lap_b],
                "rolling_best_gain_s": round(max(0.0, best_time - time), 3)}

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
