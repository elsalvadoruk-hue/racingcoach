"""Real-time class order, clean-lap measurement, invalidation and the LMU observation policy."""
import math
import unittest
from unittest.mock import Mock

from fast_class_pass import FastClassPass
from race_radio import RaceRadio


def frames(t, game='iracing', pass_at=384, faster_lap=90, rival_laps=0):
    me = .2+t/100
    fast = me+(t-pass_at)*(1/faster_lap-1/100)
    def car(idx, pos, cls, rank):
        return {'idx':idx, 'class_id':cls, 'lap_completed':math.floor(pos), 'distance_pct':pos%1,
                'class_position':rank, 'on_pit_road':False, 'track_surface':3}
    field = {'cars':[car(0,me,1,9),car(1,fast+1,2,1),car(2,me-.007+rival_laps,1,10)],
             'player_idx':0,'game':game,'session_type':'Race','sample_time':t,'track_length_m':4000}
    sample = {'on_track':True,'is_replay':False,'on_pit_road':False,'track_surface':3,
              'session_time':t,'session_state':4,'session_flags':0,'car_left_right':1}
    return sample, field


class ClassPassTests(unittest.TestCase):
    def setUp(self):
        self.watch = FastClassPass()
        self.radio = Mock(traffic_enabled=True, run_id='drive')
        self.radio.allows.return_value = True
        self.radio.emit.return_value = {}

    def drive(self, end=395, edit=None, **options):
        for t in range(end):
            sample, race = frames(t, **options)
            if edit:
                edit(t,sample,race)
            self.watch.update(self.radio,sample,race,t)
            if self.radio.emit.called:
                return t
        return None

    def test_dynamic_rank_and_one_notice_per_pass(self):
        t = self.drive()
        self.assertIsNotNone(t)
        event = self.radio.emit.call_args
        self.assertEqual(event.args[0],'class_pass_rival')
        self.assertIn('10위',self.radio.emit.return_value['race_short_text'])
        kind, token = event.kwargs['condition']
        self.assertEqual(kind,'class_pass')
        self.assertTrue(self.watch.valid(token,t))
        self.assertFalse(self.watch.valid(token,t+1.75))
        for sec in range(t+1,t+12):
            self.watch.update(self.radio,*frames(sec),sec)
        self.radio.emit.assert_called_once()
        self.assertFalse(self.watch.valid(token,t+12))

    def test_rejects_stale_order_overlap_and_new_fast_car_at_start(self):
        for reason in ('overlap','own_rank','rival_pits','new_rear','replay','own_pits','flags','gap'):
            with self.subTest(reason=reason):
                self.setUp()
                t=self.drive()
                token=self.radio.emit.call_args.kwargs['condition'][1]
                sample,race=frames(t+1)
                if reason=='overlap':sample['car_left_right']=2
                if reason=='own_rank':sample['position_confirmed']=None
                if reason=='new_rear':
                    other={**race['cars'][2],'idx':4,'class_id':2}
                    other['distance_pct']+=.002
                    race['cars'].append(other)
                if reason=='rival_pits':race['cars'][2]['on_pit_road']=True
                if reason=='replay':sample['is_replay']=True
                if reason=='own_pits':sample['on_pit_road']=True
                if reason=='flags':sample['session_flags']=0x4000
                if reason=='gap':race['sample_time']-=3
                # The fast player stream can invalidate this without a new 1Hz timing frame.
                new_timing=race if reason in ('gap','new_rear','rival_pits') else None
                self.watch.update(self.radio,sample,new_timing,t+1)
                self.assertFalse(self.watch.valid(token,t+1))

    def test_lmu_candidate_is_recorded_without_emission(self):
        with self.assertLogs('iRacingLive',level='INFO'):
            self.assertIsNone(self.drive(game='lmu'))
        self.assertEqual(self.watch.observation['rank'],10)
        self.assertEqual(self.watch.observation['game'],'lmu')
        self.assertIsNone(self.watch.token)

    def test_predicted_room_tracks_a_new_closing_rate_before_playback(self):
        t = self.drive()
        event = self.radio.emit.return_value
        self.assertIsNone(event['class_pass_contact_at'])
        sample, race = frames(t + 1)
        race['cars'][2]['distance_pct'] += .004
        self.watch.update(self.radio, sample, race, t + 1)
        self.assertAlmostEqual(event['class_pass_contact_at'] - (t + 1), .25)

    def test_pace_requires_two_clean_laps_and_matching_physical_competitor(self):
        for reason in ('one_lap','same_pace','lapped_rival','wrong_rank','missing_order','broken_motion','disabled'):
            with self.subTest(reason=reason):
                self.setUp()
                if reason=='disabled':self.radio.allows.return_value=False
                def edit(t,sample,race):
                    if reason=='one_lap' and t==310:race['cars'][1]['on_pit_road']=True
                    if reason=='wrong_rank':race['cars'][2]['class_position']=11
                    if reason=='missing_order' and t<385:race['cars'].pop()
                    if reason=='broken_motion' and t==384:race['cars'][1]['distance_pct']+=.15
                self.assertIsNone(self.drive(edit=edit,faster_lap=98 if reason=='same_pace' else 90,
                                             rival_laps=-1 if reason=='lapped_rival' else 0))

    def test_confirmed_position_crossing_start_line(self):
        def edit(t,sample,race):
            sample['position_confirmed']=9
        self.assertIsNotNone(self.drive(end=393,edit=edit,pass_at=378))

    def test_live_radio_frame_and_render_lookup_withdraw_after_overlap(self):
        radio = RaceRadio()
        for tick in range(1600):
            t = tick / 4
            sample, metadata = frames(t)
            sample.update(speed_mps=40, brake=0, steering_rad=0, throttle=1)
            radio.ingest(sample, 'drive', t, metadata if tick % 4 == 0 else None)
            events = [e for e in radio.view(t)['events'] if e['key'] == 'class_pass_rival']
            if events:
                break
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['alert_categories'], ['spotter.class_pass_rival'])
        self.assertEqual(radio.get(event['id'], t)['condition'][0], 'class_pass')
        self.assertTrue(event['prepared_only'])
        sample.update(car_left_right=2, session_time=t + .1)
        radio.ingest(sample, 'drive', t + .1)
        self.assertIsNone(radio.get(event['id'], t + .1))


if __name__=='__main__':
    unittest.main()
