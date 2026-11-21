"""Exercise the actual corner geometry, setting attribution, display and silent routing."""
import unittest
from unittest.mock import Mock

from corner_controls import CornerControls
from live_corners import LiveCornerCoach
from race_radio import RaceRadio


CORNERS = [dict(id='C7', call='9번 코너', entry_pct=.3, apex_pct=.34, exit_pct=.39),
           dict(id='C10', call='14번 코너', entry_pct=.7, apex_pct=.74, exit_pct=.79)]


class CornerControlTests(unittest.TestCase):
    def setUp(self):
        self.controls = CornerControls()
        self.corners = LiveCornerCoach(lambda identity: CORNERS)
        self.radio = RaceRadio()
        self.s = dict(on_track=True, is_replay=False, on_pit_road=False, session_state=4, session_flags=0,
                      session_time=0., session_num=1, speed_mps=40., track_surface=3, incidents=0, car_left_right=1,
                      fuel_l=30., track_temp_c=24., tire_compound=1, track_wetness=1, brake_bias=51., abs_setting=3,
                      tc_setting=3, brake=0., throttle=1., steering_rad=0., yaw_rate=0., abs_active=False)

    def pass_corner(self, lap, corner=0, *, bias=51., seconds=4., context='SOLO', correction=False,
                    brake_correction=False, alter=None):
        d = CORNERS[corner]
        start, end = d['entry_pct'], d['exit_pct']
        for i in range(-1, 62):
            pct = start+(end-start)*i/60
            braking = pct <= d['apex_pct']
            self.s.update(lap_number=lap, session_time=self.s['session_time']+seconds/60, distance_pct=pct,
                          brake_bias=bias, brake=.6 if braking else 0., throttle=0. if braking else .9)
            if alter:
                alter(self.s, i)
            self.corners.ingest(dict(self.s, traffic_context=context), {}, self.s['session_time'], 4000)
            self.controls.ingest(self.corners, self.s, context, brake_correction if braking else correction)

    def approach(self, lap, corner=0, *, session='Race', **fields):
        self.s.update(dict(lap_number=lap, distance_pct=CORNERS[corner]['entry_pct']-.11,
                           brake=0., throttle=1., steering_rad=0., speed_mps=40.), **fields)
        self.s['session_time'] += .3
        self.controls.update_card(self.corners, self.s, self.s['session_time'], 4000, session, 'SOLO', self.radio, False)
        return self.controls.view()

    def test_two_corners_prefer_opposite_values_and_the_return_is_observed(self):
        for lap in range(1,9):
            bias = 51. if lap%2 else 50.75
            self.pass_corner(lap, 0, bias=bias, seconds=4. if lap%2 else 3.65)
            self.pass_corner(lap, 1, bias=bias, seconds=3.65 if lap%2 else 4.)
        self.assertIsNone(self.approach(9, brake_bias=50.75))
        card = self.approach(9, brake_bias=51.)
        self.assertEqual((card['target'],card['text']), (50.75,'다음 9번 코너, 앞 바이어스 50.75퍼센트.'))
        self.assertIsNone(self.approach(9, brake_bias=50.75))
        card = self.approach(9, 1, brake_bias=50.75)
        self.assertEqual(card['text'], '다음 14번 코너, 앞 바이어스 51퍼센트로 복귀하세요.')
        self.assertFalse(self.radio.pending)

    def test_clean_same_corner_three_lap_trial_only_practice(self):
        for lap in range(1,4):
            self.pass_corner(lap, correction=True)
        self.assertIsNone(self.approach(4))
        card = self.approach(4, session='Practice')
        self.assertIn('9번 코너 탈출',card['text'])
        self.assertIsNone(card['target'])
        self.assertEqual(card['action'],'tc')
        self.assertIsNone(self.approach(4, session='Practice', brake=.3))
        self.assertIsNone(self.approach(5, session='Practice', tc_setting=4))
        self.assertFalse(self.radio.pending)

    def test_setting_at_braking_not_knob_position_and_no_mid_phase_change(self):
        self.pass_corner(1, alter=lambda s,i:s.update(brake_bias=50.75 if i>=35 else 51.))
        self.assertEqual(self.controls.rows['C7'][0]['controls']['brake_bias'],51.)
        self.pass_corner(2, alter=lambda s,i:s.update(brake_bias=50.75 if i>=12 else 51.))
        self.assertEqual(len(self.controls.rows['C7']),1)
        self.pass_corner(3, context='TRAFFIC_PRESENT')
        self.pass_corner(4, alter=lambda s,i:s.update(track_surface=0 if i==12 else 3))
        self.assertEqual(len(self.controls.rows['C7']),1)

    def test_abs_trial_requires_sustained_axle_slip_without_abs(self):
        for lap in range(1,4):
            self.pass_corner(lap,alter=lambda s,i:s.update(LF_wheel_speed=40., RF_wheel_speed=40.,
                LR_wheel_speed=30., RR_wheel_speed=31., abs_active=False))
        card=self.approach(4,session='Practice')
        self.assertEqual(card['action'],'abs')
        self.assertIn('ABS 개입을',card['text'])

    def test_different_corners_can_offer_forward_and_rearward_trials(self):
        for lap in range(1,4):
            self.pass_corner(lap,0,brake_correction=True,alter=lambda s,i:s.update(
                LF_wheel_speed=40., RF_wheel_speed=40., LR_wheel_speed=40., RR_wheel_speed=40.))
            self.pass_corner(lap,1,alter=lambda s,i:s.update(
                LF_wheel_speed=32., RF_wheel_speed=33., LR_wheel_speed=40., RR_wheel_speed=40.))
        self.assertEqual(self.approach(4,0,session='Practice')['action'],'forward')
        self.assertEqual(self.approach(4,1,session='Practice')['action'],'rearward')

    def test_observed_tc_and_abs_map_targets_work_in_both_numeric_directions(self):
        for control in ('tc','abs'):
            with self.subTest(control=control):
                self.setUp()
                field=control+'_setting'
                for lap in range(1,9):
                    self.s[field]=3 if lap%2 else 4
                    self.pass_corner(lap,0,seconds=3.65 if lap%2 else 4.)
                    self.pass_corner(lap,1,seconds=4. if lap%2 else 3.65)
                self.assertEqual(self.approach(9,0,**{field:4})['target'],3)
                self.assertEqual(self.approach(9,1,**{field:3})['target'],4)

    def test_two_changed_controls_or_slower_exit_do_not_qualify_a_race_target(self):
        for confound in (True,False):
            with self.subTest(confound=confound):
                self.setUp()
                for lap in range(1,9):
                    low=lap%2==0
                    self.s['tc_setting']=4 if low and confound else 3
                    def update(s,i):
                        s['speed_mps']=35. if low and not confound and i>40 else 40.
                    self.pass_corner(lap,bias=50.75 if low else 51.,seconds=3.65 if low else 4.,alter=update)
                self.assertIsNone(self.approach(9,brake_bias=51.,tc_setting=3))

    def test_abs_acting_and_fixed_map_prevent_practice_increase(self):
        for lap in range(1,4):
            self.pass_corner(lap,correction=True,alter=lambda s,i:s.update(LF_wheel_speed=40.,RF_wheel_speed=40.,
                LR_wheel_speed=30., RR_wheel_speed=31., abs_active=i<15))
        self.assertNotIn('abs',self.controls.rows['C7'][-1]['signals'])
        self.assertIsNone(self.approach(4,session='Practice',setup_fixed=True))

    def test_unavailable_setting_sentinels_do_not_authorize_a_trial(self):
        for lap in range(1,4):
            self.pass_corner(lap,bias=0.,correction=True,brake_correction=True,
                             alter=lambda s,i:s.update(tc_setting=-1))
        self.assertIsNone(self.approach(4,session='Practice'))

    def test_captions_follow_driver_control_preferences_and_stop_if_disabled(self):
        for lap in range(1,4):
            self.pass_corner(lap,correction=True)
        self.radio.allows=lambda k:False
        self.assertIsNone(self.approach(4,session='Practice'))
        self.radio.allows=lambda k:True
        self.assertIsNotNone(self.approach(4,session='Practice'))
        self.radio.allows=lambda k:False
        self.assertIsNone(self.approach(4,session='Practice'))

    def test_newly_named_track_invalidates_waiting_general_audio_on_first_sample(self):
        from live_telemetry import LiveTelemetry
        live=LiveTelemetry(clock=lambda:self.s['session_time'])
        identity=dict(game='lmu',car_id='1',track_id='1')
        self.s.update(lap_number=4,distance_pct=.15)
        live.ingest(identity,self.s.copy())
        live.controls=Mock(laps=[{'lap':i} for i in (1,2,3)])
        live.controls.view.return_value=dict(ready=True,advice=[dict(control='tc',action='more',call='control_tc_more')])
        self.s['session_time']+=.1
        live.ingest(identity,self.s.copy())
        queued=next(e for e in live.radio.pending if e['key']=='control_tc_more')
        self.assertTrue(live.radio.valid(queued,self.s['session_time']))
        live.corners.lookup=lambda _:CORNERS
        live.corners.checked_at=-100.
        self.s['session_time']+=.1
        live.ingest(identity,self.s.copy())
        self.assertFalse(live.radio.valid(queued,self.s['session_time']))

    def test_mismatched_conditions_and_crowded_or_late_approach_stay_quiet(self):
        for lap in range(1,4):
            self.pass_corner(lap, correction=True)
        for fields in [dict(fuel_l=5.), dict(car_left_right=2), dict(distance_pct=.296),
                       dict(session_flags=8), dict(steering_rad=.4), dict(on_pit_road=True)]:
            with self.subTest(fields=fields):
                before = self.s.copy()
                self.assertIsNone(self.approach(4,session='Practice',**fields))
                self.s.update(before)
        self.corners.revision += 1
        self.controls.ingest(self.corners,self.s,'SOLO',False)
        self.assertFalse(self.controls.rows)

    def test_live_pipeline_and_native_footer_receive_only_the_draft(self):
        from live_telemetry import LiveTelemetry
        import sys
        from pathlib import Path
        from unittest.mock import patch
        # Backend-only discovery does not normally import the native HUD scripts.
        with patch.object(sys,'path',[str(Path(__file__).resolve().parents[1]/'scripts'),*sys.path]):
            from overlay_rendering import OverlayRendering
        live = LiveTelemetry(clock=lambda: self.s['session_time'])
        identity={'game':'lmu','car_id':'1','track_id':'2'}
        live.corners.lookup=lambda identity:CORNERS
        def tick():
            s=self.s
            live.ingest(identity,s.copy(),dict(player_idx=0,sample_time=s['session_time'],session_type='Practice',
                        track_length_m=4000,cars=[dict(idx=0,lap_completed=s['lap_number'],distance_pct=s['distance_pct'],
                        on_pit_road=False,track_surface=3,class_est_lap_s=100.)]))
        self.s.update(lap_number=4,distance_pct=.19,session_time=100.)
        tick()
        for lap in range(1,4):
            self.pass_corner(lap,correction=True)
        live.corner_controls.rows = self.controls.rows
        self.s.update(lap_number=4,distance_pct=.19,brake=0.,throttle=1.,session_time=120.)
        tick()
        item=live.overlay_state()['control_recommendation']
        self.assertEqual(item['delivery'],'screen')
        self.assertFalse([e for e in live.radio.pending if e['group']=='control_advice'])
        hud=Mock(live_labels={}, scale_x=1., display_mode='live')
        hud.font.return_value.measure.return_value=220
        hud.x.side_effect=lambda v:v
        hud.fit_text.side_effect=lambda v,*args:v
        OverlayRendering.draw_control_recommendation(hud,item)
        self.assertEqual(hud.text.call_args.args[2],item['text'])

    def test_live_sampler_collects_its_own_evidence_in_both_games(self):
        from live_telemetry import LiveTelemetry
        for game in ('iracing','lmu'):
            with self.subTest(game=game):
                sample=self.s.copy()
                live=LiveTelemetry(clock=lambda:sample['session_time'])
                live.corners.lookup=lambda identity:CORNERS
                identity=dict(game=game,car_id='1',track_id='2')
                seen=set()
                for i in range(5*600):
                    pct=(i%600)/600
                    braking=.3<=pct<=.34
                    power=.34<pct<=.39
                    sample.update(lap_number=i//600+1,distance_pct=pct,session_time=i*.1,
                                  brake=.7 if braking else 0.,throttle=0. if braking else .9,
                                  steering_rad=-.2 if power else .1,yaw_rate=.3)
                    race=dict(player_idx=0,sample_time=sample['session_time'],session_type='Practice',track_length_m=3000,
                              cars=[dict(idx=0,lap_completed=i//600,distance_pct=pct,on_pit_road=False,
                                         track_surface=3,class_est_lap_s=60.)]) if i%10==0 else None
                    live.ingest(identity,sample.copy(),race)
                    if live.corner_controls.card:
                        seen.add(live.corner_controls.card['text'])
                live.driving_learning.close()
                self.assertEqual(seen,{'다음 9번 코너 탈출, TC 개입을 한 단계 늘려보세요.'})
                self.assertFalse([e for e in live.radio.history if e.get('group')=='control_advice'])
