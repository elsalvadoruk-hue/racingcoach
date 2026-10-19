"""Measured session events and short-lived HUD cards. No I/O, SDK writes or audio."""
from collections import deque
import math
from statistics import median

from race_engineer import RaceEngineer
from overlay_timing import CARD_S, PRE_CORNER_S, POST_CORNER_S, reviewed_exit


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def session_kind(value):
    value = str(value or '').lower()
    return 'QUALIFY' if 'qual' in value else 'RACE' if value == 'race' else 'PRACTICE' if 'practice' in value or value == 'testing' else 'LIVE'


def lap_time(value):
    return f'{int(value//60)}:{value%60:06.3f}'


def remaining(sample):
    seconds = number(sample.get('session_time_remaining_s'))
    return f'남은 {int(seconds//60)}:{int(seconds%60):02d}' if seconds is not None and 0 <= seconds < 86400 else '남은 시간 —'


class OverlayLapEvents:
    """Observe complete laps at 30Hz; never reuse an old published last-lap time."""
    def __init__(self):
        self.scope = None
        self.reset()

    def reset(self):
        self.previous = self.started = self.started_best = self.pending = None
        self.logical_lap = self.boundary_at = self.early_at = None
        self.clean = False
        self.completed = self.invalid = None
        self.invalid_laps = deque(maxlen=8)

    def ingest(self, sample, run_id, kind):
        scope = run_id, session_kind(kind)
        if scope != self.scope:
            self.reset()
            self.scope = scope
        at, pct, lap = (number(sample.get(key)) for key in ('session_time', 'distance_pct', 'lap_number'))
        if (at is None or pct is None or not 0 <= pct < 1 or lap is None or not lap.is_integer()
                or sample.get('on_track') is not True or sample.get('is_replay') is not False
                or sample.get('on_pit_road') is not False):
            self.reset()
            return
        previous = self.previous
        if previous and at == previous['session_time']:
            return
        # Lap and LapDistPct can change on adjacent SDK ticks at the same crossing.
        wrapped = bool(previous and previous['distance_pct'] > .9 and pct < .1)
        if previous:
            dt = at-previous['session_time']
            step = pct-previous['distance_pct']+(1 if wrapped else 0)
            late = self.boundary_at is not None and 0 <= at-self.boundary_at <= .5 and pct < .02 and previous['distance_pct'] < .02
            early = pct > .98 and previous['distance_pct'] > .98
            counter_ok = lap == previous['lap_number'] or (lap == previous['lap_number']+1 and (wrapped or late or early))
            if (not 0 < dt <= .5 or not 0 <= step < .05 or not counter_ok
                    or (self.early_at is not None and not wrapped and at-self.early_at > .5)):
                self.reset()
                previous = None
            elif lap == previous['lap_number']+1 and early and not wrapped:
                self.early_at = at
        if self.logical_lap is None:
            self.logical_lap = lap
        incidents = number(sample.get('incidents'))
        old_incidents = number(previous.get('incidents')) if previous else None
        increase = incidents-old_incidents if incidents is not None and old_incidents is not None else 0
        if scope[1] == 'QUALIFY' and increase > 0 and lap not in self.invalid_laps:
            self.invalid_laps.append(lap)
            self.invalid = {'id': f'invalid:{lap}:{at}', 'at': at, 'lap': lap,
                            'reason': ('트랙 이탈 · ' if sample.get('track_surface') == 0 else '인시던트 ')+f'+{increase:g}점'}
        clean = (sample.get('track_surface') == 3 and incidents is not None
                 and old_incidents is not None and incidents == old_incidents
                 and number(sample.get('session_flags')) is not None and not int(sample['session_flags']) & 0xC108)
        if previous and wrapped:
            crossed = previous['session_time']+(1-previous['distance_pct'])/step*dt
            self.pending = ({'lap': self.logical_lap, 'at': at, 'old_best': self.started_best,
                             'elapsed': crossed-self.started,
                             'last_before': number(previous.get('lap_last_s'))}
                            if self.started is not None and self.clean and clean else None)
            self.started, self.clean = crossed, clean
            self.logical_lap += 1
            self.boundary_at, self.early_at = at, None
            self.started_best = number(sample.get('lap_best_s'))
        else:
            self.clean = self.clean and clean
            if self.started is not None and 0 <= at-self.started <= 3:
                self.started_best = number(sample.get('lap_best_s'))
        self.previous = dict(sample, session_time=at, distance_pct=pct, lap_number=lap)
        pending = self.pending
        if not pending:
            return
        if at-pending['at'] > 3 or self.logical_lap != pending['lap']+1:
            self.pending = None
            return
        last, best = number(sample.get('lap_last_s')), number(sample.get('lap_best_s'))
        # A stale positive time after an invalid lap must not become a new result.
        if last is None or not 0 < last < 3600 or abs(last-pending['elapsed']) > .15:
            return
        # SDK lap clocks can update about a second after position wraps. A
        # similar previous lap is not confirmation of this completed lap.
        current = number(sample.get('lap_current_s'))
        clock_reset = current is not None and 0 <= current <= at-pending['at']+.15
        if last == pending['last_before'] and not clock_reset:
            return
        old_best = pending['old_best']
        faster = old_best is not None and old_best > 0 and last < old_best-.01
        if faster and (best is None or abs(last-best) >= .02):
            return  # Last time and best time may be published on adjacent SDK ticks.
        self.completed = {'id': f'lap:{pending["lap"]}:{pending["at"]}', 'at': at,
                          'lap': pending['lap'], 'time_s': last, 'previous_best_s': old_best,
                          'new_best': faster}
        self.pending = None

    def view(self, sample):
        at = number(sample.get('session_time'))
        return {name: dict(event) if at is not None and event and 0 <= at-event['at'] <= 12 else None
                for name, event in (('lap', self.completed), ('invalid', self.invalid))}


