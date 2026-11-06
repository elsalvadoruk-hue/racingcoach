"""Local digit reader that learns each car's dash font, with Windows OCR as the teacher.

Learning: a cell is learned only when Windows OCR accepted it (two variants
agree, format checked) and the same value was also accepted in the previous
frame. Each digit glyph then becomes a labelled 16x24 shape for that car.

Switching: per car, once the local reader has been compared with OCR enough
times and agrees almost always, it becomes the primary reader. OCR then runs
only for cells the local reader cannot read confidently, and as a full check
every VERIFY_EVERY frames or when the glyph size changes. Repeated
disagreement returns the car to OCR and relearns it from scratch.

Privacy: only normalized binary digit shapes are kept, on this PC, per car.
They are not screenshots and are never put in logs, exports or uploads.
"""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import re

import cv2
import numpy as np

from .glyphs import normalized, segment
from .pressure_ocr import (AUTO_PRESSURE_FORMATS, WHEELS, parse_formats, parse_pressure,
                           parse_temperature)

CLASSES = '0123456789'
MAX_PER_CLASS = 40
MIN_PER_CLASS = 5
SWITCH_MIN_COMPARED = 500
SWITCH_AGREEMENT = .995
SWITCH_COVERAGE = .95
SWITCH_MIN_VALUES = 8
VERIFY_EVERY = 30
VERIFY_WINDOW = 50
REVERT_DISAGREEMENTS = 2
ACCEPT_DISTANCE = .12   # mean absolute difference of normalized shapes (0..1)
ACCEPT_MARGIN = .06     # to the nearest other digit
SCALE_TOLERANCE = .2
SAVE_EVERY = 60         # learned cells between saves


def _gray(frame, box):
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)


def _formats(profile):
    formats = [tuple(f) for f in profile.get('pressure_formats') or ()]
    if formats:
        return formats
    if profile.get('screen_unit') == 'auto':
        return list(AUTO_PRESSURE_FORMATS)
    return [(profile['unit'], profile['decimals'])]


def _parse(kind, text, profile):
    if kind == 'temperature':
        return parse_temperature(text, profile.get('temperature_unit', 'C'))
    formats = _formats(profile)
    return parse_formats(text, formats) if len(formats) > 1 else parse_pressure(text, *formats[0])


