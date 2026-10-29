"""Prepared coverage and short pit calls used by the radio delivery fixes."""
import unittest
import number_splice
import radio_phrases
from race_radio import STATIC_CALLS


class PreparedCoverageTests(unittest.TestCase):
    def test_five_minutes_and_six_minutes_first_ten_seconds_are_prepared(self):
        values = set(radio_phrases.RANGES['lap'])
        self.assertTrue(set(radio_phrases.tenths(300, 369.9)) <= values)
        for key in number_splice.LAP_KEYS:
            _, templates = radio_phrases.wordings(key)
            for template in templates:
                for seconds in (299.9, 300, 309.9, 310, 359.9, 360, 369.9, 370):
                    text = template.replace('{lap}', radio_phrases.lap_time(seconds))
                    self.assertIsNotNone(number_splice.plan(text), text)

    def test_near_pit_distances_use_one_short_wording(self):
        for distance in (50, 20, 10):
            self.assertEqual(STATIC_CALLS[f'pit_box_{distance}'], (f'{distance}미터.',))
        self.assertGreater(len(STATIC_CALLS['pit_box_100']), 1)
