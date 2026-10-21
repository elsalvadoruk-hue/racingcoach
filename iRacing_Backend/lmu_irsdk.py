"""LMU shared memory presented through the iRacing SDK interface.

The live coach (spotter, race radio, pit wall, formation brief, corner coach,
F8 questions and the overlay) is written against pyirsdk's ``IRSDK`` object.
``LmuIrsdk`` reads the LMU bridge frames and answers the same variable and
session-info names, so the whole iRacing live stack runs unchanged on LMU.

Values LMU does not publish stay ``None`` (absent from ``var_headers_names``):
the shared code already treats a missing channel as "unknown" and stays silent.
"""
from __future__ import annotations

from collections import deque
import math
import re
import time

from lmu_frame import GEOMETRY, normalise as normalise_frame, normalise_traffic
from lmu_journal import FrameJournal
from lmu_race_signals import last_lap_signal, yellow_signal

# LMU mSession codes.
SESSION_TYPES = [(0, 0, 'Offline Testing'), (1, 4, 'Practice'), (5, 8, 'Open Qualify'),
                 (9, 9, 'Warmup'), (10, 13, 'Race')]
# LMU mGamePhase -> iRacing SessionState (1 get in car, 2 warmup, 3 parade, 4 racing, 5 checkered, 6 cool down).
PHASE_STATE = {0: 1, 1: 2, 2: 2, 3: 3, 4: 3, 5: 4, 6: 4, 7: 4}
STALE_SECONDS = 2.          # no new telemetry for this long => disconnected
OFF_TRACK_MARGIN_M = 2.     # beyond the measured track edge
CHANNEL_ARRAY = 128
SPOTTER_MIN_SPEED_MPS = 5.  # parked on the grid / crawling: no side calls (iRacing's "off")
NEARBY_M = 60.
SCORING_HOLD_SECONDS = 4.   # scoring (~5 Hz) stalls up to ~2.2 s in real formation laps; hold, never invent
SCORING_KEYS = (
    'scoring_et', 'scoring_lap_start_et', 'total_laps', 'last_lap_time', 'best_lap_time',
    'outstanding_penalties', 'lap_distance_m', 'track_length_m', 'session_type_code', 'session_phase',
    'session_remaining_s', 'session_end_et', 'session_max_laps', 'overall_position', 'class_position',
    'class_vehicle_count', 'vehicle_count', 'control_code', 'primary_flag', 'yellow_flag_state',
    'sector_flags', 'sector', 'pit_state', 'finish_status', 'under_yellow', 'in_garage_stall',
    'rain_ratio', 'track_wetness_ratio', 'ambient_temp_c', 'track_temp_c', 'count_lap_flag', 'in_pits',
    'scoring_cars', 'start_light', 'start_red_lights', 'class_id', 'pit_box_lap_distance_m')              # cars further away cannot overlap; their staleness is irrelevant


def session_type(code):
    for low, high, name in SESSION_TYPES:
        if isinstance(code, int) and low <= code <= high:
            return name
    return None


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')


def stable_id(prefix, text):
    """A stable string ID for corner/pit memories keyed by track or car."""
    return f'lmu-{prefix}-{slug(text)}' if slug(text) else ''


def _local(player, row):
    delta = [row['position_m'][i] - player['position_m'][i] for i in range(3)]
    return [sum(player['orientation'][j][i] * delta[j] for j in range(3)) for i in range(3)]


