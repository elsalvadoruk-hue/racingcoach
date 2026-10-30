import unittest
from race_result import RaceResultTracker


class RaceResultTests(unittest.TestCase):
    def setUp(self):
        self.a=RaceResultTracker();self.t=0.
        self.s=dict(is_replay=False,on_track=True,session_state=3,class_position=11,
                    lap_completed=0,session_flags=0,lap_best_s=None)
        self.r=dict(session_type='Race',player_idx=0,official_grid={'class_position':11, 'overall':15, 'source':'SDK_PRESTART'},cars=[
            dict(idx=0,class_id=7,class_name='GT3',best_lap_s=107.236),
            dict(idx=1,class_id=7,best_lap_s=106.842),dict(idx=2,class_id=9,best_lap_s=95.)])
    def tick(self,run='a',**changes):
        self.s.update(changes);self.t+=.1;self.a.ingest(self.s,self.r,run,self.t)
    def finish(self,**changes):
        self.tick();self.tick(session_state=4,lap_completed=21,class_position=7)
        self.tick(session_flags=1);self.tick(lap_completed=22,session_state=6,lap_best_s=107.236,**changes)
    def test_leaders_flag_does_not_finish_own_car(self):
        self.tick();self.tick(session_flags=1,session_state=5)
        self.assertIsNone(self.a.view())
    def test_result_uses_race_grid_and_own_class_best_laps(self):
        self.finish();r=self.a.view()
        self.assertEqual((r['start_position'],r['finish_position'],r['best_lap_rank']),(11,7,2))
        self.assertEqual((r['fastest_lap_s'],r['best_gap_s'],r['recorded_drivers']),(106.842,.394,2))
        self.assertFalse(r['provisional'])
    def test_lmu_explicit_finish_does_not_require_another_crossing(self):
        self.tick();self.tick(session_state=5,finish_status=1,class_position=2)
        self.assertEqual(self.a.view()['finish_position'],2)
        self.assertFalse(self.a.view()['provisional'])
    def test_retirement_and_disqualification_do_not_become_podium_finishes(self):
        for status,label in ((2,'DNF'),(3,'DSQ')):
            self.a.reset();self.tick(finish_status=status,class_position=1)
            self.assertEqual(self.a.view()['status'],label)
    def test_replay_and_practice_never_finish_a_race(self):
        self.tick(is_replay=True,finish_status=1);self.assertIsNone(self.a.view())
        self.r['session_type']='Practice';self.tick(is_replay=False);self.assertIsNone(self.a.view())
    def test_mid_race_join_does_not_invent_a_starting_position(self):
        self.tick(session_state=4);self.tick(finish_status=1,class_position=7)
        self.assertIsNone(self.a.view()['start_position'])
    def test_session_switch_clears_old_result(self):
        self.finish();self.tick(run='b',session_state=3,session_flags=0,lap_completed=0)
        self.assertIsNone(self.a.view())
    def test_equal_millisecond_laps_share_a_rank_and_missing_records_stay_missing(self):
        self.r['cars'][1]['best_lap_s']=107.236
        self.r['cars'].append(dict(idx=3,class_id=7,best_lap_s=None))
        self.finish();r=self.a.view()
        self.assertEqual((r['best_lap_rank'],r['recorded_drivers'],r['class_drivers']),(1,2,3))

    def test_cooldown_does_not_replace_finish_position_or_retirement(self):
        self.finish();self.tick(class_position=18,on_track=False)
        self.assertEqual(self.a.view()['finish_position'],7)
        self.a.reset();self.tick(finish_status=2);self.tick(finish_status=None)
        self.assertEqual(self.a.view()['status'],'DNF')

    def test_finish_stays_visible_after_cockpit_exit_but_not_new_game_or_replay(self):
        from live_telemetry import LiveTelemetry
        from overlay_visibility import driving
        now=[0.];live=LiveTelemetry(clock=lambda:now[0])
        identity=dict(game='lmu',session_id='s',subsession_id='ss',track_id='1',car_id='2')
        s={**self.s,'session_time':0.,'session_num':0}
        live.ingest(identity,s,self.r)
        s.update(session_time=1.,session_state=6,finish_status=1,class_position=2)
        live.ingest(identity,s,self.r)
        self.assertTrue(driving(live.overlay_state()))
        live.state='WAITING';s.update(on_track=False)
        live.prepare_overlay(identity,s,5000)
        self.assertTrue(driving(live.overlay_state()))
        live.prepare_overlay({**identity,'game':'iracing'},s,5000)
        self.assertFalse(driving(live.overlay_state()))
        live.prepare_overlay(identity,{**s,'is_replay':True},5000)
        self.assertFalse(driving(live.overlay_state()))
