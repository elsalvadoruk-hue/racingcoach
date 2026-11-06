"""iRacing's in-sim Weather panel forecast, read by layout; and the track temperature it implies.

The panel shows the current time and the next quarter hours in columns: time, sky icon,
air temperature, rain chance, humidity, wind direction and speed. There is no track
temperature, so that is estimated from the air forecast with the track-minus-air
difference learned on this track at a similar time of day (or the current difference).

The panel is found without a reference image: a row of at least four clock times
with forecast values below them. Only numbers and codes are kept, never pixels or text.
"""
from __future__ import annotations

from contextlib import closing
import json
import re
import statistics
from pathlib import Path
import sqlite3

MIN_COLUMNS = 4
READ_EVERY_S, SEARCH_EVERY_S = 300, 60
DIRECTIONS = {'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'}
TIME = re.compile(r'(\d{1,2})[:.](\d{2})')
TIME_NO_COLON = re.compile(r'(\d{1,2})(\d{2})')   # OCR sometimes drops the colon: 345 = 3:45
TEMP_UNIT = re.compile(r'[°º0oO]?([CF])')
PERCENT = re.compile(r'(\d{1,3})%')
SPEED_UNIT = re.compile(r'(km/h|kph|mph)', re.I)
SPEED_JOINED = re.compile(r'(\d{1,3})(km/h|kph|mph)', re.I)
OFFSET_WINDOW_S, OFFSET_MIN_ROWS = 90 * 60, 60


PLAUSIBLE = {'C': (-30, 60), 'F': (-20, 140)}


def _joined_temperature(text):
    """'19°C', '19C', or '190C' where the degree sign was read as 0. Picks the plausible reading."""
    match = re.fullmatch(r'(-?\d{1,3})([CF])', text)
    if not match:
        return None
    digits, unit = match[1], match[2]
    if digits.lstrip('-') == '0':
        return None  # '0C' is a degree sign read as 0: a unit, not a temperature
    readings = [int(digits)] + ([int(digits[:-1])] if len(digits.lstrip('-')) > 1 and digits[-1] in '0' else [])
    low, high = PLAUSIBLE[unit]
    plausible = [v for v in readings if low <= v <= high]
    return (plausible[0], unit) if plausible else None


def _reading_order(tokens):
    """Top to bottom by line (words of different heights on one line stay together), then left to right."""
    lines = []
    for token in sorted(tokens, key=lambda t: t['cy']):
        if lines and abs(token['cy'] - lines[-1][0]['cy']) < .5 * max(token['h'], lines[-1][0]['h']):
            lines[-1].append(token)
        else:
            lines.append([token])
    return [t for line in lines for t in sorted(line, key=lambda t: t['x'])]


def _tokens(words):
    return [dict(t=t.strip(), x=x, y=y, w=w, h=h, cx=x + w / 2, cy=y + h / 2) for t, x, y, w, h in words if t.strip()]


def _clock(token):
    match = TIME.fullmatch(token['t']) or TIME_NO_COLON.fullmatch(token['t'])
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2])
    return (hour, minute) if hour < 24 and minute < 60 else None


def _time_row(tokens):
    """The row of clock times: at least MIN_COLUMNS on one line, most with an explicit colon."""
    clocks = [t for t in tokens if _clock(t)]
    best = []
    for anchor in clocks:
        row = sorted((t for t in clocks if abs(t['cy'] - anchor['cy']) < .6 * anchor['h']), key=lambda t: t['x'])
        if len(row) > len(best) and sum(bool(TIME.fullmatch(t['t'])) for t in row) * 2 >= len(row):
            best = row
    return best if len(best) >= MIN_COLUMNS else []


def _minutes(row, tokens):
    """Minutes of the day per column; 12-hour times use the am/pm word next to them."""
    values = []
    for token in row:
        hour, minute = _clock(token)
        suffix = next((t['t'].lower() for t in tokens if t['t'].lower() in ('am', 'pm')
                       and abs(t['cy'] - token['cy']) < .6 * token['h'] and 0 <= t['x'] - (token['x'] + token['w']) < 3 * token['h']), None)
        if suffix == 'pm' and hour < 12:
            hour += 12
        elif suffix == 'am' and hour == 12:
            hour = 0
        values.append(hour * 60 + minute)
    # Columns after the first are quarter hours; repair one misread from its neighbours.
    for i in range(1, len(values)):
        expected = values[i - 1] + 15 - values[i - 1] % 15
        if values[i] % 15 or not 0 < (values[i] - values[i - 1]) % 1440 <= 15:
            values[i] = expected % 1440
    return values


