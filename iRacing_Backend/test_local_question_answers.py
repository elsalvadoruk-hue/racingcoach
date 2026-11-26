from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from local_question_answers import EXTRA_TOPICS, REPORTS, render, wordings
from local_question_intents import classify
from radio_buttons import EXAMPLE, TOPICS as BUTTON_TOPICS


def context():
    data = deepcopy(EXAMPLE)
    data['snapshot'].update(session_time_remaining_s=307, incidents=13)
    data['radio'] = {'pit_speed_limit_kph': 60}
    data['question_incident_limit'] = 17
    data['pit_wall'].update(fuel_finish_shortfall_l=2.2, fuel_next_stop_l=5.2,
                            tank_capacity_l=100, fuel_next_stop_full=False)
    data['pit_wall']['behind']['gap_at_line_s'] = 2.4
    data['question_rival_sections'] = [dict(place='4번 코너 부근', delta_s=-.4), dict(place='7번 구간', delta_s=.6)]
    return data


class ExpandedQuestionsTests(TestCase):
    def test_rival_comparisons_accept_explicit_self_without_changing_direction(self):
        for noun,side in [('앞차','ahead'),('뒤차','behind')]:
            for term,metric in [('빨라','gain'),('느려','loss')]:
                for subject in ('내가','나는','제가','저는'):
                    for text in (f'{noun}보다 {subject} 어디서 {term}?',
                                 f'{subject} {noun}보다 어느 코너에서 더 {term}요?',
                                 f'{noun}보다 어느 구간에서 {subject} {term}?'):
                        with self.subTest(text=text):
                            result=classify(text)
                            self.assertEqual(result['topic'],f'{side}_{metric}')
                            self.assertEqual(result['normalized_text'],text)
        self.assertEqual(classify('내가 앞차보다 빠른 구간 알려줘')['topic'],'ahead_gain')
        self.assertEqual(classify('뒤차보다 제가 느린 코너는요')['topic'],'behind_loss')

    def test_unclear_or_reversed_subject_is_not_a_different_rival_answer(self):
        for text in ['앞차가 나보다 어디서 빨라', '뒤차가 나보다 어디서 빨라',
                     '뒤차보다 내가 어디서 안 빨라', '앞차보다 내가 어디서 느리지 않아',
                     '앞차보다 뒤차가 어디서 빨라', '앞차보다 내가 어디서 빨라 그리고 연료',
                     '앞차보다 내가 어디서 빨라 말고 뒤차', '뒤차보다 내가 빠른 구간 알려주지 마']:
            with self.subTest(text=text):self.assertIsNone(classify(text)['topic'])

    def test_voice_only_topics_do_not_revive_retired_button_settings(self):
        self.assertFalse(set(EXTRA_TOPICS) & set(BUTTON_TOPICS))
        self.assertNotIn('time', BUTTON_TOPICS)

    def test_extended_natural_questions(self):
        cases = {
            '지금 완주까지 연료 부족해요?': 'fuel_finish',
            '연료가 끝까지 충분해?': 'fuel_finish',
            '이번 피트에 기름 몇 리터 더 넣어야 해요?': 'fuel_add',
            '레이스 몇 분 남았어요?': 'race_time',
            '피트로드 제한 속도 알려주세요': 'pit_speed',
            '인시던트 한도 알려줘': 'incident_limit',
            '내 인시던트 몇 점이야?': 'incidents',
            '뒷차한테 언제 잡혀요?': 'defend_time',
            '앞차보다 어느 구간에서 빨라요?': 'ahead_gain',
            '뒤차보다 어느 코너에서 느려요?': 'behind_loss',
            '앞차 정보 요약해줘': 'report_ahead',
            '현재 경기 상황 보고해줘': 'report_race',
        }
        for text, topic in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify(text)['topic'], topic)

    def test_no_wrong_target_or_unrequested_metric(self):
        for text in ['앞차 완주 연료 충분해', '뒤차 인시던트 몇 점이야', '4위 차 최근 랩',
                     '4위 차 연료 얼마야', '앞차 베스트 랩', '전체 선두 랩타임',
                     '연료 상황 보고하지 마', '연료 상황 보고 그리고 타이어',
                     '인시던트 초기화해', '피트 속도 제한 변경해', '앞차 정보 요약 말고 뒤차',
                     '연료 20리터로 설정해', '타이어 교체해', '뒤차보다 빠른 구간과 연료',
                     '연료 몇 리터 넣어 그리고 순위', '뒤차 온도 상태 보고']:
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])

    def test_new_metrics_use_real_fields(self):
        data = context()
        expected = {'fuel_finish':'3리터', 'fuel_add':'6리터', 'race_time':'5분',
                    'incidents':'13점', 'incident_limit':'17점', 'pit_speed':'60킬로미터',
                    'defend_time':'6랩', 'ahead_gain':'4번 코너', 'behind_loss':'7번 구간'}
        for topic, text in expected.items():
            with self.subTest(topic=topic):
                self.assertIn(text, ' '.join(t for _, t in wordings(topic, data)))

    def test_no_data_is_not_zero_and_nonfinite_is_withheld(self):
        for topic in EXTRA_TOPICS.keys() - REPORTS.keys():
            self.assertEqual(wordings(topic, {}), [])
        for value in (None, float('nan'), float('inf'), True, -1):
            data = context()
            data['pit_wall']['fuel_finish_shortfall_l'] = value
            self.assertEqual(wordings('fuel_finish', data), [])
            data['question_incident_limit'] = value
            self.assertEqual(wordings('incidents', data), [])

    def test_fuel_add_reuses_reserve_and_full_tank_policy(self):
        data = context()
        data['pit_wall'].update(fuel_next_stop_l=20.01, fuel_next_stop_longer_l=25)
        self.assertIn('21리터', wordings('fuel_add', data)[0][1])
        data['pit_wall']['fuel_next_stop_full'] = True
        self.assertIn('100리터', wordings('fuel_add', data)[0][1])
        self.assertIn('가득', wordings('fuel_add', data)[0][1])
        data['pit_wall']['tank_capacity_l'] = None
        self.assertEqual(wordings('fuel_add', data), [])
        data['pit_wall']['fuel_next_stop_l'] = 0
        self.assertIn('0리터', wordings('fuel_add', data)[0][1])

    def test_finish_pair_is_not_told_without_both_numbers(self):
        data = context()
        data['pit_wall']['fuel_finish_shortfall_l'] = 0
        self.assertEqual(len(wordings('fuel_finish', data)), 2)
        data['pit_wall']['fuel_laps'] = None
        self.assertEqual(wordings('fuel_finish', data), [])

    def test_rivals_pit_and_non_closing_are_withheld(self):
        data = context()
        data['pit_wall']['behind']['on_pit_road'] = True
        for topic in ('behind_gain', 'behind_loss', 'defend_time'):
            self.assertEqual(wordings(topic, data), [])
        data['pit_wall']['behind'].update(on_pit_road=False, gap_trend='opening')
        self.assertEqual(wordings('defend_time', data), [])

    def test_report_marks_missing_data_and_keeps_known_answers(self):
        data = context()
        data['snapshot']['session_time_remaining_s'] = None
        buttons = SimpleNamespace(coach=SimpleNamespace(), read_clip=Mock(return_value='clip'))
        result = render('report_race', data, buttons)
        self.assertIn('경기 남은 시간', result['missing_data'])
        self.assertEqual(len(result['parts']), 4)

    def test_label_without_its_value_is_never_spoken(self):
        buttons = SimpleNamespace(coach=SimpleNamespace())
        buttons.read_clip = Mock(side_effect=['label', FileNotFoundError()])
        result = render('ahead_gain', context(), buttons)
        self.assertFalse(result['parts'])
        self.assertEqual(result['missing_audio'], ['앞차보다 빠른 구간'])
        self.assertEqual(len(result['display']), 2)

    def test_finish_range_uses_the_installed_fuel_bank(self):
        data = context()
        data['pit_wall']['fuel_finish_shortfall_l'] = 0
        def read(item, role, text):
            if '7.7랩' in text:
                self.assertEqual(item, 'fuel_range')
            return 'clip'
        buttons = SimpleNamespace(coach=SimpleNamespace(), read_clip=read)
        self.assertEqual(len(render('fuel_finish', data, buttons)['parts']), 2)

    def test_reports_are_bounded_and_duplicate_sentences_are_removed(self):
        data = context()
        data['pit_wall']['fuel_finish_shortfall_l'] = 0
        buttons = SimpleNamespace(coach=SimpleNamespace(), read_clip=Mock(return_value='clip'))
        result = render('report_fuel', data, buttons)
        self.assertEqual(len(result['spoken']), len(set(result['spoken'])))
        self.assertEqual(len(result['parts']), 4)
