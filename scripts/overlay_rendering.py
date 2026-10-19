"""Native canvas drawing; window lifecycle and pipe commands stay separate."""
from __future__ import annotations
from math import isfinite
from time import monotonic
from tkinter import font as tkfont
from overlay_gear import gear_display
if __package__:
    from .overlay_theme import COLORS
    from .overlay_demo_data import GOAL_BY_KEY
    from .overlay_trace import TraceRenderer
else:
    from overlay_theme import COLORS
    from overlay_demo_data import GOAL_BY_KEY
    from overlay_trace import TraceRenderer


class OverlayRendering:
    def px(self, value):
        return round(value * self.scale)

    def x(self, value):
        return round(value * self.scale_x)

    def y(self, value):
        return round(value * self.scale_y)

    def ui_px(self, value):
        return round(value * self.dpi_scale)

    def font(self, size, bold=False, mono=False):
        return self.font_at_pixels(self.px(size), bold, mono)

    def ui_font(self, size, bold=False, mono=False):
        return self.font_at_pixels(self.ui_px(size), bold, mono)

    def font_at_pixels(self, size, bold=False, mono=False):
        key = size, bold, mono
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(
                family="Consolas" if mono else "맑은 고딕", size=-size,
                weight="bold" if bold else "normal",
            )
        return self.fonts[key]

    def text(self, x, y, text, size=12, color="text", bold=False, anchor="nw", **kwargs):
        return self.canvas.create_text(
            self.x(x), self.y(y), text=text, anchor=anchor,
            fill=COLORS.get(color, color), font=self.font(size, bold), **kwargs,
        )

    def rect(self, x, y, width, height, fill, radius=0, outline="", tags=(), background=False):
        canvas = self.background_canvas if background else self.canvas
        x1, y1, x2, y2 = self.x(x), self.y(y), self.x(x+width), self.y(y+height)
        if radius:
            r = self.px(radius)
            points = [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
                      x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
                      x1, y2, x1, y2-r, x1, y1+r, x1, y1]
            return canvas.create_polygon(
                points, smooth=True, splinesteps=24, fill=COLORS.get(fill, fill),
                outline=COLORS.get(outline, outline), width=self.px(1), tags=tags,
            )
        return canvas.create_rectangle(
            x1, y1, x2, y2, fill=COLORS.get(fill, fill), outline=COLORS.get(outline, outline), tags=tags,
        )

    def draw(self):
        c = self.canvas
        c.delete("all")
        self.background_canvas.delete("all")
        self.native.clear_image()
        self.trace_ready = False
        self.trace_labels = {}
        self.gear_item = None
        self.card_ticket = None
        self.notice_ticket = None
        self.celebration_item = None
        if self.display_mode == 'live':
            practice = self.live_view.get('practice') or {}
            self.live_labels = {
                'reference': self.text(6, 146, '', size=10, color='muted', anchor='sw'),
                'detail': self.text(384, 146, '', size=10, color='muted', anchor='se'),
            }
            if practice:
                self.live_labels.update(
                    title=self.text(6, 4, '', size=12, bold=True, color='accent'),
                    badge=self.text(384, 4, '', size=10, color='success', anchor='ne'))
            if practice.get('phase') == 'RESULT':
                self.draw_live_result(practice)
            else:
                self.draw_trace()
            if practice:
                self.text(6, 146, '최근 5회', size=10, color='muted', anchor='sw')
                states = practice.get('states') or [None]*5
                for index, achieved in enumerate(states):
                    self.rect(61+index*16, 134, 11, 11, 'success' if achieved is True else 'surface', radius=3,
                              outline='missing' if achieved is None else 'warning' if achieved is False else '')
                self.text(146, 146, f"{practice.get('recent_ok', 0)}/5", size=10,
                          color='success', bold=True, anchor='sw')
            self.update_live_labels()
            self.rect(6, 129, 378, 1, 'border')
            if self.celebration_started is not None:
                self.draw_celebration()
            return
        scene = self.scene.get()
        missing = scene == "missing"
        goal = GOAL_BY_KEY[self.goal_key.get()]
        self.text(6, 4, goal.title, size=12, bold=True, color="accent")
        self.text(384, 4, "T1 · 샘플", size=10, color="warning", anchor="ne")
        if scene in ("result", "missing"):
            self.draw_result(goal, missing)
        else:
            self.draw_trace()
        self.rect(6, 129, 378, 1, "border")
        self.text(6, 146, "최근 5회", size=10, color="muted", anchor="sw")
        states = (True, False, True, True, None) if missing else self.attempts
        for index, achieved in enumerate(states):
            x = 61 + index * 16
            self.rect(x, 134, 11, 11, "success" if achieved else "surface", radius=3,
                      outline="missing" if achieved is None else "")
            if achieved is None:
                self.text(x+5.5, 139.5, "?", size=9, color="muted", bold=True, anchor="center")
        count = sum(state is True for state in states)
        unmeasured = sum(state is None for state in states)
        progress = f"{count}/5 · {unmeasured}회 미측정" if unmeasured else f"{count}/5 달성"
        self.text(146, 146, progress,
                  size=10, color="success", bold=True, anchor="sw")
        self.text(384, 146, "페이스 확인 전", size=10, color="muted", anchor="se")
        if self.celebration_started is not None:
            self.draw_celebration()

    def fit_text(self, value, width, size=12, bold=False):
        font = self.font(size, bold)
        value = str(value or '')
        if font.measure(value) <= self.x(width):
            return value
        while value and font.measure(value+'…') > self.x(width):
            value = value[:-1]
        return value+'…'

    def update_live_labels(self):
        practice = self.live_view.get('practice') or {}
        session = self.live_view.get('session') or {}
        status = self.live_view.get('reference_status') or {}
        comparison = self.live_view.get('comparison')
        card = self.live_view.get('card') if not practice else None
        sample = self.live_view.get('snapshot') or {}

        def measured(key, scale, unit, maximum=None):
            value = sample.get(key)
            if (type(value) not in (int, float) or not isfinite(value) or value < 0
                    or maximum is not None and value > maximum):
                return '—'
            return f'{value*scale:.0f}{unit}'

        reference = '속도 '+measured('speed_mps', 3.6, ' km/h')
        detail = f"브레이크 {measured('brake', 100, '%', 1)} · 가속 {measured('throttle', 100, '%', 1)}"
        title = practice.get('title') or session.get('title') or ('Garage61 페달 비교' if comparison else '실시간 페달 입력')
        if card:
            reference, detail = '', ''
        elif practice:
            reference = ''
            if practice.get('phase') == 'WARMUP':
                detail = f"워밍업 {practice.get('warm_laps', 0)}/{practice.get('warmup_laps', 0)}"
            elif practice.get('phase') == 'RESULT':
                detail = '페이스 확인 전'
            elif comparison:
                detail = ('내 랩' if status.get('source') == 'PERSONAL' else 'G61')+' · 기준 +2초'
            elif status.get('state') == 'TASK_REFERENCE_MISSING':
                detail = '과제 기준 확인 필요'
        elif comparison:
            feedback = self.live_view.get('feedback') or {}
            delta, braking = feedback.get('delta_s'), feedback.get('brake_delta_m')
            reference = f'직전 코너 {delta:+.2f}초' if type(delta) in (int, float) else '직전 코너 —'
            if type(braking) in (int, float):
                metres = round(abs(braking))
                detail = f"제동 시작 {metres}m {'늦음' if braking > 0 else '빠름'}" if metres else '제동 시작 같음'
            else:
                detail = '제동 시작 —'
        basic_feedback = not practice and not card and bool(comparison)
        delta = (self.live_view.get('feedback') or {}).get('delta_s')
        delta_color = ('warning' if delta > .005 else 'success' if delta < -.005 else 'text') if type(delta) in (int, float) else 'muted'
        for name, value in (('title', self.fit_text(title, 290, 12, True)),
                            ('reference', reference), ('detail', detail),
                            ('badge', self.fit_text(practice.get('corner') or session.get('badge') or 'LIVE', 85, 10))):
            if name in self.live_labels:
                self.canvas.itemconfigure(self.live_labels[name], text=value)
                if name in ('reference', 'detail'):
                    self.canvas.itemconfigure(self.live_labels[name], font=self.font(12 if basic_feedback else 10, basic_feedback),
                                              fill=COLORS[delta_color if name == 'reference' and basic_feedback else 'accent' if basic_feedback else 'muted'])
        self.draw_footer_notice(card)

    def draw_footer_notice(self, card):
        """Temporarily replace only the existing footer; keep the graph intact."""
        if not card:
            if self.notice_ticket is not None:
                self.canvas.delete('footer-notice')
                self.background_canvas.delete('footer-notice')
                self.notice_ticket = None
            return
        color = card.get('color', 'text')
        background = ('notice_warning' if color == 'warning' else
                      'notice_success' if color == 'success' else
                      'notice_traffic' if card.get('kind') == 'closing' else 'notice_lap')
        text = str(card.get('text') or card.get('value') or '')
        ticket = text, color, background, self.scale_x, self.scale_y
        if ticket == self.notice_ticket:
            return
        self.notice_ticket = ticket
        self.canvas.delete('footer-notice')
        self.background_canvas.delete('footer-notice')
        # Put the tint in the content layer: a fully transparent user-selected
        # background must not erase the notice's type indicator.
        self.rect(6, 131, 378, 18, background, radius=3, tags=('footer-notice',))
        size = 12
        while size > 10 and self.font(size, True).measure(text) > self.x(366):
            size -= 1
        self.text(195, 146, self.fit_text(text, 366, size, True), size=size,
                  color=color, bold=True, anchor='s', tags=('footer-notice',))

    def draw_live_result(self, practice):
        for x, label in ((6, '이전'), (145, '이번'), (276, '목표')):
            self.text(x, 27, label, size=10, color='muted')
        ok = practice.get('goal_ok')
        for x, key, size, color in ((6, 'before', 30, 'muted'), (145, 'after', 36,
                                    'success' if ok is True else 'warning' if ok is None else 'text'),
                                   (276, 'target', 28, 'target')):
            value = practice.get(key) or '—'
            while size > 16 and self.font(size, True).measure(value) > self.x(108):
                size -= 1
            self.text(x, 50, value, size=size, color=color, bold=True)
        detail = '입력·부가 조건 충족' if ok is True else '미측정 · 이번 통과 제외' if ok is None else '목표 미달'
        if practice.get('goal_possible') is False:
            detail = '현재 기준으로 달성 불가 · 목표 재선택 필요'
            if practice.get('goal_issue') == 'BASELINE_MISSING':
                detail = '기준 측정 없음 · 과제 재분석 필요'
        self.text(6, 108, detail, size=11, color='success' if ok is True else 'warning')

    def set_attempts(self, states):
        """Only five measured successes crossing the threshold start an effect."""
        states = tuple(states)
        if len(states) != 5 or any(state is not True and state is not False and state is not None
                                   for state in states):
            raise ValueError("Five measured success/failure/unknown states are required.")
        if states == self.attempts:
            return
        was_complete = all(state is True for state in self.attempts)
        self.attempts = states
        complete = all(state is True for state in states)
        if not complete:
            was_celebrating = self.celebration_started is not None
            self.cancel_celebration()
            if was_celebrating:
                self.toggle_autoplay()
        if complete and not was_complete and self.scene.get() != "missing":
            self.celebration_started = monotonic()
            if self.timer is not None:
                self.root.after_cancel(self.timer)
                self.timer = None
        self.draw()

    def cancel_celebration(self):
        # Live packets also call this when no celebration is active.  The
        # shared image surface then contains the graph: hiding it would flash
        # the whole graph off and on at the telemetry update rate.
        if self.celebration_item is not None:
            self.native.clear_image()
            self.canvas.delete('celebration')
            self.canvas.itemconfigure('all', state='normal')
            self.background_canvas.itemconfigure('all', state='normal')
        self.celebration_started = None
        self.celebration_item = None

    def preview_celebration(self):
        self.autoplay.set(False)
        self.toggle_autoplay()
        self.cancel_celebration()
        self.scene.set("during")
        self.trace_started = monotonic()
        self.attempts = (True, True, True, True, False)
        self.set_attempts((True,)*5)

    def draw_celebration(self, now=None):
        now = monotonic() if now is None else now
        elapsed = max(0, now-self.celebration_started)
        if elapsed >= self.celebration_renderer.DURATION:
            self.cancel_celebration()
            self.trace_started = now
            self.draw()
            self.toggle_autoplay()
            return
        frame = self.celebration_renderer.frame(elapsed, self.scale_x, self.scale_y, COLORS)
        if self.celebration_item is None:
            self.canvas.itemconfigure("all", state="hidden")
            self.background_canvas.itemconfigure("all", state="hidden")
            self.celebration_item = self.text(195, 71, "5/5 달성!", size=38, color="success", bold=True,
                                              anchor="center", tags=("celebration",))
            title = self.live_celebration_title if self.display_mode == 'live' else GOAL_BY_KEY[self.goal_key.get()].title
            self.text(195, 106, self.fit_text(title, 360, 12, True), size=12,
                      color="text", bold=True, anchor="center", tags=("celebration",))
        self.native.present_image(self.root, frame, (0, 0), self.content_opacity.get(), self.hud_visible)
        # Fade text with the particles without changing the user's layer opacity.
        fade = max(0, min(1, (self.celebration_renderer.DURATION-elapsed)/0.55))
        for item, color in zip(self.canvas.find_withtag("celebration"), ("success", "text")):
            rgb = bytes.fromhex(COLORS[color].lstrip("#"))
            key = bytes.fromhex(COLORS["transparent"].lstrip("#"))
            fill = "#"+"".join(f"{round(a*fade+b*(1-fade)):02x}" for a, b in zip(rgb, key))
            self.canvas.itemconfigure(item, fill=fill)

    def draw_result(self, goal, missing):
        verdict = goal.verdict(not missing)
        self.text(6, 25, "기준", size=10, color="muted")
        self.text(145, 25, "이번", size=10, color="success", bold=True)
        self.text(276, 25, "목표", size=10, color="target", bold=True)
        self.text(6, 50, goal.value_text(goal.before), size=30, color="muted")
        self.text(122, 54, "→", size=22, color="muted", anchor="n")
        self.text(145, 44, goal.value_text(None if missing else goal.after), size=36,
                  color="warning" if missing else "success" if verdict["goal_ok"] else "text", bold=True)
        self.text(276, 52, goal.value_text(goal.target, target=True), size=28, color="target", bold=True)
        self.text(6, 105, "입력 채널 미측정 · 이번 통과 제외" if missing else goal.guards,
                  size=10, color="warning" if missing else "muted")

    def draw_trace(self, now=None):
        """Present complete RGBA frames without color-key edge artifacts."""
        if self.display_mode == 'live' and (self.live_view.get('practice') or {}).get('phase') == 'RESULT':
            return
        basic = self.display_mode == 'live' and not self.live_view.get('practice')
        bounds = TraceRenderer.BASIC_BOUNDS if basic else TraceRenderer.BOUNDS
        top, height = (7, 116) if basic else (43, 80)
        goal = GOAL_BY_KEY[self.goal_key.get()]
        elapsed = max(0, (monotonic() if now is None else now)-self.trace_started)
        position = 40 if self.scene.get() == "before" else goal.start_m + elapsed*25
        if self.display_mode == 'live':
            ticket = (self.live_generation, self.scale_x, self.scale_y)
            if self.trace_ready and getattr(self, 'live_drawn', None) == ticket:
                return
            self.live_drawn = ticket
            comparison = self.live_view.get('comparison')
            rows = self.live_view.get('points', [])
            steering = (self.live_view.get('practice') or {}).get('cause') == 'ROTATION_DEFICIT'
            if comparison:
                frame, live = self.trace_renderer.comparison_frame(comparison, rows, self.scale_x, self.scale_y, COLORS, steering=steering, basic=basic)
            else:
                frame, live = self.trace_renderer.live_frame(rows, self.scale_x, self.scale_y, COLORS, steering=steering, basic=basic)
            self.update_live_labels()
        else:
            frame, live = self.trace_renderer.frame(goal, position, self.scale_x, self.scale_y, COLORS)
        tag = ("trace",)
        if not self.trace_ready:
            self.trace_ready = True
            self.rect(6, top, 288 if basic else 336, height, "surface", tags=tag, background=True)
            for x in ((304, 324) if basic else (352, 372)):
                self.rect(x, top, 10, height, "surface", tags=tag, background=True)
            if self.display_mode != 'live':
                self.text(286, 27, "지금", size=8, color="muted", anchor="n", tags=tag)
        self.native.present_image(self.root, frame, (self.x(bounds[0]), self.y(bounds[1])),
                                  self.content_opacity.get(), self.hud_visible)
        if basic:
            self.draw_gear()
            return
        for channel, x, label, value in (
            ("brake", 6, "제동", '미측정' if live['brake'] is None else f"{live['brake']*100:.0f}%"),
            ("throttle", 98, "가속", '미측정' if live['throttle'] is None else f"{live['throttle']*100:.0f}%"),
            ("steering", 190, "조향", '미측정' if live.get('steering') is None else f"{live['steering']:.0f}°"),
        ):
            if channel == "steering" and ((self.display_mode == 'live' and not steering) or (self.display_mode != 'live' and goal.key != 'steering')):
                continue
            text = f"{label} {value}"
            if channel not in self.trace_labels:
                item = self.text(x, 25, text, size=10, color=channel, bold=True, tags=tag)
                self.trace_labels[channel] = item, text
            elif self.trace_labels[channel][1] != text:
                item = self.trace_labels[channel][0]
                self.canvas.itemconfigure(item, text=text)
                self.trace_labels[channel] = item, text

    def draw_session_card(self, card):
        ticket = (tuple(card.get(key) for key in ('id', 'session', 'name', 'caption', 'value', 'color', 'left', 'right')),
                  self.scale_x, self.scale_y)
        if self.card_ticket == ticket:
            return
        self.card_ticket = ticket
        tags = ('session-card',)
        self.canvas.delete('session-card')
        self.background_canvas.delete('session-card')
        self.rect(6, 7, 328, 116, 'surface', background=True, tags=tags)
        self.text(9, 9, f"{card['session']} · {card['name']}", size=12, color='accent', bold=True, tags=tags)
        self.text(9, 31, self.fit_text(card['caption'], 320, 10), size=10, color='muted', tags=tags)
        size = 42
        while size > 20 and self.font(size, True).measure(card['value']) > self.x(320):
            size -= 1
        self.text(9, 46, card['value'], size=size, bold=True, color=card['color'], tags=tags)
        self.text(9, 107, self.fit_text(card['left'], 155, 10), size=10, color='text', tags=tags)
        self.text(331, 107, self.fit_text(card['right'], 155, 10), size=10, color=card['color'], anchor='ne', tags=tags)

    def draw_gear(self):
        snapshot = self.live_view.get('snapshot') or {}
        comparison = self.live_view.get('gear_comparison') or {}
        # Validate both values again at the presentation boundary.
        gear = gear_display(comparison.get('current', snapshot.get('gear')), comparison.get('reference'))
        size = 68
        while size > 20 and self.font(size, True).measure(gear['text']) > self.x(44):
            size -= 1
        if self.gear_item is None:
            self.gear_item = self.text(362, 65, '', size=size, bold=True, anchor='center', tags=('trace',))
        self.canvas.itemconfigure(self.gear_item, text=gear['text'], fill=COLORS[gear['color']], font=self.font(size, True))
