"""Read-only pit wall. Calculations and report decisions do not call an LLM."""
from collections import deque
import math
import os
import statistics
import re
from radio_policy import report_window
from radio_audio import CALLS, speech_fields
from spotter_context import estimated_gap_s
import radio_phrases

# LMU virtual energy calls. The unit (0-1 fraction, lmu_irsdk.virtual_energy_pct) was checked
# against TinyPedal and LMU's own recordings; the user turned the calls on (2026-10-21).
ENERGY_CALLS_ENABLED = True


def target_position(text):
    match = re.search(r'(?:클래스\s*)?(\d+)\s*(?:위|등)', text)
    return int(match[1]) if match else 1 if any(w in text for w in ('선두', '리더')) else None

ROLES = {
    'coach': {'label': '주행 코치', 'voice': 'ko-KR-Chirp3-HD-Algenib'},
    'race': {'label': '레이스 엔지니어', 'voice': 'ko-KR-Chirp3-HD-Charon'},
    'strategy': {'label': '전략 엔지니어', 'voice': 'ko-KR-Chirp3-HD-Orus'},
}


def role_voice(role):
    default = 'ko-KR-Chirp3-HD-Fenrir' if role == 'spotter' else ROLES.get(role, ROLES['coach'])['voice']
    key = 'IRACING_TTS_VOICE' if role == 'coach' else f'IRACING_TTS_{role.upper()}_VOICE'
    return os.getenv(key, '').strip() or default


