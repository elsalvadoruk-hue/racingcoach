import unittest

from fuel_defence import FuelDefence
from race_strategy_clips import clauses, defence_words, feedback_words


class FuelDefenceTests(unittest.TestCase):
    def setUp(self):
        self.unit=FuelDefence()
        self.s=dict(session_time=0.,lap_number=8,session_state=4,session_flags=0,on_pit_road=False,
                    is_replay=False,engine_warnings=0)
        self.pit=dict(active=True,game='lmu',class_position=9,behind=dict(idx=2,class_position=10,on_pit_road=False),
                      pace_s=100.,remaining_laps=4.11,fuel_save_mode='finish',planned_stop_before_finish=False,
                      finish_may_extend=False,finish_may_shorten=False,fuel_observed_laps=3,energy_laps=4.27,
                      fuel_save_target_l_per_lap=3.044,fuel_goal_save_pct=1.2,fuel_save_feasible=True)
        self.advance(40)

    def advance(self,end,gap=1.41,rival=2):
        start=int(self.s['session_time'])
        for t in range(start,end+1):
            cars=[]
            for idx,rank,pos in ((1,9,7+t/100),(rival,10,7+(t-gap)/100)):
                cars.append(dict(idx=idx,class_id=2,class_position=rank,lap_completed=int(pos),distance_pct=pos%1,
                                 on_pit_road=False,track_surface=3))
            self.s['session_time']=t
            self.unit.observe(dict(cars=cars,player_idx=1),self.s,4000)

    def pick(self,feedback=(),floor=0,known=True):
        return self.unit.choice(self.s,self.pit,feedback,floor,known,self.s['session_time'])

    def test_measured_threshold_and_delivery(self):
        key,token,_=self.pick()
        self.assertEqual(key,'fuel_defence_target')
        self.assertEqual(self.unit.words(key,token),defence_words(304,4))
        self.assertTrue(self.unit.valid(key,token,self.s))
        self.assertEqual(self.pick()[:2],(key,token))
        self.unit.delivered(key,token)
        key,token,_=self.pick()
        self.assertEqual(key,'fuel_defence')
        self.assertEqual(self.unit.words(key,token),defence_words(None,4))
        self.assertNotIn('리터',self.unit.words(key,token)[0])
        self.unit.delivered(key,token)
        self.assertIsNone(self.pick())
        self.s['lap_number']=9
        history=[dict(lap=8,target_l=3.044,use_l=3.02,mode='finish')]
        key,token,_=self.pick(history)
        self.assertEqual(self.unit.words(key,token),feedback_words(True,False))
        self.unit.delivered(key,token)
        self.assertIsNone(self.pick(history))

    def test_plan_energy_and_extra_lap(self):
        self.assertIsNone(self.pick(floor=None))
        self.assertIsNone(self.pick(floor=1))
        self.assertIsNone(self.pick(known=False))
        for field,val in (('energy_laps',4),('finish_may_extend',True),('finish_may_shorten',True),
                          ('planned_stop_before_finish',True),('fuel_observed_laps',1)):
            with self.subTest(field=field):
                old=self.pit[field]
                self.pit[field]=val
                self.assertIsNone(self.pick())
                self.pit[field]=old

    def test_changing_rival_and_pressure_cancel_queued_words(self):
        key,token,_=self.pick()
        self.s['engine_warnings']=2
        self.assertFalse(self.unit.valid(key,token,self.s))
        self.s['engine_warnings']=0
        self.advance(41,rival=3)
        self.assertFalse(self.unit.valid(key,token,self.s))
        self.assertIsNone(self.pick())

    def test_uncertainty_cannot_turn_point_three_into_a_warning(self):
        self.unit.marks[1][71] += .4
        self.assertIsNone(self.pick())

    def test_followup_targets_one_completed_lap_and_stable_change(self):
        key,token,_=self.pick()
        self.unit.delivered(key,token)
        key,token,_=self.pick()
        self.unit.delivered(key,token)
        self.s['lap_number']=9
        history=[dict(lap=8,target_l=3.044,use_l=3.12,mode='finish')]
        for mark in self.unit.marks[1]:
            self.unit.marks[1][mark] -= .4
        key,token,_=self.pick(history)
        self.assertEqual(self.unit.words(key,token),feedback_words(False,True))
        self.unit.delivered(key,token)
        self.assertIsNone(self.pick(history))

    def test_both_styles_use_whole_sentences_for_unseen_values(self):
        for target,gap in ((304,4),(215,9),(1,1),(3000,320),(None,4)):
            for text in defence_words(target,gap):
                parts=clauses('strategy',text)
                self.assertIsInstance(parts,tuple)
                self.assertEqual(parts,(text,))
                self.assertNotIn(',',text)
                if target is None:
                    self.assertIn(f'{gap/10:g}초씩',text)
                else:
                    self.assertNotIn('뒤차',text)
        self.assertIsNone(clauses('strategy','다른 연료 안내'))

    def test_target_completion_cannot_force_a_changed_rival_warning(self):
        key,token,_=self.pick()
        self.unit.delivered(key,token)
        self.advance(41,rival=3)
        self.assertIsNone(self.pick())
        self.unit.delivered('fuel_defence',token)
        self.assertIsNone(self.unit.initial)

    def test_changed_fuel_target_starts_a_new_delivery_sequence(self):
        key,target_token,_=self.pick()
        self.unit.delivered(key,target_token)
        key,warning_token,_=self.pick()
        self.unit.delivered(key,warning_token)
        self.pit['fuel_save_target_l_per_lap']=3.12
        key,new_target_token,_=self.pick()
        self.assertEqual(key,'fuel_defence_target')
        self.assertEqual(self.unit.words(key,new_target_token),defence_words(312,None))
        self.assertFalse(self.unit.valid('fuel_defence',warning_token,self.s))
        self.unit.delivered('fuel_defence',warning_token)
        self.assertIsNone(self.unit.initial)
        self.unit.delivered(key,new_target_token)
        key,new_warning_token,_=self.pick()
        self.assertEqual(key,'fuel_defence')
        self.assertEqual(self.unit.words(key,new_warning_token),defence_words(None,4))
