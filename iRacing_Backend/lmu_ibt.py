"""An LMU DuckDB recording presented as a parsed iRacing IBT file.

``LmuIbtReader(path).parse()`` returns the same ``ParsedTelemetry`` the IBT
reader does: one uniform 60 Hz time axis with iRacing channel names, the
session-info structure and lap segments. The whole iRacing analysis (corner
causes, coaching, practice tasks, consistency, overlays) then runs unchanged.

What LMU does not record is left out rather than invented:
  * LMU's own G-force channels do not follow the car's motion in real files,
    so LatAccel/LongAccel/YawRate are derived from speed and the GPS path.
  * Steering is recorded as % of lock. It becomes an angle through the car's
    wheel range when the live coach has measured it (``lmu_steering``), else
    through a nominal range; ``metadata['steering_scale']`` says which.
  * Lap validity (cuts, penalties) is not in the file. Laps whose recorded
    time matches their boundaries and that stay inside the measured track
    edges count as valid, as iRacing counts its own clean laps.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from analysis_numeric import file_signature
from ibt_reader import IBTReader
from lmu_irsdk import stable_id
from lmu_lap_verdicts import game_verdict
from lmu_telemetry import LmuTelemetryFormatError, LmuTelemetryReader
from telemetry_types import LapSegment, ParsedTelemetry

TICK_RATE = 60
OFF_TRACK_MARGIN_M = 1.5     # beyond the measured track edge counts as off track
# LMU main layouts that iRacing also scans: their official maps give the corner
# number page a map and an automatic number suggestion (corner_names.suggest).
OFFICIAL_MAPS = (('monza', 239), ('sarthe', 268), ('le mans', 268), ('spa-francorchamps', 523), ('imola', 266),
                 ('enzo e dino', 266), ('algarve', 509), ('portim', 509), ('sebring', 95), ('americas', 229),
                 ('carlos pace', 212), ('interlagos', 212), ('fuji', 444), ('silverstone', 341))
SESSION_TYPES = {'race': 'Race', 'practice': 'Practice', 'qualify': 'Open Qualify', 'qualifying': 'Open Qualify',
                 'warmup': 'Warmup', 'test': 'Offline Testing'}


def official_track_id(track, layout=''):
    """The iRacing official map of an LMU track's main layout, or None."""
    name = str(track or '').casefold()
    if layout and str(layout).casefold() != name:
        return None
    return next((track_id for key, track_id in OFFICIAL_MAPS if key in name), None)


def _session_type(text):
    key = str(text or '').strip().casefold()
    return next((value for name, value in SESSION_TYPES.items() if key.startswith(name)), str(text or '') or None)


def _smooth(values, width):
    if len(values) < width or width < 2:
        return values
    kernel = np.ones(width) / width
    filled = np.where(np.isfinite(values), values, 0.)
    weight = np.convolve(np.isfinite(values).astype(float), kernel, mode='same')
    out = np.convolve(filled, kernel, mode='same') / np.maximum(weight, 1e-9)
    out[weight < .5] = np.nan
    return out


