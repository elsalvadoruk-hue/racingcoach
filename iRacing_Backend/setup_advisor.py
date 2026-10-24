"""Small, bounded per-car setup trials from the driver's own clean laps.

Mechanical ARB trials use repeated mid-corner signs, never just top speed.
Wing/pressure targets must have been driven with three matched clean laps;
no universal hot pressure, opponent setup or unobserved optimal value is made up.
"""
from collections import deque
from statistics import median
from live_controls import finite
from setup_capabilities import profile_for,LABELS

PHRASES={
    'setup_front_arb_live':'앞 안티롤바를 조금 부드럽게 시험해 보세요.',
    'setup_rear_arb_live':'뒤 안티롤바를 조금 부드럽게 시험해 보세요.',
    'setup_front_arb_pit':'다음 피트에서 앞 안티롤바를 조금 부드럽게 시험해 보세요.',
    'setup_rear_arb_pit':'다음 피트에서 뒤 안티롤바를 조금 부드럽게 시험해 보세요.',
    'setup_wing_less':'다음 피트에서 리어 윙을 낮춰 보세요.',
    'setup_wing_more':'다음 피트에서 리어 윙을 높여 보세요.',
}
for _wheel,_label in (('lf','왼쪽 앞'),('rf','오른쪽 앞'),('lr','왼쪽 뒤'),('rr','오른쪽 뒤')):
    for _direction,_word in (('less','낮춰'),('more','높여')):
        PHRASES[f'setup_pressure_{_wheel}_{_direction}']=f'다음 피트에서 {_label} 공기압을 조금 {_word} 보세요.'


def matches(a,b):
    """Comparable known fuel/track temperature/compound/wetness; missing is not equal."""
    return (len(a)==len(b)==4 and all(finite(v) is not None for v in (*a,*b))
            and abs(a[0]-b[0])<=3 and abs(a[1]-b[1])<=3 and a[2:]==b[2:])


