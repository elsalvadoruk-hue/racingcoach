"""Independent, read-only formation cards. Simulator facts never depend on voice or analysis."""
from copy import deepcopy
from functools import lru_cache
import math
import statistics
import unicodedata
from collections import deque
from live_formation import strength_of_field, rating_goal
from live_wheels import wheel_view

# The adopted idea numbers are stable preference IDs. Unadopted ideas are absent.
FORMATION_ITEMS = ('1', '6', '8', '9', '10', '14', '15', '16', '17', '20', '21', '22', '23', '24', '25', '27')
FORMATION_DEFAULT = ['8', '9', '10', '14', '15', '16', '17', '20', '21', '22', '23', '24', '25', '27']
NAMES = {'1': '공식 출발 순위', '6': '공식 정렬 안내', '8': '현재 연료', '9': '현재 날씨',
         '10': '바퀴별 온도', '14': '지난 경기 기록', '15': '클래스 필드', '16': '그리드 앞뒤 차량',
         '17': '해당 경기 퀄리', '20': '경기 규정', '21': '지난 관측 구간', '22': '가상 에너지',
         '23': 'iRating 상승 기준 추정', '24': '내 유효 랩 기록', '25': '출발부터 결과까지', '27': '내 트랙 메모'}
REVIEW = {'14', '21', '24', '25', '27'}


def number(v):
    return float(v) if type(v) in (int, float) and math.isfinite(v) else None


def rank(v, zero=False):
    return int(v) + int(zero) if type(v) is int and 0 <= v < 1024 and (zero or v > 0) else None


def official_context(sdk, names, session, game, cars, player, state):
    """Use native session tables, with their own index bases. Never use live positions as a grid.

    iRacing's pre-start Race ResultsPositions is the native starting classification;
    QualifyResultsInfo remains explicitly qualifying, even if its order differs.
    LMU's connected schema has no official starting table or follow-car ID.
    """
    out = {'official_grid': None, 'qualifying': None, 'formation_assignment': None}
    if game != 'iracing':
        return out
    rows = session.get('ResultsPositions') or []
    if state in (1, 2, 3) and str(session.get('SessionType') or '').lower() == 'race':
        grid = []
        for row in rows[:128]:
            if not isinstance(row, dict) or row.get('LapsComplete') != 0:
                continue
            pos, cp, idx = rank(row.get('Position')), rank(row.get('ClassPosition'), True), row.get('CarIdx')
            if pos and type(idx) is int and any(c['idx'] == idx and not c.get('is_pace_car') for c in cars):
                grid.append({'idx': idx, 'overall': pos, 'class_position': cp})
        if grid and len({r['idx'] for r in grid}) == len(grid) and len({r['overall'] for r in grid}) == len(grid):
            own = next((r for r in grid if r['idx'] == player), None)
            if own:
                out['official_grid'] = {**own, 'rows': grid, 'source': 'IRACING_PRESTART_CLASSIFICATION', 'basis': 'CLASS'}
    try:
        qualifying = (sdk['QualifyResultsInfo'] or {}).get('Results') or []
    except (KeyError, TypeError):
        qualifying = []
    qrows = []
    for row in qualifying[:128]:
        if not isinstance(row, dict):
            continue
        pos, cp = rank(row.get('Position'), True), rank(row.get('ClassPosition'), True)
        t = number(row.get('FastestTime'))
        if pos and type(row.get('CarIdx')) is int:
            qrows.append({'idx': row['CarIdx'], 'overall': pos, 'class_position': cp,
                          'lap_s': t if t is not None and t > 0 else None})
    if len({r['idx'] for r in qrows}) == len(qrows):
        q = next((r for r in qrows if r['idx'] == player), None)
        if q:
            out['qualifying'] = {**q, 'rows': qrows, 'source': 'IRACING_QUALIFY_RESULTS'}
    if {'CarIdxPaceLine', 'CarIdxPaceRow'}.issubset(names) and type(player) is int:
        lines, rows = sdk['CarIdxPaceLine'], sdk['CarIdxPaceRow']
        if lines is not None and rows is not None and 0 <= player < min(len(lines), len(rows)):
            line, row = lines[player], rows[player]
            if type(line) is int and type(row) is int and line >= 0 and row >= 0:
                follows = [i for i in range(min(len(lines), len(rows))) if lines[i] == line and rows[i] == row - 1]
                out['formation_assignment'] = {'line': line, 'row': row,
                                               'follow_idx': follows[0] if len(follows) == 1 else None,
                                               'source': 'IRACING_PACE_LINE_ROW'}
    return out


