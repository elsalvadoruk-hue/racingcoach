from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from tire_reader.targets import TireTargets
from tire_overlay_control import TireTargetSettings
from test_tire_advisor import snapshot


class TargetTests(unittest.TestCase):
    def test_explicit_ranges_are_per_car_preserve_color_and_temperature_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            store=TireTargets(Path(temp)/'targets.json')
            store.save('mclaren720sgt3',dict(pressure_low_bar=1.4,pressure_high_bar=1.6,
                temp_low_c=70,temp_high_c=85,temperature_kind='carcass'))
            s=snapshot(state=None)
            result=store.apply(replace(s,wheels={w:replace(v,temp_state=None) for w,v in s.wheels.items()}))
            self.assertEqual(result.wheels['FL'].pressure_state,'high')
            self.assertEqual(result.wheels['FL'].temp_state,'hot')
            self.assertEqual(store.apply(snapshot(state='low')).wheels['FL'].pressure_state,'low')
            self.assertIsNone(store.apply(replace(s,car='bmwm4gt3')).wheels['FL'].pressure_state)
            other=replace(s,wheels={w:replace(v,temp_kind='surface',temp_state=None) for w,v in s.wheels.items()})
            self.assertIsNone(store.apply(other).wheels['FL'].temp_state)
            store.save('mclaren720sgt3',{})
            self.assertIsNone(store.apply(s).wheels['FL'].pressure_state)

    def test_api_values_require_pairs_and_real_bounded_numbers(self):
        for patch in ({'pressure_low_bar':1.5},{'pressure_low_bar':2.,'pressure_high_bar':1.},
                      {'temp_low_c':80.,'temp_high_c':80.},{'pressure_low_bar':True},
                      {'pressure_low_bar':'1.4','pressure_high_bar':1.6},
                      {'temp_low_c':float('nan'),'temp_high_c':100.}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                TireTargetSettings(car='bmwm4gt3',**patch)
        self.assertEqual(TireTargetSettings(car='bmwm4gt3').model_dump()['pressure_low_bar'],None)
