"""Traffic observations use synthetic frames, temporary files and no services."""
import atexit
import gzip
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix='traffic-log-tests-')
atexit.register(_runtime.cleanup)
os.environ.setdefault('IRACING_TUNER_DB_PATH', str(Path(_runtime.name) / 'memory.sqlite3'))

from live_telemetry import CHANNELS, LiveTelemetry, session_identity
from test_lmu_irsdk import adapter, car, frame, read_all
from traffic_log import TrafficLog, read, traffic_snapshot


class Sdk(dict):
    game = 'iracing'


def fixture(count=31):
    sdk = Sdk({'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [
        {'CarIdx': i, 'CarClassID': 7, 'CarClassEstLapTime': 100., 'UserName': 'Private Driver'}
        for i in range(count)]}, 'WeekendInfo': {'TrackLength': '5 km'},
        'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': 'Race'}]},
        'CarIdxLapDistPct': [.99] + [(.99 + i * .001) % 1 for i in range(1, count)],
        'CarIdxLapCompleted': [3] * count, 'CarIdxPosition': list(range(1, count + 1)),
        'CarIdxClassPosition': list(range(1, count + 1)), 'CarIdxOnPitRoad': [False] * count,
        'CarIdxTrackSurface': [3] * count, 'CarIdxEstTime': [50.] * count})
    sample = {'session_num': 0, 'session_time': 10., 'distance_pct': .99, 'on_track': True,
              'is_replay': False, 'on_pit_road': False, 'lap_completed': 3, 'speed_mps': 50.}
    identity = {'track_name': 'Test Track', 'track_id': 'test', 'track_config': '', 'car_path': 'test-car'}
    return sdk, sample, identity


class TrafficLogTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=_runtime.name)
        self.addCleanup(self.work.cleanup)
        self.folder = Path(self.work.name) / 'traffic-logs'
        self.sdk, self.sample, self.identity = fixture()

    def snapshot(self):
        return traffic_snapshot(self.sdk, set(self.sdk), self.sample, self.identity)

    def new_log(self):
        log = TrafficLog(self.folder)
        self.addCleanup(log.close)
        return log

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2.
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_only_live_race_with_folder_is_recorded_including_pit_road(self):
        TrafficLog().record(self.snapshot(), 'run', 1.)
        log = self.new_log()
        for kind in ('Practice', 'Qualify'):
            s = self.snapshot(); s['session_type'] = kind
            log.record(s, 'run', 1.)
        for key in ('on_track', 'is_replay'):
            sample = dict(self.sample, **{key: key == 'is_replay'})
            s = {**self.snapshot(), 'sample': sample}
            log.record(s, 'run', 1.)
        log.close()
        self.assertFalse(self.folder.exists())
        log = self.new_log()
        self.sample['on_pit_road'] = True
        log.record(self.snapshot(), 'run', 1.)
        log.close()
        header, rows = read(log.path)
        self.assertEqual(header['game'], 'iracing')
        self.assertEqual(rows[0]['own']['on_pit_road'], 1)
        self.assertNotIn('Private Driver', gzip.open(log.path, 'rt', encoding='utf-8').read())

    def test_wrapped_gap_near_filter_missing_values_and_disconnected_car(self):
        self.sdk['CarIdxLapDistPct'][1:5] = [.01, .5, .98, None]
        self.sdk['CarIdxTrackSurface'][3] = -1
        log = self.new_log()
        log.record(self.snapshot(), 'run', 1.); log.close()
        _, rows = read(log.path)
        cars = {c['idx']: c for c in rows[0]['c']}
        self.assertEqual(cars[1]['gap_m'], 100.)
        self.assertEqual(cars[1]['class_id'], 7)
        self.assertIsNone(cars[1]['local_x_m'])
        for idx in (0, 2, 3, 4):
            self.assertNotIn(idx, cars)

    def test_ten_hertz_and_thirty_second_flush(self):
        log = self.new_log()
        snap = self.snapshot()
        for i in range(900):
            log.record(snap, 'run', i / 30)
        self.wait_for(lambda: log.path is not None and log.path.stat().st_size > 0)
        log.close()
        _, rows = read(log.path)
        self.assertEqual(len(rows), 300)
        self.assertTrue(all(b['t'] - a['t'] >= .099 for a, b in zip(rows, rows[1:])))

    def test_new_runs_within_one_second_have_distinct_names_and_prune_to_thirty(self):
        log = self.new_log()
        for i in range(33):
            log.record(self.snapshot(), str(i), 1.)
        log.close()
        files = list(self.folder.glob('*.gz'))
        self.assertEqual(len(files), 30)
        self.assertEqual(len({read(p)[0]['run_id'] for p in files}), 30)

    def test_lmu_local_coordinates_and_existing_spotter_result(self):
        raw = frame(others=((5, (3., 0., -4.), 0.),))
        raw['scoring_cars'][1]['lap_distance_m'] = 1004.
        sdk = read_all(*adapter([raw]), 1)
        names = set(sdk.var_headers_names)
        sample = {key: sdk[name] if name in names else None for key, name in CHANNELS.items()}
        identity = session_identity(sdk['WeekendInfo'], sdk['DriverInfo'])
        log = self.new_log()
        log.record(traffic_snapshot(sdk, names, sample, identity), 'run', 100.); log.close()
        _, rows = read(log.path)
        self.assertEqual(rows[0]['c'][0]['local_x_m'], 3.)
        self.assertEqual(rows[0]['c'][0]['local_z_m'], -4.)
        self.assertEqual(sdk['CarLeftRight'], 2)

    def test_write_error_logs_once_and_stops_only_that_run(self):
        log = self.new_log()
        with patch.object(log, '_new_file', side_effect=OSError('synthetic failure')):
            with self.assertLogs('iRacingLive', level='ERROR') as messages:
                log.record(self.snapshot(), 'run', 1.)
                log.flush()
                self.wait_for(lambda: log.failed)
                log.record(self.snapshot(), 'run', 2.)
                log.flush()
            self.assertEqual(len(messages.output), 1)
        log.record(self.snapshot(), 'next-run', 1.); log.close()
        self.assertEqual(len(read(log.path)[1]), 1)

    def test_log_exception_does_not_interrupt_live_tick_or_unfreeze(self):
        sdk, now = adapter([frame(100.), frame(100.2)])
        live = LiveTelemetry(clock=lambda: now[0])
        live.traffic_log = self.new_log()
        with patch.object(live.traffic_log, 'record', side_effect=OSError('synthetic failure')):
            with self.assertLogs('iRacingLive', level='ERROR') as messages:
                self.assertTrue(live._tick(sdk))
                now[0] += .2
                self.assertTrue(live._tick(sdk))
            self.assertEqual(len(messages.output), 1)
        self.assertEqual(len(live.samples), 2)

    def test_snapshot_and_record_31_cars_average_under_point_two_ms(self):
        log = self.new_log()
        names = set(self.sdk)
        # Include all SDK reads and detached row construction; disk is worker-only.
        start = time.perf_counter()
        for i in range(1000):
            snap = traffic_snapshot(self.sdk, names, self.sample, self.identity)
            log.record(snap, 'run', i * .1)
        elapsed_ms = (time.perf_counter() - start) * 1000 / 1000
        log.close()
        print(f'Traffic snapshot+record 31 cars: {elapsed_ms:.4f} ms average')
        self.assertLessEqual(elapsed_ms, .2)

    def test_half_second_gzip_write_does_not_hold_up_record(self):
        log = self.new_log()
        entered = threading.Event()
        original_open = gzip.open
        writer_threads = set()

        class SlowFile:
            def __init__(self, *args, **kwargs):
                self.handle = original_open(*args, **kwargs)
                self.slowed = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def write(self, value):
                writer_threads.add(threading.current_thread().name)
                if not self.slowed:
                    self.slowed = True
                    entered.set()
                    time.sleep(.5)
                return self.handle.write(value)

        snap = self.snapshot()
        with patch('traffic_log.gzip.open', SlowFile):
            log.record(snap, 'run', 0.)
            log.flush()
            self.assertTrue(entered.wait(1.))
            durations = []
            for i in range(1, 101):
                start = time.perf_counter_ns()
                log.record(snap, 'run', i * .1)
                durations.append((time.perf_counter_ns() - start) / 1e6)
            self.assertLessEqual(max(durations), 1.)
            log.close()
        self.assertEqual(writer_threads, {'traffic-log-writer'})
        self.assertEqual(len(read(log.path)[1]), 101)
        print(f'Slow gzip record maximum: {max(durations):.4f} ms')

    def test_overflow_discards_oldest_rows_counts_and_logs_only_once(self):
        log = self.new_log()
        entered, release = threading.Event(), threading.Event()
        original = log._write_rows

        def stalled(run, rows):
            entered.set()
            release.wait(2.)
            original(run, rows)

        snap = self.snapshot()
        with patch.object(log, '_write_rows', side_effect=stalled):
            log.record(snap, 'run', 0.)
            log.flush()
            self.assertTrue(entered.wait(1.))
            for i in range(1, 351):
                log.record(snap, 'run', i * .1)
            self.assertEqual(log.queued_rows, 300)
            self.assertEqual(log.dropped_rows, 50)
            with self.assertLogs('iRacingLive', level='WARNING') as messages:
                release.set()
                log.close()
            self.assertEqual(len(messages.output), 1)
        rows = read(log.path)[1]
        self.assertEqual([row['t'] for row in rows], [0.] + [round(i * .1, 3) for i in range(51, 351)])

    def test_run_switch_flushes_old_run_without_waiting_for_new_runs_end(self):
        log = self.new_log()
        snap = self.snapshot()
        log.record(snap, 'first', 1.)
        first = log._run
        log.record(snap, 'second', 1.)
        self.wait_for(lambda: first.path is not None and first.path.stat().st_size > 0)
        self.assertEqual(read(first.path)[1][0]['t'], 1.)
        log.close()
        self.assertEqual(read(log.path)[0]['run_id'], 'second')
        self.assertEqual(len(read(log.path)[1]), 1)

    def test_live_stop_flushes_pending_rows_without_a_reader_thread(self):
        live = LiveTelemetry()
        live.traffic_log = self.new_log()
        live.traffic_log.record(self.snapshot(), 'run', 1.)
        live.stop()
        self.assertFalse(live.traffic_log.worker.is_alive())
        self.assertEqual(len(read(live.traffic_log.path)[1]), 1)

    def test_close_waits_at_most_two_seconds_for_stalled_writer(self):
        log = self.new_log()
        entered, release = threading.Event(), threading.Event()
        original = log._write_rows

        def stalled(run, rows):
            entered.set()
            release.wait(5.)
            original(run, rows)

        with patch.object(log, '_write_rows', side_effect=stalled):
            log.record(self.snapshot(), 'run', 1.)
            log.flush()
            self.assertTrue(entered.wait(1.))
            start = time.monotonic()
            log.close()
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 2.2)
            self.assertTrue(log.worker.is_alive())
            release.set()
            log.worker.join(timeout=1.)
        self.assertFalse(log.worker.is_alive())
        self.assertEqual(len(read(log.path)[1]), 1)


if __name__ == '__main__':
    unittest.main()
