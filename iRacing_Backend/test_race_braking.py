import unittest
from live_telemetry import LiveTelemetry
from race_braking import ABS_KEY, BIAS_KEY, BIAS_STEP, DRY_ABS, WET_CHECK, bias_ticks, voice_catalog


class RaceBrakingTests(unittest.TestCase):
    def setUp(self):
        self.t=0.
        self.live=LiveTelemetry(clock=lambda:self.t)
        self.addCleanup(self.live.driving_learning.close)
        self.live.driving_audio_ready=lambda _:True
        self.car=dict(game='iracing',car_path='bmwm4gt3',track_id='1')
        self.rule = ''
        self.definition=dict(id='C1',call='9번 코너',entry_pct=1020/3000,apex_pct=1095/3000,exit_pct=1160/3000)
        self.live.corners.lookup=lambda _:[self.definition]
        self.base=dict(on_track=True,is_replay=False,on_pit_road=False,session_num=0,session_state=4,session_flags=0,
                       track_surface=3,car_left_right=1,tow_time_s=0.,gear=3,incidents=0,track_temp_c=22.,fuel_l=30.,
                       tire_compound=0,track_wetness=1,tc_setting=3,abs_setting=4,setup_fixed=False,
                       vert_accel_mps2=9.81,abs_active=False,yaw_rate=0.,brake_bias=51.)

    def feed(self,s,wet_ids=(1,)):
        self.t+=.05
        s={**self.base,**s,'session_time':self.t}
        race=dict(session_type='Race',sample_time=self.t,track_length_m=3000,wet_compounds=list(wet_ids),car_rule_set=self.rule,
                  player_idx=0,cars=[dict(idx=0,track_surface=s['track_surface'],on_pit_road=s['on_pit_road'],
                                         lap_completed=s['lap_number']-1,distance_pct=s['distance_pct'],class_est_lap_s=61.)])
        self.live.ingest(self.car,s,race)
        self.assertFalse(self.live._corner_failure)
        return next((e for e in self.live.radio.view(self.t)['events'] if e['key'] in (ABS_KEY,BIAS_KEY)),None)

    def pit(self,seconds,wet_ids=(1,),**fields):
        event=None
        for _ in range(round(seconds/.05)):
            s=dict(lap_number=2,distance_pct=.01,on_pit_road=True,track_surface=1,speed_mps=0.,
                   throttle=0.,brake=1.,steering_rad=0.,track_wetness=4,tire_compound=1)
            observed=self.feed({**s,**fields},wet_ids)
            if observed:event=observed
        return event

    def test_pit_wet_map_requires_thirty_seconds_and_deduplicates_after_started(self):
        self.assertIsNone(self.pit(29.95))
        event=self.pit(.2)
        self.assertEqual((event['key'],event['speech_text'],event['alert_categories']),(ABS_KEY,WET_CHECK,['pitwall.abs']))
        self.assertEqual(self.live.radio.control_trials.counts['abs']['evidence'], 1)
        self.assertEqual(self.live.radio.control_trials.counts['abs']['queued'], 1)
        self.live.radio_delivery(event['id'],'started')
        self.assertIsNone(self.pit(.05,abs_setting=7))
        self.assertIsNone(self.live.radio.get(event['id'],self.t))
        self.assertIsNone(self.pit(29.95))
        second=self.pit(.15)
        self.assertNotEqual(second['id'],event['id'])
        self.live.radio_delivery(second['id'],'started')
        self.pit(4.)
        self.assertIsNone(self.pit(31.))

    def test_wet_map_uses_car_tire_sdk_bands_and_safe_stop(self):
        self.car['car_path']='mclaren720sgt3'
        self.assertIsNone(self.pit(31,wet_ids=(),abs_setting=7))
        self.assertIsNone(self.pit(31,abs_setting=1))
        self.assertIsNone(self.pit(31,setup_fixed=True,abs_setting=7))
        self.assertIsNone(self.pit(31,abs_setting=7,speed_mps=5.))
        self.assertIsNone(self.pit(3,abs_setting=7,tow_time_s=4.))
        self.assertIsNone(self.pit(.95,abs_setting=7))
        event=self.pit(.15,abs_setting=7)
        self.assertEqual(event['text'],WET_CHECK)
        self.live.radio.configure_alerts(pitwall={'abs':False})
        self.assertIsNone(self.live.radio.get(event['id'],self.t))
        self.assertIsNone(self.pit(31,abs_setting=8))

    def test_documented_dry_abs_bands_reach_the_pit_reminder(self):
        bands = {
            (1, 6): ('amvantageevogt3','audir8lmsevo2gt3','bmwm4gt3','chevyvettez06rgt3',
                     'lamborghinievogt3','porsche992rgt3'),
            (2, 7): ('acuransxevo22gt3','ferrari296gt3','fordmustanggt3','mclaren720sgt3'),
            (7, 11): ('mercedesamgevogt3',),
        }
        for (low, high), cars in bands.items():
            for car in cars:
                with self.subTest(car=car):
                    self.car['car_path'] = car
                    self.assertEqual(DRY_ABS[car], range(low, high+1))
                    self.assertEqual(self.pit(30.2, abs_setting=high)['text'], WET_CHECK)
                    self.assertIsNone(self.pit(.1, abs_setting=high+1))
        self.assertIsNone(self.pit(31, abs_setting=6))  # unclassified AMG position

    def test_rules_disabling_abs_veto_a_dry_map_on_wets(self):
        self.car['car_path'] = 'mercedesamgevogt3'
        for rule in ('NOABS', 'NOABSTC'):
            self.rule = rule
            self.assertIsNone(self.pit(31, abs_setting=9))
        self.rule = 'NOTC'
        self.assertEqual(self.pit(30.2, abs_setting=9)['text'], WET_CHECK)
        self.assertIsNone(bias_ticks('mercedesamgevogt3', 50.25))
        self.assertIsNone(bias_ticks('porsche992rgt3', 49.515144348))

    def test_wet_map_waits_for_a_clear_learned_straight_and_withdraws_in_traffic(self):
        radio=self.live.radio
        monitor=self.live.wet_abs
        radio.metadata.update(session_type='Race',wet_compounds=[1])
        sample=dict(self.base,speed_mps=45.,steering_rad=0.,throttle=.9,brake=0.,
                    track_wetness=5,tire_compound=1)
        def advance(seconds,context,**fields):
            for _ in range(round(seconds/.1)):
                self.t+=.1
                sample.update(session_time=self.t,**fields)
                radio.latest=sample
                radio.last_sample=self.t
                monitor.ingest('bmwm4gt3',sample,context,radio,self.t,lambda _:True,False)
        advance(31.,'TRAFFIC_PRESENT',workload_in_s=16.)
        advance(2.,'SOLO',workload_in_s=None)
        advance(2.,'SOLO',workload_in_s=3.)
        self.assertFalse(radio.pending)
        advance(.1,'SOLO',workload_in_s=12.)
        event=radio.pending[-1]
        self.assertTrue(monitor.event_valid(event['condition'][1],sample,radio,self.t))
        advance(.1,'TRAFFIC_PRESENT')
        self.assertFalse(monitor.event_valid(event['condition'][1],sample,radio,self.t))

    def drive_bias(self,better=50.75,initial=51.,axle='front',change=None):
        # The control advisor learns the normal sign of steering versus yaw from prior turns.
        for index in range(500):
            self.feed(dict(lap_number=0,distance_pct=(1750+index*2.5)/3000,speed_mps=50.,
                           brake=0.,throttle=.7,steering_rad=.3,yaw_rate=.35,brake_bias=initial))
        for lap in range(1,6):
            pos=0.
            while pos<3000:
                dx=pos-910
                brake=0<=dx<148
                old=lap%2==1
                v=60-min(18,max(0,dx)*(.15 if old else .21))+min(18,max(0,dx-265)*.065)
                s=dict(lap_number=lap,distance_pct=pos/3000,speed_mps=v,brake=.7 if brake else 0.,
                       throttle=0. if brake else .9,steering_rad=.25 if 1060<pos<1160 else 0.,
                       brake_bias=initial if old else better)
                for wheel in ('LF','RF','LR','RR'):
                    slipping=old and 4<dx<92 and (wheel in ('LF','RF') if axle=='front' else wheel in ('LR','RR'))
                    s[wheel+'_wheel_speed']=v*(.89 if slipping else 1.)
                if change:change(s,lap,dx)
                event=self.feed(s)
                if event and event['key']==BIAS_KEY:return event
                pos+=v*.05

    def test_measured_bias_reaches_radio_only_after_two_independent_click_pairs(self):
        event=self.drive_bias()
        self.assertIsNotNone(event)
        self.assertEqual(event['speech_parts'],['다음 9번 코너.','바이어스 50.75퍼센트.'])
        self.assertEqual(event['text'],'다음 9번 코너. 바이어스 50.75퍼센트.')
        counts = self.live.radio.control_trials.counts['brake_bias']
        self.assertEqual([counts[k] for k in ('evidence','candidate','queued')], [1,1,1])
        self.assertEqual(event['alert_categories'],['pitwall.bbal'])
        self.assertEqual(self.live.samples[-1]['lap_number'],5)
        sample=self.live.samples[-1].copy()
        self.feed({**sample,'brake_bias':50.75})
        self.assertIsNone(self.live.radio.get(event['id'],self.t))

    def test_unnumbered_mclaren_rear_axle_uses_one_fifth_forward(self):
        self.car['car_path']='mclaren720sgt3'
        self.definition['call']=None
        event=self.drive_bias(initial=52.,better=52.2000008,axle='rear')
        self.assertIsNotNone(event)
        self.assertEqual(event['speech_parts'],['다음 코너.','바이어스 52.2퍼센트.'])

    def test_one_pair_or_unknown_target_wheels_cannot_prove_bias(self):
        def absent(s,lap,dx):
            if lap==4:s.pop('LR_wheel_speed')
        self.assertIsNone(self.drive_bias(change=absent))

    def test_two_click_improvement_does_not_authorize_a_spoken_target(self):
        self.assertIsNone(self.drive_bias(better=50.50))

    def test_absent_target_audio_suppresses_the_whole_bias_call(self):
        self.live.driving_audio_ready=lambda text:not text.startswith('바이어스 ')
        self.assertIsNone(self.drive_bias())

    def test_per_car_grid_and_catalog(self):
        for car,step in BIAS_STEP.items():
            ticks=[i for i in range(4000,6501) if bias_ticks(car,i/100)==i]
            self.assertEqual(ticks,list(range(4000,6501,step)))
        self.assertIsNone(bias_ticks('bmwm4gt3',49.52))
        self.assertIsNone(bias_ticks('mclaren720sgt3',52.25))
        self.assertEqual(sum(t.startswith('바이어스 ') for t in voice_catalog()),201)
