"""Read-only iRacing SDK sampling and bounded, timestamped question context."""
from __future__ import annotations
from live_coaching import LiveCoaching
from live_energy import EnergyWatch
from live_views import LiveViews
from live_reporting import LiveReporting

from collections import deque
import ctypes
import logging
import math
import random
import statistics
import os
import threading
import time
import uuid
from race_engineer import RaceEngineer, read_race, parse_measure
from spotter import AlongsideSpeed, Spotter
from race_radio import RaceRadio
from radio_policy import TrackSpeechWindows, HOLD_DEFAULT_S
import radio_phrases
from live_corners import LiveCornerCoach, call_seconds, CALL_EARLY_S
from live_formation import FormationBrief, RaceHistory, history_facts
from radio_audio import shown_text
from live_controls import ControlAdvisor
from live_drill import CornerDrill, VOICE_KINDS as DRILL_VOICE
from practice_live import LivePractice
from overlay_session import session_panel
from overlay_cards import OverlayLapEvents
from radio_audio import CALLS as FIXED_CALLS
from live_sectors import LapSectors, RivalSectors, place_name
from live_damage import DamageWatch, PitStopWatch, damage_parts, repair_parts
from live_pit import COLD_TYRE_S, PitKnowledge, PitLossWatch, TyreWatch, stall_estimate, tyre_advice
from timing_log import TimingLog
from traffic_log import TrafficLog, traffic_snapshot

logger = logging.getLogger('iRacingLive')


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def session_identity(weekend, driver_info):
    drivers = driver_info.get('Drivers') or []
    driver = next((d for d in drivers if d.get('CarIdx') == driver_info.get('DriverCarIdx')), {})
    return {
        'session_id': str(weekend.get('SessionID') or ''),
        'series_id': str(weekend.get('SeriesID') or ''),
        'subsession_id': str(weekend.get('SubSessionID') or ''),
        'track_id': str(weekend.get('TrackID') or ''),
        'track_path': weekend.get('TrackName') or '',
        'track_name': weekend.get('TrackDisplayName') or '',
        'track_config': weekend.get('TrackConfigName') or '',
        'car_id': str(driver.get('CarID') or ''),
        'car_path': driver.get('CarPath') or '',
        'car_name': driver.get('CarScreenName') or '',
    }


CHANNELS = {
    'session_time': 'SessionTime', 'session_num': 'SessionNum', 'lap_number': 'Lap',
    'distance_pct': 'LapDistPct', 'speed_mps': 'Speed', 'brake': 'Brake',
    'throttle': 'Throttle', 'steering_rad': 'SteeringWheelAngle', 'steering_lock_ratio': 'SteeringLockRatio', 'gear': 'Gear',
    'fuel_l': 'FuelLevel', 'virtual_energy_pct': 'VirtualEnergyPct', 'brake_bias': 'dcBrakeBias', 'abs_setting': 'dcABS',
    'tc_setting': 'dcTractionControl', 'on_track': 'IsOnTrack', 'on_pit_road': 'OnPitRoad',
    'is_replay': 'IsReplayPlaying', 'track_temp_c': 'TrackTempCrew',
    'air_temp_c': 'AirTemp', 'incidents': 'PlayerCarMyIncidentCount',
    'car_left_right': 'CarLeftRight',
    'session_state': 'SessionState', 'session_flags': 'SessionFlags',
    'session_time_remaining_s': 'SessionTimeRemain', 'session_laps_remaining': 'SessionLapsRemainEx',
    'lap_completed': 'LapCompleted', 'lap_last_s': 'LapLastLapTime', 'lap_best_s': 'LapBestLapTime',
    'lap_current_s': 'LapCurrentLapTime',
    'track_surface': 'PlayerTrackSurface',
    'position': 'PlayerCarPosition', 'class_position': 'PlayerCarClassPosition',
    'engine_warnings': 'EngineWarnings', 'pit_service_status': 'PlayerCarPitSvStatus',
    'pit_repair_left_s': 'PitRepairLeft', 'pit_optional_repair_left_s': 'PitOptRepairLeft',
    'tow_time_s': 'PlayerCarTowTime',
    'abs_active': 'BrakeABSactive', 'yaw_rate': 'YawRate',
    'track_wetness': 'TrackWetness', 'declared_wet': 'WeatherDeclaredWet', 'tire_compound': 'PlayerTireCompound',
    'pit_sv_fuel': 'PitSvFuel', 'pit_sv_flags': 'PitSvFlags',
}


