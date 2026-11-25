import unittest

from race_radio import RaceRadio
from reentry_approach import REENTRY_WORDS


class ReentryRadioTests(unittest.TestCase):
    def setUp(self):
        self.radio = RaceRadio()
        self.tick = 0
        self.arrivals = {11: 8.8}
        self.game = 'iracing'
        self.options = {}

    def advance(self, end, *, on_course=False, traffic=True, options=None):
        latest = None
        while self.tick <= round(end*10):
            t = self.tick/10
            surface = 3 if t < 1 or on_course else 0
            sample = dict(session_time=t, session_state=4, session_flags=0, is_replay=False,
                          on_track=True, on_pit_road=False, track_surface=surface, lap_number=3,
                          lap_completed=2, distance_pct=.5, car_left_right=1)
            meta = None
            if self.tick % 10 == 0:
                car = dict(track_surface=3, lap_completed=2, on_pit_road=False, class_id=1)
                cars = [dict(car,idx=0,track_surface=surface,distance_pct=.5)]
                if traffic:
                    cars.extend(dict(car,idx=i,distance_pct=.5+(t-arrival)*20/5000) for i,arrival in self.arrivals.items())
                meta=dict(game=self.game, session_type='Race', track_length_m=5000, player_idx=0,
                          sample_time=t, cars=cars, position_age_s=0, position_source='OBSERVED',
                          scoring_sample_time=t, track_name='Montreal', **self.options)
                meta.update(options or {})
            self.radio.ingest(sample,'run',t,meta)
            latest = next((e for e in self.radio.view(t)['events'] if e['key']=='reentry_approach'),None)
            self.tick+=1
        return latest

    def test_countdown_becomes_untimed_while_waiting_and_disappears_on_return(self):
        for self.game in ('iracing','lmu'):
            self.tick, self.radio = 0, RaceRadio()
            timed=self.advance(3.)
            self.assertEqual(timed['reentry_take'],'first_5')
            self.assertEqual(timed['race_short_text'],REENTRY_WORDS['first_5'][1])
            self.assertTrue(timed['prepared_only'])
            untimed=self.advance(3.9)
            self.assertEqual(untimed['reentry_take'],'first')
            self.assertNotEqual(timed['id'],untimed['id'])
            self.assertIsNone(self.radio.get(timed['id'],3.9))
            self.assertIsNone(self.advance(4.1,on_course=True))
            self.assertIsNone(self.radio.get(untimed['id'],4.1))

    def test_follow_requires_completed_first_and_positive_passage(self):
        self.arrivals={11:5.5,12:10.5}
        first=self.advance(2)
        self.assertEqual(first['reentry_take'],'first')
        self.radio.delivery(first['id'],'started')
        self.assertIsNone(self.advance(3))
        self.radio.delivery(first['id'],'completed')
        self.assertIsNone(self.advance(5.9))
        self.assertEqual(self.advance(6)['reentry_take'],'follow')

    def test_ack_without_hearing_is_not_a_follow_and_signal_loss_is_not_passage(self):
        self.arrivals={11:5.5,12:10.5}
        first=self.advance(2)
        self.radio.ack(first['id'],2)
        self.assertEqual(self.advance(6)['reentry_take'],'first')
        self.setUp()
        self.arrivals={11:5.5,12:10.5}
        first=self.advance(2)
        self.radio.delivery(first['id'],'completed')
        self.arrivals.pop(11)
        self.assertTrue(self.advance(6)['reentry_take'].startswith('first'))

    def test_two_close_cars_are_one_train(self):
        self.arrivals={11:5.,12:5.8}
        event=self.advance(2)
        self.assertEqual(event['reentry_take'],'train')
        self.radio.delivery(event['id'],'completed')
        self.assertIsNone(self.advance(5.5))

    def test_live_overlap_cancels_the_call_before_the_next_field_sample(self):
        timed=self.advance(3)
        sample={**self.radio.latest,'session_time':3.1,'car_left_right':2}
        self.radio.ingest(sample,'run',3.1,None)
        self.assertIsNone(self.radio.get(timed['id'],3.1))

    def test_stale_held_missing_and_disabled_are_quiet(self):
        self.game='lmu'
        self.assertIsNone(self.advance(3,options={'position_source':'HELD'}))
        self.setUp()
        self.radio.configure_alerts(spotter={'reentry_approach':False})
        self.assertIsNone(self.advance(4))
        self.setUp()
        self.assertIsNone(self.advance(3,options={'track_name':'Figure Eight'}))
        self.setUp()
        timed=self.advance(3)
        self.advance(4,traffic=False)
        self.assertIsNone(self.radio.get(timed['id'],4))

if __name__=='__main__':
    unittest.main()
