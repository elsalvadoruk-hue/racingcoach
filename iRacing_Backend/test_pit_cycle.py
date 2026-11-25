import unittest
from types import SimpleNamespace

from pit_cycle import PitCycles


def frames(*, reverse=False):
    for t in range(26):
        cars = []
        for idx, start, end in [(0, 4, 15), (9, 7, 18)]:
            initial = 100 if idx == 0 else 94
            if reverse:
                initial = 194 - initial
            final = 130 if idx == 0 else 136
            if reverse:
                final = 266 - final
            position = initial if t < start else initial + (final-initial)*min(1, max(0, (t-start-4)/6))
            pit = start <= t < end
            behind = (idx == 9) != reverse
            rank = 2 if (behind if t < 15 else not behind) else 1
            cars.append(dict(idx=idx, class_id=1, class_position=rank, distance_pct=position/1000,
                             lap_completed=2, on_pit_road=pit, track_surface=2 if pit else 3))
        yield t, dict(player_idx=0, track_length_m=1000, state=4, session_type='Race', cars=cars)


class PitCycleTests(unittest.TestCase):
    def test_both_return_four_consistent_updates_and_delivery(self):
        for reverse, direction in [(False, 'ahead'), (True, 'behind')]:
            tracker = PitCycles()
            for t, race in frames(reverse=reverse):
                tracker.update(race,t)
                if t != 22:
                    self.assertIsNone(tracker.choice)
                    continue
                key, token, _ = tracker.choice
                self.assertEqual(key,'pit_cycle_'+direction)
                self.assertEqual(token,(4,9,2 if reverse else 1))
                self.assertTrue(tracker.valid(key,token,{'session_time':t}))
                self.assertFalse(tracker.valid(key,token,{'session_time':t+1.66}))
                self.assertFalse(tracker.valid(key,token,{'session_time':t,'tow_time_s':1}))
                tracker.delivered(token)
                self.assertFalse(tracker.valid(key,token,{'session_time':t}))

    def test_interruptions_repeated_pits_and_late_rank_change(self):
        for change in ['missing','different car','pace car','no stop','jump','held scoring','time gap',
                       'reenter','no position change','one pit','missing rank','stale scoring','new run']:
            with self.subTest(change=change):
                tracker=PitCycles()
                for t,race in frames():
                    car=race['cars'][1]
                    if change=='missing' and t==13:
                        race['cars'].pop()
                    if change=='different car' and t>=13:
                        car['idx']=10
                    if change=='pace car':
                        car['is_pace_car']=True
                    if change=='no stop' and 7<=t<18:
                        car['distance_pct']+=.004*(t-7)
                    if change=='jump' and t==10:
                        car['distance_pct']+=.3
                    if change=='held scoring' and t==12:
                        race['position_source']='HELD'
                    if change=='time gap' and t==10:
                        continue
                    if change=='reenter' and t in (19,20):
                        car.update(on_pit_road=True,track_surface=2)
                    if change=='no position change' and t>=15:
                        car['class_position'],race['cars'][0]['class_position']=2,1
                    if change=='one pit':
                        car.update(track_surface=3,on_pit_road=False)
                    if change=='missing rank' and t==20:
                        car['class_position']=None
                    if change=='stale scoring' and t>=10:
                        race['scoring_sample_time']=10
                    if change=='new run' and t==17:
                        tracker=PitCycles()
                    tracker.update(race,t)
                    self.assertIsNone(tracker.choice)

    def test_rank_change_after_confirmation_invalidates_queued_call(self):
        tracker=PitCycles()
        for t,race in frames():
            if t==23:
                race['cars'][1]['class_position']=3
            tracker.update(race,t)
            if t==22:
                key,token,_=tracker.choice
            if t>=23:
                self.assertFalse(tracker.valid(key,token,{'session_time':t}))
                self.assertIsNone(tracker.choice)

    def test_finish_line_during_a_stop_must_match_completed_lap(self):
        for valid in (True,False):
            tracker=PitCycles()
            for t,race in frames():
                for car in race['cars']:
                    car['distance_pct']+=.89
                    if car['distance_pct']>=1:
                        car['distance_pct']-=1
                        if valid:
                            car['lap_completed']+=1
                tracker.update(race,t)
            self.assertEqual(tracker.choice is not None,valid)

    def test_race_plan_dispatches_actual_rank_after_local_yellow_and_cancels_stale_audio(self):
        from race_call_audio import selected_race_event
        from race_engineer import RaceEngineer
        from race_plan import RacePlan
        from race_radio import RaceRadio
        from race_strategy_clips import pit_cycle_words
        from voice_alerts import category
        plan, radio = RacePlan(), RaceRadio()
        live = SimpleNamespace(radio=radio, game='iracing', run_id='cycle', corners=SimpleNamespace(definitions=[]),
                               energy=SimpleNamespace(last_counted_at=None, history=[]), fuel_feedback=[],
                               fuel_history=[], race_options={}, plan_options={}, pit_wall=RaceEngineer(),
                               _pit_view=lambda *args, **kwargs: {})
        radio.run_id='cycle'
        radio.plan_valid=lambda item, now:plan.valid(*item, now, radio.latest)
        for t,race in frames(reverse=True):
            me=race['cars'][0]
            s=dict(session_time=t, lap_number=3, distance_pct=me['distance_pct'], speed_mps=35, brake=0,
                   steering_rad=0.,workload_in_s=20, on_track=True,is_replay=False, car_left_right=1,
                   on_pit_road=me['on_pit_road'], track_surface=me['track_surface'], session_state=4,
                   session_flags=8 if t==11 else 0)
            radio.metadata,radio.latest,radio.last_sample=race,s,float(t)
            plan.ingest(live,s,race,float(t))
            if t==22:
                event=next(e for e in radio.pending if e['key']=='pit_cycle_behind')
                self.assertEqual(event['speech_text'],pit_cycle_words('behind',2)[0])
                packs=SimpleNamespace(is_builtin=lambda _:True,selections={'strategy':'short'})
                self.assertEqual(selected_race_event(event,packs)['speech_text'],pit_cycle_words('behind',2)[1])
                self.assertEqual(event['alert_categories'],[category('pit_cycle_behind')])
                self.assertTrue(event['prepared_only'])
                self.assertTrue(radio.valid(event,t))
                self.assertIsNone(radio.delivery(event['id'],'failed'))
                receipt=radio.delivery(event['id'],'completed')
                plan.delivered(receipt['key'],receipt['plan_token'],t)
                self.assertFalse(radio.valid(event,t))
        self.assertEqual(sum(e['key']=='pit_cycle_behind' for e in radio.pending),1)


