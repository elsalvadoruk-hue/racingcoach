"""Overlay content preferences and observed coaching cards; no SDK or audio writes."""
from collections import deque
from copy import deepcopy
import json
import math
import os
from pathlib import Path
from statistics import median
import tempfile
from overlay_diagnostics import impact_card, REQUIREMENTS
from overlay_automatic import AutomaticDrivingCoach

SESSIONS = ('practice', 'qualify', 'race', 'formation')
MODULES = ('score', 'loss', 'divergence', 'goal', 'change', 'success', 'consistency',
           'pattern', 'retention', 'phases', 'personal', 'common', 'stint', 'impact', 'summary', 'reference', 'live')
LEGACY_PRESETS = {'practice': ['goal', 'success', 'retention', 'change'],
           'qualify': ['score', 'retention', 'personal', 'loss'],
           'race': ['stint', 'consistency', 'retention', 'summary'],
           'formation': ['reference', 'goal', None, None]}
PRESETS = {'practice': ['live', 'score', 'pattern', 'consistency'],
           'qualify': ['live', 'score', 'personal', 'loss'],
           'race': ['live', 'stint', 'consistency', 'impact'],
           'formation': ['reference', 'live', None, None]}
PHASE_ORDER = {
    'before': ('goal', 'stint', 'retention', 'success', 'consistency', 'pattern', 'common', 'impact', 'live', 'reference'),
    'after': ('score', 'divergence', 'phases', 'change', 'personal', 'pattern', 'common', 'impact', 'success', 'retention', 'consistency', 'live'),
    'lap': ('summary', 'loss', 'phases', 'divergence', 'stint', 'pattern', 'common', 'impact', 'retention', 'consistency', 'success', 'live'),
    'formation': ('reference', 'goal', 'live'),
}
PENDING = ()


def selected_modules(config, session):
    selected = (config.get('selected') or {}).get(session)
    if selected is None:
        selected = (config.get('slots') or {}).get(session, PRESETS[session])
    return list(dict.fromkeys(key for key in selected if key in MODULES))


def select_modules(config, session, dashboard, phase=None):
    """Choose only checked items suitable for the current observed situation."""
    phase = ('formation' if session == 'formation' else phase or
             ('lap' if dashboard.get('lap_result') else
              'after' if config.get('timing') == 'pass' and dashboard.get('result') else 'before'))
    selected = selected_modules(config, session)
    order = PHASE_ORDER[phase]
    if phase == 'before' and session == 'race':
        order = ('impact', 'stint', 'consistency', 'pattern', 'retention', 'common', 'goal', 'success', 'live', 'reference')
    eligible = [key for key in order if key in selected]
    items = dashboard.get('items') or {}
    measured = [key for key in eligible if (items.get(key) or {}).get('available') is True]
    ids = measured[:3] if measured else ['live'] if 'live' in eligible else eligible[:1]
    return {'phase': phase, 'ids': ids, 'selected_count': len(selected),
            'waiting': None if measured else 'data' if eligible else 'phase' if any(key not in PENDING for key in selected) else 'pending' if selected else 'empty'}


def defaults():
    return {'slots': deepcopy(PRESETS), 'selected': {s: [key for key in PRESETS[s] if key] for s in SESSIONS},
            'modes': {s: 'coach' for s in SESSIONS},
            'graph': {'throttle': True, 'brake': True, 'steering': False, 'gear': True,
                      'future': True, 'feedback': True, 'window': 10},
            'source': 'auto', 'auto': True, 'quiet': True, 'size': 'normal',
            'detail': 'standard', 'timing': 'pass', 'manual_session': 'practice'}


def normalize(value, game='iracing'):
    result = defaults()
    if not isinstance(value, dict):
        if game == 'lmu':
            result['source'] = 'personal'
        return result
    for name, allowed in (('source', ('auto', 'personal', 'garage61')), ('size', ('small', 'normal', 'large')),
                          ('detail', ('simple', 'standard', 'detailed')), ('timing', ('pass', 'lap'))):
        if value.get(name) in allowed:
            result[name] = value[name]
    for name in ('auto', 'quiet'):
        if type(value.get(name)) is bool:
            result[name] = value[name]
    if value.get('manual_session') in SESSIONS:
        result['manual_session'] = value['manual_session']
    for session in SESSIONS:
        mode = (value.get('modes') or {}).get(session) if isinstance(value.get('modes'), dict) else None
        if mode in ('coach', 'inputs'):
            result['modes'][session] = mode
        slots = (value.get('slots') or {}).get(session) if isinstance(value.get('slots'), dict) else None
        if isinstance(slots, list) and len(slots) == 4 and all(s is None or isinstance(s, str) and s in MODULES for s in slots):
            result['slots'][session] = slots[:]
        selected = (value.get('selected') or {}).get(session) if isinstance(value.get('selected'), dict) else None
        if selected is None:
            result['selected'][session] = list(dict.fromkeys(key for key in result['slots'][session] if key))
        elif (isinstance(selected, list) and len(selected) <= len(MODULES)
              and all(isinstance(key, str) and key in MODULES for key in selected)
              and len(set(selected)) == len(selected)):
            result['selected'][session] = selected[:]
    graph = value.get('graph')
    if isinstance(graph, dict):
        for name in result['graph']:
            if name == 'window':
                if type(graph.get(name)) is int and graph[name] in (6, 10, 16):
                    result['graph'][name] = graph[name]
            elif type(graph.get(name)) is bool:
                result['graph'][name] = graph[name]
    if game == 'lmu':
        result['source'] = 'personal'
    return result