class CarDigits:
    def __init__(self):
        self.shapes = {c: [] for c in CLASSES}
        self.mode = 'learning'
        self.compared = deque(maxlen=SWITCH_MIN_COMPARED)  # (answered, agreed) in learning
        self.seen = deque(maxlen=SWITCH_MIN_COMPARED)      # compared cell texts, for variety
        self.verify = deque(maxlen=VERIFY_WINDOW)           # agreed, in self mode
        self.heights = deque(maxlen=200)
        self.frames = 0
        self.dirty = 0

    def reset(self):
        self.__init__()
        self.dirty = 1

    # --- classification -------------------------------------------------
    def classify(self, vector):
        best = []
        for digit, shapes in self.shapes.items():
            if len(shapes) >= MIN_PER_CLASS:
                best.append((float(np.abs(np.asarray(shapes) - vector).mean(axis=1).min()), digit))
        if len(best) < 2:
            return None
        best.sort()
        (distance, digit), (second, _) = best[0], best[1]
        return digit if distance <= ACCEPT_DISTANCE and second - distance >= ACCEPT_MARGIN else None

    def read(self, gray, kind, profile):
        glyphs = segment(gray)
        if glyphs is None:
            return None
        digits = []
        for index in range(len(glyphs.digits)):
            digit = self.classify(normalized(glyphs, index))
            if digit is None:
                return None
            digits.append(digit)
        text = ''.join(digits)
        if glyphs.dot is not None:
            text = text[:glyphs.dot] + '.' + text[glyphs.dot:]
        return _parse(kind, text, profile)

    def height_changed(self, gray):
        glyphs = segment(gray)
        if glyphs is None or len(self.heights) < 20:
            return False
        current = float(np.median([h for *_, h in glyphs.digits]))
        learned = float(np.median(self.heights))
        return abs(current - learned) > SCALE_TOLERANCE * learned

    # --- learning ---------------------------------------------------------
    def learn(self, gray, text):
        """text: the accepted OCR text for this cell, e.g. '24.1' or '111'."""
        digits = re.sub(r'\D', '', text)
        glyphs = segment(gray)
        if glyphs is None or len(glyphs.digits) != len(digits) or not digits:
            return False
        dot = text.find('.')
        if (glyphs.dot is None) != (dot < 0) or dot >= 0 and glyphs.dot != dot:
            return False
        for index, digit in enumerate(digits):
            shapes = self.shapes[digit]
            shapes.append(normalized(glyphs, index))
            del shapes[:-MAX_PER_CLASS]
        self.heights.extend(h for *_, h in glyphs.digits)
        self.dirty += 1
        return True

    def ready_to_switch(self):
        if self.mode != 'learning' or len(self.compared) < SWITCH_MIN_COMPARED:
            return False
        answered = [agreed for done, agreed in self.compared if done]
        # A steady dash shows the same few numbers; require several different ones.
        return (len(set(self.seen)) >= SWITCH_MIN_VALUES
                and len(answered) / len(self.compared) >= SWITCH_COVERAGE
                and sum(answered) / max(1, len(answered)) >= SWITCH_AGREEMENT)

    def status(self):
        answered = [agreed for done, agreed in self.compared if done]
        return {'mode': self.mode, 'compared': len(self.compared), 'needed': SWITCH_MIN_COMPARED,
                'agreement': round(sum(answered) / len(answered), 4) if answered else None,
                'digits_learned': sum(len(s) >= MIN_PER_CLASS for s in self.shapes.values()),
                'values_seen': len(set(self.seen)), 'values_needed': SWITCH_MIN_VALUES,
                'verify_disagreements': self.verify.count(False) if self.mode == 'self' else 0}

    # --- persistence ------------------------------------------------------
    def to_arrays(self):
        rows = [(d, s) for d, shapes in self.shapes.items() for s in shapes]
        x = np.asarray([np.round(s * 255) for _, s in rows], dtype=np.uint8).reshape(len(rows), 16 * 24)
        y = np.asarray([ord(d) for d, _ in rows], dtype=np.uint8)
        meta = json.dumps({'version': 1, 'mode': self.mode, 'compared': [[int(a), int(b)] for a, b in self.compared],
                           'seen': list(self.seen),
                           'verify': [int(v) for v in self.verify], 'heights': [float(h) for h in self.heights]})
        return dict(x=x, y=y, meta=np.frombuffer(meta.encode(), dtype=np.uint8))

    @classmethod
    def from_arrays(cls, data):
        model = cls()
        meta = json.loads(bytes(data['meta']).decode())
        if meta.get('version') != 1:
            return model
        for vector, code in zip(data['x'], data['y']):
            digit = chr(int(code))
            if digit in model.shapes and vector.size == 16 * 24:
                model.shapes[digit].append(vector.astype(np.float32) / 255)
        for shapes in model.shapes.values():
            del shapes[:-MAX_PER_CLASS]
        model.mode = meta['mode'] if meta.get('mode') in ('learning', 'self') else 'learning'
        model.compared.extend((bool(a), bool(b)) for a, b in meta.get('compared', [])[-SWITCH_MIN_COMPARED:])
        model.verify.extend(bool(v) for v in meta.get('verify', [])[-VERIFY_WINDOW:])
        model.seen.extend(str(v)[:8] for v in meta.get('seen', [])[-SWITCH_MIN_COMPARED:])
        model.heights.extend(h for h in meta.get('heights', []) if isinstance(h, (int, float)) and math.isfinite(h))
        return model


