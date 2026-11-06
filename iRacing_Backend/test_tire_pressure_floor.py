"""Per-car minimum cold pressure learned from .ibt session information (no typing needed)."""
import os
from pathlib import Path
import struct
import tempfile
import unittest

from tire_reader import pressure_advice as advice
from tire_reader.pressure_floor import PressureFloors, read_ibt_setup
from tire_reader.snapshot import WHEELS

SESSION = """---
DriverInfo:
 DriverCarIdx: 1
 Drivers:
 - CarIdx: 0
   CarPath: someoneelse
 - CarIdx: 1
   CarPath: bmwm4gt3
CarSetup:
 Tires:
  LeftFront:
   StartingPressure: {p} kPa
  RightFront:
   StartingPressure: {p} kPa
  LeftRear:
   StartingPressure: {p} kPa
  RightRear:
   StartingPressure: {p} kPa
...
"""


def write_ibt(path, pressure=159):
    text = SESSION.format(p=pressure).encode('latin-1')
    header = struct.pack('<10i', 2, 1, 60, 1, len(text), 112, 0, 0, 1, 4).ljust(112, b'\0')
    Path(path).write_bytes(header + text + b'\0' * 64)


class FloorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'telemetry'; self.folder.mkdir()
        self.cache = Path(temp.name) / 'overlay/iracing/tires-floors.json'

    def test_reads_the_driver_car_and_setup_cold_pressures_from_the_header(self):
        write_ibt(self.folder / 'a.ibt')
        self.assertEqual(read_ibt_setup(self.folder / 'a.ibt'), ('bmwm4gt3', dict.fromkeys(WHEELS, 159.)))
        (self.folder / 'broken.ibt').write_bytes(b'\0' * 20)
        self.assertIsNone(read_ibt_setup(self.folder / 'broken.ibt'))

    def test_scan_caches_files_and_reads_only_new_or_changed_ones(self):
        write_ibt(self.folder / 'a.ibt', 162); write_ibt(self.folder / 'b.ibt', 159)
        (self.folder / 'damaged.ibt').write_bytes(b'\0' * 200)
        floors = PressureFloors(self.cache, [self.folder])
        self.assertEqual(floors.scan(), 3)
        self.assertEqual(floors.lowest('bmwm4gt3'), (dict.fromkeys(WHEELS, 159.), 2))
        self.assertEqual(floors.scan(), 0)
        write_ibt(self.folder / 'c.ibt', 155)
        again = PressureFloors(self.cache, [self.folder])  # cache survives a restart
        self.assertEqual(again.scan(), 1)
        self.assertEqual(again.lowest('BMWM4GT3')[0]['RR'], 155.)
        self.assertEqual(again.status(), {'files': 4, 'cars': 1, 'scanning': False})

    def test_background_scan_is_rate_limited(self):
        now = [0.]
        floors = PressureFloors(self.cache, [self.folder], clock=lambda: now[0])
        floors.scan()
        self.assertFalse(floors.scan_in_background())
        now[0] = 31 * 60
        self.assertTrue(floors.scan_in_background())
        import time
        end = time.monotonic() + 5
        while floors.status()['scanning'] and time.monotonic() < end:  # finish before the folder goes
            time.sleep(.01)
        self.assertFalse(floors.status()['scanning'])

    def test_file_minimum_feeds_the_advice_floor(self):
        merged = advice.merge_lowest({'FL': 165.}, dict.fromkeys(WHEELS, 150.))
        self.assertEqual(merged['FL'], 150.)
        self.assertEqual(advice.floors({}, merged, advice.default_floor('iracing', 'bmwm4gt3'))['FL'], (150., 'lowest_used'))

    def test_only_the_iracing_controller_learns_from_files(self):
        from tire_overlay_control import TireOverlays
        with tempfile.TemporaryDirectory() as temp:
            overlays = TireOverlays(telemetry_dirs=[self.folder])
            for controller in overlays.controllers.values():
                controller.path = Path(temp) / controller.game / 'tires.json'
            self.assertIsNotNone(overlays.controllers['iracing']._floors())
            self.assertIsNone(overlays.controllers['lmu']._floors())


if __name__ == '__main__':
    unittest.main()