def parse_panel(words):
    """words: [(text, x, y, w, h)] -> {'columns': [...], 'box': [x0, y0, x1, y1]} or None."""
    tokens = _tokens(words)
    row = _time_row(tokens)
    if not row:
        return None
    minutes = _minutes(row, tokens)
    spacing = statistics.median(b['cx'] - a['cx'] for a, b in zip(row, row[1:]))
    centers = [t['cx'] + (spacing * .15 if any(s['t'].lower() in ('am', 'pm') for s in tokens) else 0) for t in row]
    below = [t for t in tokens if t['cy'] > row[0]['cy'] + row[0]['h'] * .6 and t not in row]
    columns = []
    for index, center in enumerate(centers):
        mine = _reading_order([t for t in below if abs(t['cx'] - center) < spacing * .55])
        entry = {'minute_of_day': minutes[index], 'air_c': None, 'rain_pct': None, 'humidity_pct': None,
                 'wind_kph': None, 'wind_dir': None}
        percents = []
        for i, token in enumerate(mine):
            text, after = token['t'], mine[i + 1]['t'] if i + 1 < len(mine) else ''
            joined = _joined_temperature(re.sub(r'[°º]', '', text))
            if entry['air_c'] is None and (joined or re.fullmatch(r'-?\d{1,3}', text) and TEMP_UNIT.fullmatch(after)):
                value, unit = joined if joined else (int(text), TEMP_UNIT.fullmatch(after)[1])
                entry['air_c'] = round((value - 32) * 5 / 9, 1) if unit == 'F' else float(value)
            elif PERCENT.fullmatch(text):
                percents.append(int(PERCENT.fullmatch(text)[1]))
            elif text.upper() in DIRECTIONS:
                entry['wind_dir'] = text.upper()
            elif entry['wind_kph'] is None and (SPEED_JOINED.fullmatch(text) or re.fullmatch(r'\d{1,3}', text) and SPEED_UNIT.fullmatch(after)):
                joined_speed = SPEED_JOINED.fullmatch(text)
                value, unit = (int(joined_speed[1]), joined_speed[2]) if joined_speed else (int(text), after)
                entry['wind_kph'] = round(value * 1.609344, 1) if unit.lower() == 'mph' else float(value)
        if percents:
            entry['rain_pct'] = percents[0]
        if len(percents) > 1:
            entry['humidity_pct'] = percents[1]
        columns.append(entry)
    if sum(c['air_c'] is not None for c in columns) < MIN_COLUMNS:
        return None
    every = row + below
    box = [min(t['x'] for t in every), min(t['y'] for t in every),
           max(t['x'] + t['w'] for t in every), max(t['y'] + t['h'] for t in every)]
    return {'columns': columns, 'box': box}


