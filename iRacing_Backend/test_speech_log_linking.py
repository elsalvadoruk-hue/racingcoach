"""Recorded text regression and adversarial neighbours, without ASR or SDK writes."""
import unittest

from local_question_intents import classify
from pit_voice_commands import parse
from local_speech_enhancement import eligible, select


class SpeechLogLinkingTests(unittest.TestCase):
    def test_bounded_recorded_question_corrections(self):
        for text, topic, corrected in [
            ('남은 열요.', 'fuel', '연료'),
            ('현재 남은 열 요 알려줘', 'fuel', '연료'),
            ('등기 상황 보고 해 줘.', 'report_race', '경기'),
            ('지금 등기 상황 보고해주세요', 'report_race', '경기'),
        ]:
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result['topic'], topic)
                self.assertEqual(result['correction_status'], 'corrected')
                self.assertIn(corrected, result['normalized_text'])
                self.assertTrue(result['corrections'])

    def test_new_corrections_do_not_expand_to_literals_commands_or_negation(self):
        for text in ('열요', '남은 열요 말하지 마', '남은 열요 그리고 순위',
                     '앞차 남은 열요', '남은 열요 15리터 설정', '피트 남은 열요',
                     '등기 서류 상황 보고', '등기 상황 보고 하지 마',
                     '부동산 등기 상황 보고해줘', '등기 상황 보고 그리고 연료',
                     '피트 등기 상황 보고', '등기 상황 보고 15리터'):
            with self.subTest(text=text):
                result = classify(text)
                self.assertIsNone(result['topic'])
                self.assertNotEqual(result['correction_status'], 'corrected')
                self.assertIsNone(parse(text))

    def test_recent_rival_time_is_not_current_gap(self):
        for text in ('앞차 지난 몇 초야.', '앞차 지난 몇 초 나왔어.',
                     '앞차 최근 몇 초 나왔어요', '앞차 직전 몇 초 찍었어',
                     '앞차의 지난 랩은 몇 초야'):
            with self.subTest(text=text):
                self.assertEqual(classify(text)['topic'], 'ahead_lap')
        for text in ('뒤차 지난 몇 초 나왔어', '뒤차 최근 몇 초야',
                     '앞차 지난 몇 초 나왔어 간격도 알려줘',
                     '앞차 지난 랩 간격 몇 초야', '앞차 지난 몇 초 말하지 마'):
            with self.subTest(text=text):
                self.assertIsNone(classify(text)['topic'])
        for text, topic in [('앞차 간격 몇 초야', 'ahead'), ('뒤차랑 몇 초야', 'behind'),
                            ('앞차 지난 랩타임 알려줘', 'ahead_lap')]:
            self.assertEqual(classify(text)['topic'], topic)

    def test_incomplete_tire_action_requests_clarification_without_calls(self):
        for text in ('피트 레인 타이어.', '히트 웻 타이어', '키트 슬릭 타이어',
                     '피트 타이어', '피트 레인 타이어로'):
            with self.subTest(text=text):
                command = parse(text)
                self.assertIsNotNone(command)
                self.assertEqual(command['id'], 'invalid')
                self.assertTrue(command['ambiguous_action'])
                self.assertIn('다시', command['error'])
                self.assertNotIn('calls', command)
        for text in ('피트 레인 타이어?', '피트 레인 타이어 말고 슬릭',
                     '피트 레인 타이어 그리고 급유', '피트 슬리 타이어 교체',
                     '피트 급유 시보리터', '주차의 간격'):
            self.assertIsNone(parse(text))
        self.assertEqual(parse('피트 레인 타이어 교체')['compound_kind'], 'wet')
        self.assertEqual(parse('피트 레인 타이어 교체 취소')['id'], 'clear_tires')

    def test_current_pressure_has_specific_help_without_answer_or_command(self):
        for text in ('현재 타이어 암력.', '현재 타이어압력.', '타이어 공기압 지금 얼마야'):
            with self.subTest(text=text):
                result = classify(text)
                self.assertIsNone(result['topic'])
                self.assertEqual(result['reason'], 'unsupported')
                self.assertIn('실측', result['correction_note'])
                self.assertIn('지원하지', result['correction_note'])
                self.assertIsNone(parse(text))

    def test_noise_retry_cannot_erase_a_known_temporal_question_or_clarification(self):
        for raw, candidate in [('뒤차 지난 몇 초 나왔어', '뒤차 몇 초야'),
                               ('앞차 지난 랩 간격 몇 초야', '앞차 간격 몇 초야'),
                               ('현재 타이어 암력', '현재 연료 얼마나 남았어')]:
            with self.subTest(raw=raw):
                self.assertFalse(eligible(raw, 'iracing'))
                self.assertIsNone(select(raw, candidate, 'iracing'))


if __name__ == '__main__':
    unittest.main()
