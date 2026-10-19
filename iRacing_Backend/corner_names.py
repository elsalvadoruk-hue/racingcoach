"""Official turn numbers and names for the automatically detected corner segments.

iRacing's own track maps carry a label for every turn, but only as text placed
beside the drawing. The drawing is fitted to a driven GPS lap (rotation, scale
and shift only), each label is dropped onto the nearest point of the lap, and
the detected segment there gets the label as a suggestion. A suggestion is
never spoken as a fact: the driver confirms or corrects it once per layout.
"""
import math
from pathlib import Path
import re

import numpy as np

MAP_ROOT = Path(__file__).resolve().parent.parent / 'public' / 'track-maps' / 'official'
TOKEN = re.compile(r'([MmLlHhVvCcSsQqTtZzAa])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)')
NUMBER = re.compile(r'T?(\d+[A-Za-z]?)')
FIT_LIMIT_M = 15.   # median distance from the lap to the drawn track edge; a wrong fit is far above this
LABEL_LIMIT_M, LABEL_LIMIT_MAP = 160., 60.  # a label belongs to the track point this close (the larger of the two)


def sample_path(d):
    """Points along an SVG path. Curves are sampled; arcs keep their end point only."""
    points, current, start, command, control, index = [], np.zeros(2), np.zeros(2), None, None, 0
    tokens = TOKEN.findall(d)

    def take(count):
        nonlocal index
        values = []
        while len(values) < count:
            if index >= len(tokens) or tokens[index][0]:
                return None
            values.append(float(tokens[index][1]))
            index += 1
        return values
    while index < len(tokens):
        if tokens[index][0]:
            command = tokens[index][0]
            index += 1
        if command is None:
            break
        if command in 'Zz':
            current = start.copy()
            points.append(current.copy())
            continue
        relative, kind = command.islower(), command.upper()
        size = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7}[kind]
        values = take(size)
        if values is None:
            break
        base = current if relative else 0
        if kind in 'ML':
            current = np.array(values) + base
            if kind == 'M':
                start, command = current.copy(), 'l' if relative else 'L'
            points.append(current.copy())
            control = None
        elif kind == 'H':
            current = np.array([values[0] + (current[0] if relative else 0), current[1]])
            points.append(current.copy())
            control = None
        elif kind == 'V':
            current = np.array([current[0], values[0] + (current[1] if relative else 0)])
            points.append(current.copy())
            control = None
        elif kind in 'CS':
            pairs = np.array(values).reshape(-1, 2) + base
            first = pairs[0] if kind == 'C' else (2 * current - control if control is not None else current)
            second, end = (pairs[1], pairs[2]) if kind == 'C' else (pairs[0], pairs[1])
            for t in (.2, .4, .6, .8, 1.):
                points.append((1 - t) ** 3 * current + 3 * (1 - t) ** 2 * t * first + 3 * (1 - t) * t * t * second + t ** 3 * end)
            control, current = second, end
        else:
            current = np.array(values[-2:]) + base
            points.append(current.copy())
            control = None
    return points


def outline(track_id, root=MAP_ROOT):
    """The drawn track (its filled edge) as points in map units, or None without a usable map."""
    try:
        source = (Path(root) / str(track_id) / 'active.svg').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    points = []
    for d in re.findall(r'<path[^>]*\sd="([^"]+)"', source, re.S):
        points.extend(sample_path(d))
    for listed in re.findall(r'<polygon[^>]*points="([^"]+)"', source):
        values = [float(number) for number in re.findall(r'-?\d*\.?\d+', listed)]
        points.extend(np.array(values[k:k + 2]) for k in range(0, len(values) - 1, 2))
    return np.array(points) if len(points) >= 20 else None


def start_marker(track_id, root=MAP_ROOT):
    """Middle of the drawn start/finish marker, or None. It tells two equally good fits apart."""
    try:
        source = (Path(root) / str(track_id) / 'start-finish.svg').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    points = [point for d in re.findall(r'<path[^>]*\sd="([^"]+)"', source, re.S) for point in sample_path(d)]
    return np.array(points).mean(0) if points else None


def labels(track_id, root=MAP_ROOT):
    """[(text, x, y)] with the position moved from the text origin to the middle of the label."""
    try:
        source = (Path(root) / str(track_id) / 'turns.svg').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    size = re.search(r'font-size:\s*([\d.]+)', source)
    size = float(size[1]) if size else 18.
    found = []
    for match in re.finditer(r'<text[^>]*transform="(?:translate\(([-\d.]+)[ ,]+([-\d.]+)\)|matrix\(([^)]*)\))"[^>]*>(.*?)</text>', source, re.S):
        if match[1]:
            x, y = float(match[1]), float(match[2])
        else:
            values = [float(number) for number in re.split(r'[ ,]+', match[3].strip()) if number]
            if len(values) != 6:
                continue
            x, y = values[4], values[5]
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', match[4])).strip()
        if text:
            found.append((text, x + len(text) * size * .28, y - size * .35))
    return found


def _nearest(points, targets):
    distance = ((points[:, None, :] - targets[None, :, :]) ** 2).sum(-1)
    index = distance.argmin(1)
    return index, np.sqrt(distance[np.arange(len(points)), index])


