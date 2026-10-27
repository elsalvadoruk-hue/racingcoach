"""Optional inputs and follow-up goals use synthetic history and temporary stores."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from driver_history import DriverHistoryStore
from driver_journal import DriverJournal, JournalError, journal_context
from test_driver_history import identity
from test_driver_report import current_metrics, facts


class DriverJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='driver-journal-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'nested' / 'history.sqlite3'
        self.history = DriverHistoryStore(self.path)
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.journal = DriverJournal(self.path, clock=lambda: self.now)

    def seed(self, name='baseline', **kwargs):
        payload = current_metrics(facts(name, name, lap_times=kwargs.pop('lap_times', (100., 100.1, 99.9, 100., 100.)), **kwargs))
        self.history.record_source(payload)
        return identity(name)

    def snapshot(self, game='iracing'):
        return self.history.report_snapshot(game)

    def context(self, game='iracing', drive_id=None):
        return journal_context(game, self.snapshot(game), journal=self.journal, drive_id=drive_id)

    def choose(self, kind='PACE_REPEAT'):
        context = self.context()
        candidate = next(row for row in context['candidates'] if row['kind'] == kind)
        return self.journal.accept_challenge(self.snapshot(), 'iracing', candidate['drive_id'], candidate['candidate_id'])

    def followup(self, name='followup', **kwargs):
        return self.seed(name, date=kwargs.pop('date', (self.now + timedelta(minutes=1)).isoformat()), **kwargs)

    def reconcile(self):
        self.now += timedelta(hours=1)
        return self.journal.reconcile(self.snapshot(), 'iracing')

    def test_constructor_empty_read_delete_and_reconcile_do_not_create_storage(self):
        self.assertEqual(self.context()['drives'], [])
        self.assertIsNone(self.journal.reconcile(self.snapshot(), 'iracing'))
        self.assertFalse(self.path.parent.exists())
        drive_id = self.seed()
        self.journal.delete_feeling(self.snapshot(), 'iracing', drive_id)
        self.assertFalse(self.journal.path.exists())

    def test_journal_environment_override_and_history_path_are_distinct(self):
        other = Path(self.temp.name) / 'journal-other.sqlite3'
        with patch.dict(os.environ, IRACING_DRIVER_JOURNAL_PATH=str(other)):
            self.assertEqual(DriverJournal(self.path).path, other)
            self.assertEqual(DriverJournal(self.path, path=self.journal.path).path, self.journal.path)
        with self.assertRaises(JournalError):
            DriverJournal(self.path, path=self.path)

    def test_source_only_feeling_unsure_is_saved_and_distinct_from_unanswered(self):
        drive_id = self.seed(identity_status='SOURCE_ONLY')
        self.assertIsNone(self.context()['feeling'])
        before = self.path.read_bytes()
        result = self.journal.save_feeling(self.snapshot(), 'iracing', drive_id, 'UNSURE')
        self.assertEqual(result['response'], 'UNSURE')
        self.assertIsNone(result['difficulty'])
        self.assertEqual(self.context()['feeling'], result)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(DriverJournal(self.path).read('iracing')['feelings'][drive_id], result)

    def test_feeling_single_selection_optional_scope_update_and_delete(self):
        drive_id = self.seed()
        self.journal.save_feeling(self.snapshot(), 'iracing', drive_id, 'DIFFICULT')
        for difficulty in ('BRAKING', 'CORNERING', 'ACCELERATION', 'OVERALL'):
            self.journal.save_feeling(self.snapshot(), 'iracing', drive_id, 'DIFFICULT', difficulty)
            self.assertEqual(self.context()['feeling']['difficulty'], difficulty)
        self.journal.save_feeling(self.snapshot(), 'iracing', drive_id, 'COMFORTABLE')
        self.assertEqual(self.context()['feeling']['response'], 'COMFORTABLE')
        self.assertIsNone(self.context()['feeling']['difficulty'])
        self.journal.delete_feeling(self.snapshot(), 'iracing', drive_id)
        self.assertIsNone(self.context()['feeling'])
        self.journal.delete_feeling(self.snapshot(), 'iracing', drive_id)

    def test_exact_selected_drive_and_game_attribution(self):
        earlier = self.seed('earlier')
        latest = self.seed('latest', date='2026-10-27T10:00:00Z')
        self.journal.save_feeling(self.snapshot(), 'iracing', earlier, 'NEUTRAL')
        self.assertIsNone(self.context()['feeling'])
        self.assertEqual(self.context(drive_id=earlier)['feeling']['response'], 'NEUTRAL')
        self.assertEqual(self.context()['selected_drive_id'], latest)
        with self.assertRaises(JournalError) as error:
            self.journal.save_feeling(self.snapshot(), 'lmu', earlier, 'DIFFICULT')
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.journal.read('lmu')['feelings'], {})

    def test_feelings_validation_never_creates_journal(self):
        drive_id = self.seed()
        for response, difficulty in [('UNKNOWN', None), ('COMFORTABLE', 'OVERALL'), ('DIFFICULT', 'PRIVATE PATH'), ([], None)]:
            with self.subTest(response=response), self.assertRaises(JournalError):
                self.journal.save_feeling(self.snapshot(), 'iracing', drive_id, response, difficulty)
        with self.assertRaises(JournalError):
            self.journal.save_feeling(self.snapshot(), 'iracing', identity('absent'), 'NEUTRAL')
        self.assertFalse(self.journal.path.exists())

    def test_two_evidence_based_goals_expose_fixed_scope_and_numeric_targets(self):
        self.seed()
        context = self.context()
        self.assertEqual(len(context['candidates']), 2)
        self.assertFalse(self.journal.path.exists())
        pace, consistency = context['candidates']
        self.assertEqual(pace['target'], {'min_laps': 3, 'pace_limit_s': 101., 'mad_limit_pct': None})
        self.assertEqual(consistency['target']['min_laps'], 5)
        self.assertEqual(pace['baseline']['median_lap_s'], 100.)
        self.assertEqual(pace['scope']['track_key'], 'synthetic-track')
        self.assertEqual(pace['baseline']['replay_status'], 'CONFIRMED_NON_REPLAY')

    def test_too_few_laps_or_unknown_layout_gives_explanation(self):
        self.seed(lap_times=(100., 100.))
        self.assertEqual(self.context()['candidates'], [])
        self.assertIn('3개', self.context()['candidate_reason'])
        self.seed('missing-scope', date='2026-10-27T11:00:00Z', layout_key='')
        self.assertEqual(self.context()['candidates'], [])
        self.assertIn('레이아웃', self.context()['candidate_reason'])

    def test_select_does_not_retroactively_achieve_and_get_does_not_reconcile(self):
        self.seed()
        selected = self.choose()
        self.assertEqual(selected['status'], 'ACTIVE')
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['NEW_DRIVE_REQUIRED'])
        self.followup()
        before = self.journal.path.read_bytes()
        self.assertEqual(self.context()['active_challenge']['status'], 'ACTIVE')
        self.assertEqual(before, self.journal.path.read_bytes())
        self.assertEqual(selected['baseline_drive_ids'], [identity('baseline')])
        self.assertEqual(selected['baseline_source_ids'], [identity('baseline')])

    def test_new_independent_confirmed_drive_achieves_and_survives_reopen(self):
        self.seed()
        self.choose()
        drive_id = self.followup()
        result = self.reconcile()
        self.assertEqual(result['status'], 'ACHIEVED')
        self.assertEqual(result['achieved_drive_id'], drive_id)
        self.assertEqual(result['evaluation']['evidence']['median_lap_s'], 100.)
        reopened = DriverJournal(self.path).read('iracing')['challenges'][0]
        self.assertEqual(reopened, result)
        self.assertIsNone(self.context()['active_challenge'])
        self.assertEqual(len(self.context()['challenge_history']), 1)
        self.assertIsNone(self.journal.reconcile(self.snapshot(), 'iracing'))

    def test_reanalysis_split_source_and_copy_cannot_complete_baseline_drive(self):
        self.seed()
        self.choose()
        payload = current_metrics(facts('split-source', 'baseline', date='2026-10-27T12:30:00Z'))
        self.history.record_source(payload)
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['NEW_DRIVE_REQUIRED'])

    def test_reused_baseline_content_id_in_new_drive_cannot_complete(self):
        self.seed()
        self.choose()
        # A source reanalysis may replace segments. Baseline source membership
        # must survive that replacement as well as normal drive-id deduplication.
        self.history.record_source(current_metrics(facts('baseline', 'new-segment', date='2026-10-27T12:30:00Z')))
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['NEW_DRIVE_REQUIRED'])

    def test_historical_late_import_or_missing_date_does_not_complete(self):
        for date, reason in [('2026-10-27T11:59:59Z', 'BEFORE_ACCEPTANCE'),
                             (None, 'RECORDED_AT_REQUIRED')]:
            with self.subTest(date=date):
                self.seed()
                challenge = self.choose()
                self.followup('late-' + str(date), date=date)
                self.assertEqual(self.reconcile()['evaluation']['reason_codes'], [reason])
                self.journal.cancel_challenge('iracing', challenge['id'])

    def test_future_timestamp_is_not_accepted_as_achievement(self):
        self.seed()
        self.choose()
        self.followup(date='2030-01-01T00:00:00Z')
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['FUTURE_RECORDING'])

    def test_source_only_or_unknown_replay_followup_is_unverified(self):
        for variant, reason in [('SOURCE_ONLY', 'NATIVE_IDENTITY_REQUIRED'), ('UNKNOWN', 'REPLAY_UNCONFIRMED')]:
            with self.subTest(variant=variant):
                self.seed()
                challenge = self.choose()
                payload = current_metrics(facts('follow-' + variant, 'follow-' + variant,
                    date=(self.now + timedelta(minutes=1)).isoformat(),
                    identity_status='SOURCE_ONLY' if variant == 'SOURCE_ONLY' else 'NATIVE'),
                    replay='UNKNOWN' if variant == 'UNKNOWN' else 'CONFIRMED_NON_REPLAY')
                self.history.record_source(payload)
                result = self.reconcile()
                self.assertEqual(result['status'], 'ACTIVE')
                self.assertEqual(result['evaluation']['status'], 'UNVERIFIED')
                self.assertEqual(result['evaluation']['reason_codes'], [reason])
                self.journal.cancel_challenge('iracing', challenge['id'])

    def test_confirmed_replay_never_completes_a_real_drive_challenge(self):
        self.seed()
        self.choose()
        payload = current_metrics(facts('replay', 'replay', date='2026-10-27T12:01:00Z'), replay='CONFIRMED_REPLAY')
        metrics = payload['observations'][0]['metrics']
        metrics['driving'].update(status='UNAVAILABLE', intervals=[], time_s=None, distance_m=None)
        for lap in metrics['laps']:
            lap['measurable'] = False
        self.history.record_source(payload)
        result = self.reconcile()
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertEqual(result['evaluation']['reason_codes'], ['REPLAY_UNCONFIRMED'])

    def test_scope_or_game_mismatch_is_not_evaluated(self):
        self.seed()
        self.choose()
        for key in ('car_key', 'track_key', 'layout_key'):
            self.followup(key, **{key: 'other'})
        self.followup('other-game', game='lmu')
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['NEW_DRIVE_REQUIRED'])

    def test_lap_count_and_pace_failures_keep_goal_active_with_evidence(self):
        self.seed()
        self.choose()
        self.followup('short', lap_times=(100.,))
        result = self.reconcile()
        self.assertEqual(result['evaluation']['reason_codes'], ['INSUFFICIENT_LAPS'])
        self.followup('slow', lap_times=(110., 110., 110.))
        result = self.reconcile()
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertEqual(result['evaluation']['status'], 'NOT_MET')
        self.assertEqual(result['evaluation']['reason_codes'], ['PACE_NOT_MET'])
        self.assertEqual(result['evaluation']['evidence']['median_lap_s'], 110.)

    def test_consistency_goal_requires_both_pace_and_dispersion(self):
        self.seed()
        self.choose('CONSISTENCY_HOLD')
        self.followup('variable', lap_times=(95., 98., 100., 102., 105.))
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['CONSISTENCY_NOT_MET'])
        self.followup('steady-slow', lap_times=(110.,)*5)
        self.assertEqual(self.reconcile()['evaluation']['reason_codes'], ['PACE_NOT_MET'])
        self.followup('steady', lap_times=(100.,)*5)
        self.assertEqual(self.reconcile()['status'], 'ACHIEVED')

    def test_same_completed_criterion_is_not_recommended_again(self):
        self.seed()
        self.choose()
        self.followup()
        self.reconcile()
        self.assertEqual([row['kind'] for row in self.context()['candidates']], ['CONSISTENCY_HOLD'])

    def test_one_active_per_game_idempotent_selection_and_cancel_history(self):
        self.seed()
        selected = self.choose()
        self.assertEqual(self.choose()['id'], selected['id'])
        with self.assertRaises(JournalError) as error:
            self.choose('CONSISTENCY_HOLD')
        self.assertEqual(error.exception.status_code, 409)
        self.journal.cancel_challenge('iracing', selected['id'])
        self.journal.cancel_challenge('iracing', selected['id'])
        self.assertEqual(self.context()['challenge_history'][0]['status'], 'CANCELLED')
        self.assertNotEqual(self.choose()['id'], selected['id'])

    def test_concurrent_selection_across_instances_has_only_one_winner(self):
        self.seed()
        snapshot = self.snapshot()
        candidates = self.context()['candidates']
        def attempt(candidate):
            try:
                return DriverJournal(self.path, clock=lambda: self.now).accept_challenge(snapshot, 'iracing',
                    candidate['drive_id'], candidate['candidate_id'])['status']
            except JournalError as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, candidates))
        self.assertCountEqual(results, ['ACTIVE', 409])
        self.assertEqual(len(self.journal.read('iracing')['challenges']), 1)

    def test_stale_candidate_rejected_without_creating_challenge(self):
        self.seed()
        candidate = self.context()['candidates'][0]
        self.seed(lap_times=(105.,)*5)
        with self.assertRaises(JournalError) as error:
            self.journal.accept_challenge(self.snapshot(), 'iracing', candidate['drive_id'], candidate['candidate_id'])
        self.assertEqual(error.exception.status_code, 409)

    def test_unknown_and_future_baseline_dates_cannot_be_selected(self):
        for date in (None, '2030-01-01T00:00:00Z'):
            self.seed(date=date)
            with self.assertRaises(JournalError):
                self.journal.accept_challenge(self.snapshot(), 'iracing', identity('baseline'), identity('candidate'))

    def test_schema_mismatch_preserves_existing_file_and_raises_clear_error(self):
        self.journal.path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            db.execute('CREATE TABLE unrelated(private_value TEXT)')
        before = self.journal.path.read_bytes()
        with self.assertRaises(JournalError) as error:
            self.context()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(before, self.journal.path.read_bytes())

    def test_context_never_includes_original_paths_or_raw_samples(self):
        self.seed()
        self.choose()
        raw = json.dumps(self.context(), ensure_ascii=False)
        self.assertNotIn(str(self.path), raw)
        self.assertNotIn('observations', raw)
        self.assertNotIn('intervals', raw)
        self.assertNotIn('baseline_source_ids', raw)
        self.assertNotIn('baseline_drive_ids', raw)

    def test_has_active_is_passive_and_does_not_read_all_history(self):
        self.assertFalse(self.journal.has_active('iracing'))
        self.assertFalse(self.journal.path.exists())
        self.seed()
        goal = self.choose()
        with patch.object(self.journal, 'read', side_effect=AssertionError('bulk read forbidden')):
            self.assertTrue(self.journal.has_active('iracing'))
            self.assertFalse(self.journal.has_active('lmu'))
        self.journal.cancel_challenge('iracing', goal['id'])
        self.assertFalse(self.journal.has_active('iracing'))

    def test_context_limits_lists_and_preserves_explicit_old_drive_selection(self):
        # Reuse valid compact synthetic facts; this view does not need DB writes.
        self.seed()
        snapshot = self.snapshot()
        template = snapshot['drives'][0]
        snapshot['drives'] = [dict(template, drive_id=identity(str(index))) for index in range(205)]
        selected_id = sorted(row['drive_id'] for row in snapshot['drives'])[0]
        result = journal_context('iracing', snapshot, journal=self.journal, drive_id=selected_id)
        self.assertEqual(result['coverage']['drives'], {'total': 205, 'returned': 200, 'limit': 200})
        self.assertEqual(len(result['drives']), 200)
        self.assertIn(selected_id, [row['drive_id'] for row in result['drives']])
        # The badge count covers all saved goals, independent of the 50-row UI list.
        goals=[{'id':str(index),'status':'ACHIEVED','basis_key':str(index)} for index in range(73)]
        with patch.object(self.journal,'read',return_value={'feelings':{},'challenges':goals}):
            result=journal_context('iracing',snapshot,journal=self.journal)
        self.assertEqual(len(result['challenge_history']),50)
        self.assertEqual(result['coverage']['challenge_history']['achieved'],73)


if __name__ == '__main__':
    unittest.main()
