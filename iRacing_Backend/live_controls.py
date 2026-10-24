"""ABS, traction control and brake bias suggestions from the last clean laps.

Evidence is counted, not felt: ABS time while braking, and sustained steering
against the car's rotation under braking or on power. A suggestion is one
step to test, never a confirmed fix, and evidence gathered with one setting
is discarded when that setting changes.
"""
from collections import deque
import math

MIN_RUN_S, STEER_MIN, YAW_MIN = .25, .08, .15
REPEAT_EVENTS, LAPS_NEEDED = 2, 3


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


class ControlAdvisor:
    def __init__(self):
        self.agree = [0., 0.]  # seconds with steering and yaw rate of opposite / equal sign
        self.reset()

    def reset(self):
        self.agree = [0., 0.]
        self.laps = deque(maxlen=5)
        self.settings = None
        self.setup_settings = None
        self.current = None
        self.previous = None

    def _new_lap(self, lap, complete):
        return {'lap': lap, 'clean': complete, 'brake_s': 0., 'abs_s': 0., 'abs_known': True,
                'brake_known': True, 'power_known': True,
                'brake_sites': [], 'power_sites': [],
                'brake_events': 0, 'power_events': 0, 'run': {'brake': 0., 'power': 0.}}

    def ingest(self, sample):
        pct, at, speed, steer, yaw, brake, throttle = (finite(sample.get(k)) for k in (
            'distance_pct', 'session_time', 'speed_mps', 'steering_rad', 'yaw_rate', 'brake', 'throttle'))
        lap = sample.get('lap_number')
        # LMU may provide a signed share of steering lock without a wheel angle.
        steer_limit = STEER_MIN
        if steer is None:
            steer, steer_limit = finite(sample.get('steering_lock_ratio')), .04
        valid = (None not in (pct, at, speed, brake, throttle) and type(lap) in (int, float)
                 and 0 <= pct <= 1 and 0 <= brake <= 1 and 0 <= throttle <= 1
                 and sample.get('on_track') is True and sample.get('is_replay') is False and sample.get('on_pit_road') is False)
        previous, self.previous = self.previous, ({'at': at, 'pct': pct, 'lap': lap, 'incidents': sample.get('incidents')} if valid else None)
        settings = tuple(sample.get(k) for k in ('brake_bias', 'abs_setting', 'tc_setting'))
        setup_settings=tuple(sample.get(k) for k in ('front_arb_raw','rear_arb_raw','front_arb','rear_arb',
                                                     'rear_wing','pressure_lf','pressure_rf','pressure_lr','pressure_rr'))
        if settings != self.settings or setup_settings != self.setup_settings:
            # Laps driven with another setting say nothing about this one.
            self.settings, self.current = settings, None
            self.setup_settings=setup_settings
            self.laps.clear()
        if not valid or previous is None:
            self.current = None
            if sample.get('on_pit_road') is True:
                self.laps.clear()
            return
        dt = at - previous['at']
        clean_step = (0 < dt <= .5 and sample.get('incidents') == previous['incidents']
                      and not int(sample.get('session_flags') or 0) & 0xC108
                      and sample.get('car_left_right') not in (2, 3, 4, 5, 6))
        rollover = lap == previous['lap'] + 1 and previous['pct'] > .9 and pct < .1
        if rollover:
            # The crossing interval belongs to both laps; check it before saving the old one.
            if clean_step and self.current and self.current['clean'] and self.current['brake_s'] >= 2:
                self.laps.append({k: v for k, v in self.current.items() if k != 'run'})
            self.current = self._new_lap(lap, True)
        elif self.current is None or lap != previous['lap']:
            self.current = self._new_lap(lap, False)
        current = self.current
        if not clean_step:
            current['clean'] = False
            return
        correction_known = steer is not None and yaw is not None
        turning = correction_known and abs(steer) > steer_limit and abs(yaw) > YAW_MIN and speed > 15
        same = correction_known and steer * yaw > 0
        if turning:
            self.agree[same] += dt
        # The usual sign relation is learned from the car; the rare opposite is a correction.
        known = sum(self.agree) > 20 and max(self.agree) > 4 * min(self.agree)
        counter = turning and known and same != (self.agree[1] > self.agree[0])
        braking = brake > .2 and speed > 22
        if braking:
            current['brake_s'] += dt
            active = sample.get('abs_active')
            if active is None:
                current['abs_known'] = False
            elif active:
                current['abs_s'] += dt
        for kind, condition in (('brake', braking), ('power', throttle > .5 and brake < .05)):
            if condition and not correction_known:
                current[kind + '_known'] = False
            if counter and condition:
                before = current['run'][kind]
                current['run'][kind] = before + dt
                if before < MIN_RUN_S <= current['run'][kind]:
                    current[kind + '_events'] += 1
                    current[kind + '_sites'].append(pct)
            else:
                current['run'][kind] = 0.

    def view(self, sample=None):
        laps = list(self.laps)[-LAPS_NEEDED:]
        settings = dict(zip(('brake_bias', 'abs_setting', 'tc_setting'), self.settings or (None, None, None)))
        result = {'ready': len(laps) >= LAPS_NEEDED, 'laps': [lap['lap'] for lap in laps], 'settings': settings, 'advice': []}
        if not result['ready']:
            return result
        brake_events, power_events = [lap['brake_events'] for lap in laps], [lap['power_events'] for lap in laps]
        braking = sum(lap['brake_s'] for lap in laps)
        abs_duty = sum(lap['abs_s'] for lap in laps) / braking if braking and all(lap['abs_known'] for lap in laps) else None
        result.update(brake_events=brake_events, power_events=power_events,
                      abs_duty_pct=round(abs_duty * 100) if abs_duty is not None else None)
        def repeated(kind):
            # Require two separate corrections in matching track locations on
            # two laps. Scattered mistakes elsewhere must not become setup advice.
            for index, lap in enumerate(laps):
                for other in laps[index+1:]:
                    remaining=list(other[kind+'_sites'])
                    matched=0
                    for a in lap[kind+'_sites']:
                        distances=[min(abs(a-b),1-abs(a-b)) for b in remaining]
                        if distances and min(distances)<=.015:
                            remaining.pop(distances.index(min(distances)))
                            matched+=1
                    if matched >= REPEAT_EVENTS:
                        return True
            return False
        advice = result['advice']
        if finite(settings['brake_bias']) is not None and all(lap.get('brake_known', True) for lap in laps):
            if repeated('brake'):
                advice.append({'control': 'brake_bias', 'action': 'forward', 'call': 'control_bias_forward',
                               'text': f'제동 중 조향을 반대로 준 신호가 최근 {len(laps)}랩에서 랩당 {sum(brake_events) / len(laps):.0f}회입니다. 브레이크 바이어스를 앞쪽으로 한 단계 옮겨 시험해 보세요.'})
            else:
                advice.append({'control': 'brake_bias', 'action': 'hold', 'call': None, 'text': '제동 중 뒤가 흔들린 신호가 가끔 있었지만 반복되지는 않았습니다. 브레이크 바이어스는 유지하세요.'})
        if finite(settings['tc_setting']) is not None and all(lap.get('power_known', True) for lap in laps):
            if repeated('power'):
                advice.append({'control': 'tc', 'action': 'more', 'call': 'control_tc_more',
                               'text': f'가속 중 조향을 반대로 준 신호가 최근 {len(laps)}랩에서 랩당 {sum(power_events) / len(laps):.0f}회입니다. TC 개입을 한 단계 늘려 시험해 보세요.'})
            else:
                advice.append({'control': 'tc', 'action': 'hold', 'call': None, 'text': '가속 중 미끄러진 신호가 가끔 있었습니다. TC는 유지하세요.'})
        if finite(settings['abs_setting']) is not None and abs_duty is not None:
            if abs_duty >= .5:
                advice.append({'control': 'abs', 'action': 'driver', 'call': None,
                               'text': f'ABS가 제동 시간의 {abs_duty * 100:.0f}퍼센트에서 개입했습니다. 설정보다 먼저 최대 제동 압력을 조금 낮춰 보세요.'})
            elif repeated('brake') and abs_duty < .1:
                advice.append({'control': 'abs', 'action': 'more', 'call': 'control_abs_more',
                               'text': f'제동 중 뒤가 흔들리는데 ABS 개입은 제동 시간의 {abs_duty * 100:.0f}퍼센트뿐입니다. ABS 개입을 한 단계 늘려 시험해 보세요.'})
            else:
                advice.append({'control': 'abs', 'action': 'hold', 'call': None,
                               'text': f'ABS 개입은 제동 시간의 {abs_duty * 100:.0f}퍼센트입니다. ABS는 유지하세요.'})
        return result


