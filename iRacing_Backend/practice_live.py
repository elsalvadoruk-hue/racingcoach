"""Measured practice state shared by ordinary laps, corner drills and the HUD.

Configuration is copied in by API/storage operations. Sampling never opens a
file or calls a service. The same corner measurements and goal rules are used
for both ordinary passes and Active Reset drills.
"""
from copy import deepcopy
import math

from live_drill import CornerDrill, drill_plan
from practice_metrics import TARGETS, number


def bundle(task, result=None):
    result = result or {}
    external = result.get('external_reference') or {}
    garage = str(task.get('reference_source') or '').startswith('GARAGE61')
    reference_available = not garage or (external.get('status') == 'READY' and bool(external.get('corners')))
    corners = (external.get('corners') if garage and reference_available else
               (result.get('performance') or {}).get('corners') if not garage else []) or []
    if not corners:
        corners = [{**c, 'target': c.get('metrics') or {}} for c in task.get('corners') or []]
    length = task.get('track_length_m') or (result.get('visualizations') or {}).get('track_length_m')
    plans = []
    for corner in task.get('corners') or []:
        try:
            plan = drill_plan(corners, corner['label'], task.get('root_cause'), corner.get('metrics') or {}, length)
        except (ValueError, TypeError, KeyError):
            continue
        plan.update(task_id=task['id'], identity=task['identity'], corner_id=corner.get('id'),
                    title=task.get('title'), mode='laps', continue_after_goal=True,
                    reference_available=reference_available,
                    grid_points=(result.get('visualizations') or {}).get('analysis_grid_points'))
        plans.append(plan)
    comparison = result.get('comparison') or {}
    aggregate = external.get('aggregate') or {}
    return {'version': 1, 'plans': plans, 'reference': {
        'source': 'GARAGE61' if garage else 'PERSONAL', 'file': task.get('source_file'),
        'target_lap': comparison.get('target_lap'), 'lap': comparison.get('reference_lap'),
        'files': external.get('selected_files') or [],
        'label': (aggregate.get('reference_label') or 'Garage61 과제 기준') if garage else (
            f"내 기준 랩 {comparison['reference_lap']}" if comparison.get('reference_lap') is not None else '내 기준 랩'),
        'identity': task['identity'], 'task_id': task['id']}}


class LapPractice(CornerDrill):
    """Continuous, silent ordinary passes; never stop measuring at the drill milestone."""
    def _settled(self, length):
        return False

    def _comparable(self, length):
        return [a for a in self.attempts if a.get('series') == self.series]

    def ingest(self, sample, length):
        # Split evidence when tyre/surface/control settings or departure conditions change.
        if self.plan and self.current is None:
            values = {k: sample.get(k) for k in ('fuel_l', 'track_temp_c', 'tire_compound',
                                                'track_wetness', 'brake_bias', 'abs_setting', 'tc_setting')}
            if self.saved_conditions is None or any(
                a != b and (number(a) is None or number(b) is None or abs(a-b) >
                            (3 if key in ('fuel_l', 'track_temp_c') else 0))
                for key, a in values.items() for b in [self.saved_conditions.get(key)]):
                self.series += 1
                self.saved_conditions = values
        super().ingest(sample, length)
        # A normal lap is not an Active Reset exercise and produces no cue sounds.
        self.events.clear()


def metric_text(value, unit, limit=''):
    if number(value) is None:
        return '—'
    digits = 2 if unit == '초' else 1 if unit == '°' else 0
    rounded = round(value, digits)
    return f'{limit}{0 if rounded == 0 else rounded:.{digits}f}{unit}'


def presentation(plan, state, run_id, now, result_until=None):
    target = TARGETS.get(plan.get('cause'))
    if not target:
        return None
    metric, unit, sign, step, label = target
    length = plan['track_length_m']
    baseline = (plan.get('base_metrics') or {}).get(metric)
    rows = [row for row in state.get('attempts') or [] if row.get('comparable') is not False]
    last = (state.get('attempts') or [None])[-1]
    after = ((last or {}).get('metrics') or {}).get(metric)
    if unit == 'm':
        convert = lambda value: ((value-plan['start_pct']) % 1)*length if number(value) is not None else None
        before_value, after_value = convert(baseline), convert(after)
    else:
        before_value, after_value = baseline, after
    goal_value = before_value+sign*step if number(before_value) is not None else None
    issue = 'BASELINE_MISSING' if goal_value is None else 'UNREACHABLE' if unit != 'm' and goal_value < -1e-8 else None
    possible = issue is None
    display_unit = '%' if unit == '%p' else unit
    title = (f'{label} {metric_text(goal_value, display_unit)} {"이하" if sign < 0 else "이상"}'
             if unit not in ('m',) else f'{label} {step:g}m {"앞당기기" if sign < 0 else "늦추기"}')
    if not possible:
        title = f'{label} · '+('기준 측정 확인 필요' if issue == 'BASELINE_MISSING' else '목표 재선택 필요')
    states = [row.get('goal_ok') for row in rows[-5:]]
    count = len(states)
    states = [None]*(5-count)+states
    phase = state.get('phase') or 'READY'
    if phase != 'WARMUP':
        phase = 'RESULT' if last and (result_until is None or now <= result_until) else 'DURING' if state.get('in_section') else 'BEFORE'
    return {'task_id': plan.get('task_id'), 'corner': plan.get('label'), 'cause': plan.get('cause'),
            'title': title, 'label': label, 'unit': display_unit, 'phase': phase,
            'before': metric_text(before_value, display_unit), 'after': metric_text(after_value, display_unit),
            'target': metric_text(goal_value, display_unit, '≤' if sign < 0 else '≥') if possible else '—',
            'goal_ok': (last or {}).get('goal_ok'), 'goal_possible': possible, 'goal_issue': issue,
            'criteria': (last or {}).get('criteria') or [],
            'states': states, 'count': count, 'recent_ok': sum(s is True for s in states),
            'warm_laps': state.get('warm_laps'), 'warmup_laps': state.get('warmup_laps'),
            'pace_verified': False, 'result_id': (last or {}).get('n'),
            'scope': f"{run_id}:{plan.get('task_id')}:{plan.get('label')}:{state.get('series', 0)}",
            'five_successes': count == 5 and all(s is True for s in states)}


