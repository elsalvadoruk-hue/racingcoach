"""Compact coaching layout using parent observations, or explicitly marked demo values."""
import json
from math import radians
from overlay_studio import select_modules
if __package__:
    from .overlay_demo_data import inputs
    from .overlay_theme import COLORS
else:
    from overlay_demo_data import inputs
    from overlay_theme import COLORS


def content(hud):
    if hud.display_mode == 'live':
        return hud.live_view.get('studio') or {}, hud.live_view.get('studio_session', 'practice')
    config = getattr(hud, 'studio_config', {})
    return config if isinstance(config, dict) else {}, getattr(hud, 'studio_session', 'practice')


def coaching(hud):
    config, session = content(hud)
    return (config.get('modes') or {}).get(session) == 'coach'


def draw_coaching(hud):
    config, session = content(hud)
    live = hud.display_mode == 'live'
    dashboard = hud.live_view.get('dashboard') or {} if live else {}
    items = dashboard.get('items') or {}
    if not live:
        examples = {
            'live': ('직전 제동 해제 → 가속 연결', '0.42 s', '과제 없이 실제 입력에서 자동 관측 · 샘플'),
            'goal': ('브레이크 해제에 집중', '5m 앞에서', '선택한 과제의 목표 · 샘플'),
            'success': ('최근 입력 목표 달성', '3 / 5회', '페이스 개선은 별도 확인 · 샘플'),
            'score': ('C4 · 기준 대비 시간 차이', '−0.120 s', '깨끗한 통과 · 샘플'),
            'retention': ('C4 · 이전 통과 대비', '−0.030 s', '같은 기준의 관측 · 샘플'),
            'personal': ('C4 · 내 기준 대비 제동 시작', '8m 늦음', '비슷한 조건의 개인 기록 · 샘플'),
            'reference': ('비슷한 조건의 내 기록 준비됨', '1:37.814', '같은 차량·레이아웃 · 샘플'),
            'consistency': ('C4 · 최근 제동 위치 편차', '7m 범위', '깨끗한 통과 5회 · 샘플'),
            'stint': ('C4 · 최근 3회 시간 차이 중앙값', '+0.240 s', '같은 기준 대비 · 샘플'),
            'summary': ('최근 측정 구간의 개선', '2 / 4구간', '관측 예시 · 샘플'),
            'change': ('브레이크 해제 결과', '5m 앞당김', '페이스 확인 전 · 샘플'),
            'loss': ('C8 · 최근 최대 시간 차이', '+0.190 s', '이번·직전 랩 관측 중 · 샘플'),
            'divergence': ('C4 · 해제부터 차이 관측', '8.0m 늦음', '비교한 조작 중 첫 차이 · 샘플'),
            'pattern': ('C4 · 재리프트 / 재제동', '3 / 1회', '최근 깨끗한 5통과 · 샘플'),
            'phases': ('C4 · 진입 / 회전 / 탈출', '+0.180 s', '−0.050 / +0.030 / +0.200s · 샘플'),
            'common': ('C4 · 빠른 3회에서 관측', '3 / 5통과', '재제동 없음 · 인과관계 아님 · 샘플'),
            'impact': ('접촉·손상 신호 뒤 관측 변화', '−4.0 km/h', '이전 2랩 / 이후 1랩 · 샘플'),
        }
        items = {key: {'title': title, 'metric': metric, 'detail': detail, 'tone': 'good'}
                 for key, (title, metric, detail) in examples.items()}
    if not live:
        dashboard = {'items': {key: {**item, 'available': True} for key, item in items.items()},
                     'result': hud.scene.get() == 'result'}
    presentation = dashboard.get('presentation') if live else None
    if presentation:
        items = presentation.get('items') or {}
    layout = presentation['layout'] if presentation else select_modules(config, session, dashboard)
    slots = layout['ids']
    selected = slots[0] if slots else None
    quiet = live and session == 'race' and config.get('quiet') and dashboard.get('quiet')
    card = hud.live_view.get('card') if live else None
    ticket = (session, selected, quiet, config.get('size'), config.get('detail'),
              json.dumps([items.get(key) for key in [selected, *slots[1:3]]], sort_keys=True),
              json.dumps(card, sort_keys=True), json.dumps(presentation.get('reference') if presentation else None, sort_keys=True),
              (presentation or {}).get('state'), (presentation or {}).get('group_count'), hud.scale_x, hud.scale_y)
    if getattr(hud, 'coaching_ticket', None) == ticket:
        return
    hud.coaching_ticket = ticket
    hud.canvas.delete('coach')
    hud.background_canvas.delete('coach')
    tags = ('coach',)
    factor = {'small': .9, 'large': 1.1}.get(config.get('size'), 1.)
    large = factor > 1

    def text(x, y, value, width, size=11, color='text', bold=False, numeric=False):
        # Base glyphs on available height, even in a wide, shallow window.
        # Fit numeric values by font size before considering ellipsis.
        pixels = max(8, round(size*factor*hud.scale_y))
        value = str(value or '—')
        font = hud.font_at_pixels(pixels, bold)
        if numeric:
            minimum = max(8, round(size*.65*min(hud.scale_x, hud.scale_y)))
            while pixels > minimum and font.measure(value) > hud.x(width)-4:
                pixels -= 1
                font = hud.font_at_pixels(pixels, bold)
        while value and font.measure(value) > hud.x(width)-4:
            value = value[:-2]+'…' if len(value) > 2 else ''
        hud.canvas.create_text(hud.x(x), hud.y(y), text=value, font=font,
                               fill=COLORS.get(color, color), anchor='nw', tags=tags)

    labels = {'practice': '연습', 'qualify': '퀄리', 'race': '레이스', 'formation': '포메이션'}
    group = (presentation or {}).get('group_count', 0)
    context = f'직선 요약 · {group}코너 중 선택' if group > 1 else '직선에서 코칭' if live else '가상 데이터 · 샘플'
    text(8, 3, labels.get(session, '주행')+' · '+context, 374, 11, 'accent', True)
    waiting = {'empty': '표시할 항목을 선택하세요', 'pending': '선택한 기능은 준비 중입니다',
               'phase': '선택한 항목의 표시 시점을 기다리는 중',
               'straight': '코너 결과는 다음 직선에서'}
    item = items.get(selected) or {'title': waiting.get(layout['waiting'], '관측 자료 준비 중'), 'metric': '—',
                                  'detail': '직선에서 핵심 결과를 6초간 고정합니다.' if presentation else '오버레이 설정에서 여러 항목을 선택할 수 있습니다.'}
    if presentation and presentation.get('state') == 'busy':
        item = {'title': '주행에 집중', 'metric': '다음 직선에서', 'detail': '코너 결과를 모아 핵심 내용을 보여줍니다.'}
    if quiet:
        item = {'title': '근접 상황 · 상세 코칭 잠시 접음', 'metric': '주행에 집중', 'detail': '여유가 생기면 선택한 정보를 표시합니다.'}
    color = {'good': 'success', 'warn': 'warning'}.get(item.get('tone'), 'text')
    text(8, 23, item.get('title'), 374, 17, bold=True)
    metric, unit = str(item.get('metric') or '—'), str(item.get('unit') or '')
    reference = (presentation or {}).get('reference') or dashboard.get('reference') or {}
    compared = selected in ('score', 'loss', 'divergence', 'retention', 'phases', 'personal', 'consistency', 'stint')
    duration = reference.get('lap_time_s')
    reference_ready = compared and isinstance(duration, (int, float)) and 0 < duration < 86400
    if not live:
        reference_ready = compared
        duration = 97.607
        reference = {'source': 'GARAGE61' if config.get('source') == 'garage61' else 'PERSONAL'}
    metric_limit = 218 if reference_ready else 374
    text(8, 52 if large else 47, metric, metric_limit, 34, color, True, numeric=True)
    if unit:
        meaning = unit
        if selected in ('score', 'loss', 'stint', 'retention'):
            try:
                delta = float(metric.replace('−', '-'))
                meaning = '초 · 기준보다 느림' if delta > 0 else '초 · 기준보다 빠름' if delta < 0 else '초 · 기준과 같음'
                if selected == 'retention':
                    meaning = meaning.replace('기준', '이전 통과')
            except ValueError:
                pass
        text(8, 104 if large else 94, meaning, metric_limit, 12, color)
    if reference_ready:
        source = 'Garage61 기준 랩' if reference.get('source') == 'GARAGE61' else '내 빠른 기준 랩'
        text(236, 48, source, 146, 11, 'muted', True)
        text(236, 65, f'{int(duration//60)}:{duration%60:06.3f}', 146, 23, 'accent', True, numeric=True)
    if config.get('detail') != 'simple' and not (large and (card or hud.live_view.get('control_recommendation'))):
        detail = item.get('detail') or ''
        if config.get('detail') == 'detailed' and item.get('stats'):
            detail = ' · '.join(' '.join(map(str, stat)) for stat in item['stats'])+' · '+detail
        text(8, 123 if large else 111, detail, 374, 11, 'muted')
    if not quiet and not large and not card and not hud.live_view.get('control_recommendation'):
        for index, key in enumerate(slots[1:3]):
            item = items.get(key) or {'title': '표시 안 함' if key is None else '관측 자료 준비 중', 'metric': '—'}
            x = 8+index*190
            text(x, 131, str(item.get('name') or item.get('title') or '')+' · '+str(item.get('metric') or '—')+' '+str(item.get('unit') or ''), 180, 11, 'muted', True)
    hud.live_labels = {}
    hud.draw_footer_notice(card)


def sample_graph(goal, position, options):
    window = options.get('window', 10)
    future = 2 if options.get('future', True) else 0
    reference, current, rows = [], [], []
    for index in range((window+future)*30+1):
        offset = -window+index/30
        values = inputs(goal, position+offset*25, True)
        reference.append({'offset': offset, 'brake': values['brake'], 'throttle': values['throttle'], 'steering_deg': values['steering']})
        if offset <= 0:
            values = inputs(goal, position+offset*25, False)
            current.append({'offset': offset, 'brake': values['brake'], 'throttle': values['throttle'], 'steering_deg': values['steering']})
            rows.append({'session_time': window+offset, 'brake': values['brake'], 'throttle': values['throttle'], 'steering_rad': radians(values['steering'])})
    return {'reference': reference, 'current': current}, rows
