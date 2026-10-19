"""Shared native HUD geometry, colors and sample scene timing."""


TITLE = "iRacing 코치 · 오버레이 샘플"

WIDTH, HEIGHT = 390, 150

DRAG_HEIGHT = 24

CORNER_RADIUS = 6

MIN_ZOOM, MAX_ZOOM = 75, 200

COLORS = {
    "background": "#080c12", "surface": "#111b27", "border": "#33435a",
    "text": "#f6faff", "muted": "#bed0e6", "accent": "#00f0ff",
    "accent_surface": "#123039", "warning": "#fff04a", "missing": "#7f91a8",
    "success": "#4dff6a", "target": "#fff04a",
    "transparent": "#101a26",
    "brake": "#ff365e", "throttle": "#3dff62", "steering": "#00cfff",
    "reference_brake": "#4c1828", "reference_throttle": "#14492a",
    "reference_steering": "#17394d",
    "halo_brake": "#792038", "halo_throttle": "#155b2c", "halo_steering": "#164761",
    "gear_low": "#ff4040",
    "notice_lap": "#15243b", "notice_success": "#142b20",
    "notice_traffic": "#10252c", "notice_warning": "#292616",
}

SCENES = (
    ("before", "진입 전"), ("during", "주행 그래프"),
    ("result", "통과 결과"), ("missing", "미측정"),
)

SCENE_DURATIONS = {"before": 2000, "during": 8000, "result": 5000, "missing": 5000}
