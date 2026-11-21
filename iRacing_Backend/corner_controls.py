"""Cockpit advice attributed to the setting used in each corner phase."""
from collections import defaultdict, deque

from driving_learning import evaluate, paired
from live_controls import MIN_RUN_S, finite, pedal_ratio
from live_corners import CALL_ACCELERATING, CALL_EARLY_S, CALL_LEAD_S, call_seconds
from setup_advisor import matches
from race_braking import BIAS_KEY, BIAS_STEP, BrakingSamples, bias_phrase, bias_target


FIELDS = {'brake_bias': 'brake_bias', 'tc': 'tc_setting', 'abs': 'abs_setting'}
LABELS = {'brake_bias': '앞 바이어스', 'tc': 'TC', 'abs': 'ABS'}
# These keys select the driver's existing per-control alert preferences only.
ALERTS = {'brake_bias': 'control_bias_forward', 'tc': 'control_tc_more', 'abs': 'control_abs_more'}
PHASES = {'brake_bias': 'brake', 'abs': 'brake', 'tc': 'power'}
TRIALS = {'forward': '바이어스를 앞쪽으로', 'rearward': '바이어스를 뒤쪽으로',
          'tc': 'TC 개입을', 'abs': 'ABS 개입을'}


def conditions(s):
    fuel, temp, compound, wet = (finite(s.get(k)) for k in ('fuel_l','track_temp_c','tire_compound','track_wetness'))
    return (fuel if fuel is not None and fuel >= 0 else None, temp,
            compound if compound is not None and compound >= 0 else None,
            wet if wet is not None and wet > 0 else None)


def impaired(s):
    return (bool(int(s.get('engine_warnings') or 0) & ~0x20) or bool(s.get('pit_repair_left_s'))
            or bool(s.get('tow_time_s')) or s.get('lmu_parts_detached') is True or s.get('lmu_overheating') is True)


def setting(sample, control):
    value = finite(sample.get(FIELDS[control]))
    if value is None or value < 0 or (control == 'brake_bias' and not 0 < value < 100):
        return None
    return value


