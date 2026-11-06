"""Personal, observed performance windows; never a universal tire optimum.

One record is one complete, stable turn segment, not a telemetry frame. Faster
passage times remain an outcome: speed increases are not regressed away. G and
ABS describe the passage; neither a G spike nor less ABS alone earns a label.
"""
from collections import deque
import hashlib
import json
import math
from pathlib import Path
from queue import Empty, Queue
from statistics import median
import threading
import uuid

from .sdk import finite
from .snapshot import WHEELS, TireReadings

MAX_RECORDS = 1800
MAX_FILE_BYTES = 2_000_000
MIN_PASSES, MIN_CORNERS, MIN_LAPS = 12, 3, 3
NEAR_BEST = 1.05                 # initial evidence rule, not a physical optimum
TEMP_RADIUS_C, PRESSURE_RADIUS_KPA = 4., 3.
FRESH_S, HOLD_S = 2.5, 3.
G = 9.80665


def pressure_only(snapshot):
    """A verified absent display is distinct from an unreadable temperature."""
    if snapshot.game != 'iracing':
        return False
    from .vehicle_dash import VEHICLES
    profile = VEHICLES.get(snapshot.car)
    return profile is not None and not profile['temperature']


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid_record(row):
    if not isinstance(row, dict):
        return False
    if not all(isinstance(row.get(k), str) and len(row[k]) <= 64 for k in ('key', 'run', 'corner')):
        return False
    if not all(finite(row.get(k)) is not None for k in
               ('lap','duration','exit_speed','lateral_g','decel_g','brake','abs_fraction','track','air','fuel','age','position')):
        return False
    if not 0 < row['duration'] <= 30 or not 0 <= row['position'] < 1 or row['lap'] < 0:
        return False
    for field, low, high in (('temps', -50, 250), ('pressures', 40, 400)):
        values = row.get(field)
        if field == 'temps' and row.get('mode') == 'pressure_only':
            if values != [None]*4:
                return False
            continue
        if not isinstance(values, list) or len(values) != 4 or not all(
                finite(v) is not None and low <= v <= high for v in values):
            return False
    return True


class PerformanceStore:
    """Bounded local file; a single writer coalesces updates off the OCR thread."""
    def __init__(self, path):
        self.path = Path(path)
        self.error = False
        self.writable = True
        self.queue = Queue(maxsize=1)
        self.thread = None

    def load(self):
        try:
            if not self.path.exists():
                return []
            if self.path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('Oversized performance memory')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            rows = data.get('records') if isinstance(data, dict) else None
            if data.get('version') != 1 or not isinstance(rows, list) or len(rows) > MAX_RECORDS or not all(valid_record(r) for r in rows):
                raise ValueError('Invalid performance memory')
            return rows
        except (OSError, ValueError, AttributeError):
            self.error = True
            self.writable = False  # never overwrite a corrupt or newer file
            return []

    def submit(self, rows):
        if not self.writable:
            return
        if self.thread is None:
            self.thread = threading.Thread(target=self._write, name='tire-performance-store', daemon=True)
            self.thread.start()
        try:
            self.queue.get_nowait()
            self.queue.task_done()
        except Empty:
            pass
        self.queue.put_nowait(list(rows))

    def _write(self):
        while True:
            rows = self.queue.get()
            try:
                if rows is None:
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                try:
                    text = json.dumps({'version': 1, 'records': rows}, allow_nan=False, separators=(',', ':'))
                    if len(text.encode('utf-8')) > MAX_FILE_BYTES:
                        raise ValueError('Oversized performance memory')
                    temporary.write_text(text, encoding='utf-8')
                    temporary.replace(self.path)
                    self.error = False
                finally:
                    temporary.unlink(missing_ok=True)
            except (OSError, ValueError):
                self.error = True
            finally:
                self.queue.task_done()

    def close(self):
        if self.thread is not None:
            self.queue.join()
            self.queue.put(None)
            self.thread.join(timeout=3)
            self.thread = None


