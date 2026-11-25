"""Fuel target and the lap-time loss that would exhaust a measured race gap."""
from math import ceil, floor, isfinite
from statistics import median

from race_strategy_clips import defence_words, feedback_words


def finite(value):
    return value if type(value) in (int,float) and isfinite(value) else None


def progress(car):
    if car.get('on_pit_road') is not False or car.get('track_surface') != 3 or car.get('is_pace_car'):
        return None
    lap,pct=finite(car.get('lap_completed')),finite(car.get('distance_pct'))
    return lap+pct if lap is not None and lap>=0 and pct is not None and 0<=pct<1 else None


class FuelDefence:
    def __init__(self):
        self.generation=0
        self.last_target=None
        self.reset()

    def reset(self):
        self.generation+=1
        self.pair=None
        self.previous=None
        self.marks=({}, {})
        self.initial=None
        self.last_result=None
        self.consumed_lap=None
        self.candidate=None
        self.reference=None
        self.evidence={}

    def observe(self,race,s,length):
        time=finite(s.get('session_time'))
        length=finite(length)
        if (not race or not length or length<=0 or time is None or s.get('is_replay') is not False
                or s.get('session_state')!=4 or s.get('on_pit_road') is not False
                or race.get('position_source')=='HELD' or s.get('lmu_scoring_held')
                or (race.get('position_age_s') is not None and not 0<=race['position_age_s']<=.5)
                or int(s.get('session_flags') or 0)&0xBC119):
            self.reset()
            return
        roster=race.get('cars') or ()
        own=next((car for car in roster if car.get('idx')==race.get('player_idx')), {})
        rank=finite(own.get('class_position'))
        own_class=finite(own.get('class_id'))
        if rank is None or rank<=0 or own_class is None or own_class<0:
            self.reset()
            return
        rivals=[c for c in roster if c.get('class_id')==own_class and c.get('class_position')==rank+1
                and not c.get('is_pace_car') and c.get('idx') is not None]
        rear=rivals[0] if len(rivals)==1 else {}
        positions=progress(own),progress(rear)
        if None in positions or not 0 < positions[0]-positions[1] < .5:
            self.reset()
            return
        pair=(own['idx'],rear['idx'],rank,own_class)
        if self.pair!=pair:
            self.reset()
        self.pair=pair
        previous=self.previous
        if previous:
            dt=time-previous[0]
            if dt==0 and previous[1]==positions:
                return
            distances=tuple((p-q)*length for p,q in zip(positions,previous[1]))
            if not 0<dt<=3 or any(not 0<=d<=105*dt for d in distances):
                self.reset()
                self.pair=pair
            elif dt<=1.75:
                for marks,p,q in zip(self.marks,positions,previous[1]):
                    for mark in range(floor(q*10)+1,floor(p*10)+1):
                        marks[mark]=previous[0]+dt*(mark/10-q)/(p-q)
                    if len(marks)>12:
                        for mark in sorted(marks)[:-12]:
                            del marks[mark]
            else:
                # A missed frame cannot provide an interpolated passage. Keep only
                # previously measured marks; the consecutive-mark check rejects a hole.
                self.candidate=None
        self.previous=time,positions

    def measured_gaps(self,s,pit):
        if not self.previous or self.pair is None:
            return None
        _,rear_idx,rank,_=self.pair
        rear=pit.get('behind') or {}
        t=finite(s.get('session_time'))
        pace=finite(pit.get('pace_s'))
        if (t is None or not 0<=t-self.previous[0]<=1.75 or not pace or pace<=0
                or pit.get('class_position')!=rank or rear.get('idx')!=rear_idx
                or rear.get('class_position')!=rank+1 or rear.get('on_pit_road') is not False):
            return None
        at=floor(self.previous[1][1]*10)
        mine,theirs=self.marks
        if any(k not in mine or k not in theirs for k in range(at-2,at+1)) or t-theirs[at]>pace*.18:
            return None
        gaps=[theirs[k]-mine[k] for k in range(at-2,at+1)]
        if min(gaps)<=.1 or max(gaps)>=40 or max(gaps)-min(gaps)>max(1.5,median(gaps)*.3):
            return None
        return gaps

    @staticmethod
    def words(key,token):
        if key=='fuel_defence_feedback':
            return feedback_words(token[3],token[4])
        return defence_words(token[3] if key=='fuel_defence_target' else None,token[4])

    def choice(self,s,pit,feedback,mandatory,plan_known,now):
        self.candidate=None
        remaining=finite(pit.get('remaining_laps'))
        mode=pit.get('fuel_save_mode')
        if (not pit.get('active') or not plan_known or mandatory!=0 or pit.get('planned_stop_before_finish') is not False
                or pit.get('finish_may_extend') is not False or pit.get('finish_may_shorten') is not False
                or mode not in ('finish','reserve') or (pit.get('fuel_observed_laps') or 0)<2
                or remaining is None or not 1.25<=remaining<=12 or int(s.get('engine_warnings') or 0)&2
                or (pit.get('game')=='lmu' and (finite(pit.get('energy_laps')) or 0)<remaining)
                or (pit.get('energy_finish_shortfall_pct') or 0)>.1):
            return None
        gaps=self.measured_gaps(s,pit)
        if not gaps:
            return None
        lap=s.get('lap_number')
        if not lap:
            return None
        target=finite(pit.get('fuel_save_target_l_per_lap'))
        if (self.initial is not None and target is not None and .01<=target<=30
                and floor(target*100+1e-8)!=self.initial[3]):
            # Keep measured passages, but retire the old goal and its delivery receipts.
            self.generation+=1
            self.initial=None
            self.last_result=None
            self.consumed_lap=None
            self.reference=None
            self.evidence={}
        if self.initial is None:
            percent=finite(pit.get('fuel_goal_save_pct'))
            if (target is None or not .01<=target<=30 or percent is None or not .5<=percent<=10
                    or pit.get('fuel_save_feasible') is not True):
                return None
            # A tenth that catches the rival for only one of the three observed gaps is ambiguous.
            low=ceil((min(gaps)-.005)/(remaining+.005)*10)
            high=ceil((max(gaps)+.005)/(remaining-.005)*10)
            if low!=high or not 1<=high<=320:
                return None
            target=floor(target*100+1e-8)
            key='fuel_defence_target' if self.last_target!=(lap,target) else 'fuel_defence'
            token=(self.generation,self.pair[1],lap,target,high,self.last_target!=(lap,target))
        else:
            # Only a whole clean lap after the original advice can describe its outcome.
            last=feedback[-1] if feedback else {}
            finished=last.get('lap')
            if (not isinstance(finished,(int,float)) or finished!=lap-1 or finished<self.initial[2]
                    or finished==self.consumed_lap or last.get('mode')!=mode):
                return None
            target,use=finite(last.get('target_l')),finite(last.get('use_l'))
            if not target or use is None or self.reference is None:
                return None
            prior=self.reference
            gap=median(gaps)
            tolerance=max(.12,(max(gaps)-min(gaps))/2,(max(prior)-min(prior))/2)
            change=median(prior)-gap
            met=use<=target+.005
            if abs(use-target)<.015 and self.last_result is not None:
                met=self.last_result[0]
            closing=change>tolerance and max(gaps)<min(prior)
            if change < -tolerance or (change>tolerance and not closing):
                # An opening or noisy gap does not justify saying it held or closed.
                self.reference=tuple(gaps)
                self.consumed_lap=finished
                return None
            if self.last_result==(met,closing):
                self.reference=tuple(gaps)
                self.consumed_lap=finished
                return None
            key='fuel_defence_feedback'
            token=(self.generation,self.pair[1],finished,met,closing)
        self.candidate=(key,token,tuple(gaps))
        self.evidence[token]=tuple(gaps)
        if len(self.evidence)>12:
            del self.evidence[next(iter(self.evidence))]
        return key,token,''

    def valid(self,key,token,s):
        return bool(self.candidate and self.candidate[:2]==(key,token) and self.pair
                    and self.pair[1]==token[1] and self.previous and finite(s.get('session_time')) is not None
                    and 0<=s['session_time']-self.previous[0]<=1.75 and not int(s.get('engine_warnings') or 0)&2)

    def delivered(self,key,token):
        if not self.pair or self.generation!=token[0] or self.pair[1]!=token[1]:
            return
        if key=='fuel_defence_target':
            self.last_target=(token[2],token[3])
            return
        if key=='fuel_defence':
            self.initial=token
        else:
            self.last_result=token[3:5]
            self.consumed_lap=token[2]
        self.reference=self.evidence.get(token,self.reference)