GAME_NAMES = {'iracing': 'iRacing', 'lmu': 'LMU'}
TREAD_CHANNELS = tuple(f'{corner}wear{part}' for corner in ('LF', 'RF', 'LR', 'RR') for part in ('L', 'M', 'R'))


class LiveTelemetry(LiveReporting, LiveViews, LiveCoaching):
    SAMPLE_RATE_HZ, BUFFER_SECONDS = 30, 180

    def __init__(self, sdk_factory=None, clock=time.time):
        self.clock = clock
        self.sdk_factory = sdk_factory
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.samples = deque(maxlen=self.SAMPLE_RATE_HZ * self.BUFFER_SECONDS)
        self.identity = {}
        self.game = 'iracing'
        self._race_read_at, self._race_session = -math.inf, None
        self.run_id = None
        self.overlay_preparation = None
        self.state = 'STOPPED'
        self.message = '실시간 수집을 시작하지 않았습니다.'
        self.hotkey_down = False
        self.hotkey_serial = 0
        self.hotkey_events = deque(maxlen=32)
        self.last_clock = None
        self.lap_fuel = None
        self.fuel_history = deque(maxlen=5)
        self.energy = EnergyWatch()
        self.fuel_feedback = deque(maxlen=3)
        self.speech_windows = TrackSpeechWindows()
        self.speech_hold_s = HOLD_DEFAULT_S   # seconds before braking/steering in which no call starts; below zero is off
        self.pit_wall = RaceEngineer()
        self.spotter = Spotter()
        self.alongside = AlongsideSpeed()     # how fast the car beside us passes (iRacing lap positions)
        self.radio = RaceRadio()
        self.corners = LiveCornerCoach()
        self.controls = ControlAdvisor()
        self._corner_failure = False
        self._control_lap = None
        self._corner_call = (None, [])
        self.formation = FormationBrief()
        self.race_history = RaceHistory()        # in memory unless the app gives it a file
        self.history_lookup = None               # the last analysed session at a track, when the app provides it
        self._history_saved = None
        self._briefed = {}
        self._stint = {}
        self.lap_sectors = LapSectors()
        self.rival_sectors = RivalSectors()
        self.damage = DamageWatch()
        self.pit_stop = PitStopWatch()
        self.pit_knowledge = PitKnowledge()   # in memory until the backend gives it a file beside the database
        self.timing_log = TimingLog()         # raw race timing (F2Time study); written only once the backend gives it a folder
        self.traffic_log = TrafficLog()
        self.tyres = TyreWatch()
        self.pit_loss = PitLossWatch()
        self.drill = CornerDrill()          # today's practice corner with Active Reset (live_drill)
        self.practice = LivePractice()
        self.overlay_laps = OverlayLapEvents()
        self._drill_spoken = 0
        self.pit_rule = None                  # 'CONCURRENT' or 'SEQUENTIAL', as the driver set it for this series
        self._tyres_fitted = None
        self.race_options = {'reserve_laps': .5, 'pit_loss_s': None, 'fuel_save_mode': 'finish'}
        self._logged_failure = -math.inf
        self._link = None                     # last logged link state, so drops leave a trace

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name='iracing-live-reader', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.traffic_log.close()

    def ingest(self, identity, sample, race=None):
        """Also used by isolated tests. Never accepts telemetry through an HTTP endpoint."""
        sample = {key: (None if isinstance(value, float) and not math.isfinite(value) else value) for key, value in sample.items()}
        session_time = number(sample.get('session_time'))
        if session_time is None:
            return
        now = self.clock()
        with self.lock:
            self.overlay_preparation = None
            identity = {**identity, 'session_num': sample.get('session_num')}
            changed = identity != self.identity or (self.last_clock is not None and session_time < self.last_clock - 2)
            if changed or self.run_id is None:
                if self.run_id is not None:
                    self.drill.stop('SESSION_CHANGED')
                    self.practice.reset('SESSION_CHANGED')
                self.samples.clear()
                self.lap_fuel = None
                self.fuel_history.clear()
                self.fuel_feedback.clear()
                self.energy.reset()
                self.speech_windows.reset()
                self.pit_wall.reset()
                self.spotter.reset()
                self.radio.reset()
                self.corners.reset()
                self.controls.reset()
                self.lap_sectors.reset()
                self.overlay_laps.reset()
                self.rival_sectors.reset()
                self.damage.reset()
                self.pit_stop.reset()
                self.tyres.reset()
                self.pit_loss.reset()
                self._tyres_fitted = None
                self._stint = {}
                self.run_id = str(uuid.uuid4())
                self.identity = identity
            self.last_clock = session_time
            if self.samples and self.samples[-1]['session_time'] == session_time:
                return
            self.speech_windows.ingest(sample)
            sample = {**sample, **self.speech_windows.view(sample), 'speech_hold_s': self.speech_hold_s}
            if race is not None:
                self.pit_wall.ingest(race, self.run_id, session_time)
                try:
                    self.timing_log.record(race, self.run_id, session_time)
                except Exception:
                    if not self._briefed.get('timing_log_failed'):
                        self._briefed['timing_log_failed'] = True
                        logger.exception('Race timing log failed; recording stopped for this run')
            self._track_fuel(sample)
            self.energy.ingest(sample)
            sample['fuel_l_per_lap'] = max(self.fuel_history) if self.fuel_history else None
            # No side calls in qualifying (a lone run in iRacing, a private one in LMU) or before a race
            # starts: the grid and the double-file formation lap kept a car beside us the whole time.
            session = str(self.radio.metadata.get('session_type') or '').lower()
            if 'qual' in session or (session == 'race' and sample.get('session_state') in (1, 2, 3)):
                self.spotter.reset()
            else:
                self.spotter.ingest(sample, self.run_id, now)
            self.radio.ingest(sample, self.run_id, now, race)
            self._coach_corners(identity, sample, now)
            try:
                expected = (self.drill.plan or {}).get('identity')
                actual = {'car': str(identity.get('car_path') or identity.get('car_name') or ''),
                          'track': str(identity.get('track_id') or identity.get('track_name') or ''),
                          'config': str(identity.get('track_config') or '')}
                if expected and expected != actual:
                    self.drill.stop('IDENTITY_CHANGED')
                self.drill.ingest(sample, self.radio.metadata.get('track_length_m'))
                kind = str(self.radio.metadata.get('session_type') or sample.get('session_type') or '').lower()
                if kind == 'race' or 'qual' in kind:
                    self.practice.pause()
                else:
                    self.practice.ingest(identity, sample, self.run_id, self.drill)
                self._speak_drill(now)
            except Exception:
                if not self._briefed.get('drill_failed'):
                    self._briefed['drill_failed'] = True
                    logger.exception('Corner drill failed; continuing without it')
            try:
                self._track_sectors(identity, sample, race, session_time)
            except Exception:
                if not self._briefed.get('sectors_failed'):
                    self._briefed['sectors_failed'] = True
                    logger.exception('Sector timing failed; continuing without it')
            self._brief_straight(sample, now)
            self._brief_formation(sample, now)
            self._keep_result(sample)
            self.overlay_laps.ingest(sample, self.run_id, self.radio.metadata.get('session_type') or sample.get('session_type'))
            self.samples.append({**sample, 'session_time': session_time, 'wall_time': now, 'run_id': self.run_id})
            self.state = 'CONNECTED'
            self.message = f'{GAME_NAMES.get(self.game, "iRacing")} 실시간 텔레메트리를 읽고 있습니다.'


    # A straight this long is quiet enough for one status line; lines are this far apart at least.
    # Each further sentence of the same call needs three more seconds of straight.
    BRIEF_STRAIGHT_S, BRIEF_EVERY_S, BRIEF_SENTENCE_S = 8., 40., 3.
    BRIEF_REPEAT_S = {'gap_ahead': 90., 'gap_behind': 90., 'position': 300., 'time': 600., 'leader_lap': 600., 'ahead_lap': 300.,
                      'rival_ahead': 180., 'rival_behind': 180., 'catch': 600., 'defend': 300., 'theoretical': 600.,
                      'rejoin': 180., 'stint_ahead': 300., 'stint_behind': 300.}
    STINT_DIFFERENT_LAPS = 3     # a stint this many laps longer or shorter than ours is worth saying
    REJOIN_FUEL_LAPS = 6         # where a stop now comes out is said from this many laps of fuel left
    QUALI_BRIEFS = ('corner', 'delta', 'theoretical')   # what helps a qualifying lap; other cars are never mentioned


    def spotter_status(self, after=0):
        # The fast event path does not copy the 180-second coaching buffer.
        with self.lock:
            view = self.spotter.view(self.clock(), after, connected=self.state == 'CONNECTED')
            view['game'] = self.game if self.state == 'CONNECTED' else None
            view['radio'] = self.radio.view(self.clock()) if self.state == 'CONNECTED' else {'connected': False, 'events': [], 'history': [], 'capabilities': {}}
            view['settings'] = self.spotter_settings()
            # Only the beeps travel on the fast path; the attempts table is read with /drill.
            view['drill'] = {'active': self.drill.plan is not None, 'serial': self.drill.serial, 'events': self.drill.events[-6:]}
            return view

    def spotter_settings(self):
        return {'voice': radio_phrases.spotter_voice(), 'formation_calls': self.formation.calls,
                'repeat_seconds': self.spotter.repeat_seconds, 'clear_delay': self.spotter.clear_delay,
                'traffic_enabled': self.radio.traffic_enabled, 'pit_box_enabled': self.radio.pit_box_enabled}

    def configure_spotter(self, settings):
        with self.lock:
            self.spotter.repeat_seconds = settings['repeat_seconds']
            if 'formation_calls' in settings:
                self.formation.calls = settings['formation_calls']
            self.spotter.clear_delay = settings['clear_delay']
            self.radio.traffic_enabled = settings['traffic_enabled']
            self.radio.pit_box_enabled = settings['pit_box_enabled']
            return self.spotter_settings()

    def _track_fuel(self, sample):
        pct, fuel = number(sample.get('distance_pct')), number(sample.get('fuel_l'))
        lap = sample.get('lap_number')
        if pct is None or fuel is None or lap is None:
            self.lap_fuel = None
            return
        previous = self.lap_fuel
        usable = (sample.get('on_track') is True and sample.get('is_replay') is False
                  and sample.get('on_pit_road') is False)
        continuous = (previous is not None and 0 < sample['session_time'] - previous['at'] <= 2)
        if previous and fuel > previous['fuel'] + .2:
            self.fuel_history.clear()
            self.fuel_feedback.clear()
        if previous and lap != previous['lap']:
            use = previous['start_fuel'] - fuel
            if (previous['clean'] and usable and continuous and sample.get('incidents') == previous['incidents']
                    and previous['distance'] >= .94 and previous['pct'] >= .95 and pct <= .03
                    and lap == previous['lap'] + 1 and .05 < use < previous['start_fuel']):
                self.fuel_history.append(round(use, 3))
                self.fuel_feedback.append({'lap': previous['lap'], 'use_l': round(use, 3),
                    'target_l': previous['target_l'], 'mode': previous['mode'], 'reserve_laps': previous['reserve_laps'], 'at': sample['session_time']})
            previous = None
        if previous is None:
            view = self.pit_wall.view(sample, max(self.fuel_history) if self.fuel_history else None,
                                      len(self.fuel_history), **self.race_options)
            self.lap_fuel = {'lap': lap, 'pct': pct, 'distance': 0., 'start_fuel': fuel, 'fuel': fuel,
                             'incidents': sample.get('incidents'), 'clean': pct <= .03 and usable,
                             'at': sample['session_time'], 'target_l': view.get('fuel_save_target_l_per_lap'),
                             'reserve_laps': self.race_options.get('reserve_laps', .5),
                             'mode': self.race_options.get('fuel_save_mode', 'finish')}
        else:
            delta = pct - previous['pct']
            if (not continuous or not usable or not 0 <= delta <= .1 or fuel > previous['fuel'] + .2
                    or sample.get('incidents') != previous['incidents'] or int(sample.get('session_flags') or 0) & 0xC108):
                previous['clean'] = False
            previous['distance'] += max(0, delta)
            previous['pct'], previous['fuel'] = pct, fuel
            previous['at'] = sample['session_time']

    def status(self, after=0):
        context = self.context()
        with self.lock:
            return {**{k: v for k, v in context.items() if k != 'recent'},
                    'state': self.state if context['connected'] else ('STALE' if self.state == 'CONNECTED' else self.state),
                    'message': self.message if context['connected'] else f'{GAME_NAMES.get(self.game, "iRacing")} 실시간 데이터 대기 · 저장된 주행 분석은 계속 사용할 수 있습니다.' if self.state == 'CONNECTED' else self.message,
                    'hotkey': 'F8', 'hotkey_supported': os.name == 'nt', 'hotkey_down': self.hotkey_down,
                    'hotkey_serial': self.hotkey_serial, 'hotkey_events': [e for e in self.hotkey_events if e['serial'] > after],
                    'buffer_seconds': self.BUFFER_SECONDS, 'sample_rate_target_hz': self.SAMPLE_RATE_HZ}

    def hotkey_state(self, after=0):
        # Push-to-talk edges only; the event socket must not rebuild the coaching context.
        with self.lock:
            return {'hotkey_serial': self.hotkey_serial,
                    'hotkey_events': [e for e in self.hotkey_events if e['serial'] > after]}

    def _poll_hotkey(self):
        if os.name != 'nt':
            return
        pressed = bool(ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000)
        with self.lock:
            if pressed != self.hotkey_down:
                self.hotkey_down = pressed
                self.hotkey_serial += 1
                self.hotkey_events.append({'serial': self.hotkey_serial, 'down': pressed,
                                           'at': self.clock(), 'context': self.context() if pressed else None})

    def _reset_live(self, reason):
        with self.lock:
            self.overlay_preparation = None
            self.spotter.reset()
            self.alongside.reset()
            self.radio.reset()
            self.drill.stop(reason)
            self.practice.reset(reason)
            self.overlay_laps.reset()

    def _tick(self, sdk):
        """One read of whichever game is connected (iRacing SDK or the LMU adapter)."""
        if not sdk.is_initialized:
            self.state = 'WAITING'
            self.message = getattr(sdk, 'waiting_message', None) or 'iRacing을 실행하고 차량에 탑승하면 연결됩니다.'
            if not sdk.startup():
                return False
        if not sdk.is_connected:
            self._note_link('DISCONNECTED', sdk)
            self._reset_live('DISCONNECTED')
            sdk.shutdown()
            self.state = 'WAITING'
            return None
        sdk.freeze_var_buffer_latest()
        try:
            names = set(sdk.var_headers_names)
            sample = {key: sdk[name] if name in names else None for key, name in CHANNELS.items()}
            # Least tread left on any part of any tyre; the sim only refreshes it in the pit stall.
            sample['tyre_tread_min'] = min(sdk[name] for name in TREAD_CHANNELS) if names.issuperset(TREAD_CHANNELS) else None
            self.game = getattr(sdk, 'game', 'iracing')
            # Replay/spectator data must never masquerade as the driver's current car.
            if sample.get('is_replay') or not sample.get('on_track'):
                self._note_link('REPLAY' if sample.get('is_replay') else 'OFF_TRACK', sdk)
                weekend = sdk['WeekendInfo'] or {}
                self.prepare_overlay(session_identity(weekend, sdk['DriverInfo'] or {}), sample,
                                     parse_measure(weekend.get('TrackLength'), 'distance'))
                with self.lock:
                    self.spotter.reset()
                    self.radio.reset()
                    self.drill.current = self.drill.prev = None
                    self.practice.pause()
                    self.overlay_laps.reset()
                self.state = 'WAITING'
                self.message = '실제 차량 탑승 대기 · 리플레이는 실시간 코칭에 사용하지 않습니다.'
            else:
                self._note_link('DRIVING', sdk)
                weekend, drivers = sdk['WeekendInfo'] or {}, sdk['DriverInfo'] or {}
                identity = session_identity(weekend, drivers)
                identity['game'] = self.game
                sample['side_closing_mps'] = self._side_closing(sdk, names, sample, weekend, drivers)
                sample['side_relative_mps'] = (number(sdk['SideRelativeMps']) if 'SideRelativeMps' in names
                                               else self.alongside.relative if self.game == 'iracing' else None)
                race = None
                race_key = (identity.get('subsession_id'), sample.get('session_num'))
                if self.clock() - self._race_read_at >= 1 or race_key != self._race_session:
                    race = read_race(sdk, names, sample.get('session_num'))
                    self._race_read_at, self._race_session = self.clock(), race_key
                self.ingest(identity, sample, race)
                if self.traffic_log.folder is not None:
                    try:
                        self.traffic_log.record(traffic_snapshot(sdk, names, sample, identity),
                                                self.run_id, sample.get('session_time'))
                    except Exception:
                        self.traffic_log.fail(self.run_id)
        finally:
            sdk.unfreeze_var_buffer_latest()
        return True

    def _note_link(self, link, sdk):
        """Log each change between driving, off track, replay and disconnected.

        Voice and overlay both stop on any of these, so a drive that flickers
        needs the reason in the log; LMU adds the frame fields behind it."""
        if link == self._link:
            return
        previous, self._link = self._link, link
        detail = ''
        if getattr(sdk, 'game', None) == 'lmu':
            lmu = getattr(sdk, 'active', sdk)
            frame = getattr(lmu, 'snapshot', None) or {}
            received = getattr(lmu, 'received_at', None)
            fields = {key: frame.get(key) for key in ('realtime', 'paused', 'control_code', 'in_garage_stall',
                                                      'finish_status', 'in_pits', 'session_phase')}
            detail = (f" unavailable={getattr(lmu, 'unavailable', None)}"
                      f" frame_age={None if received is None else round(lmu.clock() - received, 2)}"
                      f" fields={fields}")
        logger.info('Live link %s -> %s (%s)%s', previous, link, getattr(sdk, 'game', None), detail)

    def _side_closing(self, sdk, names, sample, weekend, drivers):
        """How fast the car beside us passes along our car, for the spotter (None when unknown).
        The LMU adapter measures it from 3D positions; iRacing's comes from every car's lap position."""
        if getattr(sdk, 'game', 'iracing') != 'iracing':
            return number(sdk['SideClosingMps']) if 'SideClosingMps' in names else None
        if 'CarIdxLapDistPct' not in names:
            return None
        player = sdk['PlayerCarIdx'] if 'PlayerCarIdx' in names else drivers.get('DriverCarIdx')
        positions = sdk['CarIdxLapDistPct'] or []
        surfaces = (sdk['CarIdxTrackSurface'] if 'CarIdxTrackSurface' in names else None) or []
        pits = (sdk['CarIdxOnPitRoad'] if 'CarIdxOnPitRoad' in names else None) or []
        cars = {idx: pct for idx, pct in enumerate(positions)
                if idx != player and (idx >= len(surfaces) or surfaces[idx] == 3) and not (idx < len(pits) and pits[idx])}
        return self.alongside(self.clock(), number(sample.get('distance_pct')), cars,
                              parse_measure(weekend.get('TrackLength'), 'distance'))

    def _loop(self):
        sdk = None
        self._race_read_at, self._race_session = -math.inf, None
        try:
            if self.sdk_factory is None:
                if os.name != 'nt':
                    raise RuntimeError('실시간 SDK는 Windows에서 사용할 수 있습니다.')
                import irsdk
                self.sdk_factory = irsdk.IRSDK
            sdk = self.sdk_factory()
        except (ImportError, RuntimeError) as exc:
            self.state = 'UNAVAILABLE'
            self.message = str(exc) if isinstance(exc, RuntimeError) else '실시간 패키지 설치 필요 · 설치.bat를 실행하세요.'
        last_read = None   # end of the last tick that read data; a long gap starves the 2 s LMU window
        while not self.stop_event.is_set():
            self._poll_hotkey()
            if sdk is None:
                self.stop_event.wait(.05)
                continue
            try:
                started = time.monotonic()
                if last_read is not None and started - last_read > 1.:
                    logger.warning('Live loop stalled %.2f s between reads', started - last_read)
                ticked = self._tick(sdk)
                last_read = time.monotonic() if ticked else None
                if ticked is False:
                    self.stop_event.wait(1)
                    continue
                if ticked is None:
                    self.stop_event.wait(.5)
                    continue
            except Exception:
                last_read = None
                # A logic error here looks like a reconnect to the driver; keep the cause.
                failure = time.monotonic()
                if failure - self._logged_failure >= 10:
                    self._logged_failure = failure
                    logger.exception('Live telemetry tick failed; resetting the SDK connection')
                self._reset_live('DISCONNECTED')
                self.state = 'WAITING'
                self.message = '실시간 데이터 연결을 다시 확인하고 있습니다.'
                try:
                    sdk.shutdown()
                except Exception:
                    pass
                self.stop_event.wait(1)
            self.stop_event.wait(1 / 30)
        if sdk is not None:
            sdk.shutdown()
        self.state = 'STOPPED'


def live_facts(context):
    """Measurements with explicit absence, and completed-lap fuel use only."""
    snapshot = context.get('snapshot') or {}
    facts = {k: snapshot.get(k) for k in ('fuel_l', 'gear', 'brake_bias', 'abs_setting', 'tc_setting', 'air_temp_c', 'track_temp_c')}
    speed = number(snapshot.get('speed_mps'))
    facts['speed_kph'] = None if speed is None else round(speed * 3.6, 1)
    facts['fuel_l_per_lap'] = context.get('fuel_l_per_lap')
    facts['fuel_observed_laps'] = context.get('fuel_observed_laps', 0)
    return facts
