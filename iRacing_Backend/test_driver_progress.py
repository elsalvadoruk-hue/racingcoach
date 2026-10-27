"""Result-before-history ordering and challenge-after-history using temp stores."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from analysis_numeric import file_signature
from driver_history import DriverHistoryStore
from driver_history_index import index_telemetry
from driver_journal import DriverJournal, journal_context
from driver_progress import (attach_recording_results, notify_after_save,
    persist_source_results, reconcile_driver_challenges)
from driver_results import DriverResultsStore
from test_driver_history import identity
from test_driver_report import current_metrics, facts
from test_driver_results import native_info


class DriverProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='driver-progress-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.history = DriverHistoryStore(self.folder / 'history.sqlite3')
        self.enterContext(patch.dict(os.environ, IRACING_DRIVER_RESULTS_PATH='', IRACING_DRIVER_JOURNAL_PATH=''))
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)

    def item(self, name='first', *, date='2026-10-26T10:00:00Z'):
        summary = current_metrics(facts(name, name, date=date))
        telemetry = SimpleNamespace(session_info=native_info(), metadata={}, channels={})
        return telemetry, summary

    def test_attachment_is_pure_reuses_verified_date_and_keeps_input_unchanged(self):
        telemetry, summary = self.item()
        before = deepcopy(summary)
        with patch.object(Path, 'open', side_effect=AssertionError('No extra recording read')), \
                patch('driver_results.extract_recording_results', wraps=__import__('driver_results').extract_recording_results) as extract:
            attached = attach_recording_results(telemetry, summary)
        self.assertEqual(summary, before)
        extract.assert_called_once_with(telemetry, summary['source_id'], recorded_at='2026-10-26T10:00:00Z')
        self.assertEqual(attached['race_results']['results'][0]['status'], 'OFFICIAL')
        self.assertFalse(self.history.path.exists())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_undated_attachment_does_not_reopen_recording(self):
        telemetry, summary = self.item(date=None)
        with patch.object(Path, 'open', side_effect=AssertionError('No date fallback read')):
            attached = attach_recording_results(telemetry, summary)
        self.assertIsNone(attached['race_results']['results'][0]['recorded_at'])

    def test_result_persistence_uses_history_sidecar_and_is_content_idempotent(self):
        telemetry, summary = self.item()
        attached = attach_recording_results(telemetry, summary)
        persist_source_results(self.history, attached)
        persist_source_results(self.history, attached)
        results = DriverResultsStore(history_path=self.history.path).snapshot('iracing')
        self.assertEqual(results['source_count'], 1)
        self.assertEqual(len(results['rows']), 1)
        self.assertFalse(self.history.path.exists())

    def test_mismatched_or_missing_result_batch_is_rejected_before_sidecar_creation(self):
        telemetry, summary = self.item()
        for attached in (summary, dict(summary, race_results={'source_id': 'f'*64, 'game': 'iracing'}),
                         dict(summary, race_results={'source_id': summary['source_id'], 'game': 'lmu'})):
            with self.subTest(attached=attached), self.assertRaises(ValueError):
                persist_source_results(self.history, attached)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_no_active_goal_avoids_history_snapshot_and_creates_no_journal(self):
        with patch.object(self.history, 'report_snapshot', side_effect=AssertionError('No bulk snapshot')):
            self.assertEqual(reconcile_driver_challenges(self.history, 'iracing'), {'checked': False, 'challenge': None})
            self.assertEqual(reconcile_driver_challenges(self.history, {'game': 'iracing'}), {'checked': False, 'challenge': None})
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_challenge_reconciliation_only_uses_a_committed_followup_snapshot(self):
        self.history.record_source(current_metrics(facts('baseline', 'baseline')))
        journal = DriverJournal(self.history.path, clock=lambda: self.now)
        candidate = journal_context('iracing', self.history.report_snapshot('iracing'), journal=journal)['candidates'][0]
        journal.accept_challenge(self.history.report_snapshot('iracing'), 'iracing', candidate['drive_id'], candidate['candidate_id'])
        followup = current_metrics(facts('after', 'after', date='2026-10-27T12:01:00Z'))
        self.now = datetime(2026, 10, 27, 13, tzinfo=timezone.utc)
        with patch('driver_journal.DriverJournal', return_value=journal):
            before = reconcile_driver_challenges(self.history, followup)
            self.assertEqual(before['challenge']['status'], 'ACTIVE')
            self.history.record_source(followup)
            after = reconcile_driver_challenges(self.history, followup)
        self.assertTrue(after['checked'])
        self.assertEqual(after['challenge']['status'], 'ACHIEVED')

    def test_optional_evaluator_failure_returns_stable_retry_warning(self):
        callback = Mock(side_effect=RuntimeError('PRIVATE SOURCE PATH'))
        with self.assertLogs('driver_progress', level='WARNING') as logs:
            warnings = notify_after_save(self.history, {}, callback)
        self.assertEqual(warnings, ['CHALLENGE_RECONCILE_FAILED'])
        self.assertNotIn('PRIVATE', str(logs.output))
        self.assertEqual(notify_after_save(self.history, {}, None), [])

    def foreground(self, summary, *, before_save=None, after_save=None):
        path = self.folder / 'source.ibt'
        path.write_bytes(b'pure synthetic source')
        source_id = hashlib.sha256(path.read_bytes()).hexdigest()
        summary = dict(summary, source_id=source_id)
        telemetry = SimpleNamespace(path=path, signature=file_signature(path), metadata={}, channels={}, session_info=native_info())
        with patch('driver_history_index.build_source_summary', return_value=summary):
            return index_telemetry(telemetry, source_id, store=self.history,
                                   before_save=before_save, after_save=after_save)

    def test_foreground_saves_results_before_history_then_notifies_and_binds_single_drive(self):
        _, summary = self.item()
        events = []
        original = self.history.record_source
        def before(history, attached):
            self.assertIs(history, self.history)
            self.assertFalse(history.path.exists())
            self.assertIn('race_results', attached)
            events.append('results')
        def record(attached):
            events.append('history')
            return original(attached)
        def after(history, attached):
            events.append('challenges')
            self.assertIsNotNone(history.get_drive(identity('first')))
        with patch.object(self.history, 'record_source', side_effect=record):
            result = self.foreground(summary, before_save=before, after_save=after)
        self.assertEqual(events, ['results', 'history', 'challenges'])
        self.assertEqual(result['drive_ids'], [identity('first')])
        self.assertEqual(result['selected_drive_id'], identity('first'))
        self.assertEqual(result['observation_count'], 1)
        self.assertEqual(result['drive_options'], [dict(drive_id=identity('first'), recorded_at='2026-10-26T10:00:00Z',
            labels={'car': '예시 GT3', 'track': '예시 코스'}, session_type='PRACTICE')])

    def test_foreground_multi_drive_source_has_no_implicit_feeling_target(self):
        _, summary = self.item()
        summary['observations'].extend(deepcopy(current_metrics(facts('second', 'second'))['observations']))
        result = self.foreground(summary)
        self.assertEqual(result['drive_ids'], sorted([identity('first'), identity('second')]))
        self.assertIsNone(result['selected_drive_id'])
        self.assertEqual([row['drive_id'] for row in result['drive_options']], result['drive_ids'])

    def test_foreground_result_failure_never_marks_history_saved(self):
        _, summary = self.item()
        after = Mock()
        with self.assertRaisesRegex(RuntimeError, 'results store unavailable'):
            self.foreground(summary, before_save=Mock(side_effect=RuntimeError('results store unavailable')), after_save=after)
        self.assertFalse(self.history.path.exists())
        after.assert_not_called()

    def test_foreground_history_failure_retains_independent_saved_result(self):
        _, summary = self.item()
        after = Mock()
        with patch.object(self.history, 'record_source', side_effect=ValueError('synthetic history failure')):
            with self.assertRaisesRegex(ValueError, 'synthetic history failure'):
                self.foreground(summary, after_save=after)
        saved = DriverResultsStore(history_path=self.history.path).snapshot('iracing')
        self.assertEqual(saved['source_count'], 1)
        self.assertEqual(saved['rows'][0][1]['status'], 'OFFICIAL')
        self.assertFalse(self.history.path.exists())
        after.assert_not_called()

    def test_foreground_reconcile_failure_keeps_success_and_exposes_manual_retry_warning(self):
        _, summary = self.item()
        with self.assertLogs('driver_progress', level='WARNING'):
            result = self.foreground(summary, after_save=Mock(side_effect=RuntimeError('PRIVATE PATH')))
        self.assertTrue(result['saved'])
        self.assertEqual(result['warnings'], ['CHALLENGE_RECONCILE_FAILED'])
        self.assertIsNotNone(self.history.get_drive(identity('first')))

    def test_source_changed_during_result_commit_does_not_commit_stale_history(self):
        _, summary = self.item()
        after = Mock()
        def changing(history, attached):
            persist_source_results(history, attached)
            (self.folder / 'source.ibt').write_bytes(b'new recording bytes after result save')
        result = self.foreground(summary, before_save=changing, after_save=after)
        self.assertEqual(result, {'saved': False, 'reason': 'SOURCE_CHANGED'})
        self.assertFalse(self.history.path.exists())
        self.assertEqual(DriverResultsStore(history_path=self.history.path).snapshot('iracing')['source_count'], 1)
        after.assert_not_called()


if __name__ == '__main__':
    unittest.main()
