"""A continuous pack needs one approach notice; independent packs remain audible."""
import unittest
from race_radio import RaceRadio


class TrafficPackTests(unittest.TestCase):
    def setUp(self):
        self.radio = RaceRadio()
        self.sample = dict(session_state=4, session_flags=0, on_track=True, is_replay=False, on_pit_road=False)
        self.radio.ingest(dict(self.sample, session_time=1), 'run', 1)
        self.cars = []
        self.radio.traffic.view = lambda now: dict(available=True, nearby=self.cars, ahead=None, behind=None)
        self.t = 1

    def car(self, idx, gap, approaching=True):
        return dict(idx=idx, side='behind', kind='faster_class', gap_estimate_s=gap, close=False,
                    call_key='traffic_faster_behind' if approaching else None)

    def tick(self):
        self.t += .1
        self.radio.ingest(dict(self.sample, session_time=self.t), 'run', self.t)
        return [e for e in self.radio.view(self.t)['events'] if e['key'] == 'traffic_faster_behind']

    def hear(self, event):
        self.radio.delivery(event['id'], 'completed', session_time=self.t)
        self.radio.ack(event['id'], self.t)

    def test_known_pack_gets_one_notice_before_each_car_becomes_an_approach(self):
        self.cars[:] = [self.car(35, 3.4), self.car(4, 6.8, False), self.car(8, 7.5, False), self.car(30, 10.5, False)]
        events = self.tick()
        self.assertEqual(len(events), 1)
        self.assertEqual(set(events[0]['class_notice']['cars']), {35, 4, 8, 30})
        self.hear(events[0])
        for car in self.cars:
            car['call_key'] = 'traffic_faster_behind'
        for _ in range(150):
            self.assertEqual(self.tick(), [])

    def test_a_new_car_joining_a_heard_pack_does_not_repeat_the_same_notice(self):
        self.cars[:] = [self.car(7, 1)]
        self.hear(self.tick()[0])
        self.cars.append(self.car(8, 3))
        self.assertEqual(self.tick(), [])
        self.cars.append(self.car(9, 10))
        events = self.tick()
        self.assertEqual([e['car'][0] for e in events], [9])

    def test_interrupted_notice_does_not_mark_the_pack_as_heard(self):
        self.cars[:] = [self.car(7, 1), self.car(8, 2)]
        event = self.tick()[0]
        self.radio.delivery(event['id'], 'interrupted', session_time=self.t)
        self.assertNotIn((7, 'traffic_faster_behind'), self.radio.car_calls)
        self.assertNotIn((8, 'traffic_faster_behind'), self.radio.car_calls)

    def test_a_pack_can_warn_again_after_it_passes_and_returns(self):
        self.cars[:] = [self.car(7, 1), self.car(8, 2)]
        self.hear(self.tick()[0])
        for car in self.cars:
            car.update(side='ahead', call_key=None)
        for _ in range(310):
            self.assertEqual(self.tick(), [])
        for car in self.cars:
            car.update(side='behind', call_key='traffic_faster_behind')
        self.assertEqual(len(self.tick()), 1)