class DigitReader:
    """Wraps one Windows OCR call per frame; returns the same result shape as OCR."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.cars = {}
        self.previous = {}  # (car, kind, wheel) -> text accepted in the previous frame

    def _path(self, car):
        return self.folder / (re.sub(r'[^a-z0-9_-]', '_', car)[:80] + '.npz')

    def model(self, car):
        if car not in self.cars:
            try:
                with np.load(self._path(car), allow_pickle=False) as data:
                    self.cars[car] = CarDigits.from_arrays(data)
            except (OSError, ValueError, KeyError, TypeError):
                self.cars[car] = CarDigits()
        return self.cars[car]

    def save(self, car, *, force=False):
        model = self.cars.get(car)
        if model is None or not model.dirty or (not force and model.dirty < SAVE_EVERY):
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(car)
        temporary = path.with_suffix('.tmp')
        try:
            with open(temporary, 'wb') as handle:
                np.savez_compressed(handle, **model.to_arrays())
            os.replace(temporary, path)
            model.dirty = 0
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)

    def close(self):
        for car in list(self.cars):
            self.save(car, force=True)

    def reset(self, car):
        self.model(car).reset()
        self.previous = {k: v for k, v in self.previous.items() if k[0] != car}
        self.save(car, force=True)

    def status(self, car):
        return self.model(car).status() if car else None

    def read(self, car, frame, profile, run_ocr):
        """run_ocr(profile) -> Windows OCR result for that (possibly reduced) profile."""
        model = self.model(car)
        model.frames += 1
        regions = [('pressure', w, b) for w, b in profile['boxes'].items()]
        regions += [('temperature', w, b) for w, b in profile.get('temperature_boxes', {}).items()]
        grays = {(kind, wheel): _gray(frame, box) for kind, wheel, box in regions}
        own = {key: model.read(gray, key[0], profile) for key, gray in grays.items()}
        if model.mode == 'self':
            check = model.frames % VERIFY_EVERY == 0 or any(model.height_changed(g) for g in grays.values())
            needed = {key for key, value in own.items() if value is None or check}
        else:
            needed = set(grays)
        ocr = {'wheels': {}, 'temperatures': {}}
        if needed:
            reduced = dict(profile, automatic_partial=True,
                boxes={w: b for k, w, b in regions if k == 'pressure' and (k, w) in needed},
                temperature_boxes={w: b for k, w, b in regions if k == 'temperature' and (k, w) in needed})
            ocr = run_ocr(reduced)
        result = {**{k: v for k, v in ocr.items() if k not in ('wheels', 'temperatures')},
                  'wheels': {}, 'temperatures': {}}
        for kind, wheel, _ in regions:
            table = 'temperatures' if kind == 'temperature' else 'wheels'
            number = 'celsius' if kind == 'temperature' else 'kpa'
            taught = (ocr.get(table) or {}).get(wheel)
            mine = own[(kind, wheel)]
            accepted = taught if taught and taught.get(number) is not None else None
            key = (car, kind, wheel)
            if accepted is not None:
                text = next((t.strip() for t in accepted.get('texts', [])
                             if _parse(kind, t.strip(), profile) == (accepted['value'], accepted[number])), None)
                if text is None and accepted.get('dot_verified'):
                    # OCR dropped the dot and the pixels confirmed where it is.
                    plain = next((re.sub(r'\s', '', t) for t in accepted.get('texts', []) if t.strip()), '')
                    glyphs = segment(grays[(kind, wheel)])
                    text = plain[:glyphs.dot] + '.' + plain[glyphs.dot:] if glyphs and glyphs.dot else None
                stable = text is not None and self.previous.get(key) == text
                self.previous[key] = text
                if stable:
                    model.learn(grays[(kind, wheel)], text)
                agreed = mine is not None and math.isclose(mine[1], accepted[number], abs_tol=1e-6)
                if model.mode == 'learning':
                    model.compared.append((mine is not None, agreed))
                    if text is not None:
                        model.seen.append(text)
                elif mine is not None:
                    model.verify.append(agreed)
                result[table][wheel] = dict(accepted, source='windows_ocr')
            elif mine is not None and model.mode == 'self':
                result[table][wheel] = dict(status='recognized', texts=[], value=mine[0], reason=None,
                                            dot_verified=False, source='digit_model', **{number: mine[1]})
            else:
                self.previous.pop(key, None)
                base = taught or dict(status='not_visible', texts=[], value=None, reason='empty', dot_verified=False)
                result[table][wheel] = dict(base, **{number: None})
        if model.mode == 'self' and model.verify.count(False) >= REVERT_DISAGREEMENTS:
            # The dash or its rendering changed: go back to OCR and relearn.
            model.reset()
            self.previous = {k: v for k, v in self.previous.items() if k[0] != car}
            self.save(car, force=True)
        elif model.ready_to_switch():
            model.mode = 'self'
            model.verify.clear()
            self.save(car, force=True)
        else:
            self.save(car)
        result.setdefault('temperature_unit', profile.get('temperature_unit', 'C'))
        result['reader'] = {'mode': model.mode, 'ocr_regions': len(needed), 'regions': len(regions)}
        return result
