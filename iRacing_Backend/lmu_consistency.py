"""Manually selected LMU laps measured against one reference geometry definition."""
from __future__ import annotations

import math
import statistics

from lmu_corners import compare_corners

INPUT_LABELS = {"brake_start_m": "최초 브레이크 8% 초과 관측 위치",
                "throttle_first_m": "기하 중심 이후 최초 스로틀 20% 초과 위치",
                "full_throttle_m": "기하 중심 이후 최초 스로틀 95% 초과 위치"}


def corner_consistency(laps):
    if not 3 <= len(laps) <= 6:
        raise ValueError("구간별 일관성 분석은 서로 다른 기록 랩 3~6개를 직접 선택하세요.")
    reports = [compare_corners(laps[0], lap) for lap in laps[1:]]
    first = reports[0]
    base = {"status": "UNAVAILABLE", "lap_count": len(laps),
            "definition": first.get("definition"), "corners": [], "focus": [],
            "reference_status": "VALIDITY_UNVERIFIED", "conditions": "NOT_COMPARED",
            "scope": "MANUALLY_SELECTED_OBSERVATIONS", "automatic_best_lap": None}
    if any(report.get("status") == "UNAVAILABLE" for report in reports):
        return base | {"reason": next((r.get("reason") for r in reports
                                       if r.get("status") == "UNAVAILABLE"),
                                      "공통 코너 후보 구간을 확인할 수 없습니다.")}
    definitions = [r.get("definition") for r in reports]
    if any(definition != definitions[0] for definition in definitions[1:]):
        return base | {"reason": "기준 랩의 구간 정의가 달라 일관성을 계산하지 않았습니다."}
    by_id = [{row["id"]: row for row in report.get("corners", [])} for report in reports]
    rows = []
    for reference in first.get("corners", []):
        group = [report.get(reference["id"]) for report in by_id]
        measures = [reference.get("reference")] + [row.get("target") if row else None for row in group]
        passes = []
        for index, (lap, measure) in enumerate(zip(laps, measures)):
            duration = (measure or {}).get("metrics", {}).get("duration_s")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0:
                passes.append({"selection_index": index, "lap_number": lap.lap_number,
                               "duration_s": duration})
        row = {key: reference[key] for key in ("id", "label", "start_m", "end_m")}
        row.update(sample_count=len(passes), passes=passes, median_duration_s=None,
                   min_duration_s=None, max_duration_s=None, spread_s=None, std_duration_s=None)
        if len(passes) >= 3:
            durations = [p["duration_s"] for p in passes]
            row.update(status="AVAILABLE", median_duration_s=statistics.median(durations),
                       min_duration_s=min(durations), max_duration_s=max(durations),
                       spread_s=max(durations)-min(durations), std_duration_s=statistics.pstdev(durations))
        else:
            row.update(status="INSUFFICIENT_PASSES", reason="같은 구간에서 측정 가능한 주행이 3회 미만입니다.")
        row['input_consistency'] = {}
        for metric, label in INPUT_LABELS.items():
            values, observations = [], []
            for index, measure in enumerate(measures):
                value = (measure or {}).get('metrics', {}).get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    values.append(value)
                    observations.append({'selection_index': index, 'value_m': value,
                                         'active_at_center': (measure or {}).get('boundary_observations', {}).get(metric+'_active_at_center')})
            stages_match = all(item and item.get('delta_support', {}).get(metric, {}).get('status') in ('AVAILABLE', 'UNAVAILABLE')
                               for item in group)
            info = {'status': 'INSUFFICIENT_PASSES', 'label': label, 'sample_count': len(values),
                    'median_m': None, 'min_m': None, 'max_m': None, 'spread_m': None,
                    'observations': observations, 'approximate': True,
                    'reason': '입력 위치를 측정한 주행이 3회 미만입니다.'}
            if not stages_match:
                info.update(status='INPUT_STAGE_MISMATCH', reason='입력 처리 단계가 확인되지 않거나 달라 반복성을 비교하지 않았습니다.')
            elif len(values) >= 3:
                info.update(status='AVAILABLE', median_m=statistics.median(values), min_m=min(values),
                            max_m=max(values), spread_m=max(values)-min(values), reason=None)
            row['input_consistency'][metric] = info
        rows.append(row)
    observed = [row for row in rows if row["status"] == "AVAILABLE"]
    # A check to make on the next drive, not a prescription or a causal claim.
    candidates = [(row, key, info) for row in rows for key, info in row['input_consistency'].items()
                  if info['status'] == 'AVAILABLE' and key in ('brake_start_m', 'full_throttle_m')]
    candidates.sort(key=lambda item: item[2]['spread_m'], reverse=True)
    focus, used = [], set()
    for row, key, info in candidates:
        if row['id'] in used:
            continue
        used.add(row['id'])
        focus.append({'id': row['id'], 'label': row['label'], 'metric': key,
                      'observation': f"선택한 {info['sample_count']}회에서 {info['label']}의 범위가 {info['spread_m']:.1f}m입니다. 위치는 최대 2m 격자의 근사 관측입니다.",
                      'next_check': f"{info['label']}를 먼저 비교하고 다음 주행에서도 편차가 반복되는지 확인하세요. 구간 시간도 함께 확인하며 입력 변경의 효과는 아직 확정하지 않습니다."})
        if len(focus) == 3:
            break
    return base | {"status": "AVAILABLE" if len(observed) == len(rows) and rows else "PARTIAL" if observed else "UNAVAILABLE",
                   "reason": None if observed else "3회 이상 측정 가능한 공통 코너 후보가 없습니다.",
                   "corners": rows, "focus": focus, 'focus_basis': 'BRAKING_AND_EXIT_INPUT_REPEATABILITY',
                   'input_focus_reason': None if focus else '입력 반복성 관측 과제를 만들려면 같은 처리 단계의 측정값이 3회 이상 필요합니다.'}
