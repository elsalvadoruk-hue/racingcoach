"""Car permissions, matched laps and pending/applied boundaries; synthetic only."""
import unittest
from control_recommendations import ControlRecommendations


IDENTITY={'game':'iracing','car_path':'dallarair18','car_id':'1','track_id':'1'}


def samples(laps=4):
    """Complete 60-second synthetic laps with two repeatable mid-corner corrections."""
    for index in range(laps*600+1):
        pct=(index%600)/600
        counter=any(start<=pct<start+.01 for start in (.2,.55))
        yield dict(session_time=index*.1,session_num=0,lap_number=index//600,distance_pct=pct,
            session_state=4,on_track=True,on_pit_road=False,is_replay=False,setup_fixed=False,
            speed_mps=30.,brake=0.,throttle=.3,steering_rad=-.2 if counter else .2,yaw_rate=.3,
            incidents=0,session_flags=0,car_left_right=1,track_surface=3,fuel_l=30.,track_temp_c=30.,
            tire_compound=0,track_wetness=0,rear_arb_raw=0.,front_arb_raw=0.)


class PitDisplayTests(unittest.TestCase):
    def test_setting_a_pit_reservation_must_not_hide_as_applied(self):
        d=ControlRecommendations()
        d.active=dict(id='pit',control='brake_bias',initial=52.,current=52.,target=52.2,
                      timing='pit',shown_at=1.,expires_at=31.,step=.2)
        for at in (2.,3.):
            d.observe(dict(brake_bias=52.2,session_time=at,is_replay=False,on_track=True),at,set())
        self.assertIsNotNone(d.view())

    def test_pressure_pending_is_reserved_and_timeout_does_not_extend(self):
        d=ControlRecommendations()
        item=dict(control='pressure_lf',field='pressure_lf',initial=150.,current=150.,target=155.,
                  timing='pit',setup=True,pending_field='pressure_lf_pending')
        d.present(item,10.)
        for at in (11.,12.):
            d.observe(dict(session_time=at,on_track=True,is_replay=False,setup_fixed=False,pressure_lf=150.,
                           pressure_lf_pending=155.),at,set())
        self.assertEqual(d.view()['state'],'reserved')
        self.assertEqual(d.view()['current'],150.)
        d.observe(dict(session_time=40.,is_replay=False),40.,set())
        self.assertIsNone(d.view())
        self.assertIsNone(d.choose([item],{}, {},100.))


class ProfileTests(unittest.TestCase):
    def test_permissions_are_car_and_axle_specific(self):
        from setup_capabilities import profile_for
        self.assertEqual(profile_for({'game':'iracing','car_path':'porschemissionr'})['rear_arb']['timing'],'live')
        self.assertNotIn('front_arb',profile_for({'game':'iracing','car_path':'porschemissionr'}))
        self.assertNotIn('rear_arb',profile_for({'game':'iracing','car_path':'superformulalights324'}))
        self.assertEqual(profile_for({'game':'iracing','car_path':'bmwm4gt3'})['rear_wing']['timing'],'pit')
        self.assertEqual(profile_for({'game':'lmu','car_path':'bmwm4gt3'}),{})
        self.assertEqual(profile_for({'game':'iracing','car_path':'dallarap217'}),{})

    def test_cup_has_non_monotonic_arb_order_and_unknown_mission_order(self):
        from setup_capabilities import profile_for
        p=profile_for({'game':'iracing','car_path':'porsche9922cup'})
        self.assertEqual(p['front_arb']['soft_to_stiff'],[1,2,3,4,7,5,6])
        self.assertEqual(p['rear_arb']['soft_to_stiff'],[1,2,3,4,5,7,6])
        self.assertNotIn('soft_to_stiff',profile_for({'game':'iracing','car_path':'porschemissionr'})['rear_arb'])

    def test_session_setup_parser_ignores_unrelated_and_never_confuses_pending_wing_index(self):
        from setup_capabilities import session_setup_values, read_setup_channels
        values=session_setup_values({'Chassis':{'Front':{'ArbSetting':'7'},'Rear':{'WingSetting':'5 deg'}},
                                     'TiresAero':{'LeftFront':{'ColdPressure':'150 kPa'}},'Private':'SECRET'})
        self.assertEqual(values,{'front_arb':7.,'rear_wing':5.,'pressure_lf':150.})
        self.assertNotIn('SECRET',repr(values))
        channels=read_setup_channels({'dcAntiRollFront':0.,'dpWingRear':2.,'PitSvLFP':155.},
                                     {'dcAntiRollFront','dpWingRear','PitSvLFP'})
        self.assertEqual(channels['front_arb_raw'],0.)
        self.assertEqual(channels['pressure_lf_pending'],155.)
        self.assertNotIn('rear_wing',channels)

    def test_realistic_setup_aliases_and_units(self):
        from setup_capabilities import session_setup_values
        values=session_setup_values({'Chassis':{'FrontBrakesLights':{'ArbBlades':'3'},
            'RearARB':{'ArbBlades':'7'}},'TiresAero':{'RearWing':{'WingAngle':'5.5 deg'},
            'LeftFront':{'StartingPressure':'21 psi'},'RightRear':{'StartingPressure':'145 kPa'}}})
        self.assertEqual(values['front_arb'],3.)
        self.assertEqual(values['rear_arb'],7.)
        self.assertEqual(values['rear_wing'],5.5)
        self.assertAlmostEqual(values['pressure_lf'],144.7899,places=3)
        self.assertEqual(values['pressure_rr'],145.)


