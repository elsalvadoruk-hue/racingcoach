"""Per-corner lap comparison from live samples. Measured differences only; no LLM.

Corner segments come from the definitions saved by file analysis, so segment
numbers match the comparison screen. Each clean pass is compared with the
driver's own fastest clean pass of that segment in this app run. Passes with
a car alongside, a yellow, an incident or a sample gap are never a reference
and never produce advice.
"""
import math

BRAKE_ON, THROTTLE_ON, THROTTLE_FULL = .08, .20, .95
MIN_LOSS_S = .10
SPEED_NOTE_KPH, DISTANCE_NOTE_M = 3., 8.

def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def corner_number(identifier):
    digits = ''.join(ch for ch in str(identifier) if ch.isdigit())
    return int(digits) if digits else None


def compare(current, best, track_length_m):
    """Measured differences of one pass against the reference pass."""
    item = {'id': current['id'], 'number': corner_number(current['id']), 'call': current.get('call'), 'position_pct': current['apex_pct'],
            'delta_s': round(current['duration_s'] - best['duration_s'], 3),
            'min_speed_delta_kph': round((current['min_speed_mps'] - best['min_speed_mps']) * 3.6, 1),
            'brake_delta_m': None, 'throttle_delta_m': None, 'findings': [], 'cause': 'time'}
    if track_length_m:
        for key, source in (('brake_delta_m', 'brake_pct'), ('throttle_delta_m', 'throttle_pct')):
            if current.get(source) is not None and best.get(source) is not None:
                item[key] = round((current[source] - best[source]) * track_length_m, 1)
    # Order follows the corner: the earliest measured difference is the one to try first.
    notes = []
    if item['brake_delta_m'] is not None and item['brake_delta_m'] <= -DISTANCE_NOTE_M:
        notes.append(('brake_early', f"제동을 약 {abs(item['brake_delta_m']):.0f}미터 일찍 시작했습니다"))
    if item['min_speed_delta_kph'] <= -SPEED_NOTE_KPH:
        notes.append(('min_speed', f"최저속도가 {abs(item['min_speed_delta_kph']):.0f}킬로미터 낮았습니다"))
    if item['throttle_delta_m'] is not None and item['throttle_delta_m'] >= DISTANCE_NOTE_M:
        notes.append(('throttle_late', f"가속 시작이 약 {item['throttle_delta_m']:.0f}미터 늦었습니다"))
    if notes:
        item['cause'], item['findings'] = notes[0][0], [text for _, text in notes]
    return item


def describe(item):
    # A confirmed official number or name; otherwise the automatic segment number of the screen.
    label = item.get('call') or (f"{item['number']}번 구간" if item.get('number') else '해당 구간')
    text = f"{label}에서 구간 최고보다 {abs(item['delta_s']):.2f}초 {'느렸습니다' if item['delta_s'] > 0 else '빨랐습니다'}."
    if item['delta_s'] > 0 and item['findings']:
        text += ' ' + ' '.join(finding + '.' for finding in item['findings'])
    return text


# Timing of the call before a corner, in seconds before the braking point of the reference pass.
CALL_DEFAULT_S = 4.5       # the length assumed when the caller does not say how long the sentence is
CALL_LEAD_S = 1.5          # room for fetching the clip and the driver's own hold before braking
CALL_EARLY_S = 8.          # not earlier than this before the sentence would have to start
CALL_END_S = .5            # the sentence ends at least this long before the braking point, or it is not started
CALL_ACCELERATING = .9