class StudioStore:
    def __init__(self, appearance_path, game='iracing'):
        self.path = Path(appearance_path).with_suffix('.studio.json')
        self.fallback = self.path.with_name('_default.studio.json')
        self.game = game

    def load(self, scope='car'):
        for path in [self.fallback] if scope == 'game' else dict.fromkeys([self.path, self.fallback]):
            try:
                value = json.loads(path.read_text(encoding='utf8'))
                if isinstance(value, dict) and value.get('version') == 1:
                    config = normalize(value.get('config'), self.game)
                    # Upgrade untouched old recommendations only on read.
                    # Custom choices, empty selections and the file are kept.
                    for session in SESSIONS:
                        if config['selected'][session] == [key for key in LEGACY_PRESETS[session] if key]:
                            config['slots'][session] = PRESETS[session][:]
                            config['selected'][session] = [key for key in PRESETS[session] if key]
                    return config
            except (OSError, ValueError):
                continue
        return normalize({}, self.game)

    def save(self, value, scope='car'):
        path = self.fallback if scope == 'game' else self.path
        config = normalize(value, self.game)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent,
                                             prefix='.studio-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': 1, 'config': config}, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return config


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def session_id(view):
    sample = view.get('snapshot') or {}
    if (view.get('session') or {}).get('kind') == 'RACE' and sample.get('session_state') in (1, 2, 3):
        return 'formation'
    kind = str((view.get('session') or {}).get('kind') or '').lower()
    return 'qualify' if 'qual' in kind else 'race' if kind == 'race' else 'practice'


