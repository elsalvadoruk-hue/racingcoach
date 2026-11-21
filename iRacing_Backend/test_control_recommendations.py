"""Telemetry, delivery and setting-change boundaries; no SDK or speech service."""
import unittest
from live_controls import ControlAdvisor
from control_recommendations import ControlRecommendations


class MissingAngleTests(unittest.TestCase):
    def test_abs_evidence_does_not_require_a_steering_angle(self):
        a = ControlAdvisor()
        for i in range(901):
            a.ingest(dict(session_time=i*.1, distance_pct=(i%200)/200, lap_number=i//200,
                speed_mps=40., brake=.5, throttle=0., abs_active=True, steering_rad=None,
                yaw_rate=None, on_track=True, on_pit_road=False, is_replay=False,
                incidents=None, session_flags=0, car_left_right=1,
                abs_setting=4, tc_setting=4, brake_bias=52.))
        self.assertTrue(a.view()['ready'])
        self.assertEqual(a.view()['abs_duty_pct'],100)

    def test_reset_drops_the_previous_cars_steering_sign_learning(self):
        a=ControlAdvisor(); a.agree=[1.,100.]; a.reset()
        self.assertEqual(a.agree,[0.,0.])

    def test_lmu_lock_ratio_and_opposite_yaw_sign_can_generate_automatic_advice(self):
        from test_engineer_variation import laps
        a=ControlAdvisor()
        class NormalizedLMU:
            def ingest(self,s):
                a.ingest({**s,'steering_rad':None,'steering_lock_ratio':s['steering_rad']/2,
                          'yaw_rate':-s['yaw_rate']})
        laps(NormalizedLMU(),4,brake_counter=2,power_counter=2)
        calls={item['call'] for item in a.view()['advice']}
        self.assertTrue({'control_bias_forward','control_tc_more'}<=calls)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.a=ControlRecommendations()
        self.s=dict(brake_bias=52.,tc_setting=4,session_time=1,is_replay=False)
        self.cap={'brake_bias':dict(min=40,max=65,step=.2,button_adjustable=True)}
        self.items=[dict(control='tc',action='more',call='control_tc_more'),
                    dict(control='brake_bias',action='forward',call='control_bias_forward')]
    def queue(self):
        item=self.a.choose(self.items,self.s,self.cap,0)
        item['initial']=item['current'];self.a.queued({'id':'e'},item)
        return item
    def test_single_highest_priority_candidate_waits_for_real_playback(self):
        item=self.queue()
        self.assertEqual((item['control'],item['target']),('brake_bias',52.2))
        self.assertIsNone(self.a.view());self.assertEqual(self.a.sent,{})
        self.assertIsNone(self.a.choose(self.items,self.s,self.cap,1))
        self.a.delivery('e','started',10)
        self.assertEqual(self.a.view()['expires_at'],40)
        self.a.delivery('e','started',15)
        self.assertEqual(self.a.view()['expires_at'],40)
    def test_failed_unheard_call_does_not_charge_cooldown(self):
        self.queue();self.a.delivery('e','failed',10)
        self.assertEqual(self.a.sent,{})
        self.assertIsNotNone(self.a.choose(self.items,self.s,self.cap,11))

    def test_cockpit_exit_cancels_display_but_keeps_delivered_cooldown(self):
        self.queue();self.a.delivery('e','started',10);self.a.cancel()
        self.assertIsNone(self.a.view());self.assertEqual(self.a.sent['brake_bias'],10)
        self.assertIsNone(self.a.choose([self.items[1]],self.s,self.cap,11))
    def test_target_change_hides_after_distinct_valid_samples(self):
        self.queue();self.a.delivery('e','started',10)
        self.a.observe({**self.s,'brake_bias':52.2},11,{'e'})
        self.a.observe({**self.s,'brake_bias':52.2},11.1,{'e'})
        self.assertIsNotNone(self.a.view())
        self.a.observe({**self.s,'brake_bias':52.2,'session_time':1.1},11.2,{'e'})
        self.assertIsNone(self.a.view())
    def test_missing_or_partial_change_does_not_finish_and_timeout_is_30_seconds(self):
        self.queue();self.a.delivery('e','started',10)
        for i,value in enumerate((52.1,None,52.1)):
            self.a.observe({**self.s,'brake_bias':value,'session_time':2+i},12+i,{'e'})
        self.assertIsNotNone(self.a.view())
        self.a.observe(self.s,40,{'e'});self.assertIsNone(self.a.view())
    def test_unknown_bounds_or_map_never_make_up_a_numeric_target(self):
        self.assertIsNone(self.a.choose(self.items,self.s,{},0)['target'])
        item=self.a.choose([self.items[0]],self.s,{'tc':dict(min=0,max=10,step=1,button_adjustable=True)},0)
        self.assertIsNone(item['target'])

    def test_only_repeated_observed_bias_steps_inside_reached_range_get_a_target(self):
        for bias in (52.,52.2,52.4):cap=self.a.capabilities({'brake_bias':bias})
        self.assertIsNone(self.a.choose(self.items,{**self.s,'brake_bias':52.4},cap,0)['target'])
        cap=self.a.capabilities(self.s)
        self.assertEqual(self.a.choose(self.items,self.s,cap,0)['target'],52.2)
        self.assertNotIn('tc',cap);self.assertNotIn('abs',cap)

    def test_float32_brake_bias_keeps_the_observed_cars_step_and_endpoints(self):
        from ctypes import c_float
        for low,step in ((52.,.2),(52.,.25),(53.6,.2)):
            with self.subTest(low=low,step=step):
                self.a.reset()
                values=[c_float(low+i*step).value for i in range(3)]
                for bias in values:
                    cap=self.a.capabilities({'brake_bias':bias})
                self.assertEqual(cap['brake_bias']['step'],step)
                for i,bias in enumerate(values):
                    target=self.a.choose(self.items,{**self.s,'brake_bias':bias},cap,0)['target']
                    self.assertEqual(target,round(low+(i+1)*step,2) if i<2 else None)

    def test_recorded_mclaren_bias_changes_reach_the_observed_limit(self):
        readings=(53.60000228881836,53.79999923706055,54.000003814697266,54.20000076293945)
        for value in readings:
            cap=self.a.capabilities({'brake_bias':value})
        targets=[self.a.choose(self.items,{**self.s,'brake_bias':v},cap,0)['target'] for v in readings]
        self.assertEqual(targets,[53.8,54.,54.2,None])

    def test_single_fractional_bias_reading_does_not_imply_a_step(self):
        raw=49.51514434814453
        for _ in range(3):
            self.assertEqual(self.a.capabilities({'brake_bias':raw}),{})
        self.assertEqual(self.a.bias_values,{raw})


