import copy
import unittest
from driving_observations import observation, windows, digest, scope
from driving_learning import learn, applicable, clean_row
from test_driving_learning import passes


IDENTITY = dict(game='iracing',car_id='1',track_id='2',track_config='full')
CORNERS = [dict(id='C1',entry_pct=.2,apex_pct=.25,exit_pct=.3),
           dict(id='C2',entry_pct=.36,apex_pct=.4,exit_pct=.44)]


def frames():
    result = []
    for i in range(1001):
        pct = i*.0005
        result.append(dict(distance_pct=pct,session_time=1+i/60,speed_mps=30.,
            brake=.5 if .16<pct<.245 else 0.,throttle=.8 if pct>.27 else 0.,
            steering_rad=.1,on_track=True,on_pit_road=False,is_replay=False,
            track_surface=3,incidents=0,session_flags=0,car_left_right=1,engine_warnings=0,
            pit_repair_left_s=0.,traffic_context='SOLO',fuel_l=40.,track_temp_c=35.,
            tire_compound=0,track_wetness=0,brake_bias=52.,abs_setting=3.,tc_setting=5.,
            vert_accel_mps2=9.8,LF_shock=.05,abs_active=False,yaw_rate=.1))
    return result


class DrivingObservationTests(unittest.TestCase):
    def observe(self, raw=None):
        definition = windows(CORNERS,1000)[0]
        return observation(raw or frames(),definition,IDENTITY,'run',1,1000)

    def test_fixed_window_interpolation_and_control_features(self):
        row, reason = self.observe()
        self.assertIsNone(reason)
        self.assertAlmostEqual(row['metrics']['time_s'],(.3-.1)*1000/30)
        self.assertGreater(row['metrics']['release_m'],row['metrics']['brake_start_m'])
        self.assertEqual(row['traffic'],'SOLO')
        self.assertEqual(row['metrics']['relifts'],0)
        self.assertNotIn('impact_score',row['metrics'])

    def test_all_required_safety_channels_withhold_when_missing(self):
        for key in ('session_flags','engine_warnings','pit_repair_left_s','car_left_right','incidents',
                    'on_track','on_pit_road','is_replay','track_surface'):
            raw=frames()
            del raw[300][key]
            row, reason = self.observe(raw)
            self.assertIsNone(row,key)
            self.assertEqual(reason,'UNSAFE_OR_UNCONFIRMED_STATE',key)

    def test_incident_elsewhere_does_not_poison_sound_window(self):
        raw=frames()
        raw[900]['incidents']=1
        self.assertIsNotNone(self.observe(raw)[0])
        raw[300]['incidents']=1
        self.assertEqual(self.observe(raw)[1],'INCIDENT')

    def test_gap_and_incomplete_windows_are_withheld(self):
        raw=frames()
        raw[300]['session_time']+=1
        self.assertEqual(self.observe(raw)[1],'SAMPLE_GAP')
        self.assertEqual(self.observe(frames()[:350])[1],'INCOMPLETE_WINDOW')

    def test_control_change_during_window_is_not_a_setting_trial(self):
        raw=frames();raw[300]['abs_setting']=4
        self.assertEqual(self.observe(raw)[1],'CONTROL_CHANGED')

    def test_connected_windows_and_non_wrapping_policy(self):
        definitions=windows(CORNERS,1000)
        chain=next(d for d in definitions if d['kind']=='chain')
        self.assertEqual(chain['members'],['C1','C2'])
        self.assertGreater(chain['end_pct'],.44)
        self.assertFalse(windows([dict(id='C9',entry_pct=.98,apex_pct=.99,exit_pct=.02)],1000))

    def test_vertical_motion_without_suspension_motion_is_not_kerb_evidence(self):
        raw=frames()
        for i in range(280,310):raw[i]['vert_accel_mps2']=20.
        self.assertNotIn('impact_score',self.observe(raw)[0]['metrics'])
        for i in range(280,310):raw[i]['LF_shock']=.065
        row,_=self.observe(raw)
        self.assertIn('impact_score',row['metrics'])
        self.assertFalse(row['impact_verified'])
        raw[285]['LF_rumble']=1.
        self.assertTrue(self.observe(raw)[0]['impact_verified'])

    def test_unknown_traffic_does_not_confirm_and_wrong_scope_cannot_apply(self):
        rows=passes()
        model=learn(rows)['technique'][0]
        current=copy.deepcopy(rows[-1]);current['lap']=20;current['window']='wrong'
        self.assertFalse(applicable(model,current))
        for row in rows:row['traffic']='UNKNOWN'
        result=learn(rows)
        self.assertEqual(result['eligible'],0)
        self.assertFalse(result['technique'])

    def test_profile_allowlist_drops_arbitrary_content_and_malformed_rows(self):
        row=passes()[0];row['driver_name']='private';row['metrics']['note']='private'
        cleaned=clean_row(row)
        self.assertNotIn('driver_name',cleaned)
        self.assertNotIn('note',cleaned['metrics'])
        self.assertIsNone(clean_row({'id':'invalid'}))


if __name__=='__main__':unittest.main()