class LmuIbtReader:
    def __init__(self, path):
        self.path = Path(path)

    # ------------------------------------------------------------------
    def parse(self) -> ParsedTelemetry:
        reader = LmuTelemetryReader(self.path)
        with reader._connection() as connection:
            tables = reader._tables(connection)
            summary = reader._summary(connection, tables)
            specs = {spec.name.casefold(): spec for spec in summary.channels}

            def native(name):
                spec = specs.get(name.casefold())
                if spec is None:
                    return None
                try:
                    values = reader._channel(connection, tables, spec.name)
                except LmuTelemetryFormatError:
                    return None  # per-wheel tables etc.
                return start + np.arange(len(values)) / spec.frequency_hz, values

            def events(name):
                try:
                    return reader._events(connection, tables, name)
                except LmuTelemetryFormatError:
                    return None

            gps = reader._channel(connection, tables, specs['gps time'].name) if 'gps time' in specs else None
            if gps is None or len(gps) < 2 or not np.isfinite(gps[0]):
                raise LmuTelemetryFormatError('GPS Time이 없는 LMU 기록은 분석할 수 없습니다.')
            start, end = float(gps[0]), float(gps[-1])
            time = start + np.arange(int((end - start) * TICK_RATE) + 1) / TICK_RATE
            sources = {name: native(name) for name in (
                'Ground Speed', 'Throttle Pos', 'Brake Pos', 'Steering Pos', 'Engine RPM', 'Fuel Level',
                'Lap Dist', 'GPS Latitude', 'GPS Longitude', 'Path Lateral', 'Track Edge',
                'Track Temperature', 'Ambient Temperature', 'Wind Speed')}
            event_names = ('Gear', 'ABS', 'ABSLevel', 'TCLevel', 'Brake Bias Rear', 'In Pits', 'Lap Time',
                           'Best LapTime')
            changes = {name: events(name) for name in event_names}

        def at(name, scale=1.):
            source = sources.get(name)
            if source is None:
                return None
            axis, values = source
            return np.interp(time, axis, values, left=np.nan, right=np.nan) * scale

        def step(name, default=np.nan):
            source = changes.get(name)
            out = np.full(len(time), default, dtype=float)
            if source is None or not len(source[0]):
                return out
            stamps, values = source
            index = np.searchsorted(stamps, time, side='right') - 1
            known = index >= 0
            out[known] = values[index[known]]
            return out

        length = self._track_length(summary, sources.get('Lap Dist'))
        channels = {'SessionTime': time}
        speed = at('Ground Speed', 1 / 3.6)
        channels.update(Speed=speed, Throttle=at('Throttle Pos', .01), Brake=at('Brake Pos', .01),
                        RPM=at('Engine RPM'), FuelLevel=at('Fuel Level'),
                        TrackTempCrew=at('Track Temperature'), AirTemp=at('Ambient Temperature'),
                        WindVel=at('Wind Speed'), Gear=step('Gear'),
                        BrakeABSactive=step('ABS', 0.) > 0, dcABS=step('ABSLevel'),
                        dcTractionControl=step('TCLevel'))
        bias = step('Brake Bias Rear')
        channels['dcBrakeBias'] = np.where(np.isfinite(bias), 100. * (1. - bias), np.nan)
        from lmu_steering import half_range_rad
        from lmu_car_models import model_for
        half_range, steering_scale = half_range_rad(summary.metadata.get('car_name'),
                                                    model_for(summary.metadata.get('car_name'), summary.metadata.get('car_model_key')))
        steer = at('Steering Pos', .01)
        if steer is not None:
            # LMU: positive steers right; iRacing: positive steers left.
            channels['SteeringWheelAngle'] = -steer * half_range
        travelled = self._lap_distance(time, sources.get('Lap Dist'), length)
        distance = np.mod(travelled, length)
        channels['LapDist'], channels['LapDistPct'] = distance, distance / length
        pits = step('In Pits', 0.) > 0
        channels['OnPitRoad'] = pits
        channels['IsOnTrack'] = np.ones(len(time), dtype=bool)
        surface = np.full(len(time), 3, dtype=np.int8)
        lateral, edge = at('Path Lateral'), at('Track Edge')
        if lateral is not None and edge is not None:
            off = np.isfinite(lateral) & np.isfinite(edge) & (np.abs(lateral) > np.abs(edge) + OFF_TRACK_MARGIN_M)
            surface[off] = 0
        surface[pits] = 2
        channels['PlayerTrackSurface'] = surface
        latitude, longitude = at('GPS Latitude'), at('GPS Longitude')
        if latitude is not None and longitude is not None:
            channels['Lat'], channels['Lon'] = latitude, longitude
            channels.update(self._motion(time, latitude, longitude, speed))
        last = step('Lap Time', 0.)
        channels['LapLastLapTime'] = np.where(last > 0, last, 0.)
        best = step('Best LapTime', 0.)
        channels['LapBestLapTime'] = np.where(best > 0, best, 0.)

        from lmu_lap_verdicts import verdicts_for
        try:
            ended = self.path.stat().st_mtime
        except OSError:
            ended = None
        meta = summary.metadata
        verdicts = verdicts_for(meta.get('track_name'), meta.get('car_name'), meta.get('recording_time'), ended)
        laps = self._laps(summary, time, channels, length, travelled, verdicts)
        lap_numbers = np.zeros(len(time))
        lap_start = np.full(len(time), np.nan)
        for lap in laps:
            lap_numbers[lap.start_idx:lap.end_idx + 1] = lap.lap_number
            lap_start[lap.start_idx:lap.end_idx + 1] = time[lap.start_idx]
        channels['Lap'] = lap_numbers
        channels['LapCompleted'] = np.maximum(lap_numbers - 1, 0)
        channels['LapCurrentLapTime'] = time - lap_start

        metadata_in = summary.metadata
        session_info = self._session_info(metadata_in, length)
        metadata = IBTReader._extract_metadata(None, session_info, '')
        layout = metadata_in.get('track_layout') or ''
        metadata.update(game='lmu', car_class=metadata_in.get('car_class') or None,
                        driver_name=str(metadata_in.get('driver_name') or '').strip() or None,
                        car_model_key=metadata_in.get('car_model_key') or None,
                        steering_scale=steering_scale, steering_half_range_deg=round(math.degrees(half_range), 1),
                        accel_source='DERIVED_GPS_SPEED', validity_source='LMU_TIMING_AND_TRACK_EDGE',
                        track_layout=layout, recording_time=metadata_in.get('recording_time'),
                        official_track_id=official_track_id(metadata_in.get('track_name'), layout))
        from lmu_personal_reference import recording_conditions
        metadata['lmu_lap_conditions'] = recording_conditions(reader, summary)
        return ParsedTelemetry(path=self.path, signature=file_signature(self.path), tick_rate=TICK_RATE,
                               buffer_length=0, record_count=len(time), data_offset=0, session_text='',
                               session_info=session_info, variables={}, channels=channels,
                               metadata=metadata, setup={}, laps=laps)

    # ------------------------------------------------------------------
    @staticmethod
    def _track_length(summary, distance):
        travelled = [lap.distance_m for lap in summary.laps
                     if lap.status == 'VALIDITY_UNKNOWN' and lap.distance_m]
        if travelled:
            return float(np.median(travelled))
        if distance is not None and np.any(np.isfinite(distance[1])):
            return float(np.nanmax(distance[1]))
        raise LmuTelemetryFormatError('트랙 길이를 확인할 수 있는 랩 거리가 없습니다.')

    @staticmethod
    def _lap_distance(time, source, length):
        if source is None:
            return np.full(len(time), np.nan)
        axis, values = source
        values = np.asarray(values, dtype=float)
        # Unwrap the start/finish reset so interpolation never sweeps the lap backwards.
        resets = np.r_[0, np.cumsum(np.diff(values) < -length / 2)]
        unwrapped = values + resets * length
        return np.interp(time, axis, unwrapped, left=np.nan, right=np.nan)

    @staticmethod
    def _motion(time, latitude, longitude, speed):
        """Accelerations and yaw rate from the measured path and speed (iRacing signs: left +)."""
        lat0 = np.nanmedian(latitude)
        x = np.radians(longitude) * 6371000 * math.cos(math.radians(lat0))
        y = np.radians(latitude) * 6371000
        x, y = _smooth(x, 31), _smooth(y, 31)
        dt = 1 / TICK_RATE
        heading = np.unwrap(np.arctan2(np.gradient(y, dt), np.gradient(x, dt)))
        yaw = _smooth(np.gradient(heading, dt), 15)
        moving = speed > 5 if speed is not None else np.zeros(len(time), dtype=bool)
        yaw = np.where(moving, yaw, 0.)
        result = {'YawRate': yaw}
        if speed is not None:
            result['LatAccel'] = speed * yaw
            result['LongAccel'] = _smooth(np.gradient(_smooth(speed, 9), dt), 9)
        return result

    @staticmethod
    def _laps(summary, time, channels, length, travelled, verdicts=None):
        # LMU's lap event and its distance reset can be up to a second apart.
        # Cut laps where the distance actually crosses the line (as an IBT does)
        # when that crossing is close to the event; keep the recorded lap time.
        crossings = np.flatnonzero(np.diff(np.floor(travelled / length)) > 0) + 1 if np.any(
            np.isfinite(travelled)) else np.array([], dtype=int)

        def boundary(stamp):
            index = int(np.searchsorted(time, stamp))
            if len(crossings):
                nearest = crossings[np.argmin(np.abs(crossings - index))]
                if abs(nearest - index) <= 2 * TICK_RATE:
                    return int(nearest)
            return index
        segments = []
        pct, surface, pits = channels['LapDistPct'], channels['PlayerTrackSurface'], channels['OnPitRoad']
        fuel = channels.get('FuelLevel')
        for lap in summary.laps:
            first, last = boundary(lap.start_time_s), boundary(lap.end_time_s) - 1
            if last <= first:
                continue
            window = slice(first, last + 1)
            on_track = float(np.mean(surface[window] == 3))
            pit_samples = int(np.count_nonzero(pits[window]))
            complete = lap.status not in ('INCOMPLETE', 'DISTANCE_DISCONTINUITY') and (
                lap.distance_m or 0) >= .9 * length
            reason = None
            if not complete:
                status = 'OUT_LAP' if pit_samples or lap.status == 'PIT_LAP' else 'PARTIAL'
            elif lap.status == 'PIT_LAP' or pit_samples:
                status, reason = 'INVALID', 'pit road samples present'
            elif lap.status != 'VALIDITY_UNKNOWN':
                status, reason = 'INVALID', 'LMU recorded no matching lap time'
            elif (game := game_verdict(verdicts, lap.lap_number, lap.recorded_lap_time_s)) is not None:
                # LMU's own verdict, seen live for this very lap (lmu_lap_verdicts), outranks the edge rule.
                status, reason = ('INVALID', 'LMU invalidated the lap') if game else ('VALID', None)
            elif on_track < .995:
                status, reason = 'INVALID', 'beyond the measured track edge'
            else:
                status = 'VALID'
            used = None
            if fuel is not None and np.isfinite(fuel[first]) and np.isfinite(fuel[last]):
                used = max(0., float(fuel[first] - fuel[last])) if fuel[first] - fuel[last] >= -.1 else None
            lap_pct = pct[window]
            segments.append(LapSegment(
                lap_number=int(lap.lap_number), start_idx=first, end_idx=last, complete=complete,
                valid=status == 'VALID', status=status,
                lap_time_s=lap.recorded_lap_time_s if lap.recorded_lap_time_s else None,
                start_pct=float(lap_pct[0]) if np.isfinite(lap_pct[0]) else 0.,
                end_pct=float(lap_pct[-1]) if np.isfinite(lap_pct[-1]) else 0.,
                on_track_ratio=on_track, pit_samples=pit_samples, fuel_used_l=used, invalid_reason=reason))
        return segments

    @staticmethod
    def _session_info(metadata, length):
        track, car = metadata.get('track_name') or 'LMU', metadata.get('car_name') or 'LMU'
        layout = metadata.get('track_layout') or ''
        # One car per model, whatever the livery (lmu_car_models); the live coach uses the same key.
        from lmu_car_models import car_key, learn
        learn(car, fingerprint=metadata.get('car_model_key'))
        car_id, name = car_key(car, fingerprint=metadata.get('car_model_key'))
        return {
            'WeekendInfo': {'TrackDisplayName': track, 'TrackName': stable_id('track', track),
                            'TrackID': stable_id('track', track),
                            'TrackConfigName': '' if layout in ('', track) else layout,
                            'TrackLength': f'{length / 1000:.4f} km', 'EventType': metadata.get('session_type') or ''},
            'DriverInfo': {'DriverCarIdx': 0, 'DriverSetupName': 'LMU recording',
                           'Drivers': [{'CarIdx': 0, 'CarScreenName': name, 'CarPath': car_id, 'CarID': car_id}]},
            'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': _session_type(metadata.get('session_type')),
                                          'SessionLaps': 'unlimited', 'SessionTime': 'unlimited'}]},
        }
