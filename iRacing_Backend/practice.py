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
from practice_metrics import TARGETS, KEPT_METRICS, VERSION, number, evaluate, success_criteria

# How far each root cause's input should move, in the direction the coaching asks (per corner, target lap).
# metric: corner metric key; unit "m" means a distance along the lap (from a lap fraction);
# sign: +1 = should grow, -1 = should shrink; goal: the size of change that counts as done.
CAUSE_TARGETS = {k: dict(zip(('metric', 'unit', 'sign', 'goal', 'label'), v)) for k, v in TARGETS.items()}
MATCH_PCT = 0.006          # a corner of the new run is the same corner when its apex is this close (lap fraction)
TIME_GAIN_S = 0.02         # a summed corner-time change smaller than this is "the same"
HISTORY_LIMIT = 200
CONDITIONS = [('fuel_l', '연료량', 3.0), ('track_temp_c', '노면 온도', 3.0),
              ('weather_declared_wet', '건조/젖은 노면', 0), ('tire_compound', '타이어', 0),
              ('weight_penalty_kg', '추가 중량', 0.5), ('power_adjust_pct', '출력 조정', 0.2)]


def _spread(values):
    values = sorted(v for v in values if number(v) is not None)
    if len(values) < 3:
        return None
    def q(p):
        x = (len(values) - 1) * p
        i = int(x)
        return values[i] + (values[min(i + 1, len(values) - 1)] - values[i]) * (x - i)
    return round(q(.75) - q(.25), 4)


def conditions(passes):
    out = {}
    for key, _label, tolerance in CONDITIONS:
        values = [p.get('conditions', {}).get(key) for p in passes]
        if values and all(type(v) in (int, float, bool) and math.isfinite(v) for v in values):
            out[key] = _median(values)
            if tolerance == 0 and len(set(values)) > 1:
                out['unstable'] = True
    return out


def comparison_conditions(before, after, setup_before, setup_after):
    reasons = []
    for key, label, tolerance in CONDITIONS:
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            reasons.append(f'{label}: 정보 없음')
        elif abs(b - a) > tolerance:
            reasons.append(f'{label}: 조건 차이')
    if before.get('unstable') or after.get('unstable'):
        reasons.append('주행 중 노면 또는 타이어 조건이 변했습니다.')
    if not setup_before or not setup_after:
        reasons.append('셋업: 정보 없음')
    elif setup_before != setup_after:
        reasons.append('셋업: 변경됨')
    return {'comparable': not reasons, 'reasons': reasons}


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
    """Keep inputs and time from the same passes; old results remain observations only."""
    corners = {c.get("id"): c for c in (result.get("performance", {}).get("corners") or [])}
    passes = {c.get("id"): c.get("passes") or [] for c in ((result.get("performance", {}).get("corner_consistency") or {}).get("corners") or [])}
    out = []
    for cid in corner_ids:
        corner = corners.get(cid)
        if not corner:
            continue
        observed = [p for p in passes.get(cid, []) if number(p.get('time_s')) is not None]
        times = [p['time_s'] for p in observed]
        target = corner.get("target") or {}
        paired = bool(observed) and all(isinstance(p.get('metrics'), dict) for p in observed)
        metrics = {}
        for key in KEPT_METRICS:
            values = [(p.get('metrics') or {}).get(key) for p in observed]
            metrics[key] = (_median(values) if all(number(v) is not None for v in values) else None) if paired else target.get(key)
        out.append({
            "id": cid, "label": corner.get("label"), "apex_pct": corner.get("apex_pct"),
            "entry_pct": corner.get("entry_pct"), "exit_pct": corner.get("exit_pct"),
            "median_s": _median(times), "laps": len([t for t in times if isinstance(t, (int, float))]),
            "metrics": metrics, 'paired': paired, 'iqr_s': _spread(times),
            'conditions': conditions(observed),
        })
    return out


