"""Exact scalar/batch equivalence on recorded-sample boundary cases."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from corner_events import RawLap
import handling


def raw_wave(index, count=721):
    time = np.arange(count) / 60.
    pct = np.linspace(0., 1., count)
    speed = 27. + .2 * np.sin(pct * 7)
    steer = .1 + .002 * np.sin(pct * 9)
    yaw = .05 * steer * speed * np.exp((index - 4) * .003)
    if index == 0:
        yaw[(pct > .3) & (pct < .5)] *= .65
    if index == 1:
        yaw[(pct > .55) & (pct < .7)] *= 1.3
    if index == 2:
        steer[(pct > .6) & (pct < .7)] *= -1.
    return RawLap(time, pct, {'Speed': speed, 'SteeringWheelAngle': steer,
                  'YawRate': yaw, 'LatAccel': np.full(count, 36.),
                  'Brake': np.full(count, .02), 'Throttle': np.full(count, .4)},
                  1 / 60, 3000.)


def prepare(raws, *, varying_bounds=False, missing_lag=False):
    model = {'edges_mps': np.array([10., 15., 20., 40.])}
    if missing_lag:
        aligned = {}
        for raw in raws:
            values = raw.channels['SteeringWheelAngle'].copy()
            values[200:215] = np.nan
            aligned[id(raw)] = values
        model['_aligned_steer'] = aligned
    phases = [handling.prepare_phase(raw, .1 + (index % 3) * .003 if varying_bounds else .1,
                                    .9 - (index % 2) * .004 if varying_bounds else .9, model)
              for index, raw in enumerate(raws)]
    return phases, model


class HandlingBatchTests(unittest.TestCase):
    def assert_scalar_equal(self, phases, model):
        expected = [handling.classify_phase(phase, phases, model) for phase in phases]
        actual = list(handling._classify_phases(phases, model))
        # Exact equality includes b/z medians and every sustained-event z sample.
        self.assertEqual(actual, expected)
        return actual

    def test_original_samples_events_and_statistics_are_bit_exact(self):
        phases, model = prepare([raw_wave(index, 721 + index * 2) for index in range(8)],
                                varying_bounds=True)
        result = self.assert_scalar_equal(phases, model)
        self.assertTrue(any(row['observed'] for row in result))
        kinds = {event['kind'] for row in result for event in row['events']}
        self.assertEqual(kinds, {'understeer', 'oversteer', 'countersteer'})

    def test_gaps_overlapping_positions_missing_channels_and_lag_stay_unknown(self):
        raws = [raw_wave(index, 721 + index * 2) for index in range(9)]
        time = raws[3].time_s.copy()
        time[250:] += 1.
        raws[3] = replace(raws[3], time_s=time)
        pct = raws[4].pct.copy()
        pct[400:] -= .09  # Repeated physical positions in separate forward spans.
        raws[4] = replace(raws[4], pct=pct)
        raws[5].channels['Throttle'][350:370] = np.nan
        raws[6].channels['LatAccel'][300:450] *= 1.5
        raws[7] = replace(raws[7], channels={key: value for key, value in raws[7].channels.items()
                                           if key != 'Brake'})
        phases, model = prepare(raws, varying_bounds=True, missing_lag=True)
        result = self.assert_scalar_equal(phases, model)
        self.assertTrue(any('phase_incomplete' in row['unknown_reasons'] for row in result))
        self.assertEqual(result[7]['unknown_reasons'], ['phase_unavailable'])

    def test_self_references_are_excluded_even_when_repeated(self):
        phases, model = prepare([raw_wave(0)])
        self.assert_scalar_equal(phases * 5 + [None], model)
        self.assertFalse(list(handling._classify_phases(phases * 5, model))[0]['observed'])

    def test_missing_speed_bands_and_empty_phases_match_scalar(self):
        phases, model = prepare([raw_wave(index) for index in range(6)])
        model['edges_mps'][:] = np.nan
        self.assert_scalar_equal(phases, model)
        self.assert_scalar_equal([None, None], model)
        self.assertEqual(list(handling._classify_phases([], model)), [])

    def test_chunking_reuses_reference_interpolation_and_never_mutates_inputs(self):
        phases, model = prepare([raw_wave(index) for index in range(8)])
        originals = [(phase['q'].copy(), phase['support'].copy(),
                      {key: values.copy() for key, values in phase['local'].channels.items()})
                     for phase in phases]
        points_per_phase = len(phases[0]['q'])
        budget = points_per_phase * len(phases) * 2
        with patch.object(handling, '_REFERENCE_BATCH_VALUES', budget):
            with patch.object(handling, '_reference_at', wraps=handling._reference_at) as interpolate:
                actual = list(handling._classify_phases(phases, model))
            self.assertEqual(interpolate.call_count, 4 * len(phases))
            self.assertTrue(all(len(call.args[1]) * len(phases) <= budget
                                for call in interpolate.call_args_list))
        expected = [handling.classify_phase(phase, phases, model) for phase in phases]
        self.assertEqual(actual, expected)
        for phase, (q, support, channels) in zip(phases, originals):
            np.testing.assert_array_equal(phase['q'], q)
            np.testing.assert_array_equal(phase['support'], support)
            for key, values in channels.items():
                np.testing.assert_array_equal(phase['local'].channels[key], values)

    def test_one_long_phase_exceeding_batch_budget_is_not_resampled(self):
        phases, model = prepare([raw_wave(index) for index in range(6)])
        with patch.object(handling, '_REFERENCE_BATCH_VALUES', 1):
            self.assert_scalar_equal(phases, model)

    def test_noise_is_borrowed_only_from_the_same_targets_phase(self):
        raws = [raw_wave(index) for index in range(8)]
        for raw in raws[5:]:
            raw.channels['Throttle'][(raw.pct > .35) & (raw.pct < .45)] = 1.
        phases, model = prepare(raws)
        result = self.assert_scalar_equal(phases, model)
        self.assertTrue(result[0]['observed'])
        self.assertEqual(result[0]['known_fraction'], 1.)
        self.assertTrue(any(event['kind'] == 'understeer' for event in result[0]['events']))

    def test_median_implementation_boundary_preserves_exact_values(self):
        phases, model = prepare([raw_wave(index) for index in range(8)])
        # NumPy switches its nanmedian strategy at 600 rows. Keeping a self
        # row as NaN crosses that boundary without changing the finite values.
        references = [phases[0], *(phases[1:] * 86)[:599]]
        expected = handling.classify_phase(phases[0], references, model)
        actual = list(handling._classify_phase_batch([phases[0]], references, model))
        self.assertEqual(actual, [expected])


if __name__ == '__main__':
    unittest.main()
