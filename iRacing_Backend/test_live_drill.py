import unittest

from live_drill import CornerDrill, drill_plan

L = 4000.0
CORNERS = [
    {'id': 'C1', 'label': '1번 코너', 'entry_pct': .10, 'apex_pct': .12, 'exit_pct': .14,
     'target': {'brake_start_pct': .095, 'full_throttle_pct': .15}, 'reference': {'brake_start_pct': .097, 'full_throttle_pct': .148}},
    {'id': 'C2', 'label': '2번 코너', 'entry_pct': .30, 'apex_pct': .32, 'exit_pct': .34,
     'target': {'brake_start_pct': .29, 'full_throttle_pct': .352}, 'reference': {'brake_start_pct': .292, 'full_throttle_pct': .348}},
    {'id': 'C3', 'label': '3번 코너', 'entry_pct': .60, 'apex_pct': .62, 'exit_pct': .64,
     'target': {'brake_start_pct': .59, 'full_throttle_pct': .65}, 'reference': {'brake_start_pct': .592, 'full_throttle_pct': .648}},
]


def plan(**changes):
    made = drill_plan(CORNERS, '2번 코너', 'THROTTLE_RETURN_LATE',
                      {'full_throttle_pct': .352, 'exit_speed_kph': 178., 'throttle_relifts': 0}, L)
    made.update(changes)
    return made


class Driver:
    """30 Hz samples of a car through the section: 50 m/s, braking from brake_at, 25 m/s at the apex,
    full throttle from full_at."""

    def __init__(self, drill):
        self.drill, self.time = drill, 100.0

    def drive(self, start, end, brake_at=.291, full_at=.350, speed=50.0, pit=False):
        pct = start
        while (end - pct) % 1.0 > 1e-9 and (end - pct) % 1.0 < .9:
            braking = brake_at <= pct < .32
            v = 25.0 if .315 <= pct <= .325 else speed
            self.drill.ingest({'distance_pct': pct % 1.0, 'speed_mps': v, 'session_time': self.time,
                               'brake': .8 if braking else 0.0, 'throttle': 1.0 if (pct >= full_at or pct < brake_at) else .4,
                               'on_pit_road': pit, 'is_replay': False, 'on_track': True,
                               'steering_rad': .1, 'yaw_rate': .2, 'abs_active': False}, L)
            pct += v / 30 / L
            self.time += 1 / 30

    def reset_to(self, pct):
        """Active Reset: the next sample is back at the saved start point."""
        self.drive_one(pct)

    def drive_one(self, pct):
        self.drill.ingest({'distance_pct': pct, 'speed_mps': 50.0, 'session_time': self.time, 'brake': 0.0, 'throttle': 1.0,
                           'on_track': True, 'is_replay': False, 'on_pit_road': False}, L)
        self.time += 1 / 30


class PlanTests(unittest.TestCase):
    def test_start_on_the_straight_before_braking_and_end_after_full_throttle(self):
        made = plan()
        # 250 m before the reference braking point (.292 - .0625), well after corner 1 is left behind.
        self.assertAlmostEqual(made['start_pct'], .2295, places=4)
        self.assertAlmostEqual(made['end_pct'], .348 + 100 / L, places=4)
        self.assertEqual((made['brake_pct'], made['throttle_pct'], made['base_value']), (.292, .348, .352))
        self.assertEqual(made['section_m'], round((made['end_pct'] - made['start_pct']) * L))

    def test_a_short_straight_starts_once_out_of_the_previous_corner(self):
        close = [dict(CORNERS[0], exit_pct=.27, reference={'full_throttle_pct': .275}), CORNERS[1], CORNERS[2]]
        made = drill_plan(close, '2번 코너', None, {}, L)
        self.assertAlmostEqual(made['start_pct'], .275 + 30 / L, places=5)
        self.assertIsNone(made['base_value'])

    def test_an_unknown_corner_is_refused(self):
        with self.assertRaises(ValueError):
            drill_plan(CORNERS, '9번 코너', None, {}, L)


