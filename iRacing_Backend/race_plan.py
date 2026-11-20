"""One live decision for race fuel, virtual energy and pit traffic."""
from math import ceil
from collections import deque
from statistics import median

from race_call_audio import BY_WORDING, CALLS, FUEL_TARGETS
from race_corner_cost import CornerCosts, coast_combinations, num
from race_encounters import RaceEncounters
from radio_policy import quiet_window, speech_seconds
from live_pit import stall_estimate


def fuel_plan(view, combinations, mandatory):
    use, fuel, remaining, tank = (num(view.get(k)) for k in
                                  ('fuel_l_per_lap','fuel_l','remaining_laps','tank_capacity_l'))
    if (not use or fuel is None or not remaining or remaining<=.3 or not tank
            or (view.get('fuel_observed_laps') or 0)<2 or view.get('fuel_save_mode')=='off'):
        return {}
    if view.get('finish_may_extend'):
        remaining += 1
    reserve = (view.get('fuel_reserve_l') or 0) if view.get('fuel_save_mode')=='reserve' else 0
    required = max(0.,use*remaining+reserve-fuel)
    if required < .015:
        return {'secured':True}
    stops = max(ceil(required/tank), mandatory or 0)
    best = None
    for saving,cost,route in combinations:
        # Use only the opportunities that remain on this lap.
        laps = max(0,int(remaining))
        reduced = max(0.,required-saving*laps)
        future = max(ceil(reduced/tank),mandatory or 0)
        eliminated = stops-future
        if (saving>=use or cost<=0 or laps==0 or eliminated and (mandatory is None or
                view.get('game')=='lmu' and (future or (view.get('energy_laps') or 0)<remaining))):
            continue
        rate,rule = view.get('fuel_rate_lps'),view.get('pit_rule')
        tires = view.get('tyre_decision')
        earned = 0.
        if eliminated:
            # A manual total or automatic total already includes the service. Don't count it twice.
            loss = num(view.get('pit_loss_s'))
            if eliminated!=1 or tires=='change':
                continue
            if future:
                # Two fuel stops become one. Subtract the maximum modeled
                # service at the remaining stop from a lower bound on the two.
                transit = num(view.get('pit_transit_loss_s'))
                if future!=1 or not transit or not rate or tires!='keep':
                    continue
                earned = transit+required/rate-stall_estimate(reduced,rate,False,rule)
            elif loss:
                earned = loss
            else:
                continue
        else:
            if (view.get('game')=='lmu' or not rate or stops!=1 or tires not in ('keep','change')
                    or tires=='change' and rule not in ('CONCURRENT','SEQUENTIAL')):
                continue
            before = stall_estimate(required,rate,tires=='change',rule)
            after = stall_estimate(reduced,rate,tires=='change',rule)
            earned = before-after
        driving_cost = cost*ceil(remaining)
        net = earned-driving_cost
        if best is None or net>best['net_s']:
            best = dict(cue='save_stop' if eliminated else 'save_fill', route=route,
                        saving_lap_l=round(saving,3), target_lap_l=round(use-saving,3),
                        pit_gain_s=round(earned,2), driving_cost_s=round(driving_cost,2),
                        net_s=round(net,2),remaining_laps=round(remaining,2),stops_saved=eliminated)
    if best and best['net_s'] <= 1.:
        best.update(cue='normal_pace',route=[])
    return best or {}