class AdviceTests(unittest.TestCase):
    def advisor(self):
        from setup_advisor import SetupAdvisor
        a=SetupAdvisor();a.profile={'rear_arb':{'timing':'live','source':'manual'}}
        return a

    def test_repeated_mid_corner_corrections_produce_direction_only_live_arb(self):
        a=self.advisor()
        a.laps.extend([dict(lap=i,sites=[(.2,30.,.3),(.55,30.,.3)],cells={},settings=(),time=90.,conditions=(30.,30.,0,0)) for i in (1,2,3)])
        items=a.view({'rear_arb_raw':0.,'setup_fixed':False})['advice']
        self.assertEqual(items[0]['control'],'rear_arb')
        self.assertEqual(items[0]['direction'],'부드럽게')
        self.assertIsNone(items[0]['target'])

    def test_scattered_corrections_unknown_and_fixed_cars_have_no_advice(self):
        a=self.advisor()
        a.laps.extend([dict(lap=i,sites=[(p,30.,.3) for p in sites],cells={},settings=(),time=90.,conditions=(30.,30.,0,0))
                       for i,sites in enumerate(([.1,.2],[.4,.5],[.7,.8]),1)])
        self.assertEqual(a.view({'setup_fixed':False,'rear_arb_raw':0.})['advice'],[])
        a.profile={}
        self.assertEqual(a.view({})['advice'],[])

    def test_pit_cup_target_uses_stiffness_order_and_not_number_minus_one(self):
        a=self.advisor();a.profile={'rear_arb':{'timing':'pit','source':'manual','soft_to_stiff':[1,2,3,4,5,7,6]}}
        a.laps.extend([dict(lap=i,sites=[(.2,30.,.3),(.55,30.,.3)],cells={},settings=(),time=90.,conditions=(30.,30.,0,0)) for i in (1,2,3)])
        item=a.view({'rear_arb':6.,'setup_fixed':False,'setup_values_verified':True})['advice'][0]
        self.assertEqual(item['target'],7.)
        self.assertEqual(item['timing'],'pit')

    def test_same_setting_laps_are_insufficient_for_a_wing_or_pressure_target(self):
        a=self.advisor()
        self.assertEqual(a.view({})['advice'],[])

    def test_a_b_comparison_requires_one_change_matching_conditions_and_repeat_laps(self):
        from setup_advisor import SetupAdvisor
        a=SetupAdvisor();a.profile={'rear_wing':{'timing':'pit','source':'manual'}}
        def lap(i,wing,t,fuel=35.,temp=30.):
            return dict(lap=i,sites=[],cells={},settings=(('rear_wing',wing),),time=t,
                        conditions=(fuel,temp,0,0),installed=True)
        a.trials.extend([lap(i,5.,90.+i*.02) for i in range(1,4)])
        a.laps.extend([lap(i,7.,91.+i*.02) for i in range(4,7)])
        a.trials.extend(a.laps)
        sample={'rear_wing':7.,'setup_fixed':False,'setup_values_verified':True}
        items=a.view(sample)['advice']
        self.assertEqual(items[0]['target'],5.)
        self.assertEqual(items[0]['call'],'setup_wing_less')
        self.assertFalse(a.view({**sample,'setup_values_verified':False})['advice'])
        for row in a.trials:
            if row['settings']==(('rear_wing',5.),):row['conditions']=(15.,30.,0,0)
        self.assertFalse(a.view(sample)['advice'])

    def test_replay_pit_gap_setting_change_and_identity_change_clear_handling_evidence(self):
        from setup_advisor import SetupAdvisor
        a=SetupAdvisor();identity={'game':'iracing','car_path':'dallarair18'}
        s=dict(session_time=1.,lap_number=1,distance_pct=.1,on_track=True,on_pit_road=False,is_replay=False,
               speed_mps=30.,brake=0.,throttle=.3,steering_rad=.2,yaw_rate=.3)
        a.ingest(identity,s)
        a.laps.append({'lap':1})
        a.ingest(identity,{**s,'session_time':2.,'is_replay':True})
        self.assertFalse(a.laps)
        a.laps.append({'lap':1})
        a.ingest({**identity,'car_path':'porschemissionr'},{**s,'session_time':3.})
        self.assertFalse(a.laps)

    def test_real_samples_need_repeated_locations_matching_inputs_and_conditions(self):
        from setup_advisor import SetupAdvisor
        a=SetupAdvisor()
        for s in samples():a.ingest(IDENTITY,s)
        self.assertEqual(len(a.laps),3)
        self.assertEqual(a.view(s)['advice'][0]['control'],'rear_arb')
        self.assertIsNone(a.view(s)['advice'][0]['current'])
        self.assertFalse(a.view({**s,'setup_fixed':True})['advice'])
        self.assertFalse(a.view({**s,'rear_arb_raw':None})['advice'])
        for lap in a.laps:lap['sites']=[(.2,30.,.3),(.21,30.,.3)]
        self.assertFalse(a.view(s)['advice'])
        for i,lap in enumerate(a.laps):
            lap['sites']=[(.2,20.+i*5,.3),(.55,20.+i*5,.3)]
        self.assertFalse(a.view(s)['advice'])

    def test_other_setup_changes_clear_existing_abs_tc_bias_evidence(self):
        from live_controls import ControlAdvisor
        a=ControlAdvisor();s=next(samples())
        a.ingest(s);a.laps.append({'lap':1})
        a.ingest({**s,'session_time':.1,'rear_arb_raw':1.})
        self.assertFalse(a.laps)

    def test_front_arb_needs_two_locations_on_two_laps_with_matched_throttle(self):
        a=self.advisor();a.profile={'front_arb':{'timing':'live','source':'manual'}}
        for i in range(5):
            recent=i>=3
            a.laps.append(dict(lap=i,sites=[],conditions=(30.,30.,0,0),
                cells={cell:(30.,.3 if recent else .2,.006 if recent else .01,.3) for cell in (20,55)}))
        s={'setup_fixed':False,'front_arb_raw':0.}
        self.assertEqual(a.view(s)['advice'][0]['control'],'front_arb')
        a.laps[-1]['cells']={cell:(30.,.3,.006,.55) for cell in (20,55)}
        self.assertFalse(a.view(s)['advice'])


