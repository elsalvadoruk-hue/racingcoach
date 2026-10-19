import math
import unittest

import radio_phrases
from live_damage import DamageWatch, PitStopWatch, damage_parts, repair_parts


class DamageWatchTests(unittest.TestCase):
    def setUp(self):
        self.watch, self.incidents = DamageWatch(), 0

    def lap(self, number, seconds, vmax_kph=270., steer_deg=.4, hit=0, signal=False, clean=True, pit=False):
        """One lap as 30Hz samples: a corner, then a flat-out straight held at `steer_deg`."""
        for i in range(400):
            straight = i >= 150
            if i == 100 and hit:
                self.incidents += hit
            self.watch.ingest({'lap_number': number, 'on_track': True, 'is_replay': False, 'on_pit_road': pit and i < 50,
                               'speed_mps': (vmax_kph if straight else 120.) / 3.6, 'throttle': 1. if straight else .4,
                               'yaw_rate': 0. if straight else .3, 'steering_rad': math.radians(steer_deg) if straight else .8,
                               'incidents': self.incidents, 'engine_warnings': 0x100 if signal and i >= 100 else 0})
        # The next lap begins, then the radio reports the finished lap if it was clean.
        self.watch.ingest({'lap_number': number + 1, 'on_track': True, 'is_replay': False, 'on_pit_road': False, 'speed_mps': 60.,
                           'throttle': .5, 'yaw_rate': .2, 'steering_rad': .5, 'incidents': self.incidents, 'engine_warnings': 0})
        return self.watch.lap_done(seconds) if clean and not hit else None

    def say(self, result):
        return [radio_phrases.build(key, 0, **values)['text'] for key, values in damage_parts(result)]

    def test_a_bent_car_that_is_no_slower_is_told_as_it_is(self):
        self.assertIsNone(self.lap(1, 477.2, 272.4, .43))
        self.assertIsNone(self.lap(2, 479.5, 272.4, .49))
        self.assertIsNone(self.lap(3, 481.0, 272.7, .89, hit=4))            # the contact lap itself is never judged
        found = self.lap(4, 477.6, 273.4, 1.03)
        self.assertEqual((found['slower_s'], found['vmax_loss_kph'], round(found['steer_deg'], 2)), (0., 0., .57))
        self.assertEqual(self.say(found), ['충돌 뒤 랩타임과 최고속은 그대로입니다.', '직선에서 핸들이 0.6도 틀어졌습니다.'])
        self.assertIsNone(self.lap(5, 475.9, 273.8, 1.0))                    # said once per contact

    def test_slower_laps_and_lost_top_speed_are_given_with_their_numbers(self):
        for number, seconds in ((1, 100.2), (2, 100.6), (3, 100.4)):
            self.lap(number, seconds, 270., .3)
        self.lap(4, 104., 268., .3, signal=True, clean=False)                # the repair signal came on
        found = self.lap(5, 101.9, 264., .35)
        self.assertEqual(self.say(found), ['충돌 뒤 랩타임이 1.5초 느려졌습니다.', '직선 최고속이 6킬로미터 떨어졌습니다.'])

    def test_normal_spread_is_not_called_damage(self):
        for number, seconds in ((1, 476.6), (2, 479.9)):
            self.lap(number, seconds, 270.5, .40)
        self.lap(3, 485., 270., .45, hit=2)
        found = self.lap(4, 479.5, 269.8, .52)                               # inside what the laps before already showed
        self.assertEqual(self.say(found), ['충돌 뒤 랩타임과 최고속은 그대로입니다.', '핸들 중심도 그대로입니다.'])

    def test_nothing_is_said_without_laps_to_compare_or_after_a_stop(self):
        self.lap(1, 100., 270., .3)
        self.lap(2, 101., 270., .3, hit=4)
        self.assertIsNone(self.lap(3, 103., 262., 1.2))                      # one lap before the contact is no baseline
        fresh = DamageWatch()
        self.watch = fresh
        for number in (1, 2, 3):
            self.lap(number, 100., 270., .3)
        self.lap(4, 100., 270., .3, hit=4)
        self.lap(5, 130., 270., .3, pit=True, clean=False)                   # repaired or re-tyred: the old laps no longer compare
        self.assertIsNone(self.lap(6, 103., 262., 1.2))
        self.assertIsNone(self.lap(7, 103., 262., 1.2))

    def test_a_light_off_track_moment_does_not_start_a_check(self):
        for number in (1, 2):
            self.lap(number, 100., 270., .3)
        self.lap(3, 101., 270., .3, hit=1)                                   # 1x: not a contact
        self.assertIsNone(self.lap(4, 103., 262., 1.2))

    def test_every_sentence_is_prepared(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        for result in ({'slower_s': 1.5, 'vmax_loss_kph': 6., 'steer_deg': .6}, {'slower_s': 0., 'vmax_loss_kph': 0., 'steer_deg': 0.},
                       {'slower_s': 0., 'vmax_loss_kph': 0., 'steer_deg': None}):
            for sentence in self.say(result):
                self.assertIn(sentence, prepared)


class PitStopTests(unittest.TestCase):
    def stop(self, watch, start, fuel_from, fuel_to, seconds, repair=0., mandatory=0., planned=98., flags=0x1f, capacity=98.):
        """A stop at 30Hz: fuel flows at once; the repair time is there from the first moment in the stall."""
        found = None
        for i in range(int(seconds * 30) + 1):
            elapsed = i / 30
            said = watch.ingest({'session_time': start + elapsed, 'on_pit_road': True, 'pit_sv_fuel': planned, 'pit_sv_flags': flags,
                                 'fuel_l': fuel_from + (fuel_to - fuel_from) * min(1., elapsed / seconds),
                                 'pit_repair_left_s': mandatory, 'pit_optional_repair_left_s': repair}, capacity)
            found = found or said
        watch.ingest({'session_time': start + seconds + 1, 'on_pit_road': False, 'fuel_l': fuel_to}, capacity)
        return found

    def say(self, stop, damage=None, laps_to_go=None):
        return [radio_phrases.build(key, 0, **values)['text'] for key, values in repair_parts(stop, damage, laps_to_go)]

    def test_the_repair_is_given_with_the_fuel_time_and_what_the_contact_cost(self):
        watch = PitStopWatch()
        self.assertIsNone(self.stop(watch, 0., 1.1, 98., 116.4))                       # an ordinary stop teaches the fuelling rate
        self.assertAlmostEqual(watch.fuel_rate(), .832, places=2)
        found = self.stop(watch, 5000., 1.9, 98., 115.5, repair=84.1)
        self.assertEqual((found['kind'], round(found['repair_s']), round(found['fuel_s'])), ('optional', 84, 115))
        unchanged = {'slower_s': 0., 'vmax_loss_kph': 0., 'steer_deg': .6}
        self.assertEqual(self.say(found, unchanged), ['선택 수리는 84초입니다.', '급유는 약 115초 걸립니다.', '직선에서 핸들이 0.6도 틀어졌습니다.'])
        # The lap time held but the car lost top speed: that is said, never "no lap time lost".
        slow_straight = {'slower_s': 0., 'vmax_loss_kph': 7., 'steer_deg': 0.}
        self.assertEqual(self.say(found, slow_straight)[-1], '직선 최고속이 7킬로미터 떨어졌습니다.')
        same = {'slower_s': 0., 'vmax_loss_kph': 0., 'steer_deg': 0.}
        self.assertEqual(self.say(found, same)[-1], '충돌 뒤 랩타임과 최고속은 그대로입니다.')
        slower = {'slower_s': 1.5, 'vmax_loss_kph': 0., 'steer_deg': 0.}
        self.assertEqual(self.say(found, slower)[-1], '충돌 뒤 랩당 1.5초 느려져 있습니다.')

    def test_an_optional_repair_is_weighed_to_the_finish(self):
        stop = {'kind': 'optional', 'repair_s': 81., 'fuel_s': None}
        # 2026-10-12 at Le Mans: 7 km/h of top speed gone, about 1.9 s a lap, six laps left, 81 s in the stall.
        damage = {'slower_s': 0., 'vmax_loss_kph': 7., 'steer_deg': 0., 'loss_per_lap_s': 1.9}
        self.assertEqual(self.say(stop, damage, 6), ['선택 수리는 81초입니다.', '수리하지 않는 쪽이 약 70초 빠릅니다.'])
        # The same damage with forty laps to go, and the fuel taking most of the repair time anyway.
        long_stop = {'kind': 'optional', 'repair_s': 81., 'fuel_s': 60.}
        self.assertEqual(self.say(long_stop, damage, 40), ['선택 수리는 81초입니다.', '수리하는 쪽이 약 55초 빠릅니다.'])
        self.assertEqual(self.say(long_stop, damage, 12)[-1], '수리해도 안 해도 비슷합니다.')
        # Nothing measured on track, or a repair that is not a choice: the times are given as before.
        self.assertEqual(self.say(long_stop, None, 12), ['선택 수리는 81초입니다.', '급유는 약 60초 걸립니다.'])
        must = {'kind': 'mandatory', 'repair_s': 35., 'fuel_s': None}
        self.assertEqual(self.say(must, damage, 6), ['필수 수리는 35초입니다.', '직선 최고속이 7킬로미터 떨어졌습니다.'])

    def test_lost_top_speed_is_turned_into_seconds_a_lap(self):
        watch = DamageWatch()
        entry = lambda lap, vmax: {'lap': lap, 'time': 240., 'vmax': vmax, 'steer': .4, 'flat_s': 150.}
        watch.history.extend([entry(2, 290.), entry(3, 290.)])
        watch.contact_lap, watch.finished = 4, {'lap': 5, 'vmax': 283., 'steer': .4, 'flat_s': 150.}
        found = watch.lap_done(239.)                                # a faster lap all the same (a tow, a better lap)
        self.assertEqual((found['slower_s'], found['vmax_loss_kph']), (0., 7.))
        self.assertAlmostEqual(found['loss_per_lap_s'], 150. * 7. / 290. * .5)      # 1.8 s a lap

    def test_only_what_is_known_is_said_and_only_once_per_stop(self):
        first = PitStopWatch()
        found = self.stop(first, 0., 20., 60., 48., mandatory=35.)                    # the first stop of the session: no rate yet
        self.assertEqual((found['kind'], found['fuel_s']), ('mandatory', None))
        self.assertEqual(self.say(found), ['필수 수리는 35초입니다.'])                   # no comparison either: nothing is added
        watch = PitStopWatch()
        self.stop(watch, 0., 1., 98., 116.)
        splash = self.stop(watch, 3000., 60., 60., 30., repair=40., flags=0xf)          # fuel is not ticked
        self.assertIsNone(splash['fuel_s'])
        top_up = self.stop(watch, 6000., 90., 98., 10., repair=40., planned=98.)        # the tank only has room for 8 litres
        self.assertEqual(round(top_up['fuel_s']), 10)

    def test_every_sentence_is_prepared(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        for stop in ({'kind': 'optional', 'repair_s': 84.1, 'fuel_s': 115.5}, {'kind': 'mandatory', 'repair_s': 12., 'fuel_s': None}):
            for damage in (None, {'slower_s': 1.5, 'vmax_loss_kph': 7., 'steer_deg': .6}, {'slower_s': 0., 'vmax_loss_kph': 0., 'steer_deg': None}):
                for sentence in self.say(stop, damage):
                    self.assertIn(sentence, prepared)


if __name__ == '__main__':
    unittest.main()
