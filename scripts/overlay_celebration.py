"""Small, deterministic confetti animation for the offline coaching HUD."""
from __future__ import annotations

from math import cos, pi, sin
from random import Random

from PIL import Image, ImageDraw


class CelebrationRenderer:
    DURATION = 3.2

    def __init__(self):
        random = Random(51)
        # Store trajectories once; advancing time never generates new noise.
        self.particles = tuple(
            (index % 2, random.uniform(0, 0.28), random.uniform(35, 190),
             random.uniform(-135, -45), random.uniform(0.65, 1.25),
             random.uniform(0, 2*pi), random.uniform(-4, 4), index % 4)
            for index in range(64)
        )

    def frame(self, elapsed, scale_x, scale_y, colors):
        width, height = round(390*scale_x), round(150*scale_y)
        aa = 3 if width*height <= 180_000 else 2
        frame = Image.new("RGBA", (width*aa, height*aa))
        draw = ImageDraw.Draw(frame)
        scale = min(scale_x, scale_y)
        palette = tuple(colors[key] for key in ("success", "accent", "target", "brake"))
        fade = max(0, min(1, (self.DURATION-elapsed)/0.55))

        def point(x, y):
            return x*scale_x*aa, y*scale_y*aa

        def ink(color, alpha=255):
            return (*tuple(bytes.fromhex(color.lstrip("#"))), round(alpha*fade))

        for side, delay, velocity_x, velocity_y, size, angle, spin, color in self.particles:
            age = elapsed-delay
            if age < 0:
                continue
            x = 8+velocity_x*age if side == 0 else 382-velocity_x*age
            y = 142+velocity_y*age+48*age*age
            # Leave a quiet center behind the result and goal name.
            if 61 < x < 329 and 51 < y < 115:
                continue
            if not (-8 < x < 398 and -8 < y < 158):
                continue
            rotation = angle+spin*age
            corners = []
            for dx, dy in ((-3, -1.2), (3, -1.2), (3, 1.2), (-3, 1.2)):
                corners.append(point(x+size*(dx*cos(rotation)-dy*sin(rotation)),
                                     y+size*(dx*sin(rotation)+dy*cos(rotation))))
            draw.polygon(corners, fill=ink(palette[color]))

        # A single expanding ring settles into the completion check; no flashing.
        entrance = min(1, max(0, elapsed)/0.4)
        radius = 13+7*(1-entrance)**3
        draw.ellipse((*point(195-radius, 29-radius), *point(195+radius, 29+radius)),
                     outline=ink(colors["success"]), width=max(1, round(1.7*scale*aa)))
        draw.line([point(188, 29), point(193, 34), point(202, 24)],
                  fill=ink(colors["success"]), width=max(1, round(2.4*scale*aa)), joint="curve")
        for index in range(5):
            x = 171+index*12
            draw.ellipse((*point(x-2.6, 130-2.6), *point(x+2.6, 130+2.6)),
                         fill=ink(colors["success"]))
        return frame.resize((width, height), Image.Resampling.LANCZOS)