class StrategyPriorityTests(unittest.TestCase):
    def test_fuel_defence_yields_to_actual_box_requirement_and_energy_or_pressure_warning(self):
        from race_engineer import RaceEngineer
        from race_plan import RacePlan
        from race_radio import RaceRadio
        for name, ready, overrides, warnings, expected in (
            ('finish saving possible',True,{},0,True),
            ('missing finish choice clip',False,{},0,False),
            ('outside saving range',True,{'fuel_save_pct':12},0,False),
            ('energy low',True,{'energy_laps':.5},0,False),
            ('pressure low',True,{},2,False),
        ):
            with self.subTest(name=name):
                plan,radio=RacePlan(),RaceRadio()
                facts=dict(active=True,game='lmu',class_position=9,
                           behind=dict(idx=2,class_position=10,on_pit_road=False), pace_s=100,
                           fuel_laps=1.1,fuel_save_pct=5,remaining_laps=1.5,fuel_finish_shortfall_l=.2,
                           fuel_save_mode='finish',fuel_save_feasible=True,fuel_observed_laps=3,
                           planned_stop_before_finish=False,mandatory_stops_remaining=0,
                           energy_laps=2.,fuel_goal_save_pct=5,fuel_save_target_l_per_lap=3.04,
                           finish_may_extend=False,finish_may_shorten=False)
                facts.update(overrides)
                radio.run_id='one'
                live=SimpleNamespace(run_id='one',game='lmu',radio=radio,pit_wall=RaceEngineer(),
                                     race_options={}, plan_options={'run':'one','mandatory':0},
                                     _pit_view=lambda *a, **kw:facts, energy=SimpleNamespace(last_counted_at=None),
                                     corners=SimpleNamespace(definitions=[]),fuel_feedback=[],fuel_history=[],
                                     finish_save_audio_ready=lambda _:ready)
                for t in range(42):
                    own=7+t/100
                    other=own-.0141
                    cars=[dict(idx=idx, class_position=rank, class_id=1, on_pit_road=False,
                               track_surface=3, lap_completed=int(pos),distance_pct=pos%1)
                          for idx,rank,pos in [(1,9,own),(2,10,other)]]
                    race=dict(cars=cars,player_idx=1,track_length_m=4000,state=4,session_type='Race')
                    s=dict(on_track=True,on_pit_road=False,session_state=4,session_flags=0,
                           is_replay=False,lap_number=8,distance_pct=own%1,session_time=t,
                           speed_mps=40,workload_in_s=18,steering_rad=0,brake=0,car_left_right=1,
                           track_surface=3,engine_warnings=warnings)
                    radio.metadata,radio.latest,radio.last_sample=race,s,t
                    plan.ingest(live,s,race,t)
                self.assertEqual('fuel_defence_target' in plan.choices, expected)
