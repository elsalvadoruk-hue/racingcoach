"""Bounded within-stint comparisons; trends are observations, never tyre life.

Detection margins reject small changes; they are not optimal temperatures.
One-second OCR supplies lap trends, existing faster telemetry supplies inputs.
"""
from collections import deque
from itertools import islice
from statistics import median
from live_controls import finite
from .log_export import linked_run
from .snapshot import WHEELS


def live_context(live, snapshot):
    run=linked_run(live,snapshot)
    if not run or not live.lock.acquire(timeout=.02):return None
    try:
        if live.run_id!=run or not live.samples:return None
        last=live.samples[-1]
        if snapshot.t is None or abs(last['session_time']-snapshot.t)>1.5:return None
        # Only numeric/boolean driving channels, never opponents or driver names.
        keys=('session_time','lap_number','distance_pct','speed_mps','steering_rad','steering_lock_ratio',
              'yaw_rate','brake','throttle','incidents','session_flags','car_left_right',
              'on_track','on_pit_road','is_replay','abs_active','abs_setting','tc_setting','brake_bias',
              'lat_accel_mps2','long_accel_mps2','vert_accel_mps2')
        rows=[{k:r.get(k) for k in keys} for r in islice(reversed(live.samples),96)
              if r.get('run_id')==run and 0<=snapshot.t-r['session_time']<3.2]
        definitions=getattr(getattr(live,'corners',None),'definitions',())
        corners=[{k:d[k] for k in ('entry_pct','exit_pct')} for d in list(definitions)[:64]
                 if isinstance(d,dict) and all(finite(d.get(k)) is not None for k in ('entry_pct','exit_pct'))
                 and 0<=d['entry_pct']<d['exit_pct']<=1]
        return {'run':run,'samples':list(reversed(rows)),'corners':corners}
    finally:live.lock.release()


def comparable(a,b):
    return (a['kind']==b['kind'] and a['session_type']==b['session_type']
        and all(finite(a[k]) is not None and finite(b[k]) is not None and abs(a[k]-b[k])<=limit
                for k,limit in (('air',2),('track',2),('fuel',3)))
        and a['wet'] is not None and a['wet']==b['wet']
        and a['compound'] is not None and a['compound']==b['compound'])


