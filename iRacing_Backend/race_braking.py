"""Race-only evidence for a single brake-bias click and an ABS wet-map check."""
from collections import deque
from statistics import median
from typing import NamedTuple

from live_controls import finite, pedal_ratio
from live_corners import call_seconds
from radio_policy import quiet_window


# Bias increments are percentage points. ABS bands exclude the car's OFF position.
BIAS_STEP = {'bmwm4gt3': 25, 'mclaren720sgt3': 20}  # hundredths of a point
DRY_ABS = {
    'acuransxevo22gt3': range(2, 8),
    'amvantageevogt3': range(1, 7),
    'audir8lmsevo2gt3': range(1, 7),
    'bmwm4gt3': range(1, 7),
    'chevyvettez06rgt3': range(1, 7),
    'ferrari296gt3': range(2, 8),
    'fordmustanggt3': range(2, 8),
    'lamborghinievogt3': range(1, 7),
    'mclaren720sgt3': range(2, 8),
    'mercedesamgevogt3': range(7, 12),  # 1–5 wet, 6 unspecified, 12 OFF.
    'porsche992rgt3': range(1, 7),
}
WET_CHECK = '웨트 ABS 맵 확인.'
ABS_KEY = 'control_abs_wet_map'
BIAS_KEY = 'control_bias_corner'


def bias_ticks(car, value):
    value = finite(value)
    if car not in BIAS_STEP or value is None or not 39.9999 <= value <= 65.0001:
        return None
    ticks = round(value * 100)
    if abs(value * 100 - ticks) < .01 and ticks % BIAS_STEP[car] == 0:
        return ticks


def bias_phrase(ticks):
    return f'바이어스 {ticks / 100:g}퍼센트.'


def voice_catalog():
    ticks = sorted({v for step in BIAS_STEP.values() for v in range(4000, 6501, step)})
    return ([WET_CHECK, '다음 코너.', '다음 YOKOHAMA-S.']
            + [f'다음 {n}번 코너.' for n in range(1, 31)] + [bias_phrase(v) for v in ticks])


class BrakePoint(NamedTuple):
    lap: int
    position: float
    time: float
    brake: float
    speed: float
    wheel_ratios: tuple
    steering: float
    vertical: float
    gear: int
    clean: bool
    settings: tuple