def pit_target(pit, distance, energy_history, routes, joint=()):
    """One decision from both resources. Independent best laps cannot prove joint savings."""
    fuel, use, reach = (num(pit.get(k)) for k in ('fuel_l','fuel_l_per_lap','fuel_laps'))
    lmu = pit.get('game') == 'lmu'
    energy, ve = num(pit.get('energy_pct')), num(pit.get('energy_laps'))
    if (distance<=0 or None in (fuel,use,reach) or (pit.get('fuel_observed_laps') or 0)<2
            or lmu and None in (energy,ve)):
        return {}
    fuel_short = reach<distance
    energy_short = lmu and ve<distance
    if fuel_short and energy_short:
        fuel_target,energy_target=fuel/distance,energy/distance
        if (distance>1 and pit.get('fuel_save_mode')!='off' and
                sum(f<=fuel_target and e<=energy_target for f,e in joint)>=2):
            return dict(target_call='plan_both_save',fuel_target_l=round(fuel_target,3),
                        energy_target_pct=round(energy_target,2),energy_laps=ve)
        return {'target_call':'plan_both_early'}
    if energy_short:
        target = energy/distance
        feasible = distance>1 and sum(x<=target for x in energy_history)>=2
        return dict(target_call='energy_save_plan' if feasible else 'energy_early',
                    energy_target_pct=round(target,2), energy_laps=ve)
    if fuel_short:
        deficit = distance*use-fuel
        usable = [row for row in routes if row[0]*int(distance)>=deficit and pit.get('fuel_save_mode')!='off']
        return dict(target_call='plan_fuel_save' if usable else 'plan_fuel_early',
                    target_route=min(usable,key=lambda row:row[1])[2] if usable else [],fuel_target_l=round(fuel/distance,3))
    return {}


