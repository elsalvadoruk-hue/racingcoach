"""Render the real HUD into a web preview using withdrawn Tk canvases. No game/stores."""
from pathlib import Path
import base64
from html import escape
import io
import json
import sys
from types import SimpleNamespace
import tkinter as tk

sys.path.append(str(Path(__file__).resolve().parents[1]/'iRacing_Backend'))
from overlay_studio import select_modules
from overlay_demo_data import GOAL_BY_KEY
from overlay_rendering import OverlayRendering
from overlay_studio_view import sample_graph
from overlay_theme import COLORS
from overlay_trace import TraceRenderer


def sample_packet(config, session, scene, availability, game):
    """Fictional observations, fed through the same drawing path as live packets."""
    ready = availability != 'none' and not (config['source'] == 'garage61' and availability != 'g61')
    garage = game == 'iracing' and availability == 'g61' and config['source'] != 'personal'
    reference = {'state': 'READY' if ready else 'MISSING', 'source': 'GARAGE61' if garage else 'PERSONAL',
                 'lap_time_s': (97.607 if garage else 109.286 if game == 'lmu' else 97.814) if ready else None}
    # Values illustrate the actual cards' units/titles, never a user's telemetry.
    examples = {
        'live': ('자동 주행 코칭','직전 제동 해제 → 가속 연결','0.42','s','최근 무입력 1.20s · 동시 입력 0.18s · 관측값'),
        'goal': ('다음 코너 연습 목표','브레이크 해제에 집중','5','m 앞에서','C4 · 제동 시작 위치는 유지'),
        'success': ('최근 시도 성공률','선택한 입력 목표 달성','3','/ 5회','목표 달성과 페이스 개선은 별도입니다.'),
        'score': ('직전 코너 성적표','C4 · 기준 대비 시간 차이','-0.120','s','같은 거리의 통과 시간 비교'),
        'retention': ('개선 유지 여부','C4 · 이전 통과 중앙값 대비','-0.030','s','이전 4회와 이번 통과 비교'),
        'personal': ('내 빠른 기록과 차이','C4 · 내 기준 대비 제동 시작','8','m 늦음','같은 차량·레이아웃 · 비슷한 조건'),
        'reference': ('비교 기준 준비 상태','비교 기준 준비됨' if ready else '조건에 맞는 기준 기록 없음',
                      '1:37.607' if garage else '1:49.286' if game == 'lmu' else '1:37.814','', '샘플 기록 · 실제 주행 기록 아님'),
        'consistency': ('코너별 일관성','C4 · 최근 제동 시작 편차','7.0','m 범위','깨끗한 통과 5회 · 같은 기준'),
        'stint': ('스틴트 페이스 변화','C4 · 최근 3회 시간 차이 중앙값','+0.240','s','같은 기준 대비 · 원인 추정 없음'),
        'summary': ('이번 주행 개선 요약','최근 측정 구간의 기준 대비 개선','2','/ 4구간','측정되지 않은 구간은 포함하지 않습니다.'),
        'change': ('바꾼 조작과 결과','브레이크 해제 결과','5','m 앞당김','이전 통과 대비 · 페이스 확인 전'),
        'loss': ('최대 손실 구간','C8 · 최근 관측 중 최대 시간 차이','+0.190','s','이번·직전 랩의 관측 중'),
        'divergence': ('처음 달라진 조작','C4 · 해제부터 차이 관측','8.0','m 늦음','비교한 조작 중 첫 차이'),
        'pattern': ('반복 조작 패턴','C4 · 재리프트 / 재제동','3 / 1','회','최근 깨끗한 5통과'),
        'phases': ('진입·회전·탈출 손익','C4 · 진입 / 회전 / 탈출','+0.180','s','−0.050 / +0.030 / +0.200s'),
        'common': ('빠른 통과의 공통 패턴','C4 · 빠른 3회에서 관측','3','/ 5통과','재제동 없음 · 인과관계 아님'),
        'impact': ('충돌 전후 관측 변화','접촉·손상 신호 뒤 관측 변화','-4.0','km/h','이전 2랩 / 이후 1랩 · 원인 미확정'),
    }
    compared = ('score','loss','divergence','retention','phases','personal','consistency','stint')
    items = {}
    for key,(name,title,metric,unit,detail) in examples.items():
        available = scene != 'missing' and (ready or key not in compared) and (scene != 'start' or key in ('live','reference'))
        if key == 'personal' and garage:
            available = False
        if not available:
            title,metric,unit,detail = name+' · 측정 대기','—','','관측된 자료가 준비되면 표시합니다.'
        items[key] = dict(id=key,name=name,title=title,metric=metric,unit=unit,detail=detail,
                          available=available,tone='good' if key in ('score','reference') else 'neutral',stats=[])
    if not ready:
        items['reference'].update(metric='—')
    if session == 'formation':
        items['live'].update(title='포메이션 · 자동 관측 대기',metric='그린 후 관측',unit='',available=True)
    dashboard = dict(items=items,reference=reference,result=scene=='after',lap_result=scene=='lap',quiet=scene=='traffic')
    layout = select_modules(config, session, dashboard)
    presentation = dict(state='holding',layout=layout,items={key:items[key] for key in layout['ids']},reference=reference)
    if scene == 'traffic':
        presentation.update(state='busy',layout={'ids':[],'waiting':'straight'},items={})
    dashboard['presentation'] = presentation
    options = config['graph']
    position = GOAL_BY_KEY['release'].start_m + (140 if scene=='lap' else 100 if scene=='after' else 65)
    comparison,rows = sample_graph(GOAL_BY_KEY['release'],position,options)
    if scene == 'missing':
        rows=[]; comparison=None
    elif not ready:
        comparison=None
    return dict(mode='live',visible=True,studio=config,studio_session=session,dashboard=dashboard,points=rows,
                reference_status=reference,comparison=comparison,gear_comparison={'current':4,'reference':4 if ready else None},
                snapshot={'gear':4,'speed_mps':48.,'brake':0.,'throttle':1.},feedback={'delta_s':-.12,'brake_delta_m':8.} if ready else {})