class PipelineTests(unittest.TestCase):
    def test_recommendation_uses_first_safe_straight_and_expires_with_its_evidence(self):
        from types import SimpleNamespace
        from live_telemetry import LiveTelemetry
        now = [1.]
        live = LiveTelemetry(clock=lambda: now[0])
        identity = {'game':'lmu','car_id':'1','track_id':'1'}
        sample = dict(session_num=0,session_state=4,on_track=True,is_replay=False,on_pit_road=False,
                      speed_mps=48.,brake=0.,throttle=1.,steering_rad=0.,car_left_right=1,tc_setting=3)
        def tick():
            live.ingest(identity, {**sample,'session_time':now[0]})
        tick()
        advice = [dict(control='tc',action='more',call='control_tc_more')]
        live.controls = SimpleNamespace(ingest=lambda s:None,laps=[{'lap':i} for i in (1,2,3)],
                                        view=lambda:dict(ready=True,advice=advice))
        now[0] += .1
        tick()
        event = next(e for e in live.radio.pending if e['key']=='control_tc_more')
        self.assertTrue(live.radio.valid(event,now[0]))
        live.radio.lap_done_at = now[0] - .2
        self.assertFalse(live.radio.valid(event,now[0]))
        live.radio.lap_done_at = now[0] - 2.
        self.assertTrue(live.radio.valid(event,now[0]))
        for busy in (dict(brake=.4), dict(car_left_right=2), dict(workload_in_s=1.2),
                     dict(session_flags=8), dict(on_pit_road=True)):
            with self.subTest(busy=busy):
                live.radio.latest = {**sample, **busy, 'speech_hold_s':-1}
                self.assertFalse(live.radio.valid(event,now[0]))
        live.radio.latest = sample
        live.radio.traffic.updated_at = now[0]
        live.radio.traffic.nearby = [dict(side='ahead',close=False,gap_estimate_s=.5)]
        self.assertFalse(live.radio.valid(event,now[0]))
        live.radio.traffic.reset()
        self.assertTrue(live.radio.valid(event,now[0]))
        advice.clear()
        live.controls.laps.append({'lap':4})
        now[0] += .1
        tick()
        self.assertIsNone(live.radio.get(event['id'],now[0]))

    def test_recorded_pedal_endpoints_preserve_real_laps_but_invalid_pedals_do_not(self):
        from setup_advisor import SetupAdvisor
        from test_setup_advisor import IDENTITY, samples
        for brake, throttle, expected in ((-1.3969839e-9, 1.0000001192, True),
                                          (-.01, 1., False), (0., 1.01, False)):
            with self.subTest(brake=brake, throttle=throttle):
                controls, setup = ControlAdvisor(), SetupAdvisor()
                for s in samples():
                    if .75 < s['distance_pct'] < .8:
                        s.update(brake=brake, throttle=throttle)
                    elif .05 < s['distance_pct'] < .15:
                        s.update(brake=.5, throttle=0.)
                    controls.ingest(s)
                    setup.ingest(IDENTITY,s)
                self.assertEqual(controls.view()['ready'], expected)
                self.assertEqual(setup.view(s)['ready'], expected)

    def test_one_matching_correction_on_each_of_three_consecutive_laps_is_repeatable(self):
        from test_engineer_variation import laps
        advisor = ControlAdvisor()
        laps(advisor, 4, power_counter=1)
        self.assertIn('control_tc_more', [a['call'] for a in advisor.view()['advice']])
        advisor.laps[-1]['power_sites'] = [.1]
        self.assertNotIn('control_tc_more', [a['call'] for a in advisor.view()['advice']])

    def test_low_overall_abs_duty_is_insufficient_when_abs_already_acts_at_the_corrections(self):
        from test_engineer_variation import laps
        advisor = ControlAdvisor()
        class Feed:
            def ingest(self, sample):
                if sample['brake'] and sample['steering_rad'] < 0:
                    sample['abs_active'] = (sample['distance_pct'] * 60) % 1 > .28
                advisor.ingest(sample)
        laps(Feed(), 4, brake_counter=2, abs_share=0)
        view = advisor.view()
        self.assertLess(view['abs_duty_pct'], 10)
        self.assertIn('control_bias_forward', [a['call'] for a in view['advice']])
        self.assertNotIn('control_abs_more', [a['call'] for a in view['advice']])

    def test_waiting_call_is_not_replaced_and_current_run_playback_drives_the_footer(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from live_telemetry import LiveTelemetry
        import voice_coach
        now=[10.]
        live=LiveTelemetry(clock=lambda:now[0])
        sample=dict(session_time=1.,session_num=0,session_state=4,on_track=True,is_replay=False,
                    on_pit_road=False,brake_bias=52.,throttle=1.,brake=0.,speed_mps=40.,steering_rad=0.)
        live.ingest({'game':'lmu','car_id':'1','track_id':'1'},sample)
        live.controls=SimpleNamespace(ingest=lambda s:None,laps=[{'lap':i} for i in (1,2,3)],view=lambda:{'ready':True,'advice':[
            dict(control='brake_bias',action='forward',call='control_bias_forward')]})
        for index in range(25):
            now[0]+=.1;sample['session_time']+=.1;live.ingest({'game':'lmu','car_id':'1','track_id':'1'},sample)
        calls=[e for e in live.radio.pending if e['group']=='control_advice']
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['serial'],1)
        self.assertIsNone(live.overlay_state()['control_recommendation'])
        app=FastAPI();coach=SimpleNamespace(live=live,store=SimpleNamespace(record_radio=lambda es:None),speech=None)
        with patch.object(voice_coach,'SpotterAudio'):
            voice_coach.register_voice_routes(app,coach,None,None,None,None)
        client=TestClient(app)
        def started(run):
            return client.post('/api/live-coach/radio-delivery',json={'events':[
                {'event_id':calls[0]['id'],'key':'control_bias_forward','status':'started','run_id':run}]})
        self.assertEqual(started('older-run-000').status_code,200)
        self.assertIsNone(live.overlay_state()['control_recommendation'])
        self.assertEqual(started(live.run_id).status_code,200)
        self.assertEqual(live.overlay_state()['control_recommendation']['control'],'brake_bias')
        for index in range(2):
            now[0]+=.1;sample.update(session_time=sample['session_time']+.1,brake_bias=52.2)
            live.ingest({'game':'lmu','car_id':'1','track_id':'1'},sample)
        self.assertIsNone(live.overlay_state()['control_recommendation'])

    def test_scattered_corrections_do_not_become_a_setting_recommendation(self):
        from test_engineer_variation import laps
        a=ControlAdvisor();laps(a,4,brake_counter=2)
        self.assertTrue(any(i.get('call') for i in a.view()['advice']))
        for lap,sites in zip(list(a.laps)[-3:],([.1,.15],[.4,.45],[.7,.75])):
            lap['brake_sites']=sites
        self.assertFalse(any(i.get('call') for i in a.view()['advice']))

    def test_one_correction_on_another_lap_cannot_match_two_nearby_events(self):
        from test_engineer_variation import laps
        a=ControlAdvisor();laps(a,4,brake_counter=2)
        for lap,sites in zip(list(a.laps)[-3:],([.1,.105],[.103],[.7])):
            lap['brake_sites']=sites
        self.assertFalse(any(i.get('call') for i in a.view()['advice']))

if __name__=='__main__': unittest.main()