class BrakingSamples:
    """A short rolling approach, including braking before geometric corner entry."""
    def __init__(self):
        self.samples = deque(maxlen=900)

    def ingest(self, sample, solo, mechanical):
        s = sample
        wheels = tuple(finite(s.get(k+'_wheel_speed')) for k in ('LF','RF','LR','RR'))
        speed = finite(s.get('speed_mps'))
        ratios = tuple(w/speed for w in wheels) if speed and speed>0 and None not in wheels else ()
        clean = (solo and s.get('car_left_right')==1 and s.get('track_surface')==3 and s.get('session_state')==4
                 and s.get('session_flags') is not None and not int(s['session_flags'])&0xC119
                 and s.get('incidents') is not None and s.get('on_track') is True and s.get('is_replay') is False
                 and s.get('on_pit_road') is False)
        signature = (s.get('tc_setting'), s.get('abs_setting'), s.get('brake_bias'), s.get('tire_compound'),
                     s.get('track_wetness'), mechanical, s.get('incidents'))
        self.samples.append(BrakePoint(s.get('lap_number'),finite(s.get('distance_pct')),s['session_time'],
                             pedal_ratio(s.get('brake')),speed,ratios,finite(s.get('steering_rad')),
                             finite(s.get('vert_accel_mps2')),s.get('gear'),clean,signature))

    def measure(self, corner, length, car):
        if not length or not self.samples:
            return
        lap, entry, apex = corner['lap'], corner['entry_pct'], corner['apex_pct']
        # Walk back only to the start of this approach; a wrap, teleport or gap invalidates it.
        points = []
        for row in reversed(self.samples):
            if row.lap!=lap or row.position is None:
                break
            if row.position>apex:
                continue
            points.append(row)
            if row.position<entry-150/length-.025:
                break
        points.reverse()
        if len(points)<3 or any(a.position>b.position or not 0<b.time-a.time<=.2 for a,b in zip(points,points[1:])):
            return
        runs = []
        start = None
        for i,p in enumerate(points):
            if p.brake is None:
                return
            if p.brake>.05 and start is None:
                start=i
            if start is not None and (p.brake<=.05 or i==len(points)-1):
                end = i if p.brake<=.05 else i+1
                run = points[start:end]
                if run[-1].position>=entry-150/length and sum(
                        b.time-a.time for a,b in zip(run,run[1:]) if a.brake>.30 and b.brake>.30)>=.25:
                    runs.append((start,run))
                start=None
        if len(runs)!=1:
            return
        index,run = runs[0]
        first = run[0]
        pct, t, v, signature = first.position, first.time, first.speed, first.settings
        ticks = bias_ticks(car,signature[2])
        if (index==0 or pct<entry-150/length or v is None or v<24 or ticks is None
                or None in signature[:5] or any(not p.clean or p.settings!=signature or p.speed is None for p in run)):
            return
        # Compare distance at a fixed speed reduction, interpolating between SDK samples.
        target = v-40/3.6
        crossing = next(((a,b) for a,b in zip(run,run[1:]) if a.speed>target>=b.speed),None)
        if not crossing or run[-1].time-t<.5:
            return
        a,b = crossing
        dist = (a.position+(b.position-a.position)*(a.speed-target)/(a.speed-b.speed)-pct)*length
        # Integral of pedal input over the first half-second (independent of sample rate).
        pedal = 0.
        for a,b in zip(run,run[1:]):
            dt = min(t+.5,b.time)-a.time
            if dt<=0:
                break
            pedal += dt*(2*a.brake+(b.brake-a.brake)*dt/(b.time-a.time))
        baseline = [p for p in points[:index] if t-.5<=p.time<t and p.clean and p.settings==signature
                    and p.brake<.02 and p.steering is not None and abs(p.steering)<.12 and p.wheel_ratios]
        axle = set()
        usable_since = None
        measured = False
        if len(baseline)>=5 and all(.92<x<1.08 for p in baseline for x in p.wheel_ratios):
            reference = [median(p.wheel_ratios[w] for p in baseline) for w in range(4)]
            previous_gear = points[index-1].gear
            gear_since = points[index-1].time
            for p in reversed(points[:index]):
                if p.gear!=previous_gear:
                    break
                gear_since=p.time
            held, since = None,t
            for p in run:
                side=None
                if p.gear!=previous_gear:
                    previous_gear,gear_since=p.gear,p.time
                if (p.brake>=.3 and p.speed>22 and p.gear is not None and p.gear>0 and p.time-gear_since>.25
                        and p.steering is not None and abs(p.steering)<.16 and p.vertical is not None and abs(p.vertical-9.81)<4
                        and p.wheel_ratios and all(.55<q<1.6 for q in p.wheel_ratios)):
                    lf,rf,lr,rr = [1-q/ref for q,ref in zip(p.wheel_ratios,reference)]
                    if usable_since is None:
                        usable_since=p.time
                    measured |= p.time-usable_since>=.2
                    if min(lf,rf)>.07 and max(lr,rr)<.05 and min(lf-lr,rf-rr)>.04:
                        side='front'
                    elif min(lr,rr)>.07 and max(lf,rf)<.05 and min(lr-lf,rr-rf)>.04:
                        side='rear'
                else:
                    usable_since=None
                if held!=side:
                    held,since=side,p.time
                if side and p.time-since>=.2:
                    axle.add(side)
        if measured and len(axle)<=1:
            return dict(ticks=ticks,tc=signature[0],abs=signature[1],onset_m=pct*length,speed=v*3.6,pedal=pedal,distance=dist,
                        axle=next(iter(axle),None))


def bias_target(rows, car, sample, condition, mechanical):
    """Compare two independent pairs; a contradictory matched pair vetoes the target."""
    current = bias_ticks(car,sample.get('brake_bias'))
    if current is None or None in condition:
        return
    history = [r for r in rows if r.get('braking') and None not in r['condition'] and r['mechanical']==mechanical
               and r['condition'][2:]==condition[2:] and abs(r['condition'][1]-condition[1])<=3
               and abs(r['condition'][0]-condition[0])<=10 and all(
                   r['braking'][k]==r['controls'].get(k)==sample.get(k+'_setting') for k in ('tc','abs'))]
    old = [r for r in history if r['braking']['ticks']==current]
    for target in (current-BIAS_STEP[car],current+BIAS_STEP[car]):
        other = [r for r in history if r['braking']['ticks']==target]
        if len(old)<2 or len(other)<2:
            continue
        side = 'front' if target<current else 'rear'
        candidates = []
        for a in old:
            for b in other:
                x,y = a['braking'],b['braking']
                diffs = [abs(x[k]-y[k])/limit for k,limit in (('onset_m',10),('speed',3),('pedal',.1))]
                if max(diffs)<=1 and abs(a['condition'][0]-b['condition'][0])<=10 and abs(a['condition'][1]-b['condition'][1])<=3:
                    candidates.append((sum(diffs),a,b))
        used=set()
        for _,a,b in sorted(candidates,key=lambda p:p[0]):
            if a['lap'] in used or b['lap'] in used:
                continue
            x,y = a['metrics'],b['metrics']
            if (a['braking']['axle']!=side or b['braking']['axle'] is not None
                    or b['braking']['distance']>a['braking']['distance']-5 or y['time_s']>x['time_s']+.1
                    or y['end_speed']<x['end_speed']-1 or y['corrections']>x['corrections']):
                break
            used.update((a['lap'],b['lap']))
        else:
            if len(used)>=4:
                return target


