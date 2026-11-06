"""Known manual color semantics only. Pixels never leave the measurement call."""
import numpy as np
import cv2

# 'Red' has different meanings: Audi/Aston red is LOW pressure, never high.
PROFILES = {
    'mclaren720sgt3': ('text', {'blue': 'low', 'red': 'high', 'white': 'ok'},
                     {'blue': 'cold', 'red': 'hot', 'white': 'ok'}),
    'fordmustanggt3': ('text', {'blue': 'low', 'red': 'high', 'white': 'ok'},
                      {'blue': 'cold', 'red': 'hot', 'white': 'ok'}),
    'chevyvettez06rgt3': ('background', {'blue': 'low', 'red': 'high', 'green': 'ok'},
                        {'blue': 'cold', 'red': 'hot', 'green': 'ok'}),
    'ferrari296gt3': ('background', {'blue': 'low', 'red': 'high'}, {'blue': 'cold', 'red': 'hot'}),
    'amvantageevogt3': ('background', {'red': 'low', 'orange': 'low', 'green': 'ok'}, {}),
    'audir8lmsevo2gt3': ('background', {'red': 'low'}, {}),
}
# Manuals: Ferrari 'black' and Audi 'default background' mean optimum. A plain
# dark cell is also what an occluded or night screen looks like, so it counts as
# 'ok' only for a cell whose number was read in the same frame (visible proof).
NEUTRAL_OK = {'ferrari296gt3', 'audir8lmsevo2gt3'}


def neutral(image, box):
    """True when the cell's inset edge is achromatic (black/grey), any brightness."""
    if box is None:
        return False
    x1,y1,x2,y2 = (int(v) for v in box)
    if x1 < 0 or y1 < 0 or x2 > image.shape[1] or y2 > image.shape[0] or x2-x1 < 8 or y2-y1 < 8:
        return False
    s = cv2.cvtColor(image[y1:y2,x1:x2,:3], cv2.COLOR_BGR2HSV)[:,:,1]
    mask = np.ones(s.shape,dtype=bool)
    mask[2:-2,2:-2] = False
    return np.count_nonzero(mask & (s < 50)) / max(1, int(mask.sum())) >= .85


def color(image, box, mode):
    if box is None:
        return None
    x1,y1,x2,y2 = (int(v) for v in box)
    if x1 < 0 or y1 < 0 or x2 > image.shape[1] or y2 > image.shape[0] or x2-x1 < 8 or y2-y1 < 8:
        return None
    hsv = cv2.cvtColor(image[y1:y2,x1:x2,:3], cv2.COLOR_BGR2HSV)
    h,s,v = (hsv[:,:,i] for i in range(3))
    if mode == 'background':
        mask = np.ones(h.shape,dtype=bool)
        # Only the inset edge: avoid the glyph and the neighboring box border.
        mask[2:-2,2:-2] = False
    else:
        mask = v >= max(70, float(np.percentile(v, 75)))
    count = int(mask.sum())
    if count < 10:
        return None
    bright = (s >= 90) & (v >= 45) & mask
    colors = {'red': ((h < 9) | (h > 170)), 'orange': (h >= 9) & (h < 26),
              'green': (h >= 35) & (h <= 90), 'blue': (h >= 95) & (h <= 135)}
    for name, band in colors.items():
        if np.count_nonzero(bright & band) / count >= .65:
            return name
    if mode == 'text' and np.count_nonzero(mask & (v >= 100) & (s < 50)) / count >= .85:
        return 'white'
    # Black at night can be an unreadable/occluded region, not an 'OK' state.
    return None


def states(image, car, pressure, temperature, recognized=None):
    """recognized: {'pressure': wheels, 'temperature': wheels} read in this frame."""
    profile = PROFILES.get(car)
    if profile is None:
        return {}, {}
    mode, pmap, tmap = profile
    recognized = recognized or {}
    def classify(wheel, box, mapping, field):
        name = color(image, box, mode)
        if (name is None and mapping and car in NEUTRAL_OK and wheel in recognized.get(field, ())
                and neutral(image, box)):
            return 'ok'
        return mapping.get(name)
    return ({w:classify(w,box,pmap,'pressure') for w,box in pressure.items()},
            {w:classify(w,box,tmap,'temperature') for w,box in temperature.items()})
