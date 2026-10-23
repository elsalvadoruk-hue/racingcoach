"""Something to listen to on the formation lap: the race laid out by the engineers, a topic at a time.

Only while the session is in its parade laps and the car is rolling on track. A series without a
formation lap never reaches that state with a moving car, so nothing is said there.

The driver chooses how many calls there are (spotter settings). They are spread evenly over the
lap with quiet in between, and the last one, the send-off, comes at about four fifths of the lap,
before the start itself needs the radio. With fewer calls than topics, the race itself always
comes first and the others are drawn differently from race to race.

Every sentence is a whole clip of its own (radio_phrases 'form_*'), several are joined into one
call, and each has a few wordings. Sentences with a number are only said when that number was read
from the sim; what is not known is left out.
"""
import json
import math
import random
import threading
import time
from pathlib import Path

# (name, role, sentences in order). A sentence is its phrase key, and the fact it needs (None: needs nothing).
TOPICS = [
    ('race', 'race', [('form_hello', None), ('form_length_min', 'race_min'), ('form_length_laps', 'race_laps'),
                      ('form_grid', 'class_position'), ('form_field', 'class_cars')]),
    # The field, always the second call: by then every car is rolling, so the cars that never joined are known
    # (on the grid the strength of field was told before the field was complete). Strength of field of the whole
    # grid, or of the own class with several classes; where the iRating ranks the driver, and the place to beat.
    ('field', 'race', [('form_sof', 'sof'), ('form_sof_class', 'class_sof'),
                       ('form_rating_rank', 'rating_rank'), ('form_rating_target', 'rating_target'), ('form_rating_win', 'rating_win')]),
    # The last time at this track with this car: what it was like, and where the time went.
    # The second to the tenth time are counted; from the eleventh on it is simply a track the driver knows well.
    ('history', 'race', [('form_hist_first', 'hist_first'), ('form_hist_nth', 'hist_nth'), ('form_hist_familiar', 'hist_familiar'),
                         ('form_hist_best', 'hist_known'), ('form_lap', 'hist_lap'),
                         ('form_hist_temp_lower', 'hist_temp_lower'), ('form_hist_temp_higher', 'hist_temp_higher'), ('form_hist_temp_same', 'hist_temp_same')]),
    ('history_race', 'race', [('form_hist_pos', 'hist_pos'), ('form_hist_loss', 'hist_loss_turn'), ('form_hist_loss_also', 'hist_loss_turn2')]),
    ('fuel', 'strategy', [('form_fuel', 'fuel_l'), ('form_pit_speed', 'pit_speed_kph'), ('form_pit_loss', 'pit_transit_s')]),
    ('start', 'spotter', [('form_rolling', None), ('form_hold_station', None), ('form_first_corner', None)]),
    ('tyres', 'coach', [('form_cold_tyres', None), ('form_cold_brakes', None)]),
    ('weather', 'race', [('form_track_temp', 'track_temp_c'), ('form_air_temp', 'air_temp_c'), ('form_wet', 'wet'), ('form_dry', 'dry')]),
    ('rules', 'race', [('form_incident_limit', 'incident_limit'), ('form_multiclass', 'multiclass')]),
    ('radio', 'race', [('form_ask', None), ('form_calls', None)]),
]
CLOSING = ('closing', 'race', [('form_ready', None), ('form_good_luck', None)])
VALUE_NAMES = {'race_min': 'min', 'race_laps': 'laps', 'class_position': 'pos', 'class_cars': 'count', 'fuel_l': 'liters',
               'pit_speed_kph': 'kph', 'pit_transit_s': 'sec', 'track_temp_c': 'temp', 'air_temp_c': 'temp', 'incident_limit': 'points',
               'hist_nth': 'nth', 'hist_lap': 'lap', 'hist_pos': 'pos', 'hist_temp_lower': 'temp', 'hist_temp_higher': 'temp',
               'hist_loss_turn': 'turn', 'hist_loss_turn2': 'turn', 'sof': 'rating', 'class_sof': 'rating',
               'rating_rank': 'pos', 'rating_target': 'pos'}
ALWAYS = ('field',)          # said whenever there are calls enough for it, right after the race itself
DEFAULT_CALLS, MAX_CALLS = 5, len(TOPICS) + 1


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def grid_topics(position, *, multiclass=False):
    """Front-row calls for the overall grid, or the own class in a multiclass race."""
    if position not in (1, 2):
        return TOPICS
    prefix = 'form_class_' if multiclass else 'form_'
    greeting = prefix + ('pole' if position == 1 else 'front_row')
    station = prefix + ('pole_station' if position == 1 else 'front_station')
    return [(name, role, [(greeting if key == 'form_hello' else
                          station if key == 'form_hold_station' else
                          'form_front_rolling' if key == 'form_rolling' else key, need)
                         for key, need in sentences]) for name, role, sentences in TOPICS]