class SetupAdvisor:
    def __init__(self):self.reset()

    def reset(self):
        self.identity=None;self.profile={};self.laps=deque(maxlen=6);self.trials=deque(maxlen=48)
        self.current=None;self.previous=None;self.settings=None;self.agree=[0.,0.]
        self.advice=[];self.revision=0

    def ingest(self,identity,s):
        key=tuple(identity.get(k) for k in ('game','car_path','car_id','track_id','track_config'))
        if key!=self.identity:
            self.reset();self.identity=key;self.profile=profile_for(identity)
        if not self.profile:return
        values=tuple((k,s.get(k)) for k in (*LABELS,'front_arb_raw','rear_arb_raw','brake_bias','tc_setting','abs_setting'))
        changed=values!=self.settings
        if changed:
            self.settings=values;self.laps.clear();self.current=None;self.advice=[]
        pct,at,speed,brake,throttle,steer,yaw=(finite(s.get(k)) for k in (
            'distance_pct','session_time','speed_mps','brake','throttle','steering_rad','yaw_rate'))
        if steer is None:steer=finite(s.get('steering_lock_ratio'))
        lap=s.get('lap_number')
        valid=(s.get('on_track') is True and s.get('on_pit_road') is False and s.get('is_replay') is False
               and s.get('setup_fixed') is False and None not in (pct,at,speed,brake,throttle)
               and finite(lap) is not None and 0<=pct<=1 and 0<=brake<=1 and 0<=throttle<=1)
        prev,self.previous=self.previous,({'at':at,'pct':pct,'lap':lap,'incidents':s.get('incidents')} if valid else None)
        if not valid:
            self.current=None;self.laps.clear();self.advice=[]
            return
        if prev is None:return
        dt=at-prev['at']
        clean=(0<dt<=.5 and s.get('incidents')==prev['incidents'] and not int(s.get('session_flags') or 0)&0xC108
               and s.get('car_left_right') not in (2,3,4,5,6) and s.get('track_surface',3)==3
               and not s.get('engine_warnings') and not s.get('pit_repair_left_s'))
        cross=lap==prev['lap']+1 and prev['pct']>.9 and pct<.1
        clean=clean and ((cross and 0<=1+pct-prev['pct']<=.1) or
                         (lap==prev['lap'] and 0<=pct-prev['pct']<=.1))
        if cross:
            c=self.current
            if c and c['clean'] and clean and at-c['start']>20:
                cells={k:tuple(v[i]/v[4] for i in range(4)) for k,v in c['cells'].items() if v[4]>=.2}
                row=dict(lap=c['lap'],time=at-c['start'],sites=c['sites'],cells=cells,settings=c['settings'],
                         installed=c['installed'],
                         conditions=(c['fuel']/c['n'],c['temp']/c['n'],c['compound'],c['wet']) if c['n'] else ())
                self.laps.append(row);self.trials.append(row);self.revision+=1
            self.current=None
        if self.current is None:
            self.current=dict(lap=lap,start=at,clean=cross,cells={},sites=[],run=0.,fuel=0.,temp=0.,n=0,
                              compound=s.get('tire_compound'),wet=s.get('track_wetness'),settings=values,
                              installed=s.get('setup_values_verified') is True)
        c=self.current
        c['installed'] &= s.get('setup_values_verified') is True
        if not clean or lap!=c['lap'] or changed or (s.get('tire_compound'),s.get('track_wetness'))!=(c['compound'],c['wet']):
            c['clean']=False
        fuel,temp=finite(s.get('fuel_l')),finite(s.get('track_temp_c'))
        if fuel is not None and temp is not None:c['fuel']+=fuel;c['temp']+=temp;c['n']+=1
        turning=None not in (steer,yaw) and abs(steer)>.08 and abs(yaw)>.15 and 18<speed<45
        if turning:self.agree[steer*yaw>0]+=dt if clean else 0
        known=sum(self.agree)>20 and max(self.agree)>4*min(self.agree)
        mid=turning and brake<.05 and .1<throttle<.6
        counter=mid and known and (steer*yaw>0)!=(self.agree[1]>self.agree[0])
        c['run']=c['run']+dt if clean and counter else 0.
        if c['run']>=.25 and c['run']-dt<.25 and len(c['sites'])<32:c['sites'].append((pct,speed,throttle))
        if clean and mid and not counter:
            cell=int(pct*100)%100
            v=c['cells'].setdefault(cell,[0.,0.,0.,0.,0.])
            for i,val in enumerate((speed,abs(steer),abs(yaw)/speed,throttle,1.)):v[i]+=val*dt

    def _item(self,control,s,target=None):
        cap=self.profile[control];timing=cap['timing']
        # dcAntiRoll* are car-dependent SDK slots. Never label them as manual display numbers.
        field=control+'_raw' if timing=='live' and control.endswith('_arb') else control
        current=finite(s.get(field)) if not field.endswith('_raw') else None
        if timing=='pit' and s.get('setup_values_verified') is not True:current=None
        if timing=='live':target=None
        call=('setup_'+control+'_'+timing if control.endswith('_arb') else
              'setup_wing_'+('more' if target>current else 'less') if control=='rear_wing' else
              'setup_'+control+'_'+('more' if target>current else 'less'))
        return dict(control=control,name=LABELS[control],current=current,target=target,timing=timing,
                    field=field,initial=s.get(field),call=call,text=PHRASES[call],
                    direction='부드럽게' if control.endswith('_arb') else '높이기' if target>current else '낮추기',
                    unit=cap.get('unit',''),source=cap['source'],setup=True,
                    straight_only=cap.get('straight_only',False),
                    pending_field=control+'_pending' if control.startswith('pressure_') else None)

    def view(self,s):
        advice=[];laps=list(self.laps)[-3:]
        if s.get('setup_fixed') is not False:
            return {'ready':False,'advice':[],'capabilities':self.profile}
        if len(laps)>=3:
            repeated=False
            for i,a in enumerate(laps):
                for b in laps[i+1:]:
                    if not matches(a['conditions'],b['conditions']):continue
                    available=list(b['sites']);locations=[]
                    for p,speed,throttle in a['sites']:
                        distances=[min(abs(p-q),1-abs(p-q)) if abs(speed-v)<2 and abs(throttle-t)<.1
                                   else 2 for q,v,t in available]
                        if distances and min(distances)<=.015:
                            available.pop(distances.index(min(distances)));locations.append(p)
                    repeated|=any(min(abs(p-q),1-abs(p-q))>=.05 for p in locations for q in locations)
            control='rear_arb'
            # A front trial requires increased steering AND less response at matched speeds
            # in two separate locations on two later laps, against two earlier clean laps.
            if not repeated and len(self.laps)>=5:
                older=list(self.laps)[:-2];hits=[]
                for recent in list(self.laps)[-2:]:
                    found=set()
                    for cell,(speed,steer,response,throttle) in recent['cells'].items():
                        refs=[r['cells'][cell] for r in older if cell in r['cells'] and matches(r['conditions'],recent['conditions'])]
                        if len(refs)<2:continue
                        base=tuple(median(r[i] for r in refs) for i in range(4))
                        if abs(speed-base[0])<2 and abs(throttle-base[3])<.1 and steer>base[1]*1.25 and response<base[2]*.85:
                            found.add(cell)
                    hits.append(found)
                shared=hits[0]&hits[1]
                # Adjacent cells of one corner cannot count as two problem locations.
                repeated=any(min(abs(a-b),100-abs(a-b))>=5 for a in shared for b in shared)
                control='front_arb'
            cap=self.profile.get(control)
            if cap and cap['timing']=='live' and finite(s.get(control+'_raw')) is None:cap=None
            if repeated and cap:
                target=None;order=cap.get('soft_to_stiff') or [];value=s.get(control)
                if cap['timing']=='pit' and s.get('setup_values_verified') is True and value in order:
                    index=order.index(value)
                    if index==0:return {'ready':True,'advice':[]}
                    target=order[index-1]
                advice.append(self._item(control,s,target))
        # Reuse a previously successful setting, changing only that variable.
        if not advice and len(self.laps)>=3 and s.get('setup_values_verified') is True:
            current=list(self.laps)[-3:]
            now_settings=dict(current[-1]['settings'])
            if all(r.get('installed') and r['settings']==current[-1]['settings'] for r in current):
                for control in self.profile:
                    if control.endswith('_arb'):continue
                    value=finite(s.get(control))
                    if value is None:continue
                    candidates={dict(r['settings']).get(control) for r in self.trials}
                    for target in candidates:
                        if finite(target) is None or target==value:continue
                        refs=[r for r in self.trials if r.get('installed') and dict(r['settings']).get(control)==target
                              and all(dict(r['settings']).get(k)==v for k,v in now_settings.items() if k!=control)
                              and all(matches(r['conditions'],c['conditions']) for c in current)]
                        if len(refs)<3:continue
                        # Entire lap includes both straights and fast corners. Repeated win
                        # must exceed within-setting variation, not only the fastest lap.
                        before=[r['time'] for r in refs[-3:]];after=[r['time'] for r in current]
                        gain=median(after)-median(before)
                        if min(after)-max(before)>.3 and gain>max(1.,max(after)-min(after),max(before)-min(before)):
                            item=self._item(control,s,target);item['gain_s_per_lap']=round(gain,3)
                            item['evidence']='OWN_MATCHED_LAPS';advice.append(item)
        return {'ready':len(laps)>=3,'advice':advice,'laps':[r['lap'] for r in laps],
                'capabilities':self.profile,'trial':'one_change','revision':self.revision,
                'installed_values_verified':s.get('setup_values_verified') is True}