def _match(snapshot: dict[str, Any], corners: list[dict[str, Any]], length=0) -> Optional[dict[str, Any]]:
    apex = snapshot.get("apex_pct")
    if not isinstance(apex, (int, float)):
        return None
    best = min(corners, key=lambda c: abs((c.get("apex_pct") or 9) - apex), default=None)
    tolerance = min(MATCH_PCT, 20 / length) if length else MATCH_PCT
    if best is None or abs((best.get("apex_pct") or 9) - apex) > tolerance:
        return None
    for key in ('entry_pct', 'exit_pct'):
        if number(snapshot.get(key)) is None or number(best.get(key)) is None or abs(snapshot[key] - best[key]) > 1e-6:
            return None  # time differences require exactly the same section boundaries
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
    reasons, thresholds = [], []
    for base in task.get("corners") or []:
        now = _match(base, now_corners, track_length)
        row = {"label": base.get("label"), "matched": now is not None}
        if now is None:
            row["note"] = "이번 주행에서 같은 코너를 찾지 못했습니다."
            rows.append(row)
            continue
        if base.get("median_s") is not None and now.get("median_s") is not None and min(base.get('laps', 0), now.get('laps', 0)) >= 3:
            row["time_before_s"], row["time_after_s"] = round(base["median_s"], 3), round(now["median_s"], 3)
            row["gain_s"] = round(base["median_s"] - now["median_s"], 3)
            gains.append(row["gain_s"])
        assessment = evaluate(task.get('root_cause'), base.get('metrics') or {}, now.get('metrics') or {}, track_length)
        row.update(assessment, input_done=assessment['input_ok'])
        input_seen += int(assessment['input_ok'] is not None)
        input_done += int(assessment['input_ok'] is True)
        qualified = comparison_conditions(base.get('conditions') or {}, now.get('conditions') or {},
                                          task.get('setup_hash'), (result.get('session') or {}).get('setup_hash'))
        if not base.get('paired') or not now.get('paired'):
            qualified['reasons'].append('입력과 시간을 같은 통과들에서 측정한 자료가 필요합니다.')
        qualified['comparable'] = not qualified['reasons']
        row['conditions'] = qualified
        row['noise_s'] = max(TIME_GAIN_S, base.get('iqr_s') or 0, now.get('iqr_s') or 0)
        thresholds.append(row['noise_s'])
        reasons.extend(qualified['reasons'])
        rows.append(row)
    total_gain = round(sum(gains), 3) if gains else None
    if not gains and not input_seen:
        status = "NO_DATA"
        summary = "과제 코너를 이번 주행에서 측정하지 못했습니다. 유효 랩이 있는 주행으로 다시 확인해 주세요."
    else:
        faster = total_gain is not None and total_gain >= sum(thresholds)
        slower = total_gain is not None and total_gain <= -TIME_GAIN_S
        did = input_seen > 0 and input_done * 2 >= input_seen
        if total_gain is None:                       # corner times need three valid laps (corner_consistency)
            status = "INPUT_ONLY" if did else "NOT_YET"
            summary = (("입력은 과제 방향으로 바뀌었습니다. " if did else "아직 입력이 과제 방향으로 바뀌지 않았습니다. ") +
                "코너 시간은 유효 랩이 3개 미만이라 비교하지 못했습니다.")
        elif did and faster and not reasons and len(gains) == len(rows) and all(r.get('goal_ok') is True for r in rows):
            status, summary = "ACHIEVED", "같은 조건의 반복 주행에서 입력과 부가 조건을 지켰고 평소 편차보다 빨라졌습니다."
        elif did and faster:
            status, summary = 'UNVERIFIED', '입력과 시간의 개선은 관측됐지만 조건 차이·미측정 또는 부가 조건 때문에 효과를 확정하지 않았습니다.'
        elif did and not faster:
            status, summary = "INPUT_ONLY", ("입력은 바뀌었지만 시간은 줄지 않았습니다. 이 방향이 이 코너에서는 답이 아닐 수 있습니다." if slower
                                             else "입력은 바뀌었지만 시간 차이가 평소 편차보다 작습니다. 반복 주행으로 다시 확인해 주세요.")
        elif faster:
            status, summary = "FASTER_OTHER", "그 코너들은 빨라졌지만 과제로 정한 입력은 크게 바뀌지 않았습니다. 다른 이유로 빨라졌을 수 있습니다."
        else:
            status, summary = "NOT_YET", "아직 입력이 과제 방향으로 바뀌지 않았습니다. 다음 주행에서 이 코너들만 의식해 보세요."
    return {"status": status, "summary": summary, "file": file_name, "corners": rows,
            "total_gain_s": total_gain, "input_done": input_done, "input_measured": input_seen,
            "target": {k: target[k] for k in ("label", "unit", "goal", "sign")} if target else None,
            'verification_version': VERSION, 'reasons': list(dict.fromkeys(reasons)),
            'noise_s': round(sum(thresholds), 3), "checked_at": time.time()}


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
            'setup_hash': session.get('setup_hash'), 'verification_version': VERSION,
            "success_criteria": success_criteria(focus.get('root_cause')) or focus.get("success_criteria") or [], "observed_loss_s": focus.get("total_observed_loss_s") or focus.get("loss_s"),
            "corners": snapshot, "checks": [],
        }
        from practice_live import bundle
        task['live_bundle'] = bundle(task, result)
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
        followups = []
        with self.lock:
            task = self.active(ident)
            # Reanalysing the original file upgrades legacy observations without inventing missing data.
            for old in self.data['tasks']:
                if old.get('identity') != ident or old.get('source_file') != (result.get('file') or {}).get('name'):
                    continue
                snapshots = corner_snapshot(result, [c['id'] for c in (result.get('performance') or {}).get('corners', [])])
                upgraded = [_match(base, snapshots, old.get('track_length_m') or 0) for base in old.get('corners', [])]
                needs_upgrade = any(not c.get('paired') or 'conditions' not in c for c in old.get('corners', [])) or 'setup_hash' not in old
                if needs_upgrade and upgraded and all(c and c.get('paired') for c in upgraded):
                    old['corners'] = [dict(c, label=base['label']) for c, base in zip(upgraded, old['corners'])]
                    old['setup_hash'] = (result.get('session') or {}).get('setup_hash')
                    old['verification_version'] = VERSION
                    old['success_criteria'] = success_criteria(old.get('root_cause')) or old.get('success_criteria', [])
                    from practice_live import bundle
                    # A later reference selection must not change the task's pinned curve.
                    old.setdefault('live_bundle', bundle(old, result))
                    self._save()
                if not old.get('live_bundle'):
                    from practice_live import bundle
                    old['live_bundle'] = bundle(old, result)
                    self._save()
                else:
                    pinned = old['live_bundle'].get('reference') or {}
                    incomplete = not pinned.get('files') if pinned.get('source') == 'GARAGE61' else pinned.get('lap') is None
                    if incomplete:
                        from practice_live import bundle
                        repaired = bundle(old, result)
                        reference = repaired['reference']
                        complete = bool(reference.get('files')) if reference.get('source') == 'GARAGE61' else reference.get('lap') is not None
                        if complete:
                            old['live_bundle'] = repaired
                            self._save()
            latest = None
            if task:
                latest = check(task, result)
                if latest["status"] not in ("WAITING",):
                    task["checks"] = [c for c in task.get("checks", []) if c.get("file") != latest["file"]] + [latest]
                    self._save()
            # A manual finish closes the drill, not the opportunity to verify it on a later normal run.
            recorded_at = number((result.get('file') or {}).get('modified_at'))
            completed = [t for t in self.data['tasks'] if t.get('identity') == ident and t.get('status') == 'DONE']
            for old in completed[-1:]:
                if recorded_at is None or recorded_at <= old.get('closed_at', math.inf):
                    continue
                outcome = check(old, result)
                if outcome.get('total_gain_s') is not None:
                    old['checks'] = [c for c in old.get('checks', []) if c.get('file') != outcome['file']] + [outcome]
                    followups.append({'task_id': old['id'], 'title': old.get('title'), 'check': outcome})
                    self._save()
        return {"active": task, "check": latest, 'followups': followups, "history": self.history(ident)}

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
        tasks = [t for t in self.data["tasks"] if t.get("status") in ("DONE", "DROPPED", "ACTIVE") and (ident is None or t.get('identity') == ident)]
        causes: dict[str, dict[str, Any]] = {}
        for t in tasks:
            entry = causes.setdefault(t.get("root_cause") or "?", {"root_cause": t.get("root_cause"), "title": t.get("title"),
                                                                      "tried": 0, "done": 0, 'verified': 0, "gain_s": 0.0})
            entry["tried"] += 1
            entry["done"] += int(t.get("status") == "DONE")
            verified = [c for c in t.get('checks', []) if c.get('status') == 'ACHIEVED' and c.get('verification_version') == VERSION]
            entry['verified'] += int(bool(verified))
            best = max((c.get('total_gain_s') or 0.0 for c in verified), default=0.0) if t.get('status') != 'DROPPED' else 0
            entry["gain_s"] = round(entry["gain_s"] + max(0.0, best), 3)
        recent = [{k: t.get(k) for k in ("id", "status", "title", "track_name", "track_config", "car_name", "created_at", "closed_at")}
                  | {"last_check": (t.get("checks") or [None])[-1] and (t["checks"][-1] or {}).get("status")}
                  for t in reversed(tasks)][:12]
        return {"by_cause": sorted(causes.values(), key=lambda e: -e["tried"]), "recent": recent}
