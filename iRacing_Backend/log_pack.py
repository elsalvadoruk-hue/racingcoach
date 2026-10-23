"""Lossless, compact packing of the race logs (traffic and timing) for the session-log upload.

The local files are gzip'd JSON lines: one header line, then one row a tick. Rows repeat the
same keys and most values move by a little each tick, so the rows are stored as columns:

- every number column is scaled to whole numbers at the precision it was written with, and
  stored as the change from the previous value of that column;
- cars (the ``c`` list of each row) are stored as one stream per car, so a car's value is a
  change from that same car's previous value, whatever other cars came and went;
- the result is JSON compressed with xz.

``unpack(pack(header, rows))`` gives back rows equal (==) to the input. ``pack`` checks this
itself and falls back to plain xz'd JSON lines when any value does not survive the round trip,
so a strange value never changes what is sent. Nothing here reads or adds names.
"""
from __future__ import annotations

import gzip
import json
import lzma
import math

FORMAT = 'iracing-coach-packed-log'
MAX_DECIMALS = 9


def read_lines(path):
    """Header and rows of a JSON-lines log, compressed with gzip (several members) or not."""
    opener = gzip.open if str(path).endswith('.gz') else open
    header, rows = None, []
    with opener(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if header is None:
                header = item
            else:
                rows.append(item)
    return header or {}, rows


def _decimals(value):
    text = repr(float(value)) if isinstance(value, float) else str(value)
    if 'e' in text or 'E' in text or 'n' in text:
        return None
    return len(text.split('.', 1)[1]) if '.' in text else 0


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column(values, keys=None):
    """One column; ``keys`` gives each value's stream (a car index) for per-stream changes."""
    numbers = [v for v in values if v is not None]
    if not all(_is_number(v) and math.isfinite(v) for v in numbers):
        return {'raw': values}
    decimals = [_decimals(v) for v in numbers]
    if any(d is None or d > MAX_DECIMALS for d in decimals):
        return {'raw': values}
    scale = 10 ** max(decimals, default=0)
    integer = all(isinstance(v, int) for v in numbers)
    last, out = {}, []
    for index, value in enumerate(values):
        if value is None:
            out.append(None)
            continue
        whole = round(value * scale)
        stream = keys[index] if keys is not None else None
        out.append(whole - last.get(stream, 0))
        last[stream] = whole
    return {'scale': scale, 'int': integer, 'd': out}


def _restore(column, keys=None):
    if 'raw' in column:
        return list(column['raw'])
    scale, integer, last, out = column['scale'], column['int'], {}, []
    for index, change in enumerate(column['d']):
        if change is None:
            out.append(None)
            continue
        stream = keys[index] if keys is not None else None
        whole = last.get(stream, 0) + change
        last[stream] = whole
        out.append(whole if integer else whole / scale)
    return out


def _encode(header, rows):
    keys = list(rows[0].keys()) if rows else []
    if any(list(row.keys()) != keys for row in rows):
        raise ValueError('rows do not share one key order')
    scalars = {}
    for key in keys:
        if key == 'c':
            continue
        values = [row[key] for row in rows]
        lists = [v for v in values if v is not None]
        if lists and all(isinstance(v, list) for v in lists) and all(v is not None for v in values) \
                and len({len(v) for v in values}) == 1:
            width = len(values[0])
            scalars[key] = {'list': width, 'cols': [_column([v[i] for v in values]) for i in range(width)]}
        else:
            scalars[key] = _column(values)
    cars = None
    if 'c' in keys:
        flat = [car for row in rows for car in (row['c'] or [])]
        if any(not isinstance(car, list) for car in flat) or len({len(car) for car in flat}) > 1:
            raise ValueError('cars are not lists of one width')
        width = len(flat[0]) if flat else 0
        ids = [car[0] for car in flat]
        cars = {'counts': [None if row['c'] is None else len(row['c']) for row in rows], 'width': width,
                'ids': _column(ids), 'cols': [_column([car[i] for car in flat], ids) for i in range(1, width)]}
    return {'format': FORMAT, 'version': 1, 'header': header, 'rows': len(rows), 'keys': keys,
            'scalars': scalars, 'cars': cars}


def _decode(packed):
    count, keys = packed['rows'], packed['keys']
    columns = {}
    for key, column in packed['scalars'].items():
        if 'list' in column:
            parts = [_restore(part) for part in column['cols']]
            columns[key] = [[part[i] for part in parts] for i in range(count)]
        else:
            columns[key] = _restore(column)
    car_rows = None
    if packed.get('cars') is not None:
        cars = packed['cars']
        ids = _restore(cars['ids'])
        fields = [_restore(column, ids) for column in cars['cols']]
        flat = [[ids[i], *[field[i] for field in fields]] for i in range(len(ids))]
        car_rows, start = [], 0
        for size in cars['counts']:
            if size is None:
                car_rows.append(None)
            else:
                car_rows.append(flat[start:start + size])
                start += size
    rows = []
    for i in range(count):
        rows.append({key: (car_rows[i] if key == 'c' else columns[key][i]) for key in keys})
    return packed['header'], rows


def pack(header, rows):
    """xz'd bytes; columnar when that round-trips exactly, plain JSON lines otherwise."""
    try:
        packed = _encode(header, rows)
        text = json.dumps(packed, separators=(',', ':'), allow_nan=False)
        if _decode(json.loads(text)) == (header, rows):
            return lzma.compress(text.encode('utf-8'), preset=9 | lzma.PRESET_EXTREME)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        pass
    lines = '\n'.join(json.dumps(item, separators=(',', ':')) for item in [header, *rows])
    return lzma.compress(lines.encode('utf-8'), preset=9 | lzma.PRESET_EXTREME)


def unpack(data):
    """(header, rows) from bytes made by pack()."""
    text = lzma.decompress(data).decode('utf-8')
    if text.startswith('{"format":"' + FORMAT + '"'):
        return _decode(json.loads(text))
    items = [json.loads(line) for line in text.splitlines() if line.strip()]
    return (items[0] if items else {}), items[1:]
