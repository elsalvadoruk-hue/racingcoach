"""Input-only backfill keeps the existing source, clocks and measured evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from driver_history import DriverHistoryStore
from driver_history_inputs import (INPUT_VERSION, InputAttempts, current_inputs,
    input_request, measure_source_inputs, outdated_input_sources, source_observations)
from driver_history_summary import build_source_summary
from ibt_reader import IBTReader
import test_driver_history_worker as fixtures


class DriverHistoryInputsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='driver-input-enrichment-')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder/'synthetic.ibt'
        self.source_id = fixtures.synthetic_ibt(self.path)
        self.telemetry = IBTReader(self.path).parse()
        self.summary = build_source_summary(self.telemetry, self.source_id)
        for row in self.summary['observations']:
            row['metrics'].pop('input_version', None)
            for lap in row['metrics']['laps']:
                lap['inputs'].pop('input_version', None)
        self.store = DriverHistoryStore(self.folder/'history.sqlite3')
        self.store.record_source(self.summary)

    def payload(self):
        return measure_source_inputs(self.telemetry, self.source_id, self.summary['observations'])

    def test_current_version_is_independent_of_metric_and_result_versions(self):
        self.assertFalse(current_inputs({}))
        self.assertFalse(current_inputs({'calculation_version': 'observed-metrics-v4'}))
        self.assertTrue(current_inputs({'input_version': INPUT_VERSION}))
        self.assertFalse(current_inputs({'input_version': INPUT_VERSION, 'driving': {
            'reason_codes': ['METRICS_EXTRACTION_FAILED']}}))
        self.assertEqual(INPUT_VERSION, 'driver-inputs-v2')

    def test_outdated_sources_and_source_lookup_use_compact_snapshot_only(self):
        snapshot = self.store.report_snapshot('iracing')
        before = deepcopy(snapshot)
        self.assertEqual(outdated_input_sources(snapshot), {self.source_id})
        grouped = source_observations(snapshot)
        self.assertEqual(set(grouped), {self.source_id})
        self.assertEqual(len(grouped[self.source_id]), len(self.summary['observations']))
        self.assertEqual(snapshot, before)
        for row in grouped[self.source_id]:
            row['metrics']['input_version'] = INPUT_VERSION
        self.assertEqual(outdated_input_sources(snapshot), set())

    def test_worker_request_contains_only_observation_identity_and_lap_eligibility(self):
        payload = input_request(self.summary['observations'])
        self.assertTrue(all(set(item) == {'drive_id', 'laps'} for item in payload))
        self.assertTrue(all(set(lap) == {'lap_number', 'start_s', 'end_s', 'measurable', 'replay_status'}
                            for row in payload for lap in row['laps']))
        self.assertEqual(payload, input_request(payload))

    def test_success_changes_only_input_fields_and_marks_all_completed_observations(self):
        before = self.store.report_snapshot('iracing')
        self.store.enrich_source_inputs(self.payload())
        after = self.store.report_snapshot('iracing')
        self.assertEqual(outdated_input_sources(after), set())
        for drive in after['drives']:
            for row in drive['observations']:
                self.assertEqual(row['metrics'].pop('input_version'), INPUT_VERSION)
                for lap in row['metrics']['laps']:
                    self.assertEqual(lap['inputs'].pop('input_version'), INPUT_VERSION)
        self.assertEqual(after, before)

    def test_invalid_last_lap_keeps_every_original_observation_byte(self):
        payload = self.payload()
        payload['observations'][-1]['laps'][-1]['end_s'] += .1
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.enrich_source_inputs(payload)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_missing_or_duplicate_observation_is_rejected_before_any_write(self):
        for mode in ('missing', 'duplicate', 'wrong_source', 'wrong_game'):
            payload = self.payload()
            if mode == 'missing': payload['observations'] = []
            if mode == 'duplicate': payload['observations'] *= 2
            if mode == 'wrong_source': payload['source_id'] = 'f'*64
            if mode == 'wrong_game': payload['game'] = 'lmu'
            before = self.store.path.read_bytes()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.store.enrich_source_inputs(payload)
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_partial_patch_cannot_mark_all_laps_current(self):
        payload = self.payload()
        payload['observations'][0]['laps'].pop()
        before = self.store.report_snapshot('iracing')
        with self.assertRaises(ValueError):
            self.store.enrich_source_inputs(payload)
        self.assertEqual(self.store.report_snapshot('iracing'), before)

    def test_new_input_version_must_be_present_on_each_successful_lap_patch(self):
        payload = self.payload()
        payload['observations'][0]['laps'][0]['inputs'].pop('input_version')
        with self.assertRaises(ValueError):
            self.store.enrich_source_inputs(payload)
        self.assertEqual(outdated_input_sources(self.store.report_snapshot('iracing')), {self.source_id})

    def test_no_old_metric_or_result_calculator_runs_during_enrichment(self):
        with patch('driver_history_metrics.build_observation_metrics', side_effect=AssertionError('no metrics')), \
                patch('driver_history_metrics.merge_observation_metrics', side_effect=AssertionError('no merge')), \
                patch('driver_progress.attach_recording_results', side_effect=AssertionError('no results')):
            payload = self.payload()
        self.assertEqual(len(payload['observations'][0]['laps']), 3)

    def test_failure_journal_survives_restart_and_is_scoped_to_game_source_and_version(self):
        with InputAttempts(self.store.path).opened() as journal:
            journal.remember_failure('iracing', self.source_id)
        with InputAttempts(self.store.path).opened() as journal:
            self.assertTrue(journal.failed('iracing', self.source_id))
            self.assertFalse(journal.failed('lmu', self.source_id))
            self.assertFalse(journal.failed('iracing', 'f'*64))
            with patch('driver_history_inputs.INPUT_VERSION', 'future-input-version'):
                self.assertFalse(journal.failed('iracing', self.source_id))


if __name__ == '__main__':
    unittest.main()
