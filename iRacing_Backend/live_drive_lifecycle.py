"""Bounded UI-only drive lifecycle from explicit live SDK observations.

Call while holding the live reader's lock. Reading view() never advances time
or manufactures a completion; there is no telemetry history or persistence.
"""
from __future__ import annotations

import math
import uuid


def sdk_boolean(value):
    """SDK booleans may be bool or 0/1; missing/other values stay unknown."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class DriveLifecycle:
    OFF_TRACK_SECONDS = 3.
    DISCONNECTED_SECONDS = 10.
    MAX_OBSERVATION_GAP_SECONDS = 2.5

    def __init__(self):
        self.lifecycle_id = str(uuid.uuid4())
        self.completed_serial = 0
        self.last_completed = None
        self.phase = 'IDLE'
        self.active_game = None
        self.drive_id = None
        self.active_drive = False
        self._pending_reason = None
        self._pending_at = None
        self._last_observed_at = None

    def _cancel_pending(self):
        self._pending_reason = None
        self._pending_at = None

    def _game(self, game):
        if not isinstance(game, str) or game not in {'iracing', 'lmu'}:
            return False
        if game != self.active_game:
            self.active_game = game
            self.drive_id = None
            self.active_drive = False
            self.phase = 'IDLE'
            self._cancel_pending()
            self._last_observed_at = None
        return True

    def _clock(self, now):
        if not _finite(now):
            self.observe_unknown()
            return False
        if self._last_observed_at is not None and now < self._last_observed_at:
            self._last_observed_at = now
            self.observe_unknown()
            return False
        if (self._last_observed_at is not None
                and now - self._last_observed_at > self.MAX_OBSERVATION_GAP_SECONDS):
            # A stalled reader does not observe a sustained off-car state.
            # Keep the drive active and start from this real observation.
            self._cancel_pending()
        self._last_observed_at = now
        return True

    def observe_unknown(self, game=None):
        if game is not None:
            self._game(game)
        self._cancel_pending()
        self.phase = 'UNKNOWN'

    def _ending(self, reason, now, threshold):
        if not self.active_drive:
            if self.phase != 'ENDED':
                self.phase = 'IDLE'
            self._cancel_pending()
            return
        if self._pending_reason != reason:
            self._pending_reason, self._pending_at = reason, now
        self.phase = 'ENDING'
        if now - self._pending_at < threshold:
            return
        self.completed_serial += 1
        self.last_completed = dict(serial=self.completed_serial, game=self.active_game,
                                   reason=reason, drive_id=self.drive_id)
        self.active_drive = False
        self.phase = 'ENDED'
        self._cancel_pending()

    def observe_disconnected(self, game, now):
        if not self._game(game) or not self._clock(now):
            self.observe_unknown()
            return
        self._ending('DISCONNECTED', now, self.DISCONNECTED_SECONDS)

    def observe_sample(self, game, sample, now):
        if not self._game(game) or not self._clock(now):
            self.observe_unknown()
            return
        replay, on_track = (sdk_boolean(sample.get(key)) for key in ('is_replay', 'on_track'))
        if replay is not False or on_track is None:
            self.observe_unknown()
            return
        if on_track is False:
            self._ending('OFF_TRACK', now, self.OFF_TRACK_SECONDS)
            return
        self._cancel_pending()
        session_time, speed = sample.get('session_time'), sample.get('speed_mps')
        if not _finite(session_time) or session_time < 0 or not _finite(speed) or speed < 0:
            self.observe_unknown()
            return
        if speed > 1:
            if not self.active_drive:
                self.drive_id = str(uuid.uuid4())
            self.active_drive = True
        # Stationary/pit/on-track observations cannot finish an existing drive.
        self.phase = 'DRIVING' if self.active_drive else 'ENDED' if self.phase == 'ENDED' else 'IDLE'

    def view(self):
        return dict(lifecycle_id=self.lifecycle_id, completed_serial=self.completed_serial,
                    last_completed=dict(self.last_completed) if self.last_completed else None,
                    phase=self.phase, active_game=self.active_game, drive_id=self.drive_id,
                    active_drive=self.active_drive)
