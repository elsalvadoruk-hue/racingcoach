"""Practice loop: one focus at a time, checked on the next run with measurements only.

The closed-loop coach (backend_api.build_closed_loop_coach) already groups corner losses by root cause
and words the next run. This keeps the driver's chosen focus between runs and, when a new file of the
same car, track and layout is analysed, says whether the input the focus asked for actually changed in
those corners and whether the corners got faster. Nothing is guessed: a corner that cannot be matched or
measured is reported as such, and the file the focus was taken from never counts as its own check.

Stored in the data folder as practice-tasks.json (IRACING_PRACTICE_PATH); the driver's own record, never
shipped in a patch.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# How far each root cause's input should move, in the direction the coaching asks (per corner, target lap).
# metric: corner metric key; unit "m" means a distance along the lap (from a lap fraction);
# sign: +1 = should grow, -1 = should shrink; goal: the size of change that counts as done.
CAUSE_TARGETS: dict[str, dict[str, Any]] = {
    "THROTTLE_RETURN_LATE": {"metric": "full_throttle_pct", "unit": "m", "sign": -1, "goal": 3.0, "label": "풀스로틀 지점"},
    "BRAKE_RELEASE_LATE": {"metric": "min_speed_kph", "unit": "km/h", "sign": 1, "goal": 1.5, "label": "최저 속도"},
    "OVER_BRAKING_ABS": {"metric": "abs_duty_pct", "unit": "%p", "sign": -1, "goal": 5.0, "label": "ABS 개입"},
    "EXCESS_COASTING": {"metric": "coast_time_s", "unit": "초", "sign": -1, "goal": 0.04, "label": "코스팅 시간"},
    "BRAKE_POINT_EARLY": {"metric": "brake_start_pct", "unit": "m", "sign": 1, "goal": 3.0, "label": "제동 시작 지점"},
    "EXIT_INSTABILITY": {"metric": "throttle_relifts", "unit": "회", "sign": -1, "goal": 1.0, "label": "스로틀 재리프트"},
    "ROTATION_DEFICIT": {"metric": "avg_abs_steer_deg", "unit": "°", "sign": -1, "goal": 2.0, "label": "평균 조향량"},
}
KEPT_METRICS = sorted({t["metric"] for t in CAUSE_TARGETS.values()} | {"exit_speed_kph", "min_speed_kph", "steering_corrections"})
MATCH_PCT = 0.006          # a corner of the new run is the same corner when its apex is this close (lap fraction)
TIME_GAIN_S = 0.02         # a summed corner-time change smaller than this is "the same"
HISTORY_LIMIT = 200


def identity(session: dict[str, Any]) -> dict[str, str]:
    return {
        "car": str(session.get("car_path") or session.get("car_name") or ""),
        "track": str(session.get("track_platform_id") or session.get("track_name") or ""),
        "config": str(session.get("track_config") or ""),
    }


def _median(values: list[float]) -> Optional[float]:
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def corner_snapshot(result: dict[str, Any], corner_ids: list[str]) -> list[dict[str, Any]]:
    """The focus corners as they were: position, every valid lap's time through them, target-lap inputs."""
    corners = {c.get("id"): c for c in (result.get("performance", {}).get("corners") or [])}
    passes = {c.get("id"): c.get("passes") or [] for c in ((result.get("performance", {}).get("corner_consistency") or {}).get("corners") or [])}
    out = []
    for cid in corner_ids:
        corner = corners.get(cid)
        if not corner:
            continue
        times = [p.get("time_s") for p in passes.get(cid, [])]
        target = corner.get("target") or {}
        out.append({
            "id": cid, "label": corner.get("label"), "apex_pct": corner.get("apex_pct"),
            "entry_pct": corner.get("entry_pct"), "exit_pct": corner.get("exit_pct"),
            "median_s": _median(times), "laps": len([t for t in times if isinstance(t, (int, float))]),
            "metrics": {k: target.get(k) for k in KEPT_METRICS},
        })
    return out


