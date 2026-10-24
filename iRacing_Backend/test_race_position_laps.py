"""Only confirmed, per-car finish-line crossings become chart points."""
import struct
import unittest

import test_race_positions as fixtures
from timing_log import TimingLog


class RaceLapTests(unittest.TestCase):
    setUp = fixtures.RacePositionsTests.setUp
    native = fixtures.RacePositionsTests.native
    own_native = fixtures.RacePositionsTests.own_native
    history = fixtures.RacePositionsTests.history
    race = fixtures.RacePositionsTests.race
    log = fixtures.RacePositionsTests.log
    def test_confirmed_completed_laps_replace_time_axis_without_changing_ranks(self):
        result = self.history(self.native())
        self.assertEqual(result['x_axis'], 'lap')
        self.assertEqual((result['start_lap'], result['end_lap']), (1, 4))
        self.assertEqual(result['series'][0]['points'],
                         [[1, 2, 1, 102.], [2, 2, 1, 104.], [3, 1, 2, 106.], [4, 1, 2, 108.]])

    def test_each_car_uses_its_own_crossings_and_late_start_is_not_a_lap_point(self):
        telemetry = self.own_native()
        log = TimingLog(self.timing)
        for at, lap in ((103, 1), (104, 1), (105, 2), (106, 2), (107, 3)):
            race = self.race(at)
            race['cars'][2]['lap_completed'] = lap
            log.record(race, 'late', at, identity=self.identity, wall_time=1800000000 + at - 100)
        log.flush()
        result = self.history(telemetry)
        self.assertEqual(result['source'], 'IBT+LOCAL_TIMING')
        self.assertEqual(result['series'][1]['points'], [[2, 3, 2, 105.], [3, 3, 2, 107.]])

    def test_missing_lap_channel_never_relabels_seconds_as_laps(self):
        telemetry = self.own_native()
        telemetry.variables.pop('LapCompleted')
        result = self.history(telemetry)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertIn('랩', result['reason'])

    def test_array_positions_with_only_player_laps_use_verified_local_crossings_for_others(self):
        telemetry = self.native()
        from dataclasses import replace
        telemetry.variables['LapCompleted'] = replace(telemetry.variables.pop('CarIdxLapCompleted'), count=1)
        self.log()
        result = self.history(telemetry)
        self.assertEqual(result['source'], 'IBT+LOCAL_TIMING')
        self.assertEqual(result['series'][1]['points'], [[lap, 3, 2, 100. + lap * 2] for lap in range(1, 5)])

    def test_counter_jump_missing_car_and_restart_do_not_create_past_crossings(self):
        from race_positions import _observe_lap
        row = {}
        for at, lap in enumerate((8, 9, 12, None, 13, 14, 0, 1, 15)):
            _observe_lap(row, at, lap, 3, 2)
        self.assertEqual([p[0] for p in row['lap_points'] if p[2] is not None], [9, 14])
        self.assertEqual(len([p for p in row['lap_points'] if p[2] is None]), 1)

    def test_skipped_counter_and_missing_clock_break_lap_connections(self):
        telemetry = self.native()
        raw = bytearray(telemetry.path.read_bytes())
        # Drop observations around completion 3, preserving earlier history.
        for i in range(300, 420):
            struct.pack_into('<d', raw, 144 + i * telemetry.buffer_length, float('nan'))
        telemetry.path.write_bytes(raw)
        result = self.history(telemetry)
        points = result['series'][0]['points']
        self.assertEqual([p[0] for p in points if p[2] is not None], [1, 2, 4])
        self.assertTrue(any(p[2] is None for p in points[2:-1]))