class CornerControls:
    def __init__(self):
        self.reset()

    def reset(self):
        self.revision = None
        self.pass_data = self.finished = self.card = None
        self.previous = None
        self._next_check = 0.
        self.rows = defaultdict(lambda: deque(maxlen=24))
        self.shown = {}
        self.tried = set()
        self.changed_from = {}
        self.cached = {}
        self.braking = BrakingSamples()
        self.bias_car = self.track_length = None
        self.bias_heard = set()
        self.bias_receipts = {}

    def ingest(self, corners, s, context, counter, bias_car=None, length=None):
        driving = s.get('on_track') is True and s.get('is_replay') is False and s.get('on_pit_road') is False
        if corners.revision != self.revision or not driving:
            if self.revision != corners.revision or self.rows or self.pass_data or self.card or self.braking.samples:
                self.reset()
            self.revision = corners.revision
        if not driving:
            return
        self.bias_car, self.track_length = bias_car, finite(length)
        if bias_car in BIAS_STEP and getattr(corners, 'definitions', ()):
            self.braking.ingest(s, context=='SOLO' and not impaired(s), self._mechanical(s))
        previous, self.previous = self.previous, (s['session_time'], finite(s.get('speed_mps')))
        completed = corners.last_observation
        if completed is not self.finished:
            self.finished = completed
            if completed and self.pass_data and self.pass_data['key'] == (completed['id'], completed['lap']):
                self._sample(self.pass_data, s, context, counter)
                self._finish(completed, self._speed_at(previous, s, completed['exit_at']))
            self.pass_data = None
        active = corners.active
        if not active or not active.get('call') and bias_car not in BIAS_STEP:
            self.pass_data = None
            return
        key = (active['id'], active['lap'])
        if self.pass_data is None or key != self.pass_data['key']:
            self.pass_data = dict(key=key, apex=active['apex_pct'], exit=active['exit_pct'],
                entry_speed=self._speed_at(previous, s, active['entry_at']),
                valid=True, settings={}, initial={k:setting(s,k) for k in FIELDS},
                condition=conditions(s), mechanical=self._mechanical(s),
                last=s['session_time'], held={}, signals=set(), time_error=.05, corrections=0,
                was_counter=False, steering_known=True, abs_known=True, abs_time=0.,
                phase_time={'brake': 0., 'power': 0.})
        self._sample(self.pass_data, s, context, counter)

    @staticmethod
    def _speed_at(previous, current, at):
        if previous:
            t, v = previous
            end, w = current['session_time'], finite(current.get('speed_mps'))
            if t <= at <= end and 0 < end-t <= .2 and v is not None and w is not None:
                return v + (w-v) * (at-t) / (end-t)
        return None

    @staticmethod
    def _mechanical(s):
        return tuple(s.get(k) for k in ('setup_fingerprint','front_arb_raw','rear_arb_raw','front_arb','rear_arb',
                                         'rear_wing','pressure_lf','pressure_rf','pressure_lr','pressure_rr'))

    def _sample(self, p, s, context, counter):
        dt = s['session_time'] - p['last']
        p['last'] = s['session_time']
        brake, throttle = pedal_ratio(s.get('brake')), pedal_ratio(s.get('throttle'))
        if (brake is None or throttle is None or not 0 <= dt <= .2 or context != 'SOLO'
                or s.get('car_left_right') != 1 or s.get('track_surface') != 3 or s.get('session_state') != 4
                or s.get('session_flags') is None or int(s['session_flags']) & 0xC119
                or s.get('lmu_lap_invalidated') or s.get('lmu_count_lap_flag') in (0, 1)
                or impaired(s) or self._mechanical(s) != p['mechanical']
                or conditions(s)[2:] != p['condition'][2:]):
            p['valid'] = False
            return
        p['time_error'] = max(p['time_error'], dt * 2)
        if s['distance_pct'] > p['exit']:
            return
        speed = s['speed_mps']
        braking = s['distance_pct'] <= p['apex'] and brake > .2 and speed > 22
        power = s['distance_pct'] >= p['apex'] and throttle > .5 and brake < .05 and speed > 15
        if braking:
            p['abs_known'] &= isinstance(s.get('abs_active'), bool)
            if s.get('abs_active') is True:
                p['abs_time'] += dt
        p['steering_known'] &= counter is not None
        p['corrections'] += int(counter is True and not p['was_counter'])
        p['was_counter'] = counter is True
        for phase, engaged in (('brake', braking), ('power', power)):
            if engaged:
                p['phase_time'][phase] += dt
        # A change after releasing the brake is irrelevant to that braking event.
        # If the driver brakes again at the new value before exit, discard the mixed pass.
        for control in FIELDS:
            engaged = brake > .05 if PHASES[control] == 'brake' else throttle > .5 and brake < .05
            if engaged:
                p['settings'].setdefault(control, set()).add(setting(s,control))
        wheels = [finite(s.get(w + '_wheel_speed')) for w in ('LF','RF','LR','RR')] if braking else []
        front_slip = (bool(wheels) and None not in wheels and min(wheels) >= 0
                      and max(wheels[:2]) < .88 * speed and min(wheels[2:]) > .96 * speed)
        axle_slip = (bool(wheels) and None not in wheels and min(wheels) >= 0
                     and min(max(wheels[:2]), max(wheels[2:])) < .88 * speed)
        for signal, seen in (('forward', braking and counter is True), ('rearward', front_slip),
                             ('abs', axle_slip and s.get('abs_active') is False),
                             ('tc', power and counter is True)):
            p['held'][signal] = p['held'].get(signal, 0.) + dt if seen else 0.
            if p['held'][signal] >= MIN_RUN_S:
                p['signals'].add(signal)

    def _finish(self, corner, exit_speed):
        p = self.pass_data
        if not (p['valid'] and corner['clean'] and corner['traffic_context'] == 'SOLO'):
            return
        settings = {k:v for k,v in p['initial'].items() if v is not None}
        engaged = set()
        for control, values in p['settings'].items():
            if len(values) > 1:
                return  # the setting changed while it mattered
            value = next(iter(values))
            if value is None:
                settings.pop(control, None)
            else:
                settings[control] = value
                if p['phase_time'][PHASES[control]] >= MIN_RUN_S:
                    engaged.add(control)
        if not engaged or not p['steering_known'] or p['entry_speed'] is None or exit_speed is None:
            return
        if not p['abs_known'] or p['abs_time'] >= .1 * p['phase_time']['brake']:
            p['signals'].discard('abs')
        key, lap = p['key']
        row = dict(id=f'{key}/{lap}', scope='stint', window=key, kind='corner', source='LIVE',
                   run='stint', lap=lap, ordinal=lap, traffic='SOLO', controls=settings, engaged=engaged,
                   condition=p['condition'], conditions=dict(zip(('fuel','temp','compound','wet'), p['condition'])),
                   mechanical=p['mechanical'], signals=p['signals'],
                   brake_pct=corner['brake_pct'] if corner['brake_observed'] else None,
                   metrics=dict(time_s=corner['duration_s'], time_error_s=p['time_error'],
                                entry_speed=p['entry_speed']*3.6, end_speed=exit_speed*3.6,
                                corrections=p['corrections']))
        if self.rows[key] and self.rows[key][-1]['lap'] >= lap:
            return
        if self.bias_car in BIAS_STEP:
            row['braking'] = self.braking.measure(corner,self.track_length,self.bias_car)
        self.rows[key].append(row)
        self.cached.pop(key, None)

    def _recommend(self, definition, s, practice, allows):
        key = definition['id']
        rows = self.rows.get(key)
        if not rows:
            return
        settings = {k:setting(s,k) for k in FIELDS}
        current = conditions(s)
        mechanical = self._mechanical(s)
        comparable = [r for r in rows if matches(current, r['condition']) and r['mechanical'] == mechanical]
        for control in FIELDS:
            value = settings[control]
            if not allows(ALERTS[control]) or value is None:
                continue
            subset = [r for r in comparable if control in r['engaged']
                      and set(r['controls']) == {k for k,v in settings.items() if v is not None}
                      and all(settings[k] == v for k, v in r['controls'].items() if k != control)]
            old = [r for r in subset if r['controls'][control] == value]
            if len(old) < 4:
                continue
            for target in sorted({r['controls'][control] for r in subset} - {value}, key=lambda v: abs(v-value)):
                other = [r for r in subset if r['controls'][control] == target]
                if len(other) < 4:
                    continue
                cache_key = (control, value, target, tuple(r['lap'] for r in old), tuple(r['lap'] for r in other))
                cache = self.cached.setdefault(key, {})
                if cache_key not in cache:
                    pairs = paired(old, other, control=control)
                    outcome = evaluate(pairs)
                    if len(cache) >= 32:
                        cache.clear()
                    cache[cache_key] = bool(outcome and not outcome['preferred_low'] and all(
                        b['metrics']['corrections'] <= a['metrics']['corrections'] for a,b in pairs))
                if cache[cache_key]:
                    return control, target, None
        if not practice or len(rows) < 3:
            return
        last = list(rows)[-3:]
        if ([r['lap'] for r in last] != list(range(last[-1]['lap']-2, last[-1]['lap']+1))
                or not 0 <= s['lap_number'] - last[-1]['lap'] <= 1
                or not all(r in comparable and all(settings[k] == v for k,v in r['controls'].items()) for r in last)):
            return
        signals = set.intersection(*(r['signals'] for r in last))
        for action in ('rearward', 'forward', 'tc', 'abs'):
            control = 'brake_bias' if action in ('forward', 'rearward') else action
            if control != 'brake_bias' and s.get('setup_fixed') is True:
                continue
            if (action in signals and all(control in r['engaged'] for r in last) and allows(ALERTS[control])
                    and (key,control) not in self.tried
                    and not (control == 'brake_bias' and {'forward','rearward'} <= signals)):
                return control, None, action

    def update_card(self, corners, s, now, length, session, context, radio, occupied):
        """Display only on the immediate approach, withdrawing a card as soon as it goes stale."""
        definitions = getattr(corners, 'definitions', ())
        pct, speed, length = (finite(v) for v in (s.get('distance_pct'), s.get('speed_mps'), length))
        lap = s.get('lap_number')
        a = self.card
        for control, (_, applied, _) in tuple(self.changed_from.items()):
            if s.get(FIELDS[control]) != applied:
                del self.changed_from[control]
        if a and s.get(FIELDS[a['control']]) != a['initial']:
            if (now < a['expires_at'] and s.get('session_state') == 4 and
                    a['target'] is not None and s.get(FIELDS[a['control']]) == a['target']):
                self.changed_from[a['control']] = (a['initial'], a['target'], a['corner'])
            self.card = None
        if (not self.rows or not definitions or pct is None or speed is None or speed <= 15 or not length or lap is None
                or context != 'SOLO' or s.get('car_left_right') != 1 or occupied
                or s.get('track_surface') != 3 or s.get('lmu_lap_invalidated') or s.get('lmu_count_lap_flag') in (0,1)
                or impaired(s)
                or not radio.control_window(s, '', now) or corners.active
                or any(d['entry_pct'] <= pct <= d['exit_pct'] for d in definitions)):
            self.card = None
            return
        next_corner = min(definitions, key=lambda d:(d['entry_pct'] - pct) % 1)
        name, key = next_corner.get('call'), next_corner['id']
        target_lap = lap + int(next_corner['entry_pct'] < pct)
        entry = next_corner['entry_pct']
        onsets = [r['braking']['onset_m']/length if r.get('braking') else r['brake_pct']
                  for r in self.rows.get(key, ())]
        brake_lead = max(((entry-p) % 1 for p in onsets if p is not None
                          and (entry-p) % 1 <= 150/length), default=0.)
        end = ((entry-pct) % 1 - brake_lead) * length / speed * CALL_ACCELERATING
        if (not name and self.bias_car not in BIAS_STEP) or end < CALL_LEAD_S or end > 20:
            self.card = None
            return
        if self.card:
            if (self.card['corner'] == key and self.card['call'] == name and self.card['lap'] == target_lap
                    and now < self.card['expires_at'] and radio.allows(ALERTS[self.card['control']])
                    and self.card['settings'] == self._signature(s)):
                return
            self.card = None
        if self.shown.get(key) == target_lap or now < getattr(self, '_next_check', 0):
            return
        self._next_check = now + .25
        session = str(session or '').strip().lower()
        practice = session in ('practice','offline testing','testing','test','warmup')
        if not practice and session != 'race':
            return
        target_ticks = None
        if session=='race' and self.bias_car in BIAS_STEP and radio.allows(BIAS_KEY):
            target_ticks=bias_target(self.rows.get(key,()),self.bias_car,s,conditions(s),self._mechanical(s))
        choice = (('brake_bias',target_ticks/100,None) if target_ticks is not None else
                  self._recommend(next_corner, s, practice, radio.allows) if name else None)
        if not choice:
            return
        control, target, action = choice
        returning = self.changed_from.get(control)
        if target_ticks is not None:
            head = f'다음 {name}.' if name and not name.startswith('다음 ') else (name or '다음 코너')+'.'
            parts = [head,bias_phrase(target_ticks)]
            text = ' '.join(parts)
            reason = '한 클릭 다른 두 쌍 이상에서 축 미끄러짐이 줄고 같은 속도까지의 감속 거리가 짧아졌습니다.'
        elif action:
            phase = '탈출' if control == 'tc' else '제동'
            change = '늘려보세요' if control in ('abs','tc') else '옮겨보세요'
            text = f'다음 {name} {phase}, {TRIALS[action]} 한 단계 {change}.'
            reason = {'tc': '탈출 가속에서 반대 조향이 세 랩 연속 관측됐습니다.',
                      'forward': '진입 제동에서 반대 조향이 세 랩 연속 관측됐습니다.',
                      'rearward': '세 랩 연속 제동 중 앞바퀴의 속도가 뒤보다 크게 떨어졌습니다.',
                      'abs': '세 랩 연속 제동에서 바퀴 속도가 떨어질 때 ABS 개입이 관측되지 않았습니다.'}[action]
        else:
            value = f'{target:g}' + ('퍼센트' if control == 'brake_bias' else '')
            restore = returning and returning[:2] == (target, s[FIELDS[control]]) and returning[2] != key
            text = f'다음 {name}, {LABELS[control]} {value}.'
            if restore:
                text = (f'다음 {name}, 앞 바이어스 {value}로 복귀하세요.' if control == 'brake_bias'
                        else f'다음 {name}, 이전 {LABELS[control]} 맵으로 복귀하세요. {value}입니다.')
            reason = '한 설정만 다른 비교에서 구간 시간이 반복해서 줄었고 탈출 속도와 조향 안정성을 확인했습니다.'
        needed = call_seconds(text)
        if not needed + CALL_LEAD_S <= end <= needed + CALL_EARLY_S or not radio.control_window(s, text, now):
            return
        self.card = dict(id=f'corner/{key}/{target_lap}', corner=key, lap=target_lap, call=name,
                         text=text, reason=reason, control=control, initial=s[FIELDS[control]], target=target,
                         action=action, settings=self._signature(s), shown_at=now, expires_at=now+end-CALL_LEAD_S,
                         delivery='screen', timing='corner')
        self.shown[key] = target_lap
        if target_ticks is not None:
            self.card['bias_parts']=parts
        if action:
            self.tried.add((key, control))

    def view(self):
        if self.card:
            return {k:v for k,v in self.card.items() if k not in ('settings','bias_parts','queued','next_audio_check')}

    def bias_valid(self,token,s,radio,now):
        c=self.card
        if not c or 'bias_parts' not in c or (c['id'],c['initial'],c['target'],self.revision)!=token:
            return False
        text=' '.join(c['bias_parts'])
        lead=finite(s.get('workload_in_s'))
        return (self._signature(s)==c['settings'] and c['expires_at']-now>=call_seconds(text)
                and lead is not None and lead>=call_seconds(text)+CALL_LEAD_S and radio.control_window(s,text,now))

    def speak_bias(self,s,radio,now,ready,occupied):
        c=self.card
        if (not c or 'bias_parts' not in c or c.get('queued') or occupied
                or now < c.get('next_audio_check', 0)):
            return
        token=(c['id'],c['initial'],c['target'],self.revision)
        case=(c['corner'],c['initial'],c['target'])
        if (case in self.bias_heard or not radio.allows(BIAS_KEY) or not self.bias_valid(token,s,radio,now)
                or any(e['group']=='control_advice' and radio.valid(e,now) for e in radio.pending)):
            return
        parts=list(c['bias_parts'])
        c['next_audio_check'] = now + .5
        if not ready(parts[0]):
            parts[0]='다음 코너.'
        if not all(ready(p) for p in parts):
            return
        text=' '.join(parts)
        event=radio.emit('control_bias_corner',now,c['text'],role='coach',ttl=min(3,c['expires_at']-now),
                         cooldown=0,group='control_advice',condition=('corner_bias',token),
                         speech_text=text,speech_parts=parts,prepared_only=True)
        if event:
            c['queued']=True
            if len(self.bias_receipts)>=48:
                del self.bias_receipts[next(iter(self.bias_receipts))]
            self.bias_receipts[event['id']]=case

    def bias_delivery(self,identifier,status):
        if status in ('started','completed','failed','expired','interrupted','cancelled','muted'):
            case=self.bias_receipts.pop(identifier,None)
            if case and status in ('started','completed'):
                self.bias_heard.add(case)

    @staticmethod
    def _signature(s):
        return tuple(s.get(field) for field in FIELDS.values()) + (s.get('track_wetness'),s.get('tire_compound')) + CornerControls._mechanical(s)
