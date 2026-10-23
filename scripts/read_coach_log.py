"""Read a session log someone sent (the zip made by "로그 파일 받기" on the settings tab).

Prints what the session was, how each kind of call fared, and with --timeline every
call in order with its reason, the way the improvements so far were found.

    python scripts/read_coach_log.py coach-log-lemans-full-20261012-2005.zip
    python scripts/read_coach_log.py that.zip --timeline
    python scripts/read_coach_log.py that.zip --timeline --all      # with the side-by-side calls too
    python scripts/read_coach_log.py that.zip --reports             # engineer reports and the fuel numbers behind them
    python scripts/read_coach_log.py that.zip --extract out-folder  # own drive summary and race logs as plain JSON
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "iRacing_Backend"))
import session_log  # noqa: E402

SIDE_BY_SIDE = {"left", "right", "both", "two_left", "two_right", "left_only", "right_only", "left_one", "right_one", "clear_left", "clear_right",
                "clear_all", "hold", "hold_left", "hold_right", "hold_both", "hold_two_left", "hold_two_right"}
PIT_WALL = ("class_position", "fuel_l", "fuel_l_per_lap", "fuel_laps", "remaining_laps", "finish_laps_to_go", "finish_total_laps", "tank_capacity_l",
            "stops_needed", "fuel_next_stop_l", "fuel_next_stop_full", "fuel_run_dry", "tyre_decision", "pit_loss_auto_s", "time_remaining_s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--timeline", action="store_true")
    parser.add_argument("--all", action="store_true", help="좌우 호출도 타임라인에 포함")
    parser.add_argument("--reports", action="store_true")
    parser.add_argument("--extract", type=Path, help="측정값 요약·주변 차 기록을 풀어 JSON으로 저장할 폴더")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        manifest, radio, reports = session_log.read(args.file.read_bytes())
    except (OSError, ValueError) as exc:
        print(exc)
        return 1
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(manifest["started_at"]))
    print(f"{manifest.get('track')} · {manifest.get('car')} · {manifest.get('session_type')} · {started} · {manifest['minutes']}분 · 앱 {manifest.get('app_version')}")
    events = {event["event_id"] for event in radio}
    done = {event["event_id"] for event in radio if event.get("status") == "completed"}
    print(f"안내 {len(events)}건 가운데 끝까지 들린 것 {len(done)}건 · 보고 {manifest['reports']}건 · 질문 {manifest['questions_in_session']}건"
          f"{'' if manifest['questions_included'] else ' (내용 미포함)'}\n")
    for group, value in (manifest.get("extras") or {}).items():
        state = "포함" if value.get("included") else f"빠짐({value.get('reason')})"
        print(f"추가 자료 {group}: {state} · {value.get('bytes', 0):,} B")
    if manifest.get("extras"):
        print()
    for key, counts in sorted(manifest["calls"].items(), key=lambda item: -sum(item[1].values())):
        print(f"{key:<26}{'  '.join(f'{name} {count}' for name, count in sorted(counts.items(), key=lambda item: -item[1]))}")
    if args.extract:
        import json
        args.extract.mkdir(parents=True, exist_ok=True)
        for name, part in session_log.read_extras(args.file.read_bytes()).items():
            if isinstance(part, tuple):
                part = {"header": part[0], "rows": part[1]}
            target = args.extract / f"{name}.json"
            target.write_text(json.dumps(part, ensure_ascii=False), encoding="utf-8")
            print(f"풀어 저장: {target}")
    if args.timeline:
        print()
        for event in radio:
            if event.get("key") in SIDE_BY_SIDE and not args.all:
                continue
            at, pct = event.get("session_time"), event.get("distance_pct")
            print(f"{'' if at is None else format(at, '7.1f'):>7} L{event.get('lap_number')!s:<3} {'' if pct is None else format(pct, '.3f'):>5} "
                  f"{event.get('status', ''):<11} {(event.get('reason') or ''):<26} {event.get('key', ''):<24} {event.get('text') or ''}")
    if args.reports:
        print()
        for report in reports:
            context = report.get("context") or {}
            snapshot, wall = context.get("snapshot") or {}, context.get("pit_wall") or {}
            print(f"{snapshot.get('session_time') or 0:7.1f} L{snapshot.get('lap_number')} {report['source']:<11} "
                  f"{(report.get('text') + ' → ') if report.get('text') else ''}{report.get('reply') or ''}")
            shown = {name: round(wall[name], 2) if isinstance(wall.get(name), float) else wall[name] for name in PIT_WALL if wall.get(name) is not None}
            if shown:
                print(f"        {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
