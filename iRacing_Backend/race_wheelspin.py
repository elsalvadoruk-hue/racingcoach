"""Driver feedback after repeated measured power-on wheelspin at a named GT3 corner."""
from collections import defaultdict, deque

from corner_controls import CornerControls, impaired
from live_controls import finite, pedal_ratio
from live_corners import call_seconds
from radio_policy import quiet_window


# Ordered by increasing intervention. See the per-car manuals in 피트월-설정추천과-경기결과.md.
# These are candidate positions only. The next position must also have been used on this tire.
MAPS = {
    'acuransxevo22gt3': range(2, 13),
    'amvantageevogt3': range(1, 12),
    'audir8lmsevo2gt3': range(1, 12),
    'bmwm4gt3': range(1, 10),
    'chevyvettez06rgt3': range(1, 12),
    'ferrari296gt3': range(2, 13),
    'fordmustanggt3': range(2, 13),
    'lamborghinievogt3': range(1, 11),  # 11 duplicates 10, 12 is OFF.
    'mclaren720sgt3': range(2, 13),
    'mercedesamgevogt3': range(11, 0, -1),
    'porsche992rgt3': range(1, 13),  # Positive through our last recording, not a physical upper limit.
}
TRIAL_LEVELS = range(2, 13)
KEY = 'control_tc_wheelspin'
SYMPTOM = '가속 때 뒷바퀴가 자꾸 헛돕니다.'


def trial(level, crossed_line=False):
    if crossed_line:
        return f'이번 랩에서 TC {level}번 써보세요.'
    return f'다음 랩 TC {level}번을 시험해 보세요.'


def voice_catalog():
    # Other confirmed corner names may have a corresponding custom recording.
    return ([f'{n}번 코너.' for n in range(1, 31)] + ['YOKOHAMA-S.', SYMPTOM]
            + [trial(n,crossed) for crossed in (False,True) for n in TRIAL_LEVELS])


