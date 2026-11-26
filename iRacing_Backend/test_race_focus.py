from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from local_question_answers import enrich, render
from local_question_intents import classify
from radio_buttons import EXAMPLE


class RaceFocusTests(unittest.TestCase):
    def setUp(self):
        self.context = deepcopy(EXAMPLE)
        self.context['snapshot'].update(run_id='run', session_time=100, session_state=4, on_track=True,
                                        is_replay=False, on_pit_road=False, session_flags=0)
        self.context['pit_wall']['ahead'].update(idx=5,on_pit_road=False,track_surface=3)
        self.context['pit_wall']['behind'].update(idx=8,on_pit_road=False,track_surface=3,gap_at_line_s=2.4)
        self.compare = Mock(return_value=[{'middle_pct':.1,'sector':0,'delta_s':-.5},
                                         {'middle_pct':.3,'sector':1,'delta_s':.4}])
        self.live = SimpleNamespace(lock=threading.RLock(), game='lmu',
                                    _current_snapshot=lambda:(None,self.context['snapshot']),
                                    rival_sectors=SimpleNamespace(compare=self.compare),
                                    pit_wall=SimpleNamespace(race={'player_idx':3}))
        self.buttons = SimpleNamespace(coach=SimpleNamespace(), read_clip=lambda *args:'audio')
        self.live.context=lambda **kw:deepcopy(self.context)

    def test_revalidation_rejects_changed_rival_even_with_same_section_words(self):
        import race_focus
        enrich('race_focus',self.context,self.live)
        signature=race_focus.signature(self.context['question_focus'])
        self.assertTrue(race_focus.current(self.live,signature))
        self.context['pit_wall']['ahead']['idx']=6
        self.assertFalse(race_focus.current(self.live,signature))

    def test_unready_saving_plan_does_not_fall_through_to_chasing(self):
        self.context['pit_wall']['race_plan']={'run_id':'run','target_call':'plan_fuel_save'}
        enrich('race_focus',self.context,self.live)
        self.assertNotIn('question_focus',self.context)
        self.compare.assert_not_called()

    def test_phrasings_and_negative_boundaries(self):
        for text in ('지금 뭐에 집중해야 해?', '현재 뭐에 집중할까', '지금 레이스 전략 알려줘',
                     '지금 공격할까 방어할까', '레이스 전략 알려주세요'):
            self.assertEqual(classify(text)['topic'],'race_focus')
        for text in ('앞차 레이스 전략 알려줘','지금 전략 알려주지 마','지금 뭐에 집중해야 해 그리고 연료'):
            self.assertIsNone(classify(text)['topic'])

    def test_chasing_uses_measured_front_car_section_and_preserves_audio_words(self):
        enrich('race_focus',self.context,self.live)
        self.assertEqual(self.context['question_focus']['key'],'chase_section')
        self.compare.assert_called_once_with(3,5)
        result=render('race_focus',self.context,self.buttons)
        self.assertIn('지금 우선할 일',result['display'][0])
        self.assertIn('추월 가능 지점을 뜻하지 않습니다',result['display'][1])
        self.assertEqual(len(result['parts']),2)
        self.assertNotIn(result['display'][0],result['spoken'])

    def test_closing_rear_car_before_finish_takes_priority(self):
        self.context['pit_wall']['remaining_laps']=8
        enrich('race_focus',self.context,self.live)
        self.assertEqual(self.context['question_focus']['key'],'defend_section')
        self.compare.assert_called_once_with(3,8)
        result=render('race_focus',self.context,self.buttons)
        self.assertEqual(len(result['parts']),3)
        self.assertIn('뒤차가 빠른 곳', ' '.join(result['spoken']))

    def test_imminent_fuel_problem_never_becomes_chasing_advice(self):
        self.context['pit_wall'].update(fuel_laps=1.5,fuel_next_stop_l=10,stops_needed=1)
        enrich('race_focus',self.context,self.live)
        self.assertEqual(self.context['question_focus']['key'],'pit_resources')
        self.compare.assert_not_called()

    def test_current_plan_wins_without_consuming_automatic_delivery(self):
        self.context['pit_wall']['race_plan']={'run_id':'run'}
        engine=SimpleNamespace(choices={'save_stop':('token',100,'정차 한 번 절약')},
                               valid=Mock(return_value=True),announced={},
                               call=lambda *args:('race_coast','strategy','절약하면 정차를 줄일 수 있습니다.','절약하면 정차 줄어듦.'))
        self.live.race_plan=engine;self.live.clock=lambda:100
        enrich('race_focus',self.context,self.live)
        self.assertEqual(self.context['question_focus']['key'],'save_stop')
        self.compare.assert_not_called();self.assertEqual(engine.announced,{})

    def test_unknown_or_unsafe_context_withholds_conclusion(self):
        for changes in ({'session_state':5},{'on_pit_road':True},{'is_replay':True},
                        {'session_flags':0x8},{'lmu_scoring_held':True}):
            context=deepcopy(self.context);context['snapshot'].update(changes)
            self.live._current_snapshot=lambda:(None,context['snapshot'])
            enrich('race_focus',context,self.live)
            self.assertNotIn('question_focus',context)
        self.compare.assert_not_called()

    def test_no_sector_evidence_does_not_claim_an_overtaking_spot(self):
        self.compare.return_value=[]
        enrich('race_focus',self.context,self.live)
        self.assertNotIn('question_focus',self.context)
        self.assertFalse(render('race_focus',self.context,self.buttons)['parts'])