class DrillTests(unittest.TestCase):
    def setUp(self):
        self.drill = CornerDrill()
        self.drill.start(plan(), warmup=False)                   # the warm-up has its own tests below
        self.car = Driver(self.drill)
        self.made = self.drill.plan

    def kinds(self):
        return [event['kind'] for event in self.drill.view()['events']]

    def test_driving_through_once_times_an_attempt_and_asks_for_the_save(self):
        self.car.drive(.20, self.made['end_pct'] + .01)
        view = self.drill.view()
        self.assertEqual(len(view['attempts']), 1)
        self.assertIn('save_hint', self.kinds())
        self.assertIn('end_beep', self.kinds())
        self.assertFalse(view['saved'])
        attempt = view['attempts'][0]
        # 50 m/s through the section, except 40 m around the apex at 25 m/s (0.8 s more)
        self.assertAlmostEqual(attempt['time_s'], (self.made['end_pct'] - self.made['start_pct']) * L / 50 + 0.8, delta=0.1)
        self.assertAlmostEqual(attempt['full_throttle_m'], (.350 - self.made['start_pct']) * L, delta=2)
        self.assertEqual(attempt['min_speed_kph'], 90.0)
        self.assertAlmostEqual(attempt['input_change'], (.350 - .352) * L, delta=2)       # 8 m earlier than the task's lap
        self.assertTrue(attempt['input_ok'])

    def test_brake_countdown_and_throttle_beep(self):
        self.car.drive(.20, self.made['end_pct'] + .01)
        steps = [event['step'] for event in self.drill.view()['events'] if event['kind'] == 'brake_beep']
        self.assertEqual(steps, [1, 2, 3])
        self.assertEqual(self.kinds().count('throttle_beep'), 1)

    def test_an_active_reset_starts_the_next_attempt_at_the_saved_point(self):
        self.car.drive(.20, self.made['end_pct'] + .002)
        self.car.reset_to(.24)                                    # the driver saved at .24 and pressed run
        view = self.drill.view()
        self.assertTrue(view['saved'])
        self.car.drive(.24 + 50 / 30 / L, self.made['end_pct'] + .002)
        self.assertEqual(len(self.drill.view()['attempts']), 2)
        self.assertNotIn('save_hint', [e['kind'] for e in self.drill.view(after=view['serial'])['events']])

    def test_five_close_attempts_with_the_input_moved_end_the_drill(self):
        self.car.drive(.20, self.made['end_pct'] + .002)          # driven in: longer, not compared
        for _ in range(4):
            self.car.reset_to(.24)
            self.car.drive(.24 + 50 / 30 / L, self.made['end_pct'] + .002)
        self.assertFalse(self.drill.view()['done'])               # four runs from the saved point so far
        for _ in range(1):
            self.car.reset_to(.24)
            self.car.drive(.24 + 50 / 30 / L, self.made['end_pct'] + .002)
        view = self.drill.view()
        self.assertTrue(view['done'])
        self.assertIn('done', self.kinds())
        self.assertEqual(view['recent_ok'], 5)
        self.assertIsNotNone(view['baseline_s'])
        before = len(view['attempts'])
        self.car.reset_to(.24)
        self.car.drive(.24 + 50 / 30 / L, self.made['end_pct'] + .002)
        self.assertEqual(len(self.drill.view()['attempts']), before)     # finished: nothing more is counted

    def test_the_input_not_moved_or_scattered_braking_keeps_going(self):
        self.car.drive(.20, self.made['end_pct'] + .002, full_at=.356)
        for brake_at in (.291, .292, .291, .292, .291):
            self.car.reset_to(.24)
            self.car.drive(.24 + 50 / 30 / L, self.made['end_pct'] + .002, brake_at=brake_at, full_at=.356)
        self.assertFalse(self.drill.view()['done'])                     # full throttle 16 m later than the goal
        drill = CornerDrill(); drill.start(plan(), warmup=False); car = Driver(drill)
        car.drive(.20, self.made['end_pct'] + .002, brake_at=.285)
        for brake_at in (.291, .297, .285, .296, .291):
            car.reset_to(.24)
            car.drive(.24 + 50 / 30 / L, self.made['end_pct'] + .002, brake_at=brake_at)
        self.assertFalse(drill.view()['done'])                          # braking spread over 48 m

    def test_pit_lane_replays_and_gaps_are_not_attempts(self):
        self.car.drive(.20, self.made['end_pct'] + .01, pit=True)
        self.assertEqual(self.drill.view()['attempts'], [])
        self.car.drive(.20, .30)
        self.car.time += 2.0                                            # a two-second hole in the data
        self.car.drive(.30, self.made['end_pct'] + .01)
        self.assertEqual(self.drill.view()['attempts'], [])

    def test_stopped_drill_measures_nothing(self):
        self.drill.stop()
        self.car.drive(.20, self.made['end_pct'] + .01)
        self.assertEqual(self.drill.view(), {'active': False})


