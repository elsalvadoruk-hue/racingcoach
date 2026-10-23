"""Same-state reminders must not delay new side information. Synthetic clocks only."""
import unittest

from spotter import HOLD_KEYS, STATE_KEYS, Spotter


class SpotterRepetitionTests(unittest.TestCase):
    def setUp(self):
        self.spotter = Spotter()
        self.now = 0.

    def feed(self, state, seconds, run='run', **extra):
        for _ in range(round(seconds * 10)):
            self.now = round(self.now + .1, 6)
            self.spotter.ingest({'session_time': self.now, 'on_track': True, 'is_replay': False,
                                 'on_pit_road': False, 'car_left_right': state, **extra}, run, self.now)

    def keys(self):
        return [e['key'] for e in self.spotter.events]

    def test_one_timed_reminder_for_each_side_and_selected_interval(self):
        for state in STATE_KEYS:
            for interval in (2, 3, 5):
                with self.subTest(state=state, interval=interval):
                    self.setUp()
                    self.spotter.repeat_seconds = interval
                    self.feed(state, .5, corner_in_s=20.)
                    first = self.spotter.events[-1]['at']
                    self.feed(state, interval - .1, corner_in_s=20.)
                    self.assertEqual(self.keys(), [STATE_KEYS[state]])
                    self.feed(state, .2, corner_in_s=20.)
                    self.assertEqual(self.keys(), [STATE_KEYS[state], HOLD_KEYS[state]])
                    self.assertAlmostEqual(self.spotter.events[-1]['at'] - first, interval, places=6)
                    self.feed(state, 30., corner_in_s=20.)
                    self.assertEqual(self.keys(), [STATE_KEYS[state], HOLD_KEYS[state]])

    def test_no_second_timed_reminder_in_the_same_corner(self):
        self.feed(4, 25., corner_in_s=0.)
        self.assertEqual(self.keys(), ['both', 'hold_both'])

    def test_missing_corner_prediction_still_allows_one_reminder(self):
        self.feed(3, 25., corner_in_s=None)
        self.assertEqual(self.keys(), ['right', 'hold_right'])

    def test_each_new_corner_can_remind_after_the_timed_budget_is_spent(self):
        self.feed(2, 20., corner_in_s=20.)
        self.assertEqual(self.keys(), ['left', 'hold_left'])
        for count in (2, 3):
            self.feed(2, 5., corner_in_s=2.)
            self.assertEqual(self.keys(), ['left'] + ['hold_left'] * count)
            self.feed(2, 6., corner_in_s=10.)
        self.feed(1, .3)
        self.assertEqual(self.keys()[-1], 'clear_left')

    def test_new_side_changes_and_clear_do_not_wait_for_the_reminder_interval(self):
        for state, expected in [(2, 'left'), (4, 'both'), (5, 'two_left'), (2, 'left_one'),
                                (3, 'right'), (6, 'two_right'), (3, 'right_one'), (1, 'clear_right')]:
            self.feed(state, .6)
            self.assertEqual(self.keys()[-1], expected)
        self.assertFalse(any(key.startswith('hold') for key in self.keys()))

    def test_state_change_gets_its_own_single_reminder(self):
        self.feed(2, 20., corner_in_s=20.)
        self.feed(4, 20., corner_in_s=20.)
        self.feed(3, 20., corner_in_s=20.)
        self.feed(1, .3)
        self.assertEqual(self.keys(), ['left', 'hold_left', 'both', 'hold_both',
                                      'right_only', 'hold_right', 'clear_right'])

    def test_brief_unknown_does_not_restart_reminders_and_long_unknown_never_clears(self):
        self.feed(2, 20., corner_in_s=20.)
        self.feed(None, .3)
        self.feed(2, 20., corner_in_s=20.)
        self.assertEqual(self.keys(), ['left', 'hold_left'])
        self.feed(None, 1.3)
        self.feed(1, .3)
        self.assertEqual(self.keys(), [])
        self.feed(2, 20., corner_in_s=20.)
        self.assertEqual(self.keys(), ['left', 'hold_left'])

    def test_new_run_and_pit_exit_get_a_fresh_reminder_budget_without_false_clear(self):
        for reset in ('run', 'pit'):
            with self.subTest(reset=reset):
                self.setUp()
                self.feed(2, 20., corner_in_s=20.)
                if reset == 'pit':
                    self.feed(2, .1, on_pit_road=True)
                self.feed(3, 20., run='new-run' if reset == 'run' else 'run', corner_in_s=20.)
                self.assertEqual(self.keys(), ['right', 'hold_right'])


if __name__ == '__main__':
    unittest.main()
