"""Measure comparable racing lines and lift-and-coast costs over fixed distance windows."""
from collections import defaultdict, deque
from bisect import bisect_right
from math import floor, isfinite
from statistics import median


def num(x):
    return x if type(x) in (int, float) and isfinite(x) else None


def comparable(a, b):
    return (a['wet'] is not None and a['compound'] is not None and a['wet'] == b['wet']
            and a['compound'] == b['compound'] and None not in (a['temp'], b['temp'])
            and abs(a['temp'] - b['temp']) <= 3 and abs(a['fuel'] - b['fuel']) <= 6)


class CornerCosts:
    def __init__(self):
        self.records = defaultdict(lambda: deque(maxlen=96))
        self.active = self.previous = None
        self.pending = []
        self.zones = []
        self.geometry = None
        self.limits = {}
        self.costs = []
        self.revision = 0
        self.native_lap = None

    def layout(self, definitions, length):
        signature = tuple((d['id'], d['entry_pct'], d['apex_pct'], d['exit_pct'], d.get('call')) for d in definitions)
        if signature == self.geometry or not length or length < 200:
            return
        self.geometry = signature
        self.zones = []
        self.records.clear()
        self.pending.clear()
        self.active = self.previous = None
        self.limits.clear()
        self.costs = []
        self.revision += 1
        for i, (key, entry, apex, end, call) in enumerate(signature):
            prev = signature[i-1][3] if i else 0
            following = signature[i+1][1] if i+1 < len(signature) else 1
            # Split the connecting straight so adjoining windows never count the same saving.
            start = max((entry+prev)/2, entry-220/length)
            finish = min((end+following)/2, end+180/length)
            if start < entry < apex < end < finish:
                self.zones.append(dict(key=key, start=start, entry=entry, apex=apex, end=end, finish=finish,
                                       label=str(call or f'트랙 {entry*100:.0f}% 구간')))

    def observe(self, s, traffic_clear):
        t, p, fuel, v, lap = (num(s.get(k)) for k in ('session_time','distance_pct','fuel_l','speed_mps','lap_number'))
        if (None in (t,p,fuel,v,lap) or not 0 <= p < 1 or v < 3 or s.get('on_track') is not True
                or s.get('is_replay') is not False or s.get('on_pit_road') is not False):
            self.active = self.previous = None
            self.pending.clear()
            return
        flags = int(s.get('session_flags') or 0)
        prev = self.previous
        steps = num(s.get('lmu_track_limits_steps'))
        curr = (t,p,fuel,v,lap,flags,s.get('incidents'),steps)
        self.previous = curr
        if prev is None or not 0 < t-prev[0] <= .65 or flags & 0xC108:
            self.active = None
            self.pending.clear()
            return
        new_penalty = bool(flags & ~prev[5] & 0xB0000)
        for record in self.pending:
            record['penalty'] |= new_penalty
            if s.get('track_surface') == 0 or (num(s.get('incident_severity')) or 0)>2:
                record['ambiguous'] = True
            if not record['ambiguous'] and record['inc'] is not None and curr[6] is not None:
                record['points'] = max(record['points'] or 0,curr[6]-record['inc'])
            if not record['ambiguous'] and None not in (record['steps'],steps):
                record['step_delta'] = max(record['step_delta'] or 0,steps-record['steps'])
        while self.pending and t >= self.pending[0]['at']+8:
            row = self.pending.pop(0)
            if self.native_lap:
                row['lap'] = self.native_lap(row['lap'])
            group = self.records[row['key']]
            if any(old['lap']==row['lap'] for old in group):
                continue
            group.append(row)
            self.revision += 1
        if lap != prev[4] or not 0 <= p-prev[1] < .035:
            self.active = None
            return
        def crossing(bound, idx):
            share = min(1., max(0., (bound-prev[1]) / max(1e-9, p-prev[1])))
            return prev[idx] + (curr[idx]-prev[idx])*share
        if self.active is None:
            z = next((z for z in self.zones if prev[1] <= z['start'] < p), None)
            if z is None:
                return
            self.active = dict(zone=z, key=z['key'], begin=crossing(z['start'],0), fuel=crossing(z['start'],2),
                               entry_speed=crossing(z['start'],3),
                               wet=s.get('track_wetness'), compound=s.get('tire_compound'), temp=num(s.get('track_temp_c')),
                               lap=lap, distance=0., off=False, apex_off=False, penalty=False, clear=True, ambiguous=False,
                               inc=prev[6], steps=prev[7], coast=0., coasting=0., braking=False, throttle_seen=False)
        a = self.active
        z = a['zone']
        end_t = crossing(z['finish'],0) if p >= z['finish'] else t
        dt = max(0., end_t-max(a['begin'],prev[0]))
        a['distance'] += (v+prev[3])*.5*dt
        a['clear'] &= traffic_clear
        a['ambiguous'] |= (num(s.get('incident_severity')) or 0)>2 or s.get('track_surface') not in (0,3)
        a['penalty'] |= new_penalty
        if s.get('track_surface') == 0:
            a['off'] = True
            a['apex_off'] |= z['entry'] <= p <= (z['apex']+z['end'])/2
        b, gas = num(s.get('brake')), num(s.get('throttle'))
        if None in (b, gas):
            self.active = None
            return
        if not a['braking']:
            if b >= .08:
                a.update(braking=True, coast=a['coasting'])
            elif b <= .025 and gas <= .05 and a['throttle_seen']:
                a['coasting'] += dt
            elif gas > .05:
                a['coasting'] = 0.
                a['throttle_seen'] |= gas > .55
        if p < z['finish']:
            return
        self.active = None
        used = a['fuel']-crossing(z['finish'],2)
        if used <= 0 or end_t-a['begin'] < 1:
            return
        points = s.get('incidents')
        a.update(at=end_t, time=end_t-a['begin'], used=used,
                 exit_speed=crossing(z['finish'],3),
                 step_delta=steps-a['steps'] if None not in (steps,a['steps']) else None,
                 points=points-a['inc'] if None not in (points,a['inc']) else None)
        self.pending.append(a)

    def evaluate(self, s):
        self.costs, self.limits = [], {}
        current = dict(fuel=num(s.get('fuel_l')), wet=s.get('track_wetness'), compound=s.get('tire_compound'),
                       temp=num(s.get('track_temp_c')))
        if current['fuel'] is None:
            return
        for z in self.zones:
            rows = list(self.records.get(z['key'], ()))
            relevant = [r for r in rows if comparable(r,current)]
            clean = [r for r in relevant if r['clear'] and not r['off'] and not r['penalty'] and not r.get('ambiguous')
                     and (r.get('step_delta') == 0 if r.get('step_delta') is not None else r['points'] == 0)]
            normal = [r for r in clean if r['braking'] and r['coast'] < .18]
            options = []
            for low, high, cue in ((.35,.82,'coast_half'),(.82,1.55,'coast_second'),(1.55,2.5,'coast_here')):
                paired = []
                for r in clean:
                    if not r['braking'] or not low <= r['coast'] < high:
                        continue
                    refs = [b for b in normal if comparable(r,b) and b['lap'] != r['lap']
                            and abs(r['entry_speed']-b['entry_speed'])<=3 and r['exit_speed']>=b['exit_speed']-2]
                    if len({b['lap'] for b in refs}) >= 2:
                        paired.append((r['lap'], median(b['used'] for b in refs)-r['used'],
                                       r['time']-median(b['time'] for b in refs)))
                if len({x[0] for x in paired}) >= 2 and min(x[1] for x in paired) > .008:
                    options.append(dict(zone=z['key'],label=z['label'],entry=z['entry'],cue=cue,
                                        start=z.get('start',z['entry']),finish=z.get('finish',z['entry']),
                                        liters=min(x[1] for x in paired), seconds=max(.05,max(x[2] for x in paired))))
            if options:
                self.costs.append(options)
            offs = [r for r in rows[-12:] if r['clear'] and r['off'] and not r.get('ambiguous')
                    and (r['step_delta'] > 0 if r.get('step_delta') is not None else
                         r['points'] == 1 or r['points'] == 0 and r['penalty'] and r['apex_off'])]
            if len({r['lap'] for r in offs}) < 2:
                continue
            result = dict(cue='cut_repeat', entry=z['entry'], label=z['label'])
            cuts = [r for r in offs if r['apex_off'] and comparable(r,current)]
            penalized = []
            clear_cuts = []
            for cut in cuts:
                refs = [b for b in clean if comparable(cut, b)]
                if len({b['lap'] for b in refs}) < 3:
                    continue
                shorter = median(b['distance'] for b in refs) - cut['distance']
                if shorter > 3:
                    gain = median(b['time'] for b in refs) - cut['time']
                    (penalized if cut['penalty'] else clear_cuts).append((cut['lap'], shorter, gain))
            if any(r['penalty'] for r in cuts):
                result['cue'] = 'cut_penalty'
            if len({lap for lap, _, _ in clear_cuts}) >= 2:
                gains = [gain for _, _, gain in clear_cuts]
                iracing_gain = min(gains) > .12 and all(r.get('step_delta') is None for r in cuts)
                if result['cue'] != 'cut_penalty':
                    if iracing_gain:
                        result.update(cue='cut_margin', gain=round(median(gains), 2))
                    elif max(gains) < .05:
                        result['cue'] = 'cut_no_gain'
                elif iracing_gain and len({lap for lap, _, _ in penalized}) >= 2:
                    mild = [m for _, m, _ in clear_cuts]
                    deep = [m for _, m, _ in penalized]
                    # Distances are for the whole passage, never a permitted curb offset.
                    if min(deep) > max(mild) + 3:
                        result['observed_line'] = dict(one_x_shorter_m=[round(min(mild)), round(max(mild))],
                            penalty_shorter_m=[round(min(deep)), round(max(deep))], one_x_gain_s=round(median(gains), 2))
            self.limits[z['key']] = result


def coast_combinations(groups):
    """One option per corner. Quantize savings down to centiliters; keep cheapest time per total."""
    levels = [{0:(0.,[])}]
    ends = []
    for group in sorted((g for g in groups if g),key=lambda g:max(o.get('finish',o['entry']) for o in g)):
        start=min(o.get('start',o['entry']) for o in group)
        previous=levels[bisect_right(ends,start)]
        states=levels[-1]
        updated = dict(states)
        for option in group:
            gain = floor(option['liters']*100)
            if gain <= 0:
                continue
            for saved,(cost,route) in previous.items():
                target = min(500,saved+gain)
                total = cost+option['seconds']
                if target not in updated or total < updated[target][0]:
                    updated[target] = total,route+[option]
        levels.append(updated)
        ends.append(max(o.get('finish',o['entry']) for o in group))
    return [(saved/100,time,route) for saved,(time,route) in levels[-1].items() if saved]
