"""A decimal point the OCR dropped is put back from the SDK unit setting only when nothing else is possible."""
import unittest

from tire_reader.pressure_ocr import AUTO_PRESSURE_FORMATS, format_dot, parse_pressure

PSI1 = [('psi', 1)]
BAR2 = [('bar', 2)]


class FormatDotTests(unittest.TestCase):
    def test_official_dashboard_readings_without_the_point(self):
        # BMW M4 GT3 and Porsche 992 R manual dashboards, psi mode.
        self.assertEqual(format_dot(['242', '242', '24.2', '242', '242', '242'], PSI1), parse_pressure('24.2', 'psi', 1))
        self.assertEqual(format_dot(['239'] * 6, PSI1), parse_pressure('23.9', 'psi', 1))
        self.assertEqual(format_dot(['', '180', '180'], BAR2), (1.8, 180.0))

    def test_needs_one_known_format_from_the_game(self):
        self.assertIsNone(format_dot(['242'] * 6, None))
        self.assertIsNone(format_dot(['242'] * 6, list(AUTO_PRESSURE_FORMATS)))   # 2.42 bar or 24.2 psi
        self.assertIsNone(format_dot(['159'] * 3, [('kPa', 0)]))                   # integer formats have no point

    def test_needs_agreeing_variants(self):
        self.assertIsNone(format_dot(['242'], PSI1))                         # one variant only
        self.assertIsNone(format_dot(['242', '', 'æ4.2', '3', '7'], PSI1))  # less than half of the readings
        self.assertIsNone(format_dot(['242', '242', '243'], PSI1))          # two different numbers
        self.assertIsNone(format_dot(['242', '242', '24.3'], PSI1))         # a kept point shows another number
        self.assertIsNone(format_dot(['2391', '239', '239', '2391'], PSI1))

    def test_still_checks_the_range_and_never_adds_digits(self):
        self.assertIsNone(format_dot(['9', '9'], PSI1))
        self.assertIsNone(format_dot(['999', '999'], BAR2))   # 9.99 bar is outside the pressure range


if __name__ == '__main__':
    unittest.main()
