"""External racing-line comparisons and conditional setup evidence."""

from __future__ import annotations
import math
from typing import Any, Iterable, Optional
import numpy as np
from telemetry_types import LapSegment
from analysis_models import AnalyzeRequest, SessionMode, session_policy

from analysis_config import (RACING_LINE_COMPARE_MAX_CORNERS, RACING_LINE_COMPARE_MIN_LOSS_S,
    RACING_LINE_COMPARE_MIN_P75_GAP_M, RACING_LINE_COMPARE_MIN_PEAK_GAP_M,
    RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH, RACING_LINE_COMPARE_POINTS)
from analysis_numeric import contiguous_groups, downsample_indices, pct_distance, round_or_none, smooth
from setup_analysis import make_adjustment, make_directional_adjustment


class ExternalAnalysis:
    def _external_racing_line_comparison(
        self,
        target_grid: dict[str, np.ndarray[Any, Any]],
        reference_grid: dict[str, np.ndarray[Any, Any]],
        corners: list[dict[str, Any]],
        reference_count: int,
        unique_driver_count: int,
        consensus_tier: str,
    ) -> dict[str, Any]:
        """Return at most two corners where a line difference and speed loss coexist.

        The purpose is coaching, not exhaustive GPS inspection.  A corner is
        eligible only when the target is measurably slower *and* the target
        path departs from the condition-weighted Garage61 median line.  The
        output uses local metre coordinates so the browser can draw a compact
        SVG without exposing raw GPS traces to any remote service.
        """
        base: dict[str, Any] = {
            "status": "NO_QUALIFYING_CORNERS",
            "max_corners": RACING_LINE_COMPARE_MAX_CORNERS,
            "reference_type": "GARAGE61_CONDITION_WEIGHTED_MEDIAN_LINE",
            "reference_count": int(reference_count),
            "unique_driver_count": int(unique_driver_count),
            "consensus_tier": consensus_tier,
            "thresholds": {
                "minimum_corner_loss_s": round_or_none(RACING_LINE_COMPARE_MIN_LOSS_S, 3),
                "minimum_speed_deficit_kph": round_or_none(RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH, 1),
                "minimum_p75_line_gap_m": round_or_none(RACING_LINE_COMPARE_MIN_P75_GAP_M, 2),
                "minimum_peak_line_gap_m": round_or_none(RACING_LINE_COMPARE_MIN_PEAK_GAP_M, 2),
            },
            "corners": [],
            "note": (
                "모든 코너를 보여주지 않고, 속도 손실과 라인 차이가 동시에 확인된 개선 가치 상위 2개만 표시합니다."
            ),
        }
        required = ("Lat", "Lon", "speed_kph", "distance_pct")
        if not all(key in target_grid and key in reference_grid for key in required):
            base["status"] = "MISSING_GPS_OR_SPEED"
            base["note"] = "본인 또는 Garage61 기준에 Lat/Lon/Speed 채널이 없어 라인 비교를 만들 수 없습니다."
            return base

        target_lat = np.asarray(target_grid["Lat"], dtype=float)
        target_lon = np.asarray(target_grid["Lon"], dtype=float)
        ref_lat = np.asarray(reference_grid["Lat"], dtype=float)
        ref_lon = np.asarray(reference_grid["Lon"], dtype=float)
        distance = np.asarray(target_grid["distance_pct"], dtype=float)
        target_speed = np.asarray(target_grid["speed_kph"], dtype=float)
        ref_speed = np.asarray(reference_grid["speed_kph"], dtype=float)
        n = len(distance)
        if n < 100:
            base["status"] = "INSUFFICIENT_POINTS"
            return base

        valid = (
            np.isfinite(target_lat) & np.isfinite(target_lon)
            & np.isfinite(ref_lat) & np.isfinite(ref_lon)
            & (np.abs(target_lat) > 1e-7) & (np.abs(target_lon) > 1e-7)
            & (np.abs(ref_lat) > 1e-7) & (np.abs(ref_lon) > 1e-7)
        )
        if np.count_nonzero(valid) < int(n * 0.60):
            base["status"] = "INSUFFICIENT_GPS_COVERAGE"
            base["note"] = "GPS 유효 구간이 부족해 라인 차이를 안정적으로 계산하지 않았습니다."
            return base

        lat0 = float(np.nanmedian(ref_lat[valid]))
        lon0 = float(np.nanmedian(ref_lon[valid]))
        earth_r = 6371000.0
        cos_lat = math.cos(math.radians(lat0))

        def to_xy(lat: np.ndarray[Any, Any], lon: np.ndarray[Any, Any]) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
            x = earth_r * cos_lat * np.radians(lon - lon0)
            y = earth_r * np.radians(lat - lat0)
            return x.astype(float), y.astype(float)

        tx, ty = to_xy(target_lat, target_lon)
        rx, ry = to_xy(ref_lat, ref_lon)

        # Remove a possible constant coordinate-system translation.  This does
        # not remove actual racing-line shape differences within a corner.
        offset_x = float(np.nanmedian((tx - rx)[valid]))
        offset_y = float(np.nanmedian((ty - ry)[valid]))
        tx = tx - offset_x
        ty = ty - offset_y

        # Smooth only for the diagram/line-gap calculation. Telemetry metrics
        # elsewhere continue to use the original unsmoothed traces.
        window = max(5, min(17, (int(n * 0.004) | 1)))
        txs, tys = smooth(tx, window), smooth(ty, window)
        rxs, rys = smooth(rx, window), smooth(ry, window)
        tangent_x = np.gradient(rxs)
        tangent_y = np.gradient(rys)
        tangent_norm = np.hypot(tangent_x, tangent_y)
        tangent_norm = np.where(tangent_norm > 1e-6, tangent_norm, 1.0)
        tangent_x = tangent_x / tangent_norm
        tangent_y = tangent_y / tangent_norm
        dx = txs - rxs
        dy = tys - rys
        lateral = dx * tangent_y - dy * tangent_x
        abs_lateral = np.abs(lateral)

        def idx_for_pct(value: Any) -> Optional[int]:
            try:
                pct = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(pct):
                return None
            return int(np.clip(np.searchsorted(distance, pct), 0, n - 1))

        def marker_at(pct: Any, x: np.ndarray[Any, Any], y: np.ndarray[Any, Any], speed: np.ndarray[Any, Any]) -> Optional[dict[str, Any]]:
            index = idx_for_pct(pct)
            if index is None or not (math.isfinite(float(x[index])) and math.isfinite(float(y[index]))):
                return None
            return {
                "x_m": round_or_none(float(x[index]), 3),
                "y_m": round_or_none(float(y[index]), 3),
                "speed_kph": round_or_none(float(speed[index]), 1),
                "distance_pct": round_or_none(float(distance[index]), 5),
            }

        candidates: list[dict[str, Any]] = []
        for corner in corners:
            if not corner.get("measurement_usable", True):
                continue
            entry = idx_for_pct(corner.get("entry_pct"))
            apex = idx_for_pct(corner.get("apex_pct"))
            exit_idx = idx_for_pct(corner.get("exit_pct"))
            if entry is None or apex is None or exit_idx is None or exit_idx <= entry + 5:
                continue

            margin_points = max(3, int(round((45.0 / max(500.0, float(self.track_length_m))) * n)))
            start = max(0, entry - margin_points)
            stop = min(n - 1, exit_idx + margin_points)
            sl = slice(start, stop + 1)
            local_valid = valid[sl] & np.isfinite(abs_lateral[sl]) & np.isfinite(target_speed[sl]) & np.isfinite(ref_speed[sl])
            if np.count_nonzero(local_valid) < 12:
                continue

            gaps = abs_lateral[sl][local_valid]
            speed_deficits = (ref_speed[sl] - target_speed[sl])[local_valid]
            median_speed_deficit = float(np.nanmedian(speed_deficits))
            p75_speed_deficit = float(np.nanpercentile(speed_deficits, 75))
            p75_gap = float(np.nanpercentile(gaps, 75))
            peak_gap = float(np.nanpercentile(gaps, 95))
            mean_gap = float(np.nanmean(gaps))
            delta_s = float(corner.get("delta_s") or 0.0)
            target_metrics = corner.get("target") or {}
            ref_metrics = corner.get("reference") or {}
            min_speed_deficit = float((ref_metrics.get("min_speed_kph") or 0.0) - (target_metrics.get("min_speed_kph") or 0.0))
            exit_speed_deficit = float((ref_metrics.get("exit_speed_kph") or 0.0) - (target_metrics.get("exit_speed_kph") or 0.0))

            speed_gate = (
                p75_speed_deficit >= RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH
                or min_speed_deficit >= RACING_LINE_COMPARE_MIN_SPEED_DEFICIT_KPH
            )
            line_gate = (
                p75_gap >= RACING_LINE_COMPARE_MIN_P75_GAP_M
                or peak_gap >= RACING_LINE_COMPARE_MIN_PEAK_GAP_M
            )
            if delta_s < RACING_LINE_COMPARE_MIN_LOSS_S or not speed_gate or not line_gate:
                continue

            consensus = corner.get("reference_consensus") or {}
            loss_agreement = float(consensus.get("loss_agreement_ratio") or 0.0)
            # With several external references, require at least weak agreement
            # that this really is a slower corner. A single reference remains
            # displayable but is clearly labelled by the consensus tier.
            if reference_count >= 2 and loss_agreement < 0.40:
                continue

            # Determine which third of the corner contains the largest useful
            # line deviation; this becomes a single coaching phrase rather than
            # dozens of point labels.
            edges = [entry, entry + max(1, (exit_idx - entry) // 3), entry + max(2, 2 * (exit_idx - entry) // 3), exit_idx + 1]
            phase_names = ["진입", "에이펙스", "탈출"]
            phase_scores: list[float] = []
            for a, b in zip(edges[:-1], edges[1:]):
                if b <= a:
                    phase_scores.append(0.0)
                    continue
                phase_scores.append(float(np.nanmean(abs_lateral[a:b])))
            phase = phase_names[int(np.argmax(phase_scores))] if phase_scores else "코너"

            brake_delta_m = pct_distance(target_metrics.get("brake_start_pct"), ref_metrics.get("brake_start_pct"), self.track_length_m)
            throttle_delta_m = pct_distance(target_metrics.get("full_throttle_pct"), ref_metrics.get("full_throttle_pct"), self.track_length_m)

            local_indices = np.arange(start, stop + 1)
            eligible_indices = local_indices[np.isfinite(abs_lateral[sl])]
            if len(eligible_indices) == 0:
                continue
            max_gap_idx = int(eligible_indices[int(np.nanargmax(abs_lateral[eligible_indices]))])

            sample_indices = downsample_indices(stop - start + 1, RACING_LINE_COMPARE_POINTS) + start
            target_points: list[dict[str, Any]] = []
            reference_points: list[dict[str, Any]] = []
            for idx in sample_indices:
                if not valid[idx]:
                    continue
                target_points.append({
                    "x_m": round_or_none(float(txs[idx]), 3),
                    "y_m": round_or_none(float(tys[idx]), 3),
                    "speed_kph": round_or_none(float(target_speed[idx]), 1),
                    "distance_pct": round_or_none(float(distance[idx]), 5),
                })
                reference_points.append({
                    "x_m": round_or_none(float(rxs[idx]), 3),
                    "y_m": round_or_none(float(rys[idx]), 3),
                    "speed_kph": round_or_none(float(ref_speed[idx]), 1),
                    "distance_pct": round_or_none(float(distance[idx]), 5),
                })

            if len(target_points) < 12 or len(reference_points) < 12:
                continue

            score = (
                max(0.0, delta_s) * 6.0
                + max(0.0, p75_speed_deficit) * 0.08
                + min(3.0, max(p75_gap, peak_gap * 0.6)) * 0.24
                + min(1.0, loss_agreement) * 0.45
            )
            candidates.append({
                "id": corner.get("id"),
                "label": corner.get("label"),
                "distance_m": corner.get("distance_m"),
                "speed_class": corner.get("speed_class"),
                "issue_type": corner.get("issue_type"),
                "delta_s": round_or_none(delta_s, 3),
                "priority_score": round_or_none(score, 3),
                "focus_phase": phase,
                "metrics": {
                    "mean_line_gap_m": round_or_none(mean_gap, 2),
                    "p75_line_gap_m": round_or_none(p75_gap, 2),
                    "peak_line_gap_m": round_or_none(peak_gap, 2),
                    "median_speed_deficit_kph": round_or_none(median_speed_deficit, 1),
                    "p75_speed_deficit_kph": round_or_none(p75_speed_deficit, 1),
                    "min_speed_deficit_kph": round_or_none(min_speed_deficit, 1),
                    "exit_speed_deficit_kph": round_or_none(exit_speed_deficit, 1),
                    "brake_start_delta_m": round_or_none(brake_delta_m, 1) if brake_delta_m is not None else None,
                    "full_throttle_delta_m": round_or_none(throttle_delta_m, 1) if throttle_delta_m is not None else None,
                    "loss_agreement_ratio": round_or_none(loss_agreement, 3),
                },
                "coaching_note": (
                    f"{phase}에서 Garage61 기준 라인과 차이가 크고 속도 손실이 함께 발생합니다. "
                    "한 번에 전체 라인을 바꾸지 말고 그림의 가장 큰 간격 구간부터 기준 라인에 가깝게 A/B 주행하십시오."
                ),
                "target_points": target_points,
                "reference_points": reference_points,
                "max_gap_connector": {
                    "target": {
                        "x_m": round_or_none(float(txs[max_gap_idx]), 3),
                        "y_m": round_or_none(float(tys[max_gap_idx]), 3),
                    },
                    "reference": {
                        "x_m": round_or_none(float(rxs[max_gap_idx]), 3),
                        "y_m": round_or_none(float(rys[max_gap_idx]), 3),
                    },
                    "gap_m": round_or_none(float(abs_lateral[max_gap_idx]), 2),
                    "distance_pct": round_or_none(float(distance[max_gap_idx]), 5),
                },
                "markers": {
                    "target": {
                        "brake": marker_at(target_metrics.get("brake_start_pct"), txs, tys, target_speed),
                        "apex": marker_at(corner.get("apex_pct"), txs, tys, target_speed),
                        "throttle": marker_at(target_metrics.get("full_throttle_pct"), txs, tys, target_speed),
                    },
                    "reference": {
                        "brake": marker_at(ref_metrics.get("brake_start_pct"), rxs, rys, ref_speed),
                        "apex": marker_at(corner.get("apex_pct"), rxs, rys, ref_speed),
                        "throttle": marker_at(ref_metrics.get("full_throttle_pct"), rxs, rys, ref_speed),
                    },
                },
            })

        candidates.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
        selected = candidates[:RACING_LINE_COMPARE_MAX_CORNERS]
        base["corners"] = selected
        base["eligible_corner_count"] = len(candidates)
        base["status"] = "READY" if selected else "NO_QUALIFYING_CORNERS"
        if selected:
            base["note"] = (
                f"개선 가치가 큰 {len(selected)}개 코너만 표시합니다. 라인 차이만 있는 코너나 속도 손실만 있는 코너는 제외합니다."
            )
        return base

    def _straight_performance_diagnostics(
        self,
        target_grid: dict[str, np.ndarray[Any, Any]],
        reference_grid: dict[str, np.ndarray[Any, Any]],
        distribution_grids: dict[str, dict[str, np.ndarray[Any, Any]]],
        reference_count: int,
        unique_driver_count: int,
        condition_similarity: float,
        strong_consensus: bool,
    ) -> dict[str, Any]:
        """Separate corner-exit loss from genuine late-straight speed/drag loss.

        A top-speed deficit is only promoted to an aero/setup signal when the
        target and reference are both essentially full throttle and straight,
        the speed gap grows *after* the corner exit, and the same behaviour is
        repeated on multiple long straights.  Importantly, lack of enough
        measurable straights is kept distinct from a measured "no bottleneck"
        result so the UI never interprets missing evidence as proof of absence.
        """
        required = ("speed_kph", "Throttle", "Brake", "SteeringWheelAngle")
        minimum_straights_for_repetition = 2
        recommended_straights_for_strong_diagnosis = 3
        if not all(key in target_grid and key in reference_grid for key in required):
            return {
                "status": "MISSING_CHANNELS",
                "diagnosis": "INSUFFICIENT_STRAIGHT_DATA",
                "data_sufficient": False,
                "data_quality": "INSUFFICIENT",
                "required_straights": minimum_straights_for_repetition,
                "recommended_straights": recommended_straights_for_strong_diagnosis,
                "eligible_straights": 0,
                "aero_drag_suspect": False,
                "persistent_top_speed_opportunity": False,
                "segments": [],
            }

        distance = target_grid["distance_pct"]
        target_speed = target_grid["speed_kph"].astype(float)
        ref_speed = reference_grid["speed_kph"].astype(float)
        target_throttle = target_grid["Throttle"].astype(float)
        ref_throttle = reference_grid["Throttle"].astype(float)
        target_brake = target_grid["Brake"].astype(float)
        ref_brake = reference_grid["Brake"].astype(float)
        target_steer = np.abs(target_grid["SteeringWheelAngle"].astype(float))
        ref_steer = np.abs(reference_grid["SteeringWheelAngle"].astype(float))

        # iRacing steering is radians. Five degrees is tight enough to exclude
        # most loaded corners while keeping small straight-line corrections.
        steer_limit = math.radians(5.0)
        straight_mask = (
            (target_throttle >= 0.94)
            & (ref_throttle >= 0.94)
            & (target_brake <= 0.03)
            & (ref_brake <= 0.03)
            & (target_steer <= steer_limit)
            & (ref_steer <= steer_limit)
            & (ref_speed >= 120.0)
        )
        point_distance_m = self.track_length_m / max(1, len(distance) - 1)
        min_points = max(4, int(round(150.0 / max(1e-6, point_distance_m))))
        groups = contiguous_groups(np.flatnonzero(straight_mask), max_gap=2, min_len=min_points)

        segments: list[dict[str, Any]] = []
        for number, (start_idx, end_idx) in enumerate(groups, start=1):
            length_points = end_idx - start_idx + 1
            distance_m = length_points * point_distance_m
            if distance_m < 150.0:
                continue

            def at(frac: float) -> int:
                return min(end_idx, max(start_idx, start_idx + int(round((length_points - 1) * frac))))

            i15, i35, i70, i90 = at(0.15), at(0.35), at(0.70), at(0.90)
            entry_gap = float(ref_speed[i15] - target_speed[i15])
            mid_gap = float(ref_speed[i35] - target_speed[i35])
            late_slice = slice(i70, max(i70 + 1, i90 + 1))
            late_gap = float(np.nanmedian(ref_speed[late_slice] - target_speed[late_slice]))
            end_gap = float(ref_speed[i90] - target_speed[i90])
            gap_growth = late_gap - entry_gap
            target_gain = float(target_speed[i90] - target_speed[i15])
            reference_gain = float(ref_speed[i90] - ref_speed[i15])
            acceleration_gain_deficit = reference_gain - target_gain
            target_max = float(np.nanmax(target_speed[start_idx : end_idx + 1]))
            reference_max = float(np.nanmax(ref_speed[start_idx : end_idx + 1]))
            max_speed_deficit = reference_max - target_max

            # Distribution support: if the target remains below the reference
            # p25 late in the straight, the signal is not being driven by one
            # unusually fast reference lap.
            speed_dist = distribution_grids.get("speed_kph") or {}
            below_reference_p25 = None
            if "p25" in speed_dist:
                ref_p25 = speed_dist["p25"]
                below_reference_p25 = float(
                    np.mean(target_speed[late_slice] < (ref_p25[late_slice] - 0.5))
                )

            classification = "MATCHED"
            if distance_m >= 220.0 and late_gap >= 2.5 and gap_growth >= 1.8 and acceleration_gain_deficit >= 1.5:
                classification = "DRAG_LIMITED" if entry_gap <= 2.5 else "MIXED_EXIT_AND_DRAG"
            elif entry_gap >= 3.0 and gap_growth < 1.8:
                classification = "EXIT_LIMITED"
            elif late_gap >= 2.0 and gap_growth >= 1.2:
                classification = "POSSIBLE_DRAG"

            segments.append(
                {
                    "id": f"S{number}",
                    "start_pct": round(float(distance[start_idx]), 5),
                    "end_pct": round(float(distance[end_idx]), 5),
                    "distance_m": round(distance_m, 1),
                    "classification": classification,
                    "entry_speed_deficit_kph": round(entry_gap, 2),
                    "mid_speed_deficit_kph": round(mid_gap, 2),
                    "late_speed_deficit_kph": round(late_gap, 2),
                    "end_speed_deficit_kph": round(end_gap, 2),
                    "gap_growth_kph": round(gap_growth, 2),
                    "acceleration_gain_deficit_kph": round(acceleration_gain_deficit, 2),
                    "target_max_speed_kph": round(target_max, 2),
                    "reference_max_speed_kph": round(reference_max, 2),
                    "max_speed_deficit_kph": round(max_speed_deficit, 2),
                    "target_full_throttle_pct": round(float(np.mean(target_throttle[start_idx:end_idx + 1] >= 0.94)) * 100.0, 1),
                    "reference_full_throttle_pct": round(float(np.mean(ref_throttle[start_idx:end_idx + 1] >= 0.94)) * 100.0, 1),
                    "below_reference_p25_ratio": round_or_none(below_reference_p25, 3),
                }
            )

        eligible = [item for item in segments if float(item.get("distance_m") or 0.0) >= 220.0]
        drag_segments = [
            item for item in eligible
            if item.get("classification") in {"DRAG_LIMITED", "MIXED_EXIT_AND_DRAG"}
        ]
        possible_drag = [
            item for item in eligible
            if item.get("classification") in {"DRAG_LIMITED", "MIXED_EXIT_AND_DRAG", "POSSIBLE_DRAG"}
        ]
        exit_segments = [item for item in eligible if item.get("classification") == "EXIT_LIMITED"]
        persistent_top_speed_segments = [
            item for item in eligible
            if float(item.get("late_speed_deficit_kph") or 0.0) >= 4.0
            and float(item.get("max_speed_deficit_kph") or 0.0) >= 4.0
        ]
        drag_ratio = len(drag_segments) / len(eligible) if eligible else 0.0
        possible_drag_ratio = len(possible_drag) / len(eligible) if eligible else 0.0
        persistent_top_speed_ratio = len(persistent_top_speed_segments) / len(eligible) if eligible else 0.0

        # Always calculate observed straight-line medians when there are eligible
        # straights. Previously these values were only populated when a problem
        # segment existed, which made a valid "no bottleneck" result appear as
        # "-- km/h" and look like missing data.
        all_late_gaps = [float(item["late_speed_deficit_kph"]) for item in eligible]
        all_growths = [float(item["gap_growth_kph"]) for item in eligible]
        all_max_gaps = [float(item["max_speed_deficit_kph"]) for item in eligible]

        problem_late_source = possible_drag if possible_drag else persistent_top_speed_segments
        problem_late_gaps = [float(item["late_speed_deficit_kph"]) for item in problem_late_source]
        problem_growths = [float(item["gap_growth_kph"]) for item in possible_drag]
        problem_max_source = possible_drag if possible_drag else persistent_top_speed_segments
        problem_max_gaps = [float(item["max_speed_deficit_kph"]) for item in problem_max_source]
        p25_support = [
            float(item["below_reference_p25_ratio"])
            for item in (possible_drag if possible_drag else persistent_top_speed_segments)
            if item.get("below_reference_p25_ratio") is not None
        ]

        data_sufficient = len(eligible) >= minimum_straights_for_repetition
        data_quality = (
            "GOOD" if len(eligible) >= recommended_straights_for_strong_diagnosis else
            "LIMITED" if data_sufficient else
            "INSUFFICIENT"
        )

        repeated_drag = data_sufficient and len(drag_segments) >= 2 and drag_ratio >= 0.45
        broad_drag = data_sufficient and len(possible_drag) >= 3 and possible_drag_ratio >= 0.60
        distribution_support = (float(np.median(p25_support)) >= 0.55) if p25_support else False
        aero_drag_suspect = bool(repeated_drag and (strong_consensus or distribution_support or broad_drag))
        persistent_top_speed_opportunity = bool(
            data_sufficient
            and len(persistent_top_speed_segments) >= 2
            and persistent_top_speed_ratio >= 0.45
            and (strong_consensus or unique_driver_count >= 3)
        )

        confidence = 0.0
        if possible_drag and data_sufficient:
            confidence = 0.40
            confidence += min(0.18, 0.06 * len(drag_segments))
            confidence += min(0.12, max(0.0, float(np.median(problem_growths)) - 1.5) * 0.035)
            confidence += 0.08 if strong_consensus else 0.0
            confidence += 0.05 if unique_driver_count >= 3 else 0.0
            confidence += 0.05 * max(0.0, min(1.0, condition_similarity))
            confidence = min(0.90, confidence)

        if not data_sufficient:
            diagnosis = "INSUFFICIENT_STRAIGHT_DATA"
            status = "INSUFFICIENT_STRAIGHT_DATA"
        elif aero_drag_suspect:
            diagnosis = "AERO_DRAG_SUSPECT"
            status = "READY"
        elif persistent_top_speed_opportunity:
            diagnosis = "PERSISTENT_TOP_SPEED_DEFICIT"
            status = "READY"
        elif len(exit_segments) >= 2 and not possible_drag:
            diagnosis = "EXIT_SPEED_LIMITED"
            status = "READY"
        elif possible_drag:
            diagnosis = "MIXED"
            status = "READY"
        else:
            diagnosis = "NO_STRAIGHT_BOTTLENECK"
            status = "READY"

        metric_scope = "PROBLEM_SEGMENTS" if (problem_late_gaps or problem_growths or problem_max_gaps) else "ALL_ELIGIBLE_STRAIGHTS"
        displayed_late_gaps = problem_late_gaps if problem_late_gaps else all_late_gaps
        displayed_growths = problem_growths if problem_growths else all_growths
        displayed_max_gaps = problem_max_gaps if problem_max_gaps else all_max_gaps

        return {
            "status": status,
            "diagnosis": diagnosis,
            "data_sufficient": data_sufficient,
            "data_quality": data_quality,
            "required_straights": minimum_straights_for_repetition,
            "recommended_straights": recommended_straights_for_strong_diagnosis,
            "detected_straight_segments": len(segments),
            "eligible_straights": len(eligible),
            "drag_support_count": len(drag_segments),
            "possible_drag_count": len(possible_drag),
            "exit_limited_count": len(exit_segments),
            "persistent_top_speed_count": len(persistent_top_speed_segments),
            "drag_support_ratio": round(drag_ratio, 3),
            "persistent_top_speed_ratio": round(persistent_top_speed_ratio, 3),
            "aero_drag_suspect": aero_drag_suspect,
            "persistent_top_speed_opportunity": persistent_top_speed_opportunity,
            "confidence": round(confidence, 2),
            "metric_scope": metric_scope,
            "median_late_speed_deficit_kph": round_or_none(float(np.median(displayed_late_gaps)) if displayed_late_gaps else None, 2),
            "median_gap_growth_kph": round_or_none(float(np.median(displayed_growths)) if displayed_growths else None, 2),
            "median_max_speed_deficit_kph": round_or_none(float(np.median(displayed_max_gaps)) if displayed_max_gaps else None, 2),
            "overall_median_late_speed_deficit_kph": round_or_none(float(np.median(all_late_gaps)) if all_late_gaps else None, 2),
            "overall_median_gap_growth_kph": round_or_none(float(np.median(all_growths)) if all_growths else None, 2),
            "overall_median_max_speed_deficit_kph": round_or_none(float(np.median(all_max_gaps)) if all_max_gaps else None, 2),
            "reference_count": reference_count,
            "unique_driver_count": unique_driver_count,
            "condition_similarity": round(condition_similarity, 3),
            "segments": segments,
        }

    @staticmethod
    def _high_speed_stability_risk(external_reference: dict[str, Any]) -> dict[str, Any]:
        risks: list[dict[str, Any]] = []
        for corner in external_reference.get("corners", []) or []:
            if corner.get("speed_class") != "HIGH" or float(corner.get("delta_s") or 0.0) < 0.03:
                continue
            consensus = corner.get("reference_consensus", {}) or {}
            if float(consensus.get("loss_agreement_ratio") or 0.0) < 0.60:
                continue
            target = corner.get("target") or {}
            reference = corner.get("reference") or {}
            correction_excess = int(target.get("steering_corrections") or 0) - int(reference.get("steering_corrections") or 0)
            relift_excess = int(target.get("throttle_relifts") or 0) - int(reference.get("throttle_relifts") or 0)
            yaw_target = target.get("avg_abs_yaw_rate")
            yaw_reference = reference.get("avg_abs_yaw_rate")
            yaw_excess = (
                yaw_target is not None and yaw_reference is not None
                and float(yaw_target) > float(yaw_reference) * 1.08
            )
            if correction_excess >= 1 or relift_excess >= 1 or yaw_excess:
                risks.append(
                    {
                        "corner": corner.get("label"),
                        "delta_s": corner.get("delta_s"),
                        "correction_excess": correction_excess,
                        "relift_excess": relift_excess,
                    }
                )
        return {
            "risk_count": len(risks),
            "high_speed_stability_risk": len(risks) >= 2,
            "corners": risks[:4],
        }

    def _external_setup_candidates(
        self,
        session_mode: SessionMode,
        external_reference: dict[str, Any],
        setup: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not external_reference.get("decision_ready"):
            return []

        reference_count = int(external_reference.get("decision_reference_count") or 0)
        unique_drivers = int(external_reference.get("unique_driver_count") or 0)
        strong_consensus = bool(external_reference.get("strong_consensus"))
        condition_similarity = float((external_reference.get("aggregate") or {}).get("condition_similarity_median") or 0.5)
        confidence_cap = float(session_policy(session_mode)["setup_confidence_cap"])
        if not strong_consensus:
            confidence_cap = min(confidence_cap, 0.62)

        candidates: list[dict[str, Any]] = []
        straight = ((external_reference.get("performance_diagnostics") or {}).get("straight_line") or {})
        stability = self._high_speed_stability_risk(external_reference)
        rear_wing = setup.get("rear_wing_angle_deg")

        # ------------------------------------------------------------------
        # 1) Straight-line / aero drag opportunity
        # ------------------------------------------------------------------
        if straight.get("aero_drag_suspect") or straight.get("persistent_top_speed_opportunity"):
            strict_drag = bool(straight.get("aero_drag_suspect"))
            support = int(straight.get("drag_support_count") or 0)
            persistent_support = int(straight.get("persistent_top_speed_count") or 0)
            eligible = int(straight.get("eligible_straights") or 0)
            late_gap = float(straight.get("median_late_speed_deficit_kph") or 0.0)
            growth = float(straight.get("median_gap_growth_kph") or 0.0)
            max_gap = float(straight.get("median_max_speed_deficit_kph") or 0.0)
            confidence = float(straight.get("confidence") or 0.55)
            if not strict_drag:
                confidence = min(confidence, 0.56)
            confidence += 0.04 if strong_consensus else 0.0
            if stability.get("high_speed_stability_risk"):
                confidence -= 0.12
            confidence = min(confidence_cap, max(0.40, confidence))

            if rear_wing is not None and float(rear_wing) >= 1.0:
                adjustment = make_adjustment(
                    setting="rear_wing_angle_deg",
                    label="Rear wing angle",
                    original=float(rear_wing),
                    value=float(rear_wing) - 1.0,
                    unit="deg",
                    confidence=confidence,
                    evidence=(
                        (
                            f"Garage61 빠른 기준 {reference_count}개에서 장거리 풀스로틀 {support}/{eligible}개 구간이 직선 후반 속도차 증가 패턴을 보임. "
                            if strict_drag else
                            f"Garage61 빠른 기준 {reference_count}개에서 장거리 풀스로틀 {persistent_support}/{eligible}개 구간의 후반/최고속 부족이 반복됨. "
                        )
                        + f"후반 중앙 속도 부족 {late_gap:.1f} km/h, 직선 내 속도차 증가 {growth:+.1f} km/h, 최고속 중앙 부족 {max_gap:.1f} km/h"
                    ),
                    tradeoff=(
                        "직선 공기저항은 줄지만 고속 코너 후방 다운포스·제동 안정성이 감소할 수 있음"
                        + (". 현재 고속 안정성 위험 신호가 있어 특히 보수적으로 검증해야 함" if stability.get("high_speed_stability_risk") else "")
                    ),
                    validation=(
                        "Rear wing을 1단계만 낮추고 동일 조건 A/B에서 장거리 직선 후반 속도, 최고속, 전체 랩타임과 "
                        "고속 코너 스로틀 리프트·조향 보정을 동시에 비교"
                    ),
                )
            else:
                adjustment = make_directional_adjustment(
                    setting="rear_wing_angle_deg",
                    label="Rear wing angle",
                    original=rear_wing,
                    direction="DECREASE",
                    step_text="Garage 1 click decrease",
                    unit="deg",
                    confidence=confidence,
                    evidence=(
                        f"장거리 풀스로틀 {(support if strict_drag else persistent_support)}/{eligible}개 구간에서 직선 후반 속도 부족이 반복됨 "
                        f"(후반 {late_gap:.1f} km/h, gap growth {growth:+.1f} km/h)"
                    ),
                    tradeoff="고속 코너 후방 다운포스와 제동 안정성이 감소할 수 있음",
                    validation="Garage에서 리어 윙을 정확히 1칸 낮춘 뒤 동일 조건 A/B 검증",
                )

            adjustment.update(
                {
                    "tier": "SETUP_ASSIST",
                    "source": "GARAGE61_STRAIGHT_CONSENSUS",
                    "experimental": True,
                    "category": "AERO_DRAG" if strict_drag else "TOP_SPEED_OPPORTUNITY",
                    "priority_score": round(min(1.0, (0.55 if strict_drag else 0.48) + late_gap / 20.0 + max(0.0, growth) / 25.0), 3),
                    "expected_effect": "직선 후반 가속 유지와 최고속 개선",
                    "performance_support": straight,
                    "safety_gate": stability,
                    "reference_support": {
                        "reference_count": reference_count,
                        "unique_driver_count": unique_drivers,
                        "condition_similarity_median": round_or_none(condition_similarity, 3),
                        "consensus_tier": "STRONG" if strong_consensus else "PROVISIONAL",
                    },
                }
            )
            if adjustment.get("validator", {}).get("passed", True):
                candidates.append(adjustment)

            # Excess toe also creates drag, but the IBT does not expose a
            # universal per-car click size/sign convention. Surface it as a
            # lower-priority directional hypothesis rather than inventing a
            # numeric value.
            front_toe = setup.get("front_toe_mm")
            if front_toe is not None and abs(float(front_toe)) >= 0.20:
                toe_candidate = make_directional_adjustment(
                    setting="front_toe_mm",
                    label="Front total toe",
                    original=float(front_toe),
                    direction="TOWARD_ZERO",
                    step_text="Garage 1 click toward 0",
                    unit="mm",
                    confidence=min(confidence_cap, max(0.38, confidence - 0.18)),
                    evidence=(
                        "직선 후반 속도 부족이 반복되며 현재 Front total toe가 0에서 떨어져 있음. "
                        "Toe drag가 부가 요인인지 1-click A/B로 분리 가능"
                    ),
                    tradeoff="직진 저항은 줄 수 있으나 턴인 응답과 제동 안정성이 달라질 수 있음",
                    validation="리어 윙 후보와 동시에 바꾸지 말고 별도 주행에서 앞 토만 1칸 0 쪽으로 변경",
                )
                toe_candidate.update(
                    {
                        "tier": "SECONDARY_HYPOTHESIS",
                        "source": "GARAGE61_STRAIGHT_CONSENSUS",
                        "experimental": True,
                        "category": "AERO_DRAG_SECONDARY",
                        "priority_score": round(max(0.0, float(adjustment.get("priority_score") or 0.0) - 0.22), 3),
                        "expected_effect": "직선 rolling/aero drag의 부가 감소 가능성",
                    }
                )
                candidates.append(toe_candidate)

        # ------------------------------------------------------------------
        # 2) Corner balance / traction opportunities
        # ------------------------------------------------------------------
        rear_arb = setup.get("rear_arb_blades")
        front_arb = setup.get("front_arb_blades")
        for corner in external_reference.get("top_losses", [])[:8]:
            delta = float(corner.get("delta_s") or 0.0)
            consensus = corner.get("reference_consensus", {}) or {}
            agreement = float(consensus.get("loss_agreement_ratio") or 0.0)
            if delta < 0.03 or agreement < 0.60:
                continue
            target = corner.get("target") or {}
            reference = corner.get("reference") or {}
            steer_excess = (target.get("avg_abs_steer_deg") or 0.0) - (reference.get("avg_abs_steer_deg") or 0.0)
            target_eff = target.get("steering_efficiency")
            reference_eff = reference.get("steering_efficiency")
            full_throttle_delay = pct_distance(
                target.get("full_throttle_pct"), reference.get("full_throttle_pct"), self.track_length_m
            )
            min_speed_deficit = (reference.get("min_speed_kph") or 0.0) - (target.get("min_speed_kph") or 0.0)
            target_relifts = int(target.get("throttle_relifts") or 0)
            ref_relifts = int(reference.get("throttle_relifts") or 0)
            target_corr = int(target.get("steering_corrections") or 0)
            ref_corr = int(reference.get("steering_corrections") or 0)

            base_confidence = 0.42 + 0.055 * max(0, reference_count - 2) + 0.09 * agreement
            base_confidence += min(0.08, max(0.0, delta - 0.03) * 0.40)
            base_confidence += 0.05 if unique_drivers >= 2 else -0.025
            base_confidence += 0.06 * max(-0.5, min(0.5, condition_similarity - 0.5))
            confidence = min(confidence_cap, base_confidence)

            rotation_deficit = (
                steer_excess >= 2.0
                and target_eff is not None
                and reference_eff is not None
                and float(target_eff) < float(reference_eff) * 0.97
                and min_speed_deficit >= 1.0
            )
            traction_deficit = (
                full_throttle_delay is not None
                and full_throttle_delay >= 8.0
                and (corner.get("issue_type") == "EXIT" or target_relifts > ref_relifts)
            )
            unstable_rotation = target_corr > ref_corr and (
                target_relifts > ref_relifts
                or (
                    target.get("avg_abs_yaw_rate") is not None
                    and reference.get("avg_abs_yaw_rate") is not None
                    and float(target["avg_abs_yaw_rate"]) > float(reference["avg_abs_yaw_rate"]) * 1.08
                )
            )

            adjustment: Optional[dict[str, Any]] = None
            if traction_deficit and rear_arb is not None and float(rear_arb) > 1.0:
                adjustment = make_adjustment(
                    setting="rear_arb_blades",
                    label="Rear ARB blades",
                    original=float(rear_arb),
                    value=float(rear_arb) - 1.0,
                    unit="blade",
                    confidence=confidence,
                    evidence=(
                        f"{corner['label']} 손실 {delta:.3f}초, 빠른 기준 손실방향 일치 {agreement * 100:.0f}%, "
                        f"풀스로틀 복귀가 약 {full_throttle_delay:.0f}m 늦음"
                    ),
                    tradeoff="탈출 트랙션과 연석 순응성은 좋아질 수 있으나 미드코너 회전성이 감소할 수 있음",
                    validation="리어 ARB -1만 적용하고 풀스로틀 복귀·출구속도·미드코너 손실을 A/B 비교",
                )
                category = "EXIT_TRACTION"
                expected = "코너 탈출 트랙션과 풀스로틀 복귀 개선"
            elif rotation_deficit and not traction_deficit:
                if rear_arb is not None:
                    adjustment = make_adjustment(
                        setting="rear_arb_blades",
                        label="Rear ARB blades",
                        original=float(rear_arb),
                        value=float(rear_arb) + 1.0,
                        unit="blade",
                        confidence=confidence,
                        evidence=(
                            f"{corner['label']} 손실 {delta:.3f}초, 조향량 +{steer_excess:.1f}°인데 yaw/steering 효율이 기준보다 낮고 "
                            f"최저속도가 {min_speed_deficit:.1f} km/h 낮음"
                        ),
                        tradeoff="회전성은 좋아질 수 있으나 저속 탈출 트랙션과 연석 순응성이 감소할 수 있음",
                        validation="리어 ARB +1만 적용하고 조향효율·최저속도·출구 트랙션을 A/B 비교",
                    )
                    category = "ROTATION_DEFICIT"
                    expected = "진입/미드코너 회전 반응 개선"
                elif front_arb is not None and float(front_arb) > 1.0:
                    adjustment = make_adjustment(
                        setting="front_arb_blades",
                        label="Front ARB blades",
                        original=float(front_arb),
                        value=float(front_arb) - 1.0,
                        unit="blade",
                        confidence=max(0.35, confidence - 0.05),
                        evidence=f"{corner['label']}에서 조향량 대비 회전 응답 부족이 반복됨",
                        tradeoff="기계적 전륜 그립은 증가할 수 있으나 응답이 둔해지고 차체 롤이 늘 수 있음",
                        validation="프런트 ARB -1만 적용하고 같은 코너의 최저속도·조향량과 고속 안정성을 A/B 비교",
                    )
                    category = "ROTATION_DEFICIT"
                    expected = "전륜 기계적 그립과 회전 응답 개선"
            elif unstable_rotation and rear_arb is not None and float(rear_arb) > 1.0:
                adjustment = make_adjustment(
                    setting="rear_arb_blades",
                    label="Rear ARB blades",
                    original=float(rear_arb),
                    value=float(rear_arb) - 1.0,
                    unit="blade",
                    confidence=max(0.36, confidence - 0.04),
                    evidence=(
                        f"{corner['label']}에서 기준보다 조향 보정/스로틀 리프트가 많고 구간 손실 {delta:.3f}초가 반복됨"
                    ),
                    tradeoff="후방 안정성은 좋아질 수 있으나 미드코너 회전성이 감소할 수 있음",
                    validation="리어 ARB -1 후 조향 보정·스로틀 리프트·코너 손실을 함께 비교",
                )
                category = "REAR_STABILITY"
                expected = "후방 안정성과 자신감 있는 스로틀 복귀 개선"
            else:
                category = ""
                expected = ""

            if adjustment and adjustment.get("validator", {}).get("passed"):
                adjustment.update(
                    {
                        "tier": "SETUP_ASSIST",
                        "source": "GARAGE61_CORNER_CONSENSUS",
                        "experimental": True,
                        "category": category,
                        "priority_score": round(min(1.0, 0.45 + delta * 1.7 + agreement * 0.18), 3),
                        "expected_effect": expected,
                        "corner_id": corner.get("id"),
                        "reference_support": {
                            "reference_count": reference_count,
                            "unique_driver_count": unique_drivers,
                            "loss_agreement_ratio": round(agreement, 3),
                            "median_corner_loss_s": round_or_none(delta, 3),
                            "condition_similarity_median": round_or_none(condition_similarity, 3),
                            "consensus_tier": "STRONG" if strong_consensus else "PROVISIONAL",
                        },
                    }
                )
                candidates.append(adjustment)

            # Differential preload is useful for persistent rotation deficit,
            # but Garage increments vary by car. Keep it directional and below
            # the ARB candidate rather than inventing Nm.
            if rotation_deficit and setup.get("diff_preload_nm") is not None:
                diff_candidate = make_directional_adjustment(
                    setting="diff_preload_nm",
                    label="Differential preload",
                    original=float(setup["diff_preload_nm"]),
                    direction="DECREASE",
                    step_text="Garage 1 click decrease",
                    unit="Nm",
                    confidence=max(0.32, min(confidence_cap, confidence - 0.14)),
                    evidence=(
                        f"{corner['label']}에서 조향량 증가 대비 회전 효율과 최저속도가 동시에 부족함. "
                        "Preload가 회전 저항의 보조 원인인지 분리 시험 가능"
                    ),
                    tradeoff="진입/미드 회전은 좋아질 수 있으나 구동 안정성과 코너 탈출 거동이 달라질 수 있음",
                    validation="ARB 후보와 동시에 바꾸지 말고 디프 프리로드만 1칸 낮춰 별도 A/B",
                )
                diff_candidate.update(
                    {
                        "tier": "SECONDARY_HYPOTHESIS",
                        "source": "GARAGE61_CORNER_CONSENSUS",
                        "experimental": True,
                        "category": "DIFF_ROTATION",
                        "priority_score": round(max(0.0, 0.35 + delta * 1.2 + agreement * 0.12), 3),
                        "expected_effect": "진입/미드코너 differential locking 감소에 따른 회전 보조",
                        "corner_id": corner.get("id"),
                    }
                )
                candidates.append(diff_candidate)

            # Brake bias can be a useful entry-balance lever, but the click
            # size is car-specific. Only surface it when the external cohort
            # shows an entry/braking balance pattern, and keep it below the
            # mechanical/Aero primary candidate.
            if corner.get("issue_type") in {"ENTRY", "BRAKING", "BALANCE_SUSPECT"} and setup.get("brake_bias_pct") is not None:
                if unstable_rotation:
                    bb_direction = "INCREASE"
                    bb_step = "Garage 1 click forward"
                    bb_effect = "제동/진입 후방 안정성 증가"
                    bb_tradeoff = "진입 회전이 감소하고 앞타이어/ABS 부담이 증가할 수 있음"
                elif rotation_deficit and not traction_deficit:
                    bb_direction = "DECREASE"
                    bb_step = "Garage 1 click rearward"
                    bb_effect = "트레일 브레이킹 구간 회전 보조"
                    bb_tradeoff = "후륜 잠김/진입 불안정 위험이 증가할 수 있음"
                else:
                    bb_direction = ""
                    bb_step = ""
                    bb_effect = ""
                    bb_tradeoff = ""
                if bb_direction:
                    bb_candidate = make_directional_adjustment(
                        setting="brake_bias_pct",
                        label="Brake bias",
                        original=float(setup["brake_bias_pct"]),
                        direction=bb_direction,
                        step_text=bb_step,
                        unit="%",
                        confidence=max(0.30, min(confidence_cap, confidence - 0.18)),
                        evidence=(
                            f"{corner['label']}의 {corner.get('issue_type')} 손실이 빠른 기준에서 {agreement * 100:.0f}% 방향 일치. "
                            "Brake bias가 진입 밸런스에 미치는 영향을 한 click로 분리 검증"
                        ),
                        tradeoff=bb_tradeoff,
                        validation="ARB·디프 후보와 동시에 바꾸지 말고 브레이크 바이어스만 1칸 변경해 제동 안정성·회전·ABS 사용을 비교",
                    )
                    bb_candidate.update(
                        {
                            "tier": "SECONDARY_HYPOTHESIS",
                            "source": "GARAGE61_CORNER_CONSENSUS",
                            "experimental": True,
                            "category": "BRAKE_BALANCE",
                            "priority_score": round(max(0.0, 0.31 + delta * 1.0 + agreement * 0.10), 3),
                            "expected_effect": bb_effect,
                            "corner_id": corner.get("id"),
                        }
                    )
                    candidates.append(bb_candidate)

        # ------------------------------------------------------------------
        # 3) High-speed stability can justify *more* wing if there is no
        # repeated drag deficit. This is the inverse aero case.
        # ------------------------------------------------------------------
        if (
            stability.get("high_speed_stability_risk")
            and straight.get("status") == "READY"
            and bool(straight.get("data_sufficient", True))
            and not straight.get("aero_drag_suspect")
            and not straight.get("persistent_top_speed_opportunity")
            and rear_wing is not None
        ):
            confidence = min(confidence_cap, 0.48 + 0.06 * min(3, int(stability.get("risk_count") or 0)))
            adjustment = make_adjustment(
                setting="rear_wing_angle_deg",
                label="Rear wing angle",
                original=float(rear_wing),
                value=float(rear_wing) + 1.0,
                unit="deg",
                confidence=confidence,
                evidence=(
                    f"고속 코너 {stability.get('risk_count', 0)}곳에서 빠른 기준보다 조향 보정/스로틀 리프트가 반복되며 "
                    f"유효 직선 {int(straight.get('eligible_straights') or 0)}개 비교에서 반복 drag deficit은 검출되지 않음"
                ),
                tradeoff="고속 안정성은 좋아질 수 있으나 최고속과 직선 가속이 감소함",
                validation="리어 윙 +1 후 고속 코너 리프트·보정 감소량과 직선 최고속 손실을 동시에 검증",
            )
            if adjustment.get("validator", {}).get("passed"):
                adjustment.update(
                    {
                        "tier": "SETUP_ASSIST",
                        "source": "GARAGE61_HIGH_SPEED_STABILITY",
                        "experimental": True,
                        "category": "HIGH_SPEED_STABILITY",
                        "priority_score": round(min(1.0, 0.52 + 0.07 * int(stability.get("risk_count") or 0)), 3),
                        "expected_effect": "고속 코너 후방 안정성과 스로틀 유지율 개선",
                        "safety_gate": stability,
                    }
                )
                candidates.append(adjustment)

        # Keep the strongest hypothesis for a setting/direction. A setting may
        # not be recommended both up and down in the same run.
        def direction_key(item: dict[str, Any]) -> str:
            original = item.get("original_value")
            value = item.get("value")
            if isinstance(original, (int, float)) and isinstance(value, (int, float)):
                return "UP" if float(value) > float(original) else "DOWN"
            return str(item.get("direction") or "")

        candidates.sort(
            key=lambda item: (
                float(item.get("priority_score") or 0.0),
                float(item.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        seen_settings: dict[str, str] = {}
        for candidate in candidates:
            setting = str(candidate.get("setting") or "")
            direction = direction_key(candidate)
            if setting in seen_settings:
                # Do not emit conflicting directions or duplicate candidates.
                continue
            selected.append(candidate)
            seen_settings[setting] = direction
            if len(selected) >= 5:
                break
        return selected
