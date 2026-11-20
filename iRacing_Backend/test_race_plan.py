import base64
import os
from collections import deque
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from race_call_audio import CALLS, prepared_file
from race_corner_cost import CornerCosts, coast_combinations
from race_encounters import RaceEncounters, encounter_location
from race_ibt_costs import IbtCornerCosts, profile_key
from race_plan import RacePlan, fuel_plan, pit_target
from race_radio import RaceRadio
from race_engineer import RaceEngineer
from radio_audio import RadioAudioCache
from tire_reader.advisor import TireAdvisor
from test_tire_advisor import snapshot
from test_tire_performance import replay
from voice_alerts import category


def view(**changes):
    return dict(dict(fuel_l=19.6,fuel_l_per_lap=2.,fuel_observed_laps=4,remaining_laps=10.,
                     tank_capacity_l=40.,pit_loss_s=30.,fuel_save_mode='finish',
                     fuel_reserve_l=1.,fuel_rate_lps=2.,tyre_decision='keep',game='iracing'),**changes)


def option(name,entry,saving,cost):
    return dict(zone=name,label=name,entry=entry,start=entry-.03,finish=entry+.05,
                cue='coast_half',liters=saving,seconds=cost)


class StrategyTests(unittest.TestCase):
    def test_finish_savings_require_measured_range_and_yield_to_stops_and_depletion(self):
        wall = RaceEngineer()
        facts = dict(game='lmu', remaining_laps=1.25, fuel_finish_shortfall_l=.23, fuel_save_pct=6.4,
                     fuel_save_mode='finish', fuel_save_feasible=True, fuel_observed_laps=5, energy_laps=8.2)
        snapshot = {'lap_number': 17}
        self.assertEqual(wall.finish_save_percent(facts, snapshot), 7)
        for override in [dict(energy_laps=None), dict(energy_laps=1.2), dict(fuel_save_pct=10.1),
                         dict(planned_stop_before_finish=True), dict(mandatory_stops_remaining=1),
                         dict(fuel_observed_laps=1), dict(finish_may_extend=True), dict(fuel_save_mode='reserve')]:
            with self.subTest(override=override):
                self.assertIsNone(wall.finish_save_percent({**facts, **override}, snapshot))
        misses = [dict(lap=i, mode='finish', target_l=2.7, use_l=2.86) for i in (15, 16)]
        self.assertIsNone(wall.finish_save_percent(facts, snapshot, misses))
        self.assertEqual(wall.finish_save_percent(facts, snapshot, misses[:1]), 7)
        self.assertIsNone(wall.finish_save_percent(facts, {**snapshot, 'engine_warnings': 2}))

    def test_considers_whole_race_and_keeps_only_one_nonoverlapping_route(self):
        slow = option('T3',.2,.039,.77)
        quick = option('T6',.6,.054,.18)
        near = option('T7',.62,.03,.15)
        routes = coast_combinations([[slow],[quick],[near]])
        self.assertTrue(all(len({r['zone'] for r in opts}&{'T6','T7'})<2 for _,_,opts in routes))
        plan = fuel_plan(view(),routes,0)
        self.assertEqual((plan['cue'],[r['label'] for r in plan['route']]),('save_stop',['T6']))
        self.assertAlmostEqual(plan['net_s'],28.2)
        self.assertNotEqual(fuel_plan(view(),routes,1).get('cue'),'save_stop')
        self.assertNotEqual(fuel_plan(view(),routes,None).get('cue'),'save_stop')
        self.assertNotEqual(fuel_plan(view(game='lmu',energy_laps=6),routes,0).get('cue'),'save_stop')
        self.assertNotEqual(fuel_plan(view(fuel_save_mode='reserve'),routes,0).get('cue'),'save_stop')
        self.assertNotEqual(fuel_plan(view(finish_may_extend=True),routes,0).get('cue'),'save_stop')

    def test_shorter_fill_cannot_save_time_under_longer_concurrent_service(self):
        routes=[(.11,.06,[option('T1',.4,.11,.06)])]
        base=view(fuel_l=12.,tyre_decision='change',fuel_rate_lps=.5,pit_rule='SEQUENTIAL')
        self.assertEqual(fuel_plan(base,routes,1)['cue'],'save_fill')
        base['pit_rule']='CONCURRENT'
        self.assertEqual(fuel_plan(base,routes,1)['cue'],'normal_pace')
        self.assertEqual(fuel_plan(view(fuel_observed_laps=1),routes,0),{})

    def test_manual_target_compares_both_resources(self):
        base=view(game='lmu',fuel_l=12.,fuel_laps=6.,energy_pct=9.,energy_laps=4.)
        self.assertEqual(pit_target(base,5,[1.7,1.75,2.3],[])['target_call'],'energy_save_plan')
        self.assertEqual(pit_target(base,5,[2.,2.2],[])['target_call'],'energy_early')
        base.update(fuel_l=9.75,fuel_laps=4.875)
        self.assertEqual(pit_target(base,5,[1.7,1.75],[])['target_call'],'plan_both_early')
        self.assertEqual(pit_target(base,5,[],[],[(1.9,1.75),(1.91,1.7)])['target_call'],'plan_both_save')
        base.update(energy_laps=8)
        self.assertEqual(pit_target(base,5,[],[(.06,.15,['T6'])])['target_call'],'plan_fuel_save')
        self.assertEqual(pit_target(base,5,[],[])['target_call'],'plan_fuel_early')
        self.assertFalse(pit_target({**base,'energy_laps':None},5,[],[]))

    def test_physical_cut_requires_apex_and_distance_advantage_and_separate_laps(self):
        c=CornerCosts()
        c.zones=[dict(key='a',entry=.2,label='T1')]
        sample=dict(fuel_l=20.,track_temp_c=24.,track_wetness=1,tire_compound=0)
        row=dict(fuel=20.,wet=1,compound=0,temp=24.,used=.2,clear=True,braking=True,
                 coast=0.,distance=200.,time=9.,points=0,off=False,penalty=False,apex_off=False)
        clean=[dict(row,lap=i) for i in range(3)]
        cuts=[dict(row,lap=i+3,points=1,off=True,apex_off=True,distance=192.,time=8.6) for i in range(2)]
        def cue(changes):
            c.records['a']=clean+[dict(r,**changes) for r in cuts]
            c.evaluate(sample)
            return c.limits.get('a',{}).get('cue')
        self.assertEqual(cue({}),'cut_margin')
        self.assertEqual(cue({'penalty':True}),'cut_penalty')
        self.assertEqual(cue({'apex_off':False}),'cut_repeat')
        self.assertEqual(cue({'time':9.5}),'cut_no_gain')
        self.assertIsNone(cue({'points':4}))
        self.assertIsNone(cue({'ambiguous':True}))
        self.assertEqual(cue({'points':None,'step_delta':2}),'cut_repeat')

        # The same apex can have both a faster 1x line and a shorter penalized line.
        deep = [dict(r, lap=r['lap']+2, distance=184., penalty=True, points=0) for r in cuts]
        c.records['a'] = clean + cuts + deep
        c.evaluate(sample)
        self.assertEqual(c.limits['a']['cue'], 'cut_penalty')
        self.assertEqual(c.limits['a']['observed_line'], dict(one_x_shorter_m=[8, 8],
                         penalty_shorter_m=[16, 16], one_x_gain_s=.4))
        c.records['a'][-1]['distance'] = 193.
        c.evaluate(sample)
        self.assertNotIn('observed_line', c.limits['a'])