class RaceWheelspin:
    def __init__(self):
        self.scope = None
        self.race = None
        self.observed = defaultdict(set)
        self.sent_laps = deque(maxlen=2)
        self.reset_stint()

    def reset_stint(self):
        self.passes = defaultdict(lambda: deque(maxlen=3))
        self.reported = set()
        self.offers = {}
        self.receipts = {}
        self.current = self.finished = self.last_t = self.quiet_since = None
        self.gear = self.map_value = self.map_since = self.gear_since = None
        self.incidents = self.dirty_lap = None

    def _sample_pass(self, active, s, clean, signature, gap):
        key = (active['id'], active['lap'])
        if self.current is None or self.current['key'] != key:
            self.current = dict(key=key, call=active['call'], stamp=signature,
                                temp=finite(s.get('track_temp_c')), clean=True,
                                start=None, measured=False, spin=False)
        row = self.current
        temp = finite(s.get('track_temp_c'))
        row['clean'] &= (clean and not gap and row['stamp'] == signature
                         and temp is not None and row['temp'] is not None and abs(temp-row['temp'])<=3)
        gas, brake = pedal_ratio(s.get('throttle')), pedal_ratio(s.get('brake'))
        if not row['clean'] or gas is None or brake is None:
            row['clean'] = False
            return
        v, t = s['speed_mps'], s['session_time']
        if s['distance_pct'] < active['apex_pct'] or gas < .65 or brake >= .02 or v <= 15:
            row['start'] = None
            return
        wheels = [finite(s.get(k+'_wheel_speed')) for k in ('LF','RF','LR','RR')]
        if self.gear is None or None in wheels or min(wheels)<.55*v or max(wheels)>1.6*v:
            row['clean'] = False
            return
        lf, rf, lr, rr = wheels
        if self.gear <= 0 or t-self.gear_since <= .30 or abs((lf+rf)/(2*v)-1) >= .035:
            row['start'] = None
            return
        row['measured'] = True
        if lr-lf <= .04*v or rr-rf <= .04*v or (lr+rr-lf-rf) <= .16*v:
            row['start'] = None
        elif row['start'] is None:
            row['start'] = t
        elif t-row['start'] >= .30:
            row['spin'] = True

    def _finish(self, obs, now, diagnostics):
        row = self.current
        self.current = None
        if not (obs and row and row['key'] == (obs['id'], obs['lap']) and row['clean'] and row['measured']
                and obs['clean'] and obs['call'] == row['call']):
            return
        key, lap = row['key']
        history = self.passes[key]
        if history and history[-1]['key'][1] >= lap:
            return
        history.append(row)
        diagnostics.mark('tc', row['key'], 'measured_pass')
        if row['spin']:
            diagnostics.mark('tc', row['key'], 'wheelspin_pass')
        if (key not in self.reported and row['spin'] and len(history)==3
                and [x['key'][1] for x in history] == [lap-2,lap-1,lap]
                and sum(x['spin'] for x in history)>=2
                and all(x['stamp']==row['stamp'] and x['call']==row['call'] and abs(x['temp']-row['temp'])<=3 for x in history)):
            self.offers[key] = dict(row=row, until=now+45, entry=obs['entry_pct'], queued=False, next_check=now)

    def ingest(self, identity, corners, s, context, radio, now, ready, occupied=False):
        car = identity.get('car_path')
        race = (radio.run_id,car)
        if race != self.race:
            self.race = race
            self.observed.clear()
            self.sent_laps.clear()
        eligible = (identity.get('game')=='iracing' and car in MAPS
                    and str(radio.metadata.get('session_type') or '').lower()=='race'
                    and s.get('on_track') is True and s.get('is_replay') is False and s.get('on_pit_road') is False)
        scope = (radio.run_id, corners.revision, car, s.get('tire_compound')) if eligible else None
        if scope != self.scope:
            self.reset_stint()
            self.scope = scope
        if not eligible:
            return
        t, lap = s['session_time'], s.get('lap_number')
        gap = self.last_t is None or not 0 < t-self.last_t <= .2
        self.last_t = t
        incident = s.get('incidents')
        if self.dirty_lap!=lap and ((self.incidents is not None and incident != self.incidents) or s.get('track_surface') == 0):
            self.dirty_lap = lap
            for history in self.passes.values():
                if history and history[-1]['key'][1]==lap:
                    history.pop()
        self.incidents = incident
        gear, level, compound, wet, temp, speed = (finite(s.get(k)) for k in
            ('gear','tc_setting','tire_compound','track_wetness','track_temp_c','speed_mps'))
        if gear != self.gear or gap:
            self.gear, self.gear_since = gear, t
        pct = finite(s.get('distance_pct'))
        clean = (lap is not None and pct is not None and 0<=pct<1 and self.dirty_lap != lap
                 and incident is not None and s.get('car_left_right')==1
                 and s.get('session_state')==4 and s.get('track_surface')==3 and s.get('session_flags') is not None
                 and not int(s['session_flags']) & 0xC119 and not impaired(s)
                 and speed is not None and speed>15 and compound is not None and compound>=0
                 and wet is not None and wet>0 and temp is not None and level is not None and level>=0 and level.is_integer())
        positions = MAPS[car]
        adjustable = s.get('setup_fixed') is False and radio.control_available('tc')
        verified = level if clean and adjustable and level in positions else None
        if gap or verified != self.map_value:
            self.map_value, self.map_since = verified, t
        if verified is not None and t-self.map_since>=1:
            self.observed[compound].add(verified)
        signature = (level, compound, wet, CornerControls._mechanical(s))
        # A car two seconds away need not invalidate direct wheel-speed evidence.
        # Use both measured gaps only with known traffic coverage; speaking still needs SOLO.
        ahead, behind = finite(s.get('car_ahead_m')), finite(s.get('car_behind_m'))
        measured_gaps = ahead is not None and behind is not None and min(ahead,behind)>=0
        separated = (context in ('SOLO','TRAFFIC_PRESENT') and min(ahead,behind)>max(15,.7*speed)
                     if measured_gaps and speed is not None else context=='SOLO')
        obs = corners.last_observation
        if obs is not self.finished:
            self.finished = obs
            if clean and not gap and separated:
                self._finish(obs, now, radio.control_trials)
            else:
                self.current = None
        if corners.active and corners.active.get('call'):
            self._sample_pass(corners.active, s, clean and separated, signature, gap)
        else:
            self.current = None
        clear = clean and separated and context=='SOLO' and quiet_window(s)
        if not clear or gap:
            self.quiet_since = None
        elif self.quiet_since is None:
            self.quiet_since = t
        for key, offer in list(self.offers.items()):
            row = offer['row']
            n = row['key'][1]
            opportunity = row['key']
            log = radio.control_trials
            log.mark('tc', opportunity, 'evidence')
            if (not clean or now>=offer['until'] or signature!=row['stamp'] or abs(temp-row['temp'])>3
                    or lap not in (n,n+1) or lap>n and pct>=offer['entry']
                    or not any(d['id']==key and d.get('call')==row['call'] for d in corners.definitions)):
                del self.offers[key]
                continue
            if offer['queued'] or now<offer['next_check']:
                continue
            if occupied or not self._window(s, now, radio, ''):
                log.mark('tc', opportunity, 'occupied' if occupied else 'no_window')
                continue
            offer['next_check'] = now+.25
            if not radio.allows(KEY):
                log.mark('tc', opportunity, 'disabled')
                continue
            if self.sent_laps and (lap-self.sent_laps[-1]<2 or sum(lap-x<10 for x in self.sent_laps)>=2):
                log.mark('tc', opportunity, 'cooldown')
                continue
            crossed = lap>n
            has_next = crossed or not (int(s['session_flags']) & 2 or s.get('session_laps_remaining') in (0,1))
            target = int(verified) + positions.step if verified is not None and has_next else None
            if target not in TRIAL_LEVELS or target not in positions:
                log.mark('tc', opportunity, 'map_unavailable')
                target = None
            elif target not in self.observed[compound]:
                log.mark('tc', opportunity, 'map_unobserved')
                target = None
            log.mark('tc', opportunity, 'candidate')
            action = trial(target,crossed) if target is not None else SYMPTOM
            parts = [row['call']+'.', action]
            if target is not None:
                fits = self._window(s,now,radio,' '.join(parts))
                if not fits or not ready(action):
                    log.mark('tc', opportunity, 'no_window' if not fits else 'audio_missing')
                    parts[1], target = SYMPTOM, None
            text = ' '.join(parts)
            if not self._window(s,now,radio,text):
                log.mark('tc', opportunity, 'no_window')
                continue
            if not all(ready(part) for part in parts):
                log.mark('tc', opportunity, 'audio_missing')
                continue
            if any(e['group']=='control_advice' and radio.valid(e,now) for e in radio.pending):
                log.mark('tc', opportunity, 'occupied')
                continue
            token = (key,n,lap,target)
            event = radio.emit(KEY, now, text, role='coach', ttl=min(3,offer['until']-now), cooldown=0,
                               group='control_advice', condition=('wheelspin',token), speech_text=text,
                               speech_parts=parts, prepared_only=True)
            if event:
                log.mark('tc', event['id'], 'queued_trial' if target is not None else 'queued_symptom')
                offer['queued'] = True
                if len(self.receipts)>=48:
                    del self.receipts[next(iter(self.receipts))]
                self.receipts[event['id']] = (key,lap)
                break

    def _window(self,s,now,radio,text):
        lead = finite(s.get('workload_in_s'))
        return (self.quiet_since is not None and s['session_time']-self.quiet_since>=1
                and lead is not None and lead>=call_seconds(text)+3.5 and radio.control_window(s,text,now))

    def valid(self,token,s,now,radio,text):
        key,n,spoken_lap,target = token
        offer = self.offers.get(key)
        return (offer is not None and offer['row']['key'][1]==n and now<offer['until']
                and (target is None or s.get('setup_fixed') is False and s.get('lap_number')==spoken_lap
                     and radio.control_available('tc')
                     and (spoken_lap>n or not (int(s.get('session_flags') or 0)&2 or s.get('session_laps_remaining') in (0,1))))
                and self._window(s,now,radio,text))

    def delivery(self,identifier,status):
        if status not in ('started','completed','failed','expired','interrupted','cancelled','muted'):
            return
        receipt = self.receipts.pop(identifier,None)
        if receipt and status in ('started','completed'):
            key,lap = receipt
            self.reported.add(key)
            self.sent_laps.append(lap)
