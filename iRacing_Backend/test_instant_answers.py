import unittest
from types import SimpleNamespace

import radio_phrases
from instant_answers import instant_answer
from live_sectors import LapSectors


def view(**updates):
    return {'active': True, 'remaining_laps': 16.,
            'ahead': {'idx': 0, 'gap_estimate_s': 2.4, 'on_pit_road': False},
            'behind': {'idx': 2, 'gap_estimate_s': 1.1, 'on_pit_road': False}, **updates}


def context(pit_wall=None, **snapshot):
    return {'pit_wall': pit_wall if pit_wall is not None else view(),
            'snapshot': {'class_position': 5, 'session_time_remaining_s': 45 * 60., 'lap_last_s': 482.2, 'lap_best_s': 476.9, **snapshot}}


class InstantAnswerTests(unittest.TestCase):
    def test_gaps_are_answered_from_prepared_sentences(self):
        self.assertEqual(instant_answer('앞차랑 간격 얼마야?', context()), ('race', ['앞차와 2.4초 차.']))
        self.assertEqual(instant_answer('뒤차 간격은?', context()), ('race', ['뒤차와 1.1초 차.']))
        self.assertEqual(instant_answer('간격 알려줘', context()), ('race', ['앞차와 2.4초 차.', '뒤차와 1.1초 차.']))
        far = view(ahead={'idx': 0, 'gap_estimate_s': 42., 'on_pit_road': False})
        self.assertEqual(instant_answer('앞차 간격', context(far)), ('race', ['앞차까지 42초입니다.']))

    def test_place_time_and_lap_time(self):
        self.assertEqual(instant_answer('지금 몇 위야?', context()), ('spotter', ['현재 클래스 5위입니다.']))
        self.assertEqual(instant_answer('남은 시간 얼마야', context()), ('spotter', ['45분 남았습니다.']))
        self.assertEqual(instant_answer('방금 랩타임 뭐야', context()), ('spotter', ['8분 2초 2입니다.']))
        self.assertEqual(instant_answer('베스트 랩타임은?', context()), ('spotter', ['세션 베스트입니다. 7분 56초 9입니다.']))

    def test_catching_the_car_ahead(self):
        closing = view(ahead={'idx': 0, 'gap_estimate_s': 2.4, 'gap_trend': 'closing', 'gap_change_s_per_lap': -.3, 'gap_at_line_s': 2.4, 'on_pit_road': False})
        self.assertEqual(instant_answer('앞차 따라잡을 수 있어?', context(closing)),
                         ('race', ['앞차와 2.4초 차.', '지금은 랩당 0.3초씩 줄이고 있습니다.', '8랩 뒤에 닿습니다.']))
        steady = view(ahead={'idx': 0, 'gap_estimate_s': 8., 'on_pit_road': False})
        self.assertEqual(instant_answer('앞차 따라잡으려면?', context(steady))[1][1], '끝까지 닿으려면 랩당 0.5초 빨라야 합니다.')

    def test_theoretical_best_needs_a_clean_lap(self):
        live = SimpleNamespace(lap_sectors=LapSectors())
        self.assertIsNone(instant_answer('이론상 베스트 얼마야', context(), live))
        live.lap_sectors.best_lap_s, live.lap_sectors.best_sectors = 100., [4.97] * 20
        self.assertEqual(instant_answer('이론상 베스트 얼마야', context(), live),
                         ('spotter', ['이론상 베스트입니다.', '1분 39초 4입니다.', '지금 베스트보다 0.6초 빠릅니다.']))

    def test_what_is_not_measured_takes_the_normal_path(self):
        idle = {'active': False}
        self.assertIsNone(instant_answer('앞차랑 간격 얼마야?', context(idle)))                 # no race data
        self.assertIsNone(instant_answer('3위 차량이랑 간격은?', context()))                      # a specific car: the longer answer
        self.assertIsNone(instant_answer('이 코너에서 왜 앞이 밀려?', context()))
        self.assertIsNone(instant_answer('지금 몇 위야?', context(class_position=None)))
        unknown = view(ahead=None, behind=None)
        self.assertIsNone(instant_answer('간격 알려줘', context(unknown)))

    def test_every_sentence_is_a_prepared_one(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        closing = view(ahead={'idx': 0, 'gap_estimate_s': 2.4, 'gap_trend': 'closing', 'gap_change_s_per_lap': -.3, 'gap_at_line_s': 2.4, 'on_pit_road': False})
        for question, ctx in (('간격 알려줘', context()), ('지금 몇 위야?', context()), ('앞차 따라잡을 수 있어?', context(closing)),
                              ('남은 시간 얼마야', context(session_time_remaining_s=10 * 60.))):
            for sentence in instant_answer(question, ctx)[1]:
                self.assertIn(sentence, prepared, sentence)


if __name__ == '__main__':
    unittest.main()
