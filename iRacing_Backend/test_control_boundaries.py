"""A completed lap must stay clean through its final observed crossing."""
import unittest

from live_controls import ControlAdvisor


class ControlBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.advisor = ControlAdvisor()
        self.sample = {
            'on_track': True, 'is_replay': False, 'on_pit_road': False,
            'speed_mps': 40., 'steering_rad': .2, 'yaw_rate': .3,
            'brake': .4, 'throttle': 0., 'incidents': 0,
            'session_flags': 0, 'car_left_right': 1,
            'brake_bias': 50., 'abs_setting': 4, 'tc_setting': 4,
            'abs_active': False,
        }
        self.advisor.ingest({**self.sample, 'session_time': 0., 'lap_number': 0, 'distance_pct': .99})
        for index in range(101):
            self.advisor.ingest({**self.sample, 'session_time': .1 + index * .1,
                                 'lap_number': 1, 'distance_pct': index * .0099})
        self.assertTrue(self.advisor.current['clean'])
        self.assertGreater(self.advisor.current['brake_s'], 2)

    def cross(self, **changes):
        self.advisor.ingest({**self.sample, 'session_time': 10.2, 'lap_number': 2,
                             'distance_pct': 0., **changes})

    def assert_rejected(self, **changes):
        self.cross(**changes)
        self.assertEqual(list(self.advisor.laps), [])
        self.assertFalse(self.advisor.current['clean'])
        self.assertFalse(self.advisor.view()['ready'])

    def test_a_clean_crossing_keeps_the_completed_lap(self):
        self.cross()
        self.assertEqual([lap['lap'] for lap in self.advisor.laps], [1])
        self.assertTrue(self.advisor.current['clean'])

    def test_a_sample_gap_at_the_line_rejects_the_completed_lap(self):
        self.assert_rejected(session_time=12.)

    def test_an_incident_at_the_line_rejects_the_completed_lap(self):
        self.assert_rejected(incidents=4)

    def test_a_yellow_at_the_line_rejects_the_completed_lap(self):
        self.assert_rejected(session_flags=0x8)

    def test_an_overlap_at_the_line_rejects_the_completed_lap(self):
        self.assert_rejected(car_left_right=2)


if __name__ == '__main__':
    unittest.main()
