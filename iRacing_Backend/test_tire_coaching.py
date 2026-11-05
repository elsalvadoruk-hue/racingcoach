from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from tire_reader.coaching import TireCoaching,live_context
from tire_reader.snapshot import native_session_ref
from tire_reader.advisor import TireAdvisor,PHRASES,SHORT_PHRASES
from test_tire_advisor import snapshot


def drive(mode='front',patch=None,seconds=361):
    coach=TireCoaching()
    for tick in range(seconds):
        lap=tick//60;sec=tick%60;at=tick+.75
        front=98. if lap>=4 and mode in ('front','trend','both') else 90.
        rear=98. if lap>=4 and mode in ('rear','both') else 90.
        if mode=='recovery' and 4<=lap<=6:front=98.
        s=replace(snapshot(at,state=None),lap=lap,session_type='Race',air_temp_c=25.,track_temp_c=30.,
            fuel_l=30.,tire_compound_index=0,track_wetness_iracing=0.,lap_last_s=60.+max(0,lap-4)*.2)
        s=replace(s,wheels={w:replace(v,temp_c=front if w in ('FL','FR') else rear,temp_state=None) for w,v in s.wheels.items()})
        rows=[]
        for part in range(4):
            pct=(sec+part*.25)/60
            counter=mode=='rear' and lap>=4 and any(p<=pct<p+.025 for p in (.2,.55))
            bad=mode=='front' and lap>=4
            rows.append(dict(session_time=tick+part*.25,lap_number=lap,distance_pct=pct,speed_mps=30.,
                steering_rad=-.2 if counter else .32 if bad else .2,yaw_rate=.21 if bad else .3,
                throttle=.8 if counter else .3,brake=0.,incidents=0,session_flags=0,car_left_right=1,
                on_track=True,on_pit_road=False,is_replay=False))
        context={'run':'synthetic','samples':rows}
        if patch:s,context=patch(s,context)
        coach.observe(s,context)
        if mode=='recovery' and coach.candidate=='trend_front':coach.ack(coach.candidate)
    return coach,s,context