class FormationContext:
    def __init__(self):
        self.run = None
        self.data = {}

    def ingest(self, race, run):
        if run != self.run:
            self.run, self.data = run, {}
        for key in ('official_grid', 'qualifying', 'formation_assignment'):
            value = race.get(key)
            if value is not None and value != self.data.get(key):
                self.data[key] = deepcopy(value)
        return self.data


class FormationLapRecords:
    """Attach conditions only to fully observed, clean laps that the radio accepted."""
    def __init__(self):
        self.run = self.lap = self.current = self.previous = None
        self.completed = {}
        self.records = deque(maxlen=100)

    def ingest(self, sample, run, game, accepted):
        if run != self.run:
            self.__init__()
            self.run = run
        lap, at = sample.get('lap_number'), number(sample.get('session_time'))
        if lap != self.lap:
            if self.current:
                before_pct = number((self.previous or {}).get('distance_pct'))
                pct = number(sample.get('distance_pct'))
                crossed = (type(lap) in (int, float) and lap == self.lap + 1 and before_pct is not None
                           and pct is not None and before_pct > .85 and 0 <= pct < .15
                           and sample.get('incidents') == (self.previous or {}).get('incidents'))
                self.current['clean'] = self.current['clean'] and crossed
                self.completed[self.lap] = self.current
            self.current = {'clean': self.lap is not None and lap == self.lap + 1,
                            'fuel': [], 'temp': [], 'wet': sample.get('track_wetness'),
                            'compound': sample.get('tire_compound'), 'conditions_known': True}
            self.lap = lap
        current = self.current
        if current is None:
            return
        invalid = (sample.get('on_track') is not True or sample.get('on_pit_road') is not False
                   or sample.get('is_replay') is not False or sample.get('session_state') != 4
                   or int(sample.get('session_flags') or 0) & 0xC108
                   or sample.get('track_wetness') != current['wet'] or sample.get('tire_compound') != current['compound'])
        if self.previous:
            old_at = number(self.previous.get('session_time'))
            invalid = invalid or old_at is None or at is None or not 0 < at - old_at <= .5
            invalid = invalid or sample.get('incidents') != self.previous.get('incidents')
        if game == 'lmu':
            invalid = invalid or sample.get('lmu_count_lap_flag') != 2 or sample.get('lmu_lap_invalidated') is not False
        else:
            invalid = invalid or number(sample.get('incidents')) is None
        current['clean'] = current['clean'] and not invalid
        current['conditions_known'] = (current['conditions_known'] and number(sample.get('fuel_l')) is not None
                                       and number(sample.get('track_temp_c')) is not None and current['wet'] is not None)
        for key, source in (('fuel', 'fuel_l'), ('temp', 'track_temp_c')):
            value = number(sample.get(source))
            if value is not None:
                # Keep only range endpoints; do not accumulate 30 Hz samples.
                values = current[key] + [value]
                current[key] = [min(values), max(values)]
        self.previous = dict(sample)
        for record in accepted:
            key = record.get('lap')
            observed = self.completed.pop(key, None)
            if observed and observed['clean']:
                self.records.append({**record, 'fuel_range': observed['fuel'], 'temp_range': observed['temp'],
                                     'wet': observed['wet'], 'compound': observed['compound'],
                                     'conditions_known': observed['conditions_known']})
        self.completed = {k: v for k, v in self.completed.items() if type(k) in (int, float) and lap is not None and lap - k <= 3}


def pages(text, cells=72):
    """Lossless pages for native text; do not summarise a note or remove negation."""
    result, part, used = [], '', 0
    for ch in str(text):
        width = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if part and used + width > cells:
            result.append(part)
            part, used = '', 0
        part += ch
        used += width
    return result + ([part] if part else [])


@lru_cache(maxsize=8)
def _note_pages(text):
    return tuple(pages(text))


def note_pages(text):
    # Retain at most eight short notes; very long notes are never cached.
    # Return a fresh list so callers cannot alter later renderings.
    return list(_note_pages(text)) if len(text) <= 8192 else pages(text)


def lap_text(v):
    v = number(v)
    return f'{int(v // 60)}:{v % 60:06.3f}' if v is not None and 0 < v < 1800 else '확인 중'