def usable(topic, known):
    """The sentences of a topic that can be said with what is known, or [] when it is not worth a call."""
    name, role, sentences = topic
    chosen = [(key, {VALUE_NAMES[need]: known[need]} if need in VALUE_NAMES else {}) for key, need in sentences if need is None or need in known]
    # A topic built on measured facts is skipped when none of them is known: its opening words alone say nothing.
    measured = [need for _, need in sentences if need is not None]
    return [] if measured and not any(need in known for need in measured) else chosen


SOF_MIN_CARS = 2


def strength_of_field(ratings):
    """iRacing's strength of field: 1600/ln2 * ln(n / sum(2^(-iR/1600))), over the cars with a rating.

    None with fewer than two rated cars: one driver is not a field, and an unrated grid (AI, the pace car)
    says nothing about it.
    """
    rated = [value for value in (finite(r) for r in ratings) if value is not None and 0 < value < 20000]
    if len(rated) < SOF_MIN_CARS:
        return None
    return 1600 / math.log(2) * math.log(len(rated) / sum(2 ** (-value / 1600) for value in rated))


def win_chance(mine, theirs):
    """The chance to finish ahead of a driver, from the two iRatings, as iRacing's rating calculators have it."""
    base = 1600 / math.log(2)
    a, b = math.exp(-mine / base), math.exp(-theirs / base)
    return (1 - a) * b / ((1 - b) * a + (1 - a) * b)


def rating_goal(mine, others):
    """(rank by iRating in the class, the lowest place that still gains iRating) or (None, None).

    iRating moves with the drivers beaten against the number expected from the ratings: finishing at place p
    beats n - p of the field, so it goes up when n - p is above the expected count. An estimate: iRacing does
    not publish its formula, and the field can still change before the start.
    """
    mine = finite(mine)
    rated = [value for value in (finite(r) for r in others) if value is not None and 0 < value < 20000]
    if mine is None or not 0 < mine < 20000 or len(rated) < 2:
        return None, None
    field = len(rated) + 1
    rank = 1 + sum(value > mine for value in rated)
    expected = sum(win_chance(mine, value) for value in rated)
    target = math.ceil(field - expected) - 1           # the largest place p with field - p > expected
    return rank, (target if 1 <= target <= field else None)


class RaceHistory:
    """How each race ended, per car and track, in one small JSON file beside the database.

    Written when the car takes the chequered flag: the class place, the best lap and the track
    temperature. The formation lap of the next race there looks back at it.
    """
    VERSION, KEEP = 1, 20

    def __init__(self, path=None):
        self.path, self.lock, self.data = Path(path) if path else None, threading.Lock(), None

    def _load(self):
        if self.data is None:
            try:
                found = json.loads(self.path.read_text(encoding='utf-8')) if self.path else {}
                self.data = found if found.get('version') == self.VERSION else {}
            except (OSError, ValueError):
                self.data = {}
            self.data.setdefault('version', self.VERSION)
            self.data.setdefault('races', {})
        return self.data

    @staticmethod
    def key(identity):
        car, track = str(identity.get('car_id') or '').strip(), str(identity.get('track_id') or '').strip()
        return f"{car}|{track}|{identity.get('track_config') or ''}" if car and track else None

    def add(self, identity, result):
        key = self.key(identity or {})
        if not key:
            return False
        entry = {name: finite(result.get(name)) for name in ('class_position', 'class_cars', 'best_lap_s', 'track_temp_c')}
        entry['at'] = finite(result.get('at')) or time.time()
        with self.lock:
            races = self._load()['races'].setdefault(key, [])
            races.append(entry)
            del races[:-self.KEEP]
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
                    temporary.replace(self.path)
                except OSError:
                    pass
        return True

    def last(self, identity):
        key = self.key(identity or {})
        with self.lock:
            races = self._load()['races'].get(key) or [] if key else []
            return dict(races[-1]) if races else None

    def count(self, identity):
        key = self.key(identity or {})
        with self.lock:
            return len(self._load()['races'].get(key) or []) if key else 0


FAMILIAR_FROM = 11      # from this time at a track on, it is no longer counted