class TirePerformance:
    def __init__(self, path=None):
        self.records = deque(maxlen=MAX_RECORDS)
        self.store = None
        if path is not None:
            self.attach(path)
        self.reset()

    def attach(self, path):
        if self.store is None:
            self.store = PerformanceStore(path)
            self.records.extend(self.store.load())

    def close(self):
        if self.store is not None:
            self.store.close()

    def reset(self, reason='insufficient_passes'):
        self.identity = self.run = self.key = None
        self.controls = None
        self.last_t = self.previous = self.active = None
        self.tires = deque(maxlen=8)
        self.readings = TireReadings()
        self.incidents = None
        self.cool_until = -math.inf
        self.pit = False
        self.stint = uuid.uuid4().hex
        self.hold_since = None
        self.axle_since = [None,None]
        self.input_ok = False
        self.mode = 'combined'
        self.view = {'status': 'learning', 'reason': reason, 'front': False, 'rear': False,
                     'passes': 0, 'corners': 0, 'laps': 0, 'ranges': {}, 'pressure_ranges': {}, 'mode':self.mode}

    @property
    def token(self):
        return self.stint

    def _context_key(self, s, sample):
        only_pressure = pressure_only(s)
        kinds = {v.temp_kind for v in s.wheels.values() if v.temp_c is not None}
        wet = s.track_wetness_iracing if s.game == 'iracing' else s.track_wetness_fraction_lmu
        controls = [finite(sample.get(k)) for k in ('abs_setting','tc_setting','brake_bias')]
        cold = [finite(s.installed_cold_kpa.get(w)) for w in WHEELS]
        if ((not only_pressure and (len(kinds) != 1 or None in kinds)) or wet is None or s.tire_compound_index is None
                or any(v is None for v in controls + cold)):
            return None
        return _hash([s.game, s.car, s.track, 'pressure_only' if only_pressure else next(iter(kinds)), s.tire_compound_index,
                      round(wet, 2), controls, [round(v, 1) for v in cold]])

    def observe(self, s, context):
        if (s is None or not context or not context.get('run') or not s.active or s.t is None
                or s.lap is None or s.on_pit_road is not False
                or s.session_type not in ('Race','Practice','Offline Testing')):
            if s is not None and s.on_pit_road is True:
                self.pit = True
            self._invalidate('inactive_or_missing_context')
            self.active = self.previous = None
            return
        if s.identity != self.identity or context['run'] != self.run or self.last_t is not None and s.t < self.last_t:
            self.reset()
            self.identity, self.run = s.identity, context['run']
        if self.pit:
            self.stint = uuid.uuid4().hex
            self.pit = False
            self.previous = self.active = None
            self.readings = TireReadings()
        if self.last_t is not None and s.t <= self.last_t:
            self._invalidate('measurement_gap')
            self.active = self.previous = None
            return
        if self.last_t is not None and s.t-self.last_t > 3:
            self.active = self.previous = None
            self._invalidate('measurement_gap')
        self.last_t = s.t
        self.mode = 'pressure_only' if pressure_only(s) else 'combined'
        self.view['mode'] = self.mode
        rows = context.get('samples') or []
        if not rows:
            self._invalidate('missing_driving_channels')
            return
        key = self._context_key(s, rows[-1])
        if key is None:
            self._invalidate('unknown_conditions')
            self.previous = self.active = None
            return
        if key != self.key:
            self.key = key
            self.controls = tuple(finite(rows[-1].get(k)) for k in ('abs_setting','tc_setting','brake_bias'))
            self.previous = self.active = None
            self.tires.clear()
            self.readings = TireReadings()
            self._invalidate('conditions_changed')
        self.readings.update(s)
        self.tires.append(s)
        for row in rows:
            self._input(row, s, context.get('corners') or [])
        self._evaluate(s)

    def _invalidate(self, reason):
        self.hold_since = None
        self.axle_since = [None,None]
        self.input_ok = False
        self.view.update(status='learning', reason=reason, front=False, rear=False)

    def _input(self, row, s, definitions):
        at = finite(row.get('session_time'))
        if at is None or self.previous is not None and at <= self.previous['session_time']:
            return
        self.input_ok = False
        prev, self.previous = self.previous, row
        if row.get('incidents') is not None:
            if self.incidents is not None and row['incidents'] > self.incidents:
                self.cool_until = at + 8
            self.incidents = row['incidents']
        numeric = [finite(row.get(k)) for k in ('distance_pct','speed_mps','steering_rad','yaw_rate',
                   'brake','throttle','lat_accel_mps2','long_accel_mps2','vert_accel_mps2')]
        if (any(v is None for v in numeric) or row.get('on_track') is not True
                or row.get('on_pit_road') is not False or row.get('is_replay') is not False
                or row.get('car_left_right') not in (0,1) or row.get('incidents') is None
                or row.get('session_flags') is None or int(row['session_flags']) & 0xC108 or at < self.cool_until
                or tuple(finite(row.get(k)) for k in ('abs_setting','tc_setting','brake_bias')) != self.controls):
            self.active = None
            return
        pct, speed, steer, yaw, brake, throttle, lat, long, vert = numeric
        if prev is None or not 0 <= pct < 1 or not 0 < at-prev['session_time'] <= .5:
            self.active = None
            return
        # A fixed distance window is a fallback where file analysis has not yet
        # saved corners. Net heading and sign coherence reject straight weaves.
        definition = next((d for d in definitions if d['entry_pct'] <= pct < d['exit_pct']), None)
        lo, hi = (definition['entry_pct'], definition['exit_pct']) if definition else (int(pct*50)/50, (int(pct*50)+1)/50)
        corner = _hash([round(lo,6),round(hi,6)])
        dt = at-prev['session_time']
        step = pct-finite(prev.get('distance_pct')) if finite(prev.get('distance_pct')) is not None else -1
        if step < 0 or step > .025 or row.get('lap_number') != prev.get('lap_number'):
            self.active = None
        if definitions and definition is None:
            if self.active is not None:
                exit_at=at-(pct-self.active['hi'])/max(step,1e-12)*dt
                self._finish(prev,exit_at)
            self.input_ok = True
            return  # known straights must never become fallback turn evidence
        if self.active and (corner != self.active['corner'] or row.get('lap_number') != self.active['lap']):
            exit_at = at-(pct-self.active['hi'])/max(step,1e-12)*dt
            self._finish(prev,exit_at)
        self.input_ok = True
        if self.active is None:
            before = finite(prev.get('distance_pct'))
            if before is None or not before <= lo <= pct or pct-before > .025:
                return  # never time a partial corner as a complete passage
            self.active = {'corner':corner,'lo':lo,'hi':hi,'lap':row.get('lap_number'),
                'start':at-(pct-lo)/max(pct-before,1e-12)*dt,'end':at,
                'samples':[],'tires':{},'clean':True,'defined':definition is not None}
        a = self.active
        a['end'] = at
        a['clean'] &= speed >= 10 and abs(yaw) < 1.5
        a['samples'].append((dt,speed,steer,yaw,brake,abs(lat)/G,max(0,-long/G),row.get('abs_active'),.6<=vert/G<=1.4))
        # Direct observations near this sample, never held overlay values.
        for tire in self.tires:
            if abs(tire.t-at) <= 1.1:
                a['tires'][tire.t] = tire

    def _finish(self, last, exit_at=None):
        a, self.active = self.active, None
        rows = a['samples']
        if exit_at is not None:
            a['end'] = exit_at
        if (not a['clean'] or len(rows) < 20 or last['distance_pct'] < a['hi']-.003
                or not 1 <= a['end']-a['start'] <= 30):
            return
        turning = [r for r in rows if r[8] and abs(r[2])>.08 and abs(r[3])>.12 and r[5]>.5]
        signed = sum(r[0]*r[3] for r in rows)
        total = sum(r[0]*abs(r[3]) for r in rows)
        if len(turning)/len(rows)<.5 or total < .2 or abs(signed)/total < .75 or sum(not r[8] for r in rows)/len(rows)>.15:
            return
        tires = [r for t,r in a['tires'].items() if a['start']-.5 <= t <= a['end']+.5]
        values = []
        for attr in ('temp_c','pressure_kpa'):
            if attr == 'temp_c' and self.mode == 'pressure_only':
                values.extend([None]*4)
                continue
            for w in WHEELS:
                found = [getattr(r.wheels[w],attr) for r in tires if getattr(r.wheels[w],attr) is not None]
                if not found:
                    return
                if attr == 'temp_c' and max(found)-min(found)>12:
                    return
                values.append(median(found))
        s = tires[-1]
        conditions = [s.track_temp_c,s.air_temp_c,s.fuel_l]
        installed = [s.installed_lap.get(w) for w in WHEELS]
        if any(v is None for v in conditions+installed) or a['lap'] is None:
            return
        braking = [r for r in rows if r[4]>.05]
        if any(type(r[7]) is not bool for r in braking):
            return
        record = dict(key=self.key,run=_hash(self.run),corner=a['corner'],lap=a['lap'],mode=self.mode,
            position=(a['lo']+a['hi'])/2,duration=a['end']-a['start'],exit_speed=median([r[1] for r in rows[-5:]]),
            lateral_g=median([r[5] for r in turning]),decel_g=median([r[6] for r in braking if r[8]]) if any(r[8] for r in braking) else 0.,
            brake=median([r[4] for r in braking]) if braking else 0.,
            abs_fraction=sum(bool(r[7]) for r in braking)/len(braking) if braking else 0.,
            temps=values[:4],pressures=values[4:],track=conditions[0],air=conditions[1],fuel=conditions[2],
            age=a['lap']-min(installed))
        if valid_record(record) and not any((r['run'],r['lap'],r['corner']) == (record['run'],record['lap'],record['corner']) for r in self.records):
            self.records.append(record)
            if self.store:
                self.store.submit(self.records)

    def _references(self, s):
        installed = [s.installed_lap.get(w) for w in WHEELS]
        if (not installed or any(v is None for v in (s.track_temp_c,s.air_temp_c,s.fuel_l,*installed))):
            return []
        run = _hash(self.run)
        age = s.lap-min(installed)
        return [r for r in self.records if r['key']==self.key
            and not (r['run']==run and r['lap']==s.lap)
            and abs(r['track']-s.track_temp_c)<=3 and abs(r['air']-s.air_temp_c)<=3
            and abs(r['age']-age)<=4]

    @staticmethod
    def _good(rows, comparisons=None):
        grouped = {}
        for r in rows if comparisons is None else comparisons:
            grouped.setdefault(r['corner'],[]).append(r)
        good = []
        baselines = {}
        for r in rows:
            # Validate performance at each pass's fuel load, then accumulate
            # those validated passes independently of today's falling fuel.
            condition = (r['corner'],r['track'],r['air'],r['fuel'],r['age'])
            if condition not in baselines:
                group = [other for other in grouped.get(r['corner'],[]) if
                         abs(other['track']-r['track'])<=3 and abs(other['air']-r['air'])<=3
                         and abs(other['age']-r['age'])<=4]
                local = [other for other in group if abs(other['fuel']-r['fuel'])<=6]
                if len({(other['run'],other['lap']) for other in local})<2:
                    baselines[condition] = None
                else:
                    # A faster pass on a heavier fuel load remains a conservative
                    # benchmark. Burning fuel must not erase a faster cold pass.
                    benchmark = [other for other in group if other['fuel']>=r['fuel']-6]
                    best = sorted(benchmark,key=lambda other:other['duration'])[:2]
                    two_laps = len({(other['run'],other['lap']) for other in benchmark})==2
                    # With only two comparable laps, their average could admit a
                    # pass 10% slower than the faster one. Keep the strict 5% limit.
                    baselines[condition] = (best[0]['duration'] if two_laps else median(other['duration'] for other in best),
                        best[0]['exit_speed']-2 if two_laps else min(other['exit_speed'] for other in best)-2)
            if baselines[condition] is None:
                continue
            reference, exit_floor = baselines[condition]
            if r['duration'] <= reference*NEAR_BEST and r['exit_speed']>=exit_floor:
                good.append(r)
        return good

    @staticmethod
    def _evidence(rows):
        positions = sorted({r['position'] for r in rows})
        separated = []
        for p in positions:
            if not separated or p-separated[-1] >= .04:
                separated.append(p)
        corner_laps = {}
        for r in rows:
            corner_laps.setdefault(r['corner'],set()).add((r['run'],r['lap']))
        corners = {key for key,laps in corner_laps.items() if len(laps)>=2}
        laps = {(r['run'],r['lap']) for r in rows}
        return {'passes':len(rows),'corners':min(len(corners),len(separated)),'laps':len(laps)}

    @staticmethod
    def _supported(rows, temps, pressures, indices, only_pressure=False):
        nearby = [r for r in rows if all((only_pressure or abs(r['temps'][i]-temps[i])<=TEMP_RADIUS_C)
                   and abs(r['pressures'][i]-pressures[i])<=PRESSURE_RADIUS_KPA for i in indices)]
        counts = TirePerformance._evidence(nearby)
        enough = counts['passes']>=MIN_PASSES and counts['corners']>=MIN_CORNERS and counts['laps']>=MIN_LAPS
        # Joint neighbours plus observed bounds: never fill an untested rectangle
        # of temperature/pressure combinations or extrapolate past the hot edge.
        inside = bool(nearby) and all((only_pressure or min(r['temps'][i] for r in nearby)-.5 <= temps[i] <= max(r['temps'][i] for r in nearby)+.5)
            and min(r['pressures'][i] for r in nearby)-.5 <= pressures[i] <= max(r['pressures'][i] for r in nearby)+.5 for i in indices)
        return enough and inside, nearby, counts

    def _evaluate(self, s):
        run = _hash(self.run)
        comparisons = [r for r in self.records if r['key']==self.key and not (r['run']==run and r['lap']==s.lap)]
        rows = self._good(self._references(s), comparisons)
        counts = self._evidence(rows)
        self.view.update(counts)
        self.view['store_error'] = bool(self.store and self.store.error)
        self.view['ranges'] = {name:[round(min(r['temps'][i] for r in rows for i in indices),1),
                                     round(max(r['temps'][i] for r in rows for i in indices),1)]
                               for name,indices in (('front',(0,1)),('rear',(2,3)))} if rows and self.mode != 'pressure_only' else {}
        self.view['pressure_ranges'] = {name:[round(min(r['pressures'][i] for r in rows for i in indices),1),
                                              round(max(r['pressures'][i] for r in rows for i in indices),1)]
                               for name,indices in (('front',(0,1)),('rear',(2,3)))} if rows else {}
        temps = [self.readings.value(w,'temperature',s.observed_at,FRESH_S) for w in WHEELS]
        pressures = [self.readings.value(w,'pressure',s.observed_at,FRESH_S) for w in WHEELS]
        only_pressure = self.mode == 'pressure_only'
        if any(v is None for v in (pressures if only_pressure else temps+pressures)):
            self._invalidate('measurement_gap')
            return
        joint, nearby, _ = self._supported(rows,temps,pressures,range(4),only_pressure)
        front,_,_ = self._supported(rows,temps,pressures,(0,1),only_pressure)
        rear,_,_ = self._supported(rows,temps,pressures,(2,3),only_pressure)
        # No green while there is no current safe telemetry, even if the tire
        # numbers themselves are still fresh after a spin or a gap.
        fresh = self.input_ok and self.previous and 0 <= s.t-self.previous['session_time'] <= 1 and s.t>=self.cool_until
        if not fresh:
            self._invalidate('missing_driving_channels')
            return
        axle_ready=[]
        for i,matched in enumerate((front,rear)):
            if not matched:
                self.axle_since[i] = None
            elif self.axle_since[i] is None:
                self.axle_since[i] = s.t
            axle_ready.append(matched and s.t-self.axle_since[i]>=HOLD_S)
        if joint:
            if self.hold_since is None:
                self.hold_since = s.t
            ready = s.t-self.hold_since >= HOLD_S
            self.view.update(status='ready' if ready else 'confirming',reason='observed_performance',front=axle_ready[0],rear=axle_ready[1])
        else:
            self.hold_since = None
            if all(axle_ready):
                axle_ready=[False,False]  # individually known values, untested combination
            self.view.update(status='partial' if any(axle_ready) else 'learning',reason='insufficient_matching_passes',front=axle_ready[0],rear=axle_ready[1])
        self.view['physical_optimum_confirmed'] = False


def display_learning(view, snapshot, now):
    """Child-side expiry is independent of new OCR frames or parent messages."""
    if (not isinstance(view,dict) or not isinstance(snapshot,dict)
            or finite(view.get('updated_at')) is None or not 0 <= now-view['updated_at'] < FRESH_S
            or any(view.get(k)!=snapshot.get(k) for k in ('game','car','session_key','epoch'))
            or snapshot.get('active') is not True or snapshot.get('on_pit_road') is not False):
        return {'status':'learning','front':False,'rear':False}
    return {k:view.get(k) for k in ('status','front','rear','mode')}
