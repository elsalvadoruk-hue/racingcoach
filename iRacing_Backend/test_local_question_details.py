from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from live_corners import LiveCornerCoach
from local_question_answers import enrich, render, wordings
from local_question_details import catching, corner_repetition, fuel_saving
from local_question_audio import PROMPT_KEYS, fixed_catalog, wording
from radio_buttons import EXAMPLE

SHORT_LINES = {key: text for key, role, style, text in fixed_catalog()
               if style == 'short' and key not in PROMPT_KEYS}

def sample():
    context = deepcopy(EXAMPLE)
    context['game'] = 'iracing'
    context['snapshot'].update(run_id='run', session_time=500, fuel_l=23, lap_number=7)
    context['fuel_avg_l_per_lap'] = 2.5
    context['pit_wall'].update(fuel_finish_shortfall_l=2, fuel_next_stop_l=3.25,
                               fuel_reserve_l=1.25, tank_capacity_l=100, fuel_next_stop_full=False,
                               fuel_target_l_per_lap=2.3, remaining_laps=10, finish_laps_to_go=10,
                               fuel_l_per_lap=2.5, mandatory_stops_remaining=0)
    context['pit_wall']['behind'].update(idx=8, gap_at_line_s=4, gap_change_s_per_lap=-.5,
                                        gap_trend='closing', on_pit_road=False, track_surface=3)
    context['pit_wall']['ahead'].update(idx=5, gap_at_line_s=6, gap_change_s_per_lap=-.2,
                                       gap_trend='closing', on_pit_road=False, track_surface=3)
    context['corner_coach']['last_lap']['losses'][0]['id'] = 'C13'
    context['question_corner_history'] = [
        {'lap': lap, 'corners': [{'id': 'C13', 'delta_s': delta}, {'id': 'C7', 'delta_s': 1}]}
        for lap, delta in ((4, .2), (5, .3), (6, .9))]
    return context


def buttons(read=None):
    return SimpleNamespace(coach=SimpleNamespace(), read_clip=read or Mock(return_value='clip'))