def control_answer(view, text):
    """Spoken answer for 'what should I do with ABS / TC / brake bias'."""
    if not view or not view.get('ready'):
        return '아직 판단할 깨끗한 랩이 부족합니다. 같은 설정으로 옆 차량 없이 세 랩을 달리면 ABS, TC, 브레이크 바이어스를 살펴보겠습니다.'
    upper = text.upper()
    wanted = [control for control, words in (('abs', ('ABS', '에이비에스')), ('tc', ('TC', '티씨', '트랙션')),
                                              ('brake_bias', ('바이어스', 'BB', '비비', '밸런스'))) if any(word in upper for word in words)]
    chosen = [item for item in view['advice'] if not wanted or item['control'] in wanted]
    if not chosen:
        return '이 차량에서는 해당 설정 값을 읽지 못해 추천할 수 없습니다.'
    from radio_phrases import build
    short=[]
    for item in chosen:
        if item.get('call'):short.append(build(item['call'])['text'])
        elif item['action']=='driver':short.append('에이비에스 개입이 잦습니다. 최대 제동 압력을 조금 낮춰보세요.')
        else:short.append({'tc':'티씨','abs':'에이비에스','brake_bias':'브레이크 바이어스'}[item['control']]+'는 현재 설정을 유지하세요.')
    return ' '.join(short)