class CoachingDashboard:
    """Bounded observations scoped to a run and the exact reference. Unknown stays unknown."""
    def __init__(self):
        self.automatic = AutomaticDrivingCoach()
        self.reset()

    def reset(self, reset_automatic=True):
        if reset_automatic:
            self.automatic.reset()
        self.scope = None
        self.last_at = None
        self.corners = {}
        self.recent = deque(maxlen=64)

    def build(self, view):
        status, sample = view.get('reference_status') or {}, view.get('snapshot') or {}
        fingerprint = tuple(status.get(k) for k in ('source', 'lap_id', 'filename', 'lap', 'lap_time_s'))
        scope = view.get('run_id'), fingerprint
        at = number(sample.get('session_time'))
        if scope != self.scope or at is not None and self.last_at is not None and not 0 <= at-self.last_at <= 1:
            self.reset(reset_automatic=False)
            self.scope = scope
        self.last_at = at
        passed, feedback, practice = view.get('corner_pass') or {}, view.get('feedback') or {}, view.get('practice') or {}
        end, lap, delta = number(passed.get('exit_at')), number(passed.get('lap')), number(feedback.get('delta_s'))
        valid = (passed.get('clean') is True and at is not None and end is not None and 0 <= at-end <= 30
                 and lap is not None and number(sample.get('lap_number')) in (lap, lap+1)
                 and passed.get('id') == feedback.get('corner_id') and delta is not None)
        identifier = passed.get('id')
        rows = self.corners.get(identifier, [])
        if valid and not any(row['lap'] == lap for row in rows):
            if identifier not in self.corners:
                if len(self.corners) >= 64:
                    self.corners.pop(next(iter(self.corners)))
                self.corners[identifier] = deque(maxlen=10)
            row = {'lap': lap, 'at': end, 'id': identifier, 'delta': delta,
                   'brake': number(feedback.get('brake_delta_m')),
                   'label': str(passed.get('call') or identifier)}
            self.corners[identifier].append(row)
            self.recent.append(row)
            rows = self.corners[identifier]
        names = {'score': '직전 코너 성적표', 'loss': '최대 손실 구간', 'divergence': '처음 달라진 조작',
                 'goal': '다음 코너 연습 목표', 'change': '바꾼 조작과 결과', 'success': '최근 시도 성공률',
                 'consistency': '코너별 일관성', 'pattern': '반복 조작 패턴', 'retention': '개선 유지 여부',
                 'phases': '진입·회전·탈출 손익', 'personal': '내 빠른 기록과 차이', 'common': '빠른 통과의 공통 패턴',
                 'stint': '스틴트 페이스 변화', 'impact': '충돌 전후 관측 변화', 'summary': '이번 주행 개선 요약',
                 'reference': '비교 기준 준비 상태', 'live': '자동 주행 코칭'}
        items = {key: {'id': key, 'name': title, 'title': title+' · 측정 대기', 'metric': '—', 'unit': '',
                       'detail': '관측된 자료가 준비되면 표시합니다.', 'stats': [], 'tone': 'neutral', 'available': False} for key, title in names.items()}

        def set_item(key, title, metric, unit='', detail='', tone='neutral', stats=None):
            items[key].update(title=str(title), metric=str(metric), unit=unit, detail=detail, tone=tone, stats=stats or [], available=True)

        ready = status.get('state') == 'READY'
        duration = number(status.get('lap_time_s'))
        basis = '내 빠른 기록' if status.get('source') == 'PERSONAL' else 'Garage61 기준' if ready else '비교 기준 없음'
        set_item('reference', status.get('message') or '비교 기준 준비 중',
                 f'{int(duration//60)}:{duration%60:06.3f}' if duration and duration > 0 else '—',
                 detail=status.get('condition_note') or '', tone='good' if ready else 'neutral')
        if practice:
            set_item('goal', practice.get('title') or '연습 목표 확인 중', practice.get('target') or '—',
                     detail=practice.get('corner') or '', stats=[['현재', practice.get('after') or '—']])
            states = [s for s in practice.get('states') or [] if type(s) is bool]
            if states:
                set_item('success', '선택한 입력 목표 달성', sum(states), f'/ {len(states)}회',
                         '목표 달성과 페이스 개선은 별도입니다.', 'good')
            if practice.get('phase') == 'RESULT':
                set_item('change', practice.get('label') or '조작 결과', practice.get('after') or '—',
                         detail=f"이전 {practice.get('before') or '—'} · 페이스 확인 전")
        if valid:
            label = passed.get('call') or identifier
            set_item('score', f'{label} · 기준 대비 시간 차이', f'{delta:+.3f}', 's', basis,
                     'good' if delta < 0 else 'warn', [['최저 속도', f"{passed['min_speed_mps']*3.6:.1f} km/h"]] if number(passed.get('min_speed_mps')) is not None else [])
            braking = number(feedback.get('brake_delta_m'))
            if braking is not None and status.get('source') == 'PERSONAL':
                set_item('personal', f'{label} · 내 기준 대비 제동 시작', f'{abs(braking):.0f}',
                         'm 늦음' if braking > 0 else 'm 빠름' if braking < 0 else 'm 차이', status.get('condition_note') or '')
            observed = [r['brake'] for r in rows if r['brake'] is not None][-5:]
            if len(observed) >= 3:
                set_item('consistency', f'{label} · 최근 제동 시작 편차', f'{max(observed)-min(observed):.1f}',
                         'm 범위', f'깨끗한 통과 {len(observed)}회 · 같은 기준')
            deltas = [r['delta'] for r in rows][-5:]
            if len(deltas) >= 3:
                set_item('stint', f'{label} · 최근 3회 시간 차이 중앙값', f'{median(deltas[-3:]):+.3f}', 's',
                         '같은 기준 대비 · 연료/타이어 원인 추정 없음', 'good' if median(deltas[-3:]) < 0 else 'warn')
                if len(deltas) >= 4:
                    previous = median(deltas[:-1])
                    set_item('retention', f'{label} · 이전 통과 중앙값 대비', f'{deltas[-1]-previous:+.3f}', 's',
                             f'이전 {len(deltas)-1}회와 이번 통과 비교', 'good' if deltas[-1] <= previous else 'warn')
        current_lap = number(sample.get('lap_number'))
        recent = [r for r in self.recent if current_lap is not None and 0 <= current_lap-r['lap'] <= 1]
        if recent:
            worst = max(recent, key=lambda r: r['delta'])
            set_item('loss', f"{worst['label']} · 최근 관측 중 최대 시간 차이", f"{worst['delta']:+.3f}", 's',
                     f'이번·직전 랩의 관측 {len(recent)}통과 중 · 전체 코너 측정과 구분', 'warn' if worst['delta'] > 0 else 'good')
            latest = {row['id']: row for row in recent}
            improved = sum(row['delta'] < 0 for row in latest.values())
            set_item('summary', '최근 측정 구간의 기준 대비 개선', improved, f'/ {len(latest)}구간',
                     '측정되지 않은 구간과 원인은 포함하지 않습니다.')
        for key in ('divergence', 'pattern', 'phases', 'common'):
            items[key]['detail'] = REQUIREMENTS[key]
            diagnostic = (view.get('diagnostics') or {}).get(key)
            if diagnostic:
                items[key].update(diagnostic)
        items['impact'].update(impact_card(view))
        items['live'].update(self.automatic.build(view))
        # Explain the missing prerequisite without turning a waiting card into
        # a measurement. Reuse this frame; do not inspect the user's stores.
        waits = {
            'goal': '주행 요약 → 다음 주행에서 할 일 → 이 과제로 연습 시작. 같은 차량·트랙·레이아웃에서 연결됩니다.',
            'success': '연습 목표를 연결한 뒤 목표 구간을 통과하세요. 목표 측정이 가능한 시도부터 집계합니다.',
            'change': '연습 목표를 연결한 뒤 목표 구간을 통과하세요. 측정한 직전 조작 결과를 표시합니다.',
            'score': '비교 기준을 준비한 뒤 코너를 깨끗하게 통과하세요. 통과 후 결과가 표시됩니다.',
            'loss': '기준과 비교한 코너 통과가 필요합니다. 이번·직전 랩에서 관측한 구간만 비교합니다.',
            'consistency': '같은 코너의 제동 시작을 기준과 비교한 깨끗한 통과가 3회 필요합니다.',
            'retention': '같은 코너를 같은 기준으로 깨끗하게 4회 통과하면 이전 통과와 비교합니다.',
            'personal': '비교할 기록을 내 빠른 기록으로 선택하고 제동 시작을 관측한 코너를 통과하세요.',
            'stint': '같은 코너를 같은 기준으로 깨끗하게 3회 통과하면 최근 시간 차이를 표시합니다.',
            'summary': '기준과 비교한 코너 통과 후 랩을 마치세요. 측정한 구간의 개선 수를 표시합니다.',
        }
        for key, reason in waits.items():
            if not items[key]['available']:
                items[key]['detail'] = reason
        if not practice:
            for key in ('goal', 'success', 'change'):
                items[key].update(title=names[key]+' · 목표 연결 필요', metric='목표 연결')
        elif practice.get('phase') == 'UNSUPPORTED':
            for key in ('goal', 'success', 'change'):
                items[key].update(title=names[key]+' · 목표 측정 확인', metric='목표 확인', available=False,
                                  detail='연결된 목표의 측정 기준이 부족합니다. 주행 요약에서 측정 가능한 과제를 다시 선택하세요.')
        compared = ('score', 'loss', 'divergence', 'consistency', 'retention', 'phases', 'personal', 'stint', 'summary')
        for key in (*compared, 'pattern', 'common'):
            if items[key]['available']:
                continue
            if view.get('connected') is not True:
                items[key]['detail'] = '게임 입력 연결을 확인하세요. 입력값 그래프로 내 입력 수신 여부를 확인할 수 있습니다.'
            elif 'corner_zones' in view and not view['corner_zones']:
                items[key]['detail'] = '이 차량·트랙의 주행 파일을 먼저 분석하세요. 저장된 코너 구간으로 다음 통과부터 측정합니다.'
            elif key in compared and not ready:
                items[key]['detail'] = '비교 기준 준비 상태를 함께 선택해 출처를 확인하세요. 같은 차량·레이아웃의 유효한 비교 기록이 필요합니다.'
            elif key == 'personal' and status.get('source') != 'PERSONAL':
                items[key]['detail'] = '오버레이 설정 → 비교할 기록에서 내 빠른 기록을 선택하세요.'
        lap_event = (view.get('session_events') or {}).get('lap') or {}
        lap_at = number(lap_event.get('at')) if isinstance(lap_event, dict) else None
        return {'items': items, 'session': session_id(view), 'connected': view.get('connected') is True,
                'reference': status, 'measured_at': at,
                'quiet': sample.get('car_left_right') not in (None, 0, 1),
                'result': (valid or any(items[key]['available'] for key in ('divergence', 'pattern', 'phases', 'common')))
                          and at is not None and end is not None and 0 <= at-end <= 6,
                'lap_result': at is not None and lap_at is not None and 0 <= at-lap_at <= 6}
