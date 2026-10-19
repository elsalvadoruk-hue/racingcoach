"""Serialization contracts for the measured-analysis response; no stores or cloud clients."""
import json
import math
import unittest
import numpy as np
from analysis_numeric import json_safe


class JsonResponseTests(unittest.TestCase):
    def test_nested_measurements_remain_json_serializable_without_losing_missing_values(self):
        value = {1: {'trace': np.array([[1.25, np.nan], [np.inf, -np.inf]]),
                     'lap': np.int64(3), 'valid': np.bool_(True), 'missing': None},
                 'tuple': (np.float32(2.5), False, '관측값')}
        result = json_safe(value)
        expected = {'1': {'trace': [[1.25, None], [None, None]], 'lap': 3, 'valid': True, 'missing': None},
                    'tuple': [2.5, False, '관측값']}
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), expected)

    def test_scalar_subclasses_keep_the_previous_conversion_rules(self):
        class DriverLabel(str):
            pass
        class MeasuredFloat(float):
            pass
        label = DriverLabel('드라이버')
        self.assertIs(json_safe(label), label)
        self.assertEqual(json_safe(MeasuredFloat(1.25)), 1.25)
        self.assertIsNone(json_safe(MeasuredFloat(math.inf)))
        self.assertIs(type(json_safe(np.str_('측정'))), str)
        self.assertIs(type(json_safe(np.float64(1.25))), float)


if __name__ == '__main__':
    unittest.main()
