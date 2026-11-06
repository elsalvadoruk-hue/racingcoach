"""Dot confirmation, unit-mode formats, withheld reasons and the local digit reader.

Synthetic digits only (OpenCV Hershey font); no screen, game or Windows OCR.
"""
import asyncio
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from tire_reader import digit_model
from tire_reader.color_state import states
from tire_reader.digit_model import DigitReader
from tire_reader.glyphs import segment
from tire_reader.pressure_ocr import (AUTO_PRESSURE_FORMATS, WindowsPressureOcr, dot_recovery, parse_formats,
                                      parse_pressure, parse_temperature, withheld_reason)
from tire_reader.vehicle_dash import pressure_formats


def render(text, size=(140, 44)):
    image = np.zeros((size[1], size[0]), np.uint8)
    cv2.putText(image, text, (8, size[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 255, 2, cv2.LINE_AA)
    return image


def engine(texts):
    ocr = object.__new__(WindowsPressureOcr)
    ocr.language = 'synthetic'; ocr.languages = []; ocr.max_dimension = 2048
    values = iter(texts)
    async def text(_): return next(values)
    ocr._text = Mock(side_effect=text)
    return ocr


class DotAndFormatTests(unittest.TestCase):
    def test_segment_counts_digits_and_finds_one_baseline_dot(self):
        self.assertEqual((len(segment(render('24.1')).digits), segment(render('24.1')).dot), (3, 2))
        self.assertEqual(segment(render('1.80')).dot, 1)
        self.assertIsNone(segment(render('241')).dot)
        self.assertIsNone(segment(np.zeros((40, 100), np.uint8)))

    def test_dropped_dot_is_restored_only_where_the_pixels_show_it(self):
        psi = [('psi', 1)]
        self.assertEqual(dot_recovery(render('24.1'), ['241', '241', ''], psi), parse_pressure('24.1', 'psi', 1))
        self.assertEqual(dot_recovery(render('1.80'), ['180', '180'], [('bar', 2)]), (1.8, 180.0))
        for gray, texts, formats in [(render('241'), ['241', '241'], psi),        # no dot on screen
                                     (render('24.1'), ['241', '247'], psi),       # variants disagree
                                     (render('24.1'), ['241', ''], psi),          # one variant only
                                     (render('24.1'), ['241', '2A1'], psi),       # glyph noise
                                     (render('24.1'), ['2411', '2411'], psi),     # digit count differs
                                     (render('24.1'), ['241', '241'], [('bar', 2)]),  # dot in the wrong place
                                     (render('15.9'), ['159', '159'], [('kPa', 0)])]:  # integer format
            with self.subTest(texts=texts, formats=formats):
                self.assertIsNone(dot_recovery(gray, texts, formats))

    def test_unit_mode_formats_remove_noise_conflicts(self):
        self.assertEqual(pressure_formats('bmwm4gt3', 0), (('psi', 1),))
        self.assertEqual(pressure_formats('lamborghinievogt3', 0), (('psi', 2),))
        self.assertEqual(pressure_formats('mclaren720sgt3', 1), (('bar', 2),))
        self.assertEqual(pressure_formats('bmwm4gt3', None), AUTO_PRESSURE_FORMATS)
        self.assertIsNone(pressure_formats('unsupported', 0))
        # '23.91' is a valid psi(2) number under auto detection, so it conflicts with '23.9'.
        self.assertIsNotNone(parse_formats('23.91', AUTO_PRESSURE_FORMATS))
        self.assertIsNone(parse_formats('23.91', [('psi', 1)]))
        self.assertEqual(parse_formats('23.9', [('psi', 1)]), parse_pressure('23.9', 'psi', 1))

    def test_withheld_reasons_are_codes_without_text(self):
        for texts, valid, expected in [(['', ''], set(), 'empty'), (['241', '241'], set(), 'digits_without_decimal'),
                                       (['근4.3', ''], set(), 'non_digit_glyphs'), (['1.59', '1.69'], {1, 2}, 'format_conflict'),
                                       (['1.59', ''], {1}, 'single_variant'), (['1.5', '1.5'], set(), 'unreadable_format')]:
            self.assertEqual(withheld_reason(texts, valid), expected)

    def test_recognize_restores_dot_and_reports_reasons(self):
        image = np.zeros((44, 300, 3), np.uint8)
        cv2.putText(image, '24.1', (8, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(image, '24.1', (158, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        profile = dict(schema=1, car_path='bmwm4gt3', image_size=[300, 44], unit='bar', decimals=2,
                       automatic_partial=True, screen_unit='auto', pressure_formats=[['psi', 1]],
                       boxes=dict(FL=[0, 0, 140, 44], FR=[150, 0, 290, 44]))
        ocr = engine(['241'] * 6 + ['2A1', '', '', '', '', ''])
        result = asyncio.run(ocr.recognize(image, profile))
        self.assertEqual(result['wheels']['FL']['value'], 24.1)
        self.assertTrue(result['wheels']['FL']['dot_verified'])
        self.assertIsNone(result['wheels']['FL']['reason'])
        self.assertIsNone(result['wheels']['FR']['kpa'])
        self.assertEqual(result['wheels']['FR']['reason'], 'non_digit_glyphs')

    def test_ferrari_dot_recovery_needs_two_independent_segmentations(self):
        image = np.zeros((44, 150, 3), np.uint8)
        cv2.putText(image, '24.1', (8, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        profile = dict(schema=1, car_path='ferrari296gt3', image_size=[150, 44], unit='bar', decimals=2,
                       automatic_partial=True, screen_unit='auto', pressure_formats=[['psi', 1]],
                       boxes=dict(FL=[0, 0, 140, 44]))
        # Ferrari reads 8 variants in pairs per segmentation; one pair alone is not enough.
        alone = asyncio.run(engine(['241', '241'] + [''] * 6).recognize(image, profile))
        self.assertIsNone(alone['wheels']['FL']['kpa'])
        both = asyncio.run(engine(['241', '241', '241', ''] + [''] * 4).recognize(image, profile))
        self.assertEqual(both['wheels']['FL']['value'], 24.1)


class NeutralColorTests(unittest.TestCase):
    def test_plain_dark_ferrari_cell_is_ok_only_when_its_number_was_read(self):
        image = np.full((60, 60, 3), 12, np.uint8)
        box = {'FL': [0, 0, 60, 60]}
        self.assertEqual(states(image, 'ferrari296gt3', box, {}, {'pressure': {'FL'}})[0], {'FL': 'ok'})
        self.assertEqual(states(image, 'ferrari296gt3', box, {}, {'pressure': set()})[0], {'FL': None})
        self.assertEqual(states(image, 'ferrari296gt3', box, {}, None)[0], {'FL': None})
        image[:] = (200, 60, 20)  # blue background stays 'low'
        self.assertEqual(states(image, 'ferrari296gt3', box, {}, {'pressure': {'FL'}})[0], {'FL': 'low'})
        image[:] = 12  # cars without neutral-ok semantics never gain 'ok'
        self.assertEqual(states(image, 'mclaren720sgt3', box, {}, {'pressure': {'FL'}})[0], {'FL': None})


class AdvisorGapTests(unittest.TestCase):
    def test_spread_out_readings_confirm_but_long_gaps_do_not(self):
        from tire_reader.advisor import TireAdvisor
        from test_tire_advisor import snapshot
        for times, confirmed in (((0., 4., 9.), True), ((0., 1., 8.), False)):
            with self.subTest(times=times):
                now = [0.]
                advisor = TireAdvisor(clock=lambda: now[0])
                for t in times:
                    now[0] = t; advisor.observe(snapshot(t).to_dict(), enabled=True)
                self.assertEqual(advisor.current(times[-1]) is not None, confirmed)


PRESSURES = ['23.9', '24.1', '24.5', '25.0', '25.7', '26.2', '26.8', '27.3', '22.6', '28.4']
TEMPERATURES = ['85', '91', '103', '76', '64', '99', '87', '112', '70', '58']


class Dash:
    """Four pressure and four temperature cells; the teacher reads whatever is drawn."""
    def __init__(self):
        self.boxes = {w: [0, i * 44, 140, i * 44 + 44] for i, w in enumerate(('FL', 'FR', 'RL', 'RR'))}
        self.temps = {w: [150, i * 44, 290, i * 44 + 44] for i, w in enumerate(('FL', 'FR', 'RL', 'RR'))}
        self.profile = dict(schema=1, car_path='bmwm4gt3', image_size=[300, 176], unit='bar', decimals=2,
                            automatic_partial=True, screen_unit='auto', pressure_formats=[['psi', 1]],
                            boxes=self.boxes, temperature_boxes=self.temps, temperature_unit='C')
        self.calls = []
        self.lie = None

    def frame(self, pressure, temperature):
        self.shown = (pressure, temperature)
        image = np.zeros((176, 300, 3), np.uint8)
        for (x, y, *_), text in [(b, pressure) for b in self.boxes.values()] + [(b, temperature) for b in self.temps.values()]:
            cv2.putText(image, text, (x + 8, y + 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        return image

    def ocr(self, profile):
        self.calls.append(len(profile['boxes']) + len(profile['temperature_boxes']))
        pressure, temperature = self.lie or self.shown
        value = parse_pressure(pressure, 'psi', 1)
        temp = parse_temperature(temperature, 'C')
        return {'wheels': {w: dict(status='recognized', texts=[pressure, pressure], value=value[0], kpa=value[1],
                                   reason=None, dot_verified=False) for w in profile['boxes']},
                'temperatures': {w: dict(status='recognized', texts=[temperature] * 2, value=temp[0], celsius=temp[1],
                                         reason=None, dot_verified=False) for w in profile['temperature_boxes']},
                'elapsed_ms': 1.0}


@patch.multiple(digit_model, SWITCH_MIN_COMPARED=40, SWITCH_MIN_VALUES=4, VERIFY_EVERY=5)
class DigitReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'tires-digits'
        self.dash = Dash()

    def drive(self, reader, frames):
        results = []
        for pressure, temperature in frames:
            results.append(reader.read('bmwm4gt3', self.dash.frame(pressure, temperature), self.dash.profile, self.dash.ocr))
        return results

    def learn_until_switch(self, reader):
        # Each value is shown for two frames: only repeated OCR readings are learned.
        frames = [(p, t) for p, t in zip(PRESSURES, TEMPERATURES) for _ in range(2)] * 3
        self.drive(reader, frames)
        self.assertEqual(reader.status('bmwm4gt3')['mode'], 'self')

    def test_learns_from_teacher_then_reads_itself_with_periodic_checks(self):
        reader = DigitReader(self.folder)
        self.learn_until_switch(reader)
        self.dash.calls.clear()
        results = self.drive(reader, [('24.5', '91')] * 10)
        self.assertEqual({r['wheels']['FL']['source'] for r in results[:4]}, {'digit_model'})
        self.assertEqual([r['wheels'][w]['value'] for r in results for w in ('FL', 'RR')], [24.5] * 20)
        self.assertEqual({r['temperatures']['RL']['celsius'] for r in results}, {91.0})
        # OCR only for the periodic full check (every 5th frame in this test).
        self.assertEqual(self.dash.calls, [8, 8])

    def test_repeated_disagreement_returns_to_ocr_and_relearns(self):
        reader = DigitReader(self.folder)
        self.learn_until_switch(reader)
        self.dash.lie = ('26.2', '99')  # e.g. the dash font changed after an update
        results = self.drive(reader, [('24.5', '91')] * 10)
        self.assertEqual(reader.status('bmwm4gt3')['mode'], 'learning')
        # On a disagreement the teacher's accepted value is used, never a mix.
        self.assertEqual(results[-1]['wheels']['FL']['source'], 'windows_ocr')

    def test_learning_survives_restart_and_reset_clears_it(self):
        reader = DigitReader(self.folder)
        self.learn_until_switch(reader)
        reader.close()
        again = DigitReader(self.folder)
        self.assertEqual(again.status('bmwm4gt3')['mode'], 'self')
        self.assertEqual(again.status('bmwm4gt3')['digits_learned'], 10)
        again.reset('bmwm4gt3')
        self.assertEqual(DigitReader(self.folder).status('bmwm4gt3')['digits_learned'], 0)

    def test_unstable_or_rejected_readings_are_not_learned(self):
        reader = DigitReader(self.folder)
        # Every value shown once: never learned, so never switched.
        self.drive(reader, list(zip(PRESSURES, TEMPERATURES)) * 6)
        status = reader.status('bmwm4gt3')
        self.assertEqual((status['mode'], status['digits_learned']), ('learning', 0))


class ControllerStatusTests(unittest.TestCase):
    def setUp(self):
        from test_tire_overlay_control import Child
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'overlay/iracing/tires.json'
        def popen(*_, **__):
            child = Child()
            child.stdout.queue.get()  # replace the plain ready message
            child.stdout.queue.put(dict(type='ready', ocr_language='ko', ocr_languages=['ko', 'ja']))
            return child
        from tire_overlay_control import TireOverlayController
        self.controller = TireOverlayController(self.path, popen=Mock(side_effect=popen))
        self.addCleanup(self.controller.close)

    def test_ocr_language_and_state_source_are_reported(self):
        view = self.controller.update(voice_enabled=True)
        self.assertEqual((view['ocr_language'], view['ocr_english']), ('ko', False))
        self.controller.current['car_path'] = 'bmwm4gt3'
        self.assertIsNone(self.controller.status()['state_source'])
        self.controller.current['car_path'] = 'mclaren720sgt3'
        self.assertEqual(self.controller.status()['state_source'], 'dash_color')
        self.controller._targets().save('bmwm4gt3', {'pressure_low_bar': 1.7, 'pressure_high_bar': 1.9})
        self.controller.current['car_path'] = 'bmwm4gt3'
        self.assertEqual(self.controller.status()['state_source'], 'targets')

    def test_install_request_only_marks_requested_and_reports_cancel(self):
        with patch('tire_reader.ocr_language.request_install') as request:
            self.assertEqual(self.controller.install_english_ocr()['ocr_install'], 'requested')
            request.assert_called_once_with(wait=False)
            request.side_effect = RuntimeError('관리자 승인이 취소되었습니다.')
            with self.assertRaises(RuntimeError):
                self.controller.install_english_ocr()
        self.assertEqual(self.controller.status()['ocr_install'], 'not_started')


class DiagnosticsTests(unittest.TestCase):
    def test_reasons_and_reader_mode_are_kept_as_codes(self):
        from tire_reader.log_export import diagnostics
        message = {'status': 'reading', 'measurement': {'stage': 'ocr', 'reader': 'digit_model', 'reader_mode': 'self',
                   'pressure_reason': {'FL': 'digits_without_decimal', 'FR': '24.1 raw text'},
                   'pressure_dot_verified': {'RL': True}}}
        result = diagnostics(message, None, None, {}, enabled=True, voice_enabled=False)['measurement']
        self.assertEqual(result['pressure_reason']['FL'], 'digits_without_decimal')
        self.assertIsNone(result['pressure_reason']['FR'])  # anything but a code is dropped
        self.assertEqual(result['pressure_dot_verified'], {'FL': False, 'FR': False, 'RL': True, 'RR': False})
        self.assertEqual((result['reader'], result['reader_mode']), ('digit_model', 'self'))


if __name__ == '__main__':
    unittest.main()