def numeric(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def positive(value):
    value = numeric(value)
    return value if value is not None and value > 0 else None


def limit_value(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 < result < 32767 else None
    except (TypeError, ValueError):
        return None


SCALARS = {'state': 'SessionState', 'flags': 'SessionFlags', 'time_remaining_s': 'SessionTimeRemain',
           'laps_remaining': 'SessionLapsRemainEx', 'lap_last_s': 'LapLastLapTime',
           'lap_completed': 'LapCompleted', 'position': 'PlayerCarPosition', 'class_position': 'PlayerCarClassPosition'}
ARRAYS = {'lap_completed': 'CarIdxLapCompleted', 'distance_pct': 'CarIdxLapDistPct',
          'position': 'CarIdxPosition', 'class_position': 'CarIdxClassPosition',
          'last_lap_s': 'CarIdxLastLapTime', 'on_pit_road': 'CarIdxOnPitRoad', 'track_surface': 'CarIdxTrackSurface',
          'est_time_s': 'CarIdxEstTime', 'best_lap_s': 'CarIdxBestLapTime', 'f2_time_s': 'CarIdxF2Time'}


def read_race(sdk, names, session_num):
    """Called once per second from the frozen SDK buffer, absent channels stay absent."""
    def metadata(key):
        try:
            return sdk[key] or {}
        except (KeyError, TypeError):
            return {}
    info = metadata('DriverInfo')
    sessions = metadata('SessionInfo').get('Sessions') or []
    session = next((s for s in sessions if s.get('SessionNum') == session_num), {})
    data = {key: numeric(sdk[name]) if name in names else None for key, name in SCALARS.items()}
    data.update(game=getattr(sdk, 'game', 'iracing'), session_type=session.get('SessionType'), total_laps=limit_value(session.get('SessionLaps')),
                player_idx=info.get('DriverCarIdx'), tank_capacity_l=tank_capacity(info))
    weekend = metadata('WeekendInfo')
    options = weekend.get('WeekendOptions') or metadata('WeekendOptions')
    data['incident_limit'] = limit_value(options.get('IncidentLimit')) if isinstance(options, dict) else None
    tires = [t for t in (info.get('DriverTires') or []) if isinstance(t, dict)]
    data['tire_types_known'] = bool(tires)
    data['wet_compounds'] = [t.get('TireIndex') for t in tires
                             if any(word in str(t.get('TireCompoundType') or '').lower() for word in ('wet', 'rain'))]
    data['track_name'] = weekend.get('TrackDisplayName') or weekend.get('TrackName') or ''
    data['session_duration_s'] = parse_measure(session.get('SessionTime'), 'seconds')
    data['pit_speed_limit_kph'] = parse_measure(weekend.get('TrackPitSpeedLimit'), 'speed')
    data['track_length_m'] = parse_measure(weekend.get('TrackLength'), 'distance')
    # SDK DriverInfo describes the player; this field is not in Drivers[].
    pit_pct = numeric(info.get('DriverPitTrkPct'))
    data['pit_box_pct'] = pit_pct if pit_pct is not None and 0 <= pit_pct < 1 else None
    arrays = {key: sdk[name] if name in names else [] for key, name in ARRAYS.items()}
    cars = []
    for driver in (info.get('Drivers') or [])[:128]:
        idx = driver.get('CarIdx')
        if not isinstance(idx, int) or idx < 0 or driver.get('IsSpectator'):
            continue
        car = {'idx': idx, 'name': driver.get('UserName') or f'차량 {idx}', 'class_id': driver.get('CarClassID'),
               'is_pace_car': bool(driver.get('CarIsPaceCar')),
               'class_name': driver.get('CarClassShortName') or driver.get('CarClassName'),
               'class_est_lap_s': positive(driver.get('CarClassEstLapTime')),
               # The pace car is listed among the drivers with no rating of its own.
               'irating': None if driver.get('CarIsPaceCar') else positive(driver.get('IRating'))}
        for key, values in arrays.items():
            value = values[idx] if values is not None and idx < len(values) else None
            car[key] = bool(value) if key == 'on_pit_road' and value is not None else numeric(value)
        cars.append(car)
    data['cars'] = cars
    return data


def parse_measure(value, kind):
    # YAML unit strings only. Unitless / unlimited values remain unknown.
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(km/h|kph|mph|m/s|sec|s|min|minutes|km|m)\s*', value, re.I)
    if not match:
        return None
    n, unit = float(match[1]), match[2].lower()
    units = {'speed': {'km/h': 1, 'kph': 1, 'mph': 1.609344, 'm/s': 3.6},
             'seconds': {'sec': 1, 's': 1, 'min': 60, 'minutes': 60}, 'distance': {'km': 1000, 'm': 1}}
    factor = units.get(kind, {}).get(unit)
    return n * factor if factor is not None and n > 0 else None


def choose_role(text, history=()):
    compact = ''.join(text.split())
    if any(w in compact for w in ('수리', '데미지', '대미지', '손상', '패널티', '경고등', '오일압력', '수온', '유온', '고장', '검은깃발')):
        return 'race'
    if target_position(text) is not None or any(w in compact for w in ('거리차', '따라와', '따라오', '쫓아', '앞차', '뒤차', '뒤에서', '앞에서', '뒤에있는차', '앞에있는차')):
        return 'strategy' if any(w in compact for w in ('피트', '급유')) else 'race'
    if any(w in compact for w in ('랩타임', '랩기록', '브리핑', '경기전설명', '피트제한속도', '피트속도제한', '옆에차', '왼쪽차', '오른쪽차')):
        return 'race' if any(w in compact for w in ('왜', '분석', '자세히', '느려', '빨라')) else 'spotter'
    if any(w in compact for w in ('순위', '몇등')) and not any(w in compact for w in ('피트', '전략', '앞차', '뒤차', '간격')):
        return 'spotter'
    if any(word in compact for word in ('연료', '연비', '절약', '급유', '피트', '전략', '완주가능', '끝까지', '언더컷', '오버컷', '몇리터', '타이어교체')):
        return 'strategy'
    if any(word in compact for word in ('앞차', '뒤차', '순위', '몇등', '간격', '페이스', '황기', '경기상황', '레이스상황', '남은경기', '레이스얼마', '경기얼마', '다른클래스', '멀티클래스', '트래픽')):
        return 'race'
    if any(word in compact for word in ('왜', '자세히', '상세히', '그건', '그럼', '얼마나', '몇리터', '몇바퀴')) and history:
        return history[-1].get('engineer', 'coach')
    return 'coach'


class RaceEngineer:
    def __init__(self):
        self.auto_pit_loss_s = None   # the measured pit loss (live_views), for the leader's pending stop
        self.reset()

    def reset(self):
        self.race = {}
        self.previous = {}
        self.paces = {}
        self.tainted_laps = {}
        self.pit_events = deque(maxlen=12)
        self.last_report = -math.inf
        self.reported = {}
        self.run_id = None
        self.pending_laps = {}
        self.final_progress = None
        self.gap_history = {}
        self.spoken = {}
        self.finish_seen = None
        # Every car's stint: the lap it last left the pit road on, and its stops. Known from the start
        # only for cars first seen before their first lap was done; otherwise from their first stop.
        self.stints = {}

    def ingest(self, race, run_id, session_time):
        if self.run_id != run_id:
            self.reset()
            self.run_id = run_id
        self.race = race or {}
        own = next((c for c in self.race.get('cars', []) if c['idx'] == self.race.get('player_idx')), None)
        old_own = self.previous.get(own['idx']) if own else None
        crossed = (own and old_own and numeric(own.get('lap_completed')) is not None
                   and own['lap_completed'] == (numeric(old_own.get('lap_completed')) or 0) + 1
                   # Race data arrives at 1Hz; short laps move several percent per sample.
                   and (numeric(old_own.get('distance_pct')) or 0) > .85
                   and numeric(own.get('distance_pct')) is not None and 0 <= own['distance_pct'] < .15
                   and 0 < session_time - old_own['_observed_at'] <= 3)
        own_paces = self.paces.get(own['idx'], []) if own else []
        eligible = set()
        for car in self.race.get('cars', []):
            if (not own or car['idx'] == own['idx'] or own.get('class_id') is None
                    or car.get('class_id') != own['class_id'] or car.get('on_pit_road') is not False
                    or own.get('on_pit_road') is not False or car.get('track_surface') in (-1, 0)
                    or own.get('track_surface') in (-1, 0)):
                continue
            values = [numeric(c.get(k)) for c in (own, car) for k in ('lap_completed', 'distance_pct', 'class_position')]
            if any(v is None for v in values):
                continue
            a_lap, a_pct, a_pos, b_lap, b_pct, b_pos = values
            difference = b_lap + b_pct - a_lap - a_pct
            side = 'ahead' if b_pos == a_pos - 1 and 0 < difference < .5 else 'behind' if b_pos == a_pos + 1 and -.5 < difference < 0 else None
            if side is None:
                continue
            key = (car['idx'], side, a_pos, b_pos)
            eligible.add(key)
            if crossed and len(own_paces) >= 3:
                self.gap_history.setdefault(key, deque(maxlen=4)).append({
                    'lap': a_lap, 'at': session_time, 'gap_s': estimated_gap_s(own, car, difference, statistics.median(own_paces))})
        self.gap_history = {k: v for k, v in self.gap_history.items() if k in eligible}
        if self.race.get('state') == 5 and self.final_progress is None:
            own = next((c for c in self.race.get('cars', []) if c['idx'] == self.race.get('player_idx')), None)
            if own and numeric(own.get('lap_completed')) is not None and numeric(own.get('distance_pct')) is not None:
                self.final_progress = math.floor(own['lap_completed'] + own['distance_pct']) + 1
        for car in self.race.get('cars', []):
            idx = car['idx']
            prev = self.previous.get(idx)
            completed = numeric(car.get('lap_completed'))
            tainted = self.tainted_laps.get(idx, False)
            if prev:
                old_lap = numeric(prev.get('lap_completed'))
                if completed is not None and old_lap is not None and completed == old_lap + 1:
                    self.pending_laps[idx] = {'lap': completed, 'last': prev.get('last_lap_s'),
                        'at': session_time, 'clean': not tainted and not prev.get('on_pit_road')
                        and not car.get('on_pit_road') and 0 < session_time - prev['_observed_at'] <= 3}
                    tainted = False
                elif completed != old_lap:
                    tainted = False
                if car.get('on_pit_road') is True and prev.get('on_pit_road') is False:
                    self.pit_events.append({'idx': idx, 'at': session_time, 'name': car['name']})
                if completed is not None and old_lap is not None and completed < old_lap:
                    self.paces.pop(idx, None)
                    self.pending_laps.pop(idx, None)
            pending = self.pending_laps.get(idx)
            if pending:
                last = positive(car.get('last_lap_s'))
                if pending['lap'] != completed or session_time - pending['at'] > 3:
                    self.pending_laps.pop(idx, None)
                elif last and last != pending['last']:
                    if pending['clean']:
                        self.paces.setdefault(idx, deque(maxlen=3)).append(last)
                    self.pending_laps.pop(idx, None)
            self.tainted_laps[idx] = tainted or car.get('on_pit_road') is True or car.get('track_surface') in (-1, 0)
            stint = self.stints.get(idx)
            if stint is None:
                fresh = completed is not None and completed <= 0
                self.stints[idx] = {'since': 0 if fresh else None, 'stops': 0 if fresh else None}
            elif prev:
                if car.get('on_pit_road') is True and prev.get('on_pit_road') is False and stint['stops'] is not None:
                    stint['stops'] += 1
                if car.get('on_pit_road') is False and prev.get('on_pit_road') is True and completed is not None:
                    stint['since'] = completed
            self.previous[idx] = {**car, '_observed_at': session_time}

    # A stint cannot be stretched by more than this through lifting; beyond it only a stop helps.
    SAVE_LIMIT_PCT = 10
    # Laps of fuel that must be left beyond one more lap, on top of the highest measured use.
    BOX_MARGIN_LAPS = .03
    # How far a pace-based finish time may drift, as a share of the time still to run (unverified first value).
    FINISH_DRIFT = .004
    # A stop now that brings the car out within this many seconds of another car is a close call worth saying.
    REJOIN_CLOSE_S = 3.

    def _leader_stop(self, leader, own, time_left, leader_pace, leader_pct, own_pace, own_pct, fuel, use, pit_loss_s):
        """Seconds the overall leader will still lose in the pits, as far as it can be told.

        2026-10-21 (the driver's decision): a 45-minute race tipped by a lap when the leader (another
        class) pitted after the fuel call, and the fuel advised was a lap too much. In a race that
        takes our car a stop, a leader seen from the start without a stop yet is taken to make one,
        costing what ours costs. Unknown stops, a leader in the pits now, or no pit loss: nothing."""
        loss = positive(pit_loss_s) or positive(self.auto_pit_loss_s)
        if not leader or not own or not loss or leader.get('on_pit_road') is True or time_left <= loss:
            return 0.
        _, leader_stops = self.stint(leader)
        _, own_stops = self.stint(own)
        if leader_stops != 0:
            return 0.
        # Our race needs a stop: we made one, or our fuel does not reach the finish.
        end_s = (math.floor(time_left / leader_pace + leader_pct) + 1 - leader_pct) * leader_pace
        own_laps = math.ceil(end_s / own_pace + own_pct) - own_pct
        needs_stop = bool(own_stops) or (fuel is not None and use is not None and fuel < own_laps * use)
        return float(loss) if needs_stop else 0.

    def stint(self, car):
        """(laps since the car last left the pit road, stops so far): None where not known."""
        record = self.stints.get(car['idx']) if car else None
        completed = numeric(car.get('lap_completed')) if car else None
        if not record:
            return None, None
        laps = completed - record['since'] if record['since'] is not None and completed is not None else None
        return (max(0, int(laps)) if laps is not None else None), record['stops']

    def view(self, snapshot, fuel_use=None, fuel_laps=0, reserve_laps=.5, pit_loss_s=None, fuel_save_mode='finish'):
        race = self.race
        active = (str(race.get('session_type', '')).lower() == 'race' and race.get('state') in (4, 5)
                  and not int(snapshot.get('session_flags') or race.get('flags') or 0) & 0x1)
        own = next((c for c in race.get('cars', []) if c['idx'] == race.get('player_idx')), None)
        def pace(car):
            samples = self.paces.get(car['idx'], []) if car else []
            return statistics.median(samples) if samples else None
        own_pace = pace(own)
        def progress(car):
            lap, pct = numeric(car.get('lap_completed')), numeric(car.get('distance_pct'))
            return lap + pct if lap is not None and lap >= 0 and pct is not None and 0 <= pct <= 1 else None
        own_progress = progress(own) if own else None
        position = positive(own.get('class_position')) if own else None
        rivals = [c for c in race.get('cars', []) if own and own.get('class_id') is not None
                  and c.get('class_id') == own.get('class_id') and positive(c.get('class_position'))]
        def describe(car):
            if not car:
                return None
            cp, car_pace = progress(car), pace(car)
            difference = cp - own_progress if cp is not None and own_progress is not None else None
            gap = abs(difference) * own_pace if difference is not None and abs(difference) < 1 and own_pace else None
            if gap is not None and abs(difference) < .5:
                gap = estimated_gap_s(own, car, difference, own_pace)
            stint_laps, stops = self.stint(car)
            return {**car, 'pace_s': car_pace, 'pace_samples': len(self.paces.get(car['idx'], [])),
                    'stint_laps': stint_laps, 'stint_since': (self.stints.get(car['idx']) or {}).get('since'), 'stops': stops,
                    'gap_estimate_s': round(gap, 1) if gap is not None else None,
                    'pace_advantage_s': round(own_pace - car_pace, 2) if own_pace and car_pace else None,
                    'lap_difference': round(difference, 2) if difference is not None else None}
        def neighbor(offset):
            car = describe(next((c for c in rivals if position and c.get('class_position') == position + offset), None))
            if car:
                points = list(self.gap_history.get((car['idx'], 'ahead' if offset < 0 else 'behind', position, car.get('class_position')), []))
                consecutive = (len(points) >= 3 and all(b['lap'] == a['lap'] + 1 for a, b in zip(points, points[1:]))
                               and numeric(snapshot.get('session_time')) is not None and 0 <= snapshot['session_time'] - points[-1]['at'] <= (own_pace or 0) * 1.5)
                deltas = [b['gap_s'] - a['gap_s'] for a, b in zip(points, points[1:])] if consecutive else []
                trend = 'closing' if deltas and all(d < -.15 for d in deltas) else 'opening' if deltas and all(d > .15 for d in deltas) else 'steady' if deltas else None
                car.update(gap_trend=trend, gap_at_line_s=round(points[-1]['gap_s'], 1) if consecutive else None,
                           gap_start_s=round(points[0]['gap_s'], 1) if consecutive else None,
                           gap_change_s_per_lap=round(statistics.median(deltas), 2) if deltas else None,
                           gap_observations=len(points) if consecutive else 0, trend_lap=points[-1]['lap'] if points else None)
            return car
        remaining = None
        method = None
        fraction = None
        finish = None
        if active and own_progress is not None:
            total = limit_value(race.get('total_laps'))
            if total:
                remaining, method = max(0, total - own_progress), 'LAP_LIMIT'
                fraction = own_progress / total
            time_left = numeric(snapshot.get('session_time_remaining_s'))
            if time_left is None:
                time_left = numeric(race.get('time_remaining_s'))
            if time_left is not None and -86400 < time_left < 0:
                time_left = max(0, time_left)
            duration = numeric(race.get('session_duration_s'))
            if not total and duration and 0 < duration < 86400 and time_left is not None and 0 <= time_left <= duration:
                fraction = 1 - time_left / duration
            leader = next((c for c in race.get('cars', []) if c.get('position') == 1), None)
            leader_pace = pace(leader)
            # The overall leader crosses after time expires. Include the player's
            # next crossing and a reserve; never treat seconds/own pace as exact laps.
            leader_pct = numeric(leader.get('distance_pct')) if leader else None
            own_pct = numeric(own.get('distance_pct')) if own else None
            leader_progress = progress(leader) if leader else None
            if total and own_pace and leader_pace and leader_progress is not None and own_pct is not None:
                finish_s = max(0, total - leader_progress) * leader_pace
                lapped_remaining = max(0, math.ceil(finish_s / own_pace + own_pct) - own_pct)
                if lapped_remaining < remaining:
                    remaining, method = lapped_remaining, 'LEADER_FINISH_ESTIMATE'
            if (race.get('state') != 5 and time_left is not None and 0 <= time_left < 86400 and own_pace and leader_pace
                    and leader_pct is not None and 0 <= leader_pct <= 1 and own_pct is not None):
                # The leader's first crossing after the clock runs out ends the race; the player
                # finishes at his own next crossing after that (checked against 29 timed races).
                leader_stop = self._leader_stop(leader, own, time_left, leader_pace, leader_pct, own_pace, own_pct,
                                                numeric(snapshot.get('fuel_l')), positive(fuel_use), pit_loss_s)
                leader_at_zero = (time_left - leader_stop) / leader_pace + leader_pct
                end_s = (math.floor(leader_at_zero) + 1 - leader_pct) * leader_pace + leader_stop
                own_at_end = end_s / own_pace + own_pct
                timed = max(0, math.ceil(own_at_end) - own_pct)
                if remaining is None or timed < remaining:
                    remaining, method = timed, 'TIMED_ESTIMATE'
                    # How close the count is to tipping by a lap, in seconds. Two lines decide it:
                    # the leader against the clock, and the player against the leader's finish.
                    leader_before = (leader_at_zero - math.floor(leader_at_zero)) * leader_pace
                    leader_after = (math.floor(leader_at_zero) + 1 - leader_at_zero) * leader_pace
                    own_before = (own_at_end - math.floor(own_at_end)) * own_pace
                    own_after = (math.ceil(own_at_end) - own_at_end) * own_pace
                    leader_tolerance, own_tolerance = max(6., self.FINISH_DRIFT * time_left), max(8., self.FINISH_DRIFT * 1.5 * time_left)
                    finish = {'total_laps': round(own_progress + timed), 'leader_margin_s': round(min(leader_before, leader_after), 1),
                              'leader_stop_s': round(leader_stop, 1) if leader_stop else None,
                              'own_margin_s': round(min(own_before, own_after), 1),
                              # A leader who crosses just after zero, or a player who just misses the flag, means a lap more.
                              'may_extend': leader_after < leader_tolerance or own_after < own_tolerance,
                              'may_shorten': leader_before < leader_tolerance or own_before < own_tolerance}
            if race.get('state') == 5 and self.final_progress is not None:
                remaining, method = max(0, self.final_progress - own_progress), 'PLAYER_FINAL_LAP'
        fuel = numeric(snapshot.get('fuel_l'))
        use = positive(fuel_use)
        available = fuel / use if fuel is not None and fuel >= 0 and use else None
        required = (remaining + reserve_laps) * use if remaining is not None and use else None
        add = max(0, required - fuel) if required is not None and fuel is not None and fuel >= 0 else None
        finish_need = remaining * use if remaining is not None and use else None
        shortage = max(0, finish_need - fuel) if finish_need is not None and fuel is not None else None
        target_use = fuel / remaining if fuel is not None and remaining and remaining > 0 else None
        saving_distance = remaining + (reserve_laps if fuel_save_mode == 'reserve' else 0) if remaining is not None else None
        save_target = fuel / saving_distance if fuel is not None and fuel >= 0 and saving_distance and saving_distance > 0 and fuel_save_mode != 'off' else None
        saving = max(0, (1 - target_use / use) * 100) if target_use is not None and use else None
        # Saving is only a plan when it is within reach; a 4-hour race is not a no-stop race.
        savable = saving is not None and saving <= self.SAVE_LIMIT_PCT
        if use is None or save_target is None or save_target >= use or save_target < use * (1 - self.SAVE_LIMIT_PCT / 100):
            save_target = None
        capacity = positive(race.get('tank_capacity_l'))
        room = max(0, capacity - fuel) if capacity is not None and fuel is not None and fuel >= 0 else None
        # What the next stop can and should put in, and how many stops the rest of the race takes.
        fill = min(add, room) if add is not None and room is not None else add
        stops = math.ceil(add / capacity - 1e-9) if add is not None and add > .1 and capacity else 0 if add is not None and add <= .1 else None
        # The stop cannot be later than the lap this fuel runs out on. If a full tank from there reaches the
        # finish, it is one stop even when the reserve does not fit; and if a stop one lap sooner would not
        # reach, the tank has to be run down first (2026-10-12: 55 litres, 12 laps of 8.6).
        run_dry, pct = False, numeric(snapshot.get('distance_pct'))
        if stops and available is not None and pct is not None and 0 <= pct <= 1:
            to_line = 1 - pct
            more = math.floor(available - to_line - self.BOX_MARGIN_LAPS)      # whole laps after this one before the last possible stop
            after = remaining - to_line - more                                  # laps still to drive from that stop
            reach = capacity / use
            if more >= 0 and 0 < after <= reach:
                stops, run_dry = 1, after + 1 > reach
        # Should the race run a lap longer than counted: what the stop must then put in.
        longer = add + use if add is not None and use and finish and finish['may_extend'] else None
        fill_longer = min(longer, room) if longer is not None and room is not None else longer
        rejoin = rejoin_close = None
        if active and own_pace and own_progress is not None and positive(pit_loss_s) and len(rivals) > 1:
            usable = [c for c in rivals if c['idx'] != own['idx'] and progress(c) is not None
                      and pace(c) and not c.get('on_pit_road') and c.get('track_surface') not in (-1, 0)]
            # Current class order, extrapolated progress and each rival's own pace.
            if len(usable) == len(rivals) - 1:
                rejoin = 1 + sum(progress(c) + pit_loss_s / pace(c) > own_progress for c in usable)
                # The car the stop would bring us out closest to: seconds ahead of it (+) or behind it (-).
                calls = [((own_progress - progress(c)) * pace(c) - pit_loss_s, c) for c in usable if -.2 < own_progress - progress(c) < 1]
                calls = [(margin, c) for margin, c in calls if abs(margin) <= self.REJOIN_CLOSE_S]
                if calls:
                    margin, car = min(calls, key=lambda call: abs(call[0]))
                    stint_laps, _ = self.stint(car)
                    rejoin_close = {'idx': car['idx'], 'class_position': positive(car.get('class_position')),
                                    'margin_s': round(margin, 1), 'stint_laps': stint_laps}
        traffic = []
        if own and own.get('class_id') is not None and own_pace and len(self.paces.get(own['idx'], [])) >= 3:
            for car in race.get('cars', []):
                pct = numeric(car.get('distance_pct'))
                if (car.get('class_id') is None or car['class_id'] == own['class_id']
                        or car.get('on_pit_road') is not False or car.get('track_surface') in (-1, 0)
                        or pct is None or not 0 <= pct <= 1 or numeric(own.get('distance_pct')) is None):
                    continue
                delta = (pct - own['distance_pct'] + .5) % 1 - .5
                gap = abs(delta) * own_pace
                if gap <= 3 + 1e-9:
                    traffic.append({**describe(car), 'side': 'ahead' if delta >= 0 else 'behind',
                                    'gap_estimate_s': round(gap, 1)})
        return {'active': active, 'session_type': race.get('session_type'), 'state': race.get('state'),
                'flags': race.get('flags'), 'class_position': position,
                'time_remaining_s': race.get('time_remaining_s'), 'pace_s': own_pace,
                'pace_samples': len(self.paces.get(own['idx'], [])) if own else 0,
                'ahead': neighbor(-1), 'behind': neighbor(1),
                'other_class_traffic': sorted(traffic, key=lambda c: c['gap_estimate_s']),
                'speech_window_s': snapshot.get('speech_window_s'), 'speech_learned_laps': snapshot.get('speech_learned_laps', 0),
                'class_cars': [describe(c) for c in rivals],
                'remaining_laps': round(remaining, 2) if remaining is not None else None, 'finish_method': method,
                'race_fraction': round(fraction, 3) if fraction is not None else None,
                'fuel_l': fuel, 'fuel_l_per_lap': use, 'fuel_observed_laps': fuel_laps,
                'fuel_laps': round(available, 2) if available is not None else None,
                'reserve_laps': reserve_laps, 'fuel_to_add_l': round(add, 2) if add is not None else None,
                'fuel_finish_shortfall_l': round(shortage, 2) if shortage is not None else None,
                'fuel_reserve_l': round(reserve_laps * use, 2) if use else None,
                'fuel_target_l_per_lap': round(target_use, 3) if target_use is not None else None,
                'fuel_save_mode': fuel_save_mode, 'fuel_save_target_l_per_lap': round(save_target, 3) if save_target is not None else None,
                'fuel_save_pct': round(saving, 1) if saving is not None else None, 'fuel_save_feasible': savable,
                'tank_capacity_l': capacity, 'multiple_stops_needed': add is not None and capacity is not None and add > capacity,
                'stops_needed': stops, 'fuel_run_dry': run_dry, 'fuel_next_stop_l': round(fill, 2) if fill is not None else None,
                'fuel_next_stop_longer_l': round(fill_longer, 2) if fill_longer is not None else None,
                'finish_laps_to_go': math.ceil(remaining - 1e-9) if remaining is not None else None,
                'finish_total_laps': finish['total_laps'] if finish else None,
                'finish_may_extend': bool(finish and finish['may_extend']), 'finish_may_shorten': bool(finish and finish['may_shorten']),
                'finish_leader_margin_s': finish['leader_margin_s'] if finish else None,
                'finish_own_margin_s': finish['own_margin_s'] if finish else None,
                'finish_leader_stop_s': finish['leader_stop_s'] if finish else None,
                'fuel_next_stop_full': bool(add is not None and room is not None and add >= room - 1 and add > .1),
                'pit_loss_s': pit_loss_s, 'rejoin_class_position_estimate': rejoin, 'rejoin_close': rejoin_close,
                'own_stint_laps': self.stint(own)[0] if own else None, 'own_stops': self.stint(own)[1] if own else None,
                'estimate_notice': '간격은 랩 진행 거리와 페이스로 환산한 추정입니다. 복귀 순위는 상대 피트·추월·교통 변화를 포함하지 않습니다.'}

    def candidates(self, view, snapshot, context, notes=()):
        if not view['active']:
            return []
        reports = []
        def add(key, role, text=None, priority=1, token=None, cooldown=180, short_text=None, speech_key=None, phrase=None, also=()):
            # The wording only advances after a delivery, so repeated polls select the same clip.
            seed = self.spoken.get(key, 0)
            if phrase:
                # Further whole sentences ('also') follow in the same call, each from its own clip.
                sentences = [radio_phrases.build(name, seed, **values)['text'] for name, values in (phrase, *also)]
                spoken = ' '.join(sentences)
                speech = {'speech_key': None, 'spoken_text': spoken, 'speech_text': spoken}
                speech['speech_specs'] = [('phrase', name, dict(values)) for name, values in (phrase, *also)]
                if len(sentences) > 1:
                    speech['speech_parts'] = sentences
                text = text or spoken
            else:
                speech = speech_fields(speech_key or key, seed)
                text = text or speech['spoken_text']
            reports.append({'key': key, 'engineer': role, 'text': text, 'priority': priority,
                            'token': token or key, 'cooldown': cooldown, 'short_text': short_text, **speech})
        available, fuel_add = view['fuel_laps'], view['fuel_to_add_l']
        short = (view.get('fuel_finish_shortfall_l') or 0) > .1
        pct, lap = numeric(snapshot.get('distance_pct')), snapshot.get('lap_number')
        # Fuel is only talked about when it asks for a decision, and never while the car is being fuelled.
        if snapshot.get('on_pit_road') or available is None:
            pass
        elif view.get('remaining_laps') is None or short:
            # Box on the lap after which a further full lap is no longer covered. One call
            # early in that lap and one before the pit entry, however long the lap is.
            past_line = available - (1 - pct) if pct is not None and 0 <= pct <= 1 else available - 1
            if past_line < self.BOX_MARGIN_LAPS + 1:
                late = pct is not None and pct >= .75
                # Said so that it cannot be taken for "pit now" at the start of the lap.
                add('fuel_critical', 'strategy', f'현재 소비량 기준 연료가 약 {available:.1f}랩분 남았습니다. 이번 랩에 피트로 들어오세요.', 3,
                    token=f'{lap}:{late}', cooldown=3600, phrase=('box_now' if late else 'box_end_of_lap', {}))
            elif short and view.get('fuel_save_feasible'):
                # Close enough to the finish that lifting could replace a stop: worth one call per stint.
                add('fuel_short', 'strategy', fuel_advice(view, brief=True), 2, cooldown=1800,
                    short_text=f'완주 연료 약 {view["fuel_finish_shortfall_l"]:.1f}리터 부족합니다.',
                    phrase=('fuel_short_by', {'short': math.ceil(view['fuel_finish_shortfall_l'])}))
            if short:
                # A stop is needed: count it down from the measured fuel, then check what will go in.
                pace = view.get('pace_s')
                if 3.5 < available <= 5.5 and (not pace or available * pace <= 900):
                    add('pit_soon', 'strategy', priority=2, cooldown=900, phrase=('pit_soon', {}))
                elif 1.3 < available <= 2.5 and past_line >= self.BOX_MARGIN_LAPS + 1:
                    # Where the stop puts the car, when the driver entered his pit loss.
                    rejoin = view.get('rejoin_class_position_estimate')
                    # A stop any sooner would not reach the finish on one tank: that is the thing to know.
                    add('pit_two', 'strategy', priority=2, cooldown=900, phrase=('pit_two', {}),
                        also=[('pit_run_dry', {})] if view.get('fuel_run_dry') else [('pit_rejoin', {'pos': rejoin})] if rejoin else ())
                # How much goes in at this stop, to the litre, and what a lap more would need.
                amount = view.get('fuel_next_stop_l')
                # A full tank needs no number; the last stop of the race is the one that is measured out.
                # One number only (the driver's decision): when the race may still run a lap longer,
                # that lap is simply included. "It might be a lap more" is not something to act on.
                if 1.3 < available <= 2.5 and amount is not None and amount >= 1 and not view.get('fuel_next_stop_full'):
                    litres = math.ceil(view.get('fuel_next_stop_longer_l') or amount)
                    add('pit_fuel_plan', 'strategy', priority=2, cooldown=900, token=str(litres), phrase=('pit_fuel_add', {'liters': litres}))
                # From here one stop reaches the flag: the stop can now be timed freely.
                if available > 2.5 and view.get('stops_needed') == 1 and not view.get('fuel_next_stop_full'):
                    add('pit_window', 'strategy', priority=1, cooldown=7200, phrase=('pit_window_open', {}))
                # The tyre plan for this stop, from measured stops (live_pit.tyre_advice); its own call after the countdown.
                plan = [(key, values) for key, values in view.get('tyre_parts') or []]
                if plan and available <= 2.5:
                    add('tyre_plan', 'strategy', priority=2, cooldown=900, token=str(view.get('tyre_decision')), phrase=plan[0], also=plan[1:])
                planned, flags = numeric(snapshot.get('pit_sv_fuel')), numeric(snapshot.get('pit_sv_flags'))
                needed = view.get('fuel_next_stop_l')
                if available <= 2.5 and planned is not None and flags is not None and needed is not None:
                    planned = planned if int(flags) & 0x10 else 0.
                    if needed - planned > 1:
                        # A full tank is one fact; its litres shrink as fuel burns and must not restart the call.
                        full = view.get('fuel_next_stop_full')
                        add('pit_fuel_check', 'strategy', priority=2, cooldown=1800, token=f'{round(planned)}:{"full" if full else round(needed)}',
                            phrase=('pit_fuel_full', {'liters': planned}) if full else ('pit_fuel_low', {'liters': planned, 'need': needed}))
        elif fuel_add is not None and fuel_add > .1:
            add('fuel_reserve', 'strategy', '현재 소비량으로 완주는 예상되지만 안전 여유분이 부족합니다. ' + fuel_advice(view, brief=True), 1, cooldown=3600)
        if ENERGY_CALLS_ENABLED and view.get('game') == 'lmu' and not snapshot.get('on_pit_road'):
            self._energy_calls(view, add)
        # Flag announcements belong to the cached spotter radio only.
        for side, label in [('behind', '뒤차'), ('ahead', '앞차')]:
            car = view.get(side)
            if not car or car.get('on_pit_road') or car.get('track_surface') in (-1, 0):
                continue
            gap, advantage = car['gap_estimate_s'], car['pace_advantage_s']
            trend, change = car.get('gap_trend'), car.get('gap_change_s_per_lap')
            # Only a shrinking gap is reported: catching the car ahead, or being caught from behind.
            if gap is not None and gap < 8 and trend == 'closing' and car.get('gap_at_line_s') is not None:
                short = f'같은 클래스 {label} 간격이 랩당 약 {abs(change):.1f}초씩 줄고 있습니다.'
                add(f'battle_{side}', 'race', short + f' 같은 위치에서 {car["gap_observations"]}회 관측한 추정입니다. 출발선 기준 간격 약 {car["gap_at_line_s"]:.1f}초입니다.',
                    token=f'{car["idx"]}:{trend}', cooldown=120, short_text=short,
                    phrase=(f'gap_{side}_closing', {'gap': car['gap_at_line_s']}))
            elif gap is not None and gap < 5 and advantage is not None and advantage > .3 and side == 'behind' and view['pace_samples'] >= 3 and car['pace_samples'] >= 3:
                add('approach', 'race', f'같은 클래스 뒤차가 최근 {car["pace_samples"]}랩 기준 랩당 {advantage:.1f}초 빠릅니다. 거리 환산 간격은 약 {gap:.1f}초입니다.', 1, token=str(car['idx']),
                    short_text=f'같은 클래스 뒤차 페이스가 약 {advantage:.1f}초 빠릅니다.')
            if any(e['idx'] == car['idx'] and 0 <= snapshot['session_time'] - e['at'] <= 20 for e in self.pit_events):
                add(f'pit_{side}', 'race', f'같은 클래스 {label}의 피트 진입이 감지됐습니다.', 1, token=str(car['idx']))
        for car in view.get('other_class_traffic', [])[:1]:
            label = '앞' if car['side'] == 'ahead' else '뒤'
            text = f'다른 클래스 차량이 {label}에 있습니다. 거리 환산 약 {car["gap_estimate_s"]:.1f}초입니다.'
            add('class_traffic', 'race', text, token=str(car['idx']), cooldown=120,
                short_text=f'다른 클래스 차량이 {label}에 있습니다.', speech_key='class_traffic_' + car['side'])
        # The counted race distance tipped by a lap and stayed there: late in the race that changes the fuel.
        total, now = view.get('finish_total_laps'), snapshot['session_time']
        if total is None:
            self.finish_seen = None
        elif self.finish_seen is None:
            self.finish_seen = {'said': total, 'now': total, 'since': now}
        else:
            seen = self.finish_seen
            if total != seen['now']:
                seen.update(now=total, since=now)
            left = numeric(view.get('remaining_laps'))
            # Only a lap more that leaves the tank short is news; a lap less needs nothing from the driver.
            if (total > seen['said'] and now - seen['since'] >= 20 and left is not None and left <= 12
                    and (view.get('fuel_finish_shortfall_l') or 0) > .1):
                add('finish_laps', 'strategy', priority=2, cooldown=60, token=str(total), phrase=('finish_laps_more', {}))
            elif total < seen['said'] and now - seen['since'] >= 20:
                seen['said'] = total
        fraction, flags = view.get('race_fraction'), int(numeric(snapshot.get('session_flags')) or 0)
        if fraction is not None and .5 <= fraction < .58 and view.get('class_position'):
            add('halfway', 'race', cooldown=36000, phrase=('halfway', {'pos': view['class_position']}))
        if flags & 0x2:
            close = lambda side: (view.get(side) or {}).get('gap_estimate_s') is not None and view[side]['gap_estimate_s'] < 1.5
            add('last_lap', 'race', priority=2, cooldown=36000,
                phrase=('last_lap_defend' if close('behind') else 'last_lap_attack' if close('ahead') else 'last_lap', {}))
        feedback = [f for f in context.get('fuel_feedback', []) if f.get('target_l') is not None
                    and f.get('mode') == view.get('fuel_save_mode') and f.get('mode') != 'off'
                    and (f.get('mode') != 'reserve' or f.get('reserve_laps') == view.get('reserve_laps'))]
        if feedback and 0 <= snapshot['session_time'] - feedback[-1]['at'] <= 30:
            last = feedback[-1]
            error = last['use_l'] - last['target_l']
            held = (len(feedback) >= 3 and all(b['lap'] == a['lap'] + 1 for a, b in zip(feedback, feedback[1:]))
                    and all(f['use_l'] <= f['target_l'] + .03 for f in feedback[-3:]))
            text = (f'최근 3개 깨끗한 랩은 각 랩의 연료 절약 목표를 유지했습니다.' if held else
                    f'방금 깨끗한 랩은 연료 {last["use_l"]:.2f}리터 사용. 시작 때 목표보다 {abs(error):.2f}리터 {"더" if error > .03 else "덜" if error < -.03 else "비슷하게"} 썼습니다.')
            add('fuel_feedback', 'strategy', text, token=f'{last["lap"]}:{last["mode"]}', cooldown=30,
                short_text='최근 3랩 연료 절약 목표 유지.' if held else f'방금 랩 연료 {last["use_l"]:.2f}리터. 목표 {last["target_l"]:.2f}리터.',
                speech_key='fuel_save_held' if held else None,
                phrase=None if held else ('fuel_lap', {'fuel': last['use_l'], 'target': last['target_l']}))
        # Repeated feelings are reminders, not a telemetry diagnosis. Separate laps
        # are required; an old AI response never becomes measured evidence.
        run_id = (context.get('snapshot') or {}).get('run_id')
        relevant = [n for n in notes if run_id and n.get('source') != 'AUTO_REPORT'
                    and ((n.get('context') or {}).get('snapshot') or {}).get('run_id') == run_id]
        for feeling, label in [('UNDERSTEER', '앞이 밀리는 느낌'), ('OVERSTEER', '뒤가 불안한 느낌'), ('BRAKING', '제동 불안')]:
            matching = [n for n in relevant if feeling in n.get('feelings', [])]
            if len(matching) < 2:
                continue
            latest = matching[0]['context']['snapshot']
            pct = numeric(latest.get('distance_pct'))
            now_pct = numeric(snapshot.get('distance_pct'))
            laps = {n['context']['snapshot'].get('lap_number') for n in matching
                    if pct is not None and abs((numeric(n['context']['snapshot'].get('distance_pct')) or -10) - pct) < .01}
            if (len(laps - {None}) >= 2 and pct is not None and now_pct is not None
                    and snapshot.get('lap_number') == latest.get('lap_number') and .015 <= now_pct - pct <= .06):
                # Name the one input to change. The driver's own words say whether it was entry or exit.
                said = matching[0].get('text') or ''
                phase = '_entry' if re.search(r'진입|들어갈|제동|브레이크', said) else '_exit' if re.search(r'탈출|나갈|나올|가속', said) else ''
                call = f'coach_{feeling.lower()}{phase}'
                add('coach_reminder', 'coach', priority=1, token=f'{feeling}:{round(pct, 2)}', cooldown=300,
                    speech_key=call if call in CALLS else 'coach_' + feeling.lower())
                break
        return sorted(reports, key=lambda r: -r['priority'])

    @staticmethod
    def _energy_calls(view, add):
        """LMU virtual energy, said like fuel: only from measured laps (live_energy.EnergyWatch)."""
        pct, laps, per_lap = view.get('energy_pct'), view.get('energy_laps'), view.get('energy_pct_per_lap')
        if pct is None or laps is None or not per_lap:
            return
        stint, target = view.get('energy_stint', 0), view.get('energy_target_pct_per_lap')
        short = (view.get('energy_finish_shortfall_pct') or 0) > .1
        once = 36000     # once per stint: the token carries the stint
        if laps < 1:
            add('ve_critical', 'strategy', priority=3, token=str(stint), cooldown=once, phrase=('ve_critical', {}))
        elif laps <= 4 and short:
            left = math.floor(laps)
            add('ve_laps_left', 'strategy', priority=2, token=f'{stint}:{left}', cooldown=once,
                phrase=('ve_laps_left', {'laps': left}))
        if view.get('energy_limits_stint') and short:
            add('ve_limits_stint', 'strategy', priority=2, token=str(stint), cooldown=once, phrase=('ve_limits_stint', {}))
        if pct <= 50 and laps >= 1 and short:
            # Only when energy decides the race: in a sprint with energy to spare it was a riddle.
            add('ve_half', 'strategy', token=str(stint), cooldown=once, phrase=('ve_half', {}))
        if short and target:
            # A cut within what lifting can do saves the stop; more than that needs one.
            if target >= per_lap * .9:
                add('ve_save', 'strategy', priority=2, cooldown=600, token=str(stint),
                    phrase=('ve_save', {}), also=[('ve_target', {'ve_target': target})])
            else:
                add('ve_need_stop', 'strategy', priority=2, token=str(stint), cooldown=once, phrase=('ve_need_stop', {}))
        elif not short and view.get('remaining_laps') is not None:
            fraction = view.get('race_fraction')
            if fraction is not None and .5 <= fraction < .6:
                add('ve_finish_ok', 'strategy', cooldown=once, token='finish', phrase=('ve_finish_ok', {}))
            left_s = numeric(view.get('time_remaining_s'))
            spare = pct - (view.get('remaining_laps') or 0) * per_lap
            if left_s is not None and 0 < left_s <= 900 and spare >= per_lap:
                add('ve_spare', 'strategy', cooldown=once, token='spare', phrase=('ve_spare', {}))
        last, counted = view.get('energy_last_lap_pct'), view.get('energy_laps_counted') or 0
        if target and last is not None and counted % 3 == 0:
            add('ve_lap', 'strategy', cooldown=600, token=str(counted),
                phrase=('ve_lap', {'ve': last}), also=[('ve_target', {'ve_target': target})])

    def select(self, candidates, snapshot, level, cached_audio=False):
        now = snapshot['session_time']
        if level == 'off':
            return None
        # Normal reports wait for low workload; urgent status can bypass this gate.
        for report in candidates:
            if now - self.last_report < (5 if report['priority'] == 3 else 20):
                continue
            if level == 'important' and report['priority'] < 2:
                continue
            if cached_audio:
                if report['priority'] < 3 and not report_window(snapshot, report.get('spoken_text') or report['text'], brief=True):
                    continue
            elif report['priority'] < 3 and not report_window(snapshot, report['text']):
                if report.get('short_text') and report_window(snapshot, report['short_text'], brief=True):
                    report = {**report, 'text': report['short_text']}
                else:
                    continue
            last = self.reported.get(report['key'])
            if last and (now - last['at'] < report['cooldown'] and last['token'] == report['token']):
                continue
            return report
        return None

    def delivered(self, report, session_time):
        self.last_report = session_time
        if report['key'] == 'finish_laps' and getattr(self, 'finish_seen', None):
            self.finish_seen['said'] = self.finish_seen['now']
        self.spoken[report['key']] = self.spoken.get(report['key'], 0) + 1
        self.reported[report['key']] = {'at': session_time, 'token': report['token']}


def tank_capacity(info):
    """Litres this car may carry in this session: the tank times the series' fuel limit (BoP)."""
    tank, limit = positive(info.get('DriverCarFuelMaxLtr')), positive(info.get('DriverCarMaxFuelPct'))
    return tank * limit if tank and limit and limit <= 1 else tank


def fuel_advice(view, brief=False):
    shortfall = view.get('fuel_finish_shortfall_l')
    need = view.get('fuel_to_add_l')
    if need is None:
        return '완주 연료 계산에 필요한 종료 조건과 완주랩 소비량을 수집 중입니다.'
    parts = []
    if not brief:
        parts.append(f'마지막 완주 통과까지 예상 거리는 약 {view["remaining_laps"]:.1f}랩입니다.'
                     + (' 시간제 경기는 시간 종료 뒤 마지막 랩까지 포함합니다.' if view['finish_method'] == 'TIMED_ESTIMATE' else ''))
    stops = view.get('stops_needed')
    if shortfall is not None and shortfall > .1 and view.get('fuel_save_feasible') is False:
        # Too far to save: say how the rest of the race is fuelled, not a no-stop target.
        if stops and stops > 1:
            parts.append(f'완주하려면 급유가 약 {stops}번 필요합니다.')
        else:
            parts.append(f'현재 소비량을 유지하면 완주까지 약 {shortfall:.1f}리터가 부족합니다. 절약으로는 어렵고 급유가 필요합니다.')
        fill = view.get('fuel_next_stop_l')
        if view.get('fuel_next_stop_full'):
            parts.append('다음 피트에서는 가득 채우세요.')
        elif fill is not None:
            parts.append(f'다음 피트에서 안전 여유분 포함 {fill:.1f}리터를 넣으면 됩니다.')
        if view.get('fuel_laps') is not None:
            parts.append(f'지금 연료는 약 {view["fuel_laps"]:.1f}랩분입니다.')
        return ' '.join(parts)
    if shortfall is not None and shortfall > .1:
        parts.append(f'현재 소비량을 유지하면 완주까지 약 {shortfall:.1f}리터가 부족합니다.')
        target, saving = view.get('fuel_target_l_per_lap'), view.get('fuel_save_pct')
        if target is not None and saving is not None:
            parts.append(f'급유 없이 가려면 평균 랩당 {target:.2f}리터 이하, 약 {saving:.0f}퍼센트 절약이 목표입니다.')
            parts.append('직선 끝에서 가속 페달을 일찍 놓는 방식으로 절약을 검토하세요. 이 목표는 안전 여유분이 없고 완주를 보장하지 않습니다.' if not brief else '절약 달성이 어렵다면 급유를 검토하세요.')
    else:
        parts.append('현재 소비량 기준 완주 연료는 예상됩니다.' if shortfall is not None else '안전 여유분까지 포함한 계산입니다.')
    if not brief or shortfall is None or shortfall <= .1:
        reserve = view.get('fuel_reserve_l')
        parts.append(f'안전 여유분 포함 추가 급유량은 {need:.1f}리터입니다.'
                     + (f' 이 중 설정한 여유분은 {reserve:.1f}리터입니다.' if reserve is not None else ''))
    return ' '.join(parts)


def damage_answer(snapshot):
    warnings = numeric(snapshot.get('engine_warnings'))
    flags = numeric(snapshot.get('session_flags'))
    parts = []
    if flags is not None and int(flags) & 0x100000 or warnings is not None and int(warnings) & 0x80:
        parts.append('필수 수리 필요 신호가 켜져 있습니다. 게임 지시에 따라 피트를 준비하세요.')
    elif warnings is not None and int(warnings) & 0x100:
        parts.append('선택 수리 필요 신호가 켜져 있습니다. 손상 부위와 성능 저하 정도는 이 신호로 알 수 없습니다.')
    elif warnings is not None and flags is not None:
        parts.append('현재 필수·선택 수리 필요 신호는 꺼져 있습니다. 이것만으로 무손상을 확정할 수는 없습니다.')
    else:
        parts.append('수리 필요 신호를 읽지 못해 손상 여부를 확인할 수 없습니다.')
    if snapshot.get('on_pit_road'):
        for key, label in [('pit_repair_left_s', '필수 수리'), ('pit_optional_repair_left_s', '선택 수리')]:
            seconds = numeric(snapshot.get(key))
            if seconds is not None and seconds >= 0:
                parts.append(f'{label} 남은 시간 표시는 {seconds:.0f}초입니다.')
    incidents = numeric(snapshot.get('incidents'))
    if incidents is not None:
        parts.append(f'인시던트는 누적 {int(incidents)}점이며 사고 횟수나 손상량이 아닙니다.')
    return ' '.join(parts)


def measured_answer(role, text, view):
    if not view['active']:
        return '현재 진행 중인 레이스 데이터가 없습니다. 실제 Race 세션이 시작되면 경기 상황과 전략을 확인할 수 있습니다.'
    if role == 'strategy':
        if any(w in text for w in ('절약', '목표', '연비')):
            mode = view.get('fuel_save_mode', 'finish')
            if mode == 'off':
                return '연료 절약 피드백이 꺼져 있습니다. 피트월에서 무급유 완주 또는 안전 여유 포함 목표를 선택하세요.'
            last = view.get('fuel_last_lap')
            parts = []
            if last and last.get('target_l') is not None and last.get('mode') == mode:
                parts.append(f'최근 깨끗한 완료 랩은 {last["use_l"]:.2f}리터 사용했고, 그 랩 시작 목표는 {last["target_l"]:.2f}리터였습니다.')
            target = view.get('fuel_save_target_l_per_lap')
            if target is not None:
                parts.append(f'현재 {"안전 여유 포함 " if mode == "reserve" else ""}무급유 완주 목표는 랩당 {target:.2f}리터입니다. 달성 가능성은 주행으로 확인해야 합니다.')
            elif view.get('remaining_laps') is not None and view.get('fuel_l_per_lap') is not None:
                parts.append('현재 계산상 선택한 완주 목표를 위한 추가 절약은 필요하지 않습니다.')
            else:
                parts.append('목표 계산에 필요한 완료 랩 소비량과 경기 종료 정보를 수집 중입니다.')
            return ' '.join(parts)
        if any(w in text for w in ('타이어', '언더컷', '오버컷')):
            return '타이어 교체 효과와 상대의 연료·피트 계획은 현재 확인할 수 없습니다. 실제 소비량과 입력한 피트 총손실로 급유 필요량과 복귀 순위를 먼저 비교할 수 있습니다.'
        if '피트' in text and not any(w in text for w in ('연료', '급유', '리터')):
            parts = []
            if view['fuel_laps'] is not None:
                parts.append(f'실제 소비량 기준 연료는 약 {view["fuel_laps"]:.1f}랩분입니다.')
            if view['rejoin_class_position_estimate'] is not None:
                parts.append(f'입력한 피트 총손실 {view["pit_loss_s"]:g}초로 환산하면 같은 클래스 약 {view["rejoin_class_position_estimate"]}위 복귀가 예상됩니다. 상대 피트와 트래픽 변화는 반영되지 않은 추정입니다.')
            else:
                parts.append('복귀 순위는 피트 총손실 입력과 같은 클래스 차량들의 반복 랩 기록이 있어야 계산할 수 있습니다.')
            if view['fuel_to_add_l'] is not None and view['fuel_to_add_l'] > .1 and view['fuel_laps'] is not None and view['fuel_laps'] < 2:
                parts.append('완주 연료가 부족하고 남은 연료가 적으므로 피트를 준비하세요.')
            return ' '.join(parts)
        if view['fuel_to_add_l'] is None:
            return '완주 연료 계산에 필요한 경기 종료 조건 또는 완주랩 소비량이 아직 없습니다. 시간제 경기는 전체 선두와 내 완주 페이스도 필요합니다.'
        return fuel_advice(view)
    if any(w in ''.join(text.split()) for w in ('다른클래스', '멀티클래스', '트래픽')):
        cars = view.get('other_class_traffic', [])
        if not cars:
            return '현재 관측에서 거리 환산 3초 이내의 다른 클래스 차량은 확인되지 않습니다. 주변 시야와 게임 표시도 확인하세요.'
        return ' '.join(f'다른 클래스 차량 {c["name"]}이 {"앞" if c["side"] == "ahead" else "뒤"}에 있습니다. 거리 환산 약 {c["gap_estimate_s"]:.1f}초입니다.' for c in cars[:2])
    side = 'behind' if '뒤' in text else 'ahead' if '앞' in text else None
    target = target_position(text)
    if target is not None or side:
        label = f'클래스 {target}위 차량' if target is not None else f'같은 클래스 {"뒤차" if side == "behind" else "앞차"}'
        if target is not None and target == view.get('class_position'):
            return f'현재 클래스 {target}위는 본인입니다.'
        car = next((c for c in view.get('class_cars', []) if c.get('class_position') == target), None) if target is not None else view.get(side)
        if target is not None and car is None:
            car = next((c for c in (view.get('ahead'), view.get('behind')) if c and c.get('class_position') == target), None)
        if not car:
            return f'{label}의 순위 데이터를 확인할 수 없습니다.'
        reply = f'{label}는 {car["name"]}입니다. '
        if car['gap_estimate_s'] is not None:
            reply += f'랩 진행 거리를 페이스로 환산한 간격은 약 {car["gap_estimate_s"]:.1f}초입니다. '
        elif car['lap_difference'] is not None:
            reply += f'주행 거리 차이는 약 {abs(car["lap_difference"]):.1f}랩입니다. '
        if car['pace_advantage_s'] is not None:
            advantage = car['pace_advantage_s']
            reply += f'최근 {car["pace_samples"]}랩 페이스는 나보다 랩당 {abs(advantage):.1f}초 {"빠릅니다" if advantage > 0 else "느립니다"}.'
        else:
            reply += '반복 완주 페이스는 아직 수집 중입니다.'
        if car.get('gap_trend') in ('closing', 'opening'):
            reply += f' 같은 위치에서 {car["gap_observations"]}회 관측한 간격은 랩당 약 {abs(car["gap_change_s_per_lap"]):.1f}초씩 {"줄고" if car["gap_trend"] == "closing" else "벌어지고"} 있습니다.'
        if car.get('on_pit_road'):
            reply += ' 현재 피트로드에 있습니다.'
        return reply
    parts = [f'현재 같은 클래스 {int(view["class_position"])}위입니다.' if view['class_position'] else '현재 순위는 확인 중입니다.']
    if view['pace_s']:
        parts.append(f'최근 {view["pace_samples"]}랩 중앙 페이스는 {view["pace_s"]:.1f}초입니다.')
    if view['remaining_laps'] is not None:
        parts.append(f'예상 남은 주행 거리는 약 {view["remaining_laps"]:.1f}랩입니다.')
    if int(view.get('flags') or 0) & (0x8 | 0x4000 | 0x8000):
        parts.append('옐로 플래그 또는 풀코스 옐로 상황입니다. 게임 지시를 확인하세요.')
    return ' '.join(parts)