class RichQuestionAnswersTests(unittest.TestCase):
    def test_shortage_and_recommended_fill_are_distinct_without_adding_reserve_twice(self):
        result = render('fuel_finish', sample(), buttons())
        self.assertEqual(len(result['spoken']), 2)
        self.assertIn('2리터', result['spoken'][0])
        self.assertIn('4리터', result['spoken'][1])
        self.assertEqual(result['answer_evidence']['fuel_next_stop_l'], 3.25)

    def test_missing_recommended_audio_does_not_silence_known_shortage(self):
        result = render('fuel_finish', sample(), buttons(Mock(side_effect=['short', FileNotFoundError()])))
        self.assertEqual(result['parts'], ['short'])
        self.assertEqual(len(result['spoken']), 1)
        self.assertIn('이번 피트 추가 급유량', result['missing_audio'])
        self.assertEqual(len(result['display']), 2)

    def test_missing_fill_data_does_not_invent_a_recommendation(self):
        context = sample()
        context['pit_wall']['fuel_next_stop_l'] = None
        result = render('fuel_finish', context, buttons())
        self.assertEqual(len(result['spoken']), 1)
        self.assertIn('2리터', result['spoken'][0])

    def test_catching_and_defending_answers_include_race_distance(self):
        for topic, laps in (('catch_ahead', 30), ('defend_time', 8)):
            with self.subTest(topic=topic):
                result = render(topic, sample(), buttons())
                self.assertIn(f'{laps}랩', ' '.join(result['spoken']))
                self.assertIn('10랩', ' '.join(result['spoken']))
                self.assertEqual(result['answer_evidence']['before_finish'], topic == 'defend_time')
                self.assertNotIn(result['explanation'][0], result['spoken'])

    def test_short_catch_answer_preserves_prediction_and_remaining_distance(self):
        result = render('catch_ahead', sample(), buttons(), detail='short')
        self.assertEqual(len(result['spoken']), 2)
        self.assertIn('30랩', result['spoken'][0])
        self.assertIn('10랩', result['spoken'][1])

    def test_explain_does_not_fabricate_unknown_remaining_distance(self):
        context = sample()
        context['pit_wall'].update(remaining_laps=None, finish_laps_to_go=None)
        context['snapshot']['session_laps_remaining'] = None
        result = render('defend_time', context, buttons(), detail='explain')
        self.assertIsNone(result['answer_evidence']['before_finish'])
        self.assertNotIn(SHORT_LINES['defend_within'], result['spoken'])
        self.assertNotIn(SHORT_LINES['defend_after'], result['spoken'])

    def test_explain_uses_gap_and_rate_and_preserves_spoken_display_difference(self):
        result = render('defend_time', sample(), buttons(), detail='explain')
        self.assertIn('0.5초', ' '.join(result['spoken']))
        self.assertIn(SHORT_LINES['defend_within'], result['spoken'])
        self.assertEqual(result['answer_evidence']['rival_idx'], 8)

    def test_missing_explanation_audio_is_not_logged_as_spoken(self):
        def read(item, role, text):
            if text in SHORT_LINES.values():
                raise FileNotFoundError()
            return 'clip'
        result = render('defend_time', sample(), buttons(read), detail='explain')
        self.assertIn(SHORT_LINES['defend_within'], result['display'])
        self.assertNotIn(SHORT_LINES['defend_within'], result['spoken'])
        self.assertTrue(result['missing_audio'])

    def test_fuel_saving_has_target_and_actual_average_but_never_guarantees_achievability(self):
        result = render('fuel_save', sample(), buttons())
        self.assertIn('2.3리터', result['spoken'][0])
        self.assertIn('2.5리터', result['spoken'][1])
        self.assertEqual(result['answer_evidence']['feasibility'], 'not_guaranteed')
        self.assertIn(SHORT_LINES['fuel_target_only'], result['display'])

    def test_target_is_floored_without_increasing_a_marginal_fuel_goal(self):
        context = sample()
        context['pit_wall']['fuel_target_l_per_lap'] = 2.299
        self.assertEqual(fuel_saving(context)['target_l_per_lap'], 2.29)
        for value in (None, -1, True, float('nan'), float('inf'), 31):
            context['pit_wall']['fuel_target_l_per_lap'] = value
            self.assertEqual(wordings('fuel_save', context), [])

    def test_mandatory_stop_and_energy_deficit_do_not_become_no_stop_claim(self):
        context = sample()
        context['game'] = 'lmu'
        context['pit_wall'].update(mandatory_stops_remaining=1, energy_finish_shortfall_pct=5)
        result = render('fuel_save', context, buttons())
        self.assertIn(SHORT_LINES['fuel_stop_required'], result['spoken'])
        self.assertIn(SHORT_LINES['fuel_energy_short'], result['spoken'])
        self.assertNotIn('피트 한 번 줄이는', ' '.join(result['spoken']))

    def test_missing_stop_caveat_audio_withholds_the_entire_fuel_goal_even_in_short_mode(self):
        context = sample()
        context['pit_wall']['mandatory_stops_remaining'] = 1
        for detail in ('default', 'short'):
            expected = wording('fuel_stop_required', 'short')[1]
            def read(item, role, text):
                if text == expected:
                    raise FileNotFoundError()
                return 'clip'
            result = render('fuel_save', context, buttons(read), detail=detail)
            self.assertFalse(result['parts'])
            self.assertFalse(result['spoken'])
            self.assertIn(expected, result['display'])
            self.assertTrue(result['missing_audio'])

    def test_fuel_history_filters_stale_incomplete_and_invalid_observations(self):
        context = sample()
        context['fuel_feedback'] = [dict(lap=6, at=400, use_l=2.2), dict(lap=5, at=600, use_l=2.1),
                                    dict(lap=2, at=100, use_l=2.0)]
        facts = fuel_saving(context)
        self.assertEqual(facts['observed_laps'], [dict(lap=6, use_l=2.2, met_current_target=True)])
        self.assertEqual(facts['feasibility'], 'not_guaranteed')

    def test_corner_baseline_is_clean_segment_best_not_a_whole_best_lap(self):
        result = render('corner_loss', sample(), buttons(), detail='explain')
        self.assertIn(SHORT_LINES['corner_basis'], result['spoken'])
        self.assertEqual(result['answer_evidence']['basis'], 'own_clean_segment_best_at_each_lap')

    def test_repetition_distinguishes_three_two_and_one_loss(self):
        for values, key in [((.2, .3, .9), 'corner_repeat_all'),
                            ((0, .3, .9), 'corner_repeat_some'),
                            ((0, -.1, .9), 'corner_repeat_once')]:
            with self.subTest(values=values):
                context = sample()
                for row, delta in zip(context['question_corner_history'], values):
                    row['corners'][0]['delta_s'] = delta
                result = render('corner_repeat', context, buttons())
                self.assertIn(SHORT_LINES[key], result['spoken'])
                self.assertEqual(result['answer_evidence']['compared_laps'], 3)

    def test_missing_corner_record_is_unknown_instead_of_no_loss(self):
        context = sample()
        context['question_corner_history'][0]['corners'] = [{'id': 'C7', 'delta_s': 1}]
        result = render('corner_repeat', context, buttons())
        self.assertFalse(result['parts'])
        self.assertIn('최근 코너 손실 반복 여부', result['missing_data'])

    def test_stale_paused_or_wrong_corner_history_never_answers_repetition(self):
        for change in ('stale', 'paused', 'wrong', 'duplicate'):
            context = sample()
            if change == 'stale':
                context['snapshot']['lap_number'] += 1
            elif change == 'paused':
                context['corner_coach']['comparison_skipped_reason'] = 'paused'
            elif change == 'wrong':
                context['question_corner_history'][2]['corners'][0]['id'] = 'C7'
            else:
                context['question_corner_history'][0]['lap'] = 5
            with self.subTest(change=change):
                self.assertEqual(corner_repetition(context), {})

    def test_pitting_opponent_is_not_forecast_as_catchable(self):
        context = sample()
        context['pit_wall']['ahead']['on_pit_road'] = True
        self.assertFalse(catching(context, 'ahead'))

    def test_history_is_copied_only_for_requested_topic_and_same_session(self):
        context = sample()
        history = deepcopy(context['question_corner_history'])
        live = SimpleNamespace(lock=threading.RLock(), game='iracing',
                               _current_snapshot=lambda: (0, context['snapshot']),
                               corners=SimpleNamespace(question_history=history))
        enrich('corner_repeat', context, live)
        history[0]['corners'][0]['delta_s'] = 99
        self.assertEqual(context['question_corner_history'][0]['corners'][0]['delta_s'], .2)
        fresh = sample()
        fresh['snapshot']['run_id'] = 'other'
        fresh.pop('question_corner_history')
        enrich('corner_repeat', fresh, live)
        self.assertNotIn('question_corner_history', fresh)

    def test_focus_explanation_compares_front_and_rear_prospects(self):
        context = sample()
        context['question_focus'] = dict(key='defend_section', basis='rival_sector_medians',
                                         label='defend', reason='measured',
                                         normal=[('race', 'focus')], short=[('race', 'focus')])
        result = render('race_focus', context, buttons(), detail='explain')
        self.assertIn('30랩', ' '.join(result['spoken']))
        self.assertIn('8랩', ' '.join(result['spoken']))
        self.assertIn('10랩', ' '.join(result['spoken']))
        self.assertFalse(result['answer_evidence']['ahead']['before_finish'])
        self.assertTrue(result['answer_evidence']['behind']['before_finish'])

    def test_focus_explanation_keeps_fuel_and_rejoin_reasons_distinct(self):
        for key, expected in [('pit_resources', '2리터'), ('pit_now_gap', '14위')]:
            context = sample()
            context['question_focus'] = dict(key=key, basis='current_race_plan',
                                             label='priority', reason='measured',
                                             normal=[('strategy', 'focus')], short=[('strategy', 'focus')])
            result = render('race_focus', context, buttons(), detail='explain')
            self.assertIn(expected, ' '.join(result['spoken']))
            self.assertNotIn('30랩', ' '.join(result['spoken']))
            self.assertNotIn('8랩', ' '.join(result['spoken']))