class Forecast:
    """One session's forecast, stitched from repeated reads, with how far it missed."""
    def __init__(self):
        self.identity, self.entries, self.misses, self.read_at = None, {}, [], None

    def update(self, identity, columns, time_of_day_s, air_now):
        if identity != self.identity:
            self.__init__()
            self.identity = identity
        for column in columns:
            if column['air_c'] is not None:
                self.entries[column['minute_of_day']] = column
        self.read_at = time_of_day_s
        self.observe(time_of_day_s, air_now)

    def observe(self, time_of_day_s, air_now):
        """Compare the forecast for the current quarter hour with the actual air temperature."""
        if time_of_day_s is None or air_now is None:
            return
        minute = int(time_of_day_s // 60) % 1440
        for key, entry in self.entries.items():
            if key == minute - minute % 15 and not entry.get('checked'):
                entry['checked'] = True
                self.misses = (self.misses + [round(air_now - entry['air_c'], 1)])[-24:]

    def air_at(self, time_of_day_s):
        """Linear between forecast columns; None outside them."""
        if time_of_day_s is None or not self.entries:
            return None
        minute = time_of_day_s / 60 % 1440
        points = sorted(self.entries.items())
        for (m0, a), (m1, b) in zip(points, points[1:]):
            if m0 <= minute <= m1:
                return a['air_c'] + (b['air_c'] - a['air_c']) * (minute - m0) / max(1, m1 - m0)
        return points[-1][1]['air_c'] if abs(minute - points[-1][0]) < 1 else None

    def view(self):
        return {'entries': [{k: v for k, v in e.items() if k != 'checked'} for _, e in sorted(self.entries.items())],
                'read_at_s': self.read_at, 'misses_c': list(self.misses)}

    @classmethod
    def from_view(cls, view):
        forecast = cls()
        for entry in (view or {}).get('entries') or []:
            if isinstance(entry, dict) and isinstance(entry.get('minute_of_day'), int) and isinstance(entry.get('air_c'), (int, float)):
                forecast.entries[entry['minute_of_day']] = dict(entry)
        forecast.misses = [m for m in (view or {}).get('misses_c') or [] if isinstance(m, (int, float))]
        forecast.read_at = (view or {}).get('read_at_s')
        return forecast


def learned_offset(path, game, track, time_of_day_s):
    """Median of (track - air) on this track within 90 minutes of the time of day, or None."""
    if time_of_day_s is None:
        return None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            rows = db.execute('SELECT data FROM tire_observations WHERE game=? AND track=? ORDER BY id DESC LIMIT 20000',
                              (game, track)).fetchall()
    except (OSError, sqlite3.Error):
        return None
    offsets = []
    for (text,) in rows:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        tod, air, surface = data.get('session_time_of_day_s'), data.get('air_temp_c'), data.get('track_temp_c')
        if all(isinstance(v, (int, float)) for v in (tod, air, surface)):
            gap = abs((tod - time_of_day_s + 43200) % 86400 - 43200)
            if gap <= OFFSET_WINDOW_S:
                offsets.append(surface - air)
    return statistics.median(offsets) if len(offsets) >= OFFSET_MIN_ROWS else None


def expected_track(forecast, *, at_s, air_now, track_now, learned):
    """Track temperature expected at time of day at_s: forecast air + offset. Returns (value, basis)."""
    air = forecast.air_at(at_s) if forecast else None
    if air is None:
        return track_now, 'current'
    if learned is not None:
        return round(air + learned, 1), 'forecast_with_learned_offset'
    if track_now is not None and air_now is not None:
        return round(air + track_now - air_now, 1), 'forecast_with_current_offset'
    return None, None


class WeatherPanelReader:
    """Finds the panel on the game monitor once a minute; then rereads only its area every 5 minutes."""
    def __init__(self, ocr, clock):
        self.ocr, self.clock = ocr, clock
        self.box, self.next_at = None, 0.
        self.forecast = Forecast()

    def due(self):
        return self.clock() >= self.next_at

    def maybe_read(self, image, identity, time_of_day_s, air_now):
        import asyncio
        import cv2
        now = self.clock()
        self.forecast.observe(time_of_day_s, air_now)
        if now < self.next_at or image is None:
            return self.forecast
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        x0 = y0 = 0
        if self.box:
            x0, y0, x1, y1 = self.box
            gray = gray[y0:y1, x0:x1]
        scale = 2. if self.box else min(1., (self.ocr.max_dimension - 40) / max(gray.shape))
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
        words = [(t, x / scale + x0, y / scale + y0, w / scale, h / scale)
                 for t, x, y, w, h in asyncio.run(self.ocr.words(gray))]
        panel = parse_panel(words)
        if panel:
            a, b, c, d = panel['box']
            pad = max(20, int((d - b) * .3))
            self.box = [max(0, int(a) - pad), max(0, int(b) - pad), int(c) + pad, int(d) + pad]
            self.forecast.update(identity, panel['columns'], time_of_day_s, air_now)
            self.next_at = now + READ_EVERY_S
        else:
            self.box, self.next_at = None, now + SEARCH_EVERY_S
        return self.forecast