class LivePractice:
    def __init__(self):
        self.tasks = []
        self.task = None
        self.trackers = []
        self.history = None
        self.last_result = None
        self.last_at = None
        self.run_id = None
        self.celebrations = {}

    def configure(self, tasks, history):
        self.tasks, self.history = deepcopy(tasks), history
        current = next((t for t in self.tasks if self.task and t['id'] == self.task['id']), None)
        if self.task and (not current or current.get('live_bundle') != self.task.get('live_bundle')):
            self.reset('TASK_CHANGED')

    def reset(self, reason='SESSION_CHANGED'):
        for tracker in self.trackers:
            tracker.stop(reason)
        self.task, self.trackers, self.last_result, self.last_at = None, [], None, None
        self.celebrations.clear()

    def pause(self):
        for tracker in self.trackers:
            tracker.current = tracker.prev = None

    def select(self, identity, run_id):
        actual = {'car': str(identity.get('car_path') or identity.get('car_name') or ''),
                  'track': str(identity.get('track_id') or identity.get('track_name') or ''),
                  'config': str(identity.get('track_config') or '')}
        task = next((t for t in reversed(self.tasks) if t.get('status') == 'ACTIVE' and t.get('identity') == actual), None)
        if run_id != self.run_id or (task or {}).get('id') != (self.task or {}).get('id'):
            self.reset()
            self.run_id, self.task = run_id, task
            if task:
                for plan in (task.get('live_bundle') or bundle(task)).get('plans', []):
                    tracker = LapPractice()
                    tracker.history = self.history or tracker.history
                    tracker.start(plan, warmup=False)
                    self.trackers.append(tracker)

    def ingest(self, identity, sample, run_id, drill):
        self.select(identity, run_id)
        if drill.plan:
            self.pause()
            return
        for tracker in self.trackers:
            count = len(tracker.attempts)
            tracker.ingest(sample, tracker.plan['track_length_m'])
            if len(tracker.attempts) > count:
                self.last_result, self.last_at = tracker, sample.get('session_time')

    def view(self, sample, drill, run_id):
        if not self.task:
            return None
        now = sample.get('session_time') or 0
        if drill.plan and drill.plan.get('task_id') == self.task['id']:
            state, plan = drill.view(), drill.plan
            state['in_section'] = drill.current is not None
            state['series'] = drill.series
            last = (state.get('attempts') or [{}])[-1]
            result_until = (last.get('session_end_s') or -math.inf)+4
        else:
            active = next((t for t in self.trackers if t.current), None)
            recent = self.last_result if self.last_at is not None and 0 <= now-self.last_at <= 4 else None
            pct = sample.get('distance_pct')
            next_tracker = min(self.trackers, key=lambda t: (t.plan['start_pct']-(pct or 0)) % 1, default=None)
            tracker = recent or active or next_tracker
            if not tracker:
                return None
            plan = tracker.plan
            state = tracker.view()
            state.update(in_section=tracker.current is not None, phase='READY', series=tracker.series)
            result_until = (self.last_at or -math.inf)+4 if recent else -math.inf
        output = presentation(plan, state, run_id, now, result_until)
        if output:
            output['reference'] = deepcopy((self.task.get('live_bundle') or bundle(self.task)).get('reference'))
            scope = output['scope']
            previous, serial = self.celebrations.get(scope, (False, 0))
            if output['five_successes'] and not previous:
                serial += 1
            self.celebrations[scope] = output['five_successes'], serial
            output['celebration_id'] = f'{scope}:{serial}' if output['five_successes'] and serial else None
        return output or {'task_id': self.task['id'], 'title': self.task.get('title') or '목표 측정 확인 필요',
                          'corner': plan.get('label'), 'cause': plan.get('cause'), 'phase': 'UNSUPPORTED',
                          'states': [None]*5, 'recent_ok': 0, 'count': 0,
                          'scope': f'{run_id}:{self.task["id"]}:unsupported',
                          'reference': deepcopy((self.task.get('live_bundle') or bundle(self.task)).get('reference'))}
