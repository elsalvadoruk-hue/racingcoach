"""Natural question language and adjacent meanings, with no audio or SDK calls."""
import unittest

from local_question_intents import classify


CASES = {
    'fuel_finish': [
        '연료 괜찮아?', '기름은 괜찮겠어?', '야 기름 이걸로 끝까지 가냐?',
        '지금 남은 기름으로 완주 가능해?', '기름 더 안 넣고 버틸 수 있어?',
        '이대로 타면 기름 모자라는 거 아냐?', '연료 충분한 거 맞지?',
        '완주할 때까지 남은 연료로 갈 수 있겠어?', '연료가 부족하지는 않을까?',
        '지금 기름으로 끝까지 버틸 수 있나요?', '기름 더 넣을 필요 없어?',
    ],
    'fuel_add': [
        '끝까지 가려면 몇 리터 더 필요해?', '완주하려면 연료 얼마나 더 있어야 돼?',
        '피트에서 기름 몇 리터 넣으면 돼?', '추가로 필요한 급유량 좀 알려줄래?',
        '다음 피트에서 얼마나 급유해야 해?', '완주까지 부족한 연료가 몇 리터야?',
    ],
    'fuel_range': [
        '남은 기름으로 몇 바퀴 더 버틸 수 있냐?', '현재 연료로 몇 랩이나 갈 수 있어?',
        '기름 떨어지기까지 몇 바퀴야?', '연료 다 쓰려면 몇 랩 남았는데?',
    ],
    'fuel_average': [
        '요즘 한 바퀴에 기름 얼마나 쓰고 있어?', '연료가 랩마다 얼마나 나가?',
        '랩당 기름 소비가 어느 정도인데?', '한 바퀴 돌 때 연료 몇 리터 써?',
    ],
    'fuel': ['남아 있는 기름이 몇 리터인데?', '지금 연료 잔량을 알려 주실래요?', '기름 얼마나 들었냐?'],
    'closing_behind': [
        '뒤에서 따라오는 차 얼마나 빨라?', '뒤차가 나보다 얼마나 빠른데?',
        '앞차 말고 뒤차가 나보다 얼마나 빠른데?', '뒤차는 나보다 랩당 몇 초 빨라?',
        '뒤차랑 간격이 한 바퀴에 얼마나 줄어?',
    ],
    'defend_time': [
        '뒤차한테 잡히겠어?', '뒤차가 언제쯤 붙겠어?', '뒤차가 몇 바퀴 뒤에 나를 잡아?',
        '뒤에서 오는 차가 결승 전에 따라붙겠어?', '뒤차가 나 따라잡으려면 몇 랩 걸려?',
    ],
    'catch_ahead': ['앞에서 달리는 차를 따라잡을 수 있겠어?', '앞차한테 붙으려면 몇 랩 걸려?', '내가 앞차를 잡을 수 있냐?'],
    'ahead': ['앞에서 달리는 차랑 지금 몇 초 떨어져 있어?', '앞차까지 얼마나 벌어졌는데?', '뒤차 말고 앞차 간격 알려줘'],
    'behind': ['뒤에서 따라오는 차랑 몇 초 차이 나?', '뒤차가 얼마나 가까이 왔는데?', '앞차가 아니라 뒤차 간격 알려줘'],
    'ahead_lap': ['앞차의 직전 랩 기록 좀 알려 주실래요?', '앞에서 달리는 차 방금 한 바퀴 몇 초 나왔어?'],
    'last': ['나 방금 한 바퀴 몇 초 찍은 거야?', '저기 내 직전 랩 기록 좀 알려 줄래요?', '지난 랩 내 기록이 얼마였는데?'],
    'best': ['오늘 내가 제일 잘 탄 랩이 몇 초야?', '이번 세션 내 최고 기록 좀 알려줘'],
    'position': ['나 지금 몇 번째로 달리고 있어?', '현재 내 클래스에서 몇 위인데?'],
    'laps': ['결승까지 몇 바퀴 더 돌아야 돼?', '이 경기 끝나려면 몇 랩 남았는데?'],
    'race_time': ['경기 끝나려면 몇 분 더 있어야 해?', '체커까지 시간이 얼마나 남았어?'],
    'pit_rejoin': ['지금 피트 들어갔다 나오면 몇 등이겠어?', '지금 피트하면 복귀 순위가 어떻게 돼?'],
    'pit_transit': ['피트 한 번 통과하는 데 몇 초 걸려?', '피트 들어갔다 나오면서 잃는 시간은 얼마야?'],
    'pit_speed': ['피트레인에서 몇 킬로까지 달려도 돼?', '피트 속도 제한이 어떻게 되는데?'],
    'incidents': ['내 인시던트 지금 몇 점 쌓였어?', '인시던트 여유가 몇 점이야?'],
    'incident_limit': ['인시던트 몇 점까지 허용돼?', '인시던트 몇 점에서 실격인데?'],
    'report_ahead': ['앞차 상황이 어때?', '앞차 정보 좀 정리해서 알려줘'],
    'report_behind': ['뒤차 어때?', '뒤차 상황을 간단하게 보고해줘'],
    'report_race': ['지금 레이스 상황 좀 정리해줘', '경기 상황이 어떻게 돌아가고 있어?'],
    'report_pit': ['피트 전략 좀 정리해서 알려줘', '지금 피트 상황 종합해서 말해줘'],
    'race_focus': ['지금 뭘 신경 써야 돼?', '지금 공격하는 게 좋아 방어하는 게 좋아?', '이번 랩 뭐부터 챙겨야 해?'],
    'fuel_save': ['아껴서 피트 안 하고 갈 수는 없어?', '연료 아끼면 끝까지 가능해?', '완주하려면 랩당 얼마나 써야 해?'],
    'corner_repeat': ['같은 코너에서 계속 잃어?', '지난 랩 손실이 반복되고 있어?', '손실이 이번 랩만 그런 거야?'],
}


