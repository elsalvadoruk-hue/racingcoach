"""Two manually selected LMU laps, compared on jointly supported distance."""
from __future__ import annotations

import math
import numpy as np

from lmu_comparison import _axis, compatibility
from lmu_corners import compare_corners
from lmu_vehicle import read_vehicle_native
from telemetry_core import ChannelTrace


MAX_KNOTS = 2_000_000
MAX_SUPPORT_RANGES = 256


def _render_indices(reference, target, good, limit):
    valid = np.flatnonzero(good)
    if len(valid) <= limit:
        return valid
    selected = set()
    with np.errstate(over="ignore", invalid="ignore"):
        delta = target-reference
    # Six extrema per bucket fit when the budget allows. For a three-point
    # budget, difference extrema take priority; all signal extrema cannot fit.
    for bucket in np.array_split(np.arange(len(good)), max(1, limit//6)):
        bucket = bucket[good[bucket]]
        if not len(bucket):
            continue
        for values in (delta, reference, target):
            for index in (bucket[np.argmin(values[bucket])], bucket[np.argmax(values[bucket])]):
                if len(selected) < limit:
                    selected.add(int(index))
    for index in (valid[0], valid[-1]):
        if len(selected) < limit:
            selected.add(int(index))
    return np.asarray(sorted(selected), dtype=int)


def _measure(distance, reference, target, start, end, limit=None):
    inside = (distance >= start) & (distance <= end)
    d, r, t = distance[inside], reference[inside], target[inside]
    with np.errstate(over="ignore", invalid="ignore"):
        difference = t-r
    good = np.isfinite(r) & np.isfinite(t) & np.isfinite(difference)
    widths = np.diff(d)
    edges = good[:-1] & good[1:]
    support = float(np.sum(widths[edges]))
    spans = []
    for first, last in zip(np.flatnonzero(edges & ~np.r_[False, edges[:-1]]),
                           np.flatnonzero(edges & ~np.r_[edges[1:], False])):
        spans.append({"start_m": float(d[first]), "end_m": float(d[last+1])})
    stats = lambda: {"minimum": None, "maximum": None, "mean": None}
    result = {"status": "UNAVAILABLE", "start_m": float(start), "end_m": float(end),
              "requested_distance_m": float(end-start), "supported_distance_m": support,
              "support_ranges": spans[:MAX_SUPPORT_RANGES], "support_range_count": len(spans),
              "support_ranges_truncated": len(spans) > MAX_SUPPORT_RANGES,
              "reference": stats(), "target": stats(), "delta": stats()}
    if support > 0:
        supported_nodes = np.r_[edges, False] | np.r_[False, edges]
        for name, values in (("reference", r), ("target", t), ("delta", difference)):
            # Both readings use exactly the same supported intervals; gaps
            # contribute neither distance nor area to either average.
            mean = math.fsum(float(x) for x in ((values[:-1][edges]/2+values[1:][edges]/2)*(widths[edges]/support)))
            result[name] = {"minimum": float(np.min(values[supported_nodes])), "maximum": float(np.max(values[supported_nodes])), "mean": mean}
        result["status"] = "AVAILABLE" if math.isclose(support, end-start, rel_tol=0, abs_tol=1e-7) else "PARTIAL"
    if limit is not None:
        indices = _render_indices(r, t, good, limit) if support > 0 else np.empty(0, dtype=int)
        segments = np.cumsum(good & ~np.r_[False, good[:-1]])-1 if len(good) else np.empty(0)
        result["points"] = [{"distance_m": float(d[i]), "reference": float(r[i]),
                             "target": float(t[i]), "delta": float(t[i]-r[i]), "segment": int(segments[i])}
                            for i in indices]
    if result["status"] != "AVAILABLE":
        result["reason"] = "양쪽 모두 지원하는 거리만 계산했으며 결측이나 범위 밖 구간은 제외했습니다."
    return result


def compare_vehicle(reference_reader, target_reader, reference_lap, target_lap,
                    channel_name="TyresPressure", points=301):
    if type(points) is not int or not 3 <= points <= 501:
        raise ValueError("차량 비교 표시 지점은 3~501개여야 합니다.")
    reports, native = [], []
    for reader, number in ((reference_reader, reference_lap), (target_reader, target_lap)):
        report, source = read_vehicle_native(reader, number, channel_name)
        reports.append(report)
        native.append(source)
    base = {"status": "UNAVAILABLE", "reference_status": "VALIDITY_UNVERIFIED",
            "conditions": "NOT_COMPARED", "causal_effect": "NOT_ESTABLISHED",
            "compatibility": None,
            "channel": {key: reports[0]["channel"][key] for key in ("name", "label", "unit", "wheel_order")},
            "corner_comparison": {"status": "UNAVAILABLE", "definition": None, "corners": [],
                                  "reason": "기준 랩의 기하 구간을 확인하지 못했습니다."},
            "note": "차이는 대상 랩에서 기준 랩을 뺀 관측값입니다. 조건·게임 유효성·휠 순서·셋업 효과는 미확인입니다."}
    base["channel"]["components"] = []
    for key, number, report in zip(("reference", "target"), (reference_lap, target_lap), reports):
        base[key] = {"lap_number": number, "recorded_lap_time_s": report["recorded_lap_time_s"],
                     "source_unit": report["channel"]["source_unit"], "frequency_hz": report["channel"]["frequency_hz"]}
    if not all(source for source in native):
        return base | {"reason": next(report.get("reason", "지원하는 원본 관측을 확인할 수 없습니다.")
                                       for report, source in zip(reports, native) if not source)}
    laps = [source["lap"] for source in native]
    base["compatibility"] = compatibility(*laps)
    for key, lap in zip(("reference", "target"), laps):
        base[key].update(valid=None, diagnostic_status=lap.diagnostic_status,
                         metadata={name: lap.metadata.get(name) for name in
                                   ("track_name", "track_layout", "car_name", "car_class")})
    axes = [_axis(lap) for lap in laps]
    spans = [float(axis.values[-1]-axis.values[0]) for axis in axes]
    if abs(spans[1]-spans[0]) > max(50, .01*max(spans)):
        raise ValueError("두 랩의 기록 거리 범위가 달라 차량 채널을 비교하지 않았습니다.")
    if (native[0]["fields"] != native[1]["fields"] or
            reports[0]["channel"]["unit"] != reports[1]["channel"]["unit"]):
        return base | {"reason": "두 채널의 성분 또는 변환 단위가 달라 비교하지 않았습니다."}
    start = max(float(axis.values[0]) for axis in axes)
    end = min(float(axis.values[-1]) for axis in axes)
    if not start < end:
        return base | {"reason": "두 랩에서 겹치는 거리 범위가 없습니다."}
    geometry = compare_corners(*laps)
    definition = geometry.get("definition")
    bounds = definition.get("boundaries", []) if definition else []
    # Never clip/redefine a corner. A boundary outside common observation is
    # explicitly unavailable, while the overall common-distance result remains.
    available_bounds = [b for b in bounds if start <= b["start_m"] < b["end_m"] <= end]
    extra = [v for b in available_bounds for v in (b["start_m"], b["end_m"])]
    candidates = [np.asarray([start, end]+extra)]
    for report, source, axis in zip(reports, native, axes):
        positions = axis.at(source["time_s"])
        candidates.extend((positions[np.isfinite(positions) & (positions >= start) & (positions <= end)],
                           axis.values[(axis.values >= start) & (axis.values <= end)]))
        # An oversized native time gap can lie between consecutive union knots.
        # Add its midpoint so two supported endpoints never bridge that gap.
        hz = report["channel"]["frequency_hz"]
        gaps = np.diff(source["time_s"]) > 1.5/hz+1e-9
        midpoint_times = (source["time_s"][:-1][gaps]+source["time_s"][1:][gaps])/2
        midpoints = axis.at(midpoint_times)
        candidates.append(midpoints[np.isfinite(midpoints) & (midpoints >= start) & (midpoints <= end)])
    if sum(len(values) for values in candidates) > MAX_KNOTS:
        return base | {"reason": "공통 원본 거리 지점 수가 계산 메모리 한도를 초과했습니다."}
    distance = np.unique(np.concatenate(candidates))
    query_times = [np.interp(distance, axis.values, axis.time_s) for axis in axes]
    corner_rows = [b | {"components": []} for b in bounds]
    for component, field in enumerate(native[0]["fields"]):
        values = []
        for report, source, times in zip(reports, native, query_times):
            raw = source["values"][:, component]
            trace = ChannelTrace(field, report["channel"]["unit"], report["channel"]["frequency_hz"],
                                 source["time_s"], raw, np.isfinite(raw))
            values.append(trace.at(times))
        result = _measure(distance, *values, start, end, points) | {"id": field, "label": field if len(native[0]["fields"]) == 4 else "측정값"}
        base["channel"]["components"].append(result)
        for row in corner_rows:
            bstart, bend = row["start_m"], row["end_m"]
            if start <= bstart < bend <= end:
                row["components"].append(_measure(distance, *values, bstart, bend) | {"id": field, "label": result["label"]})
            else:
                row["components"].append({"id": field, "label": result["label"], "status": "UNAVAILABLE",
                    "reason": "기준 구간 경계 전체가 양쪽의 공통 관측 거리 안에 있지 않습니다.",
                    "start_m": bstart, "end_m": bend, "requested_distance_m": bend-bstart,
                    "supported_distance_m": 0., "support_ranges": [], "support_range_count": 0,
                    "support_ranges_truncated": False,
                    "reference": {"minimum": None, "maximum": None, "mean": None},
                    "target": {"minimum": None, "maximum": None, "mean": None},
                    "delta": {"minimum": None, "maximum": None, "mean": None}})
    statuses = [component["status"] for component in base["channel"]["components"]]
    base["status"] = "AVAILABLE" if all(s == "AVAILABLE" for s in statuses) else "PARTIAL" if any(s != "UNAVAILABLE" for s in statuses) else "UNAVAILABLE"
    if definition:
        cs = [c["status"] for row in corner_rows for c in row["components"]]
        base["corner_comparison"] = {"status": "AVAILABLE" if cs and all(s == "AVAILABLE" for s in cs) else "PARTIAL" if any(s != "UNAVAILABLE" for s in cs) else "UNAVAILABLE",
                                     "definition": definition, "corners": corner_rows}
    else:
        base["corner_comparison"]["reason"] = geometry.get("reason", base["corner_comparison"]["reason"])
    base["channel"]["statistics_method"] = "JOINT_SUPPORTED_DISTANCE_PIECEWISE_LINEAR_INTEGRATION"
    base["channel"]["decimation_note"] = "통계는 표시점 수와 무관합니다. 작은 표시 예산에서는 차이의 극값을 우선하며 모든 신호의 극값과 양 끝점 보존을 보장하지 않습니다."
    if base["status"] != "AVAILABLE":
        base["reason"] = "성분별 공통 지원 거리에 결측 또는 관측 범위 제한이 있습니다."
    return base


def compare_vehicle_laps(reference_reader, reference_lap_number, target_reader, target_lap_number,
                         channel_name="TyresPressure", points=301):
    return compare_vehicle(reference_reader, target_reader, reference_lap_number,
                           target_lap_number, channel_name, points)
