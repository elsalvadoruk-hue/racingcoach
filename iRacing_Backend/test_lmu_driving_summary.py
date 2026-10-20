import unittest
from lmu_driving_summary import driving_summary


def corner(key, delta, usable=True):
    return {'id': key, 'label': key, 'delta_s': delta,
            'target': {'inputs_usable': usable}, 'reference': {'inputs_usable': usable}}


class DrivingSummaryTests(unittest.TestCase):
    def test_only_observed_positive_sections_rank_without_causal_or_best_claims(self):
        report = driving_summary([corner('A', -.5), corner('B', .35), corner('C', .6),
                                  corner('D', None), corner('E', float('nan')), corner('F', .05)])
        self.assertEqual([row['corner_ids'] for row in report['options']], [['C'], ['B']])
        self.assertEqual(report['options'][0]['focus']['total_observed_loss_s'], .6)
        self.assertEqual(report['options'][0]['focus']['causal_effect'], 'UNVERIFIED')
        self.assertEqual(report['next_run']['required_measured_laps'], 3)
        self.assertNotIn('required_valid_laps', report['next_run'])

    def test_missing_comparable_inputs_never_generates_input_change_advice(self):
        report = driving_summary([corner('A', .5, False)])
        action = report['options'][0]['focus']['action']
        self.assertIn('입력 차이는 비교하지 않습니다', action)
        self.assertNotIn('제동·가속 위치', action)

    def test_empty_or_self_comparison_has_measured_lap_plan_without_fabricated_loss(self):
        for rows in ([], [corner('A', 0)]):
            report = driving_summary(rows)
            self.assertEqual(report['options'], [])
            self.assertEqual(report['next_run']['mode'], 'MEASURE_MORE')


if __name__ == '__main__':
    unittest.main()
