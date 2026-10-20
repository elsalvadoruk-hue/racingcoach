import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lmu_live import LmuLiveService, get_live_service, live_status


class Failure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('PRIVATE_NAME C:/private/setup SteamID=123')


class Source:
    def __init__(self, fail_open=None):
        self.items = queue.Queue()
        self.opened = self.closed = 0
        self.fail_open = fail_open

    def open(self):
        self.opened += 1
        if self.fail_open:
            raise Failure(self.fail_open)

    def read(self, timeout_ms=200):
        try:
            value = self.items.get(timeout=.02)
        except queue.Empty:
            return None
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed += 1


def snapshot(et=10):
    return {'session_id': 'C:/private/SteamID=123', 'vehicle_id': 2, 'telemetry_et': et,
            'telemetry_updated': True, 'scoring_updated': True,
            'scoring_et': et, 'lap_start_et': 0., 'scoring_lap_start_et': 0.,
            'total_laps': 0, 'last_lap_time': 0., 'lap_invalidated': False,
            'count_lap_flag': 2, 'track_limits_steps': 0, 'in_pits': False,
            'realtime': True, 'paused': False, 'speed_mps': 42.,
            'driver_name': 'PRIVATE_NAME', 'setup': 'PRIVATE_SETUP', 'path': 'C:/private',
            'events': ['SME_UPDATE_TELEMETRY', 'PRIVATE_NAME']}


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'records'
        self.now = 100.
        self.source = Source()
        self.created = []

    def service(self, **kwargs):
        def factory():
            self.created.append(self.source)
            return self.source
        service = LmuLiveService(source_factory=factory, recording_dir=self.directory,
                                 clock=lambda: self.now, **kwargs)
        self.addCleanup(service.stop)
        return service

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('백그라운드 관측 결과 대기 시간을 초과했습니다.')
            time.sleep(.005)

    def records(self):
        return [json.loads(line) for path in self.directory.glob('*.jsonl')
                for line in path.read_text(encoding='utf-8').splitlines()]

    def test_status_and_factory_never_attach_or_write(self):
        service = self.service()
        for _ in range(2):
            self.assertEqual(service.status()['state'], 'STOPPED')
            self.assertFalse(service.stop()['collecting'])
        self.assertEqual(self.created, [])
        self.assertFalse(self.directory.exists())
        with patch('lmu_live._default_source', side_effect=AssertionError('unexpected attach')):
            self.assertIsNot(get_live_service(), get_live_service())
            self.assertEqual(live_status()['state'], 'STOPPED')

    def test_timing_metadata_freezes_at_stop_and_resets_on_restart(self):
        service = self.service(utc_clock=lambda: 0.)
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        self.now += .5
        service.stop()
        stopped = service.status()['timing']
        self.now += 20
        self.assertEqual(service.status()['timing'], stopped)
        self.assertEqual(stopped['recording_elapsed_s'], .5)
        self.assertIsNone(stopped['read_in_progress_s'])
        rows = self.records()
        self.assertEqual([r['sequence'] for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(all(r['schema_version'] == 2 for r in rows))
        self.assertTrue(all(r['observed_utc'] == '1970-01-01T00:00:00.000+00:00' for r in rows))
        old_id = service.status()['recording_id']
        service.start()
        state = service.status()
        self.assertNotEqual(state['recording_id'], old_id)
        self.assertEqual(state['timing']['recording_elapsed_s'], 0)
        self.assertEqual(state['timing']['stale_count'], 0)
        self.assertIsNone(state['timing']['max_telemetry_receive_gap_s'])
        service.stop()
        restarted = [json.loads(line) for line in
                     (self.directory / (state['recording_id'] + '.jsonl')).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(restarted[0]['sequence'], 1)
        self.assertEqual(restarted[0]['reason_code'], 'STARTED')

    def test_stale_detection_time_survives_blocked_read_and_utc_rollback(self):
        reading, release = threading.Event(), threading.Event()
        utc = [200.]

        class Blocked(Source):
            def read(inner, timeout_ms=200):
                value = super().read(timeout_ms)
                if value == 'BLOCK':
                    reading.set()
                    release.wait(2)
                    return snapshot(10.01)
                return value

        self.source = Blocked()
        self.addCleanup(release.set)
        service = self.service(utc_clock=lambda: utc[0])
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        self.source.items.put('BLOCK')
        self.assertTrue(reading.wait(1))
        self.now = 101.5
        utc[0] = 150.  # UTC moved backwards; freshness must use monotonic time.
        state = service.status()
        self.assertEqual(state['state'], 'STALE')
        self.assertEqual(state['last_stale']['recording_elapsed_s'], 1.5)
        self.assertEqual(state['last_stale']['read_in_progress_s'], 1.5)
        self.assertEqual(state['last_stale']['stale_count'], 1)
        self.assertFalse(any(r.get('reason_code') == 'STALE_TELEMETRY' for r in self.records()))
        self.now = 104.5
        utc[0] = 100.
        release.set()
        self.wait(lambda: service.status()['frames_recorded'] == 2)
        self.assertEqual(service.status()['state'], 'OBSERVING')
        self.assertIsNone(service.status()['valid'])
        service.stop()
        rows = self.records()
        stale = next(r for r in rows if r.get('reason_code') == 'STALE_TELEMETRY')
        samples = [r for r in rows if r['type'] == 'SNAPSHOT']
        self.assertEqual(stale['observed_elapsed_s'], 1.5)
        self.assertEqual(stale['observed_utc'], '1970-01-01T00:02:30.000+00:00')
        self.assertEqual(samples[-1]['timing']['read_duration_s'], 4.5)
        self.assertEqual(samples[-1]['timing']['telemetry_receive_gap_s'], 4.5)
        self.assertAlmostEqual(samples[-1]['timing']['game_time_gap_s'], .01)
        self.assertEqual(service.status()['timing']['max_telemetry_receive_gap_s'], 4.5)
        self.assertEqual([r['sequence'] for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(all(event['valid'] is None for r in rows for event in r['lap_events']))

    def test_cached_and_scoring_only_packets_do_not_shorten_fresh_receive_gap(self):
        service = self.service()
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        for index, updated in enumerate((True, False), start=2):
            self.now += .3
            data = snapshot()
            data.update(telemetry_updated=updated, scoring_et=10 + index)
            self.source.items.put(data)
            self.wait(lambda: service.status()['frames_recorded'] == index)
        self.now += .3
        self.source.items.put(snapshot(10.01))
        self.wait(lambda: service.status()['frames_recorded'] == 4)
        service.stop()
        samples = [r for r in self.records() if r['type'] == 'SNAPSHOT']
        for row in samples[1:3]:
            self.assertIsNone(row['timing']['telemetry_receive_gap_s'])
            self.assertIsNone(row['timing']['game_time_gap_s'])
        self.assertAlmostEqual(samples[-1]['timing']['snapshot_receive_gap_s'], .3)
        self.assertAlmostEqual(samples[-1]['timing']['telemetry_receive_gap_s'], .9)
        self.assertAlmostEqual(samples[-1]['timing']['game_time_gap_s'], .01)
        self.assertEqual(service.status()['timing']['stale_count'], 0)

    def test_failed_read_timing_excludes_slow_source_cleanup(self):
        closing, release = threading.Event(), threading.Event()

        class SlowClose(Source):
            def read(inner, timeout_ms=200):
                self.now += .2
                raise Failure('GAME_EXITED')

            def close(inner):
                closing.set()
                release.wait(2)
                self.now += 2
                super().close()

        self.source = SlowClose()
        self.addCleanup(release.set)
        service = self.service()
        service.start()
        self.assertTrue(closing.wait(1))
        during_cleanup = service.status()['timing']
        self.assertIsNone(during_cleanup['read_in_progress_s'])
        self.assertAlmostEqual(during_cleanup['last_read_duration_s'], .2)
        release.set()
        self.wait(lambda: service.status()['journal_state'] == 'COMPLETE')
        self.assertAlmostEqual(service.status()['timing']['last_read_duration_s'], .2)
        failed = next(r for r in self.records() if r.get('reason_code') == 'GAME_EXITED')
        self.assertIsNone(failed['timing']['read_in_progress_s'])
        self.assertAlmostEqual(failed['timing']['last_read_duration_s'], .2)

    def test_limits_cannot_exceed_recording_safety_bounds(self):
        for kwargs in ({'max_duration_s': 901}, {'max_frames': 100001},
                       {'max_bytes': 128 * 1024 * 1024 + 1}, {'max_frames': 1.5},
                       {'max_bytes': True}, {'max_duration_s': float('nan')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service(**kwargs)
        self.assertEqual(self.created, [])
        self.assertFalse(self.directory.exists())

    def test_open_failure_no_journal_no_private_exception_and_no_retry(self):
        self.source.fail_open = 'NATIVE_NOT_BUILT'
        service = self.service()
        result = service.start()
        self.assertEqual(result['state'], 'UNAVAILABLE')
        self.assertIn('NATIVE_NOT_BUILT', result['reason'])
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.source.closed, 1)
        for _ in range(3):
            service.status()
        self.assertEqual(self.source.opened, 1)
        self.assertNotIn('PRIVATE', str(result))
        self.assertIsNone(result['valid'])

    def test_record_allowlist_session_normalisation_stop_and_idempotence(self):
        service = self.service()
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        result = service.status()
        self.assertEqual(result['state'], 'OBSERVING')
        self.assertIsNone(result['valid'])
        self.assertEqual(result['validation_state'], 'GAME_SCREEN_UNVERIFIED')
        self.assertEqual(result['snapshot']['speed_mps'], 42)
        self.assertEqual(len(result['snapshot']['session_key']), 64)
        service.start()
        self.assertEqual(self.source.opened, 1)
        service.stop()
        service.stop()
        self.assertEqual(self.source.closed, 1)
        self.assertIsNone(service.status()['snapshot'])
        self.assertEqual(service.status()['state'], 'STOPPED')
        content = next(self.directory.glob('*.jsonl')).read_text(encoding='utf-8')
        for forbidden in ('PRIVATE', 'SteamID', 'C:/private', 'setup', 'driver_name', 'session_id'):
            self.assertNotIn(forbidden, content + str(result))
        records = [json.loads(line) for line in content.splitlines()]
        self.assertEqual([r['reason_code'] for r in records if r['type'] == 'LIFECYCLE'],
                         ['STARTED', 'STOP_REQUESTED', 'STOPPED'])
        self.assertTrue(any(r['type'] == 'SNAPSHOT' for r in records))
        interrupted = [event for r in records for event in r['lap_events']]
        self.assertTrue(interrupted)
        self.assertTrue(all(event['valid'] is None for event in interrupted))
        self.assertEqual(service.status()['journal_state'], 'COMPLETE')

    def test_stale_duplicate_does_not_refresh_and_new_sample_recovers(self):
        service = self.service()
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        self.now += 1.01
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 2)
        result = service.status()
        self.assertEqual(result['state'], 'STALE')
        self.assertIsNone(result['snapshot'])
        self.assertIn('STALE_TELEMETRY', str(result['lap_observations']))
        self.source.items.put(snapshot(10.1))
        self.wait(lambda: service.status()['state'] == 'OBSERVING')
        self.assertEqual(service.status()['snapshot']['telemetry_et'], 10.1)
        service.stop()
        stale = [r for r in self.records() if r.get('reason_code') == 'STALE_TELEMETRY']
        self.assertEqual(len(stale), 1)
        self.assertTrue(stale[0]['lap_events'])
        self.assertIn('STALE_TELEMETRY', stale[0]['lap_events'][0]['reasons'])

    def test_false_update_flag_does_not_refresh_or_replace_latest(self):
        service = self.service()
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        self.now += .6
        data = snapshot(10.6)
        data['telemetry_updated'] = False
        self.source.items.put(data)
        self.wait(lambda: service.status()['frames_recorded'] == 2)
        self.assertEqual(service.status()['snapshot']['telemetry_et'], 10)
        self.now += .41
        self.assertEqual(service.status()['state'], 'STALE')
        self.assertIsNone(service.status()['snapshot'])

    def test_identity_change_clears_cached_latest_until_new_telemetry(self):
        service = self.service()
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        data = snapshot(11)
        data.update({'session_id': 'new session', 'vehicle_id': 3,
                     'telemetry_updated': False})
        self.source.items.put(data)
        self.wait(lambda: service.status()['frames_recorded'] == 2)
        self.assertEqual(service.status()['state'], 'WAITING')
        self.assertIsNone(service.status()['snapshot'])
        data['telemetry_updated'] = True
        self.source.items.put(data)
        self.wait(lambda: service.status()['frames_recorded'] == 3)
        self.assertEqual(service.status()['snapshot']['vehicle_id'], 3)
        self.assertIsNone(service.status()['timing']['last_game_time_gap_s'])
        self.assertIsNone(service.status()['timing']['last_telemetry_receive_gap_s'])
        service.stop()
        changed = [r for r in self.records() if r.get('reason_code') == 'SESSION_OR_VEHICLE_CHANGED']
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0]['lap_events'])

    def test_game_exit_terminal_persists_and_reconnect_is_new_recording_tracker(self):
        service = self.service()
        service.start()
        self.source.items.put(snapshot())
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        old_id = service.status()['recording_id']
        self.source.items.put(Failure('GAME_EXITED'))
        self.wait(lambda: self.source.closed == 1)
        self.assertEqual(service.status()['state'], 'GAME_EXITED')
        self.assertEqual(service.stop()['state'], 'GAME_EXITED')
        self.assertIsNone(service.status()['snapshot'])
        exited = [r for r in self.records() if r.get('reason_code') == 'GAME_EXITED']
        self.assertEqual(len(exited), 1)
        self.assertTrue(exited[0]['lap_events'])
        self.assertIn('GAME_EXITED', exited[0]['lap_events'][0]['reasons'])
        self.source = Source()
        result = service.start()
        self.assertNotEqual(result['recording_id'], old_id)
        self.assertEqual(result['frames_recorded'], 0)
        self.assertEqual(result['lap_observations']['completed'], [])
        self.assertEqual(len(list(self.directory.glob('*.jsonl'))), 2)

    def test_read_error_is_sanitised_and_not_retried(self):
        service = self.service()
        service.start()
        self.source.items.put(Failure('PRIVATE_NAME'))
        self.wait(lambda: self.source.closed == 1)
        result = service.status()
        self.assertEqual(result['state'], 'ERROR')
        self.assertIn('READ_FAILED', result['reason'])
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(len(self.created), 1)

    def test_frame_duration_and_byte_limits(self):
        for limit in ('frames', 'duration', 'bytes'):
            with self.subTest(limit=limit):
                self.source = Source()
                kwargs = {'max_frames': 1} if limit == 'frames' else {'max_duration_s': .1} if limit == 'duration' else {'max_bytes': 1}
                service = self.service(**kwargs)
                service.start()
                if limit == 'duration':
                    self.now += .2
                self.source.items.put(snapshot())
                self.wait(lambda: service.status()['state'] == 'LIMIT_REACHED')
                self.wait(lambda: self.source.closed == 1)
                result = service.status()
                self.assertIsNone(result['snapshot'])
                self.assertFalse(result['collecting'])
                self.assertEqual(service.stop()['state'], 'LIMIT_REACHED')
                self.assertTrue(all(path.stat().st_size <= kwargs.get('max_bytes', 128 * 1024 * 1024)
                                    for path in self.directory.glob(service.status()['recording_id'] + '.jsonl')))
                if limit == 'bytes':
                    self.assertEqual(service.status()['journal_state'], 'INCOMPLETE')
                else:
                    self.assertTrue(any(r.get('reason_code') == 'LIMIT_REACHED' for r in self.records()))

    def test_journal_write_failure_is_visible_and_sanitised(self):
        service = self.service()
        with patch.object(service, '_write', side_effect=OSError('PRIVATE_NAME C:/private')):
            service.start()
            self.wait(lambda: self.source.closed == 1)
            self.wait(lambda: service.status()['journal_state'] == 'INCOMPLETE')
        result = service.status()
        self.assertEqual(result['state'], 'ERROR')
        self.assertIsNone(result['snapshot'])
        self.assertNotIn('PRIVATE', str(result))

    def test_transient_snapshot_write_failure_never_reports_complete_journal(self):
        service = self.service()
        original_open = Path.open

        class TransientJournal:
            def __init__(inner, file):
                inner.file, inner.failed = file, False

            def write(inner, text):
                if '"type":"SNAPSHOT"' in text and not inner.failed:
                    inner.failed = True
                    raise OSError('PRIVATE_NAME C:/private')
                return inner.file.write(text)

            def flush(inner):
                return inner.file.flush()

            def fileno(inner):
                return inner.file.fileno()

            def close(inner):
                return inner.file.close()

        def open_journal(path, *args, **kwargs):
            file = original_open(path, *args, **kwargs)
            return TransientJournal(file) if args and args[0] == 'x' else file

        with patch.object(Path, 'open', open_journal):
            service.start()
            self.source.items.put(snapshot())
            self.wait(lambda: self.source.closed == 1)
            self.wait(lambda: service.status()['journal_state'] != 'ACTIVE')
        result = service.status()
        self.assertEqual(result['state'], 'ERROR')
        self.assertEqual(result['journal_state'], 'INCOMPLETE')
        self.assertIn('JOURNAL_WRITE_FAILED', result['reason'])
        self.assertNotIn('PRIVATE', str(result))
        self.assertTrue(any(r.get('reason_code') == 'SNAPSHOT_WRITE_FAILED' for r in self.records()))

    def test_status_expiration_only_queues_and_single_worker_persists_events(self):
        service = self.service()
        writes = []
        original = service._write

        def write(journal, payload, **kwargs):
            writes.append(threading.current_thread().name)
            return original(journal, payload, **kwargs)

        with patch.object(service, '_write', side_effect=write):
            service.start()
            self.source.items.put(snapshot())
            self.wait(lambda: service.status()['frames_recorded'] == 1)
            self.now += 1.1
            for _ in range(10):
                self.assertEqual(service.status()['state'], 'STALE')
            service.stop()
        self.assertTrue(writes)
        self.assertEqual(set(writes), {'lmu-live-observer'})
        stale = [r for r in self.records() if r.get('reason_code') == 'STALE_TELEMETRY']
        self.assertEqual(len(stale), 1)

    def test_concurrent_start_and_stop_create_only_one_source(self):
        service = self.service()
        threads = [threading.Thread(target=service.start) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.source.opened, 1)
        threads = [threading.Thread(target=service.stop) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.source.closed, 1)
        self.assertEqual(service.status()['state'], 'STOPPED')

    def test_bounded_stop_does_not_start_second_worker_before_exit(self):
        release = threading.Event()
        reading = threading.Event()
        class Blocked(Source):
            def read(inner, timeout_ms=200):
                reading.set()
                release.wait(2)
                return snapshot()
        self.source = Blocked()
        service = self.service(join_timeout_s=.02)
        self.addCleanup(release.set)
        service.start()
        self.assertTrue(reading.wait(1))
        before = time.monotonic()
        self.assertEqual(service.stop()['state'], 'STOPPING')
        self.assertLess(time.monotonic() - before, .3)
        service.start()
        self.assertEqual(self.source.opened, 1)
        release.set()
        self.wait(lambda: self.source.closed == 1)
        self.assertEqual(service.status()['state'], 'STOPPED')

    def test_malformed_numeric_data_never_serialises_nan_or_private_fields(self):
        service = self.service()
        service.start()
        data = snapshot()
        data.update({'speed_mps': float('nan'), 'throttle_ratio': {'secret': 'PRIVATE_NAME'},
                     'in_pits': 'PRIVATE_NAME', 'events': ['PRIVATE_NAME']})
        self.source.items.put(data)
        self.wait(lambda: service.status()['frames_recorded'] == 1)
        result = service.status()
        self.assertIsNone(result['snapshot']['speed_mps'])
        self.assertIsNone(result['snapshot']['throttle_ratio'])
        self.assertIsNone(result['snapshot']['in_pits'])
        self.assertNotIn('PRIVATE', str(result))


if __name__ == '__main__':
    unittest.main()
