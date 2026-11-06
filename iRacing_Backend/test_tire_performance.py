from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from tire_reader.performance import TirePerformance, PerformanceStore, display_learning
from tire_reader.snapshot import WHEELS
from test_tire_advisor import snapshot
from tire_reader.advisor import TireAdvisor,PHRASES

DEFINITIONS=[{'entry_pct':p,'exit_pct':p+.1} for p in (.1,.4,.7)]


def replay(learner=None, laps=7, patch=None, definitions=True, duration_scale=1, warm_duration=36):
    learner=learner or TirePerformance()
    at=0.
    for lap in range(laps):
        duration=(40 if lap<2 else warm_duration)*duration_scale
        for second in range(duration):
            t=at+second
            temp=60. if lap<2 else 85.
            s=replace(snapshot(t,state=None),lap=lap,session_type='Offline Testing',
                air_temp_c=24.,track_temp_c=40.,fuel_l=45.,tire_compound_index=0,track_wetness_iracing=1,
                installed_cold_kpa={w:159. for w in WHEELS},installed_lap={w:0 for w in WHEELS},
                wheels={w:replace(v,temp_c=temp,pressure_kpa=165. if lap<2 else 175.,temp_state=None) for w,v in snapshot(t,state=None).wheels.items()})
            rows=[]
            for part in range(30):
                pct=(second+part/30)/duration
                turning=any(d['entry_pct']<=pct<d['exit_pct'] for d in DEFINITIONS)
                rows.append(dict(session_time=t+part/30,lap_number=lap,distance_pct=pct,
                    speed_mps=30. if lap<2 else 33.,steering_rad=.2 if turning else 0.,yaw_rate=.35 if turning else 0.,
                    brake=.2 if turning else 0.,throttle=.3 if turning else 1.,incidents=0,session_flags=0,
                    car_left_right=1,on_track=True,on_pit_road=False,is_replay=False,abs_active=turning,
                    abs_setting=4.,tc_setting=4.,brake_bias=54.,lat_accel_mps2=11. if turning else 0.,
                    long_accel_mps2=-6. if turning else 1.,vert_accel_mps2=9.80665))
            # Real snapshots are made after the newest sample, not before it.
            s=replace(s,t=t+29/30,observed_at=t+29/30)
            context={'run':'test-run','samples':rows,'corners':DEFINITIONS if definitions else []}
            if patch:
                s,context=patch(s,context)
            learner.observe(s,context)
        at+=duration
    return learner,s,context


