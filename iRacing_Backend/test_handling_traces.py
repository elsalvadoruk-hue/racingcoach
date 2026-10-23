"""Corner trace fidelity and the real ZIP budget, with synthetic telemetry/stores only."""
import io
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import zipfile

import numpy as np

import handling_traces as traces
import session_log
import session_log_extras
from session_log import Extra
from telemetry_types import LapSegment
from test_session_log import RUN, SessionLogTests as _SessionLogTests


def fixture(count=6, game='iracing'):
    axis = np.arange(601) / 60
    laps = [LapSegment(i + 1, i * 601, (i + 1) * 601 - 1, True, True, 'VALID', 10.,
                       0., 1., 1., 0, None) for i in range(count)]
    channels = {k: np.tile(np.sin(axis) * .1 + 1, count) for k in traces.UNITS}
    channels['SessionTime'] = np.concatenate([axis + 100 + 11 * i for i in range(count)])
    channels['LapDistPct'] = np.tile(axis / 10, count)
    analyzer = SimpleNamespace(valid_laps=laps, channels=channels, track_length_m=3000.,
                               telemetry=SimpleNamespace(tick_rate=60, metadata={'game': game}))
    summary = {'recording': {'game': game, 'track_length_m': 3000., 'steering_scale': 'MEASURED'},
               'corners': [{'id': 'C1', 'entry_pct': .2, 'apex_pct': .4, 'exit_pct': .6}],
               'corner_passes': [], 'lap_conditions': [],
               'handling': {'method': 'personal_corner_phase_v3'},
               'handling_observations': [
                   {'corner_id': 'C1', 'phase': 'drive', 'lap': i + 1, 'observed': i % 2 == 0,
                    'unknown_reasons': ['inputs_not_comparable'] if i % 3 == 0 else []} for i in range(count)]}
    return analyzer, summary


class TraceTests(unittest.TestCase):
    def test_native_values_precision_and_non_event_laps_survive_round_trip(self):
        analyzer, summary = fixture()
        group = traces.unpack(traces.pack(next(traces.groups(analyzer, summary, 'seed'))))
        self.assertEqual(len(group['passes']), 6)
        for row in group['passes']:
            start = (row['lap'] - 1) * 601 + row['sample_start']
            end = start + row['samples']
            self.assertEqual(row['channels']['YawRate'], analyzer.channels['YawRate'][start:end].tolist())
            self.assertAlmostEqual(row['time_s'][1] - row['time_s'][0], 1 / 60)
        self.assertEqual({p['observations'][0]['observed'] for p in group['passes']}, {False, True})
        self.assertFalse(group['full_recording_replay'])
        self.assertEqual(group['units']['LongAccel'], 'm/s^2')

    def test_gaps_duplicates_missing_values_and_absent_channel_are_not_filled(self):
        analyzer, summary = fixture()
        del analyzer.channels['LongAccel']
        analyzer.channels['YawRate'][200] = np.nan
        analyzer.channels['SessionTime'][220:] += 1
        analyzer.channels['SessionTime'][240] = analyzer.channels['SessionTime'][239]
        analyzer.channels['LapDistPct'][250] = np.nan
        group = next(traces.groups(analyzer, summary, 'seed'))
        row = group['passes'][0]
        self.assertIsNone(row['channels']['LongAccel'])
        self.assertIn(None, row['channels']['YawRate'])
        self.assertIn(None, row['distance_pct'])
        self.assertGreaterEqual(row['connected'].count(False), 4)
        self.assertTrue(any(t == 0 for t in np.diff(row['time_s'])))
        self.assertTrue(any(t > .5 for t in np.diff(row['time_s'])))

    def test_laps_are_evenly_spaced_and_selection_does_not_follow_flags(self):
        analyzer, summary = fixture(20, 'lmu')
        first = list(traces.groups(analyzer, summary, 'seed'))
        again = list(traces.groups(analyzer, {**summary, 'handling_observations': []}, 'seed'))
        self.assertEqual([p['lap'] for p in first[0]['passes']], [1, 3, 6, 9, 11, 14, 17, 20])
        self.assertEqual([p['lap'] for p in first[0]['passes']], [p['lap'] for p in again[0]['passes']])
        self.assertTrue(all(p['source'] == 'RESAMPLED' for p in first[0]['passes']))
        self.assertEqual(first[0]['passes'][0]['channel_provenance']['YawRate'],
                         'DERIVED_FROM_GPS_SPEED_RESAMPLED')

    def test_fewer_than_four_laps_or_missing_core_channels_yields_no_bundle(self):
        analyzer, summary = fixture(3)
        self.assertEqual(list(traces.groups(analyzer, summary, 'seed')), [])
        analyzer, summary = fixture()
        del analyzer.channels['YawRate']
        self.assertEqual(list(traces.groups(analyzer, summary, 'seed')), [])


