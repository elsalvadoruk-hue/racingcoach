import unittest

from live_sectors import LapSectors, RivalSectors, place_name

IDENTITY = {'car_id': '1', 'track_id': '2', 'track_config': ''}


def drive(sectors, lap, seconds, start=0., slow=None, extra=0., until=1., **updates):
    """One lap at 30Hz and constant speed; `slow` is a sector that takes `extra` seconds longer."""
    at, pct, step = start, 0., 1 / 30
    while pct < until:
        sectors.ingest({'distance_pct': min(pct, .999999), 'session_time': at, 'lap_number': lap, 'on_track': True,
                        'is_replay': False, 'on_pit_road': False, 'incidents': 0, 'session_flags': 0, **updates}, IDENTITY)
        sector = int(pct * LapSectors.SECTORS)
        rate = 1 / seconds if sector != slow else 1 / (seconds + extra * LapSectors.SECTORS)
        at, pct = at + step, pct + step * rate
    return at


class LapSectorTests(unittest.TestCase):
    def test_theoretical_best_joins_the_best_sector_of_each_lap(self):
        laps = LapSectors()
        at = drive(laps, 1, 100.)                               # an out lap that is never complete from its start
        at = drive(laps, 2, 100., at, slow=3, extra=.6)
        self.assertIsNone(laps.theoretical())                   # no full clean lap yet
        at = drive(laps, 3, 100., at, slow=11, extra=.4)
        at = drive(laps, 4, 100., at, until=.02)
        best = laps.theoretical()
        self.assertAlmostEqual(best['best_lap_s'], 100.4, delta=.05)
        self.assertAlmostEqual(best['lap_s'], 100., delta=.05)   # sector 3 from one lap, sector 11 from the other
        self.assertAlmostEqual(best['gain_s'], .4, delta=.05)

    def test_delta_of_the_lap_in_progress_against_the_best_lap(self):
        laps = LapSectors()
        at = drive(laps, 1, 100.)
        at = drive(laps, 2, 100., at)
        at = drive(laps, 3, 100., at, slow=2, extra=.5, until=.52)
        delta = laps.delta()
        self.assertAlmostEqual(delta['delta_s'], .5, delta=.05)
        self.assertEqual((delta['sectors'], delta['lap']), (10, 3))

    def test_an_incident_a_yellow_or_a_gap_keeps_the_lap_out_of_the_references(self):
        for spoil in ({'incidents': 2}, {'session_flags': 0x8}):
            laps = LapSectors()
            at = drive(laps, 1, 100.)
            at = drive(laps, 2, 90., at, until=.5)
            laps.ingest({'distance_pct': .5, 'session_time': at, 'lap_number': 2, 'on_track': True, 'is_replay': False,
                         'on_pit_road': False, 'incidents': 0, 'session_flags': 0, **spoil}, IDENTITY)
            self.assertIsNone(laps.delta())
            self.assertIsNone(laps.theoretical())
        laps = LapSectors()
        at = drive(laps, 1, 100.)
        at = drive(laps, 2, 100., at)
        drive(laps, 3, 100., at, until=.02)
        self.assertIsNotNone(laps.theoretical())
        laps.ingest({'distance_pct': .5, 'session_time': at, 'lap_number': 3, 'on_track': True, 'is_replay': False,
                     'on_pit_road': False, 'incidents': 0, 'session_flags': 0}, {**IDENTITY, 'track_id': '9'})
        self.assertIsNone(laps.theoretical())   # another track: nothing carries over


class RivalSectorTests(unittest.TestCase):
    def feed(self, rivals, laps, lap_s, slow=None):
        """Two cars at 1Hz. Car 1 loses half a second in the sector named by `slow`."""
        positions = {0: .0, 1: .03}
        for second in range(int(laps * lap_s)):
            cars = []
            for idx in positions:
                sector = int(positions[idx] % 1 * RivalSectors.SECTORS)
                rate = 1 / lap_s if not (idx == 1 and sector == slow) else 1 / (lap_s + .5 * RivalSectors.SECTORS)
                positions[idx] += rate
                cars.append({'idx': idx, 'distance_pct': positions[idx] % 1, 'on_pit_road': False, 'track_surface': 3})
            rivals.ingest(cars, float(second))

    def test_where_the_driver_is_faster_than_the_car_ahead(self):
        rivals = RivalSectors()
        self.feed(rivals, 3.5, 100., slow=5)
        result = {r['sector']: r['delta_s'] for r in rivals.compare(0, 1)}
        self.assertEqual(len(result), RivalSectors.SECTORS)
        self.assertAlmostEqual(result[5], -.5, delta=.06)        # half a second faster through sector 6
        self.assertTrue(all(abs(v) < .06 for k, v in result.items() if k != 5))

    def test_a_sector_needs_two_laps_from_both_cars_and_pit_lane_breaks_the_timing(self):
        rivals = RivalSectors()
        self.feed(rivals, 1.2, 100.)
        self.assertEqual(rivals.compare(0, 1), [])
        self.feed(rivals, 2.5, 100.)
        self.assertTrue(rivals.compare(0, 1))
        rivals.ingest([{'idx': 0, 'distance_pct': .5, 'on_pit_road': True, 'track_surface': 3}], 1000.)
        self.assertNotIn(0, rivals.marks)

    def test_a_stretch_is_named_by_a_confirmed_corner_or_by_its_number(self):
        definitions = [{'apex_pct': .30, 'call': '5번 코너'}, {'apex_pct': .70, 'call': None}]
        self.assertEqual(place_name(.3125, 2, definitions), '5번 코너 부근')
        self.assertEqual(place_name(.6875, 5, definitions), '6번 구간')    # the corner there is not confirmed
        self.assertEqual(place_name(.0625, 0, []), '1번 구간')


if __name__ == '__main__':
    unittest.main()
