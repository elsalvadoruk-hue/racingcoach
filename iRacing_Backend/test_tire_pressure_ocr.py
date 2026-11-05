import asyncio
import unittest
from copy import deepcopy
from unittest.mock import patch

from tire_reader.pressure_ocr import (WindowsPressureOcr, make_profile, parse_pressure,
                                   validate_profile)


def fixture():
    return dict(schema=1, car_path='bmwm4gt3', image_size=[160, 100], unit='bar', decimals=2,
        boxes=dict(FL=[0, 0, 60, 30], FR=[80, 0, 140, 30],
                   RL=[0, 50, 60, 80], RR=[80, 50, 140, 80]))


class PressureTests(unittest.TestCase):
    def test_unit_conversion_matches_actual_bmw_cold_setting(self):
        self.assertAlmostEqual(parse_pressure('23.0', 'psi', 1)[1], 158.579417739)
        self.assertEqual(parse_pressure('1.59', 'bar', 2), (1.59, 159.0))
        self.assertEqual(parse_pressure('1 , 59', 'bar', 2), (1.59, 159.0))

    def test_ambiguous_and_incomplete_numbers_are_not_repaired(self):
        for text in ('159', '1.5', 'l.59', '1.59 1.59', 'FL 1.59', '-1.59', '15.90', '', 'nan'):
            with self.subTest(text=text):
                self.assertIsNone(parse_pressure(text, 'bar', 2))
        self.assertIsNone(parse_pressure('1.59', 'unknown', 2))

    def test_missing_wheel_overlap_outside_and_unknown_unit_are_rejected(self):
        original=fixture()
        self.assertEqual(validate_profile(original), original)
        for mutate in (lambda p:p['boxes'].pop('RR'),
                       lambda p:p['boxes'].update(RR=p['boxes']['FL']),
                       lambda p:p['boxes'].update(RR=[0, 0, 999, 50]),
                       lambda p:p.update(unit='auto'),
                       lambda p:p.update(decimals=True)):
            value=deepcopy(original)
            mutate(value)
            with self.assertRaises(ValueError): validate_profile(value)

    def test_recognition_disagreement_is_null_and_resize_needs_recalibration(self):
        import numpy as np
        engine=object.__new__(WindowsPressureOcr)
        engine.language='synthetic';engine.languages=['synthetic'];engine.max_dimension=2048
        async def text(_): return next(values)
        values=iter(['1.59','1.69','1.59','','','', '1.59','1.59','','','','', '', '', '', '', '', '', '1.59','garbled','','','',''])
        image=np.zeros((100,160,3),dtype=np.uint8)
        with patch.object(engine,'_text',side_effect=text):
            result=asyncio.run(engine.recognize(image,fixture()))
        self.assertEqual(result['wheels']['FL']['status'],'ambiguous')
        self.assertIsNone(result['wheels']['FL']['kpa'])
        self.assertEqual(result['wheels']['FR']['kpa'],159)
        self.assertIsNone(result['wheels']['RL']['value'])
        self.assertEqual(result['wheels']['RR']['status'],'single_variant')
        self.assertIsNone(result['wheels']['RR']['value'])
        with self.assertRaisesRegex(ValueError,'해상도'):
            asyncio.run(engine.recognize(image[:90],fixture()))

    def test_ferrari_requires_independent_segmentations_without_conflicting_numbers(self):
        import numpy as np
        engine=object.__new__(WindowsPressureOcr)
        engine.language='synthetic';engine.languages=['synthetic'];engine.max_dimension=2048
        values=iter(['1.59','1.59','1.69','','','','','',
                     '1.59','1.59','','','','','','',
                     '1.59','','1.59','','','','','',
                     '','','','','','','',''])
        async def text(_):return next(values)
        profile=fixture();profile['car_path']='ferrari296gt3'
        with patch.object(engine,'_text',side_effect=text):
            result=asyncio.run(engine.recognize(np.zeros((100,160,3),dtype=np.uint8),profile))
        self.assertEqual(result['wheels']['FL']['status'],'ambiguous')
        self.assertIsNone(result['wheels']['FR']['kpa'])
        self.assertEqual(result['wheels']['RL']['kpa'],159)

    def test_cup_explicit_kpa_keeps_agreement_and_conflict_checks(self):
        import itertools
        import numpy as np
        profile=fixture();profile.update(car_path='porsche9922cup',unit='kPa',decimals=0,screen_unit='kPa')
        engine=object.__new__(WindowsPressureOcr)
        engine.language='synthetic';engine.languages=[];engine.max_dimension=2048
        for readings,expected,status in [(['159','159',''],159,'recognized'),
                (['159','59','159'],None,'ambiguous'),
                (['159','',''],None,'single_variant'),
                (['1.59','23.9',''],None,'unreadable')]:
            with self.subTest(readings=readings):
                # Separate each wheel's evidence from the next wheel.
                async def text(_):return next(values)
                for wheel in profile['boxes']:
                    single=dict(profile,boxes={wheel:profile['boxes'][wheel]},automatic_partial=True)
                    values=itertools.chain(readings,itertools.repeat(''))
                    with patch.object(engine,'_text',side_effect=text):
                        result=asyncio.run(engine.recognize(np.zeros((100,160,3),dtype=np.uint8),single))
                    self.assertEqual(result['wheels'][wheel]['kpa'],expected)
                    self.assertEqual(result['wheels'][wheel]['status'],status)

    def test_ferrari_temperature_keeps_valid_conflicts_and_rejects_single_readings(self):
        import numpy as np
        profile=fixture();profile.update(car_path='ferrari296gt3',automatic_partial=True,
            boxes={},temperature_boxes=profile['boxes'],temperature_unit='C')
        engine=object.__new__(WindowsPressureOcr)
        engine.language='synthetic';engine.languages=[];engine.max_dimension=2048
        values=iter(['50','50','60','50', '48','48','4B','',
                     '48','','4B','', '49','49','49','49'])
        async def text(_):return next(values)
        with patch.object(engine,'_text',side_effect=text):
            result=asyncio.run(engine.recognize(np.zeros((100,160,3),dtype=np.uint8),profile))
        self.assertEqual(result['temperatures']['FL']['status'],'ambiguous')
        self.assertIsNone(result['temperatures']['FL']['celsius'])
        self.assertEqual(result['temperatures']['FR']['celsius'],48)
        self.assertEqual(result['temperatures']['RL']['status'],'single_variant')
        self.assertIsNone(result['temperatures']['RL']['celsius'])
        self.assertEqual(result['temperatures']['RR']['celsius'],49)


if __name__=='__main__': unittest.main()
