"""Corner comparisons, repeated input evidence and coaching priorities."""

from __future__ import annotations
import math
from typing import Any, Iterable, Optional
import numpy as np
from telemetry_types import LapSegment
from analysis_models import AnalyzeRequest, SessionMode, session_policy

from analysis_numeric import contiguous_groups, mean_finite, pct_distance, round_or_none
from reference_conditions import telemetry_reference_conditions
from practice_metrics import KEPT_METRICS
from closed_loop_coach import first_deviation
from handling import analyze_handling
from racing_line import racing_line_context, racing_line_offsets
import analysis_progress


class CornerAnalysis:
    def _corner_consistency(self, corners: list[dict[str, Any]], target_lap: Optional[int]) -> dict[str, Any]:
        """Each corner's time on every valid lap (RaceStudio 3's box plots and "fastest N sectors"). The screen
        draws the spread and compares the target lap with the corner's three fastest passes."""
        laps = self.valid_laps
        if len(laps) < 3:
            return {"available": False, "reason": "FEW_LAPS", "corners": []}
        grids = [(lap, self._lap_grid(lap), telemetry_reference_conditions(self.telemetry, lap)) for lap in laps]
        rows: list[dict[str, Any]] = []
        for corner in sorted(corners, key=lambda item: item.get("apex_pct") or 0):
            entry, exit_pct = corner.get("entry_pct"), corner.get("exit_pct")
            if not (isinstance(entry, (int, float)) and isinstance(exit_pct, (int, float))) or exit_pct <= entry:
                continue
            passes = []
            for lap, grid, lap_conditions in grids:
                pct, times = grid["distance_pct"], grid["time_s"]
                covered = np.asarray(grid.get("_coverage", np.isfinite(times)), dtype=bool)
                inside = (pct >= entry) & (pct <= exit_pct)
                if not np.any(inside) or not np.all(covered[inside]):
                    continue
                known = np.isfinite(times)
                if pct[known][0] > entry or pct[known][-1] < exit_pct:
                    continue
                seconds = float(np.interp(exit_pct, pct[known], times[known]) - np.interp(entry, pct[known], times[known]))
                if math.isfinite(seconds) and seconds > 0:
                    indices = [int(np.argmin(np.abs(pct - value))) for value in (entry, corner['apex_pct'], exit_pct)]
                    metrics = self._corner_metrics(grid, *indices) or {}
                    passes.append({"lap": lap.lap_number, "time_s": round(seconds, 3),
                                   'metrics': {key: metrics.get(key) for key in KEPT_METRICS},
                                   'conditions': lap_conditions})
            if len(passes) < 3:
                continue
            rows.append({"id": corner.get("id"), "label": corner.get("label"), "apex_pct": corner.get("apex_pct"),
                         "entry_pct": entry, "exit_pct": exit_pct, "passes": passes})
        return {"available": bool(rows), "target_lap": target_lap, "lap_count": len(laps), "corners": rows}

    def _structured_feedback(self, request: AnalyzeRequest, local_corners: list[dict[str, Any]], external: dict[str, Any]) -> dict[str, Any]:
        selected = bool(request.feedback_phase or request.feedback_speed or request.feedback_symptom)
        external_corners = external.get("corners") or external.get("top_losses") or []
        corners = external_corners if external_corners else local_corners
        observations = []
        for corner in corners:
            if corner.get("measurement_usable") is False:
                continue
            target = corner.get("target") or {}
            reference = corner.get("reference") or {}
            speed = target.get("min_speed_kph")
            if speed is None:
                continue
            band = corner.get("speed_class") or ("LOW" if float(speed) < 105 else "MEDIUM" if float(speed) < 165 else "HIGH")
            if request.feedback_speed and band != request.feedback_speed:
                continue
            phase = request.feedback_phase or "MID_CORNER"
            keys = {"ENTRY": ("brake_start_pct", "brake_release_pct", "peak_brake", "abs_duty_pct"), "MID_CORNER": ("min_speed_kph", "avg_abs_steer_deg", "steering_efficiency", "coast_time_s"), "EXIT": ("full_throttle_pct", "exit_speed_kph", "throttle_relifts", "steering_corrections")}[phase]
            metrics = {key: {"target": target.get(key), "reference": reference.get(key)} for key in keys if target.get(key) is not None}
            if not metrics:
                continue
            observations.append({"corner_id": corner.get("id"), "corner": corner.get("label"), "label": corner.get("label"), "phase": phase, "speed_band": band, "metrics": metrics, "evidence": list(corner.get("evidence") or []), "delta_s": corner.get("delta_s"), "source": corner.get("source") or ("CLEAN_SEGMENT" if not getattr(self, "valid_laps", [True]) else "GARAGE61" if external_corners else "LOCAL"), "scope": corner.get("scope") or ("COACHING_ONLY" if not getattr(self, "valid_laps", [True]) else "VALID_LAP")})
        observations.sort(key=lambda item: float(item.get("delta_s") or 0), reverse=True)
        unique_observations = {}
        for observation in observations:
            unique_observations.setdefault(observation.get("corner_id") or observation.get("label"), observation)
        observations = list(unique_observations.values())
        symptom = request.feedback_symptom
        hypotheses = {
            "UNDERSTEER": "언더스티어 체감은 진입 속도·브레이크 릴리즈·조향량 또는 셋업 균형과 관련될 수 있습니다. 현재 관측만으로 원인을 확정하지 않습니다.",
            "OVERSTEER": "오버스티어 체감은 페달 전환·조향 교정 또는 셋업 균형과 관련될 수 있습니다. 현재 관측만으로 원인을 확정하지 않습니다.",
            "BRAKING": "제동 체감은 제동 시작·최대 압력·릴리즈와 관련될 수 있습니다. ABS 채널이 없으면 ABS 개입을 판단하지 않습니다.",
            "KERB": "연석 불안정은 주행선·연석 진입 속도·서스펜션과 관련될 수 있습니다. 단일 랩의 체감만으로 바텀아웃을 확정하지 않습니다.",
        }
        return {"phase": request.feedback_phase, "speed": request.feedback_speed, "symptom": symptom, "setup_mode": request.setup_mode,
                "status": "NOT_SELECTED" if not selected else "MATCHED" if observations else "NO_MATCH",
                "source": "GARAGE61" if external_corners else "LOCAL", "observations": observations[:3] if selected else [],
                "speed_band_definition_kph": {"LOW": "<105", "MEDIUM": "105–164.9", "HIGH": ">=165"},
                "hypothesis": hypotheses.get(symptom, "선택한 구간의 입력 차이를 확인하고 반복 주행으로 체감과 관측이 일치하는지 검증합니다.") if selected else None,
                "next_run": ("탐색 후보가 있으면 한 항목만 바꾸고 평소 페이스로 1~2랩 달려 체감을 확인하세요. 랩타임을 일정하게 맞출 필요는 없습니다. 속도 효과는 별도 A/B로 확인합니다." if request.setup_mode == "EXPLORE" else "가장 손실이 큰 해당 코너에서 입력 한 가지만 바꾸고 유효 랩 3개를 기록하세요. 셋업을 바꾸면 한 항목만 바꾸고 A/B 중앙값과 산포를 비교하세요.") if observations and selected else "선택 조건에 맞는 코너 관측이 없습니다. 추가 유효 랩을 기록하거나 속도 구간을 변경하세요." if selected else None}

    def _memory_corner_metrics(self) -> list[dict[str, Any]]:
        """Persist each valid lap's corner metrics against the file-best lap."""

        if not self.valid_laps:
            return []
        reference = min(
            self.valid_laps,
            key=lambda lap: float(lap.lap_time_s or float("inf")),
        )
        reference_grid = self._lap_grid(reference)
        definitions = self._detect_corners(reference_grid)
        rows: list[dict[str, Any]] = []
        for lap in self.valid_laps[:200]:
            grid = self._lap_grid(lap)
            corners = self._corner_analysis(grid, reference_grid, definitions)
            for corner in corners:
                row = dict(corner)
                row["lap_number"] = lap.lap_number
                row["reference_lap"] = reference.lap_number
                row["lap_time_s"] = round_or_none(lap.lap_time_s, 4)
                rows.append(row)
        return rows

    def _out_lap_fingerprint(self) -> dict[str, Any]:
        """Summarize warm-up behavior without storing raw telemetry samples."""

        out_laps = [lap for lap in self.telemetry.laps if lap.status == "OUT_LAP"]
        if not out_laps:
            return {"available": False, "runs": []}

        speed = self.channels.get("Speed")
        brake = self.channels.get("Brake")
        throttle = self.channels.get("Throttle")
        steering = self.channels.get("SteeringWheelAngle")
        abs_active = self.channels.get("BrakeABSactive")
        session_time = self.channels.get("SessionTime")
        tire_temp_channels = [
            name
            for name in [
                "LFtempL", "LFtempM", "LFtempR", "RFtempL", "RFtempM", "RFtempR",
                "LRtempL", "LRtempM", "LRtempR", "RRtempL", "RRtempM", "RRtempR",
            ]
            if name in self.channels
        ]
        pressure_channels = [
            name for name in ["LFpressure", "RFpressure", "LRpressure", "RRpressure"]
            if name in self.channels
        ]

        def endpoint_delta(names: list[str], start: int, end: int) -> Optional[float]:
            if not names:
                return None
            window = max(1, int(self.telemetry.tick_rate))
            start_values: list[float] = []
            end_values: list[float] = []
            for name in names:
                values = self.channels[name].astype(float, copy=False)
                start_values.extend(values[start : min(end + 1, start + window)].tolist())
                end_values.extend(values[max(start, end - window + 1) : end + 1].tolist())
            if not start_values or not end_values:
                return None
            return float(np.nanmedian(end_values) - np.nanmedian(start_values))

        fingerprints: list[dict[str, Any]] = []
        for lap in out_laps:
            start, end = lap.start_idx, lap.end_idx
            if end <= start:
                continue
            count = end - start + 1
            dt = 1.0 / max(1, self.telemetry.tick_rate)
            if session_time is not None:
                duration = float(session_time[end] - session_time[start])
            else:
                duration = count * dt
            lap_brake = brake[start : end + 1].astype(float, copy=False) if brake is not None else np.zeros(count)
            lap_speed = speed[start : end + 1].astype(float, copy=False) if speed is not None else np.zeros(count)
            lap_throttle = throttle[start : end + 1].astype(float, copy=False) if throttle is not None else np.zeros(count)
            lap_steering = steering[start : end + 1].astype(float, copy=False) if steering is not None else np.zeros(count)
            brake_groups = contiguous_groups(
                np.flatnonzero(lap_brake > 0.08),
                max_gap=max(1, int(self.telemetry.tick_rate * 0.08)),
                min_len=max(1, int(self.telemetry.tick_rate * 0.05)),
            )
            abs_ratio: Optional[float] = None
            braking_mask = lap_brake > 0.08
            if abs_active is not None and np.any(braking_mask):
                lap_abs = abs_active[start : end + 1].astype(float, copy=False) > 0.5
                abs_ratio = float(np.mean(lap_abs[braking_mask]))
            speed_mps = lap_speed
            brake_energy_proxy = float(np.sum(lap_brake * np.square(speed_mps) * dt))
            steering_work_deg = float(np.sum(np.abs(np.diff(np.degrees(lap_steering)))))
            final_start = start + int(count * 0.8)
            final_speed = (
                float(np.nanmean(speed[final_start : end + 1]) * 3.6)
                if speed is not None and end >= final_start
                else None
            )
            fingerprints.append(
                {
                    "lap_number": lap.lap_number,
                    "duration_s": round_or_none(duration, 2),
                    "brake_events": len(brake_groups),
                    "brake_energy_proxy": round_or_none(brake_energy_proxy, 1),
                    "full_throttle_ratio": round_or_none(float(np.mean(lap_throttle >= 0.95)), 3),
                    "steering_work_deg": round_or_none(steering_work_deg, 1),
                    "abs_duty_while_braking_pct": round_or_none(abs_ratio * 100.0, 1)
                    if abs_ratio is not None
                    else None,
                    "final_20pct_mean_speed_kph": round_or_none(final_speed, 1),
                    "surface_temp_delta_c": round_or_none(
                        endpoint_delta(tire_temp_channels, start, end), 2
                    ),
                    "pressure_delta_kpa": round_or_none(
                        endpoint_delta(pressure_channels, start, end), 2
                    ),
                }
            )

        return {
            "available": bool(fingerprints),
            "runs": fingerprints,
            "aggregate": {
                "brake_events_median": round_or_none(
                    float(np.median([item["brake_events"] for item in fingerprints])), 1
                )
                if fingerprints
                else None,
                "steering_work_deg_median": round_or_none(
                    float(np.median([item["steering_work_deg"] for item in fingerprints])), 1
                )
                if fingerprints
                else None,
                "surface_temp_delta_c_median": round_or_none(
                    mean_finite([item.get("surface_temp_delta_c") for item in fingerprints]), 2
                ),
                "pressure_delta_kpa_median": round_or_none(
                    mean_finite([item.get("pressure_delta_kpa") for item in fingerprints]), 2
                ),
            },
        }

    def _corner_analysis(
        self,
        target: dict[str, np.ndarray[Any, Any]],
        reference: Optional[dict[str, np.ndarray[Any, Any]]],
        definitions: list[dict[str, int]],
        *,
        _details: bool = True,
    ) -> list[dict[str, Any]]:
        n = len(target["distance_pct"])
        delta = target["time_s"] - reference["time_s"] if reference else np.zeros(n)
        results: list[dict[str, Any]] = []

        def coverage(grid: Optional[dict[str, Any]]) -> Optional[np.ndarray]:
            if grid is None:
                return None
            mask = np.asarray(grid.get("_coverage", np.isfinite(grid["time_s"]) & np.isfinite(grid.get("speed_kph", np.full(n, np.nan)))), dtype=bool)
            return mask & np.asarray(grid.get("_analysis_coverage", np.ones(n, dtype=bool)), dtype=bool)

        target_coverage, reference_coverage = coverage(target), coverage(reference)

        for number, definition in enumerate(definitions, start=1):
            entry = definition["entry"]
            apex = definition["apex"]
            exit_idx = definition["exit"]
            target_supported = target_coverage is not None and bool(np.all(target_coverage[entry:exit_idx + 1]))
            reference_supported = reference_coverage is not None and bool(np.all(reference_coverage[entry:exit_idx + 1]))
            measurement_usable = target_supported and (reference is None or reference_supported)
            local_delta = float(delta[exit_idx] - delta[entry]) if reference and measurement_usable else None
            target_metrics = self._corner_metrics(target, entry, apex, exit_idx)
            reference_metrics = self._corner_metrics(reference, entry, apex, exit_idx) if reference else None
            evidence, issue_type, driver_action = self._corner_evidence(
                local_delta,
                target_metrics,
                reference_metrics,
            )
            if not measurement_usable:
                evidence, issue_type, driver_action = ["관측되지 않은 구간이 포함되어 시간 손실과 조작 원인을 판단하지 않습니다."], "DATA_GAP", "같은 구간의 끊김 없는 주행 데이터를 기록하세요."
            elif reference is not None and not (target_metrics.get("inputs_usable") and (reference_metrics or {}).get("inputs_usable")):
                evidence, issue_type, driver_action = [f"구간 시간 차이 {local_delta:+.3f}초", "입력 채널 결측으로 제동·스로틀·조향 코칭을 제한합니다."], "INPUT_GAP", "입력 채널이 포함된 기준 랩으로 비교하세요."
            apex_speed = target_metrics["min_speed_kph"] or 0.0
            speed_class = "LOW" if apex_speed < 105 else "MEDIUM" if apex_speed < 165 else "HIGH"
            severity = (
                "HIGH"
                if local_delta is not None and local_delta >= 0.10
                else "MEDIUM"
                if local_delta is not None and local_delta >= 0.04
                else "LOW"
            )
            result = {
                "id": definition.get("id") or f"C{number}",
                "label": f"자동 구간 {number}",
                "segment_source": definition.get("source", "AUTOMATIC"),
                "official_turn_number": None,
                "measurement_usable": measurement_usable,
                "coaching_usable": measurement_usable and bool(target_metrics.get("inputs_usable")) and (reference is None or bool((reference_metrics or {}).get("inputs_usable"))),
                "entry_pct": round(float(target["distance_pct"][entry]), 7),
                "apex_pct": round(float(target["distance_pct"][apex]), 7),
                "exit_pct": round(float(target["distance_pct"][exit_idx]), 7),
                "distance_m": round(float(target["distance_pct"][apex] * self.track_length_m), 1),
                "speed_class": speed_class,
                "delta_s": round_or_none(local_delta, 3),
                "severity": severity,
                "issue_type": issue_type,
                "evidence": evidence,
                "driver_action": driver_action,
                "target": target_metrics,
                "reference": reference_metrics,
                "braking_band": self.speed_band(target_metrics.get("brake_speed_time"), (reference_metrics or {}).get("brake_speed_time"), True) if measurement_usable else None,
                "drive_band": self.speed_band(target_metrics.get("drive_speed_time"), (reference_metrics or {}).get("drive_speed_time"), False) if measurement_usable else None,
            }
            results.append(result)

        # Repetition only consumes the evidence above. Keep detailed enrichment
        # for displayed corners and memory snapshots, without computing it for
        # every lap merely to discard it during pattern aggregation.
        if not _details:
            return sorted(results, key=lambda item: item.get("delta_s") or -999, reverse=True)

        ordered = sorted(results, key=lambda item: item['entry_pct'])
        line_context = racing_line_context(target, reference, self.track_length_m,
                                          [(item['entry_pct'], item['exit_pct']) for item in ordered])
        for index, corner in enumerate(ordered):
            next_corner = ordered[index + 1] if index + 1 < len(ordered) else None
            corner['phases'] = self._corner_phases(target, reference, corner, next_corner)
            corner['first_deviation'] = first_deviation(corner, self.track_length_m)
            corner['line'] = racing_line_offsets(target, reference, corner, self.track_length_m,
                                                 _context=line_context)

        analysis_progress.stage('handling')
        handling = self._handling_analysis(ordered)
        for corner in ordered:
            corner['handling'] = [item for item in handling['corners'] if item['corner_id'] == corner['id']] if handling else []
            corner['handling_coverage'] = [item for item in handling['phase_coverage'] if item['corner_id'] == corner['id']] if handling else []

        # The UI and briefing consume the largest losses first while the map still
        # uses distance ordering through its markers.
        return sorted(results, key=lambda item: item.get("delta_s") or -999, reverse=True)

    def _handling_analysis(self, corners):
        if not getattr(self, 'valid_laps', None) or not hasattr(self, 'telemetry'):
            return None
        cache = getattr(self, '_handling_cache', None)
        if cache is None:
            self._handling_cache = cache = {}
        key = tuple(sorted((item['id'], item['entry_pct'], item['apex_pct'], item['exit_pct']) for item in corners))
        if key not in cache:
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = analyze_handling(self, corners)
        result = cache[key]
        if result is None:
            return None
        names = {item['id']: item['label'] for item in corners}
        return {**result, 'corners': [{**item, 'label': names[item['corner_id']]} for item in result['corners']]}

    def _corner_phases(self, target_grid, reference_grid, corner, next_corner):
        """Partition entry-to-exit; compare approach and the following straight separately."""
        reference = corner.get('reference') or {}
        if reference_grid is None or not any(reference.get(key + '_pct') is not None
                for key in ('brake_start', 'brake_release', 'throttle_first')):
            return None

        def number(value):
            return float(value) if value is not None and math.isfinite(value) else None

        def at(grid, key, pct):
            if pct is None or key not in grid:
                return None
            axis, values = grid['distance_pct'], grid[key]
            if pct < axis[0] or pct > axis[-1]:
                return None
            index = int(np.searchsorted(axis, pct))
            if index < len(axis) and axis[index] == pct:
                return number(values[index])
            if index == 0 or index == len(axis):
                return None
            a, b = number(values[index - 1]), number(values[index])
            if a is None or b is None:
                return None
            return a + (b - a) * (pct - axis[index - 1]) / (axis[index] - axis[index - 1])

        def entry_bound(item):
            entry = number(item.get('entry_pct'))
            brake = number((item.get('reference') or {}).get('brake_start_pct'))
            return min(entry, brake) if entry is not None and brake is not None else entry

        entry, exit_pct = number(corner.get('entry_pct')), number(corner.get('exit_pct'))
        if entry is None or exit_pct is None or exit_pct < entry:
            return None
        brake_start = number(reference.get('brake_start_pct'))
        release = number(reference.get('brake_release_pct'))
        pickup = number(reference.get('throttle_first_pct'))
        has_brake = brake_start is not None or release is not None
        release = float(np.clip(release, entry, exit_pct)) if release is not None else (entry if not has_brake else None)
        pickup = float(np.clip(pickup, entry, exit_pct)) if pickup is not None else exit_pct
        if release is not None:
            pickup = max(release, pickup)
        straight_end = entry_bound(next_corner) if next_corner is not None else None
        lap_end = min(float(target_grid['distance_pct'][-1]), float(reference_grid['distance_pct'][-1]))
        if straight_end is None or (exit_pct is not None and straight_end <= exit_pct):
            straight_end = lap_end

        def phase(start, end):
            if start is None or end is None or end < start:
                return None
            times = [at(grid, 'time_s', pct) for grid in (target_grid, reference_grid) for pct in (start, end)]
            if any(time is None for time in times):
                return None
            return {'start_pct': start, 'end_pct': end,
                    'delta_s': round((times[1] - times[0]) - (times[3] - times[2]), 6)}

        phases = {'approach': phase(brake_start, entry) if brake_start is not None and brake_start < entry else None,
                  'braking': phase(entry, release),
                  'rotation': phase(release, pickup),
                  'drive': phase(pickup, exit_pct),
                  'straight_after': phase(exit_pct, straight_end),
                  'bounds_source': 'reference_events'}
        if phases['straight_after'] is not None:
            a, b = (at(grid, 'speed_kph', exit_pct) for grid in (target_grid, reference_grid))
            phases['straight_after']['exit_speed_delta_kph'] = round(a - b, 3) if a is not None and b is not None else None
        required = ('braking', 'rotation', 'drive')
        total = corner.get('delta_s')
        phases['unexplained_s'] = (round(float(total) - sum(phases[key]['delta_s'] for key in required
                                                          if phases[key] is not None), 6)
                                   if number(total) is not None else None)
        phases['unexplained_reason'] = 'missing_boundary' if any(phases[key] is None for key in required) else None
        return phases

    def _corner_metrics(
        self,
        grid: Optional[dict[str, np.ndarray[Any, Any]]],
        entry: int,
        apex: int,
        exit_idx: int,
    ) -> Optional[dict[str, Any]]:
        if grid is None:
            return None
        # getattr: partial analyzers built without __init__ simply skip the memo.
        registered = getattr(self, "_grid_ids", {}).get(id(grid))
        if registered is None or registered[0] is not grid:
            return self._compute_corner_metrics(grid, entry, apex, exit_idx)
        key = (registered[1], int(entry), int(apex), int(exit_idx))
        cached = self._metric_cache.get(key)
        if cached is None:
            cached = self._metric_cache[key] = self._compute_corner_metrics(grid, entry, apex, exit_idx)
        return dict(cached)

    @staticmethod
    def speed_band(target: Optional[list], reference: Optional[list], falling: bool) -> Optional[dict[str, Any]]:
        """Time each lap took between the same two speeds (the range both covered)."""
        if not target or not reference:
            return None
        mine, theirs = {point[0]: point[1] for point in target}, {point[0]: point[1] for point in reference}
        common = sorted(set(mine) & set(theirs))
        if len(common) < 2:
            return None
        start, end = (common[-1], common[0]) if falling else (common[0], common[-1])
        target_s, reference_s = mine[end] - mine[start], theirs[end] - theirs[start]
        return {"from_kph": start, "to_kph": end, "target_s": round(target_s, 3), "reference_s": round(reference_s, 3),
                "delta_s": round(target_s - reference_s, 3)}

    def _corner_evidence(
        self,
        delta_s: Optional[float],
        target: dict[str, Any],
        reference: Optional[dict[str, Any]],
    ) -> tuple[list[str], str, str]:
        if reference is None:
            return ["비교 랩이 없어 절대 지표만 표시합니다."], "NO_REFERENCE", "동일 조건의 추가 유효 랩을 기록하십시오."

        length = self.track_length_m
        evidence: list[str] = []
        issue_type = "CONSISTENT"
        driver_action = "현재 입력 패턴을 유지하십시오."

        min_speed_delta = (target.get("min_speed_kph") or 0) - (reference.get("min_speed_kph") or 0)
        if abs(min_speed_delta) >= 1.0:
            evidence.append(f"최저속도 {min_speed_delta:+.1f} km/h")

        brake_distance = pct_distance(
            target.get("brake_start_pct"), reference.get("brake_start_pct"), length
        )
        full_throttle_distance = pct_distance(
            target.get("full_throttle_pct"), reference.get("full_throttle_pct"), length
        )
        coast_delta = (target.get("coast_time_s") or 0) - (reference.get("coast_time_s") or 0)
        abs_delta = target["abs_duty_pct"] - reference["abs_duty_pct"] if target.get("abs_duty_pct") is not None and reference.get("abs_duty_pct") is not None else None
        steering_delta = (target.get("avg_abs_steer_deg") or 0) - (reference.get("avg_abs_steer_deg") or 0)
        efficiency_target = target.get("steering_efficiency")
        efficiency_reference = reference.get("steering_efficiency")

        if brake_distance is not None and abs(brake_distance) >= 3:
            evidence.append(f"제동 시작 {brake_distance:+.0f} m")
        if full_throttle_distance is not None and abs(full_throttle_distance) >= 3:
            evidence.append(f"풀스로틀 복귀 {full_throttle_distance:+.0f} m")
        if abs(coast_delta) >= 0.03:
            evidence.append(f"코스팅 {coast_delta:+.2f} s")
        if abs_delta is not None and abs(abs_delta) >= 5:
            evidence.append(f"ABS 듀티 {abs_delta:+.1f}%p")
        if abs(steering_delta) >= 2:
            evidence.append(f"평균 조향량 {steering_delta:+.1f}°")

        if delta_s is None:
            return evidence or ["비교 델타를 계산할 수 없습니다."], "NO_DELTA", "추가 유효 랩을 기록하십시오."
        if delta_s <= -0.02:
            return evidence or [f"기준 랩 대비 {-delta_s:.3f}초 이득"], "GAIN", "이 구간의 입력 순서를 기준 패턴으로 저장하십시오."
        if delta_s < 0.02:
            return evidence or ["기준 랩과 차이가 작습니다."], "CONSISTENT", "변경보다 재현성 유지가 우선입니다."

        if full_throttle_distance is not None and full_throttle_distance > 5:
            issue_type = "EXIT"
            driver_action = f"조향을 먼저 풀고 풀스로틀 복귀를 약 {full_throttle_distance:.0f} m 앞당기는 것을 우선 시험하십시오."
        elif min_speed_delta < -2.0 and coast_delta > 0.02:
            issue_type = "MID_CORNER"
            driver_action = f"불필요한 코스팅을 줄여 최저속도를 약 {abs(min_speed_delta):.1f} km/h 회복하십시오."
        elif brake_distance is not None and brake_distance < -6 and min_speed_delta < -1:
            issue_type = "ENTRY"
            driver_action = f"제동 시작을 약 {abs(brake_distance):.0f} m 늦추고 릴리즈를 더 연속적으로 만드십시오."
        elif abs_delta is not None and abs_delta > 8 and min_speed_delta < 0:
            issue_type = "BRAKING"
            driver_action = "초기 피크 제동보다 ABS 개입 직전의 일정한 압력과 부드러운 릴리즈를 우선하십시오."
        elif (
            steering_delta > 3
            and efficiency_target is not None
            and efficiency_reference is not None
            and efficiency_target < efficiency_reference * 0.95
        ):
            issue_type = "BALANCE_SUSPECT"
            driver_action = "같은 속도에서 조향을 더 추가하지 말고 진입 속도와 브레이크 릴리즈를 먼저 재현해 셋업 문제인지 분리하십시오."
        else:
            issue_type = "DRIVER_VARIANCE"
            driver_action = "제동 종료·최저속도·스로틀 복귀 순서를 기준 랩과 동일하게 재현하십시오."

        evidence.insert(0, f"구간 손실 +{delta_s:.3f}초")
        return evidence, issue_type, driver_action

    def _repetition_analysis(
        self,
        definitions: list[dict[str, int]],
        session_mode: SessionMode = SessionMode.BASELINE_3LAP,
    ) -> dict[str, Any]:
        policy = session_policy(session_mode)
        required_laps = int(policy["setup_repeat_laps"])
        if len(self.valid_laps) < 2 or not definitions:
            return {
                "status": "INSUFFICIENT_LAPS",
                "valid_laps": len(self.valid_laps),
                "required_laps": required_laps,
                "repeated_setup_patterns": [],
                "candidate_setup_patterns": [],
            }

        fastest = min(self.valid_laps, key=lambda lap: lap.lap_time_s or math.inf)
        reference_grid = self._lap_grid(fastest)
        counts: dict[str, dict[str, Any]] = {}
        compared_laps = 0
        for lap in self.valid_laps:
            if lap.lap_number == fastest.lap_number:
                continue
            compared_laps += 1
            analyzed = self._corner_analysis(self._lap_grid(lap), reference_grid, definitions, _details=False)
            for corner in analyzed:
                if corner.get("issue_type") != "BALANCE_SUSPECT" or (corner.get("delta_s") or 0) < 0.02:
                    continue
                entry = counts.setdefault(
                    corner["id"],
                    {
                        "corner": corner["label"],
                        "corner_id": corner["id"],
                        "apex_pct": corner["apex_pct"],
                        "occurrences": 0,
                        "laps": [],
                        "losses_s": [],
                        "evidence": [],
                    },
                )
                entry["occurrences"] += 1
                entry["laps"].append(lap.lap_number)
                entry["losses_s"].append(float(corner.get("delta_s") or 0.0))
                entry["evidence"].extend(corner.get("evidence", []))

        required_occurrences = max(2, math.ceil(max(1, compared_laps) * 0.60))
        candidates: list[dict[str, Any]] = []
        repeated: list[dict[str, Any]] = []
        for raw in counts.values():
            item = dict(raw)
            losses = item.pop("losses_s", [])
            item["occurrence_ratio"] = round(item["occurrences"] / max(1, compared_laps), 3)
            item["mean_loss_s"] = round_or_none(float(np.mean(losses)) if losses else None, 3)
            item["max_loss_s"] = round_or_none(max(losses) if losses else None, 3)
            item["evidence"] = list(dict.fromkeys(item["evidence"]))[:6]
            candidates.append(item)
            if session_mode != SessionMode.QUALIFYING and item["occurrences"] >= required_occurrences:
                repeated.append(dict(item))

        candidates.sort(
            key=lambda item: (item.get("occurrence_ratio") or 0.0, item.get("mean_loss_s") or 0.0),
            reverse=True,
        )
        repeated.sort(
            key=lambda item: (item.get("occurrence_ratio") or 0.0, item.get("mean_loss_s") or 0.0),
            reverse=True,
        )

        if session_mode == SessionMode.QUALIFYING:
            return {
                "status": "QUALIFYING_LIMITED",
                "valid_laps": len(self.valid_laps),
                "required_laps": 2,
                "reference_lap": fastest.lap_number,
                "compared_laps": compared_laps,
                "required_occurrences": 2,
                "repeated_setup_patterns": [],
                "candidate_setup_patterns": candidates,
                "note": (
                    "두 퀄리파잉 핫랩 사이의 밸런스 신호는 확정 근거가 아니라 A/B 실험 후보로만 사용합니다. "
                    "외부 기준 또는 별도 세션에서 같은 방향이 반복되면 신뢰도를 올립니다."
                ),
            }

        return {
            "status": "READY" if len(self.valid_laps) >= required_laps else "PARTIAL_REPEATABILITY",
            "valid_laps": len(self.valid_laps),
            "required_laps": required_laps,
            "reference_lap": fastest.lap_number,
            "compared_laps": compared_laps,
            "required_occurrences": required_occurrences,
            "repeated_setup_patterns": repeated,
            "candidate_setup_patterns": candidates,
        }

    def _coaching_priorities(
        self,
        corners: list[dict[str, Any]],
        comparison: Optional[dict[str, Any]],
        external_reference: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        priorities: list[dict[str, Any]] = []
        external_losses = (external_reference or {}).get("top_losses", [])
        if external_losses:
            for corner in external_losses:
                delta = corner.get("delta_s")
                if delta is None or delta < 0.02:
                    continue
                consensus = corner.get("reference_consensus", {}) or {}
                priorities.append(
                    {
                        "rank": len(priorities) + 1,
                        "corner": corner["label"],
                        "distance_m": corner["distance_m"],
                        "loss_s": delta,
                        "issue_type": corner["issue_type"],
                        "action": corner["driver_action"],
                        "evidence": [
                            f"Garage61 기준 {consensus.get('reference_count', 0)}개 중 손실 방향 일치 {round((consensus.get('loss_agreement_ratio') or 0) * 100)}%",
                            *corner.get("evidence", []),
                        ][:7],
                        "source": "GARAGE61_REFERENCE_CONSENSUS",
                    }
                )
                if len(priorities) >= 3:
                    return priorities

        for corner in corners:
            delta = corner.get("delta_s")
            if delta is None or delta < 0.02:
                continue
            priorities.append(
                {
                    "rank": len(priorities) + 1,
                    "corner": corner["label"],
                    "distance_m": corner["distance_m"],
                    "loss_s": delta,
                    "issue_type": corner["issue_type"],
                    "action": corner["driver_action"],
                    "evidence": corner["evidence"],
                    "source": "PERSONAL_LAP_COMPARISON",
                }
            )
            if len(priorities) >= 3:
                break

        if not priorities and comparison:
            priorities.append(
                {
                    "rank": 1,
                    "corner": "전체 랩",
                    "distance_m": None,
                    "loss_s": None,
                    "issue_type": "CONSISTENCY",
                    "action": "현재 베스트 랩의 입력 패턴을 연속 3랩 재현하여 셋업 판정 표본을 확보하십시오.",
                    "evidence": ["유의미한 단일 코너 손실이 검출되지 않았습니다."],
                    "source": "PERSONAL_LAP_COMPARISON",
                }
            )
        return priorities
