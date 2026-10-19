import unittest
from collections import deque

from live_telemetry import LiveTelemetry
from test_race_engineer import race_fixture


def sample(at, **updates):
    return {'session_time': at, 'session_num': 0, 'lap_number': 4, 'lap_completed': 3, 'distance_pct': .5,
            'on_track': True, 'is_replay': False, 'on_pit_road': False, 'session_state': 4, 'session_flags': 0,
            'speed_mps': 70., 'brake': 0., 'throttle': 1., 'steering_rad': 0., 'fuel_l': 40., 'incidents': 0,
            'session_time_remaining_s': 45 * 60., **updates}


class StraightBriefTests(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.]
        self.live = LiveTelemetry(clock=lambda: self.clock[0])
        self.race = race_fixture()
        self.live.radio.ingest(sample(0), 'run', self.clock[0], {'session_type': 'Race'})
        self.live.radio.pending.clear()
        self.live.pit_wall.ingest(self.race, 'run', 0)
        self.live.pit_wall.paces = {i: deque([100.] * 3) for i in range(3)}

    def brief(self, seconds=0., **updates):
        """Move the clock, feed one sample of a learned stretch and return the line queued for it."""
        self.clock[0] += seconds
        s = sample(self.clock[0] - 1000., **{'workload_in_s': 12., **updates})
        self.live.radio.latest, self.live.radio.last_sample = dict(s), self.clock[0]
        self.live.radio.pending.clear()
        self.live._brief_straight(s, self.clock[0])
        return next((e for e in self.live.radio.pending if e['key'] == 'straight_brief'), None)

    def test_a_long_straight_gets_one_measured_line_and_lines_rotate(self):
        self.assertIsNone(self.brief(workload_in_s=5.))        # not a long straight
        self.assertIsNone(self.brief(workload_in_s=None))      # braking points not learned yet
        first = self.brief()   # the car ahead is 30 seconds up the road: not worth a line
        self.assertEqual((first['speech_text'], first['engineer'], first['priority']), ('뒤차와 2.0초 차.', 'race', 1))
        self.assertEqual(first['condition'], ('straight', 5.))
        self.assertIsNone(self.brief(10))                      # lines are at least 40 seconds apart
        self.assertEqual(self.brief(40)['speech_text'], '현재 클래스 2위입니다.')
        self.assertEqual(self.brief(40)['speech_text'], '45분 남았습니다.')
        leader = self.brief(40)   # a label and a lap-time sentence that already exists, as one call
        self.assertEqual((leader['speech_parts'], leader['engineer']), (['클래스 선두 지난 랩입니다.', '1분 39초 0입니다.'], 'spotter'))
        self.assertEqual(self.brief(40)['speech_text'], '뒤차까지 2.0초입니다.')   # round again, in another wording
        self.assertIsNone(self.brief(40))                      # nothing new to say yet

    def test_the_line_expires_when_the_straight_is_nearly_over(self):
        event = self.brief()
        self.assertTrue(self.live.radio.valid(event, self.clock[0]))
        self.live.radio.latest['workload_in_s'] = 3.
        self.assertFalse(self.live.radio.valid(event, self.clock[0]))

    def test_the_worst_corner_of_the_lap_just_driven_comes_first_and_only_with_a_confirmed_number(self):
        self.live.corners.review = {'lap': 3, 'compared': 9, 'losses': [{'id': 'C5', 'number': 5, 'call': '5번 코너', 'delta_s': .42, 'cause': 'time'}]}
        self.assertEqual(self.brief()['speech_text'], '지난 랩은 5번 코너에서 0.4초 잃었습니다.')
        self.assertNotIn('코너', self.brief(40)['speech_text'])   # once per lap
        self.live.corners.review = {'lap': 4, 'compared': 9, 'losses': [{'id': 'C2', 'number': 2, 'call': None, 'delta_s': .3, 'cause': 'time'}]}
        self.assertNotIn('코너', self.brief(40, lap_number=5)['speech_text'])   # an unconfirmed segment is never numbered aloud

    def test_two_corners_are_two_whole_sentences_in_one_call_when_the_straight_is_long_enough(self):
        losses = [{'id': 'C5', 'number': 5, 'call': '5번 코너', 'delta_s': .42, 'cause': 'time'},
                  {'id': 'C9', 'number': 9, 'call': '9번 코너', 'delta_s': .21, 'cause': 'time'}]
        self.live.corners.review = {'lap': 3, 'compared': 9, 'losses': losses}
        event = self.brief(workload_in_s=14.)
        self.assertEqual(event['speech_parts'], ['지난 랩은 5번 코너에서 0.4초 잃었습니다.', '9번 코너에서도 0.2초 잃었습니다.'])
        self.assertEqual(event['speech_text'], ' '.join(event['speech_parts']))
        self.assertEqual(event['condition'], ('straight', 8.))   # two sentences need more straight
        fresh = LiveTelemetry(clock=lambda: self.clock[0])
        self.live = fresh
        self.setUp()
        self.live.corners.review = {'lap': 3, 'compared': 9, 'losses': losses}
        short = self.brief(workload_in_s=9.)
        self.assertNotIn('speech_parts', short)                  # room for one sentence only
        self.assertEqual(short['speech_text'], '지난 랩은 5번 코너에서 0.4초 잃었습니다.')

    def test_best_corner_follows_the_losses_when_the_straight_has_room(self):
        self.live.corners.review = {'lap': 3, 'compared': 9, 'losses': [{'id': 'C5', 'number': 5, 'call': '5번 코너', 'delta_s': .42, 'cause': 'time'}],
                                    'best_gain': {'id': 'C2', 'number': 2, 'call': '2번 코너', 'delta_s': -.31}}
        event = self.brief(workload_in_s=20.)
        self.assertEqual(event['speech_parts'], ['지난 랩은 5번 코너에서 0.4초 잃었습니다.', '가장 좋았던 곳은 2번 코너입니다.', '0.3초 줄였습니다.'])
        self.assertEqual(event['condition'], ('straight', 11.))

    def test_the_lap_in_progress_is_compared_with_the_best_lap_once_per_lap(self):
        self.live.lap_sectors.best_lap = [5.] * 20
        self.live.lap_sectors.best_lap_s = 100.
        self.live.lap_sectors.clean, self.live.lap_sectors.lap = True, 4
        self.live.lap_sectors.marks = [0., 5., 10., 15., 20., 25., 29.6]      # six sectors in 29.6 s against 30.0
        self.race['session_type'] = 'Practice'
        self.live.pit_wall.ingest(self.race, 'run', 1)
        event = self.brief()
        self.assertEqual((event['speech_text'], event['engineer']), ('베스트보다 0.4초 앞서 있습니다.', 'coach'))
        self.assertIsNone(self.brief(40))                                      # once for this lap
        self.live.lap_sectors.marks = [200., 205.7]                            # too early in the next lap to say anything
        self.live.lap_sectors.lap = 5
        self.assertIsNone(self.brief(40, lap_number=5))

    def test_theoretical_best_is_a_label_the_lap_time_and_the_gain(self):
        self.live.lap_sectors.best_lap, self.live.lap_sectors.best_lap_s = [5.] * 20, 100.
        self.live.lap_sectors.best_sectors = [4.97] * 20
        self.race['session_type'] = 'Practice'
        self.live.pit_wall.ingest(self.race, 'run', 1)
        event = self.brief(workload_in_s=20.)
        self.assertEqual(event['speech_parts'], ['이론상 베스트입니다.', '1분 39초 4입니다.', '지금 베스트보다 0.6초 빠릅니다.'])
        self.assertIsNone(self.brief(700))                                     # not again until it improves
        self.live.lap_sectors.best_sectors = [4.95] * 20
        self.assertEqual(self.brief(40)['speech_parts'][1], '1분 39초 0입니다.')

    def test_where_the_driver_is_faster_and_slower_than_the_car_behind(self):
        self.live.rival_sectors.times = {1: {2: [10., 10.], 5: [12., 12.]}, 2: {2: [10.5, 10.5], 5: [11.6, 11.6]}}
        self.live.corners.definitions = [{'apex_pct': .30, 'call': '5번 코너'}]
        event = self.brief(workload_in_s=30.)
        self.assertEqual(event['engineer'], 'race')
        self.assertEqual(event['speech_parts'], ['뒤차와 2.0초 차.', '뒤차보다 빠른 곳은 5번 코너 부근입니다.', '0.5초 빠릅니다.',
                                                 '뒤차가 빠른 곳은 6번 구간입니다.', '0.4초 느립니다.'])
        # On a shorter straight later on there is room for the gap and one of the two places.
        later = [self.brief(100, workload_in_s=14.) for _ in range(6)]
        again = next(e for e in later if e and len(e.get('speech_parts') or []) == 3 and '뒤차' in e['speech_parts'][1])
        self.assertIn('뒤차', again['speech_parts'][0])

    def test_what_it_takes_to_catch_the_car_ahead(self):
        self.race['cars'][0].update(distance_pct=.58)          # eight seconds up the road
        self.race['total_laps'] = 20
        self.live.pit_wall.ingest(self.race, 'run', 2)
        seen = {}
        for _ in range(8):
            event = self.brief(40, workload_in_s=30.)
            if event and len(event.get('speech_parts') or []) > 1:
                seen[event['speech_parts'][0]] = event['speech_parts']
        self.assertEqual(seen['앞차와 8.0초 차.'][1], '끝까지 닿으려면 랩당 0.5초 빨라야 합니다.')   # 8 s over 16.5 laps

    def test_qualifying_only_gets_what_helps_the_lap(self):
        self.live.radio.qualifying = True
        self.assertIsNone(self.brief())                        # no gaps, places or other cars
        self.live.lap_sectors.best_lap, self.live.lap_sectors.best_lap_s = [5.] * 20, 100.
        self.live.lap_sectors.clean, self.live.lap_sectors.lap = True, 4
        self.live.lap_sectors.marks = [0., 5., 10., 15., 20., 25., 30.5]
        self.assertEqual(self.brief()['speech_text'], '베스트보다 0.5초 뒤져 있습니다.')
        self.live.radio.qualifying = False

    def test_the_first_clean_lap_after_a_contact_is_reported_as_one_call(self):
        self.live.damage.lap_done = lambda seconds: {'lap': 18, 'baseline_laps': 2, 'slower_s': 0., 'vmax_loss_kph': 0., 'steer_deg': .63}
        self.live.radio.laps.append({'lap': 18, 'time': 477.6, 'incidents': 4})
        self.live._track_sectors({'car_id': '1', 'track_id': '2'}, sample(5.), None, 5.)
        event = next(e for e in self.live.radio.pending if e['key'] == 'damage_check')
        self.assertEqual((event['speech_parts'], event['engineer']),
                         (['충돌 뒤 랩타임과 최고속은 그대로입니다.', '직선에서 핸들이 0.6도 틀어졌습니다.'], 'race'))
        self.live.radio.pending.clear()
        self.live._track_sectors({'car_id': '1', 'track_id': '2'}, sample(6.), None, 6.)   # the same lap is not reported twice
        self.assertFalse(self.live.radio.pending)

    def test_pit_loss_fills_itself_from_measured_stops_and_the_tyre_plan_rides_along(self):
        self.live.identity = {'track_id': '523', 'track_config': 'Grand Prix', 'car_id': '132'}
        s = sample(5., fuel_l=10., pit_sv_flags=0x1f)
        empty = self.live._pit_view(s, 2., 3)
        self.assertEqual((empty['pit_loss_auto_s'], empty['pit_loss_source']), (None, None))     # no stop was ever measured here
        self.live.pit_knowledge.learn(self.live.identity, transit_loss_s=16.3, fuel_rate_lps=2.5, wear_pct_per_lap=.8)
        view = self.live._pit_view(s, 2., 3)
        # 4 litres at 2.5 L/s and the release, on top of this track's transit loss; a ten-lap race keeps its tyres.
        self.assertEqual((view['pit_transit_loss_s'], view['pit_stall_estimate_s'], view['pit_loss_auto_s'], view['pit_loss_source']), (16.3, 3.1, 19.4, 'AUTO'))
        self.assertEqual((view['tyre_decision'], view['tyre_parts']), ('keep', [['tyre_keep', {}], ['tyre_setting_on', {}]]))
        self.assertEqual(view['pit_loss_s'], 19.4)
        typed = self.live._pit_view(s, 2., 3, pit_loss_s=30.)
        self.assertEqual((typed['pit_loss_s'], typed['pit_loss_source']), (30., 'INPUT'))        # the driver's own number wins

    def test_silent_in_qualifying_in_the_pits_under_yellow_and_while_another_call_waits(self):
        self.assertIsNone(self.brief(on_pit_road=True))
        self.assertIsNone(self.brief(session_flags=0x8))
        self.live.radio.qualifying = True
        self.assertIsNone(self.brief())
        self.live.radio.qualifying = False
        self.live.radio.emit('lap_time', self.clock[0], '4랩, 1분 40초입니다.', ttl=45)
        waiting = list(self.live.radio.pending)
        s = sample(self.clock[0] - 1000., workload_in_s=12.)
        self.live.radio.latest, self.live.radio.last_sample = dict(s), self.clock[0]
        self.live._brief_straight(s, self.clock[0])
        self.assertEqual(list(self.live.radio.pending), waiting)

    def test_practice_has_no_race_lines(self):
        self.race['session_type'] = 'Practice'
        self.live.pit_wall.ingest(self.race, 'run', 1)
        self.assertIsNone(self.brief())


if __name__ == '__main__':
    unittest.main()
