"""A single-frame digit misread is held until the next reading agrees (real BMW M4 GT3 cases)."""
import unittest

from tire_reader.pressure_jump import PressureJumpFilter


def run(values, start=100., step=1., **kwargs):
    jumps = PressureJumpFilter()
    return [jumps.check('FL', v, start + i * step, **kwargs)[0] for i, v in enumerate(values)]


class PressureJumpTests(unittest.TestCase):
    def test_one_frame_misreads_seen_while_driving_are_held(self):
        # 1.64 -> 1.84 -> 1.65 and 1.69 -> 1.89 -> 1.69 bar, one second apart.
        self.assertEqual(run([164., 184., 165.]), [164., None, 165.])
        self.assertEqual(run([169., 189., 169.]), [169., None, 169.])

    def test_a_real_change_is_accepted_once_it_repeats(self):
        self.assertEqual(run([164., 184., 185., 185.]), [164., None, 185., 185.])

    def test_normal_warm_up_passes_and_long_gaps_start_afresh(self):
        self.assertEqual(run([159., 162., 166., 170., 174.]), [159., 162., 166., 170., 174.])
        self.assertEqual(run([160., 180.], step=11.), [160., 180.])     # no reading for 11 s
        self.assertEqual(run([160., 175.], step=10.), [160., 175.])     # 1.5 kPa/s over 10 s

    def test_pit_road_new_session_and_time_going_back_are_not_held(self):
        jumps = PressureJumpFilter()
        jumps.check('FL', 180., 100., key='a')
        self.assertEqual(jumps.check('FL', 160., 101., key='a', pit=True), (160., False))
        jumps.check('FL', 180., 102., key='a')
        self.assertEqual(jumps.check('FL', 160., 103., key='b'), (160., False))
        self.assertEqual(jumps.check('FL', 180., 50., key='b'), (180., False))

    def test_wheels_are_independent_and_missing_values_pass_through(self):
        jumps = PressureJumpFilter()
        jumps.check('FL', 164., 1.); jumps.check('FR', 184., 1.)
        self.assertEqual(jumps.check('FR', 185., 2.), (185., False))
        self.assertEqual(jumps.check('FL', 184., 2.), (None, True))
        self.assertEqual(jumps.check('FL', None, 3.), (None, False))


if __name__ == '__main__':
    unittest.main()
