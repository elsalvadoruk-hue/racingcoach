"""Driving eligibility and an independent, atomic automatic-display preference."""
import json
import os
from pathlib import Path
import tempfile


def preparing(view):
    """Session metadata may prepare a reference before cockpit entry or pit exit."""
    sample = view.get('snapshot') or {}
    state = sample.get('session_state')
    return (view.get('connected') is True
            and sample.get('is_replay') is False and type(state) is int and 1 <= state <= 5)


def driving(view):
    sample = view.get('snapshot') or {}
    result = bool(view.get('race_result'))
    eligible = preparing(view) or (result and view.get('connected') is True
                                  and sample.get('is_replay') is False
                                  and sample.get('session_state') == 6)
    return eligible and (sample.get('on_track') is True or result)


class VisibilityStore:
    def __init__(self, path, fallback=None):
        self.path = Path(path)
        self.fallback = Path(fallback) if fallback else None

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value.get('version') == 1 and value.get('enabled') is True
        except FileNotFoundError:
            return VisibilityStore(self.fallback).load() if self.fallback else True
        except (OSError, ValueError, AttributeError):
            return False

    def save(self, enabled):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.overlay-visibility-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump({'version': 1, 'enabled': enabled}, file)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
