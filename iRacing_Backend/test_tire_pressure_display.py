import unittest
from tire_reader.pressure_display import PressureDisplay
from tire_reader.pressure_ocr import WHEELS


class DisplayTests(unittest.TestCase):
    def sample(self,**values):
        return dict(status='reading',**{'raw_kpa_'+w:v for w,v in values.items()})

    def test_first_valid_measurement_is_shown_immediately(self):
        display=PressureDisplay()
        self.assertTrue(display.update(dict(self.sample(FL=160),status='waiting_for_agreement')))
        self.assertEqual(display.formatted()['FL'],'1.60')
        self.assertEqual(display.formatted()['FR'],'—')

    def test_failure_preserves_previous_numbers_and_next_success_replaces_them(self):
        display=PressureDisplay()
        display.update(self.sample(FL=160,FR=161,RL=162,RR=163))
        before=display.formatted()
        for _ in range(8):
            self.assertFalse(display.update(dict(status='weak_geometry')))
            self.assertEqual(display.formatted(),before)
        self.assertIn('이전 값 유지',display.status)
        self.assertEqual(display.updates,1)
        display.update(self.sample(FL=164,FR=None,RL=165,RR=None))
        self.assertEqual(display.formatted(),dict(FL='1.64',FR='1.61',RL='1.65',RR='1.63'))

    def test_missing_initial_values_and_invalid_values_are_not_invented(self):
        display=PressureDisplay()
        display.update(dict(status='weak_geometry'))
        self.assertEqual(display.formatted(),dict.fromkeys(WHEELS,'—'))
        display.update(self.sample(FL=float('nan'),FR=True,RL=999,RR=-1))
        self.assertEqual(display.formatted(),dict.fromkeys(WHEELS,'—'))

    def test_error_rows_do_not_overwrite_old_numbers_and_restart_clears_them(self):
        display=PressureDisplay()
        display.update(self.sample(FL=170))
        display.update(dict(self.sample(FL=180),status='error'))
        self.assertEqual(display.formatted()['FL'],'1.70')
        display.reset()
        self.assertEqual(display.formatted()['FL'],'—')

    def test_old_profile_units_cannot_change_the_bar_overlay(self):
        display=PressureDisplay('psi',1)
        display.update(self.sample(FL=172.368932325))
        self.assertEqual(display.formatted()['FL'],'1.72')
        self.assertEqual(display.unit,'bar')
