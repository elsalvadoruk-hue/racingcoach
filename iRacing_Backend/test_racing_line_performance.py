"""Batched alignment preserves scalar projection and observed-window rules."""
import unittest

import numpy as np

from racing_line import _alignment_samples, _project, _section


def _scalar_alignment(target, reference, positions):
    kept, offsets = [], []
    for position in positions:
        point = _section(target, position, position)
        if point is None:
            continue
        offset = _project(reference, position, point[1][0])
        if offset is not None:
            kept.append(position)
            offsets.append(abs(offset))
    return np.asarray(kept), np.asarray(offsets)


def _path(axis, shift=0):
    return (axis, np.column_stack([axis + np.sin(axis / 9), np.cos(axis / 13) + shift]),
            np.ones(len(axis), dtype=bool))


class RacingLineBatchTests(unittest.TestCase):
    def assert_matches_scalar(self, target, reference, positions):
        expected = _scalar_alignment(target, reference, positions)
        actual = _alignment_samples(target, reference, positions)
        for before, after in zip(expected, actual):
            np.testing.assert_array_equal(after, before)

    def test_exact_offsets_for_curves_and_independent_irregular_axes(self):
        rng = np.random.default_rng(731)
        for _ in range(12):
            reference = _path(np.cumsum(rng.uniform(.1, 8, 121)))
            target = _path(np.cumsum(rng.uniform(.1, 8, 133)), shift=.7)
            self.assert_matches_scalar(target, reference, reference[0])

    def test_missing_points_large_gaps_and_degenerate_segments(self):
        axis = np.arange(201, dtype=float)
        for source in ('target', 'reference'):
            for kind in ('coverage', 'gap', 'nan', 'degenerate'):
                with self.subTest(source=source, kind=kind):
                    target, reference = _path(axis.copy(), .6), _path(axis.copy())
                    path = target if source == 'target' else reference
                    if kind == 'coverage':
                        path[2][[0, 15, 103, 200]] = False
                    elif kind == 'gap':
                        path[0][120:] += 25
                    elif kind == 'nan':
                        path[1][33:36] = np.nan
                        path[2][33:36] = False
                    else:
                        path[1][45:95] = path[1][45]
                    self.assert_matches_scalar(target, reference, reference[0])

    def test_batch_boundary_dense_and_short_windows_preserve_first_segment_ties(self):
        for axis in (np.arange(2001, dtype=float) / 10,
                     np.linspace(0, .01, 1501), np.array([0., 20., 40., 60., 80.])):
            with self.subTest(points=len(axis)):
                target, reference = _path(axis.copy(), .5), _path(axis.copy())
                self.assert_matches_scalar(target, reference, reference[0])

    def test_no_supported_samples_or_empty_selection(self):
        target, reference = _path(np.arange(51, dtype=float)), _path(np.arange(51, dtype=float))
        target[2][:] = False
        self.assert_matches_scalar(target, reference, reference[0])
        self.assert_matches_scalar(target, reference, np.empty(0))
        target[2][:] = True
        reference[1][:] = 0
        self.assert_matches_scalar(target, reference, reference[0])


if __name__ == '__main__':
    unittest.main()
