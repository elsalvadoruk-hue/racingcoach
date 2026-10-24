"""Windows HUD for an offline sample and read-only live pedal measurements.

Run with pythonw scripts/overlay_sample.py. No SDK or personal driving stores are
opened here. Only explicitly saved overlay appearance is restored. The web app
passes observed live measurements and visibility over the child's private pipes.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from contextlib import ExitStack
import os
import json
from pathlib import Path
import queue
import sys
import threading
from time import monotonic
import tkinter as tk
from tkinter import font as tkfont

sys.path.append(str(Path(__file__).resolve().parents[1] / "iRacing_Backend"))
from overlay_preferences import PreferencesStore, default_path, fit_position
from overlay_gear import gear_display
if __package__:
    from .overlay_commands import COMMAND_POLL_INTERVAL_MS, OverlayCommands
else:
    from overlay_commands import COMMAND_POLL_INTERVAL_MS, OverlayCommands
if __package__:
    from .overlay_rendering import OverlayRendering
else:
    from overlay_rendering import OverlayRendering
if __package__:
    from .overlay_native import NativeWindows
else:
    from overlay_native import NativeWindows
if __package__:
    from .overlay_theme import TITLE, WIDTH, HEIGHT, DRAG_HEIGHT, CORNER_RADIUS, MIN_ZOOM, MAX_ZOOM, COLORS, SCENES, SCENE_DURATIONS
else:
    from overlay_theme import TITLE, WIDTH, HEIGHT, DRAG_HEIGHT, CORNER_RADIUS, MIN_ZOOM, MAX_ZOOM, COLORS, SCENES, SCENE_DURATIONS

if __package__:
    from .overlay_demo_data import GOALS, GOAL_BY_KEY, help_text, inputs
    from .overlay_trace import TraceRenderer
    from .overlay_celebration import CelebrationRenderer
    from .overlay_race_result import RaceResultRenderer, animations_enabled
else:
    from overlay_demo_data import GOALS, GOAL_BY_KEY, help_text, inputs
    from overlay_trace import TraceRenderer
    from overlay_celebration import CelebrationRenderer
    from overlay_race_result import RaceResultRenderer, animations_enabled


class OverlaySample(OverlayRendering, OverlayCommands):
    def __init__(self, scene="during", goal="abs", autoplay=True, preferences_path=None, managed=False, hidden=False):
        self.native = NativeWindows()
        self.window_lifetime = ExitStack()
        try:
            self.window_lifetime.enter_context(self.native.block_activation(managed))
            self.initialize(scene, goal, autoplay, preferences_path, managed, hidden)
        except BaseException:
            self.window_lifetime.close()
            raise

    def initialize(self, scene, goal, autoplay, preferences_path, managed, hidden):
        self.preferences = PreferencesStore(preferences_path) if preferences_path is not None else None
        self.managed = managed
        self.hud_visible = not hidden
        self.display_mode = 'preview'
        self.live_view = {}
        self.live_received = None
        self.live_generation = 0
        self.live_structure = None
        self.live_celebrations = set()
        self.live_celebration_title = None
        self.commands = queue.Queue()
        self.control_timer = None
        self.last_published = None
        self.edit_controls = []
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(TITLE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", COLORS["transparent"])
        self.root.attributes("-alpha", 0.0 if hidden else 1.0)
        self.dpi_scale = max(1.0, min(1.75, self.root.winfo_fpixels("1i") / 96))
        self.scale = self.dpi_scale
        self.scale_x = self.scale_y = self.dpi_scale
        self.fonts = {}
        self.scene = tk.StringVar(value=scene)
        self.goal_key = tk.StringVar(value=goal)
        self.goal_label = tk.StringVar(value=GOAL_BY_KEY[goal].title)
        self.help = tk.StringVar(value=help_text(GOAL_BY_KEY[goal]))
        self.locked = tk.BooleanVar(value=False)
        self.settings_fixed = tk.BooleanVar(value=False)
        self.fix_button_text = tk.StringVar(value="현재 설정 저장·고정")
        self.autoplay = tk.BooleanVar(value=autoplay)
        self.content_opacity = tk.IntVar(value=100)
        self.background_opacity = tk.IntVar(value=85)
        self.width_zoom = tk.IntVar(value=100)
        self.height_zoom = tk.IntVar(value=100)
        self.width_text = tk.StringVar(value="100%")
        self.height_text = tk.StringVar(value="100%")
        self.status = tk.StringVar(value="제목: 이동 · 오른쪽: 가로 · 아래쪽: 세로 조절")
        self.timer = None
        self.animation_timer = None
        self.trace_started = monotonic()
        self.drag_origin = None
        self.resize_origin = None
        self.closed = False
        self.trace_renderer = TraceRenderer()
        self.trace_ready = False
        self.trace_labels = {}
        self.gear_item = None
        self.attempts = (False, True, False, True, True)
        self.celebration_renderer = CelebrationRenderer()
        self.race_result_renderer = RaceResultRenderer()
        self.reduced_motion = not animations_enabled()
        self.race_celebrations = {}
        self.celebration_started = None
        self.celebration_item = None
        self.background = tk.Toplevel(self.root)
        self.background.withdraw()
        self.background.title(TITLE + " 배경")
        self.background.overrideredirect(True)
        self.background.attributes("-topmost", True)
        self.background.attributes("-alpha", 0.0 if hidden else self.background_opacity.get()/100)
        self.background_canvas = tk.Canvas(
            self.background, width=self.x(WIDTH), height=self.y(HEIGHT),
            bg=COLORS["background"], highlightthickness=0, borderwidth=0,
        )
        self.background_canvas.pack()
        self.canvas = tk.Canvas(
            self.root, width=self.x(WIDTH), height=self.y(HEIGHT),
            bg=COLORS["transparent"], highlightthickness=0, borderwidth=0,
        )
        self.canvas.pack()
        for canvas in (self.canvas, self.background_canvas):
            canvas.bind("<ButtonPress-1>", self.start_drag)
            canvas.bind("<B1-Motion>", self.drag)
            canvas.bind("<ButtonRelease-1>", self.finish_drag)
            canvas.bind("<Motion>", self.pointer_motion)
        # The web app owns managed controls. Creating the unused Tk control
        # toplevel can activate its wrapper during startup, even if withdrawn.
        self.controls = None
        if not managed:
            self.controls = tk.Toplevel(self.root)
            self.controls.withdraw()
            self.controls.title("오버레이 샘플 조작")
            self.controls.attributes("-topmost", True)
            self.controls.resizable(False, False)
            self.controls.configure(bg=COLORS["background"])
            self.controls.protocol("WM_DELETE_WINDOW", self.close)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("<Escape>", lambda event: self.close())
        self.root.bind_all("<Control-0>", lambda event: self.reset_size())
        if self.controls is not None:
            self.make_controls()
        self.draw()
        self.root.update_idletasks()
        # Set styles on the final wrappers before their first visible map.
        self.native.click_through(self.background, False, no_activate=True)
        self.native.click_through(self.root, False, no_activate=managed)
        left, top, right, bottom = self.native.primary_work_area()
        hud_width, hud_height = self.x(WIDTH), self.y(HEIGHT)
        x = left + max(0, (right - left - hud_width) // 2)
        y = top + min(self.px(100), max(0, (bottom - top - hud_height) // 5))
        self.root.geometry(f"{hud_width}x{hud_height}+0+0")
        self.background.geometry(f"{hud_width}x{hud_height}+0+0")
        for window in (self.root, self.background):
            self.native.round_corners(window, hud_width, hud_height, self.px(CORNER_RADIUS))
        if not hidden:
            self.native.show_without_activation(self.background)
            self.native.show_without_activation(self.root)
        if self.controls is not None:
            self.controls.update_idletasks()
            self.controls.deiconify()
        self.root.update()
        self.native.move(self.root, x, y)
        self.root.bind("<Configure>", self.sync_background)
        self.sync_background()
        # Leave the HUD's growing right/bottom edges clear of its controls.
        if self.controls is not None:
            control_x = x - self.controls.winfo_width() - self.ui_px(24)
            control_y = y
            if control_x < left:
                control_x = left
                control_y = min(y + hud_height + self.ui_px(28),
                                bottom - self.controls.winfo_height() - self.ui_px(45))
            self.native.move(self.controls, control_x, control_y)
        if self.preferences is not None:
            self.restore_preferences()
        if managed:
            threading.Thread(target=self.read_commands, name="overlay-controls", daemon=True).start()
            self.control_timer = self.root.after(COMMAND_POLL_INTERVAL_MS, self.poll_commands)
        if hidden:
            self.root.withdraw(); self.background.withdraw()
        self.root.after(100, lambda: self.root.lift() if self.hud_visible else None)
        self.toggle_autoplay()
        self.animation_timer = self.root.after(16, self.tick)


    def set_size(self, axis, value, force=False):
        if self.settings_fixed.get() and not force:
            return
        percentage = max(MIN_ZOOM, min(MAX_ZOOM, round(float(value))))
        variable, label = ((self.width_zoom, self.width_text) if axis == "width"
                           else (self.height_zoom, self.height_text))
        variable.set(percentage)
        label.set(f"{percentage}%")
        self.scale_x = self.dpi_scale * self.width_zoom.get()/100
        self.scale_y = self.dpi_scale * self.height_zoom.get()/100
        # Keep glyphs proportional while each layout axis and the graph resize freely.
        self.scale = min(self.scale_x, self.scale_y)
        width, height = self.x(WIDTH), self.y(HEIGHT)
        self.canvas.configure(width=width, height=height)
        self.background_canvas.configure(width=width, height=height)
        self.root.geometry(f"{width}x{height}")
        self.background.geometry(f"{width}x{height}")
        self.draw()

    def reset_size(self):
        self.set_size("width", 100)
        self.set_size("height", 100)

    def set_opacity(self, layer, value, force=False):
        if self.settings_fixed.get() and not force:
            return
        percentage = max(0, min(100, round(float(value))))
        variable, window = ((self.content_opacity, self.root) if layer == "content"
                            else (self.background_opacity, self.background))
        variable.set(percentage)
        window.attributes("-alpha", percentage/100 if self.hud_visible else 0)
        if layer == 'content':
            self.native.sync_image(self.root, percentage, self.hud_visible)

    def display(self, values):
        mode, visible = values['mode'], values['visible']
        if mode not in ('preview', 'live') or type(visible) is not bool:
            raise ValueError('알 수 없는 오버레이 표시 방식입니다.')
        changed = mode != self.display_mode
        if changed:
            self.cancel_celebration()
            self.autoplay.set(False); self.toggle_autoplay()
        self.display_mode = mode
        if mode == 'live':
            previous_scope = (self.live_view.get('practice') or {}).get('scope')
            self.live_view = values if visible else {}
            self.live_received = monotonic() if visible else None
            self.live_generation += 1
            practice = self.live_view.get('practice') or {}
            if not visible or previous_scope != practice.get('scope') or not practice.get('five_successes'):
                self.cancel_celebration()
            structure = (bool(practice), practice.get('scope'), practice.get('phase') == 'RESULT',
                         practice.get('result_id'), tuple(practice.get('states') or []),
                         None,  # Footer notices never change the graph's structure.
                         self.live_view.get('run_id'), (self.live_view.get('session') or {}).get('kind'))
            result=self.live_view.get('race_result')
            if result:
                structure=(*structure[:-1],(structure[-1],result.get('id')))
            if visible and structure != self.live_structure:
                self.live_structure = structure
                self.draw()
            elif visible and result:
                self.draw_race_result()
            elif visible and self.celebration_started is None:
                # Paint this observed snapshot while processing its packet.
                # Waiting for the independent animation timer adds irregular
                # frame delays even when telemetry arrives at a steady 30 Hz.
                self.draw_trace()
            celebration = practice.get('celebration_id')
            if visible and celebration and celebration not in self.live_celebrations:
                self.live_celebrations.add(celebration)
                self.live_celebration_title = practice.get('title')
                self.celebration_started = monotonic()
                self.draw_celebration()
        if changed:
            self.toggle_lock()
        if visible != self.hud_visible:
            self.hud_visible = visible
            if visible:
                # Render the new session before exposing either layer again.
                self.draw()
                self.native.show_without_activation(self.background)
                self.native.show_without_activation(self.root)
                self.root.attributes('-alpha', self.content_opacity.get()/100)
                self.background.attributes('-alpha', self.background_opacity.get()/100)
                self.sync_background()
            else:
                self.native.sync_image(self.root, self.content_opacity.get(), False)
                self.root.withdraw(); self.background.withdraw()
        if changed:
            self.draw()

    def appearance(self):
        return {"width": self.width_zoom.get(), "height": self.height_zoom.get(),
                "x": self.root.winfo_x(), "y": self.root.winfo_y(),
                "content_opacity": self.content_opacity.get(),
                "background_opacity": self.background_opacity.get(), "fixed": self.settings_fixed.get()}

    def refresh_fixed_controls(self):
        fixed = self.settings_fixed.get()
        self.fix_button_text.set("고정 해제·편집" if fixed else "현재 설정 저장·고정")
        for widget in self.edit_controls:
            widget.configure(state="disabled" if fixed else "normal")
        self.drag_origin = self.resize_origin = None
        for canvas in (self.canvas, self.background_canvas):
            canvas.configure(cursor="arrow")

    def set_fixed(self, fixed):
        if fixed == self.settings_fixed.get():
            return
        if fixed:
            if self.preferences is None:
                raise OSError("오버레이 설정 저장 경로가 없습니다.")
            self.root.update_idletasks()
            self.preferences.save({**self.appearance(), "fixed": True})
        self.settings_fixed.set(fixed)
        self.locked.set(fixed)
        self.toggle_lock()
        self.refresh_fixed_controls()
        self.status.set("저장됨 · 위치·크기·불투명도 고정 · 클릭 통과"
                        if fixed else "편집 중 · 조절한 뒤 저장·고정을 누르세요.")

    def toggle_fixed(self):
        try:
            self.set_fixed(not self.settings_fixed.get())
        except OSError as error:
            self.status.set(f"설정을 저장하지 못했습니다: {error}")

    def restore_preferences(self):
        saved = self.preferences.load()
        self.set_size("width", saved["width"])
        self.set_size("height", saved["height"])
        self.set_opacity("content", saved["content_opacity"])
        self.set_opacity("background", saved["background_opacity"])
        self.root.update_idletasks()
        if saved["x"] is not None and saved["y"] is not None:
            self.move_inside(saved["x"], saved["y"])
        self.settings_fixed.set(saved["fixed"])
        self.locked.set(saved["fixed"])
        self.toggle_lock()
        self.refresh_fixed_controls()
        if saved["fixed"]:
            self.status.set("저장한 배치 복원됨 · 위치·크기·불투명도 고정")
        if self.preferences.load_error:
            self.status.set(self.preferences.load_error)

    def move_inside(self, x, y):
        x, y = fit_position(x, y, self.x(WIDTH), self.y(HEIGHT), self.native.work_areas())
        self.native.move(self.root, x, y)
        self.sync_background()


    def sync_background(self, event=None):
        if self.closed or (event is not None and event.widget is not self.root):
            return
        self.native.place_behind(self.background, self.root, self.root.winfo_x(),
                                 self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height())
        for window in (self.root, self.background):
            self.native.round_corners(window, self.root.winfo_width(), self.root.winfo_height(), self.px(CORNER_RADIUS))
        self.native.sync_image(self.root, self.content_opacity.get(), self.hud_visible)


    def tick(self):
        if self.closed:
            return
        started = monotonic()
        if self.display_mode == 'live' and self.live_received is not None and started-self.live_received >= 1:
            self.display({'mode': 'live', 'visible': False})
        if not self.hud_visible:
            self.animation_timer = self.root.after(100, self.tick)
            return
        if self.celebration_started is not None:
            self.draw_celebration(started)
        elif self.display_mode == 'live' and self.live_view.get('race_result'):
            self.draw_race_result(started)
        elif self.display_mode == 'live':
            if (self.live_view.get('practice') or {}).get('phase') != 'RESULT':
                self.draw_trace(started)
        elif self.scene.get() == "during":
            self.draw_trace(started)
        # Live data has its own frame cadence.  It must keep the 60 Hz paint
        # loop even when the preview left the scene selector on before/result;
        # otherwise a practice HUD silently falls back to a 100 ms timer.
        live_phase = (self.live_view.get('practice') or {}).get('phase')
        animated = (self.celebration_started is not None or self.scene.get() == "during"
                    or (self.display_mode == 'live' and live_phase != 'RESULT'))
        delay = max(1, round(1000/60-(monotonic()-started)*1000)) if animated else 100
        self.animation_timer = self.root.after(delay, self.tick)

    def make_controls(self):
        px, font = self.ui_px, self.ui_font
        panel = tk.Frame(self.controls, bg=COLORS["background"], padx=px(16), pady=px(12))
        panel.pack(fill="both", expand=True)
        goal_row = tk.Frame(panel, bg=COLORS["background"])
        goal_row.pack(fill="x", pady=(0, px(7)))
        tk.Label(goal_row, text="목표", font=font(11), bg=COLORS["background"],
                 fg=COLORS["muted"]).pack(side="left", padx=(0, px(8)))
        chooser = tk.OptionMenu(goal_row, self.goal_label, *(goal.title for goal in GOALS), command=self.manual_goal)
        chooser.configure(font=font(11), bg=COLORS["surface"], fg=COLORS["text"],
                          activebackground=COLORS["border"], activeforeground=COLORS["text"],
                          highlightthickness=0, relief="flat", width=31, anchor="w")
        chooser["menu"].configure(font=font(11), bg=COLORS["surface"], fg=COLORS["text"])
        chooser.pack(side="left", fill="x", expand=True)
        tk.Label(panel, textvariable=self.help, font=font(9), bg=COLORS["background"],
                 fg=COLORS["muted"]).pack(anchor="w", pady=(0, px(9)))
        scenes = tk.Frame(panel, bg=COLORS["background"])
        scenes.pack(fill="x", pady=(0, px(8)))
        for value, label in SCENES:
            tk.Radiobutton(
                scenes, text=label, variable=self.scene, value=value,
                command=self.manual_scene, indicatoron=False, width=10, borderwidth=0,
                padx=px(4), pady=px(5), font=font(11), cursor="hand2",
                bg=COLORS["surface"], fg=COLORS["text"],
                selectcolor=COLORS["accent_surface"], activebackground=COLORS["border"],
                activeforeground=COLORS["accent"], highlightthickness=0,
            ).pack(side="left", padx=(0, px(5)))
        tk.Button(panel, text="5/5 축하 보기", command=self.preview_celebration,
                  font=font(11, bold=True), cursor="hand2", bg=COLORS["accent_surface"],
                  fg=COLORS["success"], activebackground=COLORS["border"],
                  activeforeground=COLORS["success"], relief="flat", borderwidth=0,
                  padx=px(12), pady=px(6)).pack(fill="x", pady=(0, px(8)))
        options = tk.Frame(panel, bg=COLORS["background"])
        options.pack(fill="x")
        for label, variable, command in (
            ("자동 재생", self.autoplay, self.toggle_autoplay),
            ("클릭 통과", self.locked, self.toggle_lock),
        ):
            tk.Checkbutton(
                options, text=label, variable=variable, command=command,
                font=font(11), bg=COLORS["background"], fg=COLORS["text"],
                activebackground=COLORS["background"], activeforeground=COLORS["text"],
                selectcolor=COLORS["surface"], highlightthickness=0,
            ).pack(side="left", padx=(0, px(10)))
        tk.Button(options, text="닫기", command=self.close, font=font(11),
                  bg=COLORS["surface"], fg=COLORS["text"], relief="flat",
                  padx=px(13), pady=px(4)).pack(side="right")
        reset = tk.Button(options, text="크기 초기화", command=self.reset_size, font=font(10),
                  bg=COLORS["surface"], fg=COLORS["text"], relief="flat",
                  padx=px(8), pady=px(4))
        reset.pack(side="right", padx=(0, px(8)))
        self.edit_controls.append(reset)
        tk.Button(panel, textvariable=self.fix_button_text, command=self.toggle_fixed,
                  font=font(11, bold=True), bg=COLORS["accent_surface"], fg=COLORS["success"],
                  relief="flat", padx=px(10), pady=px(7)).pack(fill="x", pady=(px(8), 0))
        for label, axis, variable, value_text in (
            ("가로", "width", self.width_zoom, self.width_text),
            ("세로", "height", self.height_zoom, self.height_text),
        ):
            size_row = tk.Frame(panel, bg=COLORS["background"])
            size_row.pack(fill="x", pady=(px(8), 0))
            tk.Label(size_row, text=label, width=13, anchor="w", bg=COLORS["background"],
                     fg=COLORS["accent"], font=font(11)).pack(side="left")
            slider = tk.Scale(size_row, from_=MIN_ZOOM, to=MAX_ZOOM, variable=variable,
                     orient="horizontal", resolution=1,
                     command=lambda value, axis=axis: self.set_size(axis, value), showvalue=False,
                     length=px(195), bg=COLORS["background"], fg=COLORS["text"],
                     troughcolor=COLORS["surface"], highlightthickness=0, borderwidth=0,
                     activebackground=COLORS["accent"])
            slider.pack(side="left", padx=px(8))
            self.edit_controls.append(slider)
            tk.Label(size_row, textvariable=value_text, width=5, bg=COLORS["background"],
                     fg=COLORS["accent"], font=font(10)).pack(side="left")
        for label, layer, variable in (
            ("표시 불투명도", "content", self.content_opacity),
            ("배경 불투명도", "background", self.background_opacity),
        ):
            opacity_row = tk.Frame(panel, bg=COLORS["background"])
            opacity_row.pack(fill="x", pady=(px(8), 0))
            tk.Label(opacity_row, text=label, width=13, anchor="w", bg=COLORS["background"],
                     fg=COLORS["muted"], font=font(11)).pack(side="left")
            slider = tk.Scale(opacity_row, from_=0, to=100, variable=variable, orient="horizontal",
                     command=lambda value, layer=layer: self.set_opacity(layer, value),
                     length=px(195), bg=COLORS["background"], fg=COLORS["muted"],
                     troughcolor=COLORS["surface"], highlightthickness=0, borderwidth=0,
                     font=font(10), activebackground=COLORS["accent"])
            slider.pack(side="left", padx=px(8))
            self.edit_controls.append(slider)
        tk.Label(panel, textvariable=self.status, bg=COLORS["background"], fg=COLORS["muted"],
                 font=font(10)).pack(anchor="w", pady=(px(6), 0))

    def resize_edges(self, event):
        return (event.x >= self.x(WIDTH)-self.ui_px(10),
                event.y >= self.y(HEIGHT)-self.ui_px(10))

    def pointer_motion(self, event):
        cursor = "arrow"
        if not self.locked.get() and not self.settings_fixed.get():
            right, bottom = self.resize_edges(event)
            if right or bottom:
                cursor = "size_nw_se" if right and bottom else "sb_h_double_arrow" if right else "sb_v_double_arrow"
            elif event.y < self.y(DRAG_HEIGHT):
                cursor = "fleur"
        for canvas in (self.canvas, self.background_canvas):
            canvas.configure(cursor=cursor)

    def start_drag(self, event):
        if self.locked.get() or self.settings_fixed.get():
            return
        self.root.lift()
        self.sync_background()
        right, bottom = self.resize_edges(event)
        if right or bottom:
            self.resize_origin = (event.x_root, event.y_root, self.width_zoom.get(),
                                  self.height_zoom.get(), right, bottom)
        elif event.y < self.y(DRAG_HEIGHT):
            self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag(self, event):
        if self.locked.get() or self.settings_fixed.get():
            return
        if self.resize_origin:
            mouse_x, mouse_y, initial_width, initial_height, right, bottom = self.resize_origin
            if right:
                self.set_size("width", initial_width+(event.x_root-mouse_x)*100/(WIDTH*self.dpi_scale))
            if bottom:
                self.set_size("height", initial_height+(event.y_root-mouse_y)*100/(HEIGHT*self.dpi_scale))
        elif self.drag_origin:
            mouse_x, mouse_y, x, y = self.drag_origin
            self.native.move(self.root, x + event.x_root - mouse_x, y + event.y_root - mouse_y)
            self.sync_background()

    def finish_drag(self, event):
        self.drag_origin = None
        self.resize_origin = None
        self.pointer_motion(event)

    def toggle_lock(self):
        try:
            through = self.display_mode == 'live' or self.locked.get()
            self.native.click_through(self.background, through, no_activate=True)
            self.native.click_through(self.root, through, no_activate=self.managed)
        except OSError as error:
            self.locked.set(False)
            self.status.set(f"클릭 통과를 바꾸지 못했습니다: {error}")
        else:
            self.status.set("클릭 통과 켜짐 · 이 조작창에서 해제하세요."
                            if self.locked.get() else "제목: 이동 · 오른쪽: 가로 · 아래쪽: 세로 조절")
        self.sync_background()
        self.draw()

    def manual_scene(self):
        self.cancel_celebration()
        self.autoplay.set(False)
        self.toggle_autoplay()
        self.trace_started = monotonic()
        self.draw()

    def manual_goal(self, title):
        self.cancel_celebration()
        self.attempts = (False, True, False, True, True)
        goal = next(goal for goal in GOALS if goal.title == title)
        self.goal_key.set(goal.key)
        self.goal_label.set(title)
        self.help.set(help_text(goal))
        self.trace_started = monotonic()
        self.draw()
        self.toggle_autoplay()

    def toggle_autoplay(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.autoplay.get() and self.celebration_started is None:
            self.timer = self.root.after(SCENE_DURATIONS[self.scene.get()], self.next_scene)

    def next_scene(self):
        values = ["before", "during", "result"]
        previous = self.scene.get()
        self.scene.set(values[(values.index(previous)+1) % len(values)] if previous in values else "before")
        self.trace_started = monotonic()
        self.draw()
        self.timer = None
        self.toggle_autoplay()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.root.after_cancel(self.timer)
        if self.animation_timer is not None:
            self.root.after_cancel(self.animation_timer)
        if self.control_timer is not None:
            self.root.after_cancel(self.control_timer)
        self.native.close_image()
        try:
            self.root.destroy()
        finally:
            self.window_lifetime.close()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=[key for key, _ in SCENES], default="during")
    parser.add_argument("--goal", choices=list(GOAL_BY_KEY), default="abs")
    parser.add_argument("--paused", action="store_true", help="Keep the selected scene on screen.")
    parser.add_argument("--width", type=int, help="HUD width percentage (75-200), overriding saved settings.")
    parser.add_argument("--height", type=int, help="HUD height percentage (75-200), overriding saved settings.")
    parser.add_argument("--celebrate", action="store_true", help="Preview five successful sample attempts.")
    parser.add_argument("--settings", type=Path, help="Override the overlay appearance file.")
    parser.add_argument("--managed", action="store_true", help="Receive controls from the local web app.")
    parser.add_argument('--hidden', action='store_true', help='Wait for an explicit display command without flashing the HUD.')
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This native overlay sample runs on Windows.")
    # Opt in before creating any windows so sizing and dragging use physical pixels.
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    sample = OverlaySample(args.scene, args.goal, autoplay=not args.paused,
                           preferences_path=args.settings or default_path(), managed=args.managed, hidden=args.hidden)
    if args.width is not None:
        sample.set_size("width", args.width, force=True)
    if args.height is not None:
        sample.set_size("height", args.height, force=True)
    if args.celebrate:
        sample.root.after(600, sample.preview_celebration)
    sample.run()


if __name__ == "__main__":
    main()
