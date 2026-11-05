import json
from pathlib import Path
import tempfile
import unittest

from tire_reader.positions import TirePositions


class TirePositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tires-position.json'

    def test_cars_restore_independent_positions_including_negative_monitors(self):
        store = TirePositions(self.path)
        store.save('bmwm4gt3', (-1200, 80))
        store.save('ferrari296gt3', (800, 700))
        reopened = TirePositions(self.path)
        self.assertEqual(reopened.load('bmwm4gt3'), (-1200, 80))
        self.assertEqual(reopened.load('ferrari296gt3'), (800, 700))
        self.assertIsNone(reopened.load('mclaren720sgt3'))

    def test_legacy_position_is_inherited_without_overwriting_other_cars(self):
        self.path.write_text('{"x":30,"y":40}')
        store = TirePositions(self.path)
        self.assertEqual(store.load('bmwm4gt3'), (30, 40))
        store.save('bmwm4gt3', (200, 300))
        self.assertEqual(store.load('mclaren720sgt3'), (30, 40))
        self.assertEqual(store.load('bmwm4gt3'), (200, 300))
        store.reset('bmwm4gt3')
        self.assertEqual(store.load('bmwm4gt3'), (30, 40))

    def test_reset_default_keeps_car_positions_and_legacy_reset_removes_file(self):
        store = TirePositions(self.path)
        store.save(None, (20, 20))
        store.save('bmwm4gt3', (90, 80))
        store.reset()
        self.assertEqual(store.load('bmwm4gt3'), (90, 80))
        self.assertIsNone(store.load(None))
        store.reset('bmwm4gt3')
        self.assertFalse(self.path.exists())

    def test_invalid_coordinates_and_car_keys_cannot_create_paths(self):
        store = TirePositions(self.path)
        for car, xy in (('../outside', (2, 3)), ('bmwm4gt3', (True, 5)),
                        ('bmwm4gt3', (900000, 1))):
            with self.subTest(car=car, xy=xy), self.assertRaises(ValueError):
                store.save(car, xy)
        self.assertFalse(self.path.exists())
        self.path.write_text(json.dumps({'version': 2, 'cars': {'bmwm4gt3': {'x': 1, 'y': 'x'}}}))
        self.assertIsNone(store.load('bmwm4gt3'))