class CoachingTests(unittest.TestCase):
    def test_front_action_requires_repeated_matched_response_changes(self):
        c,_,_=drive()
        self.assertEqual(c.candidate,'input_front')
        self.assertEqual(c.view['baseline_laps'],[2,3])
        self.assertEqual(c.view['recent_laps'],[4,5])
        self.assertEqual(c.view['front_change_c'],8.)
        self.assertFalse(c.view['wear_or_grip_confirmed'])

    def test_temperature_only_is_a_trend_not_an_input_diagnosis(self):
        c,_,_=drive('trend')
        self.assertEqual(c.candidate,'trend_front')
        c,_,_=drive('both')
        self.assertIsNone(c.candidate)

    def test_rear_action_requires_repeated_power_corrections_and_never_claims_wheelspin(self):
        c,_,_=drive('rear')
        self.assertEqual(c.candidate,'input_rear')
        self.assertNotIn('휠스핀',PHRASES[c.candidate])

    def test_recovery_is_temperature_return_not_grip_or_normal_window(self):
        c,_,_=drive('recovery',seconds=541)
        self.assertEqual(c.candidate,'temperature_recovered')
        self.assertNotIn('그립',PHRASES[c.candidate])
        self.assertNotIn('적정',PHRASES[c.candidate])
        c.ack(c.candidate)
        self.assertTrue(c.recovered)

    def test_warmup_or_one_recent_lap_is_insufficient(self):
        for seconds in (120,180,240,300):
            with self.subTest(seconds=seconds):self.assertIsNone(drive(seconds=seconds)[0].candidate)

    def test_unknown_or_changed_conditions_withhold_comparison(self):
        for field,value in (('track_temp_c',36.),('air_temp_c',31.),('fuel_l',20.),('tire_compound_index',1),('track_wetness_iracing',1.),('fuel_l',None)):
            with self.subTest(field=field,value=value):
                def patch(s,c):return (replace(s,**{field:value}) if s.lap>=4 else s),c
                self.assertIsNone(drive(patch=patch)[0].candidate)

    def test_missing_kind_mixed_kind_or_temperature_gap_withholds(self):
        for kind in ('missing','mixed','gap'):
            with self.subTest(kind=kind):
                def patch(s,c):
                    if s.lap>=4:
                        values={w:replace(v,temp_kind=None if kind=='missing' else 'surface' if w=='FL' and kind=='mixed' else v.temp_kind,
                            temp_c=None if kind=='gap' and 10<=s.t%60<16 else v.temp_c) for w,v in s.wheels.items()}
                        s=replace(s,wheels=values)
                    return s,c
                self.assertIsNone(drive(patch=patch)[0].candidate)

    def test_incidents_replay_traffic_pit_and_link_loss_clear_evidence(self):
        for problem in ('incident','replay','traffic','pit','link'):
            with self.subTest(problem=problem):
                def patch(s,c):
                    if s.lap>=4:
                        if problem=='pit':s=replace(s,on_pit_road=True)
                        elif problem=='link':c=None
                        else:
                            for row in c['samples']:
                                row.update({'incidents':int(s.t%60>20)} if problem=='incident' else
                                    {'is_replay':True} if problem=='replay' else {'car_left_right':2})
                    return s,c
                self.assertIsNone(drive(patch=patch)[0].candidate)

    def test_current_recovery_missing_data_and_other_session_invalidate_candidate(self):
        for reason in ('cooled','missing','session'):
            with self.subTest(reason=reason):
                c,s,context=drive()
                self.assertIsNotNone(c.candidate)
                s=replace(s,t=s.t+1,observed_at=s.observed_at+1)
                if reason=='session':s=replace(s,session_key='other')
                else:s=replace(s,wheels={w:replace(v,temp_c=None if reason=='missing' else 90.) for w,v in s.wheels.items()})
                c.observe(s,context)
                self.assertIsNone(c.candidate)

    def test_advisor_uses_default_text_and_checks_freshness_before_start(self):
        c,s,context=drive()
        a=TireAdvisor(clock=lambda:s.observed_at)
        a.identity=s.identity;a.latest=s;a.last_t=s.t;a.coaching=c
        s=replace(s,t=s.t+1)
        a.observe(s.to_dict(),enabled=True,context=context)
        event=a.current(s.observed_at)
        self.assertEqual(event['text'],PHRASES['input_front'])
        self.assertIsNone(a.current(s.observed_at+3))

    def test_scripts_have_both_lengths_without_pressure_adjustment_instructions(self):
        self.assertEqual(len(PHRASES),19);self.assertEqual(set(PHRASES),set(SHORT_PHRASES))
        self.assertTrue(all(PHRASES[k]!=SHORT_PHRASES[k] for k in PHRASES))
        self.assertFalse(any('bar' in t or '공기압을' in t or '시작.' in t for t in list(PHRASES.values())+list(SHORT_PHRASES.values())))


class LinkTests(unittest.TestCase):
    def test_context_is_same_session_car_track_and_bounded_numeric_only(self):
        identity=dict(game='iracing',car_path='bmwm4gt3',track_id=7,track_config='test',session_id='3',subsession_id='4',session_num=0)
        s=replace(snapshot(20.),car='bmwm4gt3',track='iracing-track-7-test',native_session_ref=native_session_ref('iracing','3','4',0))
        live=SimpleNamespace(lock=threading.Lock(),run_id='run',identity=identity,clock=lambda:100.,
            samples=[dict(wall_time=100.,session_time=i/10,run_id='run',driver_name='PRIVATE') for i in range(201)])
        data=live_context(live,s)
        self.assertLessEqual(len(data['samples']),33)
        self.assertNotIn('PRIVATE',str(data))
        self.assertIsNone(live_context(live,replace(s,car='other')))
        live.clock=lambda:104.
        self.assertIsNone(live_context(live,s))
