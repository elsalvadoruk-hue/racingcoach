"""Glyph geometry inside one numeric cell. Pixels stay in the measurement call.

Only used to confirm what the pixels show (a decimal dot, the digit count) or to
feed the local digit model. It never invents a digit the OCR did not report.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .pressure_ocr import digit_mask

GLYPH_SIZE = (16, 24)  # width, height of a normalized digit


@dataclass(frozen=True)
class Glyphs:
    digits: tuple  # (x, y, w, h) per digit, left to right
    dot: int | None  # number of digits before the single decimal dot, if exactly one is visible
    mask: np.ndarray


def segment(gray):
    """Split a cell into digit-height components and at most one baseline dot.

    Returns None when the layout is not a clean row of digits (touching glyphs,
    several dots, label text); callers then keep the existing OCR result.
    """
    if gray is None or gray.size == 0 or min(gray.shape[:2]) < 6:
        return None
    mask = digit_mask(cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    parts = [tuple(int(v) for v in s[:4]) for s in stats[1:] if s[4] >= 3]
    if not parts:
        return None
    tall = max(h for _, _, _, h in parts)
    if tall < 6:
        return None
    digits = sorted((p for p in parts if p[3] >= .6 * tall), key=lambda p: p[0])
    if not digits or len(digits) > 4:
        return None
    width = float(np.median([w for _, _, w, _ in digits]))
    baseline = max(y + h for _, y, _, h in digits)
    rest = [p for p in parts if p not in digits]
    dots = [p for p in rest if p[3] <= .3 * tall and p[2] <= .6 * width
            and p[1] + p[3] >= baseline - .25 * tall
            and digits[0][0] + digits[0][2] <= p[0] + 1 and p[0] + p[2] <= digits[-1][0] + 1]
    # Anything else that is not tiny noise means the cell is not a clean number.
    noise = [p for p in rest if p not in dots and p[2] * p[3] > .15 * width * tall]
    if noise or len(dots) > 1:
        return None
    dot = None
    if dots:
        x = dots[0][0]
        dot = sum(1 for d in digits if d[0] + d[2] <= x + 1)
    return Glyphs(tuple(digits), dot, mask)


def normalized(glyphs, index):
    """A fixed-size binary digit image for the local model; never written as an image."""
    x, y, w, h = glyphs.digits[index]
    crop = glyphs.mask[y:y + h, x:x + w]
    gw, gh = GLYPH_SIZE
    scale = min(gw / max(w, 1), gh / max(h, 1))
    resized = cv2.resize(crop, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((gh, gw), dtype=np.float32)
    ox, oy = (gw - resized.shape[1]) // 2, (gh - resized.shape[0]) // 2
    canvas[oy:oy + resized.shape[0], ox:ox + resized.shape[1]] = resized / 255.0
    return canvas.ravel()