class DeliveryTests(unittest.TestCase):
    def test_prepared_voice_waits_for_started_and_raw_slot_change_cancels_card(self):
        from setup_advisor import SetupAdvisor
        from live_telemetry import LiveTelemetry
        a=SetupAdvisor()
        for s in samples():a.ingest(IDENTITY,s)
        clock=[s['session_time']];live=LiveTelemetry(clock=lambda:clock[0]);live.speech_hold_s=-1
        live.ingest(IDENTITY,s);live.setup_advisor=a;live.setup_audio_ready=lambda text:True
        clock[0]+=1.1;s={**s,'session_time':clock[0],'distance_pct':.018}
        live.ingest(IDENTITY,s)
        calls=[e for e in live.radio.pending if e['key'].startswith('setup_')]
        self.assertEqual(len(calls),1)
        self.assertIsNone(live.control_recommendations.view())
        event=calls[0]
        live.control_recommendations.delivery(event['id'],'started',clock[0])
        self.assertIsNone(live.control_recommendations.view()['target'])
        live.control_recommendations.observe({**s,'session_time':clock[0]+.1,'rear_arb_raw':1.},clock[0]+.1,{event['id']})
        self.assertIsNone(live.control_recommendations.view())

    def test_gtp_straight_only_limit_survives_disabled_speech_hold(self):
        from race_radio import RaceRadio
        radio=RaceRadio();radio.run_id='r';radio.last_sample=0.;radio.setup_advice_allowed=True
        radio.latest=dict(on_track=True,is_replay=False,on_pit_road=False,session_state=4,
            rear_arb_raw=0.,speed_mps=30.,brake=0.,steering_rad=.3,speech_hold_s=-1,setup_fixed=False)
        event=dict(run_id='r',not_before=0.,expires_at=30.,condition=('control_setting',('rear_arb',0.,'rear_arb_raw')),
                   control_recommendation=dict(setup=True,straight_only=True))
        self.assertFalse(radio.valid(event,.1))
        radio.latest['steering_rad']=0.
        self.assertTrue(radio.valid(event,.1))
        radio.latest['setup_fixed']=True
        self.assertFalse(radio.valid(event,.1))

    def test_real_live_pipeline_without_clip_uses_screen_and_never_queues_new_speech(self):
        from live_telemetry import LiveTelemetry
        clock=[0.];live=LiveTelemetry(clock=lambda:clock[0])
        live.speech_hold_s=-1
        for s in samples():
            clock[0]=s['session_time'];live.ingest(IDENTITY,s)
        s={**s,'session_time':s['session_time']+.1,'steering_rad':0.,'throttle':1.}
        clock[0]=s['session_time'];live.ingest(IDENTITY,s)
        self.assertFalse(live._corner_failure)
        item=live.overlay_state()['control_recommendation']
        self.assertEqual(item['control'],'rear_arb')
        self.assertEqual(item['delivery'],'screen')
        self.assertFalse(any(e['key'].startswith('setup_') for e in live.radio.pending))
        live._reset_live('DISCONNECTED')
        self.assertFalse(live.setup_advisor.laps)
        self.assertIsNone(live.control_recommendations.view())

    def test_approved_phrases_are_number_free_and_in_preparation_catalog(self):
        from setup_advisor import PHRASES
        from radio_audio import fixed_catalog
        from radio_phrases import catalog
        import re
        self.assertEqual(len(PHRASES),14)
        self.assertTrue(all(not re.search(r'\d',s) for s in PHRASES.values()))
        self.assertTrue(set(PHRASES.values()).issubset({text for role,text in fixed_catalog()+catalog() if role=='coach'}))

    def test_missing_or_disappearing_setup_clip_never_synthesizes(self):
        import asyncio,base64,tempfile,threading
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from radio_audio import RadioAudioCache
        import voice_coach
        speech=Mock();event=dict(id='e',engineer='coach',speech_text='뒤 안티롤바를 조금 부드럽게 시험해 보세요.',
                                control_recommendation={'setup':True})
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ',{'IRACING_RADIO_AUDIO_DIR':directory}):
                cache=RadioAudioCache(speech,Path(directory))
            path=cache.path('coach',event['speech_text'])
            coach=SimpleNamespace(live=SimpleNamespace(lock=threading.RLock(),clock=lambda:1.,
                radio=SimpleNamespace(get=lambda *args:event)),fixed_audio=cache,
                speech=speech,radio_audio_mode=lambda e:(cache.cached('coach',e['speech_text']),False),cloud_lock=asyncio.Lock())
            app=FastAPI()
            with patch.object(voice_coach,'SpotterAudio'):
                voice_coach.register_voice_routes(app,coach,None,None,None,None)
            client=TestClient(app)
            self.assertIsNone(client.post('/api/live-coach/spotter/radio/e/render').json()['event'])
            path.write_bytes(b'fixture'*100)
            result=client.post('/api/live-coach/spotter/radio/e/render').json()
            self.assertEqual(base64.b64decode(result['audio_base64']),b'fixture'*100)
            path.unlink()
            # A race between the existence check and the actual read also cannot synthesize.
            coach.radio_audio_mode=lambda e:(True,False)
            self.assertEqual(client.post('/api/live-coach/spotter/radio/e/render').status_code,422)
            self.assertFalse(speech.mock_calls)


if __name__=='__main__':unittest.main()
