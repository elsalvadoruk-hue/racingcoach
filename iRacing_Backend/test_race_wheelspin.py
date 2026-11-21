import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import LiveTelemetry
from race_wheelspin import KEY, SYMPTOM, trial
from voice_coach import VoiceCoach, register_voice_routes
from voice_notes import VoiceNoteStore


class WheelspinTests(unittest.TestCase):
    """Drive the public sampler with independent session/traffic and wheel observations."""
    def setUp(self):
        self.t = 0.
        self.live = LiveTelemetry(clock=lambda:self.t)
        self.addCleanup(self.live.driving_learning.close)
        self.defs = [dict(id='C1',call='9번 코너',entry_pct=.20,apex_pct=.24,exit_pct=.30)]
        self.live.corners.lookup = lambda _:self.defs
        self.live.driving_audio_ready = lambda _:True
        self.car = dict(game='iracing',car_path='bmwm4gt3',track_id='123')
        self.session = 'Race'

    def drive(self, change=lambda s,n,i:None, *, until=4):
        seen=[]
        for tick in range(1,until*600):
            n,i=divmod(tick,600)
            n+=1
            p=i/600
            self.t=tick*.1
            power=.24<=p<=.30
            braking=.16<=p<.24
            s=dict(session_time=self.t,session_num=0,on_track=True,on_pit_road=False,is_replay=False,
                   session_state=4,session_flags=0,track_surface=3,lap_number=n,distance_pct=p,car_left_right=1,
                   speed_mps=40.,gear=3,throttle=0. if braking else .9,brake=.6 if braking else 0.,
                   steering_rad=.25 if braking or power else 0.,yaw_rate=.22 if power else 0.,
                   LF_wheel_speed=40.,RF_wheel_speed=40.,LR_wheel_speed=44. if power else 40.,
                   RR_wheel_speed=44. if power else 40.,tire_compound=0,track_temp_c=23.,track_wetness=1,
                   setup_fixed=False,tc_setting=3 if n==1 and i<55 else 2,incidents=0)
            change(s,n,i)
            race=dict(player_idx=0,sample_time=self.t,session_type=self.session,track_length_m=2400,
                      cars=[dict(idx=0,track_surface=3,on_pit_road=False,distance_pct=p,lap_completed=n-1,
                                 class_est_lap_s=60.)]) if tick%10==0 or tick==1 else None
            if race and s.get('car_ahead_m')==80.:
                race['cars'].append(dict(idx=1,track_surface=3,on_pit_road=False,
                                         distance_pct=(p+80/2400)%1,lap_completed=n-1,class_est_lap_s=60.))
            self.live.ingest(self.car,s,race)
            if any(e['key']==KEY for e in self.live.radio.pending):
                seen=[e for e in self.live.radio.view(self.t)['events'] if e['key']==KEY]
                if seen:return seen[0]
        self.assertFalse(self.live._corner_failure)

    def test_trial_render_and_cancellation_through_normal_sampler_and_http(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'IRACING_RADIO_AUDIO_DIR':''}):
            speech=Mock()
            coach=VoiceCoach(self.live,VoiceNoteStore(Path(tmp)/'notes.sqlite3'),speech=speech)
            app=FastAPI()
            register_voice_routes(app,coach,None,None,None,None)
            clips={}
            for index,part in enumerate(['9번 코너.',trial(3),SYMPTOM]):
                path=coach.fixed_audio.path('coach',part)
                path.parent.mkdir(exist_ok=True)
                clips[part]=bytes([index+1])*200
                path.write_bytes(clips[part])
            event=self.drive()
            self.assertEqual(event['text'],'9번 코너. '+trial(3))
            self.assertEqual(event['alert_categories'],['pitwall.tc'])
            self.assertEqual(event['group'],'control_advice')
            self.assertFalse(event['cached'])
            url=f"/api/live-coach/spotter/radio/{event['id']}/render"
            with TestClient(app) as client:
                result=client.post(url).json()
                self.assertEqual([base64.b64decode(x) for x in result['audio_parts']],
                                 [clips[p] for p in event['speech_parts']])
                coach.fixed_audio.path('coach',trial(3)).unlink()
                self.assertTrue(client.post(url).json()['missing_prepared_audio'])
                self.live.radio.configure_alerts(pitwall={'tc':False})
                self.assertIsNone(client.post(url).json()['event'])
            speech.synthesize.assert_not_called()
            self.live.radio_delivery(event['id'],'muted')
            self.assertFalse(self.live.wheelspin.sent_laps)

    def test_same_tire_stable_numeric_proof_and_independent_bias(self):
        def change(s,n,i):
            if n==1 and i<55:s['tire_compound']=1
            s['brake_bias']=50.+n*.25
        event=self.drive(change)
        self.assertEqual(event['text'],'9번 코너. '+SYMPTOM)
        self.live.radio_delivery(event['id'],'started')
        self.live.radio_delivery(event['id'],'completed')
        self.assertEqual(list(self.live.wheelspin.sent_laps),[3])
        self.assertIn('C1',self.live.wheelspin.reported)

    def test_measured_separation_supports_observation_but_call_waits_for_solo(self):
        def traffic(s,n,i):
            s['car_ahead_m']=80. if i<215 else 180.
            s['car_behind_m']=220.
        event=self.drive(traffic)
        self.assertEqual(event['text'],'9번 코너. '+trial(3))
        self.assertGreaterEqual(self.live.samples[-1]['distance_pct'],215/600)

    def test_no_positive_without_both_wheels_and_last_pass(self):
        for reason in ('single_wheel','missing','shift','short','last_clean','traffic','nearby','incidents','mixed_compound','no_window'):
            with self.subTest(reason=reason):
                self.setUp()
                def change(s,n,i):
                    if reason=='single_wheel':s['LF_wheel_speed']=s['LR_wheel_speed']
                    if reason=='missing':s.pop('RF_wheel_speed')
                    if reason=='shift':s['gear']=2+i%2
                    if reason=='short' and i%3==0:s['RR_wheel_speed']=40.
                    if reason=='last_clean' and n>=3:s['RR_wheel_speed']=40.
                    if reason=='traffic' and n==2 and i==153:s['car_left_right']=2
                    if reason=='nearby' and n==2 and i==153:s.update(car_ahead_m=23.,car_behind_m=130.)
                    if reason=='incidents':s['incidents']=int(n>2 or n==2 and i>=400)
                    if reason=='mixed_compound':s['tire_compound']=n%2
                    if reason=='no_window':s['steering_rad']=.4
                self.assertIsNone(self.drive(change,until=3))

    def test_numeric_off_and_unproven_never_suggested(self):
        for car,fixed,first,normal in [('bmwm4gt3',False,10,9),('bmwm4gt3',True,3,2),
                                       ('bmwm4gt3',None,3,2),('mclaren720sgt3',False,2,1)]:
            with self.subTest(car=car,fixed=fixed,normal=normal):
                self.setUp()
                self.car['car_path']=car
                event=self.drive(lambda s,n,i:s.update(setup_fixed=fixed,tc_setting=first if n==1 and i<55 else normal))
                self.assertEqual(event['text'],'9번 코너. '+SYMPTOM)

    def test_missing_corner_recording_unknown_location_game_and_session(self):
        for reason in ('missing_audio','unnamed','other_car','lmu','practice'):
            with self.subTest(reason=reason):
                self.setUp()
                if reason=='missing_audio':self.live.driving_audio_ready=lambda text:text!='9번 코너.'
                if reason=='unnamed':self.defs[0]['call']=None
                if reason=='other_car':self.car['car_path']='other'
                if reason=='lmu':self.car['game']='lmu'
                if reason=='practice':self.session='Practice'
                self.assertIsNone(self.drive(until=3))

    def test_stale_setting_and_short_straight_withdraw_event(self):
        event=self.drive()
        s=self.live.samples[-1].copy()
        s.update(session_time=s['session_time']+.1,distance_pct=s['distance_pct']+.001,tc_setting=1)
        self.t+=.1
        self.live.ingest(self.car,s)
        self.assertIsNone(self.live.radio.get(event['id'],self.t))
        self.assertFalse(self.live.wheelspin.sent_laps)
