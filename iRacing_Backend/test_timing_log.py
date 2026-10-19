"""Race timing log: written only in a race with a folder, no names, one file per run, read back."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys

import timing_log
from timing_log import TimingLog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import read_timing_log  # noqa: E402


def race(t, ahead_f2=10., own_f2=12., state=4, session_type='Race', own_pct=.5):
    # car 1 runs 2 s ahead of the player (car 0) at 50 m/s on a 5 km lap: 2 s = 0.02 lap
    return {'session_type': session_type, 'state': state, 'player_idx': 0, 'track_name': 'Road America', 'track_length_m': 5000.,
            'cars': [{'idx': 0, 'name': 'Me', 'class_id': 7, 'class_name': 'GT3', 'class_est_lap_s': 100., 'lap_completed': 3,
                      'distance_pct': own_pct, 'f2_time_s': own_f2, 'est_time_s': own_pct * 100, 'on_pit_road': False, 'class_position': 2, 'position': 2, 'last_lap_s': 100.},
                     {'idx': 1, 'name': 'Someone', 'class_id': 7, 'class_name': 'GT3', 'class_est_lap_s': 100., 'lap_completed': 3,
                      'distance_pct': own_pct + .02, 'f2_time_s': ahead_f2, 'est_time_s': (own_pct + .02) * 100, 'on_pit_road': False,
                      'class_position': 1, 'position': 1, 'last_lap_s': 100.}]}


class TimingLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name) / 'timing-logs'

    def tearDown(self):
        self.dir.cleanup()

    def test_nothing_is_written_without_a_folder_or_outside_a_race(self):
        TimingLog().record(race(1.), 'run', 1.)
        log = TimingLog(self.folder)
        log.record(race(1., session_type='Practice'), 'run', 1.)
        log.flush()
        self.assertFalse(self.folder.exists())

    def test_a_race_is_written_once_a_second_without_names_and_read_back(self):
        log = TimingLog(self.folder)
        for t in range(40):
            log.record(race(float(t)), 'run', float(t))
        log.record(race(39.), 'run', 39.)                    # same second twice: once
        log.flush()
        files = list(self.folder.glob('timing-*.jsonl.gz'))
        self.assertEqual(len(files), 1)
        header, rows = timing_log.read(files[0])
        self.assertEqual(header['track'], 'Road America')
        self.assertEqual(header['classes']['7']['name'], 'GT3')
        self.assertEqual(len(rows), 40)
        self.assertEqual(rows[0]['c'][1]['f2_time_s'], 10.)
        self.assertEqual(rows[0]['c'][0]['on_pit_road'], 0)
        import gzip
        self.assertNotIn('Someone', gzip.open(files[0], 'rt', encoding='utf-8').read())

    def test_a_new_run_starts_a_new_file_and_old_files_are_pruned(self):
        log = TimingLog(self.folder)
        for n in range(timing_log.KEEP_FILES + 3):
            log.record(race(1.), f'run{n}', 1.)
            log.path = log.path.with_name(f'timing-{n:03d}.jsonl.gz')   # distinct names within one second
        log.flush()
        self.assertLessEqual(len(list(self.folder.glob('timing-*.jsonl.gz'))), timing_log.KEEP_FILES)

    def test_the_reader_compares_f2_and_the_app_estimate_with_the_measured_gap(self):
        log = TimingLog(self.folder)
        for t in range(60):
            # both cars move 0.01 lap a second and the car ahead is 0.02 lap on: 2 s ahead, F2 says 2 s
            log.record(race(float(t), ahead_f2=10., own_f2=12., own_pct=.1 + .01 * t), 'run', float(t))
        log.flush()
        out = io.StringIO()
        with redirect_stdout(out):
            read_timing_log.main([str(next(self.folder.glob('*.gz'))), '--csv', str(Path(self.dir.name) / 'gaps.csv')])
        text = out.getvalue()
        self.assertIn('그린 상태(4) 초: 60', text)
        self.assertIn('[3]', text)
        rows = (Path(self.dir.name) / 'gaps.csv').read_text(encoding='utf-8').splitlines()
        measured = [line.split(',') for line in rows[1:] if line.split(',')[4]]
        self.assertTrue(measured)
        self.assertAlmostEqual(float(measured[-1][4]), 2., places=1)
        self.assertAlmostEqual(float(measured[-1][5]), 2., places=3)


if __name__ == '__main__':
    unittest.main()
