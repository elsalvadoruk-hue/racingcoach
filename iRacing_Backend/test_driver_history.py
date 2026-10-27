"""Driver history invariants using synthetic summaries and temporary SQLite only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from driver_history import DriverHistoryStore


def identity(value):
    return hashlib.sha256(value.encode()).hexdigest()


def source(name='a', *, drive='drive', game='iracing', intervals=None, **changes):
    observation = {
        'drive_id': identity(drive), 'identity_status': 'NATIVE', 'game': game,
        'car_key': 'synthetic-car', 'track_key': 'synthetic-track', 'layout_key': 'full',
        'session_type': 'PRACTICE', 'session_type_source': 'NATIVE_SESSION_NUM',
        'recorded_at': '2026-10-27T00:00:00Z', 'recorded_at_source': 'IBT_DISK_SUBHEADER',
        'coverage': {'time_basis': 'NATIVE_SESSION_TIME', 'intervals': intervals or [[0., 10.]],
                     'status': 'OBSERVED', 'sample_count': 101, 'reasons': []},
        'validity_status': 'UNKNOWN', 'conditions': {},
    }
    observation.update(changes)
    return {'schema_version': 1, 'source_id': identity(name), 'game': game,
            'observations': [observation]}


class DriverHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='driver-history-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'nested' / 'history.sqlite3'
        self.store = DriverHistoryStore(self.path)

    def test_constructor_and_reads_never_create_database_or_parent(self):
        self.assertEqual(self.store.list_drives(), [])
        self.assertIsNone(self.store.get_drive(identity('missing')))
        self.assertEqual(self.store.status()['source_count'], 0)
        self.assertFalse(self.path.parent.exists())

    def test_default_path_uses_explicit_environment_before_data_folder(self):
        with patch.dict(os.environ, {'IRACING_DRIVER_HISTORY_PATH': str(self.path)}):
            self.assertEqual(DriverHistoryStore().path, self.path)
        with patch.dict(os.environ, {'IRACING_DRIVER_HISTORY_PATH': ''}), patch('app_settings.data_dir', return_value=Path(self.temp.name)):
            self.assertEqual(DriverHistoryStore().path, Path(self.temp.name) / 'driver-history.sqlite3')
        self.assertFalse(self.path.exists())

    def test_reanalysis_same_source_is_idempotent_and_survives_reopen(self):
        payload = source()
        self.assertEqual(self.store.record_source(payload), {'saved': True, 'source_id': identity('a'), 'observation_count': 1})
        before = self.store.get_drive(identity('drive'))
        self.store.record_source(payload)
        after = DriverHistoryStore(self.path).get_drive(identity('drive'))
        self.assertEqual(before, after)
        self.assertEqual(self.store.status()['source_count'], 1)
        self.assertEqual(after['source_ids'], [identity('a')])

    def test_source_replacement_prunes_deleted_segments_and_empty_source(self):
        payload = source()
        payload['observations'].append(source(drive='second')['observations'][0])
        self.store.record_source(payload)
        self.assertEqual(len(self.store.list_drives()), 2)
        self.store.record_source(source(drive='second'))
        self.assertIsNone(self.store.get_drive(identity('drive')))
        payload['observations'] = []
        self.store.record_source(payload)
        self.assertEqual(self.store.list_drives(), [])
        self.assertEqual(self.store.status()['source_count'], 1)
        self.assertEqual(self.store.status()['observation_count'], 0)

    def test_split_and_overlapping_files_union_intervals_without_filling_gaps(self):
        self.store.record_source(source('a', intervals=[[0, 10], [20, 30]]))
        self.store.record_source(source('b', intervals=[[5, 15], [30, 40]]))
        result = self.store.get_drive(identity('drive'))
        self.assertEqual(result['coverage']['intervals'], [[0., 15.], [20., 40.]])
        self.assertEqual(result['source_count'], 2)
        self.assertIsNone(result['coverage']['sample_count'])
        self.assertEqual(len(result['observations']), 2)
        self.assertEqual(self.store.status()['native_session_count'], 1)

    def test_different_time_bases_are_kept_separate(self):
        self.store.record_source(source('a'))
        second = source('b')
        second['observations'][0]['coverage']['time_basis'] = 'UTC'
        self.store.record_source(second)
        coverage = self.store.get_drive(identity('drive'))['coverage']
        self.assertEqual(coverage['time_basis'], 'MIXED')
        self.assertEqual(coverage['intervals'], [])
        self.assertEqual(set(coverage['intervals_by_basis']), {'UTC', 'NATIVE_SESSION_TIME'})

    def test_unknown_dates_validity_and_source_only_scope_are_not_promoted(self):
        payload = source(identity_status='SOURCE_ONLY', session_type='UNKNOWN',
                         session_type_source='UNKNOWN', recorded_at=None,
                         recorded_at_source='UNKNOWN', validity_status='UNVERIFIED')
        payload['observations'][0]['coverage'].update(status='PARTIAL', reasons=['SESSION_KIND_UNKNOWN'])
        self.store.record_source(payload)
        result = self.store.get_drive(identity('drive'))
        self.assertIsNone(result['recorded_at'])
        self.assertEqual(result['identity_status'], 'SOURCE_ONLY')
        self.assertEqual(result['validity_status'], 'UNVERIFIED')
        self.assertEqual(self.store.status()['native_session_count'], 0)
        self.assertEqual(self.store.status()['source_only_observation_count'], 1)
        self.assertEqual(self.store.status()['unknown_recorded_at_count'], 1)

    def test_native_identity_requires_known_car_and_track(self):
        for changes in ({'car_key': ''}, {'track_key': 'UNKNOWN'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.record_source(source(**changes))
        self.store.record_source(source(identity_status='SOURCE_ONLY', car_key='', track_key='UNKNOWN'))
        self.assertEqual(self.store.status()['source_only_observation_count'], 1)

    def test_source_only_identifier_cannot_merge_different_sources(self):
        self.store.record_source(source(identity_status='SOURCE_ONLY'))
        with self.assertRaises(ValueError):
            self.store.record_source(source('b', identity_status='SOURCE_ONLY'))
        self.assertEqual(self.store.status()['source_count'], 1)

    def test_same_identifier_cannot_merge_different_game_car_track_or_layout(self):
        self.store.record_source(source())
        for change in ({'game': 'lmu'}, {'car_key': 'other'}, {'track_key': 'other'},
                       {'layout_key': 'other'}, {'identity_status': 'SOURCE_ONLY'}, {'session_type': 'RACE'}):
            payload = source('b', **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.record_source(payload)
        self.assertEqual(self.store.status()['source_count'], 1)

    def test_same_source_cannot_switch_game(self):
        self.store.record_source(source())
        with self.assertRaises(ValueError):
            self.store.record_source(source(game='lmu', drive='lmu'))

    def test_unknown_fields_and_sensitive_text_are_not_persisted(self):
        payload = source()
        payload['private_path'] = r'C:\private\recording.ibt'
        observation = payload['observations'][0]
        observation['driver_name'] = 'PRIVATE DRIVER'
        observation['session_yaml'] = 'PRIVATE YAML'
        observation['conditions'] = {'private_name': 'PRIVATE CONDITION', 'fuel_level_l': 20.,
                                     'fuel_level_l_source': 'NATIVE_CHANNEL'}
        observation['coverage']['raw_samples'] = 'PRIVATE SAMPLES'
        self.store.record_source(payload)
        result = json.dumps(self.store.get_drive(identity('drive')))
        self.assertNotIn('PRIVATE', result)
        self.assertNotIn('private_path', result)
        self.assertIn('fuel_level_l', result)
        self.assertNotIn(b'PRIVATE', self.path.read_bytes())

    def test_aggregate_labels_skip_an_empty_earlier_source(self):
        payloads = sorted([source('a'), source('b')], key=lambda item: item['source_id'])
        payloads[0]['observations'][0]['labels'] = {'car': '', 'track': '', 'layout': ''}
        expected = {'car': 'Synthetic GT3', 'track': 'Synthetic Track', 'layout': 'GP'}
        payloads[1]['observations'][0]['labels'] = expected
        for item in payloads:
            self.store.record_source(item)
        self.assertEqual(self.store.get_drive(identity('drive'))['labels'], expected)

    def test_current_metrics_roundtrip_preserves_unknown_replay_and_old_payload_shape(self):
        metrics = {'schema_version': 1, 'calculation_version': 'observed-metrics-v2',
            'driving': {'status': 'PARTIAL', 'time_s': 1., 'distance_m': 10., 'intervals': [[0., 1.]],
                        'reason_codes': ['REPLAY_STATE_UNAVAILABLE'], 'replay_status': 'UNKNOWN'},
            'laps': [{'lap_number': 1, 'start_s': 0., 'end_s': 1., 'lap_time_s': 1., 'measurable': True,
                      'conditions': {}, 'official_validity': 'UNKNOWN', 'replay_status': 'UNKNOWN',
                      'reason_codes': ['REPLAY_STATE_UNAVAILABLE']}]}
        payload = source()
        payload['observations'][0]['metrics'] = metrics
        self.store.record_source(payload)
        self.assertEqual(self.store.get_drive(identity('drive'))['observations'][0]['metrics'], metrics)
        metrics.pop('calculation_version')
        metrics['driving'].pop('replay_status')
        metrics['laps'][0].pop('replay_status')
        metrics['laps'][0].pop('reason_codes')
        self.store.record_source(payload)
        self.assertEqual(self.store.get_drive(identity('drive'))['observations'][0]['metrics'], metrics)

    def test_quality_and_incidents_roundtrip_strip_unknown_fields_and_reanalysis_is_idempotent(self):
        from driver_history_metrics import build_observation_metrics
        from test_driver_history_metrics import DriverHistoryMetricsTests
        import numpy as np
        telemetry=DriverHistoryMetricsTests().telemetry()
        telemetry.channels['PlayerCarMyIncidentCount']=np.array([0]*5+[1]*6)
        value=source()
        value['observations'][0]['metrics']=build_observation_metrics(telemetry,value['observations'][0])
        value['observations'][0]['metrics']['incidents']['private_path']='PRIVATE SOURCE'
        value['observations'][0]['metrics']['lap_quality']['completed_laps'][0]['private_name']='PRIVATE DRIVER'
        self.store.record_source(value)
        before=self.store.get_drive(identity('drive'))
        self.store.record_source(value)
        after=self.store.get_drive(identity('drive'))
        self.assertEqual(before,after)
        metrics=after['observations'][0]['metrics']
        self.assertEqual(metrics['incidents']['increase_points'],1)
        self.assertEqual(metrics['lap_quality']['completed_laps'][0]['analysis_validity'],'VALID')
        self.assertEqual(metrics['lap_quality']['official_validity'],'UNKNOWN')
        self.assertNotIn('PRIVATE',json.dumps(metrics))

    def test_quality_and_counter_validation_rejects_inconsistent_or_malformed_facts_atomically(self):
        from driver_history_metrics import build_observation_metrics
        from test_driver_history_metrics import DriverHistoryMetricsTests
        import numpy as np
        telemetry=DriverHistoryMetricsTests().telemetry()
        telemetry.channels['PlayerCarMyIncidentCount']=np.array([0]*5+[1]*6)
        value=source()
        value['observations'][0]['metrics']=build_observation_metrics(telemetry,value['observations'][0])
        self.store.record_source(value)
        before=self.store.get_drive(identity('drive'))
        changes=[('increase_points',2),('increase_points',True),('observed_time_s',2.),
            ('observed_time_s',float('nan')),('scope','DRIVING_TIME'),('reason_codes',['PRIVATE/path']),
            ('events',[dict(start_s=.4,end_s=.5,points=-1)]),
            ('events',[dict(start_s=.4,end_s=.5,points=1),dict(start_s=.4,end_s=.5,points=1)]),
            ('intervals',[[0.,1.],[.5,2.]])]
        for key,invalid in changes:
            broken=deepcopy(value)
            broken['observations'][0]['metrics']['incidents'][key]=invalid
            with self.subTest(key=key),self.assertRaises(ValueError):self.store.record_source(broken)
        for key,invalid in [('authority','OFFICIAL'),('official_validity','VALID'),('status','OBSERVED')]:
            broken=deepcopy(value)
            quality=broken['observations'][0]['metrics']['lap_quality']
            quality[key]=invalid
            if key=='status':quality['completed_laps'][0]['analysis_validity']='UNKNOWN'
            with self.subTest(key=key),self.assertRaises(ValueError):self.store.record_source(broken)
        for field in ['lap_quality','incidents']:
            broken=deepcopy(value)
            broken['observations'][0]['metrics'].pop(field)
            with self.subTest(field=field),self.assertRaises(ValueError):self.store.record_source(broken)
        self.assertEqual(before,self.store.get_drive(identity('drive')))

    def test_metrics_new_fields_reject_invalid_enums_and_inconsistent_replay_claims(self):
        from driver_history import _metrics
        metrics = {'schema_version': 1, 'calculation_version': 'observed-metrics-v2',
            'driving': {'status': 'PARTIAL', 'time_s': 1., 'distance_m': 10., 'intervals': [[0., 1.]],
                        'reason_codes': ['REPLAY_STATE_UNAVAILABLE'], 'replay_status': 'UNKNOWN'},
            'laps': [{'lap_number': 1, 'start_s': 0., 'end_s': 1., 'lap_time_s': 1., 'measurable': True,
                      'conditions': {}, 'official_validity': 'UNKNOWN', 'replay_status': 'UNKNOWN',
                      'reason_codes': ['REPLAY_STATE_UNAVAILABLE']}]}
        invalid = []
        for value in [None, 'observed-metrics-v5', 2]:
            item = deepcopy(metrics); item['calculation_version'] = value; invalid.append(item)
        for value in [None, 'NON_REPLAY', 'PRIVATE replay', 0]:
            for target in ['driving', 'lap']:
                item = deepcopy(metrics)
                (item['driving'] if target == 'driving' else item['laps'][0])['replay_status'] = value
                invalid.append(item)
        for value in [None, 'REPLAY_STATE_UNAVAILABLE', ['private path'], [1], ['UNKNOWN'] * 101]:
            item = deepcopy(metrics); item['laps'][0]['reason_codes'] = value; invalid.append(item)
        for replay_status in ['CONFIRMED_REPLAY', 'MIXED', 'INVALID']:
            item = deepcopy(metrics); item['laps'][0]['replay_status'] = replay_status; invalid.append(item)
        item = deepcopy(metrics); item['driving']['status'] = 'OBSERVED'; invalid.append(item)
        item = deepcopy(metrics); item['driving'].pop('replay_status'); invalid.append(item)
        item = deepcopy(metrics); item['laps'][0].pop('reason_codes'); invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                _metrics(item)

    def test_aggregate_labels_fill_known_fields_independently(self):
        payloads = sorted([source('a'), source('b')], key=lambda item: item['source_id'])
        payloads[0]['observations'][0]['labels'] = {'car': 'Synthetic GT3', 'track': '', 'layout': ''}
        payloads[1]['observations'][0]['labels'] = {'car': '', 'track': 'Synthetic Track', 'layout': 'GP'}
        for item in payloads:
            self.store.record_source(item)
        self.assertEqual(self.store.get_drive(identity('drive'))['labels'],
                         {'car': 'Synthetic GT3', 'track': 'Synthetic Track', 'layout': 'GP'})

    def test_optional_input_summary_round_trips_and_old_v2_remains_readable(self):
        from driver_history import _metrics
        from driver_history_metrics import build_observation_metrics
        from test_driver_style import telemetry
        sample = telemetry()
        value = source(intervals=[[0., 12.]])
        row = value['observations'][0]
        row['metrics'] = build_observation_metrics(sample, row)
        checked = _metrics(row['metrics'])
        self.assertEqual(checked['calculation_version'], 'observed-metrics-v4')
        self.assertEqual(checked['laps'][0]['inputs']['brake_steer']['event_count'], 3)
        self.store.record_source(value)
        restored = self.store.get_drive(identity('drive'))['observations'][0]['metrics']
        self.assertEqual(restored['laps'][0]['inputs'], checked['laps'][0]['inputs'])
        legacy = deepcopy(value)
        legacy['observations'][0]['metrics']['calculation_version'] = 'observed-metrics-v2'
        legacy['observations'][0]['metrics']['laps'][0].pop('inputs')
        self.store.record_source(legacy)
        restored = self.store.get_drive(identity('drive'))['observations'][0]['metrics']
        self.assertNotIn('inputs', restored['laps'][0])
        self.assertEqual(self.store.status()['source_count'], 1)

    def test_input_payload_rejects_invalid_values_without_losing_existing_observation(self):
        from driver_history_metrics import build_observation_metrics
        from test_driver_style import telemetry
        value = source(intervals=[[0., 12.]])
        value['observations'][0]['metrics'] = build_observation_metrics(telemetry(), value['observations'][0])
        self.store.record_source(value)
        before = self.store.get_drive(identity('drive'))
        changes = [('status', 'PRIVATE'), ('sample_count', -1), ('sample_count', True),
                   ('reason_codes', ['C:/PRIVATE/file.ibt']), ('schema_version', 2)]
        for key, invalid in changes:
            broken = deepcopy(value)
            broken['observations'][0]['metrics']['laps'][0]['inputs'][key] = invalid
            with self.subTest(key=key), self.assertRaises(ValueError): self.store.record_source(broken)
        for key, invalid in [('event_count', -1), ('linked_count', 500), ('overlap_pct', float('nan')),
                             ('sampling_hz', 0.), ('timing_uncertainty_s', None)]:
            broken = deepcopy(value)
            broken['observations'][0]['metrics']['laps'][0]['inputs']['brake_steer'][key] = invalid
            with self.subTest(key=key), self.assertRaises(ValueError): self.store.record_source(broken)
        broken = deepcopy(value)
        broken['observations'][0]['metrics']['laps'][0]['measurable'] = False
        with self.assertRaises(ValueError): self.store.record_source(broken)
        self.assertEqual(self.store.get_drive(identity('drive')), before)

    def test_conflicting_nonempty_labels_are_withheld_without_changing_native_identity(self):
        payloads = [source('a'), source('b')]
        for item, car in zip(payloads, ['Synthetic Car A', 'Synthetic Car B']):
            item['observations'][0]['labels'] = {'car': car, 'track': 'Synthetic Track', 'layout': 'GP'}
            self.store.record_source(item)
        drive = self.store.get_drive(identity('drive'))
        self.assertEqual(drive['labels'], {'car': '', 'track': 'Synthetic Track', 'layout': 'GP'})
        self.assertEqual(drive['car_key'], 'synthetic-car')
        self.assertEqual(drive['identity_status'], 'NATIVE')
        self.assertEqual(self.store.status()['native_session_count'], 1)

    def test_invalid_input_fails_before_creating_storage(self):
        invalid = []
        p = source(); p['schema_version'] = 2; invalid.append(p)
        p = source(); p['source_id'] = 'not-a-hash'; invalid.append(p)
        p = source(); p['observations'][0]['drive_id'] = 'x'; invalid.append(p)
        p = source(); p['observations'][0]['recorded_at'] = '2026-10-27T00:00:00'; invalid.append(p)
        for interval in ([[-1, 1]], [[1, 1]], [[2, 1]], [[0, float('nan')]], [[False, 1]]):
            p = source(); p['observations'][0]['coverage']['intervals'] = interval; invalid.append(p)
        p = source(); p['observations'][0]['conditions']['fuel_level_l'] = float('inf'); invalid.append(p)
        p = source(); p['observations'][0]['conditions']['fuel_level_l'] = 10 ** 1000; invalid.append(p)
        p = source(); p['observations'].append(deepcopy(p['observations'][0])); invalid.append(p)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.record_source(payload)
        self.assertFalse(self.path.parent.exists())

    def test_replacement_failure_rolls_back_deletion_and_new_source(self):
        self.store.record_source(source())
        before = self.store.get_drive(identity('drive'))
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TRIGGER fail_observation BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            connection.commit()
        with self.assertRaises(sqlite3.Error):
            self.store.record_source(source(drive='replacement'))
        self.assertEqual(self.store.get_drive(identity('drive')), before)
        with self.assertRaises(sqlite3.Error):
            self.store.record_source(source('new', drive='replacement'))
        self.assertEqual(self.store.status()['source_count'], 1)

    def test_readonly_queries_do_not_modify_database(self):
        self.store.record_source(source())
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        self.store.list_drives(); self.store.status(); self.store.get_drive(identity('drive'))
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_read_connections_are_enforced_readonly_by_sqlite(self):
        self.store.record_source(source())
        with self.store._connection() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('DELETE FROM observations')
        self.assertEqual(self.store.status()['observation_count'], 1)

    def test_existing_unrelated_database_is_rejected_without_migration(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE personal_memory (value TEXT)')
            connection.execute("INSERT INTO personal_memory VALUES ('synthetic preserved')")
            connection.commit()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.record_source(source())
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_store_is_safe_for_concurrent_upserts_and_queries(self):
        def write(i):
            self.store.record_source(source(str(i), intervals=[[i, i + 2]]))
            return self.store.get_drive(identity('drive')) is not None
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertTrue(all(pool.map(write, range(24))))
        result = self.store.get_drive(identity('drive'))
        self.assertEqual(result['source_count'], 24)
        self.assertEqual(result['coverage']['intervals'], [[0., 25.]])

    def test_external_write_lock_times_out_and_preserves_previous_records(self):
        self.store.record_source(source())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('BEGIN EXCLUSIVE')
            started = time.perf_counter()
            with self.assertRaises(sqlite3.OperationalError):
                self.store.record_source(source('b'))
            self.assertLess(time.perf_counter() - started, 1.5)
        self.assertEqual(self.store.status()['source_count'], 1)

    def test_filtering_and_stable_pagination(self):
        for i in range(5):
            self.store.record_source(source(str(i), drive=str(i)))
        self.store.record_source(source('lmu', drive='lmu', game='lmu'))
        all_iracing = self.store.list_drives(game='iracing')
        self.assertEqual(len(all_iracing), 5)
        self.assertEqual(self.store.list_drives(game='iracing', limit=2, offset=2), all_iracing[2:4])
        self.assertEqual(self.store.status(game='lmu')['source_count'], 1)
        self.assertEqual(self.store.list_drives(game='iracing', offset=99), [])
        for kwargs in ({'limit': 0}, {'limit': 201}, {'offset': -1}, {'game': 'other'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.list_drives(**kwargs)


if __name__ == '__main__':
    unittest.main()
