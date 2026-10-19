"""Discrete gear values and comparison colors; no interpolation or recommendations."""
import math


def gear_value(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value != int(value):
        return None
    return int(value) if -1 <= value <= 20 else None


def gear_display(current, reference):
    current, reference = gear_value(current), gear_value(reference)
    text = '—' if current is None else 'R' if current == -1 else 'N' if current == 0 else str(current)
    color = 'muted'
    if current is not None and reference is not None and current > 0 and reference > 0:
        color = 'gear_low' if current < reference else 'success' if current > reference else 'text'
    return {'current': current, 'reference': reference, 'text': text, 'color': color}
