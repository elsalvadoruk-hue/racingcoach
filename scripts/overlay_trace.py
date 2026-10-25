"""Subpixel, antialiased graphs for fictional previews and observed live pedals."""
from __future__ import annotations

from math import ceil, floor, isfinite, degrees

from PIL import Image, ImageDraw

if __package__:
    from .overlay_demo_data import LENGTH_M, inputs
else:
    from overlay_demo_data import LENGTH_M, inputs


class TraceRenderer:
    # Include padding for the halo, endpoint dots and the top of the cursor.
    BOUNDS = (3, 39, 386, 127)
    BASIC_BOUNDS = (3, 3, 338, 127)

    @staticmethod
    def basic_x(x):
        return 6+(x-6)*288/336 if x <= 342 else x-48

    def __init__(self):
        self.samples = {}

    def live_frame(self, rows, scale_x, scale_y, colors, steering=False, basic=False, options=None):
        """Render observed pedals only; unknown channels and gaps stay empty."""
        bounds = self.BASIC_BOUNDS if basic else self.BOUNDS
        origin_x, origin_y = round(bounds[0]*scale_x), round(bounds[1]*scale_y)
        width = round(bounds[2]*scale_x)-origin_x
        height = round(bounds[3]*scale_y)-origin_y
        aa = 3 if width*height <= 120_000 else 2
        frame = Image.new('RGBA', (width*aa, height*aa))
        draw = ImageDraw.Draw(frame)

        def point(x, y):
            if basic:
                x = self.basic_x(x)
                y = 7+(y-43)*116/80
            return ((x*scale_x-origin_x)*aa, (y*scale_y-origin_y)*aa)

        def known(value):
            return type(value) in (int, float) and isfinite(value)

        rows = [row for row in rows if known(row.get('session_time'))]
        now = rows[-1]['session_time'] if rows else 0
        for y in (43, 83, 123):
            draw.line((point(6, y), point(342, y)), fill=colors['border'], width=max(1, round(aa*min(scale_x, scale_y))))
        live = {channel: None for channel in ('brake', 'throttle')}
        options = options or {}
        window = options.get('window', 6)
        channels = [('throttle', 372), ('brake', 352)]+([('steering', None)] if steering else [])
        for channel, bar_x in channels:
            if not options.get(channel, True):
                continue
            segment, previous = [], None

            def paint():
                if len(segment) >= 2:
                    draw.line(segment, fill=colors[channel], width=max(1, round(2*aa*min(scale_x, scale_y))), joint='curve')

            for row in rows:
                at, value = row['session_time'], row.get(channel)
                if channel == 'steering':
                    angle = row.get('steering_rad')
                    value = min(1, abs(degrees(angle))/30) if known(angle) else None
                if not known(value) or not 0 <= now-at <= window or (previous is not None and not 0 < at-previous <= .5):
                    paint(); segment = []
                if known(value) and 0 <= now-at <= window:
                    segment.append(point(342-(now-at)/window*336, 123-max(0, min(1, value))*80))
                previous = at
            paint()
            value = rows[-1].get(channel) if rows else None
            if channel == 'steering':
                angle = rows[-1].get('steering_rad') if rows else None
                live[channel] = abs(degrees(angle)) if known(angle) else None
                continue
            if known(value):
                live[channel] = max(0, min(1, value))
                if live[channel] > 0:
                    draw.rectangle((point(bar_x, 123-live[channel]*80), point(bar_x+10, 123)), fill=colors[channel])
        return frame.resize((width, height), Image.Resampling.LANCZOS), live

    def history(self, goal, start, end, reference):
        """Keep sample positions fixed in track space as the viewport moves."""
        key = goal.key, reference
        if key not in self.samples:
            self.samples[key] = tuple(inputs(goal, distance, reference)
                                      for distance in range(int(LENGTH_M)))
        stored = self.samples[key]
        yield start, inputs(goal, start, reference)
        for distance in range(floor(start)+1, ceil(end)):
            yield distance, stored[distance % len(stored)]
        yield end, inputs(goal, end, reference)

    def comparison_frame(self, comparison, rows, scale_x, scale_y, colors, steering=False, basic=False, options=None):
        """Real inputs and a real reference share a position-aligned reference time axis."""
        bounds = self.BASIC_BOUNDS if basic else self.BOUNDS
        origin_x, origin_y = round(bounds[0]*scale_x), round(bounds[1]*scale_y)
        width, height = round(bounds[2]*scale_x)-origin_x, round(bounds[3]*scale_y)-origin_y
        aa = 3 if width*height <= 120_000 else 2
        frame = Image.new('RGBA', (width*aa, height*aa))
        draw = ImageDraw.Draw(frame)
        scale = min(scale_x, scale_y)
        options = options or {}
        window = options.get('window', 10)
        future = 2 if options.get('future', True) else 0
        def point(x, y):
            if basic:
                x = self.basic_x(x)
                y = 7+(y-43)*116/80
            return ((x*scale_x-origin_x)*aa, (y*scale_y-origin_y)*aa)
        def thickness(size):
            return max(1, round(size*scale*aa))
        def known(value):
            return type(value) in (int, float) and isfinite(value)
        for y in (43, 83, 123):
            draw.line((point(6, y), point(342, y)), fill=colors['border'], width=thickness(1))

        def segments(points, channel):
            segment = []
            for row in points:
                offset, value = row.get('offset'), row.get(channel)
                if channel == 'steering':
                    angle = row.get('steering_deg')
                    value = min(1, abs(angle)/30) if known(angle) else None
                valid = known(offset) and -window <= offset <= future and known(value) and 0 <= value <= 1
                if row.get('break') or not valid:
                    if len(segment) >= 2:
                        yield segment
                    segment = []
                if valid:
                    segment.append(point(6+(offset+window)/(window+future)*336, 123-value*80))
            if len(segment) >= 2:
                yield segment

        channels = ('throttle', 'brake', 'steering') if steering else ('throttle', 'brake')
        channels = tuple(channel for channel in channels if options.get(channel, True))
        for channel in channels:
            for segment in segments(comparison['reference'], channel):
                base_y = point(0, 123)[1]
                draw.polygon([(segment[0][0], base_y), *segment, (segment[-1][0], base_y)],
                             fill=colors[f'reference_{channel}'])
        for channel in channels:
            for segment in segments(comparison['current'], channel):
                draw.line(segment, fill=colors[f'halo_{channel}'], width=thickness(4.2), joint='curve')
                draw.line(segment, fill=colors[channel], width=thickness(2), joint='curve')
        cursor = 6+window/(window+future)*336
        draw.line((point(cursor, 40), point(cursor, 123)), fill=colors['text'], width=thickness(1))
        live = {}
        for channel, x in (('brake', 352), ('throttle', 372)):
            value = rows[-1].get(channel) if rows else None
            live[channel] = max(0, min(1, value)) if known(value) else None
            if options.get(channel, True) and live[channel] is not None and live[channel] > 0:
                draw.rectangle((point(x, 123-live[channel]*80), point(x+10, 123)), fill=colors[channel])
        if steering:
            angle = rows[-1].get('steering_rad') if rows else None
            live['steering'] = abs(degrees(angle)) if known(angle) else None
        return frame.resize((width, height), Image.Resampling.LANCZOS), live

    def frame(self, goal, position, scale_x, scale_y, colors):
        origin_x, origin_y = round(self.BOUNDS[0]*scale_x), round(self.BOUNDS[1]*scale_y)
        width = round(self.BOUNDS[2]*scale_x)-origin_x
        height = round(self.BOUNDS[3]*scale_y)-origin_y
        # Supersample the geometry, keeping large previews within a frame budget.
        aa = 3 if width*height <= 120_000 else 2
        frame = Image.new("RGBA", (width*aa, height*aa))
        draw = ImageDraw.Draw(frame)
        scale = min(scale_x, scale_y)

        def point(x, y):
            return ((x*scale_x-origin_x)*aa, (y*scale_y-origin_y)*aa)

        def thickness(size):
            return max(1, round(size*scale*aa))

        left, right, top, bottom = 6, 342, 43, 123
        current_x = left+(right-left)*10/12
        channels = ("throttle", "brake", "steering") if goal.key == "steering" else ("throttle", "brake")
        live = inputs(goal, position)
        before = position-250
        reference = tuple(self.history(goal, before, position+50, True))
        current = tuple(self.history(goal, before, position, False))
        for y in (top, (top+bottom)/2, bottom):
            draw.line((point(left, y), point(right, y)), fill=colors["border"], width=thickness(1))

        def trace(rows, channel):
            maximum = 30 if channel == "steering" else 1
            return [point(left+(right-left)*(distance-before)/300,
                          bottom-values[channel]/maximum*(bottom-top))
                    for distance, values in rows]

        # Paint all reference areas first so they cannot cover a current input line.
        for channel in channels:
            points = [point(left, bottom), *trace(reference, channel), point(right, bottom)]
            draw.polygon(points, fill=colors[f"reference_{channel}"])
        for channel in channels:
            points = trace(current, channel)
            draw.line(points, fill=colors[f"halo_{channel}"], width=thickness(4.2), joint="curve")
            draw.line(points, fill=colors[channel], width=thickness(2), joint="curve")
            x, y = points[-1]
            radius = 2.2*scale*aa
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=colors[channel])
        draw.line((point(current_x, top-3), point(current_x, bottom)),
                  fill=colors["text"], width=thickness(1))
        for x, channel in ((352, "brake"), (372, "throttle")):
            value = live[channel]
            if value > 0:
                draw.rectangle((point(x, bottom-value*(bottom-top)), point(x+10, bottom)),
                               fill=colors[channel],
                               outline=colors["warning"] if channel == "brake" and live["abs"] else None,
                               width=thickness(1))
        return frame.resize((width, height), Image.Resampling.LANCZOS), live