if __name__ == '__main__':
    unittest.main()


class WarmupTests(unittest.TestCase):
    def setUp(self):
        self.drill = CornerDrill()
        self.drill.start(plan())
        self.car = Driver(self.drill)

    def kinds(self):
        return [event['kind'] for event in self.drill.view()['events']]

    def lap(self):
        self.car.drive(.0, .5)                                   # the test driver goes at most 0.9 lap at a time
        self.car.drive(.5, .999)
        self.car.drive_one(.0005)

    def test_a_short_lap_warms_over_two_crossings_counting_the_out_lap(self):
        self.assertEqual((self.drill.view()['phase'], self.kinds()), ('WARMUP', ['warmup']))
        self.car.drive(.6, .999)                                 # out of the pits to the line: counts
        self.car.drive_one(.0005)
        self.assertEqual((self.drill.view()['warm_laps'], self.kinds()[-1]), (1, 'warmup_last'))
        self.assertEqual(self.drill.view()['attempts'], [])      # nothing timed on cold tyres
        self.lap()
        self.assertEqual((self.drill.view()['phase'], self.kinds()[-1]), ('READY', 'ready'))
        self.car.drive(.0005, .40)                               # the next lap: now the section is measured
        self.assertEqual(len(self.drill.view()['attempts']), 1)
        self.assertIn('save_hint', self.kinds())

    def test_a_long_lap_is_ready_after_the_first_lap_out_of_the_pits(self):
        drill = CornerDrill()
        drill.start(plan(warmup_crossings=1))
        car = Driver(drill)
        self.assertEqual([e['kind'] for e in drill.view()['events']], ['warmup_one'])
        car.drive(.6, .999)
        car.drive_one(.0005)
        self.assertEqual((drill.view()['phase'], drill.view()['warmup_laps']), ('READY', 1))

    def test_the_lap_time_decides_the_crossings(self):
        from live_drill import warmup_crossings
        self.assertEqual([warmup_crossings(t) for t in (65.2, 79.9, 80.0, 118.0, None)], [2, 2, 1, 1, 2])

    def test_warm_tyres_can_skip_the_warm_up(self):
        self.assertEqual(self.drill.skip_warmup()['phase'], 'READY')
        self.assertEqual(self.kinds(), ['warmup', 'ready'])
        self.car.reset_to(.24)
        self.car.drive_one(.2405)
        self.assertEqual(self.drill.view()['phase'], 'READY')     # no jump yet


class SpokenWarmupTests(unittest.TestCase):
    def test_the_coach_says_the_warm_up_and_the_ready(self):
        from live_telemetry import LiveTelemetry
        from radio_audio import CALLS
        clock = [1000.0]
        live = LiveTelemetry(clock=lambda: clock[0])
        base = {'session_num': 0, 'lap_number': 2, 'on_track': True, 'is_replay': False, 'on_pit_road': False,
                'session_state': 4, 'session_flags': 0, 'speed_mps': 50., 'brake': 0., 'throttle': 1., 'steering_rad': 0.}
        live.ingest({'track_id': '1', 'car_id': '2'}, {**base, 'session_time': 10., 'distance_pct': .5})
        live.drill.start(plan())
        live.ingest({'track_id': '1', 'car_id': '2'}, {**base, 'session_time': 10.1, 'distance_pct': .5012})
        spoken = [event for event in live.radio.pending if event['key'].startswith('drill_')]
        self.assertEqual([(e['key'], e['engineer'], e['speech_key']) for e in spoken], [('drill_warmup', 'coach', 'drill_warmup')])
        self.assertEqual(spoken[0]['text'], CALLS['drill_warmup'][1])
        live.drill.skip_warmup()
        live.ingest({'track_id': '1', 'car_id': '2'}, {**base, 'session_time': 10.2, 'distance_pct': .5024})
        self.assertIn('drill_ready', [event['key'] for event in live.radio.pending])
        live.ingest({'track_id': '1', 'car_id': '2'}, {**base, 'session_time': 10.3, 'distance_pct': .5036})
        self.assertEqual(sum(1 for event in live.radio.pending if event['key'] == 'drill_warmup'), 1)   # said once