def _match(snapshot: dict[str, Any], corners: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    apex = snapshot.get("apex_pct")
    if not isinstance(apex, (int, float)):
        return None
    best = min(corners, key=lambda c: abs((c.get("apex_pct") or 9) - apex), default=None)
    if best is None or abs((best.get("apex_pct") or 9) - apex) > MATCH_PCT:
        return None
    return best


def check(task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Did the asked input move in the focus corners, and did those corners get faster?"""
    file_name = (result.get("file") or {}).get("name")
    if file_name == task.get("source_file"):
        return {"status": "WAITING", "file": file_name,
                "summary": "이 주행에서 정한 과제입니다. 다음 주행 파일을 분석하면 여기서 결과를 보여 드립니다."}
    target = CAUSE_TARGETS.get(task.get("root_cause") or "")
    track_length = float((result.get("session") or {}).get("track_length_m") or task.get("track_length_m") or 0.0)
    now_corners = corner_snapshot(result, [c.get("id") for c in (result.get("performance", {}).get("corners") or [])])
    rows, gains, input_done, input_seen = [], [], 0, 0
    for base in task.get("corners") or []:
        now = _match(base, now_corners)
        row = {"label": base.get("label"), "matched": now is not None}
        if now is None:
            row["note"] = "이번 주행에서 같은 코너를 찾지 못했습니다."
            rows.append(row)
            continue
        if base.get("median_s") is not None and now.get("median_s") is not None and now.get("laps", 0) >= 1:
            row["time_before_s"], row["time_after_s"] = round(base["median_s"], 3), round(now["median_s"], 3)
            row["gain_s"] = round(base["median_s"] - now["median_s"], 3)
            gains.append(row["gain_s"])
        if target:
            before, after = base["metrics"].get(target["metric"]), now["metrics"].get(target["metric"])
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                change = (after - before) * (track_length if target["unit"] == "m" else 1.0)
                if target["unit"] == "m" and not track_length:
                    change = None
                if change is not None:
                    input_seen += 1
                    moved = change * target["sign"]
                    row["input_change"] = round(change, 3)
                    row["input_done"] = moved >= target["goal"]
                    input_done += int(row["input_done"])
        rows.append(row)
    total_gain = round(sum(gains), 3) if gains else None
    if not gains and not input_seen:
        status = "NO_DATA"
        summary = "과제 코너를 이번 주행에서 측정하지 못했습니다. 유효 랩이 있는 주행으로 다시 확인해 주세요."
    else:
        faster = total_gain is not None and total_gain >= TIME_GAIN_S
        slower = total_gain is not None and total_gain <= -TIME_GAIN_S
        did = input_seen > 0 and input_done * 2 >= input_seen
        if total_gain is None:                       # corner times need three valid laps (corner_consistency)
            status = "INPUT_ONLY" if did else "NOT_YET"
            summary = (("입력은 과제 방향으로 바뀌었습니다. " if did else "아직 입력이 과제 방향으로 바뀌지 않았습니다. ") +
                "코너 시간은 유효 랩이 3개 미만이라 비교하지 못했습니다.")
        elif did and faster:
            status, summary = "ACHIEVED", "입력을 바꿨고 그 코너들이 빨라졌습니다. 과제를 해냈습니다."
        elif did and not faster:
            status, summary = "INPUT_ONLY", ("입력은 바뀌었지만 시간은 줄지 않았습니다. 이 방향이 이 코너에서는 답이 아닐 수 있습니다." if slower
                                             else "입력은 바뀌었지만 시간은 그대로입니다. 한 번 더 해 보면서 다른 입력도 함께 보세요.")
        elif faster:
            status, summary = "FASTER_OTHER", "그 코너들은 빨라졌지만 과제로 정한 입력은 크게 바뀌지 않았습니다. 다른 이유로 빨라졌을 수 있습니다."
        else:
            status, summary = "NOT_YET", "아직 입력이 과제 방향으로 바뀌지 않았습니다. 다음 주행에서 이 코너들만 의식해 보세요."
    return {"status": status, "summary": summary, "file": file_name, "corners": rows,
            "total_gain_s": total_gain, "input_done": input_done, "input_measured": input_seen,
            "target": {k: target[k] for k in ("label", "unit", "goal", "sign")} if target else None,
            "checked_at": time.time()}


class PracticeStore:
    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.data = {"tasks": []}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {"tasks": []}

    def _save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self.path)

    def active(self, ident: dict[str, str]) -> Optional[dict[str, Any]]:
        return next((t for t in reversed(self.data["tasks"]) if t.get("status") == "ACTIVE" and t.get("identity") == ident), None)

    def start(self, result: dict[str, Any], focus: dict[str, Any]) -> dict[str, Any]:
        corner_ids = [c.get("id") for c in (result.get("performance", {}).get("corners") or [])
                      if c.get("label") in (focus.get("affected_corners") or [focus.get("corner")])]
        snapshot = corner_snapshot(result, corner_ids)
        if not snapshot:
            raise ValueError("과제 코너의 측정값이 없어 시작할 수 없습니다.")
        session = result.get("session") or {}
        ident = identity(session)
        task = {
            "id": uuid.uuid4().hex[:12], "status": "ACTIVE", "created_at": time.time(), "identity": ident,
            "car_name": session.get("car_name"), "track_name": session.get("track_name"), "track_config": session.get("track_config"),
            "track_length_m": session.get("track_length_m"), "source_file": (result.get("file") or {}).get("name"),
            "root_cause": focus.get("root_cause"), "title": focus.get("root_cause_label"), "action": focus.get("action"),
            # what the loss was measured against: the Garage61 fast laps or the driver's own reference lap
            "reference_source": focus.get("source"),
            "success_criteria": focus.get("success_criteria") or [], "observed_loss_s": focus.get("total_observed_loss_s") or focus.get("loss_s"),
            "corners": snapshot, "checks": [],
        }
        with self.lock:
            for old in self.data["tasks"]:
                if old.get("status") == "ACTIVE" and old.get("identity") == ident:
                    old["status"], old["closed_at"] = "REPLACED", time.time()
            self.data["tasks"].append(task)
            self.data["tasks"] = self.data["tasks"][-HISTORY_LIMIT:]
            self._save()
        return task

    def record(self, result: dict[str, Any]) -> dict[str, Any]:
        """Check the active focus against this run (kept once per file) and return what the screen shows."""
        session = result.get("session") or {}
        ident = identity(session)
        with self.lock:
            task = self.active(ident)
            latest = None
            if task:
                latest = check(task, result)
                if latest["status"] not in ("WAITING",):
                    task["checks"] = [c for c in task.get("checks", []) if c.get("file") != latest["file"]] + [latest]
                    self._save()
        return {"active": task, "check": latest, "history": self.history(ident)}

    def close(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in ("DONE", "DROPPED"):
            raise ValueError("status must be DONE or DROPPED")
        with self.lock:
            task = next((t for t in self.data["tasks"] if t.get("id") == task_id), None)
            if task is None:
                raise KeyError(task_id)
            task["status"], task["closed_at"] = status, time.time()
            self._save()
        return task

    def history(self, ident: Optional[dict[str, str]] = None) -> dict[str, Any]:
        """What the driver has worked on: per root cause, how often tried, done, and time found."""
        tasks = [t for t in self.data["tasks"] if t.get("status") in ("DONE", "DROPPED", "ACTIVE")]
        causes: dict[str, dict[str, Any]] = {}
        for t in tasks:
            entry = causes.setdefault(t.get("root_cause") or "?", {"root_cause": t.get("root_cause"), "title": t.get("title"),
                                                                     "tried": 0, "done": 0, "gain_s": 0.0})
            entry["tried"] += 1
            entry["done"] += int(t.get("status") == "DONE")
            best = max((c.get("total_gain_s") or 0.0 for c in t.get("checks", [])), default=0.0)
            entry["gain_s"] = round(entry["gain_s"] + max(0.0, best), 3)
        recent = [{k: t.get(k) for k in ("id", "status", "title", "track_name", "track_config", "car_name", "created_at", "closed_at")}
                  | {"last_check": (t.get("checks") or [None])[-1] and (t["checks"][-1] or {}).get("status")}
                  for t in reversed(tasks)][:12]
        return {"by_cause": sorted(causes.values(), key=lambda e: -e["tried"]), "recent": recent}
