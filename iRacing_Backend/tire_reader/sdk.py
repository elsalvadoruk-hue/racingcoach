"""Minimal, read-only iRacing context. No names, accounts or raw session export."""
import math


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


SDK_WHEELS = ('LF', 'RF', 'LR', 'RR')
# Pit service and the tire values iRacing measures at a pit stop (they do not change on track).
PIT_CHANNELS = (('PitSvFlags', 'PlayerCarInPitStall', 'SessionTimeOfDay')
                + tuple(f'PitSv{w}P' for w in SDK_WHEELS)
                + tuple(f'{w}{kind}{side}' for w in SDK_WHEELS for kind in ('wear', 'tempC') for side in 'LMR'))


class SdkReader:
    def __init__(self, sdk):
        self.sdk = sdk
        self.names = set(sdk.var_headers_names or [])
        self.last_tick = self.last_generation = self.identity_update = None
        self.identity = {}

    def _identity(self):
        update = self.sdk._header.session_info_update
        if update != self.identity_update:
            weekend, drivers = self.sdk['WeekendInfo'] or {}, self.sdk['DriverInfo'] or {}
            driver = next((item for item in drivers.get('Drivers', [])
                           if item.get('CarIdx') == drivers.get('DriverCarIdx')), {})
            self.identity = dict(car_path=str(driver.get('CarPath') or '')[:160],
                track_id=finite(weekend.get('TrackID')),
                track_config=str(weekend.get('TrackConfigName') or '')[:160],
                session_id=finite(weekend.get('SessionID')),
                subsession_id=finite(weekend.get('SubSessionID')))
            from setup_capabilities import fixed_setup, session_setup_values
            self.identity['fixed_setup'] = fixed_setup(weekend)
            try:
                setup = session_setup_values(self.sdk['CarSetup'] or {})
            except (KeyError, TypeError, AttributeError):
                setup = {}
            self.identity['garage_cold_pressure_kpa'] = {w:setup.get('pressure_'+sdk_wheel)
                for w,sdk_wheel in zip(('FL','FR','RL','RR'),('lf','rf','lr','rr'))}
            try:
                sessions = (self.sdk['SessionInfo'] or {}).get('Sessions', [])
            except (KeyError, TypeError):
                sessions = []
            self.session_types = {s.get('SessionNum'): str(s.get('SessionType') or '')[:40]
                                  for s in sessions if isinstance(s, dict)}
            self.identity_update = update
        return self.identity

    def snapshot(self, generation):
        sdk = self.sdk
        try:
            sdk.freeze_var_buffer_latest()
            tick = sdk._var_buffer_latest.tick_count
            row = {name: sdk[name] if name in self.names else None for name in
                   ('IsOnTrack', 'IsOnTrackCar', 'IsReplayPlaying', 'IsInGarage',
                    'SessionTime', 'SessionNum', 'DisplayUnits', 'Lap', 'AirTemp',
                    'TrackTempCrew', 'OnPitRoad', 'SteeringWheelAngle', 'Throttle', 'Brake', 'Speed',
                    'LFcoldPressure', 'RFcoldPressure', 'LRcoldPressure', 'RRcoldPressure',
                    'FuelLevel', 'LapLastLapTime', 'TrackWetness', 'PlayerTireCompound') + PIT_CHANNELS}
            row.update(self._identity())
            row['session_type'] = self.session_types.get(row.get('SessionNum'))
            row.update(generation=generation,
                       fresh=generation != self.last_generation or tick != self.last_tick)
            self.last_tick, self.last_generation = tick, generation
            return row
        finally:
            sdk.unfreeze_var_buffer_latest()