class TireCoaching:
    def __init__(self):self.reset()

    def reset(self):
        self.identity=None;self.run=None;self.start_lap=None;self.lap=None;self.row=None
        self.laps=deque(maxlen=8);self.last_t=None;self.last_sample=None;self.sign=[0.,0.]
        self.candidate=None;self.baseline=None;self.thermal_spoken=False;self.recovered=False;self.last_temperature=None
        self.view={'ready':False,'reason':'insufficient_laps'}

    def _new(self,s):
        self.lap=s.lap
        self.row={'lap':s.lap,'temps':deque(maxlen=1800),'kind':None,'cells':{},'sites':set(),'counter_run':0.,
            'clean':True,'samples':0,'start':s.t,'end':s.t,'incidents':None,'pace':None,
            'air':s.air_temp_c,'track':s.track_temp_c,'fuel':s.fuel_l,
            'compound':s.tire_compound_index,'wet':s.track_wetness_iracing if s.game=='iracing' else s.track_wetness_fraction_lmu,
            'session_type':s.session_type}

    def observe(self,s,context):
        key=s.identity
        if (not context or not context.get('run') or not s.active or s.on_pit_road is not False
            or s.session_type not in ('Race','Practice','Offline Testing') or s.t is None or s.lap is None):
            self.reset();self.view['reason']='no_linked_context' if not context else 'inactive_or_unsupported_session';return
        if key!=self.identity or context['run']!=self.run or self.last_t is not None and s.t<self.last_t:
            self.reset();self.identity=key;self.run=context['run'];self.start_lap=s.lap
        if self.last_t is not None and s.t<=self.last_t:return
        if self.lap!=s.lap:
            if self.row and s.lap==self.lap+1:
                self.row['pace']=s.lap_last_s
                self._finish()
            elif self.row:
                self.reset();self.identity=key;self.run=context['run'];self.start_lap=s.lap
            self._new(s)
        r=self.row
        if self.last_t is not None and s.t-self.last_t>3:r['clean']=False;self.candidate=None
        self.last_t=s.t;r['end']=s.t
        # Unknown/mixed kinds are never treated as comparable surface/carcase data.
        kinds={v.temp_kind for v in s.wheels.values() if v.temp_c is not None}
        values=[s.wheels[w].temp_c for w in WHEELS]
        if len(kinds)==1 and None not in kinds and all(v is not None for v in values):
            kind=next(iter(kinds))
            if r['kind'] is not None and kind!=r['kind']:r['clean']=False;self.candidate=None
            r['kind']=kind;r['temps'].append((sum(values[:2])/2,sum(values[2:])/2))
            if self.last_temperature is not None and s.t-self.last_temperature>3:r['clean']=False
            self.last_temperature=s.t
        else:self.candidate=None
        for sample in context.get('samples',[]):self._input(sample)
        if not r['clean']:self.candidate=None;self.view['reason']='excluded_current_lap'
        # Keep an advice valid only while the current fresh pair confirms its trend.
        if self.candidate and self.baseline and all(v is not None for v in values):
            current=(sum(values[:2])/2,sum(values[2:])/2)
            if self.candidate!='temperature_recovered':
                axle=0 if self.candidate.endswith('front') else 1
                if current[axle]-self.baseline[axle]<3:self.candidate=None
            elif any(current[i]>self.baseline[i]+3 for i in (0,1)):self.candidate=None
        self.view['candidate']=self.candidate

    def _input(self,s):
        at=finite(s.get('session_time'))
        if at is None or self.last_sample and at<=self.last_sample['session_time']:return
        prev=self.last_sample;self.last_sample=s
        r=self.row
        if s.get('lap_number')!=self.lap:return
        if (s.get('on_track') is not True or s.get('on_pit_road') is not False or s.get('is_replay') is not False
            or s.get('incidents') is None or s.get('session_flags') is None or int(s['session_flags'])&0xC108
            or s.get('car_left_right') not in (0,1)):
            r['clean']=False;return
        if r['incidents'] is not None and s['incidents']!=r['incidents']:r['clean']=False
        r['incidents']=s['incidents']
        if not prev:return
        dt=at-prev['session_time']
        if not 0<dt<=.5:r['clean']=False;return
        pct,speed,steer,yaw,brake,throttle=(finite(s.get(k)) for k in
            ('distance_pct','speed_mps','steering_rad','yaw_rate','brake','throttle'))
        if None in (pct,speed,steer,yaw,brake,throttle) or not 0<=pct<1:return
        r['samples']+=1
        turning=abs(steer)>.08 and abs(yaw)>.15 and speed>15
        if turning:self.sign[steer*yaw>0]+=dt
        known=sum(self.sign)>20 and max(self.sign)>4*min(self.sign)
        counter=turning and known and (steer*yaw>0)!=(self.sign[1]>self.sign[0])
        power=counter and throttle>.5 and brake<.05
        r['counter_run']=r['counter_run']+dt if power else 0.
        if r['counter_run']>=.25:r['sites'].add(int(pct*100))
        if turning and not counter and brake<.05 and .1<throttle<.6:
            cell=int(pct*100)
            v=r['cells'].setdefault(cell,[0.,0.,0.,0.,0.])
            for i,val in enumerate((speed,abs(steer),abs(yaw)/speed,throttle,1.)):v[i]+=val*dt

    def _finish(self):
        r=self.row
        if (not r['clean'] or r['lap']-self.start_lap<2 or len(r['temps'])<30 or r['samples']<100
            or r['end']-r['start']<30):
            self.candidate=None;self.laps.clear();self.view={'ready':False,'reason':'excluded_lap'};return
        r['temperature']=tuple(median(pair[i] for pair in r['temps']) for i in (0,1))
        r['cells']={k:tuple(v[i]/v[4] for i in range(4)) for k,v in r['cells'].items() if v[4]>=.2}
        r.pop('temps');self.laps.append(r)
        if len(self.laps)<4:return
        base=list(self.laps)[:2];recent=list(self.laps)[-2:]
        if not all(comparable(a,b) for a in base for b in recent):
            self.candidate=None;self.view={'ready':False,'reason':'conditions_changed'};return
        baseline=tuple(median(a['temperature'][i] for a in base) for i in (0,1))
        delta=tuple(min(a['temperature'][i] for a in recent)-baseline[i] for i in (0,1))
        self.baseline=baseline;self.candidate=None
        front=delta[0]>=5 and delta[0]-delta[1]>=3
        rear=delta[1]>=5 and delta[1]-delta[0]>=3
        if front:self.candidate='input_front' if self._front(base,recent) else 'trend_front'
        elif rear:self.candidate='input_rear' if self._rear(base,recent) else 'trend_rear'
        elif self.thermal_spoken and not self.recovered and all(abs(r['temperature'][i]-baseline[i])<=3 for r in recent for i in (0,1)):
            self.candidate='temperature_recovered'
        before=[r['pace'] for r in base];after=[r['pace'] for r in recent]
        pace=(median(after)-median(before)) if all(finite(v) is not None for v in before+after) else None
        self.view={'ready':True,'reason':'own_matched_laps','baseline_laps':[r['lap'] for r in base],
            'recent_laps':[r['lap'] for r in recent],'front_change_c':round(delta[0],2),
            'rear_change_c':round(delta[1],2),'pace_change_s':round(pace,3) if pace is not None else None,
            'candidate':self.candidate,'wear_or_grip_confirmed':False}

    @staticmethod
    def _separate(cells):return any(min(abs(a-b),100-abs(a-b))>=5 for a in cells for b in cells)

    def _front(self,base,recent):
        hits=[]
        for r in recent:
            found=set()
            for cell,(speed,steer,response,throttle) in r['cells'].items():
                refs=[a['cells'].get(cell) for a in base]
                if any(v is None for v in refs):continue
                if all(abs(speed-v[0])<2 and abs(throttle-v[3])<.1 and steer>v[1]*1.25 and response<v[2]*.85 for v in refs):found.add(cell)
            hits.append(found)
        return self._separate(hits[0]&hits[1])

    def _rear(self,base,recent):
        return self._separate(recent[0]['sites']&recent[1]['sites']) and not any(self._separate(r['sites']) for r in base)

    def ack(self,key):
        if key in ('trend_front','trend_rear','input_front','input_rear'):self.thermal_spoken=True
        if key=='temperature_recovered':self.recovered=True
