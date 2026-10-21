"""LMU's own lap verdicts: kept live, used by the recording analysis only for a matching lap.

Synthetic frames and recordings in a temporary folder; no game or personal data.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(), 'lmu-lap-verdicts.sqlite3'))

from lmu_lap_verdicts import LapVerdicts, game_verdict, verdicts_for

RECORDING_START = 1792490400.     # 2026-10-20T10:00:00Z, the synthetic recording's RecordingTime


def frame(et, completed, invalid=False, last=0., key='session-a'):
    return {'session_key': key, 'total_laps': completed, 'telemetry_et': et, 'lap_invalidated': invalid,
            'last_lap_time': last, 'track_name': 'Test Ring', 'car_name': 'Test GT3 #7', 'session_type_code': 1}


class LapVerdictTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / 'verdicts.json'
        self.now = [RECORDING_START + 5]
        self.watch = LapVerdicts(self.path, clock=lambda: self.now[0])

    def drive(self, frames):
        for item in frames:
            self.now[0] += .1
            self.watch.observe(item)

    def test_each_finished_lap_is_kept_with_its_published_time(self):
        self.drive([frame(10., 0), frame(20., 0, invalid=True), frame(41., 0)])        # lap 1 voided mid-lap
        self.drive([frame(41.5, 1, invalid=True),                                        # the old flag lingers at the line
                    frame(42., 1, last=31.416), frame(60., 1), frame(72.4, 1)])
        self.drive([frame(73., 2), frame(73.5, 2, last=31.4)])
        verdicts = verdicts_for('Test Ring', 'Test GT3 #7', '2026-10-20T10_00_00Z', self.now[0], self.path)
        self.assertEqual(verdicts, {1: (True, 31.416), 2: (False, 31.4)})

    def test_a_recorded_lap_takes_the_verdict_only_with_the_same_time(self):
        verdicts = {1: (True, 31.416), 2: (False, 31.4)}
        self.assertIs(game_verdict(verdicts, 1, 31.42), True)
        self.assertIs(game_verdict(verdicts, 0, 31.416), True)        # recordings may count the first lap 0
        self.assertIsNone(game_verdict(verdicts, 1, 31.6))            # another lap: the edge rule decides
        self.assertIsNone(game_verdict({}, 1, 31.416))

    def test_another_car_track_or_time_has_no_verdicts(self):
        self.drive([frame(10., 0), frame(41., 1, last=31.416)])
        self.watch._flush()
        self.assertEqual(verdicts_for('Test Ring', 'Other #1', '2026-10-20T10_00_00Z', self.now[0], self.path), {})
        self.assertEqual(verdicts_for('Test Ring', 'Test GT3 #7', '2026-10-21T10_00_00Z', RECORDING_START + 86400 + 60, self.path), {})


class RecordingTests(unittest.TestCase):
    def test_the_game_verdict_outranks_the_track_edge_rule(self):
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest('LMU 전용 의존성을 설치하지 않은 환경입니다.')
        from test_lmu_ibt import LAP_S, make_circuit
        from lmu_ibt import LmuIbtReader
        folder = Path(tempfile.mkdtemp())
        path = folder / 'Test Ring_P_2026-10-20T10_00_00Z.duckdb'
        make_circuit(path)                                     # lap 2 leaves the edge
        os.utime(path, (RECORDING_START + 200, RECORDING_START + 200))
        store = folder / 'verdicts.json'
        watch = LapVerdicts(store, clock=lambda: RECORDING_START + 100)
        # Live: LMU kept lap 2 (the edge rule calls it off) and voided lap 3.
        lap_time = round(LAP_S, 3)
        # Every lap the same time, so the published time never changes: it is read after the wait.
        for completed, invalid in ((2, False), (3, True)):
            watch.observe(frame(completed * 40., completed - 1, last=lap_time))
            watch.observe(frame(completed * 40. + 5, completed - 1, invalid=invalid, last=lap_time))
            watch.observe(frame(completed * 40. + 30, completed, last=lap_time))
        watch.observe(frame(3 * 40. + 40, 3, last=lap_time))
        with patch.dict(os.environ, {'IRACING_LMU_LAP_VERDICTS_PATH': str(store),
                                     'IRACING_LMU_STEERING_PATH': str(folder / 'steering.json')}):
            laps = {lap.lap_number: (lap.status, lap.invalid_reason) for lap in LmuIbtReader(path).parse().laps}
        self.assertEqual(laps[2], ('VALID', None))
        self.assertEqual(laps[3], ('INVALID', 'LMU invalidated the lap'))
        self.assertEqual(laps[1], ('VALID', None))             # no live verdict for lap 1: its own rule


if __name__ == '__main__':
    unittest.main()