class PerformanceTests(unittest.TestCase):
    def test_fuel_burn_does_not_discard_validated_repeated_warm_passes(self):
        for burn in (2.,3.9):
            for car in ('mclaren720sgt3','bmwm4gt3'):
                def patch(s,c):
                    s=replace(s,car=car,fuel_l=80-burn*(s.lap+c['samples'][-1]['distance_pct']))
                    if car=='bmwm4gt3':
                        s=replace(s,wheels={w:replace(v,temp_c=None,temp_kind=None) for w,v in s.wheels.items()})
                    return s,c
                learner,s,_=replay(patch=patch)
                with self.subTest(burn=burn,car=car):
                    self.assertEqual(learner.view['status'],'ready',learner.view)
                    self.assertGreaterEqual(learner.view['laps'],3)
                    self.assertGreaterEqual(learner.view['passes'],12)
                    self.assertTrue(any(s.fuel_l-r['fuel'] < -6 for r in learner._references(s)))

    def test_slower_hot_passes_cannot_forget_faster_heavier_fuel_evidence(self):
        def patch(s,c):
            if s.lap>=2:
                for row in c['samples']:row['lat_accel_mps2']*=1.5
            return replace(s,fuel_l=80-3.9*(s.lap+c['samples'][-1]['distance_pct'])),c
        learner,_,_=replay(warm_duration=44,patch=patch)
        self.assertNotEqual(learner.view['status'],'ready',learner.view)
        good=TirePerformance._good(list(learner.records))
        self.assertFalse(any(r['lap'] in (2,3,4) for r in good))

    def test_one_nearby_fuel_lap_cannot_validate_unmatched_fuel_records(self):
        learner,_,_=replay()
        base=next(r for r in learner.records if r['lap']==2)
        rows=[{**base,'lap':i,'age':i,'fuel':80-15*i} for i in range(4)]
        self.assertEqual(TirePerformance._good(rows),[])

    def test_two_lap_local_comparison_uses_the_faster_pass_as_reference(self):
        learner,_,_=replay()
        base=next(r for r in learner.records if r['lap']==2)
        fast={**base,'lap':2,'duration':3.,'fuel':40.,'age':2}
        slow={**base,'lap':3,'duration':3.3,'fuel':36.,'age':3}
        self.assertEqual(TirePerformance._good([fast,slow]),[fast])

    def test_repeated_faster_warm_passes_learn_without_controlling_speed_away(self):
        learner,s,context=replay()
        self.assertGreaterEqual(len(learner.records),15)
        self.assertEqual(learner.view['status'],'ready',learner.view)
        self.assertTrue(learner.view['front'] and learner.view['rear'])
        self.assertFalse(learner.view['physical_optimum_confirmed'])
        self.assertTrue(any(r['temps'][0]==60 for r in learner.records))
        self.assertEqual(learner.view['ranges']['front'],[85.,85.])
        self.assertTrue(all(r['abs_fraction']==1. for r in learner.records))

    def test_more_g_without_faster_passages_is_not_a_good_temperature(self):
        def patch(s,c):
            if s.lap>=2:
                for r in c['samples']:r['lat_accel_mps2']*=1.5
            return s,c
        learner,_,_=replay(warm_duration=44,patch=patch)
        self.assertNotEqual(learner.view['status'],'ready')

    def test_verified_pressure_only_cars_learn_pressure_without_inventing_temperatures(self):
        def patch(s,c):
            return replace(s,car='bmwm4gt3',wheels={w:replace(v,temp_c=None,temp_kind=None) for w,v in s.wheels.items()}),c
        learner,s,c=replay(patch=patch)
        self.assertEqual(learner.view['status'],'ready')
        self.assertEqual(learner.view['mode'],'pressure_only')
        self.assertEqual(learner.view['ranges'],{})
        self.assertEqual(learner.view['pressure_ranges']['front'],[175.,175.])
        self.assertTrue(all(r['temps']==[None]*4 for r in learner.records))
        s=replace(s,t=s.t+1,observed_at=s.observed_at+1,
                  wheels={w:replace(v,pressure_kpa=None) for w,v in s.wheels.items()})
        c={**c,'samples':[{**r,'session_time':r['session_time']+1} for r in c['samples']]}
        for _ in range(4):
            learner.observe(s,c)
            s=replace(s,t=s.t+1,observed_at=s.observed_at+1)
            c={**c,'samples':[{**r,'session_time':r['session_time']+1} for r in c['samples']]}
        self.assertNotEqual(learner.view['status'],'ready')

    def test_controller_pipe_and_status_include_mode_and_expire_without_new_ocr(self):
        from io import StringIO
        from types import SimpleNamespace
        from unittest.mock import patch
        from tire_overlay_control import TireOverlayController
        with tempfile.TemporaryDirectory() as folder:
            controller=TireOverlayController(Path(folder)/'tires.json')
            s=snapshot(10.)
            controller.snapshot=s;controller.ready=True
            pipe=StringIO()
            controller.process=SimpleNamespace(poll=lambda:None,stdin=pipe)
            controller.advisor.performance.view.update(status='ready',mode='pressure_only',front=True,rear=True)
            controller._send_performance()
            self.assertEqual(json.loads(pipe.getvalue())['view']['mode'],'pressure_only')
            with patch('tire_overlay_control.time.monotonic',return_value=11.):
                self.assertEqual(controller.status()['performance']['status'],'ready')
            with patch('tire_overlay_control.time.monotonic',return_value=13.):
                self.assertEqual(controller.status()['performance']['status'],'learning')
                self.assertFalse(controller.status()['performance']['front'])
            controller.process=None

    def test_one_lap_or_one_corner_cannot_turn_green(self):
        for laps in (2,3,4,5):
            with self.subTest(laps=laps):
                self.assertNotEqual(replay(laps=laps)[0].view['status'],'ready')
        def patch(s,c):
            c['corners']=DEFINITIONS[:1]
            for r in c['samples']:
                if r['distance_pct']>.3:r['yaw_rate']=0.;r['steering_rad']=0.;r['lat_accel_mps2']=0.
            return s,c
        self.assertNotEqual(replay(patch=patch)[0].view['status'],'ready')

    def test_missing_g_controls_or_tire_observations_withhold(self):
        for problem in ('g','abs_setting','pressure','temperature','age'):
            def patch(s,c):
                if problem in ('g','abs_setting'):
                    for r in c['samples']:r['lat_accel_mps2' if problem=='g' else 'abs_setting']=None
                elif problem=='age':s=replace(s,installed_lap={})
                else:s=replace(s,wheels={w:replace(v,**{'pressure_kpa' if problem=='pressure' else 'temp_c':None}) for w,v in s.wheels.items()})
                return s,c
            with self.subTest(problem=problem):self.assertNotEqual(replay(patch=patch)[0].view['status'],'ready')

    def test_hotter_unobserved_values_pressure_or_weather_changes_revoke_green(self):
        for problem in ('temperature','pressure','track','wet','compound','controls'):
            learner,s,c=replay()
            s=replace(s,t=s.t+1,observed_at=s.observed_at+1)
            c={**c,'samples':[{**r,'session_time':r['session_time']+1} for r in c['samples']]}
            if problem in ('temperature','pressure'):
                s=replace(s,wheels={w:replace(v,**({'temp_c':95.} if problem=='temperature' else {'pressure_kpa':190.})) for w,v in s.wheels.items()})
            elif problem=='track':s=replace(s,track_temp_c=46.)
            elif problem=='wet':s=replace(s,track_wetness_iracing=3)
            elif problem=='compound':s=replace(s,tire_compound_index=1)
            else:
                for r in c['samples']:r['abs_setting']=6.
            learner.observe(s,c)
            with self.subTest(problem=problem):self.assertNotEqual(learner.view['status'],'ready')

    def test_incident_lap_keeps_earlier_turns_and_recovers_in_later_segments(self):
        def patch(s,c):
            if s.lap==3:
                for r in c['samples']:
                    r['incidents']=int(r['distance_pct']>=.35)
            elif s.lap>3:
                for r in c['samples']:r['incidents']=1
            return s,c
        learner,_,_=replay(patch=patch)
        records=[r for r in learner.records if r['lap']==3]
        self.assertTrue(any(r['position']<.3 for r in records))
        self.assertTrue(any(r['position']>.65 for r in records))
        self.assertFalse(any(.35<r['position']<.65 for r in records))

    def test_straight_weaves_do_not_become_turn_evidence(self):
        def patch(s,c):
            for i,r in enumerate(c['samples']):r['yaw_rate']*=1 if i%6<3 else -1
            return s,c
        learner,_,_=replay(patch=patch)
        self.assertEqual(len(learner.records),0)

    def test_reset_cannot_count_the_same_run_lap_and_corner_twice(self):
        learner,_,_=replay()
        count=len(learner.records)
        learner.reset()
        replay(learner)
        self.assertEqual(len(learner.records),count)

    def test_safe_live_data_loss_or_frozen_time_immediately_revokes_green(self):
        for problem in ('frozen','missing','replay','incident','traffic','kind'):
            learner,s,c=replay()
            if problem!='frozen':
                s=replace(s,t=s.t+1,observed_at=s.observed_at+1)
                c={**c,'samples':[{**r,'session_time':r['session_time']+1} for r in c['samples']]}
            if problem=='missing':c={**c,'samples':[]}
            elif problem=='kind':s=replace(s,wheels={w:replace(v,temp_kind=None) for w,v in s.wheels.items()})
            elif problem in ('replay','incident','traffic'):
                for r in c['samples']:r[{'replay':'is_replay','incident':'incidents','traffic':'car_left_right'}[problem]]=True if problem=='replay' else 2
            learner.observe(s,c)
            with self.subTest(problem=problem):
                self.assertNotEqual(learner.view['status'],'ready')
                self.assertFalse(learner.view['front'] or learner.view['rear'])

    def test_individually_known_axles_do_not_validate_an_unseen_joint_combination(self):
        learner,s,c=replay()
        originals=list(learner.records)
        learner.records.clear()
        for r in originals:
            learner.records.append({**r,'temps':r['temps'][:2]+[100.,100.]})
            learner.records.append({**r,'temps':[100.,100.]+r['temps'][2:]})
        for _ in range(5):
            s=replace(s,t=s.t+1,observed_at=s.observed_at+1)
            c={**c,'samples':[{**r,'session_time':r['session_time']+1} for r in c['samples']]}
            learner.observe(s,c)
        self.assertNotEqual(learner.view['status'],'ready')
        self.assertFalse(learner.view['front'] and learner.view['rear'])

    def test_fallback_distance_segments_can_learn_without_file_corner_definitions(self):
        learner,_,_=replay(definitions=False,duration_scale=2)
        self.assertGreater(len(learner.records),0)

    def test_saved_evidence_survives_restart_and_corrupt_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'performance.json'
            learner,_,_=replay(TirePerformance(path))
            before=list(learner.records)
            learner.close()
            loaded=TirePerformance(path)
            self.assertEqual(list(loaded.records),before)
            loaded.close()
            path.write_text('{broken',encoding='utf-8')
            store=PerformanceStore(path)
            self.assertEqual(store.load(),[])
            store.submit(before);store.close()
            self.assertEqual(path.read_text(encoding='utf-8'),'{broken')
            self.assertTrue(store.error)

    def test_native_state_expires_without_another_measurement_and_rejects_other_car(self):
        s=snapshot(10.).to_dict()
        view={k:s[k] for k in ('game','car','session_key','epoch')}
        view.update(updated_at=10.,status='ready',front=True,rear=True)
        self.assertTrue(display_learning(view,s,11.)['front'])
        self.assertFalse(display_learning(view,s,13.)['front'])
        self.assertFalse(display_learning(view,{**s,'car':'other'},11.)['front'])

    def test_prepared_voice_uses_learned_basis_once_per_stint_and_revokes_on_data_loss(self):
        learner,s,context=replay()
        advisor=TireAdvisor(clock=lambda:s.observed_at)
        advisor.performance=learner;advisor.identity=s.identity;advisor.latest=s;advisor.last_t=s.t
        def advance():
            nonlocal s,context
            s=replace(s,t=s.t+1,observed_at=s.observed_at+1)
            context={**context,'samples':[{**r,'session_time':r['session_time']+1} for r in context['samples']]}
            advisor.observe(s.to_dict(),enabled=True,context=context)
        advance()
        event=advisor.current(s.observed_at)
        self.assertEqual(event['text'],PHRASES['tires_ok'])
        self.assertEqual(event['basis'],'learned_performance')
        self.assertIsNone(advisor.current(s.observed_at+3))
        advance()
        event=advisor.current(s.observed_at)
        self.assertTrue(advisor.ack(event['id'],s.observed_at))
        advance()
        self.assertIsNone(advisor.current(s.observed_at))

    def test_local_profile_keys_and_raw_passes_never_enter_export_summary(self):
        from tire_reader.log_export import performance_summary
        data=performance_summary({'status':'ready','front':True,'rear':True,'passes':15,'corners':3,'laps':4,
            'ranges':{'front':[80.,90.],'rear':[75.,85.]},'key':'PRIVATE','records':['PRIVATE']})
        self.assertNotIn('PRIVATE',str(data))
        self.assertFalse(data['physical_optimum_confirmed'])


if __name__=='__main__':unittest.main()