class CornerQuestionHistoryTests(unittest.TestCase):
    def test_history_is_bounded_and_reset_clears_all_old_context(self):
        coach = LiveCornerCoach()
        coach.key = ('car', 'track')
        reference = dict(id='C1', call='1번 코너', apex_pct=.2, duration_s=10,
                         min_speed_mps=20, brake_pct=None, throttle_pct=None,
                         entry_pct=.1, clean=True, traffic_context='SOLO')
        coach.best[coach.key] = {'C1': reference}
        for lap in range(1, 9):
            coach.lap = {'C1': dict(reference, duration_s=10.2)}
            coach._lap_finished(lap, 1000)
        self.assertEqual(len(coach.question_history), 5)
        self.assertEqual([row['lap'] for row in coach.question_history], [4, 5, 6, 7, 8])
        self.assertEqual(coach.question_history[-1]['corners'], [{'id': 'C1', 'delta_s': .2}])
        self.assertNotIn('question_history', coach.view())
        coach.reset()
        self.assertEqual(list(coach.question_history), [])

    def test_nonclean_pass_is_absent_not_a_zero_loss(self):
        coach = LiveCornerCoach()
        coach.key = ('car', 'track')
        coach.lap = {'C1': {'id': 'C1', 'clean': False, 'traffic_context': 'SOLO'}}
        coach._lap_finished(1, 1000)
        self.assertEqual(list(coach.question_history), [{'lap': 1, 'corners': []}])


if __name__ == '__main__':
    unittest.main()
