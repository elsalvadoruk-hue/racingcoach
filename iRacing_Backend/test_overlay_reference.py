"""Real-reference selection and distance alignment; temporary cache, mocked HTTP only."""
from datetime import datetime, timedelta, timezone
import json
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import numpy as np

from overlay_control import OverlayController
from overlay_reference import AlignedReference, DownloadRedirect, GridReference, OverlayReference, live_request, matching, ranked_metadata
from urllib.request import Request


def metadata(**changes):
    return {'car_platform_id': 10, 'track_platform_id': 20, 'lap_id': 'TESTLAP',
            'clean': True, 'weather_declared_wet': False, 'tire_compound': 1,
            'track_temp_c': 30., 'air_temp_c': 24., 'fuel_l': 30., 'lap_time_s': 100.,
            'start_time': datetime.now(timezone.utc).isoformat(), **changes}


def live(**changes):
    sample = {'on_track': True, 'is_replay': False, 'session_state': 4,
              'on_pit_road': False, 'track_wetness': 1, 'tire_compound': 0,
              'track_temp_c': 30., 'air_temp_c': 24., 'fuel_l': 30.,
              'lap_number': 2, 'distance_pct': .5, 'lap_best_s': 102., **changes}
    return {'connected': True, 'run_id': 'session-1', 'track_length_m': 5000,
            'identity': {'car_id': '10', 'track_id': '20'}, 'snapshot': sample,
            'points': [{'session_time': 100+i/30, 'lap_number': 2, 'distance_pct': .45+i/3000,
                        'brake': .2, 'throttle': .8, 'on_pit_road': False} for i in range(151)]}


class Lap:
    def __init__(self, meta=None):
        self.metadata = meta or metadata()
        self.distance_pct = np.linspace(0, 1, 1001)
        self.sample_time_s = self.distance_pct*100
        self.channels = {'Speed': np.full(1001, 50.), 'Brake': np.full(1001, .2),
                         'Throttle': np.full(1001, .8)}

    def grid(self, count, length):
        x = np.linspace(0, 1, count)
        return {'distance_pct': x, 'time_s': x*100,
                'Brake': (np.sin(x*30) > .5).astype(float)*.4,
                'Throttle': (np.sin(x*30) <= .5).astype(float)}


def wait_for(predicate):
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Reference worker did not reach expected state')