def fit(lap, drawing, start=None):
    """Similarity transform (no mirroring) of a lap in metres onto the drawing.

    Returns (points on the map, map units per metre, median error in metres).
    Several starting rotations are refined, because a circuit can look alike
    when turned around. An oval fits equally well both ways, so among the good
    fits the one whose lap start lies nearest the drawn start/finish marker wins.
    """
    sample = lap[::max(1, len(lap) // 400)]
    target = drawing[::max(1, len(drawing) // 2500)]
    centre, scale = target.mean(0), math.sqrt(((target - target.mean(0)) ** 2).sum(1).mean() / ((lap - lap.mean(0)) ** 2).sum(1).mean())
    starts = []
    for degrees in range(0, 360, 4):
        angle = math.radians(degrees)
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        placed = scale * (sample - lap.mean(0)) @ rotation.T + centre
        starts.append((float(np.median(_nearest(placed, target)[1])), degrees, rotation))
    chosen = []
    for score, degrees, rotation in sorted(starts, key=lambda item: item[0]):
        if all(min(abs(degrees - other), 360 - abs(degrees - other)) >= 30 for other in chosen) and len(chosen) < 4:
            chosen.append(degrees)
    best = None
    for degrees in chosen:
        rotation = next(r for _, d, r in starts if d == degrees)
        size, shift = scale, centre - scale * (lap.mean(0) @ rotation.T)
        for _ in range(30):
            index, distance = _nearest(size * sample @ rotation.T + shift, target)
            keep = distance <= np.percentile(distance, 85)
            a, b = sample[keep], target[index][keep]
            mean_a, mean_b = a.mean(0), b.mean(0)
            u, singular, vt = np.linalg.svd((a - mean_a).T @ (b - mean_b) / len(a))
            sign = np.diag([1, np.sign(np.linalg.det(vt.T @ u.T))])
            rotation = vt.T @ sign @ u.T
            size = (singular * np.diag(sign)).sum() / ((a - mean_a) ** 2).sum(1).mean()
            shift = mean_b - size * mean_a @ rotation.T
        error = float(np.median(_nearest(size * sample @ rotation.T + shift, target)[1]) / size)
        placed = size * lap @ rotation.T + shift
        offset = float(np.hypot(*(placed[0] - start)) / size) if start is not None else 0.
        # Rank good fits by the start/finish position; a poor fit can never win on that alone.
        rank = (error > FIT_LIMIT_M, offset if error <= FIT_LIMIT_M else error, error)
        if best is None or rank < best[3]:
            best = (placed, float(size), error, rank)
    return best[:3]


def number_key(text):
    match = NUMBER.fullmatch(text)
    return (int(re.match(r'\d+', match[1])[0]), match[1]) if match else None


def suggest(definitions, pct, lat, lon, track_id, root=MAP_ROOT):
    """Label suggestions per segment id from one clean lap: {'status', 'kind', 'fit_median_m', 'corners'}."""
    drawing, found = outline(track_id, root), labels(track_id, root)
    if drawing is None or not found:
        return {'status': 'NO_OFFICIAL_MAP', 'corners': {}}
    pct, lat, lon = (np.asarray(values, dtype=float) for values in (pct, lat, lon))
    usable = np.isfinite(pct) & np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 1e-7)
    if usable.sum() < 200 or usable.mean() < .9:
        return {'status': 'NO_GPS', 'corners': {}}
    pct, lat, lon = pct[usable], lat[usable], lon[usable]
    east = np.radians(lon - lon.mean()) * 6371000. * math.cos(math.radians(float(lat.mean())))
    north = np.radians(lat - lat.mean()) * 6371000.
    order = np.argsort(pct)   # the first point is the start/finish line
    pct, east, north = pct[order], east[order], north[order]
    placed, size, error = fit(np.c_[east, -north], drawing, start_marker(track_id, root))   # map y grows downwards
    if error > FIT_LIMIT_M:
        return {'status': 'MAP_FIT_FAILED', 'fit_median_m': round(error, 1), 'corners': {}}
    numbered = [item for item in found if number_key(item[0])]
    kind = 'numbered' if len(numbered) >= max(3, len(found) * .4) else 'named'
    corners = {}

    def segment_at(position):
        def cost(definition):
            if definition['entry_pct'] - .004 <= position <= definition['exit_pct'] + .004:
                return abs(position - definition['apex_pct'])
            return 1 + min(abs(position - definition['entry_pct']), abs(position - definition['exit_pct']))
        best = min(definitions, key=cost, default=None)
        return best if best is not None and cost(best) < 1 else None
    for text, x, y in found:
        distance = np.hypot(placed[:, 0] - x, placed[:, 1] - y)
        index = int(distance.argmin())
        if distance[index] > max(LABEL_LIMIT_M * size, LABEL_LIMIT_MAP):
            continue
        segment = segment_at(float(pct[index]))
        if segment is None:
            continue
        entry = corners.setdefault(segment['id'], {'numbers': [], 'names': []})
        key = number_key(text)
        (entry['numbers'] if key else entry['names']).append((key or float(pct[index]), text if not key else key[1]))
    result = {}
    for identifier, entry in corners.items():
        numbers = [text for _, text in sorted(entry['numbers'])]
        names = [text for _, text in sorted(entry['names'])]
        number = '-'.join(dict.fromkeys(numbers)) if kind == 'numbered' and numbers else None
        # On a numbered circuit a stray word of a multi-word name is not a usable call.
        name = names[0] if names and (kind == 'named' or number) else None
        if number or name:
            result[identifier] = {'number': number, 'name': name, 'labels': numbers + names}
    return {'status': 'SUGGESTED', 'kind': kind, 'fit_median_m': round(error, 1), 'corners': result}


def spoken_call(number, name, spoken=None):
    """How a confirmed corner is called on the radio: its own wording, its number, or its name."""
    if spoken:
        return spoken
    if number:
        return f"{str(number).split('-')[0]}번 코너"
    return name or None