class RacePlan:
    def __init__(self):
        self.corners = CornerCosts()
        self.traffic = RaceEncounters()
        self.view = {}
        self.choices = {}
        self.announced = {}
        self.next_evaluation = 0.
        self.curves = []
        self.plan = {}
        self.was_saving = False
        self.confirmed_stops = 0
        self.service_started = False
        self.pit_entries = deque(maxlen=8)
        self.previous_pit = None
        self.stops_at_target = None
        self.lap_key = None
        self.braking_marks = {}
        self.auto_zones = []
        self.history_stamp = None
        self.historical = CornerCosts()
        self.previous_brake = None
        self.last_voice_at = -1e9
        self.joint_laps = deque(maxlen=5)

    def delivered(self, key, token, now):
        self.announced[key] = self.notice_token(key, token)
        self.last_voice_at = now
        if key=='fuel_secured':
            self.was_saving = False

    @staticmethod
    def notice_token(key, token):
        # A different second car is new information even with the same leading car.
        return token[:2] + token[3:] if key.startswith('catch_') else token

    def distance_to_planned_stop(self, target, lap, pct, remaining):
        if None in (target, lap, pct) or (self.stops_at_target is not None and target <= self.stops_at_target):
            return None
        entry = median(self.pit_entries) if self.pit_entries else 1.
        distance = target - lap + entry - pct
        return distance if distance > 0 and (remaining is None or distance <= remaining + .05) else None

    def reset_motion(self):
        self.choices.clear()
        self.view = {}
        self.traffic.reset_motion()
        for costs in (self.corners,self.historical):
            costs.active = costs.previous = None
            costs.pending.clear()
        self.previous_pit = None
        self.service_started = False
        self.previous_brake = None

    def definitions(self,s,known):
        if known:
            return known
        p,lap,b=s.get('distance_pct'),s.get('lap_number'),s.get('brake')
        if None in (p,lap,b):
            return self.auto_zones
        if not self.auto_zones and self.previous_brake is not None and self.previous_brake < .22 <= b:
            marks=self.braking_marks.setdefault(lap,[])
            if .06<p<.94 and (not marks or p-marks[-1]>.04):
                marks.append(p)
            if len(self.braking_marks)>4:
                del self.braking_marks[min(self.braking_marks)]
            older=[v for k,v in self.braking_marks.items() if k<lap]
            if len(older)>=2:
                agreed=[x for x in older[-1] if any(abs(x-y)<.008 for y in older[-2])]
                if len(agreed)>=2:
                    self.auto_zones=[dict(id=f'measured-{i}',entry_pct=x,apex_pct=x+.012,exit_pct=x+.025)
                                     for i,x in enumerate(agreed)]
        self.previous_brake=b
        return self.auto_zones

    def ingest(self,live,s,race,now):
        t,p,lap = (num(s.get(k)) for k in ('session_time','distance_pct','lap_number'))
        meta = live.radio.metadata
        green = (str(meta.get('session_type','')).lower()=='race' and s.get('session_state')==4
                 and s.get('session_flags') is not None and not int(s['session_flags'])&0xC119 and s.get('on_track') is True
                 and s.get('is_replay') is False and not s.get('lmu_scoring_held'))
        if None in (t,p,lap) or not green:
            self.reset_motion()
            return
        if self.previous_pit is False and s.get('on_pit_road') is True:
            self.pit_entries.append(p)
            self.stops_at_target = lap
            self.service_started = False
            self.joint_laps.clear()
        if s.get('on_pit_road') is True and s.get('pit_service_status') == 1:
            self.service_started = True
        if self.service_started and s.get('on_pit_road') is True and s.get('pit_service_status') == 2:
            self.confirmed_stops += 1
            self.service_started = False
        self.previous_pit = s.get('on_pit_road')
        if (live.game=='lmu' and live.energy.last_counted_at==t and live.energy.history
                and live.fuel_feedback and live.fuel_feedback[-1].get('at')==t):
            self.joint_laps.append((s.get('track_wetness'),live.fuel_feedback[-1]['use_l'],live.energy.history[-1]))
        length = num(meta.get('track_length_m'))
        definitions = self.definitions(s,getattr(live.corners,'definitions',[]))
        if race is not None:
            self.corners.layout(definitions, length)
            mapped = definitions is not self.auto_zones and len(self.corners.zones) == len(definitions)
            self.traffic.update(race, t, self.corners.zones if mapped else [])
            archive = getattr(live,'race_ibt_costs',None)
            scope = (getattr(live,'race_profile_key',None),self.corners.geometry)
            if archive is not None and scope!=self.history_stamp:
                self.historical=archive.reference_windows(scope[0],self.corners.zones,length,live.identity)
                self.history_stamp=scope
        has_traffic = self.traffic.last_time is not None and 0<=t-self.traffic.last_time<2
        clear = has_traffic and self.traffic.near_clear and s.get('car_left_right') in (1,0)
        self.corners.observe(s,clear)
        if self.historical.zones:
            self.historical.observe(s,clear)
        if s.get('on_pit_road') is not False or s.get('track_surface')!=3:
            self.choices.clear()
            self.view = {}
            return
        # Score at 1 Hz, keeping telemetry collection and last-moment validity separate.
        if t < self.next_evaluation:
            return
        self.next_evaluation = t+ .95
        pit = live._pit_view(s,max(live.fuel_history) if live.fuel_history else None,
                             len(live.fuel_history),**live.race_options)
        configured = live.plan_options
        target = configured.get('pit_lap') if configured.get('run')==live.run_id else None
        floor = configured.get('mandatory') if configured.get('run')==live.run_id else None
        official = num(meta.get('mandatory_pit_stops'))
        if official is not None:
            floor = max(floor or 0,official)
        if floor is not None:
            floor=max(0,floor-self.confirmed_stops)
        history_stamp = (lap,getattr(live,'race_profile_key',None),self.corners.geometry,
                         s.get('track_wetness'),s.get('tire_compound'),round(s.get('track_temp_c') or 0))
        signature = (history_stamp,self.corners.revision,self.historical.revision)
        if signature!=self.lap_key:
            self.lap_key=signature
            self.corners.evaluate(s)
            self.historical.evaluate(s)
            observed = {group[0]['zone'] for group in self.corners.costs}
            self.curves = coast_combinations(self.corners.costs+[g for g in self.historical.costs if g[0]['zone'] not in observed])
        result = dict(run_id=live.run_id,planned_pit_lap=target,evaluated_at=t)
        choices = []
        reach = num(pit.get('fuel_laps'))
        ve = num(pit.get('energy_laps'))
        location = median(self.pit_entries) if self.pit_entries else 1.
        remaining = num(pit.get('remaining_laps'))
        distance = self.distance_to_planned_stop(target, lap, p, remaining)
        target_active = distance is not None
        urgent_fuel = reach is not None and (pit.get('fuel_finish_shortfall_l') or 0)>.1 and reach-(1-p)<1+live.pit_wall.BOX_MARGIN_LAPS
        saving_to_flag = live.pit_wall.finish_save_percent(pit, s, live.fuel_feedback) is not None
        if saving_to_flag:
            urgent_fuel = False
        urgent_energy = live.game=='lmu' and ve is not None and ve<1
        if target_active and pit.get('active'):
            joint = [(f,e) for wet,f,e in self.joint_laps if wet==s.get('track_wetness')]
            result.update(pit_target(pit,distance,live.energy.history,self.curves,joint))
            if (urgent_fuel or urgent_energy) and result.get('target_call') in ('plan_fuel_save','energy_save_plan','plan_both_save'):
                result['target_call'] = 'plan_both_early' if urgent_fuel and urgent_energy else 'plan_fuel_early' if urgent_fuel else 'energy_early'
                result.pop('target_route',None)
        override = result.get('target_call')
        if override:
            choices.append((override,target,f'예정 {target}랩 피트'))
        plan = fuel_plan(pit,self.curves,max(1,floor or 0) if target_active else floor) if pit.get('active') and not override else {}
        if (urgent_fuel or urgent_energy) and plan.get('cue') == 'save_stop':
            plan = {}
        if saving_to_flag and plan.get('cue') in ('normal_pace', 'save_fill'):
            # A partial-fill comparison assumes a stop which the finish strategy can avoid.
            plan = {}
        result['fuel'] = plan
        if plan.get('cue') in ('save_stop','save_fill'):
            self.was_saving=True
        if plan.get('secured') and self.was_saving:
            choices.append(('fuel_secured','finish','완주 여유 연료 확보'))
        if plan.get('cue'):
            choices.append((plan['cue'],(plan['cue'],tuple(x['zone'] for x in plan['route'])),
                            f"랩당 {plan['saving_lap_l']:.2f} L 절약 · 피트 {plan['pit_gain_s']:.1f}초, 주행 {plan['driving_cost_s']:.1f}초"))
            if plan['route']:
                ml = plan['saving_lap_l']*1000
                amount = min(FUEL_TARGETS,key=lambda k:abs(k-ml))
                if abs(ml-amount)<=ml*.10:
                    choices.append((f'coast_target_{amount}',(plan['cue'],amount),f'랩당 {ml:.0f} mL'))
        for opt in result.get('target_route') or plan.get('route',[]):
            eta=(opt['entry']-p)%1*(pit.get('pace_s') or 0)
            if 4<=eta<=12 and clear:
                choices.insert(0,(opt['cue'],(lap,opt['zone']),opt['label']))
        planned_now = target_active and target==lap
        automatic = not target_active and not saving_to_flag and pit.get('stops_needed') and reach is not None and reach<=2.3
        if (not override and (planned_now or automatic) and reach is not None
                and reach>=1+(location-p)%1+.15 and (live.game!='lmu' or ve is not None and ve>=1+(location-p)%1+.15)
                and (planned_now or plan.get('cue')!='save_stop') and (num(pit.get('remaining_laps')) or 0)>1+(location-p)%1):
            decision=self.traffic.rejoin(pit,s,self.corners.zones)
            if decision:
                key,a,b=decision
                choices.insert(0,(key,lap,f'지금 복귀 간격 {a:.1f}초 · 다음 랩 {b:.1f}초'))
                result.update(rejoin_call=key,rejoin_now_s=a,rejoin_later_s=b)
        pace=num(pit.get('pace_s'))
        limit,points=num(meta.get('incident_limit')),num(s.get('incidents'))
        if pace:
            for zone,row in self.corners.limits.items():
                if 4<=(row['entry']-p)%1*pace<=12:
                    key=row['cue']
                    if key=='cut_margin' and definitions is self.auto_zones:
                        key='cut_repeat'
                    if live.game=='lmu':
                        limit,points = num(s.get('lmu_track_limits_steps_per_penalty')),num(s.get('lmu_track_limits_steps'))
                    if key!='cut_penalty' and None not in (limit,points) and limit-points<=max(3,(pit.get('remaining_laps') or 0)*points/max(1,lap)):
                        key='cut_risk'
                    elif key=='cut_margin' and (limit is None or points is None):
                        key='cut_repeat'
                    result['track_limit'] = dict(row,cue=key,remaining_points=limit-points if None not in (limit,points) else None)
                    choices.insert(0,(key,(zone,key),f"{row['label']} · 최근 이득 {row['gain']:.2f}초" if 'gain' in row else row['label']))
        if has_traffic and live.radio.traffic_enabled:
            choices=[(key,token,'') for key,token in self.traffic.calls]+choices
        result['superseded'] = self.superseded_reports(live, choices, plan, result, override)
        self.view=result
        self.choices={}
        for key,token,detail in choices:
            self.choices.setdefault(key,(token,now,detail))
        pending = next((e for e in live.radio.pending if e['group']=='race_plan' and live.radio.valid(e,now)),None)
        for key,token,detail in choices:
            category,role,wording,_=CALLS[key]
            encounter = category == 'race_catch_zone'
            if self.announced.get(key)==self.notice_token(key,token) or not self.valid(key,token,now,s):
                continue
            if pending and (pending['key']==key or category not in ('race_pack','race_catch_zone')):
                break
            if now-self.last_voice_at<4 and category not in ('race_pack','race_catch_zone'):
                continue
            if encounter:
                standard, _ = BY_WORDING[role, token[2]]
                wording = CALLS[standard][2] if standard else token[2]
            ev=live.radio.emit(key,now,(token[2] if encounter else wording)+(' '+detail if detail else ''),role=role,ttl=2,
                speech_text=wording,prepared_only=True,group='race_plan',condition=('race_plan',(key,token)),
                alert_category='pitwall.'+category,cooldown=12)
            if ev:
                if encounter:
                    ev['race_short_text'] = token[2]
                cars = token[3] if encounter and len(token) > 3 else (token[0],) if encounter else ()
                live.radio.track_delivery(ev, plan_token=token, class_notice=(cars, 'behind') if encounter else None)
                break

    def superseded_reports(self, live, choices, plan, result, override):
        """Suppress the previous advice only after the replacement was heard."""
        delivered = {key for key, token, _ in choices
                     if self.announced.get(key) == self.notice_token(key, token) and live.radio.allows(key)}
        countdown = ['pit_soon', 'pit_two', 'pit_window']
        if plan.get('cue') == 'save_stop' and 'save_stop' in delivered:
            return [*countdown, 'pit_fuel_plan', 'pit_fuel_check']
        if override in delivered:
            return [*countdown, 've_need_stop', 've_save', 've_lap'] if live.game == 'lmu' else countdown
        return countdown if result.get('rejoin_call') in delivered else []

    def valid(self,key,token,now,s):
        choice=self.choices.get(key)
        if (not choice or choice[0]!=token or not 0<=now-choice[1]<1.6 or s.get('on_pit_road') is not False
                or s.get('is_replay') is not False or s.get('on_track') is not True or s.get('lmu_scoring_held')
                or s.get('track_surface')!=3 or s.get('session_state')!=4 or s.get('session_flags') is None
                or int(s['session_flags']) & 0xBC119 or (num(s.get('speed_mps')) or 0)<8
                or s.get('car_left_right') not in (0,1) or not quiet_window(s)):
            return False
        if CALLS[key][0] in ('race_pack','race_catch_zone') and (key,token) not in self.traffic.calls:
            return False
        if key.startswith('catch_'):
            position = num(s.get('distance_pct'))
            window = self.traffic.catch_window
            if position is None or not window:
                return False
            travelled = round(position - window[0], 9) % 1
            if travelled >= window[1]:
                return False
        ahead=num(s.get('workload_in_s'))
        duration = speech_seconds(CALLS[key][2])
        if key.startswith('catch_'):
            duration = max(duration, speech_seconds(token[2]))
        return ahead is not None and ahead > duration