class WetAbsReminder:
    def __init__(self):
        self.episode = None
        self.last = self.stable = self.window_since = self.mode = self.level = None
        self.announced = False
        self.event = None
        self.attempts = 0
        self.next_try = 0.

    def ingest(self, car, sample, context, radio, now, ready, occupied):
        s = sample
        t = s['session_time']
        level = finite(s.get('abs_setting'))
        tire = finite(s.get('tire_compound'))
        mismatch = (car in DRY_ABS and str(radio.metadata.get('session_type') or '').lower()=='race' and not radio.result
                    and s.get('on_track') is True and s.get('is_replay') is False and s.get('session_state')==4
                    and radio.control_available('abs')
                    and s.get('setup_fixed') is False and tire is not None and tire>=0 and
                    tire in (radio.metadata.get('wet_compounds') or ()) and s.get('track_wetness') in (4,5,6,7)
                    and level in DRY_ABS[car])
        episode = (radio.run_id,car,tire) if mismatch else None
        if episode!=self.episode:
            self.__init__()
            self.episode=episode
        if episode is None:
            return
        if self.last is None or not 0<t-self.last<=.5 or self.level!=level:
            self.stable=t
            self.window_since=None
        self.last,self.level=t,level
        radio.control_trials.mark('abs', (episode,self.stable), 'wet_dry_map_observed')
        speed = finite(s.get('speed_mps'))
        green = (s.get('session_flags') is not None and not int(s['session_flags'])&0xC119
                 and s.get('tow_time_s')==0)
        mode=None
        if green and speed is not None:
            if s.get('on_pit_road') is True and s.get('track_surface')==1 and 0<=speed<.5:
                mode='pit'
            elif (context=='SOLO' and s.get('car_left_right')==1 and s.get('track_surface')==3
                  and s.get('on_pit_road') is False and speed>15 and quiet_window(s)):
                mode='straight'
        if self.mode!=mode or self.window_since is None:
            self.window_since=t
        self.mode=mode
        if self.announced or self.attempts>=3 or now<self.next_try or t-self.stable<30:
            return
        opportunity = (self.episode, self.stable)
        log = radio.control_trials
        log.mark('abs', opportunity, 'evidence')
        if not radio.allows(ABS_KEY):
            log.mark('abs', opportunity, 'disabled')
            return
        if occupied or not self.valid(s,radio,now):
            log.mark('abs', opportunity, 'occupied' if occupied else 'no_window')
            return
        log.mark('abs', opportunity, 'candidate')
        self.next_try=now+1
        if not ready(WET_CHECK):
            log.mark('abs', opportunity, 'audio_missing')
            return
        if any(e['group']=='control_advice' and radio.valid(e,now) for e in radio.pending):
            log.mark('abs', opportunity, 'occupied')
            return
        event=radio.emit(ABS_KEY,now,WET_CHECK,role='coach',ttl=3,cooldown=0,group='control_advice',
                         condition=('wet_abs',(episode,level,mode)),speech_text=WET_CHECK,prepared_only=True)
        if event:
            log.mark('abs', event['id'], 'queued')
            self.event=event['id']
            self.attempts+=1
            self.next_try=now+15

    def valid(self,s,radio,now):
        if (self.episode is None or self.mode is None or self.last!=s.get('session_time')
                or not radio.control_available('abs')
                or self.last-self.stable<30 or self.last-self.window_since<1):
            return False
        if self.mode=='pit':
            return True
        lead=finite(s.get('workload_in_s'))
        return lead is not None and lead>=call_seconds(WET_CHECK)+3.5 and radio.control_window(s,WET_CHECK,now)

    def event_valid(self,token,s,radio,now):
        return token==(self.episode,self.level,self.mode) and self.valid(s,radio,now)

    def delivery(self,identifier,status):
        if identifier==self.event and status in ('started','completed'):
            self.announced=True
