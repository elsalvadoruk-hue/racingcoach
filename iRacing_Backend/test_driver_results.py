"""Synthetic native classifications; no account, SDK attachment or private stores."""
import copy
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from driver_results import (DriverResultsStore, LiveResultCapture, capture_live_result,
                            extract_recording_results, read_driver_results)


SOURCE = 'a'*64
STAMP = '2026-10-20T10:00:00+00:00'


def native_info(*, official=1, position=0, reason_id=0, reason='Running'):
    # SDK YAML ResultsPositions.ClassPosition starts at zero. Its Position and
    # live PlayerCarClassPosition are different fields and start at one.
    return {'WeekendInfo': {'SessionID': 101, 'SubSessionID': 201, 'TrackID': 8,
                            'TrackName': 'test-track', 'TrackDisplayName': 'Test Track'},
            'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [
                {'CarIdx': 0, 'UserID': 99, 'UserName': 'PRIVATE DRIVER', 'CarID': 7,
                 'CarPath': 'test-car', 'CarScreenName': 'Test GT3', 'CarClassID': 4},
                {'CarIdx': 1, 'UserID': 100, 'CarID': 7, 'CarClassID': 4}]},
            'SessionInfo': {'Sessions': [{'SessionNum': 2, 'SessionType': 'Race', 'ResultsOfficial': official,
                'ResultsPositions': [{'CarIdx': 0, 'Position': position+1, 'ClassPosition': position,
                                      'ReasonOutId': reason_id, 'ReasonOutStr': reason, 'LapsComplete': 20},
                                     {'CarIdx': 1, 'Position': 9, 'ClassPosition': 8,
                                      'ReasonOutId': 0, 'ReasonOutStr': 'Running'}]}]}}


def telemetry(info=None, game='iracing'):
    return SimpleNamespace(session_info=info or native_info(), metadata={'game': game},
                           channels={}, path=Path('synthetic.ibt' if game == 'iracing' else 'synthetic.duckdb'))