def build_formation(sample, race, identity, context=None, archive=None, energy=None):
    context, archive = context or {}, archive or {}
    game = identity.get('game') or race.get('game') or 'iracing'
    active = (str(race.get('session_type') or '').lower() == 'race' and sample.get('session_state') in (1, 2, 3)
              and sample.get('on_track') is True and sample.get('on_pit_road') is False
              and sample.get('is_replay') is False)
    cards = {}
    def card(key, text=None, detail=None, source=None, supported=True, extra=None):
        cards[key] = {'title': NAMES[key], 'text': text or '현재 자료 확인 중', 'metric': text or '확인 중',
                      'detail': detail or '', 'available': text is not None, 'supported': supported,
                      'source': source, 'group': '지난번 돌아보기' if key in REVIEW else '출발 준비', **(extra or {})}
    cars = [c for c in race.get('cars') or [] if not c.get('is_pace_car')]
    own = next((c for c in cars if c.get('idx') == race.get('player_idx')), {})
    mates = [c for c in cars if own.get('class_id') is not None and c.get('class_id') == own['class_id']]
    grid, quali = context.get('official_grid') or {}, context.get('qualifying') or {}
    cp, op = grid.get('class_position'), grid.get('overall')
    grid_text = (f'출발 클래스 {cp}위 · 전체 {op}위' if cp and op else f'출발 전체 {op}위' if op else '출발 순위 확인 중')
    card('1', grid_text, f'클래스 {len(mates)}대 · 전체 {len(cars)}대' if cars else '참가 규모 확인 중', grid.get('source'))
    assign = context.get('formation_assignment') or {}
    followed = next((c for c in cars if c.get('idx') == assign.get('follow_idx')), {})
    assignment = f"공식 열 {assign['line']} · 행 {assign['row']}" if assign else None
    if followed:
        assignment += f" · 앞 #{followed.get('number') or followed['idx']}"
    card('6', assignment, '게임이 제공한 정렬 번호', assign.get('source'), game == 'iracing')
    fuel = number(sample.get('fuel_l'))
    card('8', f'{fuel:.1f} L' if fuel is not None and fuel >= 0 else None, '현재 관측', 'TELEMETRY')
    weather = [f"{label} {v:.1f}°C" for key, label in (('track_temp_c', '노면'), ('air_temp_c', '기온'))
               if (v := number(sample.get(key))) is not None]
    if number(sample.get('track_wetness')) is not None:
        weather.append(f"젖음 단계 {sample['track_wetness']}")
    if type(sample.get('declared_wet')) is bool:
        weather.append('웨트 선언' if sample['declared_wet'] else '웨트 선언 아님')
    card('9', ' · '.join(weather) or None, '현재 관측과 게임 선언 · 예보 아님', 'TELEMETRY_AND_DECLARATION')
    wheels = wheel_view(sample, game)
    wheel_pages = []
    for row in wheels['wheels']:
        ts = '/'.join(f'{v:.0f}' if v is not None else '?' for v in row['temperature_c'])
        brake = row['brake_temp_c']
        state = ' · 펑크' if row['flat'] is True else ''
        state += ' · 바퀴 탈락' if row['detached'] is True else ''
        wheel_pages.append(f"{row['position']} 타이어 {ts}°C · 브레이크 {brake:.0f}°C{state}" if brake is not None else f"{row['position']} 타이어 {ts}°C{state}")
    card('10', '\n'.join(wheel_pages) if wheels['available'] else None, 'LMU 실측 · 최적 온도 판정 없음', 'LMU_TELEMETRY', game == 'lmu', {'pages': wheel_pages})
    last = archive.get('last') or {}
    card('14', f"지난 경기 베스트 {lap_text(last.get('best_lap_s'))}" if last else None,
         f"기록 날짜 {last.get('at_label', '확인 중')} · 경기 기록", 'RACE_HISTORY')
    complete = (game == 'iracing' and len(mates) >= 2 and not any(c.get('is_ai') for c in mates)
                and all(number(c.get('irating')) is not None and 0 < c['irating'] < 20000 for c in mates))
    sof = strength_of_field(c['irating'] for c in mates) if complete else None
    rating_rank, target = rating_goal(own.get('irating'), [c['irating'] for c in mates if c is not own]) if complete else (None, None)
    if complete:
        rating_rank = 1 + sum(c['irating'] > own['irating'] for c in mates if c is not own)
    card('15', f'클래스 SoF {sof:.0f} · 내 iRating {rating_rank}위' if sof is not None and rating_rank else None,
         f'같은 클래스 {len(mates)}대 · 유효 iRating 전체 확인 필요', 'IRACING_RATINGS', game == 'iracing')
    neighbors = []
    for delta, label in ((-1, '앞 출발'), (1, '뒤 출발')):
        row = next((r for r in grid.get('rows') or [] if op and r.get('overall') == op + delta), {})
        car = next((c for c in cars if c.get('idx') == row.get('idx')), {})
        if car:
            text = f"{label} #{car.get('number') or '?'} {car.get('car_name') or car.get('class_name') or '차종 미확인'}"
            q = next((r for r in quali.get('rows') or [] if r['idx'] == car['idx']), {})
            if number(q.get('lap_s')) and number(quali.get('lap_s')):
                text += f" · 퀄리 차이 {q['lap_s'] - quali['lap_s']:+.3f}초"
            neighbors.append(text)
    card('16', '\n'.join(neighbors) or None, '공식 그리드의 바로 앞뒤 출발 슬롯', grid.get('source'), game == 'iracing')
    card('17', f"퀄리 클래스 {quali['class_position']}위 · {lap_text(quali.get('lap_s'))}" if quali.get('class_position') else None,
         '해당 경기의 퀄리 · 레이스 베스트와 별도', quali.get('source'), game == 'iracing')
    rules = []
    for key, label in (('incident_limit', '인시던트 제한'), ('fast_repairs_limit', '빠른 수리 제한'), ('mandatory_pit_stops', '의무 피트')):
        if number(race.get(key)) is not None:
            rules.append(f'{label} {race[key]:g}')
    card('20', ' · '.join(rules) or None, '입력이 없는 규정은 미확인', 'SIM_SESSION_OPTIONS')
    analysed = archive.get('analysed') or {}
    losses = analysed.get('losses') or []
    card('21', ' · '.join(str(x) for x in losses[:3]) or None, '지난 분석의 관측 구간 · 원인 판정 없음', 'SAVED_ANALYSIS')
    ep = number((energy or {}).get('energy_pct'))
    card('22', f'{ep:.1f}%' if ep is not None else None, '현재 유효 잔량', 'LMU_VIRTUAL_ENERGY', game == 'lmu')
    card('23', f'{target}위 이내 마무리' if target else None, 'iRating 상승 기준 추정 · 공식 증감 보장 없음', 'IRATING_ESTIMATE', game == 'iracing')
    records = archive.get('records') or {}
    recent = []
    fuel_now, temp_now = number(sample.get('fuel_l')), number(sample.get('track_temp_c'))
    for row in (records.get('recent') or [])[-20:]:
        if not isinstance(row, dict):
            continue
        fr, tr = row.get('fuel_range') or [], row.get('temp_range') or []
        suitable = (row.get('conditions_known') is True and fuel_now is not None and temp_now is not None and len(fr) == len(tr) == 2
                    and max(abs(fuel_now - x) for x in fr) <= 10 and max(abs(temp_now - x) for x in tr) <= 5
                    and sample.get('track_wetness') is not None and row.get('wet') == sample['track_wetness']
                    and row.get('compound') == sample.get('tire_compound'))
        value = number(row.get('time'))
        if suitable and value is not None and 20 < value < 1800:
            recent.append(value)
    personal = records.get('best_s')
    card('24', f"개인 베스트 {lap_text(personal)} · 최근 중앙값 {lap_text(statistics.median(recent))}" if personal and recent else None,
         f"같은 조합 개인 베스트 · 최근 중앙값은 조건이 맞는 유효 랩 {len(recent)}개", 'VALID_LAP_RECORDS')
    start, finish = last.get('start_position'), last.get('class_position')
    status = last.get('status') or '상태 미확인'
    provisional = '잠정' if last.get('provisional') is True else '확정' if last.get('provisional') is False else '확정 여부 미확인'
    card('25', f"클래스 출발 {int(start)}위 → 결과 {int(finish)}위" if start and finish and last.get('start_source') else None,
         f"{status} · {provisional} · 추월 수와 다름", 'RACE_HISTORY')
    notes = archive.get('notes') or []
    note = notes[0] if notes else {}
    card('27', note.get('text'), f"{note.get('at_label', '')} · 직접 작성한 메모", 'USER_NOTE', extra={'note_id': note.get('id')})
    for item in cards.values():
        if not item['supported']:
            item.update(available=False, text='이 게임의 연결 입력에서 지원하지 않습니다.', metric='미지원')
        item['pages'] = note_pages(item['text']) if item is cards['27'] else [part for text in item.get('pages') or [item['text']] for part in pages(text)]
    return {'active': active, 'grid': cards['1'], 'cards': cards, 'game': game,
            'source_time': sample.get('session_time')}