class ImageLayer:
    def clear_image(self):
        self.frame = None
    def present_image(self, widget, frame, offset, opacity, visible):
        self.frame,self.offset = frame,offset


class PreviewHud(OverlayRendering):
    """Actual Tk text metrics and canvas operations; no native window is shown."""
    def __init__(self, packet, appearance, pixel_ratio):
        self.root = tk.Tk(); self.root.withdraw()
        self.scale_x = appearance['width']/100*pixel_ratio
        self.scale_y = appearance['height']/100*pixel_ratio
        self.scale = min(self.scale_x,self.scale_y); self.dpi_scale = pixel_ratio
        self.canvas = tk.Canvas(self.root,highlightthickness=0)
        self.background_canvas = tk.Canvas(self.root,highlightthickness=0)
        self.fonts = {}; self.native = ImageLayer(); self.native.clear_image()
        self.display_mode = 'live'; self.live_view = packet; self.hud_visible = False
        self.live_generation = 1; self.trace_started = 0.; self.live_labels = {}
        self.goal_key = SimpleNamespace(get=lambda:'release');self.scene = SimpleNamespace(get=lambda:'during')
        self.content_opacity = SimpleNamespace(get=lambda:appearance['content_opacity'])
        self.trace_renderer = TraceRenderer()


def canvas_svg(hud, canvas):
    parts,texts = [],[]
    for item in canvas.find_all():
        if canvas.itemcget(item,'state') == 'hidden':continue
        kind,coords,fill = canvas.type(item),canvas.coords(item),canvas.itemcget(item,'fill')
        if kind == 'text':
            value = canvas.itemcget(item,'text')
            if not value:continue
            font = next(font for font in hud.fonts.values() if str(font)==canvas.itemcget(item,'font'))
            bounds = canvas.bbox(item); actual = font.actual()
            x,y = bounds[0]+1,bounds[1]+font.metrics('ascent')
            pixels=abs(int(font.cget('size')))
            parts.append(f'<text x="{x}" y="{y}" fill="{fill}" font-family="{escape(actual["family"])}" font-size="{pixels}" font-weight="{actual["weight"]}" xml:space="preserve">{escape(value)}</text>')
            texts.append(value)
        elif kind == 'rectangle':
            x,y,x2,y2 = coords
            parts.append(f'<rect x="{x}" y="{y}" width="{x2-x}" height="{y2-y}" fill="{fill or "none"}"/>')
        elif kind == 'polygon':
            pairs = list(zip(coords[::2],coords[1::2]))
            if canvas.itemcget(item,'smooth') not in ('0','false',''):
                mid=lambda a,b:f'{(a[0]+b[0])/2},{(a[1]+b[1])/2}'
                path='M'+mid(pairs[-1],pairs[0])+''.join(f' Q{x},{y} '+mid((x,y),pairs[(index+1)%len(pairs)]) for index,(x,y) in enumerate(pairs))+' Z'
                parts.append(f'<path d="{path}" fill="{fill}"/>')
            else:
                parts.append(f'<polygon points="{" ".join(f"{x},{y}" for x,y in pairs)}" fill="{fill}"/>')
        else:
            raise ValueError('Unsupported native preview canvas item: '+kind)
    return ''.join(parts),texts


def render(value):
    packet = sample_packet(value['config'],value['session'],value['scene'],value['availability'],value['game'])
    appearance,ratio = value['appearance'],value['pixel_ratio']
    hud = PreviewHud(packet,appearance,ratio)
    try:
        hud.draw()
        width,height = hud.x(390),hud.y(150)
        back,_ = canvas_svg(hud,hud.background_canvas)
        front,texts = canvas_svg(hud,hud.canvas)
        image = ''
        if hud.native.frame is not None:
            stream=io.BytesIO();hud.native.frame.save(stream,format='PNG')
            x,y=hud.native.offset;w,h=hud.native.frame.size
            image=f'<image x="{x}" y="{y}" width="{w}" height="{h}" href="data:image/png;base64,{base64.b64encode(stream.getvalue()).decode()}"/>'
        clip=f'<defs><clipPath id="hud"><rect width="{width}" height="{height}" rx="{hud.px(6)}"/></clipPath></defs>'
        svg=(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
             +clip+f'<g clip-path="url(#hud)"><g opacity="{appearance["background_opacity"]/100}"><rect width="{width}" height="{height}" fill="{COLORS["background"]}"/>{back}</g>'
             +f'<g opacity="{appearance["content_opacity"]/100}">{image}{front}</g></g></svg>')
        return dict(svg=svg,width=width/ratio,height=height/ratio,texts=texts,
                    ids=packet['dashboard']['presentation']['layout']['ids'],renderer='native-tk-v1',mode=value['config']['modes'][value['session']])
    finally:
        hud.fonts.clear();hud.root.destroy()


if __name__ == '__main__':
    value=json.loads(sys.stdin.buffer.read(65537).decode('utf8'))
    print(json.dumps(render(value),ensure_ascii=True))
