"""Overlay appearance only. Importing this module opens no app or personal stores."""
import json
import os
from pathlib import Path
import tempfile

DEFAULTS = {"width": 100, "height": 100, "x": None, "y": None,
            "content_opacity": 100, "background_opacity": 85, "fixed": False}
LIMITS = {"width": (75, 200), "height": (75, 200), "x": (-100000, 100000),
          "y": (-100000, 100000), "content_opacity": (0, 100), "background_opacity": (0, 100)}


def default_path():
    import app_settings
    return app_settings.data_dir() / "overlay-settings.json"


def normalize(values):
    result = dict(DEFAULTS)
    if not isinstance(values, dict):
        return result
    for key, (low, high) in LIMITS.items():
        value = values.get(key)
        if type(value) is int:
            result[key] = max(low, min(high, value))
    if type(values.get("fixed")) is bool:
        result["fixed"] = values["fixed"]
    return result


class PreferencesStore:
    def __init__(self, path):
        self.path = Path(path)
        self.load_error = None

    def load(self):
        self.load_error = None
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or values.get("version") != 1:
                raise ValueError("Unsupported overlay settings")
            return normalize(values)
        except FileNotFoundError:
            return dict(DEFAULTS)
        except (OSError, ValueError):
            self.load_error = "저장된 오버레이 설정을 읽지 못해 기본값으로 열었습니다."
            return dict(DEFAULTS)

    def save(self, values):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".overlay-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({"version": 1, **normalize(values)}, file, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def fit_position(x, y, width, height, areas):
    """Keep the whole HUD on its nearest current monitor, including negative coordinates."""
    candidates = []
    for left, top, right, bottom in areas:
        fitted_x = max(left, min(x, max(left, right-width)))
        fitted_y = max(top, min(y, max(top, bottom-height)))
        candidates.append(((fitted_x-x)**2+(fitted_y-y)**2, fitted_x, fitted_y))
    _, x, y = min(candidates)
    return x, y
