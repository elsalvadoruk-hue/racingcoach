"""Foreground/live priority uses synthetic clocks and no game connection."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import driver_history_scheduling as scheduling
from driver_history_scheduling import HistoryActivity, SystemPressure, is_foreground_analysis


class HistoryActivityTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.live = {'active_drive': False, 'completed_serial': 0}
        self.pressure = False
        self.activity = HistoryActivity(live_state=lambda: self.live, clock=lambda: self.now,
                                        pressure=lambda: self.pressure, idle_seconds=10.)

    def test_idle_start_is_allowed_without_waiting_for_a_new_drive(self):
        self.assertIsNone(self.activity.reason())

    def test_moving_drive_unknown_active_pit_and_ending_keep_background_stopped(self):
        for phase in ('DRIVING', 'UNKNOWN', 'ENDING'):
            self.live.update(active_drive=True, phase=phase)
            self.assertEqual(self.activity.reason(), 'DRIVING')

    def test_completed_drive_waits_then_resumes_when_no_analysis_arrives(self):
        self.live['active_drive'] = True
        self.assertEqual(self.activity.reason(), 'DRIVING')
        self.live.update(active_drive=False, completed_serial=1)
        self.assertEqual(self.activity.reason(), 'COOLDOWN')
        self.now += 9
        self.assertEqual(self.activity.reason(), 'COOLDOWN')
        self.now += 1
        self.assertIsNone(self.activity.reason())

    def test_foreground_analysis_extends_drive_end_wait_until_analysis_finishes(self):
        self.live.update(active_drive=True)
        self.activity.reason()
        self.live.update(active_drive=False, completed_serial=1)
        self.activity.reason()
        with self.activity.foreground():
            self.now += 60
            self.assertEqual(self.activity.reason(), 'ANALYZING')
        self.assertEqual(self.activity.reason(), 'COOLDOWN')
        self.now += 10
        self.assertIsNone(self.activity.reason())

    def test_concurrent_requests_and_failed_request_do_not_resume_prematurely(self):
        with self.activity.foreground():
            try:
                with self.activity.foreground():
                    raise ValueError('synthetic failure')
            except ValueError:
                pass
            self.assertEqual(self.activity.reason(), 'ANALYZING')
        self.now += 10
        self.assertIsNone(self.activity.reason())

    def test_catalog_polling_yields_while_running_without_restarting_idle_delay(self):
        for _ in range(4):
            with self.activity.foreground(settle=False):
                self.assertEqual(self.activity.reason(), 'ANALYZING')
            self.assertIsNone(self.activity.reason())
            self.now += 5

    def test_catalog_polling_preserves_existing_analysis_cooldown(self):
        with self.activity.foreground():
            pass
        self.now += 5
        with self.activity.foreground(settle=False):
            pass
        self.assertEqual(self.activity.reason(), 'COOLDOWN')
        self.now += 5
        self.assertIsNone(self.activity.reason())

    def test_cpu_or_memory_pressure_waits_for_a_quiet_interval(self):
        self.pressure = True
        self.assertEqual(self.activity.reason(), 'RESOURCE_BUSY')
        self.pressure = False
        self.assertEqual(self.activity.reason(), 'COOLDOWN')
        self.now += 10
        self.assertIsNone(self.activity.reason())

    def test_previous_completion_at_start_does_not_require_another_analysis(self):
        self.live['completed_serial'] = 18
        self.assertIsNone(self.activity.reason())

    def test_live_view_error_conservatively_defers_work(self):
        self.activity.live_state = lambda: (_ for _ in ()).throw(ValueError())
        self.assertEqual(self.activity.reason(), 'DRIVING')

    def test_analysis_routes_count_but_polling_and_job_controls_do_not(self):
        for method, path in [('POST','/api/analyze'), ('POST','/api/workspace/session'),
                             ('POST','/api/lmu/workspace/comparison'), ('GET','/api/file-laps'),
                             ('POST','/api/lap-overlay'), ('POST','/api/comparison-detail'),
                             ('GET','/api/channels'),
                             *[('GET', '/api/lmu/telemetry/' + route) for route in (
                                 'file', 'lap-analysis', 'vehicle-channels', 'vehicle-comparison',
                                 'setup', 'setup-comparison', 'compare-files', 'compare')],
                             ('POST', '/api/lmu/telemetry/replay'),
                             ('POST', '/api/lmu/telemetry/corner-consistency'),
                             ('POST', '/api/lmu/practice/tasks/synthetic/compare')]:
            self.assertTrue(is_foreground_analysis(method,path),path)
        for method, path in [('GET','/api/status'), ('GET','/api/driver-report'),
                             ('GET','/api/workspace/progress/abc'), ('POST','/api/driver-history/library'),
                             ('POST','/api/driver-history/library/pause'),
                             ('GET','/api/lmu/status'), ('GET','/api/lmu/telemetry/files')]:
            self.assertFalse(is_foreground_analysis(method,path),path)


class SystemPressureTests(unittest.TestCase):
    def setUp(self):
        self.now, self.idle, self.total = 100., 100, 200
        self.total_memory, self.free_memory = 8*1024**3, 3*1024**3
        self.calls = 0
        def system_times(idle, kernel, user):
            self.calls += 1
            for pointer, value in [(idle,self.idle),(kernel,self.total),(user,0)]:
                pointer._obj.dwHighDateTime, pointer._obj.dwLowDateTime = value >> 32, value & 0xffffffff
            return 1
        def memory_status(pointer):
            pointer._obj.total_phys, pointer._obj.available_phys = self.total_memory, self.free_memory
            return 1
        self.enterContext(patch.object(scheduling.ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(
            GetSystemTimes=system_times, GlobalMemoryStatusEx=memory_status)), create=True))
        self.enterContext(patch.object(scheduling, 'os', SimpleNamespace(name='nt')))
        self.probe = SystemPressure(clock=lambda:self.now)

    def test_cpu_threshold_and_one_second_sampling(self):
        self.assertFalse(self.probe())
        self.idle, self.total = 120, 300
        self.assertFalse(self.probe())
        self.assertEqual(self.calls,1)
        self.now += 1
        self.assertTrue(self.probe())
        self.now += 1
        self.idle, self.total = 210, 400
        self.assertFalse(self.probe())

    def test_memory_threshold_has_minimum_and_maximum(self):
        self.total_memory, self.free_memory = 2*1024**3, 500*1024**2
        self.assertTrue(self.probe())
        self.now += 1
        self.total_memory, self.free_memory = 64*1024**3, int(1.9*1024**3)
        self.assertTrue(self.probe())
        self.now += 1
        self.free_memory = int(2.1*1024**3)
        self.assertFalse(self.probe())


if __name__ == '__main__':
    unittest.main()
