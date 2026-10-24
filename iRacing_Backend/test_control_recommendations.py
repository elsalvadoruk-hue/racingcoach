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


class PipelineTests(unittest.TestCase):
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
        live.controls=SimpleNamespace(ingest=lambda s:None,laps=[{'lap':3}],view=lambda:{'ready':True,'advice':[
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
