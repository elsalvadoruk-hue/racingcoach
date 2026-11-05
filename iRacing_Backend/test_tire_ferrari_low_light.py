import asyncio
import sys
import unittest

import cv2
import numpy as np
from tire_reader.multi_dash_locator import MultiCarDashLocator, ocr_frame
from tire_reader.pressure_ocr import WindowsPressureOcr, load_image, make_profile
from tire_reader.vehicle_dash import ROOT


class FerrariLowLightTests(unittest.TestCase):
    def test_location_is_revalidated_in_darkness_and_never_returned_for_black_frame(self):
        image, _ = load_image(ROOT / 'samples/gt3_dash/ferrari_296_p7.png')
        locator = MultiCarDashLocator('ferrari296gt3')
        self.assertEqual(locator.locate(image)['status'], 'located')
        for frame in (np.uint8(image * .2), np.uint8((image / 255.) ** 2 * 255)):
            self.assertEqual(locator.locate(frame)['status'], 'located')
        self.assertIsNone(locator.locate(np.zeros_like(image))['location'])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows OCR')
    def test_ferrari_colored_cells_read_all_four_pressures_and_temperatures(self):
        image, _ = load_image(ROOT / 'samples/gt3_dash/ferrari_296_p7.png')
        found = MultiCarDashLocator('ferrari296gt3').locate(image)
        frame, pressure, temperatures = ocr_frame(image, found['location'])
        profile = make_profile(frame, '', '', pressure, 'bar', 2, car_path='ferrari296gt3',
            temperature_boxes=temperatures, temperature_unit='F', automatic_partial=True, screen_unit='auto')
        result = asyncio.run(WindowsPressureOcr().recognize(frame, profile))
        for wheel in ('FL', 'FR', 'RL', 'RR'):
            self.assertAlmostEqual(result['wheels'][wheel]['kpa'], 23.9 * 6.894757293)
            self.assertAlmostEqual(result['temperatures'][wheel]['celsius'], (111 - 32) * 5 / 9)
