"""Regression checks for coaching-only data, observed setup aliases, and fast lists."""
import asyncio
import os
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from test_analysis_reliability import api
from analysis_support import FileMetadataIndex, clean_segment_context, mask_grid_to_clean_windows


class PartialCoachingTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def telemetry(self):
        n = 6001
        distance = np.linspace(0, 1, n)
        speed = 38 - 15 * np.exp(-((distance-.25)/.04)**2) - 15 * np.exp(-((distance-.75)/.04)**2)
        channels = {"SessionTime": 100+distance*100, "LapDistPct": distance,
                    "LapCurrentLapTime": distance*100, "Speed": speed,
                    "Lap": np.full(n,2), "IsOnTrack": np.ones(n), "PlayerTrackSurface": np.full(n,3), "OnPitRoad": np.zeros(n),
                    "Brake": (speed < 30).astype(float)*.5, "Throttle": (speed >= 30).astype(float),
                    "SteeringWheelAngle": .5*(np.exp(-((distance-.25)/.04)**2)+np.exp(-((distance-.75)/.04)**2)),
                    "Gear": np.full(n, 3), "YawRate": np.zeros(n)}
        channels["PlayerTrackSurface"][(distance>.49)&(distance<.51)] = 0
        path = self.root/'partial.ibt'; path.write_bytes(b'test')
        lap = api.LapSegment(2, 0, n-1, True, False, 'INVALID', None, 0, 1, .98, 0, None, 'off track')
        return api.ParsedTelemetry(path, api.file_signature(path), 60, 0, n, 0, '', {}, {}, channels,
                                   {"track_length_m":4000, "car_name":"Test", "track_name":"Test"},
                                   {"Chassis":{"Rear":{"ArbBlades":"3"}}}, [lap])

    def test_incident_buffer_is_discarded_without_adding_official_laps(self):
        telemetry = self.telemetry()
        clean = clean_segment_context(telemetry)
        self.assertTrue(clean['coaching_ready'])
        self.assertEqual(clean['official_laps_added'], 0)
        self.assertEqual(len(clean['segments']), 2)
        self.assertLess(clean['segments'][0]['end_pct'], .49)
        self.assertGreater(clean['segments'][1]['start_pct'], .51)
        analyzer = api.TelemetryAnalyzer(telemetry)
        grid = mask_grid_to_clean_windows(analyzer._lap_grid(telemetry.laps[0]), clean['segments'])
        bad = (grid['distance_pct'] > .48) & (grid['distance_pct'] < .52)
        self.assertFalse(np.any(grid['_coverage'][bad]))
        self.assertTrue(np.all(np.isnan(grid['speed_kph'][bad])))
        self.assertEqual(analyzer.valid_laps, [])

    def test_missing_safety_channels_cannot_create_clean_evidence(self):
        telemetry = self.telemetry()
        del telemetry.channels['OnPitRoad']
        self.assertFalse(clean_segment_context(telemetry)['coaching_ready'])

    def test_partial_coaching_analysis_never_returns_full_lap_pace(self):
        analyzer = api.TelemetryAnalyzer(self.telemetry())
        result = analyzer.analyze(api.AnalyzeRequest(feedback_phase='MID_CORNER', feedback_speed='LOW', feedback_symptom='OVERSTEER'))
        self.assertEqual(result['data_quality']['valid_laps'], 0)
        self.assertTrue(result['data_quality']['coaching_ready'])
        self.assertFalse(result['data_quality']['timed_lap_comparison_ready'])
        self.assertIsNone(result['comparison']['target_time_s'])
        self.assertIsNone(result['comparison']['total_delta_s'])
        self.assertEqual(result['lap_selection']['strategy'], 'CLEAN_SEGMENT_COACHING')
        candidate = result['setup_recommendation']['test_candidates'][0]
        self.assertEqual(candidate['source_scope'], 'COACHING_ONLY')
        self.assertFalse(candidate['effect_confirmed'])
        self.assertLessEqual(candidate['confidence'], .4)

    def test_observed_bmw_names_recover_controls_without_creating_missing_values(self):
        setup = {'Chassis': {'FrontBrakes': {'ArbBlades':'3','TotalToeIn':'-3 mm'},
                            'Rear': {'WingAngle':'-1 degrees'}, 'GearsDifferential': {'DiffPreload':'100 Nm'}}}
        summary = api.extract_setup_summary(setup)
        self.assertEqual(summary['front_arb_blades'], 3)
        self.assertEqual(summary['rear_wing_angle_deg'], -1)
        self.assertEqual(summary['diff_preload_nm'], 100)
        self.assertIsNone(summary['tc_setting'])

    def test_partial_analysis_with_missing_brake_measurements_still_completes(self):
        telemetry = self.telemetry()
        telemetry.channels['Brake'][:] = np.nan
        result = api.TelemetryAnalyzer(telemetry).analyze(api.AnalyzeRequest())
        self.assertEqual(result['data_quality']['valid_laps'], 0)
        self.assertIsNone(result['comparison']['total_delta_s'])
        self.assertEqual(result['dynamic_controls'], [])

    def test_dynamic_controls_require_measured_finite_braking_data(self):
        analyzer = api.TelemetryAnalyzer(self.telemetry())
        distance = np.linspace(0, 1, 2001)
        grid = {'distance_pct': distance, 'Throttle': np.ones(len(distance)),
                'Brake': np.zeros(len(distance)), 'SteeringWheelAngle': np.zeros(len(distance))}
        measured = {'label': 'C1', 'distance_m': 3000, 'measurement_usable': True,
                    'target': {'brake_start_pct': .75, 'peak_brake': .8}}
        self.assertEqual(len(analyzer._dynamic_control_opportunities([measured], grid)), 1)
        for start, peak, usable in ((.75, None, True), (.75, np.nan, True),
                                    (np.nan, .8, True), (.75, .8, False), (.75, .2, True)):
            with self.subTest(start=start, peak=peak, usable=usable):
                corner = {**measured, 'measurement_usable': usable,
                          'target': {'brake_start_pct': start, 'peak_brake': peak}}
                self.assertEqual(analyzer._dynamic_control_opportunities([corner], grid), [])

    def test_entry_hypothesis_uses_known_preload_but_does_not_invent_a_click(self):
        feedback = {'status':'MATCHED','observations':[{'corner_id':'C1','scope':'COACHING_ONLY'}]}
        request = api.AnalyzeRequest(feedback_phase='ENTRY',feedback_speed='LOW',feedback_symptom='UNDERSTEER')
        candidate = api.setup_exploration_candidate(request, {'diff_preload_nm':100,'front_arb_blades':3},feedback,0,set())['candidate']
        self.assertEqual(candidate['setting'], 'diff_preload_nm')
        self.assertEqual(candidate['direction'], 'DECREASE')
        self.assertIsNone(candidate['value'])
        self.assertFalse(candidate['validator']['numeric_value_verified'])
        fallback = api.setup_exploration_candidate(request, {'diff_preload_nm':100,'front_arb_blades':3},feedback,1,{'diff_preload_nm'})['candidate']
        self.assertEqual(fallback['setting'], 'front_arb_blades')

    def test_index_survives_restart_and_invalidates_modified_files(self):
        path = self.root/'index.sqlite3'
        signature = ('file.ibt', 100, 123)
        FileMetadataIndex(path).put(signature, {'valid_laps':3})
        self.assertEqual(FileMetadataIndex(path).get(signature), {'valid_laps':3})
        self.assertIsNone(FileMetadataIndex(path).get(('file.ibt',101,124)))

    def test_fast_listing_reads_header_without_loading_telemetry_samples(self):
        path = self.root/'header.ibt'
        session = b'WeekendInfo:\n TrackDisplayName: Test Track\n TrackLength: 4 km\n'
        header = struct.pack('<10i',1,0,60,0,len(session),112,1,0,1,8)+b'\0'*72
        path.write_bytes(header+session)
        with patch.object(api,'_FILE_METADATA_INDEX',FileMetadataIndex(self.root/'index.sqlite3')), patch.object(api.repository,'load',side_effect=AssertionError('sample scan')):
            result = api.quick_file_visibility(path)
        self.assertTrue(result['metadata_pending'])
        self.assertIsNone(result['valid_laps'])
        self.assertIsNone(result['analyzable'])
        self.assertEqual(result['track_name'],'Test Track')

    def test_locked_file_is_pending_and_recovers_without_a_signature_change(self):
        path = self.root/'locked.ibt'
        session = b'WeekendInfo:\n TrackDisplayName: Test Track\n TrackLength: 4 km\n'
        header = struct.pack('<10i',1,0,60,0,len(session),112,1,0,1,8)+b'\0'*72
        path.write_bytes(header+session)
        signature = api.file_signature(path)
        index = FileMetadataIndex(self.root/'index.sqlite3')
        original_open = Path.open
        def locked_open(target, *args, **kwargs):
            if target == path:
                raise PermissionError(13, 'Permission denied', str(path))
            return original_open(target, *args, **kwargs)
        with patch.object(api, '_FILE_METADATA_INDEX', index), patch.object(Path, 'open', locked_open):
            quick = api.quick_file_visibility(path)
            full = api.telemetry_file_visibility(path)
        self.assertEqual(quick['status'], 'WAITING_FILE')
        self.assertTrue(quick['retryable'])
        self.assertIsNone(quick['error'])
        self.assertFalse(full['analyzable'])
        self.assertIsNone(index.get(signature))
        self.assertNotIn(signature, api._FILE_VISIBILITY_CACHE)
        with patch.object(api, '_FILE_METADATA_INDEX', index):
            ready = api.quick_file_visibility(path)
        self.assertEqual(api.file_signature(path), signature)
        self.assertEqual(ready['status'], 'PENDING')
        self.assertEqual(ready['track_name'], 'Test Track')
        self.assertFalse(ready.get('retryable'))

    def test_locked_explicit_analysis_returns_waiting_response_instead_of_500(self):
        path = self.root/'locked.ibt'
        path.write_bytes(b'fixture')
        with patch.object(api, 'resolve_telemetry_file', return_value=path), \
                patch.object(api.IBTReader, 'parse', side_effect=PermissionError(13, 'Permission denied')):
            client = TestClient(api.app)
            response = client.post('/api/analyze', json={'filename': path.name, 'include_ai': False})
            references = client.get('/api/external-references', params={'filename': path.name})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(references.status_code, 409)
        self.assertEqual(response.json()['code'], 'TELEMETRY_FILE_BUSY')
        self.assertTrue(response.json()['retryable'])
        self.assertEqual(response.headers['Retry-After'], '2')
        self.assertNotIn('Errno', response.json()['detail'])

    def test_unlock_event_is_emitted_even_when_file_signature_is_unchanged(self):
        signature = ['same.ibt',100,123]
        busy = {'telemetry': {'file_name': 'same.ibt', 'file_signature': signature, **api.waiting_file_visibility()}}
        ready = {'telemetry': {'file_name': 'same.ibt', 'file_signature': signature, 'status': 'PENDING'}}
        async def scenario():
            response = await api.telemetry_events()
            stream = response.body_iterator
            first = await anext(stream)
            second = await anext(stream)
            await stream.aclose()
            return first, second
        from unittest.mock import AsyncMock
        with patch.object(api, 'get_status', side_effect=[busy,ready]), patch.object(api.asyncio, 'sleep', new_callable=AsyncMock):
            first, second = asyncio.run(scenario())
        self.assertIn('WAITING_FILE', first)
        self.assertIn('PENDING', second)

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing lock')
    def test_real_windows_exclusive_lock_waits_and_recovers_after_close(self):
        import ctypes
        from ctypes import wintypes
        path = self.root/'windows-lock.ibt'
        session = b'WeekendInfo:\n TrackDisplayName: Test Track\n'
        path.write_bytes(struct.pack('<10i',1,0,60,0,len(session),112,1,0,1,8)+b'\0'*72+session)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        with patch.object(api, '_FILE_METADATA_INDEX', FileMetadataIndex(self.root/'index.sqlite3')):
            try:
                self.assertTrue(api.quick_file_visibility(path)['retryable'])
                with self.assertRaises(api.TelemetryFileBusy):
                    api.TelemetryRepository().load(path)
            finally:
                kernel.CloseHandle(handle)
            self.assertEqual(api.quick_file_visibility(path)['status'], 'PENDING')

    def test_missing_endpoints_do_not_make_nan_traces_repeatable(self):
        telemetry = self.telemetry()
        first = replace(telemetry.laps[0], valid=True, status='VALID', lap_time_s=100.)
        second = replace(first, lap_number=3, lap_time_s=100.1)
        analyzer = api.TelemetryAnalyzer(replace(telemetry, laps=[first,second]))
        distance = np.linspace(0,1,analyzer.grid_points)
        grid = {'distance_pct':distance, 'time_s':np.full(len(distance),np.nan), '_coverage':np.zeros(len(distance),dtype=bool)}
        with patch.object(analyzer,'_lap_grid',return_value=grid):
            profiles = analyzer._lap_reproducibility_profiles()
        self.assertEqual(profiles['repeatable_lap_count'],0)
        self.assertTrue(all(p['reproducibility_status']=='ISOLATED' for p in profiles['profiles']))


if __name__ == '__main__':
    unittest.main()
