"""Reject unusable driving references before publishing or drawing them."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from garage61_reader import Garage61ReferenceReader
from overlay_reference_quality import check_csv, check_lap


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)/'lap.csv'
        self.rows = [[50., i/1000, .2, .8, 0., 3] for i in range(1001)]

    def tearDown(self):
        self.folder.cleanup()

    def read(self):
        self.path.write_text('Speed,LapDistPct,Brake,Throttle,SteeringWheelAngle,PositionType\n'+
                             '\n'.join(','.join(map(str, row)) for row in self.rows))
        check_csv(self.path)
        return Garage61ReferenceReader(self.path, metadata_lookup=lambda _: {'lap_time_s': 100.}).parse()

    def test_full_flying_lap_and_small_export_tail_are_accepted(self):
        self.rows += [[50., .0001, .2, .8, 0., 3], [50., .001, .2, .8, 0., 3]]
        quality = check_lap(self.read(), 5000)
        self.assertAlmostEqual(quality['travel_distance_ratio'], 1)

    def test_raw_missing_pedals_offtrack_and_backwards_position_are_rejected(self):
        for column, value in ((2, np.nan), (3, 2), (5, 2), (1, .1)):
            with self.subTest(column=column):
                row = self.rows[500][:]
                self.rows[500][column] = value
                with self.assertRaises(ValueError):
                    self.read()
                self.rows[500] = row

    def test_stationary_rows_removed_by_parser_do_not_hide_a_time_gap(self):
        for row in self.rows[880:980]:
            row[1] = self.rows[879][1]
            row[0] = 1
        with self.assertRaisesRegex(ValueError, '단절'):
            check_lap(self.read(), 5000)

    def test_partial_lap_and_long_sample_gap_are_rejected(self):
        lap = self.read()
        lap.distance_pct = .02+.98*lap.distance_pct
        with self.assertRaisesRegex(ValueError, '시작·끝'):
            check_lap(lap, 5000)
        lap = self.read()
        lap.sample_time_s[500:] += 1
        with self.assertRaisesRegex(ValueError, '단절'):
            check_lap(lap, 5000)

    def test_low_speed_and_inconsistent_speed_distance_are_rejected(self):
        lap = self.read()
        lap.channels['Speed'][500:520] = 2
        with self.assertRaisesRegex(ValueError, '저속'):
            check_lap(lap, 5000)
        lap = self.read()
        lap.channels['Speed'] *= .5
        with self.assertRaisesRegex(ValueError, '불일치'):
            check_lap(lap, 5000)


if __name__ == '__main__':
    unittest.main()