def racer(idx,p,group=1):
    return dict(idx=idx,distance_pct=p%1,lap_completed=int(p),on_pit_road=False,track_surface=3,
                class_id=group,class_est_lap_s=100 if group==1 else 78,last_lap_s=100)


class TrafficTests(unittest.TestCase):
    def test_catch_counts_from_driver_through_finish_line(self):
        zones = [dict(entry=.01, end=.02), dict(entry=.07, end=.10),
                 dict(entry=.83, end=.86), dict(entry=.93, end=.95)]
        for low, high, expected in [
            (5.80,5.81,'straight_0'), (5.83,5.85,'corner_1'), (5.86+.001,5.90,'straight_1'),
            (5.94,5.949,'corner_2'), (5.97,6.005,'after_2'), (6.012,6.019,'corner_3'),
            (5.94,5.97,None), (5.93-.001,5.951,None),
        ]:
            with self.subTest(low=low, high=high):
                self.assertEqual(encounter_location(5.78,low,high,zones),expected)
        self.assertEqual(encounter_location(5.84,5.85,5.855,zones),'corner_0')
        self.assertEqual(encounter_location(5.84,5.88,5.89,zones),'straight_1')
        self.assertEqual(encounter_location(5.84,5.97,5.98,zones),'after_1')

    def test_close_reversed_order_slower_pair_and_mixed_classes(self):
        traffic=RaceEncounters()
        seen=set()
        for t in range(9):
            own=4.1+t*.01
            lead=4.12+t*.0086
            # Hold each order for independent positions; a single noisy swap does not count.
            a,b=(lead,lead+.002) if t<5 else (lead+(.0035 if t==6 else .002),lead)
            race=dict(cars=[racer(0,own),racer(1,a),racer(2,b)],player_idx=0,track_length_m=4000.,sample_time=t)
            for idx in (1,2):
                for j in range(64):
                    traffic.profiles[idx].setdefault(j,deque([(1,90.),(2,90.)],maxlen=6))
            traffic.update(race,t,[])
            seen.update(k for k,_ in traffic.calls)
        self.assertIn('pack_close',seen)
        self.assertTrue({'pack_fight','pack_slow'} & seen)
        race['cars'][2]['class_id']=2
        traffic.update(race,9,[])
        self.assertFalse(traffic.calls)

    def curves(self):
        tr = RaceEncounters()
        tr.player=0
        for idx,pace in enumerate((100,78,77)):
            tr.previous[idx]=(5.4,2.,False,True)
            tr.positions[idx]=racer(idx,5.4,1 if idx==0 else 2)
            for j in range(64):
                tr.profiles[idx][j]=deque([(1,pace),(2,pace)],maxlen=6)
        return tr

    def test_fast_class_single_zone_train_and_uncertain_boundary(self):
        tr=self.curves()
        tr.closing={i:deque([(0,100.),(1,94.)],maxlen=4) for i in (1,2)}
        tr._catch([(80,1),(85,2)],[],tr.positions[0],5.4,2,4000,[dict(entry=.45,end=.52)])
        self.assertEqual(tr.calls[0][0],'catch_train')
        self.assertEqual(tr.calls[0][1][2], '빠른 클래스 두 대, 다음 코너에서 붙을 듯.')
        tr.calls=[]
        tr._catch([(80,1)],[],tr.positions[0],5.4,3,4000,[dict(entry=.6,end=.65)])
        self.assertEqual(tr.calls[0][0],'catch_straight')
        tr.calls=[]
        tr._catch([(80,1)],[],tr.positions[0],5.4,4,4000,[dict(entry=.47,end=.6)])
        self.assertFalse(tr.calls)

    def test_rejoin_needs_first_braking_and_actual_exit_samples(self):
        tr=self.curves()
        tr.last_time=2.
        tr.exit_samples.extend([.02,.021])
        tr.positions[1]['distance_pct']=.56
        tr.positions[2]['distance_pct']=.5
        zones=[dict(key='T1',entry=.10,end=.12)]
        s=dict(distance_pct=.85,session_time=2.)
        self.assertIsNone(tr.rejoin(view(pace_s=100),s,zones))
        tr.exit_to_brake['T1'].extend([(4.,8.),(4.1,8.1)])
        for one,two,expected in ((.01,.07,'pit_now_gap'),(.34,.43,'stay_for_gap')):
            tr.positions[1]['distance_pct']=one
            tr.positions[2]['distance_pct']=two
            self.assertEqual(tr.rejoin(view(pace_s=100),s,zones)[0],expected)

    def test_travel_across_finish_requires_only_bins_on_the_route(self):
        traffic = self.curves()
        for cell in range(traffic.bins):
            rate = 80. + cell
            traffic.profiles[1][cell] = [(1, rate), (2, rate)]
        del traffic.profiles[1][2]
        start = 63.5 / traffic.bins
        distance = 2.5 / traffic.bins
        # Half of bin 63, then the full bins 0 and 1 on the next lap.
        expected = (.5 * 143. + 80. + 81.) / traffic.bins
        for spread, multiplier in ((-1, .97), (0, 1.), (1, 1.03)):
            self.assertAlmostEqual(traffic.travel(1, start, distance, spread), expected * multiplier)
            self.assertIsNone(traffic.travel(1, start, distance + .001, spread))
            self.assertIsNone(traffic.travel(1, start, distance + 1., spread))


