"""Offline pressure recognition. This module never captures a screen or sends UDP."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import time

WHEELS = ('FL', 'FR', 'RL', 'RR')
KPA_PER_UNIT = {'bar': 100.0, 'psi': 6.894757293, 'kPa': 1.0}


def load_image(path):
    import cv2
    import numpy as np
    raw = Path(path).read_bytes()
    if len(raw) > 50 * 1024 * 1024:
        raise ValueError('이미지 파일이 너무 큽니다.')
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] * image.shape[1] > 40_000_000:
        raise ValueError('이미지를 읽을 수 없거나 해상도가 너무 큽니다.')
    return image, hashlib.sha256(raw).hexdigest()


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 1:
        raise ValueError('지원하지 않는 압력 프로필입니다.')
    from .vehicle_dash import canonical_car
    if canonical_car(profile.get('car_path')) is None:
        raise ValueError('현재 GT3 11종과 Porsche Cup 992.2를 지원합니다.')
    dimensions = profile.get('image_size')
    if (not isinstance(dimensions, list) or len(dimensions) != 2
            or any(type(v) is not int or not 1 <= v <= 20000 for v in dimensions)):
        raise ValueError('기준 이미지 해상도를 확인해 주세요.')
    if profile.get('unit') not in KPA_PER_UNIT:
        raise ValueError('압력 단위를 bar / psi / kPa 중에서 선택해 주세요.')
    if type(profile.get('decimals')) is not int or not 0 <= profile['decimals'] <= 2:
        raise ValueError('소수 자릿수는 0~2만 지원합니다.')
    boxes = profile.get('boxes')
    partial=profile.get('automatic_partial') is True
    if not isinstance(boxes, dict) or (not partial and set(boxes)!=set(WHEELS)) or not set(boxes)<=set(WHEELS):
        raise ValueError('FL / FR / RL / RR 네 영역을 모두 지정해 주세요.')
    temperatures=profile.get('temperature_boxes',{})
    if (not isinstance(temperatures,dict) or not set(temperatures)<=set(WHEELS)
            or profile.get('temperature_unit','C') not in ('C','F','unknown')):
        raise ValueError('타이어 온도 영역과 단위를 확인해 주세요.')
    if not boxes and not temperatures:raise ValueError('읽을 수 있는 숫자 영역이 없습니다.')
    width, height = dimensions
    accepted = []
    for kind,wheel,box in [('pressure',w,b) for w,b in boxes.items()]+[('temperature',w,b) for w,b in temperatures.items()]:
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) is not int for v in box)):
            raise ValueError(f'{wheel} 영역 좌표가 올바르지 않습니다.')
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
                and x1-x0 >= 8 and y1-y0 >= 8):
            raise ValueError(f'{wheel} 숫자를 포함하도록 화면 안에 영역을 지정해 주세요.')
        if any(kind==other and max(x0, a) < min(x1, c) and max(y0, b) < min(y1, d)
               for other,a, b, c, d in accepted):
            raise ValueError('바퀴별 압력 영역이 겹치지 않게 지정해 주세요.')
        accepted.append((kind,*box))
    return profile


def make_profile(image, source_path, sha256, boxes, unit, decimals, *, car_path='bmwm4gt3',
                 temperature_boxes=None,temperature_unit='C',automatic_partial=False,**extra):
    height, width = image.shape[:2]
    return validate_profile(dict(schema=1, car_path=car_path, image_size=[width, height],
        unit=unit, decimals=decimals, boxes=boxes, source_image_name=Path(source_path).name,
        source_sha256=sha256,temperature_boxes=temperature_boxes or {},temperature_unit=temperature_unit,
        automatic_partial=automatic_partial,
        note='Screen regions; re-detect on every live frame.',**extra))


def parse_pressure(text, unit, decimals):
    if unit not in KPA_PER_UNIT or type(decimals) is not int or not 0 <= decimals <= 2:
        return None
    # Spaces around a decimal separator are harmless; missing digits and letters
    # are not repaired. A label, multiple numbers, or wrong precision is rejected.
    normalized = re.sub(r'\s*([.,])\s*', r'\1', text.strip()).replace(',', '.')
    pattern = r'[0-9]{1,3}' + (rf'\.[0-9]{{{decimals}}}' if decimals else '')
    if re.fullmatch(pattern, normalized) is None:
        return None
    value = float(normalized)
    kpa = value * KPA_PER_UNIT[unit]
    return (value, kpa) if math.isfinite(kpa) and 40 <= kpa <= 400 else None


def parse_auto_pressure(text):
    # Each format must contain the actual decimal point. No repairing glyphs,
    # missing decimals, or ambiguous unit choices using a previous reading.
    candidates=[]
    # Generic auto detection supports bar or psi. Integer OCR such as "239"
    # must not turn a dropped decimal from "23.9" into a plausible kPa reading.
    # Explicit kPa profiles remain supported by parse_pressure.
    for unit,decimals in (('bar',2),('psi',1),('psi',2)):
        value=parse_pressure(text,unit,decimals)
        if value is not None:candidates.append(value)
    return candidates[0] if len(candidates)==1 else None


def parse_temperature(text,unit):
    normalized=text.strip().replace(',', '.')
    if unit not in ('C','F') or re.fullmatch(r'-?[0-9]{1,3}(?:\.[0-9])?',normalized) is None:return None
    value=float(normalized)
    celsius=value if unit=='C' else (value-32)*5/9
    return (value,celsius) if -50<=celsius<=250 else None


def digit_mask(gray):
    """Separate numeric glyphs from the inverse LCD background and its border."""
    import cv2
    import numpy as np
    _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY|cv2.THRESH_OTSU)
    if np.median(mask)>127:mask=255-mask
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask)
    height,width=gray.shape
    for index,(x,y,w,h,area) in enumerate(stats[1:],1):
        touching=x<=1 or y<=1 or x+w>=width-1 or y+h>=height-1
        if touching and (h>w*3 or w>h*3):mask[labels==index]=0
    points=cv2.findNonZero(mask)
    if points is None:return mask
    x,y,w,h=cv2.boundingRect(points)
    return cv2.copyMakeBorder(mask[y:y+h,x:x+w],4,4,4,4,cv2.BORDER_CONSTANT,value=0)


class WindowsPressureOcr:
    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        self.languages = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
        self.engine = (OcrEngine.try_create_from_language(Language('en-US'))
                       or OcrEngine.try_create_from_user_profile_languages())
        if self.engine is None:
            raise RuntimeError('Windows OCR 언어가 없습니다. Windows 언어 설정에서 OCR을 확인해 주세요.')
        self.language = self.engine.recognizer_language.language_tag
        self.max_dimension = OcrEngine.max_image_dimension

    async def _text(self, gray):
        import cv2
        from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
        from winrt.windows.storage.streams import DataWriter
        bgra = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA)
        writer = DataWriter()
        bitmap = None
        try:
            writer.write_bytes(bgra.tobytes())
            bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(),
                BitmapPixelFormat.BGRA8, bgra.shape[1], bgra.shape[0])
            result = await self.engine.recognize_async(bitmap)
            return result.text.strip()
        finally:
            if bitmap is not None:
                bitmap.close()
            writer.close()

    async def recognize(self, image, profile):
        import cv2
        validate_profile(profile)
        if list(image.shape[1::-1]) != profile['image_size']:
            raise ValueError('프로필과 이미지 해상도가 다릅니다. 영역을 다시 지정해 주세요.')
        started = time.perf_counter()
        wheels = {w:dict(status='not_visible',texts=[],value=None,kpa=None) for w in WHEELS}
        temperatures={w:dict(status='not_available',texts=[],value=None,celsius=None) for w in WHEELS}
        regions=[('pressure',w,b) for w,b in profile['boxes'].items()]
        regions += [('temperature',w,b) for w,b in profile.get('temperature_boxes',{}).items()]
        for kind,wheel,box in regions:
            x0, y0, x1, y1 = box
            crop = image[y0:y1, x0:x1]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
            # Remove empty margins so small locator rounding changes do not
            # change the OCR line layout. Retain the decimal point as a glyph.
            _, foreground = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            points = cv2.findNonZero(foreground)
            tight = gray
            if points is not None:
                gx,gy,gw,gh = cv2.boundingRect(points)
                tight = cv2.copyMakeBorder(gray[gy:gy+gh,gx:gx+gw], 3,3,3,3,
                                         cv2.BORDER_CONSTANT, value=0)
            # These LCD fonts have unusually wide digits or black text on green.
            # Normalize their glyphs without changing or inventing numeric text.
            car=profile['car_path']
            if car=='ferrari296gt3':
                # Blue gradient cells confuse the OCR layout and can add a digit.
                # Isolate the real glyphs and decimal; never repair numeric text.
                mask=digit_mask(gray)
                if kind=='pressure':
                    threshold,_=cv2.threshold(gray,0,255,cv2.THRESH_BINARY|cv2.THRESH_OTSU)
                    high=digit_mask(cv2.threshold(gray,min(255,round(threshold*1.15)),255,cv2.THRESH_BINARY)[1])
                    flat=cv2.morphologyEx(gray,cv2.MORPH_TOPHAT,
                        cv2.getStructuringElement(cv2.MORPH_RECT,(17,17)))
                    flat=cv2.normalize(flat,None,0,255,cv2.NORM_MINMAX)
                    # Resizing the same wrong glyph twice is insufficient. Check
                    # independent segmentations and retain any valid conflict.
                    variants=[(base,scale,1,0) for base in (mask,gray,high,flat) for scale in (2.0,3.0)]
                else:
                    # Keep the original LCD strokes: thresholding a small 48
                    # can turn its 8 into B. Small binary resizes also close
                    # the gap in 50's 5, making it look like 60.
                    variants=[(gray,2.0,1,0),(gray,3.0,1,0),
                              (mask,3.0,1,0),(mask,4.0,1,0)]
            elif car in ('audir8lmsevo2gt3','chevyvettez06rgt3'):
                # Cell outlines are mistaken for 1s, while low-resolution
                # antialiased text can lose a leading digit. Remove the real
                # border before OCR; never repair the returned numeric string.
                mask=digit_mask(gray)
                variants=[(mask,scale,1,0) for scale in (2.0,3.0,4.0)]
            elif car=='fordmustanggt3' and kind=='pressure':
                # Its wide, short glyphs and blue decimal need padding and
                # more than one aspect/scale to form an actual consensus.
                mask=digit_mask(gray)
                variants=[(mask,scale,stretch,0) for stretch in (1.,1.3) for scale in (2.,4.)]
            elif car=='lamborghinievogt3' or (car=='fordmustanggt3' and kind=='temperature'):
                mask=digit_mask(gray)
                stretch=(1.3 if kind=='pressure' else 1.0) if car=='lamborghinievogt3' else .7
                variants=[(mask,2.0,stretch,0),(mask,3.0,stretch,0)]
            elif car=='porsche9922cup' and kind=='pressure' and profile['unit']=='kPa' and profile['decimals']==0:
                # The red LCD cell leaves little margin around the leading 1.
                # Isolate actual glyphs and pad them before resizing, so OCR
                # does not drop that digit. Keep all valid conflicts below.
                mask=digit_mask(gray)
                variants=[(mask,scale,1,0) for scale in (2.0,3.0,4.0)]
            else:
                variants=[(gray,2.0,1,0),(gray,3.0,1,0),(gray,4.0,1,0),(tight,3.0,1,0),
                          (255-gray,2.0,1,255),(255-gray,3.0,1,255)]
            texts = []
            for base, requested_scale,stretch,border in variants:
                scale = min(requested_scale, (self.max_dimension-40)/max(base.shape[0],base.shape[1]*stretch))
                variant = cv2.resize(base, None, fx=scale*stretch, fy=scale, interpolation=cv2.INTER_CUBIC)
                variant = cv2.copyMakeBorder(variant, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=border)
                texts.append(await self._text(variant))
            if kind=='temperature':
                candidates=[parse_temperature(text,profile.get('temperature_unit','C')) for text in texts]
            elif profile.get('screen_unit')=='auto':
                candidates=[parse_auto_pressure(text) for text in texts]
            else:
                candidates=[parse_pressure(text, profile['unit'], profile['decimals']) for text in texts]
            valid = {value for value in candidates if value is not None}
            # Require agreement in at least two image variants. Conflicting valid
            # numbers are never averaged or resolved by the previous value.
            accepted = next(iter(valid)) if len(valid) == 1 and sum(v is not None for v in candidates) >= 2 else None
            if car=='ferrari296gt3' and kind=='pressure' and len({i//2 for i,v in enumerate(candidates) if v is not None})<2:
                accepted=None
            status = 'recognized' if accepted else ('ambiguous' if len(valid) > 1 else
                ('single_variant' if valid else 'unreadable'))
            target=temperatures if kind=='temperature' else wheels
            target[wheel] = dict(status=status, texts=texts,
                value=accepted[0] if accepted else None,
                **{('celsius' if kind=='temperature' else 'kpa'):accepted[1] if accepted else None})
        return dict(source='offline_screenshot_ocr', language=self.language,
            available_languages=self.languages, unit=profile['unit'], wheels=wheels,temperatures=temperatures,
            temperature_unit=profile.get('temperature_unit','C'),temperature_kind=profile.get('temperature_kind'),
            elapsed_ms=round((time.perf_counter()-started)*1000, 3),
            note='Static image only. Temporal stability, dash page and live capture are not verified.')