def history_facts(race, analysed, track_temp_c, races=0, known=True):
    """What can be said about the last time here, from the last race (race) and the last analysed session (analysed).

    races is how many races were kept for this car and track; with the analysed sessions it tells how well
    the track is known. known=False means the records could not be read, and then nothing is claimed.
    """
    race, analysed = race or {}, analysed or {}
    visits = max(int(races or 0), int(finite(analysed.get('sessions')) or 0), 1 if race or analysed.get('best_lap_s') else 0)
    # The newer of the two knows the lap and the temperature of "last time"; only a race knows the place.
    newer = race if (finite(race.get('at')) or 0) >= (finite(analysed.get('at')) or 0) and finite(race.get('best_lap_s')) else analysed
    lap, then, now = finite(newer.get('best_lap_s')), finite(newer.get('track_temp_c')), finite(track_temp_c)
    place = finite(race.get('class_position'))
    difference = round(now - then) if lap and then is not None and now is not None else None
    turns = [turn for turn in analysed.get('losses') or [] if turn][:2]
    return {
        'hist_first': known and visits == 0,
        'hist_nth': visits + 1 if 1 <= visits < FAMILIAR_FROM - 1 else None, 'hist_familiar': visits >= FAMILIAR_FROM - 1,
        'hist_known': bool(lap and 20 < lap < 1800), 'hist_lap': lap if lap and 20 < lap < 1800 else None,
        'hist_pos': place if place and place > 0 and newer is race else None,
        'hist_temp_lower': -difference if difference is not None and difference <= -2 else None,
        'hist_temp_higher': difference if difference is not None and difference >= 2 else None,
        'hist_temp_same': difference is not None and abs(difference) < 2,
        'hist_loss_turn': turns[0] if turns else None, 'hist_loss_turn2': turns[1] if len(turns) > 1 else None,
    }


class FormationBrief:
    ROLLING_MPS = 5.          # slower than this the car is waiting on the grid, not on its formation lap
    FIRST_AT, LAST_AT = .02, .78   # of the lap driven: where the first call and the send-off belong
    PAUSE_S = 6.              # at least this much quiet after a call, however the lap is paced
    SENTENCE_S = 3.2          # how long a sentence is taken to last

    def __init__(self, calls=DEFAULT_CALLS):
        self.calls = calls
        self.reset()

    def reset(self):
        self.run = None
        self.plan = None          # the topics of this formation lap, in the order they are said
        self.said = 0
        self.next_at = 0.
        self.driven = 0.
        self.last_pct = None

    def planned(self, known, run_id):
        """This lap's calls: the race first, the send-off last, the rest drawn anew for each race."""
        count = max(0, min(MAX_CALLS, int(self.calls)))
        multiclass = bool(known.get('multiclass'))
        position = known.get('class_position' if multiclass else 'grid_position')
        ready = [topic for topic in grid_topics(position, multiclass=multiclass) if usable(topic, known)]
        if count <= 0:
            return []
        if count == 1:
            return ready[:1] or [CLOSING]
        first, others = ready[:1], ready[1:]
        room = max(0, count - 1 - len(first))
        required = (*ALWAYS, 'start') if position in (1, 2) else ALWAYS
        kept = [topic for topic in others if topic[0] in required][:room]
        others = [topic for topic in others if topic not in kept]
        wanted = room - len(kept)
        picked = set(random.Random(str(run_id)).sample(range(len(others)), min(wanted, len(others))))
        return first + kept + [topic for index, topic in enumerate(others) if index in picked] + [CLOSING]

    def step(self, sample, facts, run_id, now):
        """The next call to say as (name, role, [(phrase key, values)]), or None."""
        if run_id != self.run:
            self.reset()
            self.run = run_id
        speed, pct = finite(sample.get('speed_mps')), finite(sample.get('distance_pct'))
        rolling = (sample.get('session_state') == 3 and sample.get('on_pit_road') is False and sample.get('on_track') is True
                   and sample.get('is_replay') is False and speed is not None and speed >= self.ROLLING_MPS and pct is not None)
        if not rolling:
            self.last_pct = None
            return None
        if self.last_pct is not None:
            self.driven += (pct - self.last_pct) % 1 if (pct - self.last_pct) % 1 < .2 else 0.
        self.last_pct = pct
        known = {name: value for name, value in facts.items() if value is not None and value is not False}
        if self.plan is None:
            self.plan = self.planned(known, run_id)
        if self.said >= len(self.plan) or now < self.next_at:
            return None
        # Evenly over the lap: call k of n belongs at FIRST_AT + k/(n-1) of the way to LAST_AT.
        last = len(self.plan) - 1
        due = self.LAST_AT if last == 0 and self.plan[0] is CLOSING else self.FIRST_AT + (self.LAST_AT - self.FIRST_AT) * (self.said / last if last else 0)
        if self.driven < due:
            return None
        topic = self.plan[self.said]
        self.said += 1
        chosen = usable(topic, known)
        if not chosen:
            return None
        self.next_at = now + len(chosen) * self.SENTENCE_S + self.PAUSE_S
        return topic[0], topic[1], chosen
