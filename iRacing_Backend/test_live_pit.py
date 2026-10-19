import tempfile
import unittest
from pathlib import Path

import radio_phrases
from live_pit import PitKnowledge, PitLossWatch, TyreWatch, stall_estimate, tyre_advice

IDENTITY = {'track_id': '523', 'track_config': 'Grand Prix', 'car_id': '132'}


def say(parts):
    return [radio_phrases.build(key, 0, **values)['text'] for key, values in parts]


class TyreAdviceTests(unittest.TestCase):
    def test_a_sprint_keeps_its_tyres_and_says_so_when_the_change_is_ticked(self):
        decision, parts = tyre_advice(45 * 60, None, 24., 2.5, .9, 13, 8, tyres_ticked=False)
        self.assertEqual((decision, say(parts)), ('keep', ['타이어는 바꾸지 않습니다.']))
        decision, parts = tyre_advice(40 * 60, 'CONCURRENT', 24., 2.5, .9, 13, 8, tyres_ticked=True)
        self.assertEqual(say(parts), ['타이어는 바꾸지 않습니다.', '지금 설정은 타이어 교체입니다.'])

    def test_endurance_needs_the_drivers_pit_rule(self):
        self.assertEqual(tyre_advice(180 * 60, None, 95., 2.5, .77, 24, 24, stints_on_set=1), (None, []))

    def test_a_free_change_is_still_weighed_against_the_cold_set(self):
        # Monza, concurrent: fuelling outlasts the tyres, so a change costs only the cold set, about 4 s.
        # The set is 20 % worn; over 29 laps of 108.8 s it may lose 8.5 s. Changing is the smaller risk.
        decision, parts = tyre_advice(160 * 60, 'CONCURRENT', 108., 2.75, .66, 30, 29, tyres_ticked=False, stints_on_set=1, lap_s=108.8)
        self.assertEqual((decision, say(parts)), ('change', ['타이어도 바꿉니다.', '지금 설정은 타이어 교체 없음입니다.']))
        # The same stop with twelve laps to the flag: the old set may lose 3.5 s, less than the cold set costs.
        self.assertEqual(tyre_advice(160 * 60, 'CONCURRENT', 45., 2.75, .66, 30, 12, stints_on_set=1, lap_s=108.8)[0], 'keep')
        # Nothing measured yet: 60 litres outlast a tyre change at any fuelling speed seen, so change.
        self.assertEqual(tyre_advice(160 * 60, 'CONCURRENT', 60., None, None, None, None)[0], 'change')
        self.assertEqual(tyre_advice(160 * 60, 'CONCURRENT', 20., None, None, None, None), (None, []))   # nothing is known: nothing is said

    def test_where_a_change_costs_standing_time_the_first_stop_keeps_the_set_and_the_next_changes(self):
        spa = (180 * 60, 'SEQUENTIAL', 95., 2.5, .77, 24, 24)
        # Changing costs 24 s here; an 18 % worn set may lose 8 s over the next 24 laps.
        first = tyre_advice(*spa, stints_on_set=1, lap_s=138.7)
        self.assertEqual((first[0], say(first[1])), ('keep', ['타이어는 바꾸지 않습니다.']))
        second = tyre_advice(180 * 60, 'SEQUENTIAL', 95., 2.5, .77, 48, 24, stints_on_set=2, lap_s=138.7)
        self.assertEqual((second[0], say(second[1])), ('change', ['타이어도 바꿉니다.']))     # never a third stint
        self.assertEqual(tyre_advice(*spa, stints_on_set=1, lap_s=138.7, tyres_ticked=True)[1][-1], ('tyre_setting_on', {}))
        # A track that eats tyres: 32 % worn after 26 laps of Road America may lose 15 s over the next 26.
        road_america = tyre_advice(160 * 60, 'CONCURRENT', 30., 2.5, 1.24, 26, 26, stints_on_set=1, lap_s=126.3)
        self.assertEqual(road_america[0], 'change')
        # Wear and pace unknown: the plain rule, keep on the first stint and change after.
        self.assertEqual(tyre_advice(180 * 60, 'SEQUENTIAL', 95., None, None, None, None, stints_on_set=1)[0], 'keep')
        self.assertEqual(tyre_advice(180 * 60, 'SEQUENTIAL', 95., None, None, None, None, stints_on_set=2)[0], 'change')
        self.assertEqual(tyre_advice(180 * 60, 'SEQUENTIAL', 95., None, None, None, None), (None, []))   # joined mid-race

    def test_a_short_last_stint_keeps_even_a_twice_used_set(self):
        decision, parts = tyre_advice(180 * 60, 'SEQUENTIAL', 28., 2.5, .44, 60, 12, stints_on_set=2, lap_s=78.8)   # 32 % at the flag
        self.assertEqual((decision, say(parts)), ('keep', ['타이어는 바꾸지 않습니다.']))
        self.assertEqual(tyre_advice(160 * 60, 'CONCURRENT', 13., 2.5, .6, 43, 10, stints_on_set=1, lap_s=76.4)[0], 'keep')

    def test_rain_leaves_the_tyres_to_the_weather_calls(self):
        self.assertEqual(tyre_advice(45 * 60, None, 24., 2.5, .9, 13, 8, wet=True), (None, []))

    def test_stall_time_follows_the_rule(self):
        self.assertAlmostEqual(stall_estimate(25., 2.5, False, 'CONCURRENT'), 11.5)
        self.assertAlmostEqual(stall_estimate(25., 2.5, True, 'CONCURRENT'), 21.)          # the tyres outlast the fuel
        self.assertAlmostEqual(stall_estimate(100., 2.5, True, 'CONCURRENT'), 41.5)        # the fuel outlasts the tyres
        self.assertAlmostEqual(stall_estimate(100., 2.5, True, 'SEQUENTIAL'), 61.4)
        self.assertIsNone(stall_estimate(100., None, True, 'CONCURRENT'))

    def test_the_plan_is_never_more_than_two_short_prepared_sentences(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        for args, extra in (((45 * 60, None, 24., 2.5, .9, 13, 8, True), {}), ((160 * 60, 'CONCURRENT', 108., 2.75, .66, 30, 29, False), {}),
                            ((180 * 60, 'SEQUENTIAL', 95., 2.5, .77, 24, 24, True), {'stints_on_set': 1}),
                            ((180 * 60, 'SEQUENTIAL', 95., 2.5, .77, 48, 24, False), {'stints_on_set': 2})):
            sentences = say(tyre_advice(*args, **extra)[1])
            self.assertLessEqual(len(sentences), 2)
            for sentence in sentences:
                self.assertIn(sentence, prepared)


class PitKnowledgeTests(unittest.TestCase):
    def test_measurements_are_kept_per_track_and_car_and_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pit-knowledge.json'
            store = PitKnowledge(path)
            for loss in (16.1, 17.4, 16.3):
                store.learn(IDENTITY, transit_loss_s=loss, fuel_rate_lps=2.5, wear_pct_per_lap=.8)
            store.learn(IDENTITY, transit_loss_s=400.)                         # nonsense is not stored
            again = PitKnowledge(path)
            self.assertEqual(again.track(IDENTITY)['transit_loss_s'], 16.3)    # the median
            self.assertEqual(again.car(IDENTITY)['fuel_rate_lps'], 2.5)
            self.assertEqual(again.track({**IDENTITY, 'track_config': 'Endurance'}), {})
            self.assertEqual(again.car({**IDENTITY, 'car_id': '9'}), {})

    def test_without_a_file_nothing_is_written(self):
        store = PitKnowledge()
        self.assertTrue(store.learn(IDENTITY, transit_loss_s=20.))
        self.assertEqual(store.track(IDENTITY)['transit_loss_s'], 20.)
        self.assertEqual(store.track({}), {})


class WatchTests(unittest.TestCase):
    def sample(self, at, lap, **updates):
        return {'session_time': at, 'lap_number': lap, 'on_track': True, 'on_pit_road': False, 'speed_mps': 60., 'session_state': 4,
                'pit_sv_flags': 0x1f, 'tyre_tread_min': 1., **updates}

    def test_tread_read_in_the_stall_gives_the_wear_rate_and_a_change_restarts_the_set(self):
        watch = TyreWatch()
        watch.ingest(self.sample(0., 1, tyre_tread_min=.99))
        self.assertEqual(watch.laps_on_set(20), 20)
        # Standing in the stall the old value stays for a moment, then the fresh reading arrives;
        # the crew clears the tyres one by one while the fuel flows.
        tread = lambda i: .99 if i < 2 else .82
        flags = [0x1f, 0x1f, 0x1d, 0x1c, 0x14, 0x10, 0x10, 0x10]
        learned = [watch.ingest(self.sample(2000. + i, 24, on_pit_road=True, speed_mps=0., tyre_tread_min=tread(i), pit_sv_flags=flags[i])) for i in range(8)]
        self.assertEqual([round(v, 2) for v in learned if v], [.75])           # 18 % over 24 laps, read once
        watch.ingest(self.sample(2100., 24, tyre_tread_min=.82))
        self.assertEqual(watch.laps_on_set(30), 6)                             # all four were done: a new set
        self.assertEqual(watch.stints, 1)
        fuel_only = TyreWatch()
        fuel_only.ingest(self.sample(0., 1, tyre_tread_min=.99))
        for i in range(4):
            fuel_only.ingest(self.sample(2000. + i, 24, on_pit_road=True, speed_mps=0., tyre_tread_min=tread(i), pit_sv_flags=0x10))
        fuel_only.ingest(self.sample(2100., 24, tyre_tread_min=.82))
        self.assertEqual((fuel_only.laps_on_set(30), round(fuel_only.rate, 2)), (30, .75))
        self.assertEqual(fuel_only.stints, 2)                                  # the same set starts its second stint

    def test_transit_loss_is_the_stop_laps_against_normal_laps_less_the_standing_time(self):
        watch, at, found = PitLossWatch(), 0., []
        def lap(number, seconds, stop_s=0.):
            nonlocal at
            end = at + seconds
            while at < end:
                pit = stop_s and seconds - 30 - stop_s <= at - (end - seconds) < seconds - 10
                stalled = stop_s and seconds - 20 - stop_s <= at - (end - seconds) < seconds - 20
                found.append(watch.ingest(self.sample(at, number, on_pit_road=bool(pit), speed_mps=0. if stalled else 60.), 100.))
                at += 1 / 30
        lap(1, 100.); lap(2, 100.); lap(3, 100.)
        lap(4, 137., stop_s=12.)      # the in-lap holds entry, twelve seconds standing and the exit
        lap(5, 104.)                  # the out-lap still starts in the pit lane? no: a normal lap follows
        lap(6, 100.)
        losses = [v for v in found if v]
        self.assertEqual(len(losses), 1)
        self.assertAlmostEqual(losses[0], 137. - 100. - 12., delta=.2)


if __name__ == '__main__':
    unittest.main()