class QuestionLanguageTests(unittest.TestCase):
    def test_colloquial_questions_retain_the_requested_quantity(self):
        for topic, phrases in CASES.items():
            for text in phrases:
                with self.subTest(text=text, topic=topic):
                    self.assertEqual(classify(text)['topic'], topic)

    def test_wrappers_do_not_require_exact_registered_sentences(self):
        for prefix in ('', '야 ', '저기요, ', '혹시 ', '있잖아 ', '제발 ', '좀 ', '야', '코치야', '어, 저기요 '):
            for question, topic in [('지금 남은 기름으로 완주 가능해', 'fuel_finish'),
                                    ('뒤에서 따라오는 차 얼마나 빨라', 'closing_behind'),
                                    ('피트에서 기름 몇 리터 넣으면 돼', 'fuel_add')]:
                for ending in ('?', '요?', ' 좀 알려줘', ' 좀 알려 주실래요', ' 알려 주시겠어요', ' 알려 주실 수 있을까요'):
                    text = prefix + question + ending
                    with self.subTest(text=text):
                        self.assertEqual(classify(text)['topic'], topic)

    def test_missing_context_and_unsupported_quantities_are_not_filled_in(self):
        for text in ('이걸로 끝까지 가?', '얼마나 남았어?', '그럼 뒤차는?', '왜?', '응',
                     '연료 얼마 넣을까 아니 타이어 압력 알려줘',
                     '앞차 연료 괜찮아?', '뒤차 기름 몇 리터 더 필요해?',
                     '선두 연료 끝까지 되겠어?', '완주 연료 목표',
                     '연료 압력 괜찮아?', '연료 온도 괜찮아?',
                     '현재 타이어 공기압 몇이야?', '뒤차 어제 랩 기록',
                     '기름 더 넣지 마', '연료 15리터 더 넣어', '기름 더 안 넣어',
                     '앞차와 뒤차 간격 알려줘', '앞차 말고 뒤차 말고 선두 간격',
                     '뒤차가 아닌 앞차보다 내가 얼마나 느려?',
                     '뒤차보다 내가 얼마나 빠른데?',
                     '뒤차가 나보다 얼마나 느린데?',
                     '앞차 타이어 마모 몇 퍼센트야?', '앞차 베스트 기록 얼마야?',
                     '지난 랩 뒤차 간격이 몇 초였는데?', '브레이크를 왜 못 밟아?',
                     '연료 괜찮아 그리고 타이어는?', '야 연료 괜찮아 말하지 마',
                     '연료 괜찮다고 말하지 마', '연료 충분한 게 아니고 뒤차 간격',
                     '기름 말고 에너지', '어디선가 연료 얘기를 들었어'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])

    def test_explanation_and_length_are_response_modes_not_other_topics(self):
        for text, topic, style in [('연료 괜찮아 짧게 말해줘', 'fuel_finish', 'short'),
                                   ('뒤차 상황 자세히 알려줘', 'report_behind', 'explain')]:
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result['topic'], topic)
                self.assertEqual(result.get('answer_detail'), style)

    def test_section_comparisons_compose_target_subject_place_and_metric(self):
        for noun, side in [('앞차', 'ahead'), ('뒤차', 'behind')]:
            for subject in ('내가', '나는', '제가', '저는'):
                for place in ('어디에서', '어느 코너에서', '어떤 구간에서'):
                    for metric, phrase in [('gain', '시간을 벌고 있어'), ('loss', '손해 보고 있어')]:
                        for text in (f'{subject} {noun}에 비해서 {place} {phrase}?',
                                     f'{noun}보다 {subject} {place} {phrase}?',
                                     f'{place} {subject} {noun}보다 {phrase}?'):
                            with self.subTest(text=text):
                                self.assertEqual(classify(text)['topic'], f'{side}_{metric}')

    def test_composition_never_reverses_who_is_catching_whom(self):
        for text in ('앞차가 나를 잡을 수 있어?', '앞차가 날 따라잡겠어?',
                     '내가 뒤차보다 랩당 얼마나 빨라?', '뒤차가 나보다 랩당 몇 초 느려?',
                     '뒤차보다 내가 어느 코너에서 안 빨라?',
                     '앞차보다 뒤차가 어디에서 빨라?',
                     '내가 앞차에 비해서 어떤 구간에서 손해 보고 있어 그리고 연료',
                     '앞차보다 어디에서 시간을 벌고 있어 말하지 마'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])

    def test_questions_with_specific_numbers_are_not_general_current_answers(self):
        for text in ('연료 15리터 더 넣어', '연료 15리터 넣으면 끝까지 가?',
                     '피트 2번 하면 몇 등이야?', '3번 코너에서 앞차보다 얼마나 느려?',
                     '뒤차가 5초 뒤면 언제 잡혀?', '1위 말고 4위 차 간격 알려줘'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])

    def test_reviewed_direction_and_historical_fallbacks_do_not_change_meaning(self):
        for text in ('앞차가 언제 날 따라잡겠어?', '내가 뒤차한테 몇 초씩 좁히고 있어?',
                     '어제 내 베스트 얼마였어?', '지난 경기 내 순위 몇 등?',
                     '지난주 연료 몇 리터 남았어?', '남은 연료 말해주지 마'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])

    def test_independent_reviewed_boundary_words_are_not_discarded(self):
        for text in ('앞차가 저를 몇 랩 뒤에 잡아?', '앞차 잡지 마',
                     '피트 두 번 하면 몇 위로 나와?', '연료가 몇 리터 남았는지 안 알려줘'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])

    def test_noise_retry_cannot_drop_newly_understood_boundary_words(self):
        from local_speech_enhancement import eligible, select
        for raw, candidate in [('앞차가 저를 몇 랩 뒤에 잡아?', '앞차 잡을 수 있어'),
                               ('앞차 잡지 마', '앞차 잡을 수 있어'),
                               ('어제 내 베스트 얼마였어?', '내 베스트 얼마야'),
                               ('지난 경기 내 순위 몇 등?', '내 순위 몇 등'),
                               ('연료가 몇 리터 남았는지 안 알려줘', '연료 몇 리터 남았어')]:
            with self.subTest(text=raw):
                self.assertFalse(eligible(raw, 'iracing'))
                self.assertIsNone(select(raw, candidate, 'iracing'))

    def test_reviewed_unregistered_language_variations(self):
        for text, topic in [
            ('연료 좀 충분하겠냐?', 'fuel_finish'),
            ('기름 얼마나 더 넣어야 완주하냐?', 'fuel_add'),
            ('이만큼 기름 갖고 끝까지 갈 수 있어?', 'fuel_finish'),
            ('레이스 끝나기까지 몇 분 남았니?', 'race_time'),
            ('나 앞차보다 어느 구간에서 시간 벌고 있냐?', 'ahead_gain'),
            ('뒤에서 쫓아오는 차 나보다 얼마나 빨라?', 'closing_behind'),
            ('뒤차 언제 정도 붙을 것 같아?', 'defend_time'),
            ('앞차 쫓아갈 만해?', 'catch_ahead'),
            ('피트 한 번 들렀다 오는 데 시간 얼마나 버려?', 'pit_transit'),
        ]:
            with self.subTest(text=text):
                self.assertEqual(classify(text)['topic'], topic)


if __name__ == '__main__':
    unittest.main()
