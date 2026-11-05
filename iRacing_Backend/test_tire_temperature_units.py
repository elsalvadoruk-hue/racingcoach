import asyncio,unittest
from unittest.mock import patch
import numpy as np
from test_tire_pressure_ocr import fixture
from tire_reader.pressure_ocr import WindowsPressureOcr,parse_auto_pressure,parse_temperature
from tire_reader.pressure_display import PressureDisplay


class TemperatureUnitTests(unittest.TestCase):
    def test_psi_and_fahrenheit_ocr_end_as_bar_and_celsius(self):
        profile=fixture();profile.update(screen_unit='auto',temperature_unit='F',
            temperature_boxes=dict(FL=[0,32,60,48],FR=[80,32,140,48],RL=[0,82,60,98],RR=[80,82,140,98]))
        engine=object.__new__(WindowsPressureOcr)
        engine.language='synthetic';engine.languages=[];engine.max_dimension=2048
        texts=iter(['25.0']*24+['122']*24)
        async def text(_):return next(texts)
        with patch.object(engine,'_text',side_effect=text):
            result=asyncio.run(engine.recognize(np.zeros((100,160,3),dtype=np.uint8),profile))
        display=PressureDisplay('psi',1)
        display.update(dict(status='reading',**{'raw_kpa_'+w:v['kpa'] for w,v in result['wheels'].items()},
            **{'raw_temp_c_'+w:v['celsius'] for w,v in result['temperatures'].items()}))
        self.assertEqual(set(display.formatted().values()),{'1.72'})
        self.assertEqual(set(display.formatted_temperatures().values()),{'50°C'})
        self.assertEqual(display.unit,'bar')

    def test_bar_celsius_zero_and_negative_temperatures_are_valid(self):
        self.assertEqual(parse_auto_pressure('1.59'),(1.59,159))
        self.assertEqual(parse_temperature('0','C'),(0,0))
        self.assertEqual(parse_temperature('32','F'),(32,0))
        self.assertEqual(parse_temperature('-40','F'),(-40,-40))
        self.assertIsNone(parse_temperature('122','unknown'))
        for text in ['239','159','1.5','l.59','23.9 23.9']:
            self.assertIsNone(parse_auto_pressure(text))

    def test_temperature_and_pressure_failures_hold_independently_until_next_success(self):
        display=PressureDisplay()
        display.update(dict(status='reading',raw_kpa_FL=159,raw_temp_c_FL=50))
        display.update(dict(status='reading',raw_kpa_FL=None,raw_temp_c_FL=51))
        self.assertEqual((display.formatted()['FL'],display.formatted_temperatures()['FL']),('1.59','51°C'))
        display.update(dict(status='reading',raw_kpa_FL=160,raw_temp_c_FL=None))
        self.assertEqual((display.formatted()['FL'],display.formatted_temperatures()['FL']),('1.60','51°C'))
        display.update(dict(status='weak_geometry',raw_kpa_FL=180,raw_temp_c_FL=90))
        self.assertEqual((display.formatted()['FL'],display.formatted_temperatures()['FL']),('1.60','51°C'))
        display.reset()
        self.assertEqual((display.formatted()['FL'],display.formatted_temperatures()['FL']),('—','—'))


if __name__=='__main__':unittest.main()
