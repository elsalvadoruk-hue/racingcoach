"""One local measurement per call. Screen pixels are never saved or transmitted."""
import asyncio
import time

from .sdk import SdkReader, finite
from .cockpit_gate import CockpitGate, cockpit_context
from .vehicle_dash import canonical_car, name_for
from .pressure_ocr import WHEELS, WindowsPressureOcr, make_profile
from .multi_dash_locator import MultiCarDashLocator, ocr_frame
from .window_visibility import GameVisibility

MAX_PIXELS = 40_000_000
GEOMETRY = ('left', 'top', 'width', 'height')
IDENTITY = ('car_path', 'track_id', 'track_config', 'SessionNum', 'session_id', 'subsession_id')


class ScreenTireReader:
    def __init__(self, *, sdk_factory=None, screens_factory=None, locator=None, ocr=None,
                 visibility=None, clock=time.monotonic):
        if sdk_factory is None:
            import irsdk
            sdk_factory = irsdk.IRSDK
        if screens_factory is None:
            import mss
            screens_factory = mss.mss
        self.sdk_factory, self.screens_factory = sdk_factory, screens_factory
        self.locator = locator or MultiCarDashLocator()
        self.ocr = ocr or WindowsPressureOcr()
        self.visibility = visibility or GameVisibility()
        self.clock, self.gate = clock, CockpitGate(10)
        self.sdk = self.reader = self.screens = None
        self.generation = 0
        self.car = self.rect = None
        self.misses = 0
        self.context = {}

    def _disconnect(self):
        sdk, self.sdk, self.reader = self.sdk, None, None
        if sdk is not None:
            sdk.shutdown()

    def close(self):
        try:
            self._disconnect()
        finally:
            if self.screens is not None:
                self.screens.close()
                self.screens = None

    def sample(self):
        from .snapshot import from_iracing
        row = self._sample()
        row['tire_snapshot'] = from_iracing(row, self.context, self.clock()).to_dict()
        return row

    def _sample(self):
        tick = self.clock()
        row = dict(status='sdk_waiting', overlay_ready=False, cockpit_epoch=self.gate.epoch)
        operation = 'sdk_connect'
        try:
            if self.sdk is None:
                candidate = self.sdk_factory()
                try:
                    connected = candidate.startup() and candidate.is_connected
                except Exception:
                    candidate.shutdown()
                    raise
                if connected:
                    self.sdk = candidate
                    self.reader = SdkReader(candidate)
                    self.generation += 1
                else:
                    candidate.shutdown()
            if self.sdk is None or not self.sdk.is_connected:
                self._disconnect()
                self.gate.update({}, tick)
                return row
            operation = 'sdk_before'
            before = self.reader.snapshot(self.generation)
            self.context = before
            gate = self.gate.update(before, tick)
            row.update(status=gate['status'], overlay_ready=gate['ready'],
                       cockpit_epoch=gate['epoch'], warmup_remaining_s=gate['remaining_s'],
                       car_path=before.get('car_path'), car_name=name_for(before.get('car_path')))
            car = canonical_car(before.get('car_path'))
            if car != self.car:
                self.car, self.rect, self.misses = car, None, 0
                self.locator.set_car(car)
                self.locator.previous_bounds = None
            if not gate['ready']:
                if car is None:
                    row['status'] = 'unsupported_car'
                return row
            import numpy as np
            operation = 'capture'
            if self.screens is None:
                self.screens = self.screens_factory()
            physical = [{key: int(m[key]) for key in GEOMETRY} for m in self.screens.monitors[1:]]
            if self.rect not in physical:
                self.rect = None
            candidates = [self.rect] if self.rect else []
            if not candidates or self.misses >= 2:
                candidates += [m for m in physical if m not in candidates]
            found = dict(status='game_window_not_visible', location=None)
            for rect in candidates:
                if rect['width'] * rect['height'] > MAX_PIXELS or not self.visibility.window_visible(rect):
                    continue
                image = np.asarray(self.screens.grab(rect))[:, :, :3].copy()
                if list(image.shape[1::-1]) != [rect['width'], rect['height']]:
                    raise ValueError('capture_size_changed')
                operation = 'locate'
                found = self.locator.locate(image)
                if found['location']:
                    self.rect, self.misses = rect, 0
                    break
            if not found['location']:
                self.misses += 1
                row['status'] = found['status']
                row['measurement'] = {'stage': 'locate', 'status': found['status']}
                return row
            location = found['location']
            frame, pressure, temperatures = ocr_frame(image, location)
            # Cup 992.2 uses integer kPa in metric mode (159 = 1.59 bar).
            # Do not infer kPa from integer OCR on other dashboards: a missing
            # decimal in a psi/bar reading would then become a plausible value.
            display_units = before.get('DisplayUnits')
            cup_kpa = car == 'porsche9922cup' and type(display_units) is int and display_units == 1
            unit, decimals = ('kPa', 0) if cup_kpa else ('bar', 2)
            profile = make_profile(frame, 'live-visible-game', '', pressure, unit, decimals,
                car_path=car, temperature_boxes=temperatures, automatic_partial=True,
                screen_unit='kPa' if cup_kpa else 'auto', temperature_kind=location.get('temperature_kind'),
                temperature_unit={0: 'F', 1: 'C'}.get(before.get('DisplayUnits'), 'unknown'))
            operation = 'ocr'
            recognized = asyncio.run(self.ocr.recognize(frame, profile))
            row['measurement'] = {'stage': 'ocr', 'locate_ms': found.get('elapsed_ms'),
                'ocr_ms': recognized.get('elapsed_ms'),
                'temperature_unit': recognized.get('temperature_unit'),
                'pressure': {w: recognized['wheels'].get(w, {}).get('status') for w in WHEELS},
                'temperature': {w: recognized.get('temperatures', {}).get(w, {}).get('status') for w in WHEELS}}
            operation = 'sdk_after'
            after = self.reader.snapshot(self.generation)
            after['fresh'] = before['fresh'] or after['fresh']
            self.context = after  # Voice timing must use controls after the OCR work.
            before_time, after_time = finite(before.get('SessionTime')), finite(after.get('SessionTime'))
            same = all(before.get(key) == after.get(key) for key in IDENTITY)
            same = same and before.get('DisplayUnits') == after.get('DisplayUnits')
            same = same and before_time is not None and after_time is not None and after_time >= before_time
            if not same or not cockpit_context(after):
                self.gate.update({}, self.clock())
                row.update(status='context_changed_during_frame', overlay_ready=False)
            elif not self.visibility.window_visible(self.rect):
                row['status'] = 'game_window_not_visible'
            elif not self.visibility.pressure_visible(self.rect, {**location['boxes'],
                    **{'temp_' + w: box for w, box in location.get('temperature_boxes', {}).items()}}):
                row['status'] = 'pressure_area_occluded'
            elif self.clock() - tick > 1.5:
                row['status'] = 'frame_expired'
            else:
                from .color_state import states
                pressure_states, temperature_states = states(frame, car, pressure, temperatures)
                row.update(status='reading', capture=dict(self.rect),
                           temperature_kind=location.get('temperature_kind'),
                           pressure_states=pressure_states, temperature_states=temperature_states)
                row.update({'raw_kpa_' + w: finite(recognized['wheels'].get(w, {}).get('kpa')) for w in WHEELS})
                row.update({'raw_temp_c_' + w: finite(recognized.get('temperatures', {}).get(w, {}).get('celsius')) for w in WHEELS})
            return row
        except Exception as error:
            if operation.startswith('sdk'):
                self.gate.update({}, tick)
                row['overlay_ready'] = False
                try:
                    self._disconnect()
                except Exception:
                    pass
            row.update(status='measurement_error', error_type=type(error).__name__, operation=operation)
            return row
