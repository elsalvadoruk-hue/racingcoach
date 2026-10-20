"""One live connection for whichever sim is running: iRacing or LMU.

Both expose the pyirsdk ``IRSDK`` interface (LMU through ``LmuIrsdk``), so the
live coach does not care which game it reads. The first game that starts
answering is used until it disconnects.
"""
from __future__ import annotations


class AutoGameSdk:
    def __init__(self, candidates):
        # [(game, sdk)] in preference order. Each sdk is created once and reused.
        self.candidates = list(candidates)
        self.active = None

    @property
    def game(self):
        return None if self.active is None else getattr(self.active, 'game', 'iracing')

    @property
    def is_initialized(self):
        return self.active is not None and self.active.is_initialized

    @property
    def is_connected(self):
        return self.active is not None and self.active.is_connected

    @property
    def waiting_message(self):
        notes = [sdk.status_message() for _, sdk in self.candidates if hasattr(sdk, 'status_message')]
        note = next((n for n in notes if n), None)  # only actionable notes (e.g. bridge not built)
        base = 'iRacing 또는 LMU를 실행하고 차량에 탑승하면 연결됩니다.'
        return f'{base} {note}' if note else base

    def startup(self, *args, **kwargs):
        for game, sdk in self.candidates:
            try:
                started = sdk.startup(*args, **kwargs)
            except Exception:
                started = False
            if started:
                if not getattr(sdk, 'game', None):
                    try:
                        sdk.game = game
                    except AttributeError:
                        pass
                self.active = sdk
                return True
        return False

    def shutdown(self):
        active, self.active = self.active, None
        if active is not None:
            active.shutdown()

    def freeze_var_buffer_latest(self):
        self.active.freeze_var_buffer_latest()

    def unfreeze_var_buffer_latest(self):
        self.active.unfreeze_var_buffer_latest()

    @property
    def var_headers_names(self):
        return self.active.var_headers_names

    def __getitem__(self, name):
        return self.active[name]


def default_game_sdk():
    import irsdk
    from lmu_irsdk import shared
    return AutoGameSdk([('iracing', irsdk.IRSDK()), ('lmu', shared())])
