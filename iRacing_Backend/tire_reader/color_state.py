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


def states(image, car, pressure, temperature):
    profile = PROFILES.get(car)
    if profile is None:
        return {}, {}
    mode, pmap, tmap = profile
    return ({w:pmap.get(color(image,box,mode)) for w,box in pressure.items()},
            {w:tmap.get(color(image,box,mode)) for w,box in temperature.items()})
