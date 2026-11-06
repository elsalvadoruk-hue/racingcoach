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


def write_lmu(path, entry='Team WRT 2026 #69:LM', pressure='136 kPa', available=True):
    import duckdb, json
    setup = {key: {'available': available, 'key': key, 'minValue': 0, 'maxValue': 75, 'value': 0, 'stringValue': pressure}
             for key in ('WM_PRESSURE-W_FL', 'WM_PRESSURE-W_FR', 'WM_PRESSURE-W_RL', 'WM_PRESSURE-W_RR')}
    setup.update({f'VM_SETTING_{i}': {'available': True, 'minValue': 0, 'maxValue': i} for i in range(25)})
    connection = duckdb.connect(str(path))
    connection.execute('create table metadata (key varchar, value varchar)')
    connection.executemany('insert into metadata values (?, ?)', [('CarName', entry), ('CarClass', 'GT3'),
                           ('CarSetup', json.dumps(setup)), ('SteamID', 'not read')])
    connection.close()


class LmuFloorTests(unittest.TestCase):
    def setUp(self):
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest('duckdb is not installed')
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'Telemetry'; self.folder.mkdir()
        self.cache = Path(temp.name) / 'overlay/lmu/tires-floors.json'
        self.models = Path(temp.name) / 'lmu-car-models.json'
        old = os.environ.get('IRACING_LMU_CAR_MODELS_PATH')
        os.environ['IRACING_LMU_CAR_MODELS_PATH'] = str(self.models)
        self.addCleanup(lambda: os.environ.__setitem__('IRACING_LMU_CAR_MODELS_PATH', old) if old is not None
                        else os.environ.pop('IRACING_LMU_CAR_MODELS_PATH', None))

    def test_reads_the_setup_cold_pressures_and_units_from_the_metadata(self):
        from tire_reader.pressure_floor import read_lmu_setup, lmu_pressure_kpa
        write_lmu(self.folder / 'a.duckdb')
        entry, cold, extra = read_lmu_setup(self.folder / 'a.duckdb')
        self.assertEqual((entry, cold), ('Team WRT 2026 #69:LM', dict.fromkeys(WHEELS, 136.)))
        self.assertTrue(extra['fingerprint'].startswith('lmu-model-'))
        self.assertEqual((lmu_pressure_kpa('19.7 psi'), lmu_pressure_kpa('1.36 bar'), lmu_pressure_kpa('N/A')), (135.8, 136., None))
        write_lmu(self.folder / 'na.duckdb', pressure='N/A')
        self.assertIsNone(read_lmu_setup(self.folder / 'na.duckdb'))
        write_lmu(self.folder / 'off.duckdb', available=False)
        self.assertIsNone(read_lmu_setup(self.folder / 'off.duckdb'))

    def test_liveries_of_one_model_share_the_live_car_key(self):
        import lmu_car_models
        from tire_reader.pressure_floor import read_lmu_setup, lmu_car
        write_lmu(self.folder / 'a.duckdb', 'Team WRT 2026 #69:LM', '140 kPa')
        write_lmu(self.folder / 'b.duckdb', 'Team WRT 2026 #32:LM', '136 kPa')
        (self.folder / 'damaged.duckdb').write_bytes(b'\0' * 64)
        floors = PressureFloors(self.cache, [self.folder], reader=read_lmu_setup, pattern='*.duckdb', resolve=lmu_car)
        self.assertEqual(floors.scan(), 3)
        # Before the model is known, a recording is only its livery; nothing is guessed.
        self.assertEqual(floors.lowest('lmu-car-bmw-m4-lmgt3'), (dict.fromkeys(WHEELS), 0))
        lmu_car_models.learn('Team WRT 2026 #69:LM', model='BMW M4 LMGT3')   # seen live once
        fingerprint = read_lmu_setup(self.folder / 'a.duckdb')[2]['fingerprint']
        lmu_car_models.learn('Team WRT 2026 #69:LM', fingerprint=fingerprint)
        lmu_car_models.learn('Team WRT 2026 #32:LM', fingerprint=fingerprint)
        self.assertEqual(floors.lowest('lmu-car-bmw-m4-lmgt3'), (dict.fromkeys(WHEELS, 136.), 2))
        self.assertEqual(floors.status()['cars'], 1)
