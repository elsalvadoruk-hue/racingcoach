"""Fictional telemetry for the offline overlay, using the app's real goal rules.

The waveforms are example recorded inputs, not an ideal pedal curve inferred from
a scalar goal. Importing the pure rules module never opens the backend or stores.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path


_rule_path = Path(__file__).resolve().parents[1] / "iRacing_Backend" / "practice_metrics.py"
_spec = importlib.util.spec_from_file_location("_overlay_practice_rules", _rule_path)
_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rules)
LENGTH_M = 300.0


@dataclass(frozen=True)
class DemoGoal:
    key: str
    cause: str
    title: str
    label: str
    before: float
    after: float
    unit: str
    guards: str
    start_m: float

    @property
    def rule(self):
        return _rules.TARGETS[self.cause]

    @property
    def target(self):
        return round(self.before + self.rule[2] * self.rule[3], 6)

    def value_text(self, value, target=False):
        if value is None:
            return "—"
        precision = 2 if self.unit == "초" else 0
        number = f"{value:.{precision}f}" if precision else f"{value:g}"
        limit = ("≤" if self.rule[2] < 0 else "≥") if target else ""
        return f"{limit}{number}{self.unit}"

    def verdict(self, measured=True):
        before = {
            "min_speed_kph": 80.0, "exit_speed_kph": 100.0,
            "avg_abs_steer_deg": 18.0, "abs_duty_pct": 28.0,
            "braking_distance_m": 60.0, "throttle_relifts": 2,
            "steering_corrections": 3, "steering_efficiency": 0.45,
        }
        after = {
            "min_speed_kph": 82.0, "exit_speed_kph": 102.0,
            "avg_abs_steer_deg": 15.0, "abs_duty_pct": 18.0,
            "braking_distance_m": 58.0, "throttle_relifts": 1,
            "steering_corrections": 2, "steering_efficiency": 0.49,
        }
        factor = LENGTH_M if self.unit == "m" else 1
        before[self.rule[0]] = self.before / factor
        after[self.rule[0]] = self.after / factor if measured else None
        return _rules.evaluate(self.cause, before, after, LENGTH_M)


GOALS = (
    DemoGoal("abs", "OVER_BRAKING_ABS", "ABS 개입 23% 이하", "ABS 개입",
             28, 18, "%", "최저 속도·제동 거리 유지", 85),
    DemoGoal("throttle", "THROTTLE_RETURN_LATE", "풀스로틀 3m 앞당기기", "풀스로틀 위치",
             195, 189, "m", "탈출 속도 +2km/h · 재리프트 증가 없음", 170),
    DemoGoal("release", "BRAKE_RELEASE_LATE", "브레이크 해제 3m 앞당기기", "브레이크 해제 위치",
             120, 114, "m", "최저 속도 +2km/h · 조향·ABS 증가 없음", 105),
    DemoGoal("coast", "EXCESS_COASTING", "코스팅 0.04초 줄이기", "코스팅 시간",
             0.16, 0.10, "초", "최저 속도 +2km/h · 조향 수정 감소", 118),
    DemoGoal("brake", "BRAKE_POINT_EARLY", "제동 시작 3m 늦추기", "제동 시작 위치",
             60, 65, "m", "최저 속도 +2km/h · ABS 증가 없음", 63),
    DemoGoal("relift", "EXIT_INSTABILITY", "스로틀 재리프트 1회 줄이기", "스로틀 재리프트",
             2, 1, "회", "조향 수정 감소 · 탈출 속도 유지", 218),
    DemoGoal("steering", "ROTATION_DEFICIT", "평균 조향 2° 줄이기", "평균 조향량",
             18, 15, "°", "최저 속도 유지 · 회전 효율 증가", 102),
)
GOAL_BY_KEY = {goal.key: goal for goal in GOALS}


def interpolate(points, position):
    for (left, a), (right, b) in zip(points, points[1:]):
        if position <= right:
            fraction = max(0, min(1, (position-left)/(right-left)))
            return a + fraction*(b-a)
    return points[-1][1]


def inputs(goal: DemoGoal, distance_m: float, reference=False):
    """Return pedal fractions, steering degrees and an example ABS activity flag."""
    position = distance_m % LENGTH_M
    brake_start, release, full_throttle = 60.0, 120.0, 195.0
    throttle_start, peak = 135.0, 0.88
    if not reference:
        peak = 0.76 if goal.key == "abs" else 0.84
        if goal.key == "throttle":
            full_throttle = goal.after
        elif goal.key == "release":
            release = goal.after
        elif goal.key == "brake":
            brake_start = goal.after
    if goal.key == "coast":
        throttle_start = release + (goal.before if reference else goal.after)*25
    brake = interpolate(((0, 0), (brake_start, 0), (brake_start+7, peak),
                         (brake_start+15, peak*0.95), (release-14, peak*0.25),
                         (release, 0), (300, 0)), position)
    throttle = interpolate(((0, 1), (brake_start-5, 1), (brake_start, 0),
                            (throttle_start, 0), (full_throttle, 1), (300, 1)), position)
    if goal.key == "relift":
        throttle *= 1 - 0.5 * max(0, 1-abs(position-218)/7)
        if reference:
            throttle *= 1 - 0.4 * max(0, 1-abs(position-245)/7)
    steering = interpolate(((0, 0), (75, 0), (105, 24), (135, 28), (205, 0), (300, 0)), position)
    if goal.key == "steering" and not reference:
        steering *= goal.after/goal.before
    duty = 0.28 if reference else 0.18
    phase = (position-brake_start) / (release-brake_start)
    abs_active = 0.12 <= phase < 0.12 + duty
    return {"brake": brake, "throttle": throttle, "steering": steering, "abs": abs_active}


def help_text(goal):
    if goal.key == "abs":
        return "ABS 수치: 제동 중 개입 비율 · 그래프: 페달 입력"
    if goal.unit == "m":
        return "위치 수치는 연습 구간 시작점으로부터의 거리입니다."
    if goal.key == "steering":
        return "조향은 파란 선(0~30°) · 제동·가속은 0~100%입니다."
    return "실선은 이번 입력 · 옅은 면은 내 기준 랩의 입력입니다."