class BudgetTests(unittest.TestCase):
    setUp = _SessionLogTests.setUp

    @staticmethod
    def provider(supplement, size=1000):
        return lambda run: Extra('drive_summary', {'drive_summary.bin': b'x' * size}, supplement=supplement)

    @staticmethod
    def bundle(index, size):
        return Extra('handling_traces', {f'handling-traces/{index:03}.json.xz': os.urandom(size)},
                     {'passes': 4, 'samples': 100})

    def test_nearby_files_that_fit_are_kept_and_detail_is_not_evaluated(self):
        detail = Mock(side_effect=AssertionError('detail must stay lazy'))
        nearby = lambda run: Extra('nearby_cars', {'traffic.bin': b'traffic'})
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[nearby, self.provider(detail)], limit=50_000)
        detail.assert_not_called()
        archive = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(archive.read('traffic.bin'), b'traffic')
        self.assertFalse(any(n.startswith('handling-traces/') for n in archive.namelist()))

    def test_size_dropped_nearby_files_allow_whole_bundles_only_and_keep_base_parts(self):
        base = self.provider(lambda: iter([self.bundle(0, 20_000), self.bundle(1, 40_000), self.bundle(2, 10_000)]))
        nearby = lambda run: Extra('nearby_cars', {'traffic.bin': os.urandom(60_000)})
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[nearby, base], limit=50_000)
        self.assertLessEqual(len(data), 50_000)
        manifest, radio, reports = session_log.read(data)
        _, plain = session_log.export(self.store, RUN, '2.1.0')
        self.assertEqual((radio, reports), session_log.read(plain)[1:])
        self.assertFalse(manifest['extras']['nearby_cars']['included'])
        archive = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(archive.read('drive_summary.bin'), b'x' * 1000)
        self.assertEqual(len([n for n in archive.namelist() if n.startswith('handling-traces/')]), 2)
        self.assertEqual(manifest['extras']['handling_traces']['passes'], 8)
        self.assertEqual(manifest['extras']['handling_traces']['skipped_groups'], 1)

    def test_real_limit_includes_manifest_and_zip_overhead(self):
        base = self.provider(lambda: iter([self.bundle(0, 700_000), self.bundle(1, 700_000)]))
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[base], limit=session_log.SEND_LIMIT)
        self.assertLessEqual(len(data), 1_400_000)
        self.assertEqual(session_log.read(data)[0]['extras']['handling_traces']['groups'], 1)

    def test_work_is_capped_and_no_metadata_overflow_displaces_the_base(self):
        calls = []
        def bundles():
            for i in range(100):
                calls.append(i)
                yield self.bundle(i, 100)
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider(bundles)], limit=50_000)
        self.assertEqual(len(calls), session_log.MAX_SUPPLEMENT_GROUPS)
        self.assertEqual(session_log.read(data)[0]['extras']['handling_traces']['groups'], len(calls))
        with unittest.mock.patch.object(session_log.time, 'time', return_value=1_700_000_000):
            _, plain = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider(None)])
            detail = Mock(side_effect=AssertionError('no room for metadata'))
            _, limited = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider(detail)], limit=len(plain) + 5)
        self.assertLessEqual(len(limited), len(plain) + 5)
        detail.assert_not_called()

    def test_detail_failure_or_no_room_keeps_the_original_files(self):
        def broken():
            raise RuntimeError('synthetic')
        for detail in (broken, lambda: iter([self.bundle(0, 50_000)])):
            _, data = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider(detail)], limit=5000)
            self.assertLessEqual(len(data), 5000)
            self.assertEqual(zipfile.ZipFile(io.BytesIO(data)).read('drive_summary.bin'), b'x' * 1000)
        detail = Mock()
        _, data = session_log.export(self.store, RUN, '2.1.0', extras=[self.provider(detail, 60_000)], limit=5000)
        detail.assert_not_called()  # the ordinary summary was dropped; no replacement with traces

    def test_an_oversized_base_is_not_sent_and_file_download_still_works(self):
        with self.assertRaisesRegex(ValueError, '기본 로그'):
            session_log.export(self.store, RUN, '2.1.0', limit=100)
        self.assertIsNotNone(session_log.export(self.store, RUN, '2.1.0'))

    def test_provider_wiring_readback_privacy_and_driving_deferral(self):
        analyzer, summary = fixture()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private-file.ibt'
            path.touch()
            run = {'run_id': RUN, 'started_at': time.time() - 60, 'ended_at': time.time(), 'identity': {}}
            result = {'laps': [], 'performance': {'corners': summary['corners']}}
            callback = Mock(side_effect=lambda path, summary, seed: traces.groups(analyzer, summary, seed))
            driving = [False]
            provider = session_log_extras.own_drive(lambda: [path], lambda path: {}, lambda path: (result, {}, None),
                                                  traces=callback, driving=lambda: driving[0])
            made = provider(run)
            callback.assert_not_called()
            _, data = session_log.export(self.store, RUN, '2.1.0', extras=[lambda run: made],
                                         limit=session_log.SEND_LIMIT)
            parts = session_log.read_extras(data)
            self.assertEqual(len(parts['handling_traces'][0]['passes']), 6)
            self.assertNotIn('private-file', json.dumps(parts))
            self.assertNotIn(str(path), json.dumps(parts))
            driving[0] = True
            with self.assertRaises(session_log.Deferred):
                next(made.supplement())

    def test_recording_changed_after_summary_is_not_mixed_with_new_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.ibt'
            path.touch()
            provider = session_log_extras.own_drive(lambda: [path], lambda p: {}, lambda p: ({'laps': []}, {}, None),
                                                  traces=Mock(side_effect=AssertionError('changed source')))
            made = provider({'run_id': RUN, 'started_at': time.time() - 60, 'ended_at': time.time(), 'identity': {}})
            path.write_bytes(b'changed')
            self.assertEqual(list(made.supplement()), [])


del _SessionLogTests
