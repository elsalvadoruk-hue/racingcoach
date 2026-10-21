"""LMU entries (team, year, number) of one car model compare as the same car."""
import json
import unittest

from lmu_telemetry import car_model_key
from personal_reference import identity


def setup(gear_max=10, fuel_max=100, value=3):
    items = {f'VM_SETTING_{n}': {'available': True, 'minValue': 0, 'maxValue': 20, 'value': value} for n in range(25)}
    items['VM_GEAR_1'] = {'available': True, 'minValue': 0, 'maxValue': gear_max, 'value': value}
    items['VM_FUEL_LEVEL'] = {'available': True, 'minValue': 0, 'maxValue': fuel_max, 'value': value}
    items['symmetric'] = True
    return json.dumps(items)


class CarModelKeyTests(unittest.TestCase):
    def test_same_garage_ranges_with_other_values_or_fuel_allowance_are_one_model(self):
        self.assertEqual(car_model_key(setup(value=3), 'GT3'), car_model_key(setup(value=9, fuel_max=80), 'GT3'))

    def test_other_ranges_or_class_are_another_model(self):
        self.assertNotEqual(car_model_key(setup(), 'GT3'), car_model_key(setup(gear_max=12), 'GT3'))
        self.assertNotEqual(car_model_key(setup(), 'GT3'), car_model_key(setup(), 'Hyper'))

    def test_missing_or_unreadable_setup_has_no_model(self):
        self.assertIsNone(car_model_key(None, 'GT3'))
        self.assertIsNone(car_model_key('not json', 'GT3'))
        self.assertIsNone(car_model_key(json.dumps({'VM_A': {'available': True}}), 'GT3'))

    def test_reference_identity_uses_the_model_over_the_entry_name(self):
        key = car_model_key(setup(), 'GT3')
        a = identity({'car_name': 'Team WRT 2026 #32:LM', 'car_model_key': key, 'track_name': 'Monza'})
        b = identity({'car_name': 'Team WRT 2026 #31:LM', 'car_model_key': key, 'track_name': 'Monza'})
        self.assertEqual(a, b)
        self.assertNotEqual(identity({'car_name': 'Team WRT 2026 #32:LM', 'track_name': 'Monza'}),
                            identity({'car_name': 'Team WRT 2026 #31:LM', 'track_name': 'Monza'}))


if __name__ == '__main__':
    unittest.main()