class SelectionTests(unittest.TestCase):
    def test_external_csv_redirect_does_not_receive_the_garage61_token(self):
        original = Request('https://garage61.net/api/v1/laps/TEST/csv', headers={'Authorization': 'Bearer test'})
        redirected = DownloadRedirect().redirect_request(original, None, 302, '', {}, 'https://download.example.test/lap.csv')
        self.assertFalse(redirected.has_header('Authorization'))

    def test_exact_car_layout_surface_clean_lap_age_and_condition_limits_are_required(self):
        context = live_request(live())
        self.assertTrue(matching(metadata(), context))
        for changes in ({'car_platform_id': 11}, {'track_platform_id': 21},
                        {'weather_declared_wet': True}, {'weather_declared_wet': None},
                        {'clean': False},
                        {'offtrack': True}, {'pit_in': True}, {'missing': True},
                        {'fuel_l': 90}, {'track_temp_c': 50}, {'start_time': None},
                        {'start_time': (datetime.now(timezone.utc)-timedelta(days=181)).isoformat()}):
            self.assertFalse(matching(metadata(**changes), context), changes)
        self.assertIsNone(live_request(live(track_wetness=None))['conditions']['weather_declared_wet'])

    def test_sdk_tire_index_neither_rejects_nor_favors_a_g61_code(self):
        context = live_request(live(tire_compound=0))
        self.assertEqual(context['sdk_tire_compound'], 0)
        self.assertIsNone(context['conditions']['tire_compound'])
        original = metadata()
        results = []
        for code in (0, 1, None):
            with self.subTest(g61_code=code):
                result = ranked_metadata([{**original, 'tire_compound': code}], context)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['_unknown_conditions'], ['tire_compound'])
                results.append(result[0])
        for key in ('similarity', 'coverage', 'reasons'):
            self.assertEqual(results[0][key], results[1][key])
            self.assertEqual(results[0][key], results[2][key])
        # The last rank element is recency against the current clock, which moves between calls
        # on clocks finer than Windows' old 16 ms tick (Python 3.13+).
        for other in results[1:]:
            self.assertEqual(results[0]['_rank'][:3], other['_rank'][:3])
            self.assertAlmostEqual(results[0]['_rank'][3], other['_rank'][3], places=6)

    def test_verified_g61_tire_condition_would_still_require_a_matching_code(self):
        context = live_request(live())
        # This field is reserved for a verified G61 code, never the raw SDK index.
        context['conditions']['tire_compound'] = 1
        self.assertTrue(matching(metadata(tire_compound=1), context))
        for code in (0, None):
            self.assertFalse(matching(metadata(tire_compound=code), context))

    def test_conditions_take_priority_over_a_much_faster_unlike_lap(self):
        close = metadata(lap_id='CLOSE', lap_time_s=101)
        unlike = metadata(lap_id='UNLIKE', lap_time_s=90, track_temp_c=41, fuel_l=55)
        result = ranked_metadata([unlike, close], live_request(live()))
        self.assertEqual(result[0]['lap_id'], 'CLOSE')
        partial = ranked_metadata([close], live_request(live(fuel_l=None)))
        self.assertIn('fuel_l', partial[0]['_unknown_conditions'])

    def test_no_completed_best_lap_does_not_impose_an_impossible_one_second_search_limit(self):
        for value in (-1, 0, None):
            context = live_request(live(lap_best_s=value))
            self.assertIsNone(context['current_best_lap_s'])

    def test_slow_outlier_is_rejected_even_with_newer_date_and_matching_conditions(self):
        items = [metadata(lap_id='SLOW', lap_time_s=104.5),
                 metadata(lap_id='NORMAL', lap_time_s=80), metadata(lap_id='FAST', lap_time_s=79)]
        ranked = ranked_metadata(items, live_request(live(lap_best_s=0)))
        self.assertEqual([m['lap_id'] for m in ranked], ['FAST', 'NORMAL'])

    def test_similar_conditions_prefer_pace_before_recency(self):
        old = (datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        items = [metadata(lap_id='NEW', lap_time_s=102), metadata(lap_id='FAST', lap_time_s=100, start_time=old)]
        ranked = ranked_metadata(items, live_request(live(lap_best_s=0)))
        self.assertEqual(ranked[0]['lap_id'], 'FAST')
        self.assertFalse(ranked_metadata([metadata(lap_time_s=110)], live_request(live(lap_best_s=100))))
        # A small fuel difference must not outweigh a normal, faster flying lap.
        items = [metadata(lap_id='SLOW', lap_time_s=105),
                 metadata(lap_id='FAST', lap_time_s=100, fuel_l=24)]
        self.assertEqual(ranked_metadata(items, live_request(live(lap_best_s=0)))[0]['lap_id'], 'FAST')

    def test_one_implausibly_fast_metadata_value_does_not_remove_the_healthy_pool(self):
        items = [metadata(lap_id='BROKEN', lap_time_s=1), metadata(lap_id='A', lap_time_s=80),
                 metadata(lap_id='B', lap_time_s=81)]
        self.assertEqual({m['lap_id'] for m in ranked_metadata(items, live_request(live(lap_best_s=0)))},
                         {'BROKEN', 'A', 'B'})  # The CSV admission check rejects the broken timing.


class AlignmentTests(unittest.TestCase):
    def test_small_finish_gap_joins_measured_ends_without_clearing_history(self):
        grid = Lap().grid(2501, 5000)
        for name, values in grid.items():
            if name != 'distance_pct':
                values[[0, -1]] = np.nan
        reference = AlignedReference(GridReference(grid, 100), 5000)
        self.assertIsNotNone(reference.at(0))
        self.assertTrue(np.isnan(grid['time_s'][0]))
        self.assertAlmostEqual(reference.at(1)-reference.at(0), 100)
        for stagger in (-1, 0, 1):
            history = []
            for i in range(361):
                position = .9+i/3000
                history.append({'session_time': i/30, 'distance_pct': position % 1,
                                'lap_number': 2+int(position+stagger/3000 >= 1),
                                'brake': .2, 'throttle': .8, 'on_pit_road': False,
                                'on_track': True, 'is_replay': False})
                if i >= 298:
                    view = {**live(), 'snapshot': history[-1], '_history': tuple(history)}
                    frame = reference.window(view)
                    self.assertIsNotNone(frame, (i, stagger))
                    self.assertGreaterEqual(len(frame['current']), 298, (i, stagger))
                    self.assertLess(frame['current'][0]['offset'], -9.8)

    def test_large_finish_gap_is_not_filled(self):
        grid = Lap().grid(2501, 5000)
        grid['time_s'][grid['distance_pct'] < .02] = np.nan
        reference = AlignedReference(GridReference(grid, 100), 5000)
        self.assertIsNone(reference.at(0))
        self.assertIsNone(reference.window(live(distance_pct=.01)))

    def test_slow_formation_uses_observed_history_to_fill_reference_time_axis(self):
        reference = AlignedReference(Lap(), 5000)
        view = live(session_state=3, lap_number=0)
        # At one third of reference speed the last ten reference seconds need
        # thirty seconds of measured driving, not just ten wall-clock seconds.
        history = [{'session_time': 100+i/30, 'lap_number': 0,
                    'distance_pct': .39+i/9000, 'brake': .2, 'throttle': .8,
                    'on_track': True, 'is_replay': False, 'on_pit_road': False}
                   for i in range(991)]
        view['_history'] = tuple(history)
        view['points'] = history[-301:]
        frame = reference.window(view)
        self.assertAlmostEqual(frame['current'][0]['offset'], -10, delta=.02)
        self.assertAlmostEqual(frame['current'][-1]['offset'], 0)
        self.assertGreater(len(frame['current']), 890)
        self.assertLessEqual(max(row['offset'] for row in frame['current']), 0)
        # The native packet's ordinary recent points remain small.
        self.assertEqual(len(view['points']), 301)

    def test_extended_history_stops_at_a_gap_or_cockpit_exit(self):
        reference = AlignedReference(Lap(), 5000)
        for interrupt in ({'on_track': False}, {'is_replay': True},
                          {'on_pit_road': True}, {'session_time': 103}):
            view = live()
            points = [{**row, 'on_track': True, 'is_replay': False} for row in view['points']]
            points[-3].update(interrupt)
            view['_history'] = tuple(points)
            frame = reference.window(view)
            self.assertEqual(len(frame['current']), 2, interrupt)
            self.assertTrue(frame['current'][0]['break'])

    def test_position_alignment_ignores_different_session_clocks_and_has_future_reference_only(self):
        reference = AlignedReference(Lap(), 5000)
        view = live()
        frame = reference.window(view)
        json.dumps(frame, allow_nan=False)
        self.assertAlmostEqual(frame['current'][-1]['offset'], 0)
        self.assertAlmostEqual(frame['current'][0]['offset'], -5)
        self.assertGreater(max(row['offset'] for row in frame['reference']), 1.9)
        self.assertLessEqual(max(row['offset'] for row in frame['current']), 0)
        changed_clock = {**view, 'points': [{**p, 'session_time': p['session_time']+8000} for p in view['points']]}
        self.assertEqual(reference.window(changed_clock), frame)

    def test_lap_wrap_keeps_previous_lap_and_reset_discards_old_inputs(self):
        reference = AlignedReference(Lap(), 5000)
        view = live(distance_pct=.01, lap_number=3)
        view['points'] = [{'session_time': 1., 'lap_number': 2, 'distance_pct': .99, 'brake': .2, 'on_pit_road': False},
                          {'session_time': 1.03, 'lap_number': 3, 'distance_pct': .01, 'brake': .3, 'on_pit_road': False}]
        frame = reference.window(view)
        self.assertAlmostEqual(frame['current'][0]['offset'], -2)
        view = live(distance_pct=.5)
        view['points'] = [{'session_time': 1., 'lap_number': 2, 'distance_pct': .7, 'brake': .9, 'on_pit_road': False},
                          {'session_time': 1.03, 'lap_number': 2, 'distance_pct': .5, 'brake': .3, 'on_pit_road': False}]
        self.assertEqual(len(reference.window(view)['current']), 1)

    def test_pit_inputs_are_not_carried_into_the_track_comparison_after_rejoining(self):
        view = live()
        view['points'][100]['on_pit_road'] = True
        frame = AlignedReference(Lap(), 5000).window(view)
        self.assertEqual(len(frame['current']), 50)
        self.assertTrue(frame['current'][0]['break'])

    def test_missing_reference_intervals_are_not_interpolated(self):
        reference = AlignedReference(Lap(), 5000)
        reference.times[(reference.distance > .49) & (reference.distance < .51)] = np.nan
        self.assertIsNone(reference.window(live()))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)
        self.fetch = Mock(side_effect=AssertionError('No network expected'))
        self.loader = Mock(return_value=Lap())
        self.service = OverlayReference(self.path, self.loader, fetch=self.fetch)

    def tearDown(self):
        self.service.close()
        self.folder.cleanup()

    def cache(self, meta=None):
        meta = meta or metadata()
        path = self.path/f"Garage 61 - Test - Car - Track - 01.40.000 - {meta['lap_id']}.csv"
        path.write_text('Speed,LapDistPct,Brake,Throttle,SteeringWheelAngle\n'+
                        '\n'.join(f'50,{i/1000},0.2,0.8,0' for i in range(1001)))
        path.with_suffix('.meta.json').write_text(json.dumps(meta))
        return path

    def test_invalid_cached_csv_is_skipped_for_a_checked_reference(self):
        bad = self.cache(metadata(lap_id='BAD', lap_time_s=99.9))
        bad.write_text(bad.read_text().replace('0.2,0.8', '2,0.8'))
        self.cache(metadata(lap_id='GOOD'))
        self.service.observe(live(), True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        self.assertEqual(self.service.status()['lap_id'], 'GOOD')
        self.assertGreater(self.service.status()['quality']['samples'], 250)
        self.loader.assert_called_once()
        self.fetch.assert_not_called()

    def test_fuel_bucket_change_at_finish_keeps_a_suitable_reference(self):
        self.cache()
        self.service.observe(live(fuel_l=40.1), True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        reference = self.service.reference
        self.service.observe(live(fuel_l=39.9, lap_number=3, distance_pct=.0001), True)
        self.assertIs(self.service.reference, reference)
        self.assertEqual(self.service.status()['state'], 'READY')
        self.assertIsNotNone(self.service.enrich(live(distance_pct=.0001))['comparison'])
        self.loader.assert_called_once()

    def test_cached_reference_is_reused_without_network_or_reloading_every_tick(self):
        self.cache()
        for _ in range(30):
            self.service.observe(live(), True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        for _ in range(30):
            self.service.observe(live(), True)
        self.assertEqual(self.loader.call_count, 1)
        self.fetch.assert_not_called()
        self.assertTrue(self.service.enrich(live())['comparison'])
        self.assertEqual(self.service.status()['condition_note'], '타이어 조건 미확인')

    def test_sdk_tire_change_clears_and_reselects_the_reference(self):
        self.cache()
        self.service.observe(live(tire_compound=0), True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        original = self.service.reference
        started, release = threading.Event(), threading.Event()
        def load(path):
            started.set()
            release.wait(2)
            return Lap()
        self.loader.side_effect = load
        try:
            self.service.observe(live(tire_compound=1), True)
            self.assertTrue(started.wait(1))
            self.assertEqual(self.service.status()['state'], 'LOADING')
            self.assertIsNone(self.service.enrich(live(tire_compound=1))['comparison'])
        finally:
            release.set()
        wait_for(lambda: self.service.status()['state'] == 'READY')
        self.assertIsNot(self.service.reference, original)
        self.assertTrue(self.service.enrich(live(tire_compound=1))['comparison'])
        self.assertEqual(self.loader.call_count, 2)
        self.fetch.assert_not_called()

    def test_slow_load_does_not_block_observation_and_disabled_result_is_discarded(self):
        self.cache()
        started, release = threading.Event(), threading.Event()
        def load(path):
            started.set()
            release.wait(2)
            return Lap()
        self.loader.side_effect = load
        before = time.monotonic()
        self.service.observe(live(), True)
        self.assertLess(time.monotonic()-before, .1)
        self.assertTrue(started.wait(1))
        self.service.observe(live(), False)
        release.set()
        self.service.close()
        self.assertEqual(self.service.status()['state'], 'IDLE')
        self.assertIsNone(self.service.enrich(live())['comparison'])

    def test_missing_surface_clears_a_previous_reference(self):
        self.cache()
        self.service.observe(live(), True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        self.service.observe(live(track_wetness=None), True)
        self.assertIsNone(self.service.reference)
        self.assertIsNone(self.service.key)

    def test_pit_keeps_prepared_reference_but_hides_comparison_and_unknown_layout_clears_it(self):
        self.cache()
        self.service.observe(live(), True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        self.service.observe(live(on_pit_road=True), True)
        self.assertIsNotNone(self.service.reference)
        self.assertIsNone(self.service.enrich(live(on_pit_road=True))['comparison'])
        gear = self.service.enrich(live(on_pit_road=True, gear=2))['gear_comparison']
        self.assertEqual(gear, {'current': 2, 'reference': None, 'text': '2', 'color': 'muted'})
        view = live()
        view['identity'] = {'car_id': '10'}
        self.service.observe(view, True)
        self.assertEqual(self.service.status()['state'], 'UNAVAILABLE')
        self.assertIsNone(self.service.reference)

    def test_reference_is_ready_before_cockpit_entry_and_pit_exit_without_reloading(self):
        self.cache()
        garage = live(on_track=False, on_pit_road=True, session_state=1)
        self.service.observe(garage, True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        self.assertIsNone(self.service.enrich(garage)['comparison'])
        self.assertIsNone(self.service.enrich(garage)['feedback'])
        self.service.observe(live(), True)
        self.assertTrue(self.service.enrich(live())['comparison'])
        self.assertEqual(self.loader.call_count, 1)
        self.fetch.assert_not_called()
        for changes in ({'is_replay': True}, {'session_state': 6}):
            self.service.observe(live(**changes), True)
            self.assertIsNone(self.service.reference)

    def test_controller_prepares_in_garage_without_starting_a_window(self):
        reference = Mock()
        reference.status.return_value = {'state': 'READY'}
        controller = OverlayController(self.path/'overlay.json', reference=reference)
        controller._start_child = Mock()
        controller.command = Mock()
        garage = live(on_track=False, on_pit_road=True, session_state=1)
        controller.sync(garage)
        reference.observe.assert_called_once_with(garage, True)
        controller._start_child.assert_not_called()
        controller.command.assert_not_called()

    def test_sdk_garage_metadata_prepares_local_reference_without_driving_samples(self):
        from live_telemetry import CHANNELS, LiveTelemetry
        self.cache()
        reader = LiveTelemetry(clock=lambda: 100.)
        data = {CHANNELS[key]: value for key, value in {
            'is_replay': False, 'on_track': False, 'session_state': 1, 'session_num': 0,
            'session_time': 20., 'track_wetness': 1, 'track_temp_c': 30., 'air_temp_c': 24.,
            'brake': .9, 'fuel_l': 30., 'lap_best_s': 50., 'tire_compound': 0}.items()}
        data.update(WeekendInfo={'SessionID': 1, 'TrackID': 20, 'TrackLength': '5 km'},
                    DriverInfo={'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'CarID': 10}]})
        # Mock the SDK mapping separately; no real SDK or Windows keyboard calls.
        from unittest.mock import MagicMock
        sdk = MagicMock()
        sdk.is_initialized = sdk.is_connected = True
        sdk.var_headers_names = list(data)
        sdk.__getitem__.side_effect = data.__getitem__
        reader.sdk_factory = lambda: sdk
        reader._poll_hotkey = Mock()
        captured = []
        sdk.unfreeze_var_buffer_latest.side_effect = lambda: captured.append(reader.overlay_state())
        reader.stop_event.wait = lambda _: reader.stop_event.set()
        reader._loop()
        view = captured[0]
        self.assertTrue(view['connected'])
        self.assertFalse(view['snapshot']['on_track'])
        self.assertEqual(view['track_length_m'], 5000.)
        self.assertFalse(reader.samples)
        self.assertFalse(reader.context()['connected'])
        self.assertEqual(view['points'], [])
        for key in ('brake', 'throttle', 'fuel_l', 'tire_compound', 'lap_best_s'):
            self.assertNotIn(key, view['snapshot'])
        self.service.observe(view, True)
        wait_for(lambda: self.service.status()['state'] == 'READY')
        self.assertIsNone(self.service.enrich(view)['comparison'])
        self.fetch.assert_not_called()
        self.assertFalse(reader.overlay_state()['connected'])

    def test_surface_change_while_loading_discards_the_old_result(self):
        self.cache()
        started, release = threading.Event(), threading.Event()
        def load(path):
            started.set()
            release.wait(2)
            return Lap()
        self.loader.side_effect = load
        self.service.observe(live(), True)
        self.assertTrue(started.wait(1))
        with patch('overlay_reference.g61._token_from_sources', return_value=('', '')):
            self.service.observe(live(track_wetness=4), True)
            release.set()
            wait_for(lambda: self.service.status()['state'] == 'TOKEN_REQUIRED')
        self.assertIsNone(self.service.reference)

    def test_auth_failure_is_not_reported_as_a_missing_matching_lap(self):
        self.fetch.side_effect = HTTPError('https://garage61.net/api/v1/cars', 403, 'Forbidden', {}, None)
        with patch('overlay_reference.g61._token_from_sources', return_value=('test-token', 'test')), \
             patch.dict('os.environ', {'GARAGE61_AUTO_COLLECT': '1'}):
            self.service.observe(live(), True)
            wait_for(lambda: self.service.status()['state'] == 'AUTH_REQUIRED')
        self.fetch.assert_called_once()

    def test_long_retry_after_is_preserved_across_a_session_change_without_sleeping(self):
        self.service.clock = lambda: 100.
        failure = HTTPError('https://garage61.net/api/v1/laps', 429, 'Limit', {'Retry-After': '3600'}, io.BytesIO(b'{}'))
        opener = Mock()
        opener.open.side_effect = failure
        with patch('overlay_reference.request.build_opener', return_value=opener):
            with self.assertRaises(HTTPError):
                self.service._fetch('laps', 'test-token')
        self.assertEqual(self.service.network_retry_at, 3700.)
        with patch('overlay_reference.g61._token_from_sources', return_value=('test-token', 'test')), \
             patch.dict('os.environ', {'GARAGE61_AUTO_COLLECT': '1'}):
            self.service.observe(live(), True)
            wait_for(lambda: self.service.status()['state'] == 'RATE_LIMIT')
        self.assertEqual(self.service.retry_at, 3700.)
        self.fetch.assert_not_called()

    def test_token_missing_is_reported_without_repeated_network_requests(self):
        with patch('overlay_reference.g61._token_from_sources', return_value=('', '')):
            self.service.observe(live(), True)
            wait_for(lambda: self.service.status()['state'] == 'TOKEN_REQUIRED')
            self.service.observe(live(), True)
        self.fetch.assert_not_called()

    def test_download_uses_exact_api_ids_filters_conditions_and_caches_one_real_lap(self):
        meta = metadata()
        api_lap = {'id': 'TESTLAP', 'clean': True, 'canViewTelemetry': True, 'lapTime': 100,
                   'startTime': meta['start_time'], 'trackWetness': 0, 'fuelLevel': 30,
                   'trackTemp': 30, 'airTemp': 24, 'tireCompound': 1,
                   'car': {'platform_id': 10, 'name': 'Car'},
                   'track': {'platform_id': 20, 'name': 'Track'}, 'driver': {'firstName': 'Test'}}
        csv = ('Speed,LapDistPct,Brake,Throttle,SteeringWheelAngle\n'+
               '\n'.join(f'50,{i/1000},0.2,0.8,0' for i in range(1001))).encode()
        def fetch(path, token, query=None):
            self.assertEqual(token, 'test-token')
            if path == 'cars':
                return json.dumps({'items': [{'id': 110, 'platform_id': 10}]}).encode()
            if path == 'tracks':
                return json.dumps({'items': [{'id': 120, 'platform_id': 20}]}).encode()
            if path == 'laps':
                self.assertEqual((query['cars'], query['tracks']), (110, 120))
                return json.dumps({'items': [{**api_lap, 'id': 'BAD', 'lapTime': 99.9}, api_lap,
                                             {**api_lap, 'id': 'WET', 'trackWetness': 40}]}).encode()
            if path == 'laps/BAD/csv':
                return csv.replace(b'0.2,0.8', b'2,0.8')
            self.assertEqual(path, 'laps/TESTLAP/csv')
            return csv
        self.fetch.side_effect = fetch
        with patch('overlay_reference.g61._token_from_sources', return_value=('test-token', 'test')), \
             patch.dict('os.environ', {'GARAGE61_AUTO_COLLECT': '1'}):
            self.service.observe(live(), True)
            wait_for(lambda: self.service.status()['state'] == 'READY')
        self.assertEqual(len(list((self.path/'live').glob('*.csv'))), 1)
        self.assertEqual(self.service.status()['lap_id'], 'TESTLAP')
        self.assertEqual(self.service.status()['condition_note'], '타이어 조건 미확인')
        self.assertEqual(self.fetch.call_count, 5)
        self.assertFalse(list((self.path/'live').glob('*BAD*')))
        self.assertFalse(list((self.path/'live').glob('.checking-*')))

    def test_controller_forwards_reference_to_child_but_never_loads_during_preview(self):
        reference = Mock()
        reference.status.return_value = {'state': 'READY'}
        reference.enrich.side_effect = lambda view: {**view, 'comparison': {'reference': [], 'current': []},
                                                     'feedback': {'delta_s': .18, 'brake_delta_m': 8.},
                                                     'gear_comparison': {'current': 2, 'reference': 3}}
        controller = OverlayController(self.path/'overlay.json', reference=reference)
        controller._start_child = Mock()
        controller.command = Mock()
        controller.sync(live())
        self.assertIn('comparison', controller.command.call_args.args[1])
        self.assertEqual(controller.command.call_args.args[1]['feedback'], {'delta_s': .18, 'brake_delta_m': 8.})
        self.assertEqual(controller.command.call_args.args[1]['gear_comparison'], {'current': 2, 'reference': 3})
        controller.process = Mock()
        controller.process.poll.return_value = None
        controller.preview = True
        controller.sync(live())
        self.assertFalse(reference.observe.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