class LiveCornerCoach:
    def __init__(self, lookup=None):
        self.lookup = lookup  # identity -> saved corner definitions, or None
        self.best = {}        # (car, track, layout) -> {corner id: reference pass}
        self.key = None
        self.reset()

    def reset(self):
        """Forget the lap in progress. References for this car and layout are kept."""
        self.previous = None
        self.active = None
        self.lap = {}
        self.brake_onset = None
        self.review = None
        self.last_pass = None
        self.last_measurement = None
        self.focus = None

    def _definitions(self, identity, now):
        key = (identity.get('car_id') or identity.get('car_path'), identity.get('track_id') or identity.get('track_path'),
               identity.get('track_config') or '')
        if key != self.key:
            self.key, self.definitions, self.checked_at = key, [], -math.inf
            self.reset()
        # File analysis may save definitions, and the driver may confirm turn numbers, while the car is on track.
        if self.lookup and now - self.checked_at >= (5 if not self.definitions else 30):
            self.checked_at = now
            found = self.lookup(identity) or self.definitions
            self.definitions = sorted((d for d in found if all(finite(d.get(k)) is not None for k in ('entry_pct', 'apex_pct', 'exit_pct'))
                                       and d['entry_pct'] < d['apex_pct'] < d['exit_pct']), key=lambda d: d['entry_pct'])
        return self.definitions

    def ingest(self, sample, identity, now, track_length_m=None):
        definitions = self._definitions(identity, now)
        pct, at, speed = (finite(sample.get(k)) for k in ('distance_pct', 'session_time', 'speed_mps'))
        lap = sample.get('lap_number')
        valid = (definitions and pct is not None and 0 <= pct < 1 and at is not None and speed is not None
                 and type(lap) in (int, float) and sample.get('on_track') is True
                 and sample.get('is_replay') is False and sample.get('on_pit_road') is False)
        previous, self.previous = self.previous, ({**sample, 'distance_pct': pct, 'session_time': at, 'speed_mps': speed} if valid else None)
        if not valid or previous is None:
            self.active, self.brake_onset = None, None
            self.last_measurement = None
            return
        dt = at - previous['session_time']
        rollover = lap == previous['lap_number'] + 1 and previous['distance_pct'] > .9 and pct < .1
        step = pct - previous['distance_pct'] + (1 if rollover else 0)
        if not 0 < dt <= .5 or not 0 <= step < .05 or (lap != previous['lap_number'] and not rollover):
            self.active, self.brake_onset = None, None
            self.last_measurement = None
            return
        start, end = previous['distance_pct'], previous['distance_pct'] + step  # end may pass 1.0 at the line

        def crossing(boundary):
            for value in (boundary, boundary + 1):
                if start < value <= end:
                    return previous['session_time'] + (value - start) / step * dt if step else at
            return None

        brake, old_brake = finite(sample.get('brake')), finite(previous.get('brake'))
        throttle = finite(sample.get('throttle'))
        if brake is not None and old_brake is not None and old_brake <= BRAKE_ON < brake:
            self.brake_onset = pct
        dirty = (sample.get('incidents') != previous.get('incidents') or int(sample.get('session_flags') or 0) & 0xC108
                 or sample.get('car_left_right') in (2, 3, 4, 5, 6) or brake is None or throttle is None)
        active = self.active
        if active:
            active['min_speed_mps'] = min(active['min_speed_mps'], speed)
            active['clean'] = active['clean'] and not dirty
            if active['brake_pct'] is None and brake is not None and brake > BRAKE_ON and pct <= active['apex_pct']:
                active['brake_pct'] = pct
                active['brake_observed'] = self.brake_onset == pct
            if pct >= active['apex_pct'] and throttle is not None:
                if active['throttle_pct'] is None and throttle > THROTTLE_ON:
                    active['throttle_pct'] = pct
            exit_at = crossing(active['exit_pct'])
            if exit_at is not None:
                active['duration_s'] = exit_at - active.pop('entry_at')
                active['exit_at'] = exit_at
                self.last_measurement = None
                self.active = None
                if active['clean'] and active['duration_s'] > 0:
                    self._finished(active, track_length_m)
        if rollover:
            self._lap_finished(int(previous['lap_number']), track_length_m)
        if self.active is None:
            for definition in definitions:
                entry_at = crossing(definition['entry_pct'])
                if entry_at is None:
                    continue
                # Braking that began shortly before the segment still belongs to this corner.
                onset = self.brake_onset if brake is not None and brake > BRAKE_ON else None
                reach = 150 / track_length_m if track_length_m else .03
                if onset is not None and not 0 <= (definition['entry_pct'] - onset) % 1 <= reach:
                    onset = None
                self.active = {'id': definition['id'], 'call': definition.get('call'), 'apex_pct': definition['apex_pct'], 'exit_pct': definition['exit_pct'],
                               'entry_pct': definition['entry_pct'], 'entry_at': entry_at, 'lap': int(lap),
                               'min_speed_mps': min(speed, previous['speed_mps']), 'brake_pct': onset,
                               'brake_observed': onset is not None,
                               'throttle_pct': None, 'clean': not dirty}
                break

    def _finished(self, corner_pass, track_length_m):
        self.last_measurement = dict(corner_pass)
        best = self.best.setdefault(self.key, {})
        reference = best.get(corner_pass['id'])
        self.lap[corner_pass['id']] = corner_pass
        self.last_pass = ({**compare(corner_pass, reference, track_length_m), 'lap': corner_pass['lap']} if reference
                          else {'id': corner_pass['id'], 'number': corner_number(corner_pass['id']), 'call': corner_pass.get('call'),
                                'lap': corner_pass['lap'], 'delta_s': None})

    def _lap_finished(self, lap_number, track_length_m):
        best = self.best.setdefault(self.key, {})
        passes, self.lap = self.lap, {}
        # Every pass is checked on its own, so a lap with a gap still compares its clean segments.
        compared = [compare(p, best[p['id']], track_length_m) for p in passes.values() if p['id'] in best]
        for corner_pass in passes.values():
            if corner_pass['id'] not in best or corner_pass['duration_s'] < best[corner_pass['id']]['duration_s']:
                best[corner_pass['id']] = corner_pass
        self.focus = None
        if not compared:
            return  # the first clean pass of a segment only supplies its reference
        losses = sorted((c for c in compared if c['delta_s'] >= MIN_LOSS_S), key=lambda c: -c['delta_s'])
        gains = sorted((c for c in compared if c['delta_s'] < 0), key=lambda c: c['delta_s'])
        self.review = {'lap': lap_number, 'compared': len(compared), 'losses': losses[:3], 'loss_count': len(losses),
                       'best_gain': gains[0] if gains else None,
                       'total_loss_s': round(sum(c['delta_s'] for c in losses), 2)}
        if losses:
            worst, source = losses[0], passes[losses[0]['id']]
            reference = best[worst['id']]
            until = reference.get('brake_pct') if reference.get('brake_pct') is not None else source['entry_pct']
            self.focus = {'id': worst['id'], 'number': worst['number'], 'call': worst.get('call'), 'cause': worst['cause'], 'until_pct': until, 'loss_s': worst['delta_s'],
                          'text': f'{lap_number}랩 ' + describe(worst), 'announced': False}

    def reminder(self, sample, lap_seconds, track_length_m=None, speech_s=(CALL_DEFAULT_S,)):
        """The one corner to mention, once, early enough for the whole sentence to end before its braking point.

        speech_s lists how long each wording takes, longest first; 'fits' is the first one there is room for.
        The time to the braking point comes from the speed now (a straight is driven much faster than the lap average).
        """
        focus, pct, pace = self.focus, finite(sample.get('distance_pct')), finite(lap_seconds)
        if not focus or focus['announced'] or pct is None or pace is None or not 20 < pace < 1800:
            return None
        speed, length = finite(sample.get('speed_mps')), finite(track_length_m)
        # Still accelerating down the straight: the car arrives a little sooner than this speed says.
        per_s = speed / length / CALL_ACCELERATING if length and speed is not None and speed > 10 else 1 / pace
        seconds = ((focus['until_pct'] - pct) % 1) / per_s
        for index, needed in enumerate(speech_s):
            if needed + CALL_LEAD_S <= seconds <= needed + CALL_EARLY_S:
                focus['announced'] = True
                return {**focus, 'fits': index, 'margin_pct': (needed + CALL_END_S) * per_s}
        return None

    def view(self):
        best = self.best.get(self.key, {}) if self.key else {}
        definitions = getattr(self, 'definitions', [])
        return {'available': bool(definitions),
                'reason': None if definitions else '이 차량·트랙의 구간 정의가 없습니다. 주행 파일을 한 번 분석하면 다음 주행부터 구간별로 비교합니다.',
                'corner_count': len(definitions), 'reference_corners': len(best),
                'reference_basis': '이번 실행 중 깨끗하게 통과한 구간 최고 기록',
                'last_lap': self.review, 'last_corner': self.last_pass,
                'focus': {k: v for k, v in self.focus.items() if k != 'announced'} if self.focus else None}