class LeftRight:
    """Measured side overlap as iRacing's CarLeftRight (CrewChief geometry).

    1 clear, 2 car left, 3 car right, 4 both, 5 two left, 6 two right.
    ``None`` when coverage is incomplete: the spotter then never calls clear.
    """

    SPAN_S, KEEP_S = .2, 1.   # closing speed over at least this long, from the positions kept this long

    def __init__(self):
        self.reset()

    def reset(self):
        self.occupied = set()
        self.tracks = {}
        self.closing = None       # m/s the slowest car beside us moves along our car; None when unknown
        self.relative = None      # the same, signed: + that car pulls ahead of us, - we pass it

    def __call__(self, traffic):
        if not traffic or traffic.get('status') != 'AVAILABLE':
            self.occupied, self.closing, self.relative = set(), None, None
            return None
        player = traffic['player']
        now = player.get('telemetry_et')
        sides, occupied, tracks, closing, relative = [[], []], set(), {}, None, None
        for row in traffic['vehicles']:
            x, y, z = _local(player, row)
            track = self.tracks.get(row['vehicle_id']) or deque()
            if now is not None:
                track.append((now, z))
                while track and now - track[0][0] > self.KEEP_S:
                    track.popleft()
            tracks[row['vehicle_id']] = track
            margin = GEOMETRY['exit_margin_m'] if row['vehicle_id'] in self.occupied else 0
            if (abs(y) > GEOMETRY['maximum_height_difference_m']
                    or abs(z) > GEOMETRY['car_length_m'] + margin
                    or abs(x) > GEOMETRY['lateral_warning_distance_m'] + margin):
                continue
            if abs(x) < .5 and abs(z) < GEOMETRY['car_length_m'] / 2:
                self.tracks, self.closing, self.relative = tracks, None, None
                return None  # coincident centres give no side; never clear
            if abs(x) < GEOMETRY['car_width_m'] * .7:
                # In line ahead or behind (nose to tail in a tow, bumping): a car beside us cannot be
                # this close sideways without touching. Called a side car before (2026-10-21).
                continue
            sides[0 if x > 0 else 1].append(abs(x))
            occupied.add(row['vehicle_id'])
            past = next((p for p in reversed(track) if now - p[0] >= self.SPAN_S), None) if now is not None else None
            if past is not None:
                # The car's local forward is -z: a car pulling ahead has a falling z.
                signed = -(z - past[1]) / (now - past[0])
                if closing is None or abs(signed) < closing:
                    closing, relative = abs(signed), signed
        self.occupied, self.tracks, self.closing, self.relative = occupied, tracks, closing, relative
        left, right = [2 if len(xs) > 1 and max(xs) - min(xs) >= GEOMETRY['car_width_m']
                       else 1 if xs else 0 for xs in sides]
        return 4 if left and right else 5 if left > 1 else 6 if right > 1 else 2 if left else 3 if right else 1


def nearby_traffic(raw, telemetry_et, vehicle_id):
    """Strict coverage over the cars that could be alongside.

    One stale car anywhere on track used to withdraw the whole picture. Cars
    more than ``NEARBY_M`` away cannot overlap, so only the near ones must be
    fresh; any doubt about a near car still withdraws coverage (no clear).
    """
    if not isinstance(raw, dict) or raw.get('status') != 'AVAILABLE':
        return None
    player = raw.get('player') or {}
    origin = player.get('position_m')
    if not isinstance(origin, list) or len(origin) != 3:
        return None
    near = []
    for row in raw.get('vehicles') or []:
        position = row.get('position_m') if isinstance(row, dict) else None
        if not isinstance(position, list) or len(position) != 3:
            return None
        try:
            if math.dist(position, origin) > NEARBY_M:
                continue
        except (TypeError, ValueError):
            return None
        near.append(row)
    traffic = normalise_traffic({**raw, 'vehicles': near}, telemetry_et, vehicle_id)
    return traffic if traffic.get('status') == 'AVAILABLE' else None


def virtual_energy_pct(raw):
    """LMU's mVirtualEnergy is the remaining share of the stint allowance as a fraction (0-1).

    TinyPedal reads the same field as a fraction and multiplies by 100, and LMU's own
    telemetry recordings store 'Virtual Energy' in % (both checked 2026-10-21).
    """
    if type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 1:
        return None
    return raw * 100.


def _fraction(distance, length):
    if distance is None or not length:
        return None
    return (distance % length) / length


def _surface(row, player_snapshot=None):
    """iRacing TrkLoc: 0 off track, 1 pit stall, 2 pit lane, 3 on track."""
    source = player_snapshot or row
    if source.get('in_garage_stall'):
        return 1
    if source.get('in_pits'):
        return 2
    lateral, edge = row.get('path_lateral_m'), row.get('track_edge_m')
    if lateral is not None and edge and abs(lateral) > abs(edge) + OFF_TRACK_MARGIN_M:
        return 0
    return 3


def _wetness(ratio):
    """LMU path wetness 0..1 -> iRacing TrackWetness 1 (dry) .. 7 (extremely wet)."""
    if ratio is None:
        return None
    for limit, level in ((.01, 1), (.05, 2), (.15, 3), (.3, 4), (.5, 5), (.75, 6)):
        if ratio < limit:
            return level
    return 7