class TireAndDeliveryTests(unittest.TestCase):
    def test_push_requires_this_stop_braking_and_temperature(self):
        a=TireAdvisor()
        learner,s,_=replay()
        learner.previous['session_state']=4
        a.performance=learner
        s=replace(s,session_type='Race',pit_snapshot=dict(stop=1,t=s.t-15,tire_changed={'FL':True}))
        a.latest=s
        a.post_enabled=True
        self.assertIsNone(a._post_call(s.observed_at))
        a.pit_entered_at=s.t-25
        a._track_replacement(s)
        self.assertEqual(a._post_call(s.observed_at),'tire_push')
        learner.last_good_at=s.pit_snapshot['t']-1
        self.assertIsNone(a._post_call(s.observed_at))
        learner.last_good_at=None
        self.assertIsNone(a._post_call(s.observed_at))
        learner.view['mode']='pressure_only'
        self.assertEqual(a._post_call(s.observed_at),'tire_press_ready')
        learner.view.update(mode='combined',status='partial',front=True,rear=False)
        self.assertIsNone(a._post_call(s.observed_at))
        for wheel in ('RL','RR'):
            a.history['temperature',wheel]=[('cold',s.observed_at-i) for i in (2,1,0)]
        self.assertEqual(a._post_call(s.observed_at),'tire_rear_wait')
        a._track_replacement(replace(s,on_pit_road=True))
        self.assertIsNone(a._post_call(s.observed_at))
        # An earlier change may still be present during a subsequent fuel-only stop.
        after_fuel = replace(s, t=s.t+6, installed_lap={'FL':s.lap})
        a._track_replacement(after_fuel)
        self.assertIsNone(a.post_pit)

    def test_all_calls_have_categories_and_selected_prepared_audio(self):
        class Pack:
            selections=dict(coach='short',strategy='short',race='short')
            def has_selection(self,role):return True
            def is_builtin(self,key):return key=='short'
            def has(self,role,text):return False
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            media = directory/'race-radio'
            media.mkdir(parents=True)
            with patch.dict(os.environ,{'IRACING_RADIO_AUDIO_DIR':str(directory)}):
                cache=RadioAudioCache(None,directory)
            for key,(group,role,words,_) in CALLS.items():
                with self.subTest(key):
                    for style in ('default','short'):
                        (media/f'{key}-{style}.ogg').write_bytes((key+style).encode())
                    self.assertEqual(category(key),'pitwall.'+group)
                    cache.custom_packs=None
                    self.assertEqual(base64.b64decode(cache.read_prepared(role,words).split(',')[1]),
                                     prepared_file(role,words,None,directory).read_bytes())
                    cache.custom_packs=Pack()
                    self.assertTrue(cache.cached(role,words))
                    self.assertTrue(prepared_file(role,words,Pack(),directory).name.endswith('-short.ogg'))
                    self.assertEqual(base64.b64decode(cache.read_prepared(role,words).split(',')[1]),
                                     prepared_file(role,words,Pack(),directory).read_bytes())
            cache.custom_packs.selections=dict(coach='other')
            words = CALLS['tire_push'][2]
            self.assertEqual(cache.read_prepared('coach',words),
                             'data:audio/ogg;codecs=opus;base64,'+base64.b64encode(b'tire_pushdefault').decode())

    def test_pit_advice_survives_until_new_audio_is_delivered_for_the_same_plan(self):
        from types import SimpleNamespace
        plan = RacePlan()
        live = SimpleNamespace(game='lmu', radio=RaceRadio())
        key = 'plan_both_early'
        choices = [(key, 14, '')]
        self.assertEqual(plan.superseded_reports(live, choices, {}, {}, key), [])
        plan.delivered(key, 14, 100)
        self.assertIn('ve_need_stop', plan.superseded_reports(live, choices, {}, {}, key))
        self.assertEqual(plan.superseded_reports(live, [(key, 15, '')], {}, {}, key), [])

    def test_missing_finish_save_clip_keeps_critical_fuel_warning(self):
        wall = RaceEngineer()
        facts = dict(active=True, game='iracing', fuel_laps=1.1, fuel_to_add_l=2.,
                     fuel_finish_shortfall_l=.23, remaining_laps=1.2, fuel_save_mode='finish',
                     fuel_save_pct=6.4, fuel_save_feasible=True, fuel_observed_laps=4)
        sample = {'lap_number': 17, 'distance_pct': .65, 'session_time': 1700}
        for ready in (True, False):
            with self.subTest(ready=ready):
                reports = wall.candidates(facts, sample, {'finish_save_audio_ready': lambda _: ready})
                self.assertEqual(any(r.get('finish_save_pct') == 7 for r in reports), ready)
                self.assertEqual(any(r['key'] == 'fuel_critical' for r in reports), not ready)

    def test_queue_rechecks_green_approach_and_actual_receipt(self):
        plan=RacePlan()
        s=dict(speed_mps=45.,steering_rad=0.,brake=0.,on_track=True,is_replay=False,on_pit_road=False,
               track_surface=3,session_flags=0,session_state=4,car_left_right=1,workload_in_s=12)
        plan.choices={'coast_half':('corner',42.,'T1')}
        self.assertTrue(plan.valid('coast_half','corner',42.5,s))
        for field,value in [('brake',.5),('workload_in_s',1),('is_replay',True),('car_left_right',2),('session_flags',8)]:
            self.assertFalse(plan.valid('coast_half','corner',42.6,{**s,field:value}))
        self.assertFalse(plan.valid('coast_half','corner',44,s))
        radio=RaceRadio()
        radio.run_id='run'
        ev=radio.emit('coast_half',42,CALLS['coast_half'][2])
        radio.track_delivery(ev,plan_token='corner')
        self.assertIsNone(radio.delivery(ev['id'],'interrupted'))
        self.assertEqual(radio.delivery(ev['id'],'completed')['plan_token'],'corner')

    def test_archive_requires_specific_driver_and_layout(self):
        v={'car_path':'gt3','track_path':'park','track_config':'GP'}
        self.assertIsNone(profile_key(v,None))
        self.assertNotEqual(profile_key(v,1),profile_key(v,2))
        self.assertNotEqual(profile_key(v,1),profile_key({**v,'track_config':'short'},1))

    def test_archived_normal_passes_can_support_new_measured_lifts(self):
        archive=IbtCornerCosts()
        rows=archive.groups['driver-track'][.30]
        base=dict(fuel=20.,wet=1,compound=0,temp=30.,used=.22,time=12.,coast=.01,entry_speed=45.,
                  exit_speed=37.,clear=True,off=False,penalty=False,points=0,braking=True)
        rows.extend([dict(base,lap='historic-a'),dict(base,lap='historic-b')])
        zone=dict(key='T3',entry=.30,apex=.32,end=.34,label='T3')
        costs=archive.reference_windows('driver-track',[zone],4000)
        self.assertAlmostEqual(costs.zones[0]['start'],.26)
        sample=dict(fuel_l=20.,track_wetness=1,tire_compound=0,track_temp_c=30.)
        costs.evaluate(sample)
        self.assertFalse(costs.costs)
        for lap in (9,10):
            costs.records['T3'].append(dict(base,lap=lap,coast=.5,used=.17,time=12.2))
        costs.evaluate(sample)
        self.assertAlmostEqual(costs.costs[0][0]['liters'],.05)