class SessionCards:
    """Select measured cards after reference alignment; consume each event once."""
    DURATION_S, GRAPH_SECONDS = CARD_S, 45.

    def __init__(self, footer=False):
        # The native HUD uses compact notices. Keep full-card timing available
        # for the original preview/validation callers that reserve graph space.
        self.footer = footer
        self.DURATION_S = 4. if footer else CARD_S
        self.reset()

    def reset(self):
        self.scope = self.last_at = self.reference = self.current = None
        self.graph_since = -math.inf
        self.seen = deque(maxlen=128)
        self.pending = {}
        self.corners = {}
        self.passes = {}
        self.improved = {}
        self.shown_laps = deque(maxlen=8)
        self.last_important = self.last_setting = -math.inf
        self.review_until = -math.inf

    @staticmethod
    def safe(sample):
        brake, throttle, steer, speed = (number(sample.get(key)) for key in ('brake', 'throttle', 'steering_rad', 'speed_mps'))
        return (None not in (brake, throttle, steer, speed) and 0 <= brake <= .05 and .8 <= throttle <= 1
                and abs(steer) <= .12 and speed > 10
                and sample.get('car_left_right') in (0, 1)
                and number(sample.get('session_flags')) is not None and not int(sample['session_flags']) & 0xC119)

    def room(self, view, starting=False):
        if self.footer:
            # No graph is covered, so a future reference/long straight is not
            # required. Actual workload still closes the notice immediately.
            return self.safe(view.get('snapshot') or {}) and reviewed_exit(view, .5)
        sample, window = view.get('snapshot') or {}, view.get('card_window') or {}
        at, measured, clear = number(sample.get('session_time')), number(window.get('at')), number(window.get('clear_s'))
        return (None not in (at, measured, clear) and 0 <= at-measured <= .2
                and window.get('lap') == sample.get('lap_number') and at >= self.review_until
                and clear >= PRE_CORNER_S+(self.DURATION_S if starting else 0)
                and self.safe(sample) and reviewed_exit(view))

    def add(self, key, at, kind, name, caption, value, color, left, right, basis, priority=1):
        if key in self.seen:
            if kind in ('fuel', 'fuel_setting') and key in self.pending:
                # A still-critical fuel condition is current information, even after a long corner.
                self.pending[key].update(at=at, left=left, right=right)
            if kind in ('fuel', 'fuel_setting') and self.current and self.current['id'] == key:
                self.current.update(left=left, right=right)
            return
        self.seen.append(key)
        self.pending[key] = {'id': key, 'at': at, 'kind': kind, 'session': name[0], 'name': name[1],
                             'caption': caption, 'value': value, 'color': color, 'left': left, 'right': right,
                             'basis': basis, 'priority': priority}

    def select(self, view):
        sample = view.get('snapshot') or {}
        at = number(sample.get('session_time'))
        kind = (view.get('session') or {}).get('kind')
        scope = view.get('run_id'), kind
        valid = (view.get('connected') is True and sample.get('on_track') is True
                 and sample.get('is_replay') is False and sample.get('on_pit_road') is False
                 and sample.get('session_state') in (4, 5) and at is not None and bool(scope[0]))
        if not valid or view.get('practice'):
            self.reset()
            return None
        if scope != self.scope or (self.last_at is not None and not 0 <= at-self.last_at <= 1):
            self.reset()
            self.scope = scope
        self.last_at = at
        if not self.safe(sample):
            self.review_until = at+POST_CORNER_S
        if kind in ('PRACTICE', 'QUALIFY'):
            self.corner(view, at, kind)
        events = view.get('session_events') or {}
        if kind in ('PRACTICE', 'QUALIFY') or self.footer and kind == 'RACE':
            event = events.get('lap') or {}
            stamp, elapsed, best = (number(event.get(key)) for key in ('at', 'time_s', 'previous_best_s'))
            if stamp is not None and 0 <= at-stamp <= 8 and elapsed is not None and elapsed > 0:
                if kind in ('PRACTICE', 'RACE'):
                    delta = elapsed-best if best is not None and best > 0 else None
                    self.add(event['id'], stamp, 'lap', ('레이스' if kind == 'RACE' else '연습', '랩 완료'), '이전 베스트 대비' if delta is not None else '이번 랩 기록',
                             f'{delta:+.3f}초' if delta is not None else lap_time(elapsed),
                             'success' if delta is not None and delta < 0 else 'text', f'이번 {lap_time(elapsed)}',
                             f'이전 {lap_time(best)}' if delta is not None else '깨끗한 완료 랩', '개인 베스트 대비' if delta is not None else '관측 완료 랩')
                else:
                    improved = event.get('new_best') is True and best is not None and best > elapsed
                    known_best = best is not None and best > 0
                    self.add(event['id'], stamp, 'lap', ('퀄리', '베스트 갱신' if improved else '랩 완료'),
                             '개인 베스트 갱신 · 이번 랩' if improved else '이번 유효 랩',
                             lap_time(elapsed), 'success' if improved else 'text',
                             f'이전보다 {elapsed-best:+.3f}초' if known_best else '베스트 비교 없음',
                             remaining(sample), '개인 베스트 대비' if known_best else '관측 완료 랩')
                if event['id'] in self.pending:
                    delta = elapsed-best if best is not None and best > 0 else None
                    self.pending[event['id']]['lap_text'] = ('랩 '+lap_time(elapsed)+
                        (f' · 베스트 대비 {delta:+.3f}초' if delta is not None else ''))
                # Add the largest measured loss to this result, never a second popup.
                summary = self.passes.get(event.get('lap'), {})
                losses = [row for row in summary.values() if row['delta'] >= .1]
                if event['id'] in self.pending and losses:
                    worst = max(losses, key=lambda row: row['delta'])
                    self.pending[event['id']].update(right=f"{worst['label']} +{worst['delta']:.2f}초",
                                                    basis=f'G61 {len(summary)}구간 중 최대 손실')
                    self.pending[event['id']]['lap_text'] += f" · {worst['label']} +{worst['delta']:.2f}초"
            invalid = events.get('invalid') or {}
            stamp = number(invalid.get('at'))
            if kind == 'QUALIFY' and stamp is not None and 0 <= at-stamp <= 8:
                self.add(invalid['id'], stamp, 'invalid', ('퀄리', '무효 랩'), '이번 랩 결과', '기록 무효',
                         'warning', invalid['reason'], remaining(sample), '관측 인시던트 기준', priority=2)
        race = view.get('session_race') or {}
        fuel_critical = False
        setting_short = False
        if kind == 'RACE' and race.get('active') is True:
            fuel, pct = number(race.get('fuel_laps')), number(sample.get('distance_pct'))
            shortage, to_go = number(race.get('fuel_finish_shortfall_l')), number(race.get('remaining_laps'))
            fill = number(race.get('fuel_next_stop_l'))
            planned, flags = number(sample.get('pit_sv_fuel')), number(sample.get('pit_sv_flags'))
            if planned is not None and planned >= 0 and flags is not None and flags >= 0 and flags.is_integer():
                planned = planned if int(flags) & 0x10 else 0.
                setting_short = (fill is not None and fill > 0 and fill-planned > 1
                                 and fuel is not None and 0 <= fuel <= 2.5 and shortage is not None and shortage > .1)
            else:
                planned = None
            fuel_critical = (fuel is not None and fuel >= 0 and pct is not None and 0 <= pct < 1
                             and (to_go is None or (shortage is not None and shortage > .1))
                             and fuel-(1-pct) < RaceEngineer.BOX_MARGIN_LAPS+1)
            if fuel_critical:
                key = f'fuel:{sample.get("lap_number")}:{pct >= .75}'
                self.add(key, at, 'fuel', ('레이스', '연료·피트'), '연료 부족 · 피트 판단', '이번 랩 피트',
                         'warning', f'잔여 연료 {fuel:.1f}랩',
                         f'설정 {planned:g} / 필요 {fill:.1f}L' if setting_short else f'필요 급유 {fill:.1f}L' if fill is not None else '급유량 —',
                         '관측 연료 기준', priority=3)
            elif setting_short and (at-self.last_setting >= 120 or
                                    (self.current is not None and self.current['kind'] == 'fuel_setting')):
                self.add(f'fuel-setting:{sample.get("lap_number")}', at, 'fuel_setting', ('레이스', '급유 설정'),
                         '피트 진입 전 확인', '급유 설정 부족', 'warning',
                         f'설정 {planned:g}L', f'필요 {fill:.1f}L', '관측 연비·현재 급유 설정', priority=2)
            ahead = race.get('ahead') or {}
            first, last, change, count = (number(ahead.get(key)) for key in
                                        ('gap_start_s', 'gap_at_line_s', 'gap_change_s_per_lap', 'gap_observations'))
            if (ahead.get('gap_trend') == 'closing' and None not in (first, last, change, count)
                    and 0 < last <= 8 and first > last and count >= 3 and change < -.15):
                key = f'closing:{ahead.get("idx")}:{ahead.get("trend_lap")}'
                self.add(key, at, 'closing', ('레이스', '앞차 접근'), '앞차 추정 간격', f'{first:.1f} → {last:.1f}초',
                         'accent', f'최근 {int(count)-1}랩 연속 접근', f'랩당 약 {abs(change):.2f}초', '출발선 통과 기준')
        self.pending = {key: card for key, card in self.pending.items() if 0 <= at-card['at'] <= 8}
        def relevant(card):
            if card['kind'] == 'fuel':
                return fuel_critical
            if card['kind'] == 'fuel_setting':
                return setting_short and not fuel_critical
            if card['kind'] == 'closing':
                ahead = race.get('ahead') or {}
                return (card['id'] == f'closing:{ahead.get("idx")}:{ahead.get("trend_lap")}'
                        and ahead.get('gap_trend') == 'closing' and 0 < (number(ahead.get('gap_estimate_s')) or math.inf) <= 8)
            if card['kind'] in ('corner', 'improvement'):
                return card.get('reference') == self.reference and (view.get('feedback') or {}).get('corner_id') == card.get('corner_id')
            event = events.get('invalid' if card['kind'] == 'invalid' else 'lap') or {}
            return event.get('id') == card['id']
        self.pending = {key: card for key, card in self.pending.items() if relevant(card)}
        previous_priority = self.current['priority'] if self.current else 0
        if self.current and (at >= self.current['expires_at'] or not relevant(self.current) or not self.room(view)):
            self.current, self.graph_since = None, at
        candidates = sorted(self.pending.values(), key=lambda card: (-card['priority'], -card['at']))
        if candidates and self.room(view, starting=True):
            chosen = candidates[0]
            important = chosen['priority'] >= 2
            can_replace = self.current is not None and chosen['priority'] > self.current['priority'] and important
            if important:
                allowed = at-self.last_important >= 30 or chosen['priority'] > previous_priority > 0
            else:
                allowed = sample.get('lap_number') not in self.shown_laps and at-self.graph_since >= self.GRAPH_SECONDS
            if (allowed or can_replace) and (can_replace or self.current is None):
                self.current = dict(chosen, shown_at=at, expires_at=at+self.DURATION_S)
                self.pending.pop(chosen['id'])
                # A warning also consumes this lap's ordinary-card opportunity.
                self.shown_laps.append(sample.get('lap_number'))
                if important:
                    self.last_important = at
                if chosen['kind'] == 'fuel_setting':
                    self.last_setting = at
        if not self.current:
            return None
        card = dict(self.current)
        if self.footer:
            kind = card['kind']
            if kind == 'lap':
                text = card['lap_text']
            elif kind == 'fuel':
                text = card['value']+' · '+card['left']
                if card['right'].startswith('설정 '):
                    text += ' · 급유 설정 부족'
            elif kind == 'fuel_setting':
                text = card['value']+' · '+card['left']+' / '+card['right']
            elif kind == 'invalid':
                text = '무효 랩 · '+card['left']
            elif kind == 'closing':
                text = '앞차 접근 · '+card['value']
            elif kind == 'corner':
                text = '최근 3회 제동 '+card['value']+' · '+card['right']
            else:
                text = '구간 개선 · '+card['value']
            card.update(presentation='footer', text=text)
        return card

    def corner(self, view, at, kind):
        status = view.get('reference_status') or {}
        duration = number(status.get('lap_time_s'))
        reference = ((status['lap_id'], duration) if status.get('state') == 'READY' and status.get('lap_id')
                     and duration is not None and duration > 0 and status.get('source') != 'PERSONAL' else None)
        if reference != self.reference:
            self.reference, self.corners, self.passes, self.improved = reference, {}, {}, {}
        if reference is None:
            return
        feedback, passed = view.get('feedback') or {}, view.get('corner_pass') or {}
        braking, delta = number(feedback.get('brake_delta_m')), number(feedback.get('delta_s'))
        lap, ended = number(passed.get('lap')), number(passed.get('exit_at'))
        if (delta is None or lap is None or ended is None or not 0 <= at-ended <= 30
                or passed.get('clean') is not True or passed.get('id') != feedback.get('corner_id')):
            return
        identifier = passed['id']
        history = self.corners.setdefault(identifier, deque(maxlen=4))
        if history and history[-1]['lap'] == lap:
            return
        if history and lap != history[-1]['lap']+1:
            history.clear()
        digits = ''.join(c for c in str(identifier) if c.isdigit())
        label = passed.get('call') or (f'{digits}번 구간' if digits else '해당 구간')
        self.passes = {key: value for key, value in self.passes.items() if lap-1 <= key <= lap}
        self.passes.setdefault(lap, {})[identifier] = {'delta': delta, 'label': label}
        previous = list(history)
        history.append({'lap': lap, 'braking': braking, 'delta': delta})
        if kind != 'PRACTICE':
            return
        if len(previous) >= 3:
            baseline = median(row['delta'] for row in previous[-3:])
            gain = baseline-delta
            watermark = self.improved.get(identifier)
            if gain >= .15 and (watermark is None or delta <= watermark-.15):
                key = f'improvement:{reference}:{identifier}:{lap}'
                self.add(key, ended, 'improvement', ('연습', '개선 확인'), '이전 3회 중앙값 대비',
                         f'{gain:.2f}초 단축', 'success', label, f'G61 대비 {delta:+.2f}초', '같은 기준·깨끗한 통과')
                if key in self.pending:
                    self.pending[key].update(reference=reference, corner_id=identifier)
                    self.improved[identifier] = delta
        latest = list(history)[-3:]
        if len(latest) < 3 or not all(item['delta'] >= .1 for item in latest) or braking is None:
            return
        sign = 1 if braking > 0 else -1
        if not all(item['braking'] is not None and item['braking']*sign >= 5 for item in latest):
            return
        key = f'corner:{reference}:{identifier}:{lap}'
        self.add(key, ended, 'corner', ('연습', '반복 코너'), '제동 시작 · Garage61 대비',
                 f'{abs(braking):.0f}m {"늦음" if braking > 0 else "빠름"}', 'warning',
                 '최근 3회 반복', f'코너 {delta:+.2f}초', 'Garage61 대비')
        if key in self.pending:
            self.pending[key].update(reference=reference, corner_id=identifier)