class LmuIrsdk:
    """pyirsdk ``IRSDK`` lookalike over the LMU shared-memory bridge."""

    game = 'lmu'

    def __init__(self, source_factory=None, clock=time.monotonic, normalised=False, journal=None):
        self.source_factory = source_factory
        self.journal = journal or FrameJournal()
        self.normalised = normalised   # replay sources yield already-normalised frames
        self.clock = clock
        self.source = self._candidate = None
        self.is_initialized = False
        self.snapshot = None
        self.received_at = None
        self.last_et = None
        self.unavailable = None      # last bridge error code, for the status line
        self.vars = {}
        self.info = {}
        self.sessions = {}
        self.durations = {}
        self.steering_learned = set()
        from lmu_lap_verdicts import LapVerdicts
        self.lap_verdicts = LapVerdicts()
        self.car_ids = {}            # (entry, model) -> (CarID, name)
        self.scoring = None          # last frame with aligned scoring, for short gaps
        self.state = 1
        self.left_right = LeftRight()
        self.position_et = None

    # --- connection -----------------------------------------------------
    def startup(self, *_args, **_kwargs):
        if self.source is None:
            try:
                if self._candidate is None:
                    factory = self.source_factory
                    if factory is None:
                        from lmu_shared_memory import SharedMemorySource
                        factory = SharedMemorySource
                    self._candidate = factory()
                opened = getattr(self._candidate, 'open', None)
                if opened:
                    opened()
            except Exception as exc:  # GAME_NOT_RUNNING, NATIVE_NOT_BUILT, ...
                self.unavailable = getattr(exc, 'code', type(exc).__name__)
                return False
            self.source, self._candidate, self.unavailable = self._candidate, None, None
        self.is_initialized = True
        self.received_at = self.clock()
        return True

    @property
    def is_connected(self):
        return (self.is_initialized and self.received_at is not None
                and self.clock() - self.received_at < STALE_SECONDS)

    def shutdown(self):
        source, self.source = self.source, None
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        self.is_initialized = False
        self.snapshot = self.last_et = self.received_at = self.scoring = None
        self.vars, self.info = {}, {}
        self.left_right.reset()

    # --- frame access ---------------------------------------------------
    def freeze_var_buffer_latest(self):
        if self.source is None:
            return
        try:
            raw = self.source.read(timeout_ms=20)
        except Exception as exc:
            self.unavailable = getattr(exc, 'code', type(exc).__name__)
            self.received_at = None   # reported as disconnected; the loop shuts us down
            return
        if raw is None:
            return
        snapshot = raw if self.normalised else normalise_frame(raw)
        if snapshot is None or snapshot.get('telemetry_et') is None:
            return
        traffic = snapshot.get('traffic') if self.normalised else nearby_traffic(
            raw.get('traffic'), snapshot.get('telemetry_et'), snapshot.get('vehicle_id'))
        if (traffic or {}).get('status') != 'AVAILABLE':
            traffic = None
        if snapshot['telemetry_et'] == self.last_et and snapshot.get('scoring_updated') is not True:
            return
        self.last_et = snapshot['telemetry_et']
        self.received_at = self.clock()
        if not self.normalised:
            # Journal exactly what was read, with the traffic actually used.
            self.journal.write({**snapshot, 'traffic': traffic or {'status': 'UNAVAILABLE'}})
            try:
                self.lap_verdicts.observe(snapshot)   # LMU's own lap verdict, for the recording analysis
            except Exception:
                pass
        snapshot = self._hold_scoring(snapshot)
        self.snapshot = snapshot
        self._translate(snapshot, traffic)

    def _hold_scoring(self, s):
        """Bridge frames without aligned scoring (and so without the session's
        number in its key) are kept on the last scoring for up to two seconds
        of continuous telemetry (the limit below); longer gaps stay unknown."""
        held = self.scoring
        if s.get('session_phase') is not None:
            self.scoring = {key: s.get(key) for key in SCORING_KEYS}
            self.scoring.update(session_key=s.get('session_key'), vehicle_id=s.get('vehicle_id'))
            return s
        if (held and held['vehicle_id'] == s.get('vehicle_id') and held.get('scoring_et') is not None
                and 0 <= s['telemetry_et'] - held['scoring_et'] <= SCORING_HOLD_SECONDS):
            return {**s, **held}
        return s

    def unfreeze_var_buffer_latest(self):
        pass

    @property
    def var_headers_names(self):
        return [name for name, value in self.vars.items() if value is not None]

    def __getitem__(self, name):
        if name in self.info:
            return self.info[name]
        return self.vars.get(name)

    # --- translation ----------------------------------------------------
    def _session_num(self, key):
        if key not in self.sessions:
            self.sessions[key] = len(self.sessions)
        return self.sessions[key]

    def _flags(self, s):
        phase, flags = s.get('session_phase'), 0
        if phase == 5 and s.get('primary_flag') == 0:
            flags |= 0x4
        if s.get('primary_flag') == 6:
            flags |= 0x20
        if yellow_signal(s):
            flags |= 0x8
        yellow_state = s.get('yellow_flag_state')
        if phase == 6 or isinstance(yellow_state, int) and 1 <= yellow_state <= 6:
            flags |= 0x4000 | (0x8000 if yellow_state == 1 else 0)
        if phase == 7 or yellow_state == 7:
            flags |= 0x10
        if phase == 8:
            flags |= 0x1
        if last_lap_signal(s):
            flags |= 0x2
        if phase == 3:
            flags |= 0x10000000          # start hidden while on the formation lap
        if phase == 4:
            flags |= 0x20000000 | (0x40000000 if (s.get('start_light') or 0) > 0 else 0)
        if (s.get('outstanding_penalties') or 0) > 0:
            flags |= 0x10000
        if s.get('finish_status') == 3:
            flags |= 0x20000
        return flags

    def _state(self, s):
        phase = s.get('session_phase')
        if phase == 8:
            self.state = 5 if s.get('finish_status') == 0 else 6
        elif phase in PHASE_STATE:
            self.state = PHASE_STATE[phase]
        return self.state  # paused (9) keeps the previous state

    def _car(self, car, model):
        """(CarID, name) of the model, as the recording analysis keys it (lmu_car_models)."""
        cached = self.car_ids.get((car, model))
        if cached is None:
            from lmu_car_models import car_key, learn
            if car and model:
                learn(car, model=model)
            cached = self.car_ids[(car, model)] = car_key(car, model)
        return cached

    def _translate(self, s, traffic):
        length = s.get('track_length_m')
        track, car = s.get('track_name') or '', s.get('car_name') or ''
        kind = session_type(s.get('session_type_code'))
        key = s.get('session_key') or ''
        num = self._session_num(key)
        cars = s.get('scoring_cars') or []
        scoring_et, telemetry_et = s.get('scoring_et'), s.get('telemetry_et')
        lag = (telemetry_et - scoring_et) if scoring_et is not None and telemetry_et is not None else 0.
        lag = min(max(lag, 0.), SCORING_HOLD_SECONDS)
        player_id = s.get('vehicle_id')
        player_row = next((c for c in cars if c['vehicle_id'] == player_id), {})

        # Scoring updates ~5 Hz; extrapolate lap distance with measured speed
        # so 30 Hz corner/sector timing does not step. Never cross the line
        # before scoring confirms the new lap.
        distance = s.get('lap_distance_m')
        if distance is not None and length and s.get('speed_mps') is not None:
            distance = min(distance + s['speed_mps'] * lag, length * .9999)
        on_track = (s.get('realtime') is True and s.get('paused') is not True
                    and s.get('control_code') == 0 and s.get('in_garage_stall') is not True
                    and s.get('finish_status') in (0, 1, None))
        steering = s.get('steering_rad')
        ratio = s.get('steering_ratio')
        car_id, car_label = self._car(car, s.get('vehicle_model'))
        if steering is not None and ratio is not None and abs(ratio) > .1 and car and car not in self.steering_learned:
            # rad = ratio * half range; remember the full range in degrees once per car and run.
            self.steering_learned.add(car)
            from lmu_steering import remember
            remember(car_label, math.degrees(abs(steering / ratio)) * 2)
        warnings = None
        if s.get('overheating') is not None or s.get('speed_limiter_active') is not None:
            warnings = ((0x1 if s.get('overheating') else 0) | (0x10 if s.get('speed_limiter_active') else 0)
                        | (0x100 if s.get('parts_detached') else 0))
        wet = s.get('front_tyre_wet')
        maximum = s.get('session_max_laps')
        leader = next((c for c in cars if c.get('overall_position') == 1), None)
        laps_left = (max(0, maximum - (leader.get('total_laps') or 0))
                     if maximum and leader and leader.get('total_laps') is not None else None)
        # Same player checks as the iRacing spotter (on track, not in pits);
        # the formation lap is covered too, as in iRacing.
        if not (on_track and s.get('in_pits') is False and s.get('session_key')):
            traffic = None
        wetness = s.get('track_wetness_ratio')
        rain = s.get('rain_ratio')
        self.vars = {
            'SessionTime': telemetry_et, 'SessionNum': num, 'SessionState': self._state(s),
            'SessionFlags': self._flags(s), 'SessionTimeRemain': s.get('session_remaining_s'),
            'SessionLapsRemainEx': laps_left,
            'Lap': None if s.get('total_laps') is None else int(s['total_laps']) + 1,
            'LapCompleted': None if s.get('total_laps') is None else int(s['total_laps']),
            'LapDistPct': _fraction(distance, length), 'LapDist': distance,
            'LapLastLapTime': s.get('last_lap_time') or None, 'LapBestLapTime': s.get('best_lap_time'),
            'LapCurrentLapTime': (telemetry_et - s['lap_start_et']
                                  if telemetry_et is not None and s.get('lap_start_et') is not None else None),
            'Speed': s.get('speed_mps'), 'Throttle': s.get('throttle_ratio'), 'Brake': s.get('brake_ratio'),
            'Clutch': s.get('clutch_ratio'),
            # rF2/LMU steering is positive to the right; iRacing positive to the left.
            'SteeringWheelAngle': None if steering is None else -steering,
            # Not an iRacing variable: steering as a share of full lock, for the quiet-moment check
            # when the wheel reports no range (and so no angle).
            'SteeringLockRatio': None if ratio is None else -ratio,
            'Gear': s.get('gear'), 'RPM': s.get('engine_rpm'), 'FuelLevel': s.get('fuel_l'),
            'dcBrakeBias': s.get('brake_bias'), 'dcABS': s.get('abs_setting'),
            'dcTractionControl': s.get('tc_setting'),
            'IsOnTrack': on_track, 'OnPitRoad': s.get('in_pits'), 'IsReplayPlaying': False,
            'TrackTempCrew': s.get('track_temp_c'), 'AirTemp': s.get('ambient_temp_c'),
            'CarLeftRight': (0 if (s.get('speed_mps') or 0) < SPOTTER_MIN_SPEED_MPS and traffic
                             else self.left_right(traffic)),
            # Not an iRacing variable: how fast the car beside us passes, for the spotter (live_telemetry).
            'SideClosingMps': self.left_right.closing,
            'SideRelativeMps': self.left_right.relative,
            # Not an iRacing variable either: LMU virtual energy left, in % (live_energy).
            'VirtualEnergyPct': virtual_energy_pct(s.get('virtual_energy_raw')),
            'PlayerTrackSurface': _surface(player_row, s) if player_row else None,
            'PlayerCarPosition': s.get('overall_position'), 'PlayerCarClassPosition': s.get('class_position'),
            'EngineWarnings': warnings, 'BrakeABSactive': s.get('abs_active'), 'YawRate': s.get('yaw_rate'),
            'TrackWetness': _wetness(wetness),
            'WeatherDeclaredWet': (None if wetness is None and rain is None
                                   else (wetness or 0) >= .1 or (rain or 0) >= .1),
            'PlayerTireCompound': None if wet is None else int(wet),
        }
        size = max([CHANNEL_ARRAY] + [c['vehicle_id'] + 1 for c in cars])
        arrays = {name: [None] * size for name in (
            'CarIdxLapCompleted', 'CarIdxLapDistPct', 'CarIdxPosition', 'CarIdxClassPosition',
            'CarIdxLastLapTime', 'CarIdxOnPitRoad', 'CarIdxTrackSurface', 'CarIdxBestLapTime', 'CarIdxF2Time')}
        drivers = []
        for c in cars:
            i = c['vehicle_id']
            dist = c.get('lap_distance_m')
            if dist is not None and c.get('speed_mps') is not None and length:
                dist = min(dist + c['speed_mps'] * lag, length * .9999)
            arrays['CarIdxLapCompleted'][i] = c.get('total_laps')
            arrays['CarIdxLapDistPct'][i] = _fraction(dist, length)
            arrays['CarIdxPosition'][i] = c.get('overall_position') or 0
            arrays['CarIdxClassPosition'][i] = c.get('class_position') or 0
            arrays['CarIdxLastLapTime'][i] = c.get('last_lap_time') or -1
            arrays['CarIdxBestLapTime'][i] = c.get('best_lap_time') or -1
            arrays['CarIdxOnPitRoad'][i] = c.get('in_pits')
            arrays['CarIdxTrackSurface'][i] = -1 if c.get('control_code') == -1 else _surface(
                c, s if i == player_id else None)
            arrays['CarIdxF2Time'][i] = c.get('time_behind_leader_s')
            class_id = c.get('class_id')
            drivers.append({'CarIdx': i, 'UserName': f'{c.get("overall_position") or i}위 차량',
                            'CarClassID': class_id, 'IsSpectator': 0, 'CarIsPaceCar': 0})
        self.vars.update(arrays)
        if not cars and player_id is not None:
            drivers.append({'CarIdx': player_id, 'CarClassID': s.get('class_id'), 'IsSpectator': 0})
        for d in drivers:
            if d['CarIdx'] == player_id:
                # Same IDs as the recording analysis (lmu_ibt) so corners and practice match.
                d.update(CarID=car_id, CarPath=car_id, CarScreenName=car_label, UserName='나')
        pit = s.get('pit_box_lap_distance_m')
        tires = ([{'TireIndex': 0, 'TireCompoundType': 'Dry'}, {'TireIndex': 1, 'TireCompoundType': 'Wet'}]
                 if wet is not None else [])
        # Before the green the remaining time is the whole race; afterwards
        # the SDK's session end time (practice/qualifying) is the length.
        remaining = s.get('session_remaining_s')
        racing = isinstance(s.get('session_type_code'), int) and s['session_type_code'] >= 10
        if racing and s.get('session_phase') in (0, 3, 4) and remaining:
            self.durations[key] = max(self.durations.get(key, 0), remaining)
        duration = self.durations.get(key) or (s.get('session_end_et') if s.get('session_phase') in (5, 6, 7, 8) else None)
        self.info = {
            'WeekendInfo': {'SessionID': key[:16], 'SubSessionID': key[16:32], 'SeriesID': '',
                            'TrackID': stable_id('track', track), 'TrackName': stable_id('track', track),
                            'TrackDisplayName': track, 'TrackConfigName': '',
                            'TrackLength': f'{length / 1000:.4f} km' if length else None,
                            'WeekendOptions': {}},
            'DriverInfo': {'DriverCarIdx': player_id, 'Drivers': drivers,
                           'DriverCarFuelMaxLtr': s.get('fuel_capacity_l'), 'DriverCarMaxFuelPct': 1,
                           'DriverPitTrkPct': _fraction(pit, length) if pit is not None else None,
                           'DriverTires': tires},
            'SessionInfo': {'Sessions': [{'SessionNum': num, 'SessionType': kind,
                                          'SessionLaps': str(maximum) if maximum else 'unlimited',
                                          'SessionTime': f'{duration:.4f} sec' if duration else 'unlimited'}]},
        }

    def status_message(self):
        messages = {
            'NATIVE_NOT_BUILT': 'LMU 실시간 연결 파일을 자동으로 준비합니다.',
            'ABI_MISMATCH': 'LMU 버전에 맞는 연결 파일을 확인 중입니다.',
            'CONNECTION_PREPARING': 'LMU 실시간 연결 파일을 자동으로 받는 중입니다.',
            'CONNECTION_UPDATE_PENDING': '현재 LMU 버전용 연결 파일을 준비 중입니다. 준비되면 자동으로 다시 연결합니다.',
            'CONNECTION_DOWNLOAD_FAILED': '인터넷 연결을 확인하고 LMU 실시간 연결을 다시 켜 주세요.',
        }
        return messages.get(self.unavailable)


_shared = None


def shared():
    """The one LMU adapter the live coach uses (and the journal routes control)."""
    global _shared
    if _shared is None:
        _shared = LmuIrsdk()
    return _shared
