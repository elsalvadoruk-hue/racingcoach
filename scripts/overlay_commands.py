"""Private parent/child command transport; all Tk mutations stay on its UI thread."""
from __future__ import annotations
import json
import queue
import sys
if __package__:
    from .overlay_theme import SCENES
    from .overlay_demo_data import GOAL_BY_KEY
else:
    from overlay_theme import SCENES
    from overlay_demo_data import GOAL_BY_KEY


# Keep receipt jitter below a quarter of the 30 Hz telemetry interval. Live
# packets paint on receipt instead of waiting for a second animation timer.
COMMAND_POLL_INTERVAL_MS = 8


class OverlayCommands:
    def read_commands(self):
        for line in sys.stdin:
            try:
                self.commands.put(json.loads(line))
            except ValueError:
                continue
        self.commands.put({"action": "close"})

    def publish_state(self, request_id=None, error=None):
        state = {"settings": self.appearance(), "goal": self.goal_key.get(),
                 "scene": self.scene.get(), "click_through": self.display_mode == 'live' or self.locked.get(),
                 "mode": self.display_mode, "visible": self.hud_visible,
                 "card": ({key: self.live_view['card'].get(key) for key in ('id', 'kind', 'value')}
                          if self.hud_visible and self.live_view.get('card') and not self.live_view.get('practice') else None)}
        if request_id is not None or state != self.last_published:
            print(json.dumps({"state": state, "id": request_id, "error": error}, ensure_ascii=True), flush=True)
            self.last_published = state

    def poll_commands(self):
        if self.closed:
            return
        for _ in range(10):
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            try:
                action, values = command.get("action"), command.get("values", {})
                if action == 'display':
                    self.display(values)
                elif action == 'studio':
                    self.studio_config = values['config']
                    self.studio_session = values['session']
                    self.draw()
                elif action == "settings":
                    if self.settings_fixed.get():
                        raise ValueError("설정이 고정되어 있습니다. 먼저 고정을 해제하세요.")
                    for key in ("width", "height"):
                        if key in values:
                            self.set_size(key, values[key])
                    for key, layer in (("content_opacity", "content"), ("background_opacity", "background")):
                        if key in values:
                            self.set_opacity(layer, values[key])
                    self.root.update_idletasks()
                    self.move_inside(values.get("x", self.root.winfo_x()), values.get("y", self.root.winfo_y()))
                elif action == "fixed":
                    self.set_fixed(values["fixed"])
                elif action == "demo":
                    if "goal" in values:
                        self.manual_goal(GOAL_BY_KEY[values["goal"]].title)
                    if "scene" in values:
                        if values["scene"] not in dict(SCENES):
                            raise ValueError("알 수 없는 샘플 장면입니다.")
                        self.scene.set(values["scene"])
                        self.manual_scene()
                    if values.get("celebrate"):
                        self.preview_celebration()
                elif action == "close":
                    self.publish_state(command.get("id"))
                    self.close()
                    return
                else:
                    raise ValueError("알 수 없는 오버레이 조작입니다.")
                self.publish_state(command.get("id"))
            except (OSError, ValueError, KeyError) as error:
                self.publish_state(command.get("id"), str(error))
        self.publish_state()
        self.control_timer = self.root.after(COMMAND_POLL_INTERVAL_MS, self.poll_commands)
