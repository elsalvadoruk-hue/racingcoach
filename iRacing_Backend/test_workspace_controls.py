"""Native intervention regressions with synthetic arrays; no files or services."""
from types import SimpleNamespace
import unittest
import numpy as np
from workspace_controls import control_signal, comparison_controls, lap_controls


class ControlEventsTests(unittest.TestCase):
    def signal(self, values, distance=None, times=None, **kwargs):
        n = len(values)
        return control_signal(np.arange(n) * .001 if distance is None else distance,
                              np.arange(n) / 60 if times is None else times,
                              values, 5000, 'BrakeABSactive', **kwargs)

    def test_single_sample_pulses_survive_without_chart_downsampling(self):
        signal = self.signal([0, 1, 0, 0])
        self.assertEqual(signal['events'], [[.001, .002, .016667]])
        self.assertEqual(signal['coverage'], [[0., .003]])

    def test_missing_unknown_and_observed_inactive_are_distinct(self):
        self.assertFalse(self.signal([np.nan, np.nan])['available'])
        self.assertEqual(self.signal([0, 0])['events'], [])
        self.assertTrue(self.signal([0, 0])['available'])
        self.assertFalse(self.signal([.2, .5])['available'])

    def test_gaps_unknown_samples_and_reversed_distance_are_not_filled(self):
        signal = self.signal([1, 1, np.nan, 1, 1], distance=[0, .001, .002, .004, .003], times=[0, .8, .9, 1, 1.1])
        self.assertTrue(all(event[0] == event[1] and event[2] == 0 for event in signal['events']))

    def test_spatial_gaps_and_unclean_portions_are_not_interventions(self):
        signal = self.signal([1, 1, 1], distance=[0, .02, .021], allowed=[True, True, False])
        self.assertEqual(signal['events'], [[0., 0., 0.], [.02, .02, 0.]])

    def telemetry(self, channels):
        return SimpleNamespace(channels={'LapDistPct': np.array([.1, .101, .102]),
            'SessionTime': np.array([0, .1, .2]), **channels}, variables={}, metadata={'track_length_m': 1000})

    def test_tc_channels_are_not_loaded_or_returned(self):
        telemetry = self.telemetry({'dcTractionControl': np.ones(3) * 4, 'dcTractionControlToggle': np.ones(3)})
        telemetry.variables = {'TCActive': object(), 'TractionControlActive': object()}
        lap = SimpleNamespace(lap_number=1, start_idx=0, end_idx=2, valid=True)
        # No API reader is supplied: requesting either optional TC channel fails.
        result = lap_controls(None, telemetry, lap)
        self.assertEqual(set(result), {'abs'})
        self.assertFalse(result['abs']['available'])
        self.assertEqual(set(comparison_controls(None, telemetry, lap, lap)), {'abs'})

    def test_abs_cut_signal_is_supported(self):
        telemetry = self.telemetry({'BrakeABScutPct': np.array([0, .05, 0])})
        lap = SimpleNamespace(lap_number=1, start_idx=0, end_idx=2, valid=True)
        result = lap_controls(None, telemetry, lap)
        self.assertEqual(result['abs']['events'], [[.101, .102, .1]])
        self.assertEqual(result['abs']['source'], 'BrakeABScutPct')

    def test_aggregate_reference_is_not_a_representative_laps_flags(self):
        telemetry = self.telemetry({'BrakeABSactive': np.ones(3)})
        lap = SimpleNamespace(lap_number=1, start_idx=0, end_idx=2, valid=True)
        result = comparison_controls(None, telemetry, lap, lap, aggregate=True)
        self.assertEqual(result['abs']['reference']['reason'], 'AGGREGATE_REFERENCE')
        self.assertFalse(result['abs']['reference']['available'])


if __name__ == '__main__':
    unittest.main()
