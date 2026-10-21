"""Measure distance-grid event errors against recorded samples (read-only T0).

No application modules are imported until main() has isolated every data store.
LMU samples are already resampled to 60 Hz and are reported separately.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {
    "brake_start": ("Brake", .08, True, True),
    "brake_release": ("Brake", .05, False, False),
    "throttle_first": ("Throttle", .20, True, True),
    "full_throttle": ("Throttle", .95, True, True),
}


def sample_event(pct, times, values, threshold, low, high, rising, first, tick_s,
                 track_length_m):
    """Locate a threshold crossing without sorting or bridging unobserved spans.

    T0 uses the existing thresholds, without T2's proposed debounce changes.
    A control already held at the search boundary is reported as clipped and
    excluded from the crossing-error gate: its true crossing was not observed.
    """
    if values is None:
        return None
    selected = np.flatnonzero(np.isfinite(pct) & (pct >= low) & (pct <= high))
    if not len(selected):
        return None
    hits = []
    for i in selected:
        j = i - 1 if rising else i + 1
        if j < 0 or j >= len(pct):
            continue
        a, b = min(i, j), max(i, j)
        if not all(math.isfinite(float(v)) for v in
                   (pct[a], pct[b], times[a], times[b], values[a], values[b])):
            continue
        dt, dp = times[b] - times[a], pct[b] - pct[a]
        if not (0 < dt <= 1.5 * tick_s and 0 <= dp * track_length_m <= 25):
            continue
        crossed = (values[a] <= threshold < values[b]) if rising else (
            values[a] > threshold >= values[b])
        if not crossed:
            continue
        fraction = float((threshold - values[a]) / (values[b] - values[a]))
        position = float(pct[a] + fraction * dp)
        if low <= position <= high:
            hits.append({"pct": position, "time_s": float(times[a] + fraction * dt),
                         "source": "CROSSING"})
    # Match the baseline's boundary-held controls, but do not claim a crossing.
    edge = int(selected[0] if first else selected[-1])
    if math.isfinite(float(values[edge])) and values[edge] > threshold:
        clipped = {"pct": float(pct[edge]), "time_s": float(times[edge]),
                   "source": "CLIPPED"}
        if math.isfinite(clipped["time_s"]):
            if not hits or (first and clipped["pct"] < hits[0]["pct"]) or (
                    not first and clipped["pct"] > hits[-1]["pct"]):
                return clipped
    return (hits[0] if first else hits[-1]) if hits else None


def abs_time_share(pct, times, brake, active, low, high, tick_s, track_length_m):
    """Time-weight ABS states, clipping each observed interval to the window.

    ABS is a discrete state held to the next sample. Brake threshold crossings
    use linear interpolation. Missing ABS intervals are excluded, never false.
    """
    if brake is None or active is None:
        return None
    observed = engaged = braking = 0.0
    for i in range(len(pct) - 1):
        if not all(math.isfinite(float(v)) for v in
                   (pct[i], pct[i + 1], times[i], times[i + 1], brake[i], brake[i + 1])):
            continue
        dt, dp = times[i + 1] - times[i], pct[i + 1] - pct[i]
        if not (0 < dt <= 1.5 * tick_s and 0 < dp * track_length_m <= 25):
            continue
        left, right = max(0., (low - pct[i]) / dp), min(1., (high - pct[i]) / dp)
        if right <= left:
            continue
        slope = brake[i + 1] - brake[i]
        if slope > 0:
            left = max(left, (.08 - brake[i]) / slope)
        elif slope < 0:
            right = min(right, (.08 - brake[i]) / slope)
        elif brake[i] <= .08:
            continue
        duration = max(0., right - left) * dt
        braking += duration
        if math.isfinite(float(active[i])):
            observed += duration
            if active[i] > .5:
                engaged += duration
    if observed <= 0:
        return None
    return {"pct": 100 * engaged / observed,
            "observed_share": observed / braking if braking else None,
            "observed_s": observed}


def distribution(values):
    values = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if not len(values):
        return {"n": 0, "median": None, "p95": None, "max": None}
    return {"n": len(values), "median": float(np.median(values)),
            "p95": float(np.percentile(values, 95)), "max": float(np.max(values))}


def summarize(rows):
    result = {}
    for name in (*EVENTS, "abs_duty"):
        items = [r[name] for r in rows if r.get(name)]
        # A clipped boundary says nothing about the location of a true crossing.
        comparable = [item for item in items if item.get("source") != "CLIPPED"]
        deltas = [item["delta"] for item in comparable if item.get("delta") is not None]
        result[name] = {"signed": distribution(deltas),
                        "absolute": distribution([abs(v) for v in deltas]),
                        "clipped": sum(item.get("source") == "CLIPPED" for item in items),
                        "missing": sum(item.get("delta") is None for item in items) + len(rows) - len(items)}
    return result


def phase_summary(rows):
    complete, missing, unavailable = [], 0, 0
    for row in rows:
        phases = row.get('phases')
        if phases is None:
            unavailable += 1
        elif any(phases.get(key) is None for key in ('braking', 'rotation', 'drive')):
            missing += 1
        else:
            complete.append(phases.get('unexplained_s'))
    return {'signed': distribution(complete),
            'absolute': distribution([abs(value) for value in complete if value is not None]),
            'missing_boundary': missing, 'unavailable': unavailable,
            'changed_causes': sum(bool(row.get('cause_changed')) for row in rows)}


def phase_gate(files):
    native = [item for item in files if item['sample_basis'] == 'NATIVE']
    failures = [item['file'] for item in native
                if item['phase_measurement']['absolute']['p95'] is not None
                and item['phase_measurement']['absolute']['p95'] > .005]
    return {'stop': bool(failures), 'failed_files': failures,
            'rule': 'Per NATIVE file: absolute unexplained_s p95 <= 0.005 s, all three corner phases observed'}


def measure_phases(analyzer, api):
    """Compare each other valid lap with the file's fastest valid lap.

    The gate uses absolute residuals for complete three-phase corners per file.
    Approach and straight losses are excluded from corner accounting.
    """
    if not analyzer.valid_laps:
        return {'reference_lap': None, 'rows': [], **phase_summary([])}
    best = min(analyzer.valid_laps, key=lambda lap: float(lap.lap_time_s or float('inf')))
    reference = analyzer._lap_grid(best)
    definitions = analyzer._detect_corners(reference)
    rows = []
    for lap in analyzer.valid_laps:
        if lap.lap_number == best.lap_number:
            continue
        for corner in analyzer._corner_analysis(analyzer._lap_grid(lap), reference, definitions):
            diagnosis = api.detailed_corner_diagnosis(corner, analyzer.track_length_m, 'LOCAL')
            rows.append({'lap': lap.lap_number, 'corner': corner['id'],
                         'entry_pct': corner['entry_pct'], 'exit_pct': corner['exit_pct'],
                         'delta_s': corner['delta_s'], 'phases': corner['phases'],
                         'first_deviation': corner['first_deviation'],
                         'root_cause': diagnosis['root_cause'],
                         'rule_cause': diagnosis.get('rule_cause'),
                         'cause_changed': bool(diagnosis.get('cause_from_first_deviation'))})
    return {'reference_lap': best.lap_number, 'comparison': 'other valid laps vs file-fastest valid lap',
            'rows': rows, **phase_summary(rows)}


def measure_file(path, api, native_events=False, phases=False):
    telemetry = api.repository.load(path)
    analyzer = api.TelemetryAnalyzer(telemetry)
    # Exercise the same analyzer entry point used by /api/analyze, without AI,
    # saved analyses, shared-state updates or external reference selection.
    result = analyzer.analyze(api.AnalyzeRequest(filename=path.name, include_ai=False,
                                                persist_memory=False))
    length = telemetry.metadata.get("track_length_m")
    if not length or not math.isfinite(float(length)) or length <= 0:
        raise ValueError("Recorded track length is unavailable")
    game = str(telemetry.metadata.get("game") or "iracing").lower()
    corners = result["performance"]["corners"]
    rows = []
    for lap in analyzer.valid_laps:
        grid = analyzer._lap_grid(lap)
        span = slice(lap.start_idx, lap.end_idx + 1)
        channels = analyzer.channels
        pct = np.asarray(channels["LapDistPct"][span], dtype=float)
        times = np.asarray(channels["SessionTime"][span], dtype=float)
        raw = {key: np.asarray(channels[key][span], dtype=float) if key in channels else None
               for key in ("Brake", "Throttle", "BrakeABSactive")}
        for corner in sorted(corners, key=lambda c: c["apex_pct"]):
            entry, apex, exit_idx = [int(np.argmin(abs(grid["distance_pct"] - corner[key])))
                                     for key in ("entry_pct", "apex_pct", "exit_pct")]
            metrics = analyzer._corner_metrics(grid, entry, apex, exit_idx)
            # Use the actual grid window, including the current integer padding.
            n = len(grid["distance_pct"])
            pad = max(1, int(round(75. / max(1., length) * (n - 1))))
            windows = {
                "brake_start": (grid["distance_pct"][max(0, entry - pad)], grid["distance_pct"][apex]),
                "brake_release": (grid["distance_pct"][entry], grid["distance_pct"][min(n, apex + pad) - 1]),
                "throttle_first": (grid["distance_pct"][apex], grid["distance_pct"][min(n - 1, exit_idx + pad)]),
            }
            windows["full_throttle"] = windows["throttle_first"]
            row = {"lap": lap.lap_number, "corner": corner["id"], "label": corner["label"]}
            native = None
            if native_events:
                from corner_events import corner_events
                recorded = analyzer._raw_lap_for(grid)
                if recorded is None:
                    raise ValueError('Native lap association unavailable')
                native = corner_events(recorded, *(float(grid['distance_pct'][i]) for i in
                                                   (entry, apex, exit_idx)))
            for name, (key, threshold, rising, first) in EVENTS.items():
                event = sample_event(pct, times, raw[key], threshold, *windows[name],
                                     rising, first, 1. / telemetry.tick_rate, length)
                if native is not None:
                    event = native[name]
                baseline = metrics.get(name + "_pct")
                row[name] = {"grid_pct": baseline, "raw": event,
                             "source": event["source"] if event else None,
                             "delta": (baseline - event["pct"]) * length
                             if baseline is not None and event is not None else None}
                row[name]['matches'] = bool((baseline is None and event is None) or
                                            (baseline is not None and event is not None and baseline == event['pct']))
            share = abs_time_share(pct, times, raw["Brake"], raw["BrakeABSactive"],
                                   grid["distance_pct"][entry], grid["distance_pct"][apex],
                                   1. / telemetry.tick_rate, length)
            baseline = metrics.get("abs_duty_pct")
            expected_share = share['pct'] if share is not None else None
            if native is not None:
                expected_share = native['abs_time_share']
                expected_share = round(expected_share, 1) if expected_share is not None else None
                share = {'pct': expected_share, 'observed_share': native['abs_observed_share']}
            row["abs_duty"] = {"grid_pct": baseline, "raw": share,
                               "delta": baseline - expected_share
                               if baseline is not None and expected_share is not None else None,
                               'matches': bool(baseline == expected_share)}
            rows.append(row)
    return {"file": path.name, "game": game,
            "sample_basis": "RESAMPLED" if game == "lmu" else "NATIVE",
            "track": telemetry.metadata.get("track_name"), "valid_laps": len(analyzer.valid_laps),
            "summary": summarize(rows), "rows": rows,
            **({'phase_measurement': measure_phases(analyzer, api)} if phases else {})}


def isolate(folder):
    # Overwrite inherited data locations so this script cannot write user stores.
    for key, suffix in {
        "IRACING_TUNER_DB_PATH": "memory.sqlite3", "IRACING_MEMORY_DB_PATH": "memory.sqlite3",
        "IRACING_VOICE_DB_PATH": "voice.sqlite3", "IRACING_DATA_DIR": ".",
        "IRACING_FILE_INDEX_PATH": "file-index.sqlite3", "IRACING_CORNER_DEFINITIONS_PATH": "corners.json",
        "IRACING_PIT_KNOWLEDGE_PATH": "pit.json", "IRACING_RADIO_AUDIO_DIR": "radio_audio",
        "IRACING_SPOTTER_AUDIO_DIR": "spotter_audio", "IRACING_EXTERNAL_REFERENCE_DIR": "references",
        "IRACING_LMU_CAR_MODELS_PATH": "models.json", "IRACING_LMU_LAP_VERDICTS_PATH": "verdicts.json",
        "IRACING_GARAGE61_JOB_DIR": "jobs",
    }.items():
        os.environ[key] = str(folder / suffix)
    os.environ.update(IRACING_AI_ENABLED="0", GARAGE61_AUTO_COLLECT="0", IRACING_IMPORT_LEGACY="0")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "logs/analysis-210/t0.json")
    parser.add_argument('--native-events', action='store_true',
                        help='Verify T2 recorded events against the connected analyzer output')
    parser.add_argument('--phases', action='store_true',
                        help='Measure T3 phase residuals and diagnosis changes against file-best laps')
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="iracing-analysis-t0-") as temporary:
        isolate(Path(temporary))
        sys.path.insert(0, str(ROOT / "iRacing_Backend"))
        import backend_api as api
        from lmu_telemetry import list_lmu_telemetry_files

        paths = args.files
        if not paths:
            ibt = Path.home() / "Documents/iRacing/telemetry"
            paths = sorted(ibt.glob("*.ibt"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
            paths += list_lmu_telemetry_files()[:3]
        if not paths:
            parser.error("No recorded IBT or DuckDB files found")
        report = {"format": "corner-event-baseline", "version": 1, "files": [], "errors": []}
        for path in paths:
            print(f"Measuring {path.name}", flush=True)
            try:
                report["files"].append(measure_file(path.resolve(), api, args.native_events, args.phases))
            except Exception as exc:
                report["errors"].append({"file": path.name, "type": type(exc).__name__, "error": str(exc)})
                print(f"  {type(exc).__name__}: {exc}", flush=True)
            # Large input files must not accumulate in the repository cache.
            api.repository._cache.clear()
            gc.collect()
        report["groups"] = {}
        for basis in ("NATIVE", "RESAMPLED"):
            rows = [r for f in report["files"] if f["sample_basis"] == basis for r in f["rows"]]
            report["groups"][basis] = summarize(rows)
        native = report["groups"]["NATIVE"]
        proceed = any(native[key]["absolute"]["p95"] is not None and
                      native[key]["absolute"]["p95"] >= 1. for key in EVENTS)
        abs_median = native["abs_duty"]["absolute"]["median"]
        proceed |= abs_median is not None and abs_median >= 3.
        report["t2_gate"] = {"proceed": proceed, "basis": "NATIVE",
                             "rule": "event absolute p95 >= 1 m OR ABS absolute median >= 3 percentage points"}
        if args.native_events:
            checks = [item for f in report['files'] for row in f['rows']
                      for key, item in row.items() if key in (*EVENTS, 'abs_duty')]
            report['native_verification'] = {'checked': len(checks),
                                             'mismatches': sum(not item['matches'] for item in checks)}
        if args.phases:
            report['t3_gate'] = phase_gate(report['files'])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["file | basis | event | n | signed median | absolute median | absolute p95 | absolute max | unit"]
        for item in report["files"]:
            for event, stats in item["summary"].items():
                s, a = stats["signed"], stats["absolute"]
                def fmt(value):
                    return "—" if value is None else f"{value:.4f}"
                lines.append(f"{item['file']} | {item['sample_basis']} | {event} | {a['n']} | "
                             f"{fmt(s['median'])} | {fmt(a['median'])} | {fmt(a['p95'])} | "
                             f"{fmt(a['max'])} | {'%p' if event == 'abs_duty' else 'm'}")
        lines.append("LMU: RESAMPLED = 재표본 기준. CLIPPED observations are excluded from the crossing gate.")
        lines.append(f"Native verification: {report['native_verification']}" if args.native_events else
                     f"T2 gate: {'PROCEED' if proceed else 'ASK USER'}")
        if args.phases:
            lines.append('T3 file | reference lap | complete n | missing boundary | unavailable | signed median / p95 (s) | absolute median / p95 (s) | changed causes')
            for item in report['files']:
                phases = item['phase_measurement']
                signed, absolute = phases['signed'], phases['absolute']
                lines.append(f"{item['file']} | {phases['reference_lap']} | {signed['n']} | "
                             f"{phases['missing_boundary']} | {phases['unavailable']} | "
                             f"{signed['median']} / {signed['p95']} | {absolute['median']} / {absolute['p95']} | "
                             f"{phases['changed_causes']}")
            lines.append(f"T3 gate: {report['t3_gate']}")
        table = "\n".join(lines) + "\n"
        args.out.with_suffix(".txt").write_text(table, encoding="utf-8")
        print(table)
        print(f"JSON: {args.out}")
        return 1 if (report["errors"] or report.get('native_verification', {}).get('mismatches')
                     or report.get('t3_gate', {}).get('stop')) else 0


if __name__ == "__main__":
    raise SystemExit(main())
