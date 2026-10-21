"""How fast the car beside us passes (iRacing lap positions, LMU 3D positions) and the corner ahead.

Synthetic positions only: no game, personal database or cloud requests.
"""
import os
import tempfile
import unittest

os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(), 'spotter-passing.sqlite3'))

from live_telemetry import LiveTelemetry
from lmu_irsdk import LeftRight
from spotter import AlongsideSpeed
import test_adaptive_pitwall as pitwall

LENGTH = 5000.


class AlongsideSpeedTests(unittest.TestCase):
    def run_cars(self, own_speed, other_speed, start_gap, seconds=1., rate=30):
        """Our car and one other along a 5 km lap; what the tracker says at the end."""
        tracker, result = AlongsideSpeed(), None
        for step in range(int(seconds * rate) + 1):
            t = step / rate
            own = (1000 + own_speed * t) / LENGTH
            other = (1000 + start_gap + other_speed * t) / LENGTH
            result = tracker(t, own, {7: other}, LENGTH)
        return result

    def test_a_car_holding_station_beside_us_is_slow(self):
        self.assertAlmostEqual(self.run_cars(50., 50.5, -1.), .5, places=2)

    def test_a_car_sweeping_past_is_fast(self):
        self.assertAlmostEqual(self.run_cars(50., 62., -6.), 12., places=1)

    def test_only_cars_that_can_overlap_count_and_the_slowest_decides(self):
        tracker = AlongsideSpeed()
        for step in range(31):
            t = step / 30
            own = (1000 + 50 * t) / LENGTH
            cars = {1: (1000 + 2 + 50.2 * t) / LENGTH,       # beside us, settled
                    2: (1000 - 5 + 60 * t) / LENGTH,         # sweeping past
                    3: (1000 + 30 + 50 * t) / LENGTH}        # 30 m ahead: cannot overlap
            result = tracker(t, own, cars, LENGTH)
        self.assertLess(result, 1.)

    def test_the_start_line_and_unknowns(self):
        tracker = AlongsideSpeed()
        for step in range(31):
            t = step / 30
            own = ((LENGTH - 2 + 50 * t) % LENGTH) / LENGTH
            other = ((LENGTH - 4 + 51 * t) % LENGTH) / LENGTH
            result = tracker(t, own, {4: other}, LENGTH)
        self.assertAlmostEqual(result, 1., places=1)
        self.assertIsNone(AlongsideSpeed()(0., .5, {1: .5}, LENGTH))        # no history yet
        self.assertIsNone(tracker(2., None, {4: .1}, LENGTH))
        self.assertIsNone(tracker(2., .1, {4: .1}, None))
        self.assertEqual(tracker.tracks, {})


class LmuClosingTests(unittest.TestCase):
    IDENTITY = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]

    def traffic(self, t, others):
        return {'status': 'AVAILABLE', 'player': {'vehicle_id': 0, 'telemetry_et': t, 'position_m': [0., 0., 0.],
                                                  'orientation': self.IDENTITY},
                'vehicles': [{'vehicle_id': vid, 'telemetry_et': t, 'position_m': [x, 0., z]} for vid, x, z in others]}

    def test_closing_speed_of_the_car_beside_us(self):
        sides = LeftRight()
        for step in range(13):
            t = step / 60
            state = sides(self.traffic(t, [(3, 3., -4. + 15 * t)]))     # 15 m/s past us on the left
        self.assertEqual(state, 2)
        self.assertAlmostEqual(sides.closing, 15., places=1)
        for step in range(13, 40):
            t = step / 60
            sides(self.traffic(t, [(3, 3., -1.8 + .5 * (t - 13 / 60))]))
        self.assertAlmostEqual(sides.closing, .5, places=1)
        self.assertIsNone(sides(None))
        self.assertIsNone(sides.closing)

    def test_the_adapter_publishes_it_and_the_live_loop_uses_only_that(self):
        class Lmu:
            game = 'lmu'
            def __init__(self, value): self.value = value
            def __getitem__(self, name): return {'SideClosingMps': self.value, 'CarIdxLapDistPct': [.1, .1]}[name]
        live = LiveTelemetry()
        self.assertEqual(live._side_closing(Lmu(3.5), {'SideClosingMps', 'CarIdxLapDistPct'}, {}, {}, {}), 3.5)
        self.assertIsNone(live._side_closing(Lmu(None), {'CarIdxLapDistPct'}, {'distance_pct': .1}, {}, {}))


class IracingClosingTests(unittest.TestCase):
    def test_the_live_loop_reads_every_cars_lap_position(self):
        clock = [0.]
        live = LiveTelemetry(clock=lambda: clock[0])

        class Sdk:
            game = 'iracing'
            def __init__(self, t): self.t = t
            def __getitem__(self, name):
                return {'PlayerCarIdx': 0,
                        'CarIdxLapDistPct': [(1000 + 50 * self.t) / LENGTH, (998 + 58 * self.t) / LENGTH,
                                             (1001 + 58 * self.t) / LENGTH, -1.],
                        'CarIdxTrackSurface': [3, 3, 1, -1], 'CarIdxOnPitRoad': [False, False, True, False]}[name]
        names = {'PlayerCarIdx', 'CarIdxLapDistPct', 'CarIdxTrackSurface', 'CarIdxOnPitRoad'}
        result = None
        for step in range(16):
            clock[0] = step / 30
            result = live._side_closing(Sdk(clock[0]), names, {'distance_pct': (1000 + 50 * clock[0]) / LENGTH},
                                        {'TrackLength': '5.00 km'}, {})
        self.assertAlmostEqual(result, 8., places=1)        # car 1 only; car 2 is in its pit stall


class CornerAheadTests(unittest.TestCase):
    train = pitwall.SpeechTimingTests.train

    def test_the_corner_ahead_is_known_with_a_car_beside_us(self):
        model = self.train()
        middle = pitwall.sample(315, 4, .15)
        alone, alongside = model.view(middle), model.view({**middle, 'car_left_right': 2})
        self.assertGreater(alone['corner_in_s'], 9)
        self.assertEqual(alongside['corner_in_s'], alone['corner_in_s'])
        self.assertEqual(alongside['workload_in_s'], 0)           # other calls still wait
        self.assertEqual(model.view({**middle, 'brake': .4})['corner_in_s'], 0)


if __name__ == '__main__':
    unittest.main()
