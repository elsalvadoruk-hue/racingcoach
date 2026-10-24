"""Observed chart annotations. No recording, database access or live-state changes."""
from bisect import bisect_right
import math


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def observe(row, at, completed, *, distance=None, pit=None, incidents=None, best=None, last=None, gap=False):
    previous = row.get('_event_state') if not gap else None
    lap = number(completed)
    pct = number(distance)
    best = number(best)
    last = number(last)
    incidents = number(incidents)
    pit = bool(pit) if pit in (0, 1) else None
    valid_lap = lap is not None and lap >= 0 and lap.is_integer()
    continuous = previous is not None and valid_lap and previous['lap'] is not None and lap in (previous['lap'], previous['lap'] + 1)
    x = lap + pct if valid_lap and pct is not None and 0 <= pct < 1 else None
    events = row.setdefault('events', [])
    if not continuous or pit is None or previous['pit'] is None:
        row.pop('_pit_open', None)
    if continuous and x is not None:
        if previous['pit'] is not None and pit is not None and pit != previous['pit']:
            event = dict(kind='pit_in' if pit else 'pit_out', x=round(x, 5), lap=int(lap) + 1, at_s=round(at, 3))
            events.append(event)
            if pit:
                row['_pit_open'] = event
            elif (entry := row.pop('_pit_open', None)) is not None and x >= entry['x']:
                entry['end_x'] = round(x, 5)
                entry['duration_s'] = round(at - entry['at_s'], 3)
        if incidents is not None and previous['incidents'] is not None and incidents > previous['incidents'] >= 0:
            delta = incidents - previous['incidents']
            if delta.is_integer():
                events.append(dict(kind='incident', x=round(x, 5), lap=int(lap) + 1, at_s=round(at, 3), delta=int(delta)))
    if best is not None and best > 0:
        history = row.setdefault('_best_history', [])
        if not history or history[-1][1] != best:
            history.append((round(at, 3), best))
        if (continuous and lap > 0 and last is not None and abs(last - best) <= .005
                and (previous['best'] is None or best < previous['best'] - .0005)):
            events.append(dict(kind='personal', x=int(lap), lap=int(lap), at_s=round(at, 3), time_s=round(best, 3)))
    row['_event_state'] = dict(lap=lap if valid_lap else None, pit=pit, incidents=incidents, best=best)


def attach_laps(result, telemetry):
    """Reuse the selected file's existing verdict; never alter lap analysis rules."""
    own = next((row for row in result['series'] if row['own']), None)
    times = telemetry.channels.get('SessionTime')
    first_index = result.pop('_event_start_index', 0)
    if own is None or times is None:
        return
    checkpoints = {point[0]: point for point in own.get('lap_points', []) if point[2] is not None}
    best = None
    for lap in getattr(telemetry, 'laps', []):
        point = checkpoints.get(lap.lap_number)
        if (not lap.complete or point is None or not first_index <= lap.start_idx <= lap.end_idx < len(times)
                or not 0 <= point[3] - float(times[lap.end_idx]) <= max(3, result['sample_interval_s'] * 2.5)):
            continue
        events = own.setdefault('events', [])
        if lap.status == 'INVALID':
            events[:] = [event for event in events if event['kind'] != 'personal' or event['lap'] != lap.lap_number]
            # This is the analyzer's verdict, which may include pit/off-track samples.
            events.append(dict(kind='invalid', x=lap.lap_number, lap=lap.lap_number, at_s=point[3],
                               reason=lap.invalid_reason or 'invalid lap'))
        value = number(lap.lap_time_s)
        if lap.valid and value is not None and value > 0 and (best is None or value < best - .0005):
            best = value
            if not any(e['kind'] == 'personal' and e['lap'] == lap.lap_number for e in events):
                events.append(dict(kind='personal', x=lap.lap_number, lap=lap.lap_number, at_s=point[3], time_s=round(value, 3)))


def finalize(result, telemetry):
    if not result or result.get('status') != 'READY':
        return result
    attach_laps(result, telemetry)
    rows = result['series']
    complete = result.pop('_event_best_scope', False)
    for row in rows:
        for event in row.get('events', []):
            if event['kind'] != 'personal' or not complete:
                continue
            bests = []
            for other in rows:
                history = other.get('_best_history', [])
                index = bisect_right(history, (event['at_s'], math.inf)) - 1
                if index < 0:
                    break
                bests.append(history[index][1])
            if len(bests) == len(rows) and event['time_s'] <= min(bests) + .0005:
                event['kind'] = 'fastest'
        # Publish only events in the observed time range; gaps never become pit spans.
        row['events'] = sorted(row.get('events', []), key=lambda e: (e['at_s'], e['kind']))
    return result
