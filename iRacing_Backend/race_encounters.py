"""Forecast traffic from per-car passages; an incomplete speed curve yields no location call."""
from collections import defaultdict, deque
from statistics import median
import math
from race_corner_cost import num
from race_call_audio import ENCOUNTER_CLIPS


def encounter_location(position, low, high, zones):
    """Count mapped corners from the player to one unambiguous encounter segment."""
    if not zones or not position <= low <= high <= position + 1:
        return None
    intervals = [(lap + z['entry'], lap + z['end'])
                 for lap in range(math.floor(position), math.floor(high) + 1) for z in zones]
    touched = [(entry, end) for entry, end in intervals if entry <= high and low <= end]
    count = sum(position < entry <= low for entry, _ in intervals)
    if touched:
        if len(touched) == 1 and touched[0][0] <= low <= high <= touched[0][1]:
            return f'corner_{count}'
        return None
    in_corner = any(entry <= position <= end for entry, end in intervals)
    if count == 0:
        return 'straight_1' if in_corner else 'straight_0'
    if count == 1 and not in_corner:
        return 'straight_1'
    return f'after_{count}'


class RaceEncounters:
    bins = 64

    def __init__(self):
        self.previous = {}
        self.profiles = defaultdict(dict)
        self.integrals = {}
        self.positions = {}
        self.last_time = None
        self.pair = deque(maxlen=9)
        self.passed = {}
        self.closing = {}
        self.last_pass = None
        self.exit_samples = deque(maxlen=32)
        self.outlap = None
        self.exit_to_brake = defaultdict(lambda: deque(maxlen=8))
        self.player = None
        self.near_clear = False
        self.calls = []
        self.catch_window = None

    def reset_motion(self):
        self.pair.clear()
        self.previous.clear()
        self.positions.clear()
        self.passed.clear()
        self.closing.clear()
        self.last_pass = None
        self.calls = []
        self.catch_window = None
        self.near_clear = False
        self.outlap = None
        self.last_time = None
        self.integrals.clear()

    def update(self, race, t, zones):
        cars = race.get('cars') or []
        self.player = race.get('player_idx')
        length = num(race.get('track_length_m'))
        if self.last_time is None or not 0 < t-self.last_time <= 2.2:
            self.reset_motion()
        self.last_time = t
        self.integrals.clear()
        self.calls = []
        self.catch_window = None
        ahead, behind, current, previous = [], [], {}, self.previous
        for car in cars:
            idx = car.get('idx')
            pct,lap = num(car.get('distance_pct')),num(car.get('lap_completed'))
            if (car.get('is_pace_car') or idx is None or pct is None or not 0<=pct<1 or lap is None
                    or lap < 0 or car.get('on_pit_road') is None):
                continue
            p = lap+pct
            old = previous.get(idx)
            pit = car['on_pit_road']
            valid = not pit and car.get('track_surface') == 3
            current[idx] = (p,t,pit,valid)
            if not old or not valid:
                continue
            if old[2] and length and 0 <= (p-old[0])*length < 180:
                self.exit_samples.append(pct)
                if idx == self.player and zones:
                    zone = min(zones, key=lambda z:(z['entry']-pct)%1)
                    dist = (zone['entry']-pct)%1
                    if .003 < dist < .25:
                        self.outlap = (zone['key'], p+dist, dist, t, [None,None])
            if idx == self.player and self.outlap:
                key, end, span, started, marks = self.outlap
                for i, bound in enumerate((end-span/2,end)):
                    if old[0] <= bound <= p and p > old[0]:
                        marks[i] = old[1]-started+(bound-old[0])/(p-old[0])*(t-old[1])
                if p >= end or t-started > 60:
                    if all(x is not None and x>0 for x in marks) and t-started<=60:
                        self.exit_to_brake[key].append(tuple(marks))
                    self.outlap = None
            dx = p-old[0]
            if not length or not old[3] or not 3 < dx*length/(t-old[1]) < 120:
                continue
            # One value per independent lap and position bin, including crossed boundaries.
            for j in range(int(old[0]*self.bins), int(p*self.bins)+1):
                cell = self.profiles[idx].setdefault(j%self.bins, deque(maxlen=6))
                recent = (math.floor(j/self.bins),(t-old[1])/dx)
                if cell and cell[-1][0] == recent[0]:
                    cell[-1] = recent
                else:
                    cell.append(recent)
        self.previous = current
        self.positions = {c['idx']:c for c in cars if c.get('idx') in current and current[c['idx']][3]}
        own = self.positions.get(self.player)
        self.near_clear = False
        # Track the player's surroundings even during a short off-track.
        # Requiring the player's on-track surface here discards every 1x pass.
        car = next((c for c in cars if c.get('idx')==self.player),None)
        if (length and car and self.player in current and car.get('track_surface') in (0,3)
                and car.get('on_pit_road') is False and num(race.get('sample_time')) is not None
                and 0<=t-race['sample_time']<2):
            rivals = [c for c in cars if c.get('idx')!=self.player and not c.get('is_pace_car')
                      and c.get('on_pit_road') is not True and c.get('track_surface')!=-1]
            self.near_clear = all(c.get('idx') in current and c.get('track_surface') in (0,3)
                and abs((current[c['idx']][0]-current[self.player][0]+.5)%1-.5)*length>=100 for c in rivals)
        if not own or not length or not previous.get(self.player, (None,None,None,False))[3]:
            self.pair.clear()
            return
        op = current[self.player][0]
        for idx, car in self.positions.items():
            if idx == self.player:
                continue
            gap = ((current[idx][0]-op+.5)%1-.5)*length
            (ahead if gap >= 0 else behind).append((abs(gap),idx))
        ahead.sort()
        behind.sort()
        if len(ahead)>=2 and ahead[1][0]<160:
            self._battle(ahead[:2],op,t,length)
        else:
            self.pair.clear()
        self._catch(behind,ahead,own,op,t,length,zones)

    def _battle(self, front, op, t, length):
        (gap,a),(far,b) = front
        cars = self.positions
        same_class = cars[a].get('class_id') is not None and cars[a].get('class_id') == cars[b].get('class_id')
        if not same_class or abs(self.previous[a][0]-self.previous[b][0]) > .25 or far-gap>28:
            self.pair.clear()
            return
        pair = tuple(sorted((a,b)))
        if self.pair and self.pair[-1][0]!=pair:
            self.pair.clear()
        self.pair.append((pair,a,t,gap,far-gap,op,self.previous[a][0]))
        if len(self.pair)<4 or self.pair[0][3]-gap < 3 or gap>100:
            return
        stable = [r for r in self.pair if r[4]>4]
        swaps = [i for i in range(2,len(stable)-1)
                 if stable[i-2][1]==stable[i-1][1]!=stable[i][1]==stable[i+1][1]]
        # A completed pass that simply pulls away is not a battle. Look for
        # another exchange or a renewed challenge after the confirmed exchange.
        swap = len(swaps)>=2 or bool(swaps and len(stable)-swaps[0]>=3 and
                    max(r[4] for r in stable[swaps[0]:-1])-stable[-1][4]>=3 and stable[-1][4]<=14)
        last = list(self.pair)[-4:]
        alongside = (last[-1][2]-last[0][2]>=2.5 and all(r[4]<3 for r in last)
                     and (last[-1][5]-last[0][5])*length>45
                     and (last[-1][6]-last[0][6])*length>45)
        fight = swap or alongside
        key = 'pack_fight' if fight else 'pack_close'
        # Compare the front pair's measured pace with the faster car's own earlier passes.
        if fight:
            idx = a
            old = next((r for r in self.pair if r[1]==idx and t-r[2]>=2),None)
            pace = self._rate(idx,self.previous[idx][0],0)
            if old and pace and 0 < (self.previous[idx][0]-old[6])/(t-old[2]) < .88/pace:
                key = 'pack_slow'
        self.calls.append((key,(pair,math.floor(op))))

    def _rate(self,idx,p,spread):
        cell = self.profiles.get(idx,{}).get(int((p%1)*self.bins),())
        rates = [v for lap,v in cell if lap < math.floor(self.previous[idx][0])]
        if len(rates)<2:
            return None
        return (min(rates)*.97 if spread<0 else max(rates)*1.03 if spread>0 else median(rates))

    def _meeting(self,idx,op,gap,spread):
        me, other = op, op-gap
        for _ in range(42):
            a,b = self._rate(self.player,me,spread), self._rate(idx,other,-spread)
            if a is None or b is None:
                return None
            me += .4/a
            other += .4/b
            if other>=me:
                return me
        return None

    def _catch(self,behind,ahead,own,op,t,length,zones):
        self.passed = {idx:stamp for idx,stamp in self.passed.items() if 0<=t-stamp<=4}
        self.closing = {idx:rows for idx,rows in self.closing.items() if rows and 0<t-rows[-1][0]<=2.2}
        for gap,idx in ahead:
            if idx in self.passed and gap<100:
                self.last_pass = (idx,t)
                del self.passed[idx]
        caught = []
        for gap,idx in behind[:4]:
            car = self.positions[idx]
            if not zones or not 10<=gap<=180 or car.get('class_id') in (None,own.get('class_id')):
                continue
            faster = num(car.get('class_est_lap_s'))
            pace = num(own.get('class_est_lap_s'))
            if not faster or not pace:
                # LMU does not publish class estimate times. Compare observed full
                # lap curves only when the measured faster pace is unambiguous.
                faster = self.travel(idx,0,1,1)
                pace = self.travel(self.player,0,1,-1)
            if not faster or not pace or faster>=pace*.98:
                continue
            history=self.closing.setdefault(idx,deque(maxlen=4))
            history.append((t,gap))
            if len(history)<3 or history[0][1]-gap<3:
                continue
            self.passed[idx]=t
            bounds = [self._meeting(idx,op,gap/length,x) for x in (-1,1)]
            if None in bounds:
                continue
            location = encounter_location(op, min(bounds), max(bounds), zones)
            if location is not None:
                caught.append((idx, location, min(bounds)))
        if caught:
            idx, location, earliest = caught[0]
            traffic = 'single'
            if self.last_pass and self.last_pass[0]!=idx and 0<=t-self.last_pass[1]<6:
                traffic = 'another'
            elif len(caught) > 1 and caught[1][1] == location:
                traffic = 'train'
                earliest = min(earliest, caught[1][2])
            recording = ENCOUNTER_CLIPS.get((traffic, location))
            if recording:
                key, short = recording
                # Between 1 Hz traffic updates, crossing a corner boundary can
                # already change what "next" means to the driver.
                limits = [(z[edge] - op) % 1 for z in zones for edge in ('entry','end')]
                self.catch_window = (op % 1, min([earliest-op, *[v for v in limits if v > 0]]))
                cars = (idx, caught[1][0]) if traffic == 'train' else (idx,)
                self.calls.append((key, (idx, math.floor(op), short, cars)))

    def travel(self, idx, start, distance, spread):
        """Integrate the observed time per lap fraction through each crossed bin."""
        if distance < 0 or distance > 3:
            return None
        key = idx,spread
        if key not in self.integrals:
            elapsed, missing = [0.], [0.]
            for i in range(self.bins):
                rate = self._rate(idx,(i+.5)/self.bins,spread)
                elapsed.append(elapsed[-1]+(rate or 0)/self.bins)
                missing.append(missing[-1]+int(rate is None))
            self.integrals[key] = elapsed,missing
        elapsed,missing = self.integrals[key]
        def at(p, curve):
            lap = math.floor(p)
            cell = (p-lap)*self.bins
            i = min(int(cell),self.bins-1)
            return lap*curve[-1]+curve[i]+(cell-i)*(curve[i+1]-curve[i])
        end = start+distance
        if at(end,missing)-at(start,missing)>1e-9:
            return None
        return at(end,elapsed)-at(start,elapsed)

    def rejoin(self, view, s, zones):
        pace,loss = num(view.get('pace_s')), num(view.get('pit_loss_s'))
        if (not pace or not loss or len(self.exit_samples)<2 or len(self.positions)<3
                or self.player not in self.positions or self.last_time is None
                or not 0 <= s['session_time']-self.last_time < 2):
            return None
        center = self.exit_samples[-1]
        cluster = [((x-center+.5)%1-.5) for x in self.exit_samples]
        if max(abs(x) for x in cluster)>.025:
            return None
        exit_pct = (center+median(cluster))%1
        # Include the drive to the pit exit at normal pace; pit loss is incremental.
        distance = (exit_pct-s['distance_pct'])%1
        if distance>.55:
            return None
        error = max(1.5, (max(cluster)-min(cluster))*pace)
        bounds = view.get('pit_loss_range_s')
        if bounds:
            error += max(abs(loss-x) for x in bounds)
        if not zones:
            return None
        first = min(zones,key=lambda z:(z['entry']-exit_pct)%1)
        span = (first['entry']-exit_pct)%1
        measured = self.exit_to_brake.get(first['key'],())
        if len(measured)<2 or not .003<span<.25:
            return None

        def spacing(extra):
            drive = [self.travel(self.player,s['distance_pct'],distance+extra,b) for b in (-1,0,1)]
            if None in drive:
                return None
            horizon = drive[1]+loss
            margins=[]
            for idx,car in self.positions.items():
                if idx==self.player:
                    continue
                start = car['distance_pct']
                direct = (exit_pct-start)%1
                recent = self.travel(idx,start,1,0)
                if not recent:
                    return None
                nearest = round((horizon-direct*recent)/recent)
                for lap in range(max(-1,nearest-1),nearest+2):
                    gaps = []
                    for i,extra_dist in enumerate((0,span/2,span)):
                        dest = direct+lap+extra_dist
                        if dest < 0 or dest > 3:
                            continue
                        times = [self.travel(idx,start,dest,b) for b in (-1,1)]
                        if None in times:
                            return None
                        service = (0,0) if i==0 else (min(r[i-1] for r in measured),max(r[i-1] for r in measured))
                        own = (drive[0]+loss+service[0]-error, drive[2]+loss+service[1]+error)
                        gaps.append((times[0]-own[1],times[1]-own[0]))
                    if gaps:
                        lo,hi=min(g[0] for g in gaps),max(g[1] for g in gaps)
                        margins.append((max(0,lo,-hi),min(max(abs(a),abs(b)) for a,b in gaps)))
            return (min(r[0] for r in margins),min(r[1] for r in margins)) if margins else None
        now,later=spacing(0),spacing(1)
        if None in (now,later) or min(now[0],later[0])>4:
            return None
        if later[0]>now[1]+1:
            key='stay_for_gap'
        elif now[0]>later[1]+1:
            key='pit_now_gap'
        else:
            return None
        return (key,round(now[0],1),round(later[0],1))