def call_seconds(text):
    """Budget at 1.0x; the original estimate was measured with clips played at 1.15x."""
    return (len(''.join((text or '').split())) / 5.5 + 1.) * 1.15


def lap_answer(view):
    if not view or not view.get('available'):
        return None
    review = view.get('last_lap')
    if not review:
        return '아직 구간별로 비교할 완료 랩이 없습니다. 깨끗하게 통과한 기록이 구간마다 한 번 쌓인 뒤 다음 랩부터 비교합니다.'
    if not review['losses']:
        text = f"{review['lap']}랩은 비교한 {review['compared']}개 구간 모두 구간 최고와 0.1초 이내였습니다."
    else:
        parts = [f"{review['lap']}랩은 비교한 {review['compared']}개 구간 중 " + describe(review['losses'][0])]
        if len(review['losses']) > 1:
            parts.append('다음은 ' + ', '.join(f"{c.get('call') or str(c['number']) + '번 구간'} {c['delta_s']:.2f}초" for c in review['losses'][1:]) + ' 손실입니다.')
        parts.append(f"0.1초 이상 잃은 구간의 합은 {review['total_loss_s']:.2f}초입니다.")
        text = ' '.join(parts)
    gain = review.get('best_gain')
    if gain:
        text += f" {gain.get('call') or str(gain['number']) + '번 구간'}은 구간 최고를 {abs(gain['delta_s']):.2f}초 줄였습니다."
    return text + ' 구간 번호는 코너 비교 화면의 자동 구간 번호입니다.'


def corner_answer(view):
    if not view or not view.get('available'):
        return None
    last = view.get('last_corner')
    if not last:
        return '아직 깨끗하게 통과한 구간 기록이 없습니다.'
    if last.get('delta_s') is None:
        return f"방금 {last.get('call') or str(last['number']) + '번 구간'}은 처음 기록한 통과라 비교할 구간 최고가 아직 없습니다."
    text = '방금 ' + describe(last)
    if last['delta_s'] > 0 and not last['findings']:
        text += ' 제동 시작·최저속도·가속 시작 위치는 구간 최고와 큰 차이가 없었습니다.'
    return text