class DriverResultExtractionTests(unittest.TestCase):
    def batch(self, info=None, **kwargs):
        return extract_recording_results(telemetry(info), SOURCE, recorded_at=STAMP, **kwargs)

    def test_final_native_own_class_result_uses_zero_based_result_position(self):
        batch = self.batch()
        row, = batch['results']
        self.assertEqual((row['class_position'], row['overall_position'], row['status']), (1, 1, 'OFFICIAL'))
        self.assertEqual(row['authority'], 'IRACING_FINAL_CLASSIFICATION')
        self.assertEqual(row['identity_status'], 'NATIVE')
        self.assertEqual(len(row['drive_id']), 64)
        self.assertNotIn('PRIVATE DRIVER', json.dumps(batch))
        self.assertNotIn('UserID', json.dumps(batch))
        self.assertNotIn('synthetic.ibt', json.dumps(batch))

    def test_native_drive_link_matches_the_existing_history_hash_rule(self):
        from driver_history_summary import _context, _digest
        item = telemetry()
        *_, native, _ = _context(item, 'iracing')
        self.assertEqual(self.batch()['results'][0]['drive_id'], _digest(['native', *native, 2]))

    def test_live_position_or_leader_flag_never_substitutes_for_final_native_table(self):
        info = native_info(official=0)
        info['SessionInfo']['Sessions'][0].pop('ResultsPositions')
        batch = self.batch(info)
        self.assertEqual(batch['results'], [])
        self.assertIn('NO_RECORDED_RACE_RESULT', batch['coverage']['reason_codes'])

    def test_missing_native_identity_keeps_final_result_as_an_observation(self):
        for location, key in [('WeekendInfo', 'SessionID'), ('WeekendInfo', 'SubSessionID')]:
            info = native_info()
            info[location].pop(key)
            row = self.batch(info)['results'][0]
            self.assertEqual(row['status'], 'OBSERVATION')
            self.assertIn('NATIVE_IDENTITY_UNAVAILABLE', row['reason_codes'])
        info = native_info()
        info['DriverInfo']['Drivers'][0].pop('UserID')
        self.assertEqual(self.batch(info)['results'][0]['status'], 'OBSERVATION')

    def test_ambiguous_driver_or_own_result_never_becomes_official(self):
        for target in ('driver', 'result'):
            info = native_info()
            rows = (info['DriverInfo']['Drivers'] if target == 'driver'
                    else info['SessionInfo']['Sessions'][0]['ResultsPositions'])
            rows.append(copy.deepcopy(rows[0]))
            self.assertEqual(self.batch(info)['results'][0]['status'], 'OBSERVATION')

    def test_negative_missing_bool_or_impossible_class_rank_is_not_a_win(self):
        for value in (-1, None, True, 9999):
            info = native_info()
            info['SessionInfo']['Sessions'][0]['ResultsPositions'][0]['ClassPosition'] = value
            row = self.batch(info)['results'][0]
            self.assertEqual(row['status'], 'OBSERVATION')
            self.assertIsNone(row['class_position'])

    def test_dnf_and_dsq_are_explicit_and_do_not_use_arbitrary_reason_text(self):
        for reason_id, reason, expected in [(10, 'Disconnected', 'DNF'), (11, 'Disqualified', 'DSQ')]:
            row = self.batch(native_info(reason_id=reason_id, reason=reason))['results'][0]
            self.assertEqual(row['finish_status'], expected)
            self.assertEqual(row['status'], 'OFFICIAL')
            self.assertNotIn(reason, json.dumps(row))

    def test_an_unknown_native_out_code_never_invents_a_specific_retirement_cause(self):
        row = self.batch(native_info(reason_id=999, reason='Unknown private diagnostic'))['results'][0]
        self.assertEqual(row['finish_status'], 'UNKNOWN')
        self.assertIn('FINISH_REASON_UNAVAILABLE', row['reason_codes'])
        self.assertNotIn('private diagnostic', json.dumps(row))

    def test_practice_is_not_a_race_and_imported_reference_is_not_personal(self):
        info = native_info()
        info['SessionInfo']['Sessions'][0]['SessionType'] = 'Practice'
        self.assertEqual(self.batch(info)['results'], [])
        item = telemetry()
        item.metadata['imported'] = True
        self.assertEqual(extract_recording_results(item, SOURCE)['results'], [])

    def test_practice_recording_does_not_create_an_empty_scheduled_race_result(self):
        info = native_info(official=0)
        info['SessionInfo']['Sessions'][0]['ResultsPositions'] = []
        info['SessionInfo']['Sessions'].insert(0, {'SessionNum': 0, 'SessionType': 'Practice'})
        item = telemetry(info)
        item.channels = {'SessionNum': [0, 0, 0]}
        batch = extract_recording_results(item, SOURCE, recorded_at=STAMP)
        self.assertEqual(batch['results'], [])
        self.assertIn('NO_RECORDED_RACE_RESULT', batch['coverage']['reason_codes'])

    def test_a_racing_result_table_is_an_observation_without_claiming_a_finish(self):
        item = telemetry(native_info(official=0))
        item.channels = {'SessionNum': [2, 2], 'SessionState': [4, 5], 'IsReplayPlaying': [False, False]}
        row, = extract_recording_results(item, SOURCE, recorded_at=STAMP)['results']
        self.assertEqual((row['status'], row['authority'], row['finish_status']),
                         ('OBSERVATION', 'IRACING_RESULT_TABLE', 'UNKNOWN'))
        self.assertEqual(row['class_position'], 1)
        self.assertIn('RACE_FINISH_UNCONFIRMED', row['reason_codes'])

    def test_recorded_native_cooldown_can_preserve_finish_observation_without_official_flag(self):
        info = native_info()
        info['SessionInfo']['Sessions'][0].pop('ResultsOfficial')
        item = telemetry(info)
        item.channels = {'SessionNum': [2, 2], 'SessionState': [4, 6], 'IsReplayPlaying': [False, False]}
        row, = extract_recording_results(item, SOURCE, recorded_at=STAMP)['results']
        self.assertEqual((row['status'], row['authority'], row['finish_status']),
                         ('OBSERVATION', 'IRACING_FINISH_OBSERVATION', 'FINISHED'))
        self.assertIn('FINAL_CLASSIFICATION_UNAVAILABLE', row['reason_codes'])

    def test_unbound_cooldown_and_leader_checkered_flag_cannot_create_a_finish(self):
        for channels in ({'SessionNum': [2], 'SessionFlags': [1], 'IsReplayPlaying': [False]},
                         {'SessionNum': [0], 'SessionState': [6], 'IsReplayPlaying': [False]},
                         {'SessionNum': [2], 'SessionState': [6], 'IsReplayPlaying': [True]},
                         {'SessionNum': [2], 'SessionState': [6]}):
            item = telemetry(native_info(official=0))
            item.channels = channels
            batch = extract_recording_results(item, SOURCE, recorded_at=STAMP)
            self.assertTrue(not batch['results'] or batch['results'][0]['finish_status'] == 'UNKNOWN')

    def test_cooldown_without_own_completed_lap_is_not_a_finish_observation(self):
        for laps in (None, 0, -1, True):
            info = native_info(official=0)
            info['SessionInfo']['Sessions'][0]['ResultsPositions'][0]['LapsComplete'] = laps
            item = telemetry(info)
            item.channels = {'SessionNum': [2], 'SessionState': [6], 'IsReplayPlaying': [False]}
            row, = extract_recording_results(item, SOURCE, recorded_at=STAMP)['results']
            self.assertEqual(row['finish_status'], 'UNKNOWN')

    def test_lmu_finish_fields_are_bound_to_the_recorded_session_axis(self):
        item = telemetry(game='lmu')
        item.channels = {'SessionNum': [2, 2], 'PlayerFinishStatus': [0, 1], 'PlayerCarClassPosition': [3, 2]}
        row, = extract_recording_results(item, SOURCE, recorded_at=STAMP)['results']
        self.assertEqual((row['finish_status'], row['class_position']), ('FINISHED', 2))

    def test_lmu_finish_is_useful_observation_without_server_final_classification(self):
        item = telemetry(game='lmu')
        item.channels = {'PlayerFinishStatus': [1], 'PlayerCarClassPosition': [2]}
        row = extract_recording_results(item, SOURCE, recorded_at=STAMP)['results'][0]
        self.assertEqual((row['status'], row['finish_status'], row['class_position']), ('OBSERVATION', 'FINISHED', 2))
        self.assertIn('FINAL_CLASSIFICATION_UNAVAILABLE', row['reason_codes'])

    def test_extraction_never_reopens_recordings_or_uses_mtime_when_date_is_unknown(self):
        with patch.object(Path, 'open', side_effect=AssertionError('No recording reread')), \
                patch.object(Path, 'stat', side_effect=AssertionError('No mtime fallback')):
            row = extract_recording_results(telemetry(), SOURCE, recorded_at=None)['results'][0]
        self.assertIsNone(row['recorded_at'])
        self.assertIn('RECORDING_UTC_UNAVAILABLE', row['reason_codes'])

    def test_duplicate_class_rank_in_the_same_class_is_not_an_official_classification(self):
        info = native_info()
        info['SessionInfo']['Sessions'][0]['ResultsPositions'][1]['ClassPosition'] = 0
        row = self.batch(info)['results'][0]
        self.assertEqual(row['status'], 'OBSERVATION')
        self.assertIn('INVALID_CLASSIFICATION', row['reason_codes'])

    def test_spectator_and_unconfirmed_team_participation_are_not_personal_official_results(self):
        for group, key in [('DriverInfo', 'DriverIsSpectator'), ('WeekendInfo', 'TeamRacing')]:
            info = native_info()
            info[group][key] = 1
            self.assertEqual(self.batch(info)['results'][0]['status'], 'OBSERVATION')

    def test_actual_synthetic_ibt_final_header_can_backfill_without_rereading(self):
        from ibt_reader import IBTReader
        from test_driver_history_worker import synthetic_ibt
        with tempfile.TemporaryDirectory(prefix='native-result-ibt-') as folder:
            path = Path(folder)/'final.ibt'
            synthetic_ibt(path)
            raw = path.read_bytes()
            session_offset = struct.unpack_from('<i', raw, 20)[0]
            old_data_offset = struct.unpack_from('<i', raw, 40)[0]
            info = native_info()
            info['SessionInfo']['Sessions'][0]['SessionNum'] = 0
            session = json.dumps(info).encode()
            header = bytearray(raw[:session_offset])
            struct.pack_into('<i', header, 16, len(session))
            struct.pack_into('<i', header, 40, session_offset+len(session))
            path.write_bytes(header+session+raw[old_data_offset:])
            import hashlib
            source_id = hashlib.sha256(path.read_bytes()).hexdigest()
            parsed = IBTReader(path).parse()
            with patch.object(Path, 'open', side_effect=AssertionError('Already parsed')):
                batch = extract_recording_results(parsed, source_id, recorded_at=STAMP)
            self.assertEqual((batch['results'][0]['status'], batch['results'][0]['class_position']), ('OFFICIAL', 1))

    def test_fresh_child_can_import_and_extract_under_the_read_only_worker_audit(self):
        script = ('import sys,json; from types import SimpleNamespace; '
                  'from driver_history_worker import _read_only_audit; '
                  'sys.addaudithook(_read_only_audit); '
                  'from driver_results import extract_recording_results; '
                  'row=SimpleNamespace(session_info=json.load(sys.stdin),metadata={},channels={}); '
                  f'print(json.dumps(extract_recording_results(row,{SOURCE!r})))')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script],
            input=json.dumps(native_info()), text=True, encoding='utf-8', capture_output=True,
            cwd=Path(__file__).resolve().parent, timeout=5., **options)
        self.assertEqual(result.returncode, 0, 'Pure extraction must work in the restricted worker')
        self.assertEqual(json.loads(result.stdout)['results'][0]['status'], 'OFFICIAL')


class DriverResultsStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='driver-results-')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'new'/'history.results.sqlite3'
        self.store = DriverResultsStore(self.path)
        self.now = datetime(2026, 10, 27, tzinfo=timezone.utc)

    def batch(self, source=SOURCE, info=None, stamp=STAMP):
        return extract_recording_results(telemetry(info), source, recorded_at=stamp)

    def report(self, period=0):
        return read_driver_results('iracing', period, store=self.store, now=self.now)

    def test_read_does_not_create_storage_and_unknown_wins_are_null(self):
        report = self.report()
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(report['coverage']['result_count'], 0)
        self.assertIsNone(report['career']['wins']['value'])
        self.assertEqual(report['career']['wins']['status'], 'UNAVAILABLE')

    def test_result_observations_are_counted_separately_from_official_facts(self):
        item = telemetry(native_info(official=0))
        item.channels = {'SessionNum': [2], 'SessionState': [6], 'IsReplayPlaying': [False]}
        self.store.record_batch(extract_recording_results(item, SOURCE, recorded_at=STAMP))
        report = self.report()
        self.assertEqual((report['aggregate']['recorded_results'], report['aggregate']['official_races'],
                          report['aggregate']['finish_observations'], report['aggregate']['unconfirmed_results']),
                         (1, 0, 1, 0))
        self.assertIsNone(report['aggregate']['wins']['value'])
        self.assertIsNone(report['aggregate']['average_position']['value'])

    def test_legacy_empty_race_placeholders_are_read_without_becoming_results_or_modifying_storage(self):
        batch = self.batch(info=native_info(official=0))
        row = batch['results'][0]
        row.update(authority='UNVERIFIED', class_position=None, overall_position=None, finish_status='UNKNOWN',
                   reason_codes=['FINAL_CLASSIFICATION_UNAVAILABLE', 'OWN_RESULT_UNAVAILABLE'])
        self.store.record_batch(batch)
        before = self.path.read_bytes()
        report = self.report()
        self.assertEqual(report['results'], [])
        self.assertEqual(report['aggregate']['recorded_results'], 0)
        self.assertEqual(report['coverage']['excluded_empty_result_count'], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_running_classification_remains_a_useful_rank_without_a_finish_claim(self):
        batch = self.batch(info=native_info(official=0, position=13))
        batch['results'][0].update(authority='UNVERIFIED', finish_status='CLASSIFIED',
                                   reason_codes=['FINAL_CLASSIFICATION_UNAVAILABLE'])
        self.store.record_batch(batch)
        before = self.path.read_bytes()
        report = self.report()
        row, = report['results']
        self.assertEqual((row['class_position'], row['finish_status'], row['authority']),
                         (14, 'UNKNOWN', 'IRACING_RESULT_TABLE'))
        self.assertEqual(report['aggregate']['recorded_results'], 1)
        self.assertEqual(report['aggregate']['unconfirmed_results'], 1)
        self.assertIsNone(report['aggregate']['wins']['value'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_observed_finish_is_not_downgraded_by_a_later_provisional_source(self):
        item = telemetry(native_info(official=0))
        item.channels = {'SessionNum': [2], 'SessionState': [6], 'IsReplayPlaying': [False]}
        self.store.record_batch(extract_recording_results(item, SOURCE, recorded_at=STAMP))
        item.channels['SessionState'] = [4]
        self.store.record_batch(extract_recording_results(item, 'b'*64, recorded_at='2026-10-20T11:00:00Z'))
        report = self.report()
        self.assertEqual(report['aggregate']['finish_observations'], 1)
        self.assertEqual(report['coverage']['result_count'], 1)
        self.assertIsNone(report['aggregate']['wins']['value'])

    def test_live_cooldown_with_own_native_result_can_save_only_a_finish_observation(self):
        result = capture_live_result(native_info(official=0), source_id=SOURCE, recorded_at=STAMP,
            sample={'session_num': 2, 'session_state': 6, 'is_replay': False}, store=self.store)
        self.assertTrue(result['saved'])
        report = self.report()
        self.assertEqual((report['aggregate']['recorded_results'], report['aggregate']['official_races'],
                          report['aggregate']['finish_observations']), (1, 0, 1))
        self.assertIsNone(report['aggregate']['wins']['value'])

    def test_native_final_result_is_idempotent_and_zero_wins_are_real_after_a_loss(self):
        batch = self.batch(info=native_info(position=4))
        self.store.record_batch(batch)
        self.store.record_batch(batch)
        report = self.report()
        self.assertEqual(report['coverage']['result_count'], 1)
        self.assertEqual(report['career']['wins']['value'], 0)
        self.assertEqual(report['career']['average_position']['value'], 5)

    def test_official_results_from_multiple_sources_count_as_one_native_race(self):
        self.store.record_batch(self.batch())
        self.store.record_batch(self.batch(source='b'*64))
        report = self.report()
        self.assertEqual((report['coverage']['source_count'], report['coverage']['result_count']), (2, 1))
        self.assertEqual(report['career']['wins']['value'], 1)

    def test_source_replacement_and_removal_are_atomic(self):
        self.store.record_batch(self.batch())
        self.store.record_batch(self.batch(info=native_info(position=4)))
        self.assertEqual(self.report()['career']['wins']['value'], 0)
        empty = self.batch()
        empty['results'] = []
        self.store.record_batch(empty)
        self.assertEqual(self.report()['coverage']['result_count'], 0)

    def test_invalid_batch_does_not_partially_delete_existing_result(self):
        self.store.record_batch(self.batch())
        before = self.report()
        invalid = self.batch(info=native_info(position=4))
        invalid['results'][0]['id'] = 'not a hash'
        with self.assertRaises(ValueError):
            self.store.record_batch(invalid)
        self.assertEqual(before, self.report())

    def test_provisional_source_cannot_downgrade_another_final_source(self):
        self.store.record_batch(self.batch())
        self.store.record_batch(self.batch(source='b'*64, info=native_info(official=0, position=5)))
        self.assertEqual(self.report()['career']['wins']['value'], 1)

    def test_later_final_correction_replaces_old_official_classification(self):
        self.store.record_batch(self.batch())
        self.store.record_batch(self.batch(source='b'*64, info=native_info(position=4),
                                          stamp='2026-10-20T11:00:00+00:00'))
        self.assertEqual(self.report()['career']['average_position']['value'], 5)

    def test_equal_authority_conflicting_final_results_are_held_out_of_aggregate(self):
        self.store.record_batch(self.batch())
        self.store.record_batch(self.batch(source='b'*64, info=native_info(position=4)))
        report = self.report()
        self.assertIsNone(report['career']['wins']['value'])
        self.assertIn('CONFLICTING_FINAL_RESULTS', report['results'][0]['reason_codes'])

    def test_dnf_and_dsq_do_not_award_win_or_podium(self):
        for reason_id, reason, counter in [(10, 'Disconnected', 'dnf_count'), (11, 'Disqualified', 'dsq_count')]:
            self.store.record_batch(self.batch(info=native_info(reason_id=reason_id, reason=reason)))
            career = self.report()['career']
            self.assertEqual(career[counter], 1)
            self.assertEqual((career['wins']['value'], career['podiums']['value']), (0, 0))

    def test_selected_period_and_whole_career_are_separate_and_undated_never_uses_mtime(self):
        self.store.record_batch(self.batch(stamp='2025-01-01T10:00:00+00:00'))
        info = native_info(position=4)
        info['WeekendInfo']['SubSessionID'] += 1
        self.store.record_batch(self.batch(source='b'*64, info=info))
        report = self.report(30)
        self.assertEqual(report['aggregate']['official_races'], 1)
        self.assertEqual(report['career']['official_races'], 2)
        self.assertEqual(report['aggregate']['wins']['value'], 0)
        self.assertEqual(report['career']['wins']['value'], 1)

    def test_live_capture_requires_native_table_and_keeps_full_names_out_of_storage(self):
        response = capture_live_result(native_info(), source_id=SOURCE, recorded_at=STAMP, store=self.store)
        self.assertTrue(response['saved'])
        self.assertEqual(self.report()['career']['wins']['value'], 1)
        self.assertNotIn(b'PRIVATE DRIVER', self.path.read_bytes())
        self.assertNotIn(b'ResultsPositions', self.path.read_bytes())

    def test_nonfinal_live_capture_does_not_create_empty_database_or_infer_finish_from_flag(self):
        response = capture_live_result(native_info(official=0), source_id=SOURCE,
                                       sample={'session_state': 6, 'session_flags': 1, 'class_position': 1},
                                       recorded_at=STAMP, store=self.store)
        self.assertFalse(response['saved'])
        self.assertFalse(self.path.exists())

    def test_live_native_source_is_stable_and_same_run_final_correction_replaces(self):
        first = capture_live_result(native_info(), run_id='synthetic-run', recorded_at=STAMP, store=self.store)
        corrected = capture_live_result(native_info(position=4), run_id='synthetic-run',
            recorded_at='2026-10-20T11:00:00Z', store=self.store)
        self.assertEqual(first['source_id'], corrected['source_id'])
        report = self.report()
        self.assertEqual((report['coverage']['source_count'], report['coverage']['result_count']), (1, 1))
        self.assertEqual(report['career']['wins']['value'], 0)

    def test_undated_native_result_is_in_all_time_but_not_a_calendar_period(self):
        batch = extract_recording_results(telemetry(), SOURCE, recorded_at=None)
        self.store.record_batch(batch)
        self.assertEqual(self.report()['career']['wins']['value'], 1)
        recent = self.report(30)
        self.assertEqual(recent['coverage']['undated_result_count'], 1)
        self.assertIsNone(recent['aggregate']['wins']['value'])
        self.assertEqual(recent['results'], [])

    def test_history_sibling_path_is_lazy_and_existing_foreign_schema_is_not_migrated(self):
        history = Path(self.folder.name)/'history.sqlite3'
        sibling = DriverResultsStore(history_path=history)
        self.assertEqual(sibling.path, history.with_name('history.results.sqlite3'))
        self.assertFalse(history.exists())
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('CREATE TABLE never_touch(value TEXT)')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.record_batch(self.batch())
        self.assertEqual(before, self.path.read_bytes())

    def test_sql_failure_after_deleting_the_old_source_rolls_back_the_whole_batch(self):
        self.store.record_batch(self.batch())
        before = self.report()
        info = native_info(position=4)
        extra = copy.deepcopy(info['SessionInfo']['Sessions'][0])
        extra['SessionNum'] = 3
        info['SessionInfo']['Sessions'].append(extra)
        batch = self.batch(info=info)
        blocked = batch['results'][1]['id']
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(f"CREATE TRIGGER synthetic_failure BEFORE INSERT ON results WHEN NEW.id='{blocked}' "
                               "BEGIN SELECT RAISE(ABORT,'Synthetic rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_batch(batch)
        self.assertEqual(before, self.report())

    def test_environment_override_changes_only_the_lazy_results_destination(self):
        destination = Path(self.folder.name)/'isolated'/'override.sqlite3'
        with patch.dict(os.environ, {'IRACING_DRIVER_RESULTS_PATH': str(destination)}):
            store = DriverResultsStore()
        self.assertEqual(store.path, destination)
        self.assertFalse(destination.parent.exists())


class LiveResultCaptureTests(unittest.TestCase):
    def service(self, **kwargs):
        service = LiveResultCapture(**kwargs)
        self.addCleanup(lambda: service.shutdown(wait=True, timeout=2.))
        return service

    def wait_for(self, condition):
        deadline = time.monotonic()+2.
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.005)
        self.fail('Owned capture queue did not reach its expected state')

    def test_database_writes_run_on_owned_thread_and_duplicate_final_tables_skip(self):
        parent = threading.get_ident()
        threads = []
        writer = Mock()
        writer.record_batch.side_effect = lambda _: threads.append(threading.get_ident())
        service = self.service(store_factory=lambda: writer)
        self.assertTrue(service.submit(native_info(), run_id='run', recorded_at=STAMP))
        self.wait_for(lambda: service.status()['saved'] == 1)
        service.submit(native_info(), run_id='run', recorded_at='2026-10-20T11:00:00Z')
        self.wait_for(lambda: service.status()['skipped'] == 1)
        service.submit(native_info(position=4), run_id='run', recorded_at='2026-10-20T12:00:00Z')
        self.wait_for(lambda: service.status()['saved'] == 2)
        self.assertEqual(writer.record_batch.call_count, 2)
        self.assertTrue(all(value != parent for value in threads))
        service.shutdown(wait=True, timeout=2.)
        self.assertFalse(service.status()['running'])
        self.assertFalse(service.submit(native_info()))

    def test_pending_work_is_bounded_coalesced_and_detached_from_native_dictionary(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        batches = []
        def save(batch):
            batches.append(batch)
            if len(batches) == 1:
                entered.set()
                release.wait(2.)
        writer = Mock()
        writer.record_batch.side_effect = save
        service = self.service(store_factory=lambda: writer, max_pending=1)
        service.submit(native_info(), run_id='active', recorded_at=STAMP)
        self.assertTrue(entered.wait(1.))
        info = native_info(position=1)
        self.assertTrue(service.submit(info, run_id='pending', recorded_at=STAMP))
        info['SessionInfo']['Sessions'][0]['ResultsPositions'][0]['ClassPosition'] = 6
        self.assertFalse(service.submit(native_info(), run_id='full', recorded_at=STAMP))
        self.assertEqual(service.status()['queued'], 1)
        correction = native_info(position=4)
        self.assertTrue(service.submit(correction, run_id='pending', recorded_at=STAMP))
        correction['SessionInfo']['Sessions'][0]['ResultsPositions'][0]['ClassPosition'] = 6
        self.assertEqual(service.status()['queued'], 1)
        release.set()
        service.shutdown(wait=True, timeout=2.)
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[-1]['results'][0]['class_position'], 5)
        self.assertEqual(service.status()['dropped'], 1)

    def test_failed_write_is_not_cached_and_the_next_native_tick_retries(self):
        writer = Mock()
        writer.record_batch.side_effect = [RuntimeError('PRIVATE SOURCE PATH'), None]
        service = self.service(store_factory=lambda: writer)
        service.submit(native_info(), recorded_at=STAMP)
        self.wait_for(lambda: service.status()['failed'] == 1)
        service.submit(native_info(), recorded_at=STAMP)
        self.wait_for(lambda: service.status()['saved'] == 1)
        self.assertNotIn('PRIVATE', json.dumps(service.status()))

    def test_provisional_or_replay_snapshots_do_not_even_construct_a_store(self):
        factory = Mock()
        service = self.service(store_factory=factory)
        service.submit(native_info(official=0), recorded_at=STAMP)
        self.wait_for(lambda: service.status()['skipped'] == 1)
        service.submit(native_info(), sample={'is_replay': True}, recorded_at=STAMP)
        self.wait_for(lambda: service.status()['skipped'] == 2)
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
