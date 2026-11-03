"""Stable export identities, explicit coverage and no personal source filenames."""
import json
import shutil
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

import drive_summary
import session_log
import session_log_extras as extras
from test_session_log_extras import write_recording


class ExportIdentityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.run = {'run_id': 'synthetic-run', 'started_at': 1700000100., 'ended_at': 1700000200.,
                    'session_type': 'Race', 'identity': {'car_path': 'gt3', 'track_path': 'test'}}
        self.path = self.folder / 'private-driver-name.ibt'
        write_recording(self.path, 1700000000., 1700000300.)
        self.meta = self.run['identity']

    def provider(self, analyze=None, **kwargs):
        return extras.own_drive(lambda: [self.path], lambda p: self.meta,
            analyze or (lambda p: ({'laps': []}, self.meta, None)), **kwargs)

    def summary(self, made):
        return drive_summary.unpack(made.files['drive_summary.json.xz'])['recordings'][0]

    def test_copied_or_renamed_same_content_shares_identity_but_changed_bytes_do_not(self):
        ref = self.summary(self.provider()(self.run))['source_ref']
        copied = self.folder / 'renamed.ibt'
        shutil.copy2(self.path, copied)
        self.path = copied
        self.assertEqual(self.summary(self.provider()(self.run))['source_ref'], ref)
        data = bytearray(copied.read_bytes())
        data[100] = 7
        copied.write_bytes(data)
        self.assertNotEqual(self.summary(self.provider()(self.run))['source_ref'], ref)
        self.assertRegex(ref, r'^sha256:[0-9a-f]{64}$')

    def test_summary_and_trace_share_source_and_explicit_partial_run_coverage(self):
        traces = Mock(return_value=iter([{'passes': [{'samples': 2}]}]))
        made = self.provider(traces=traces)(self.run)
        summary = self.summary(made)
        coverage = summary['source_coverage']
        self.assertEqual(coverage['recording_window'], [-100., 200.])
        self.assertEqual(coverage['overlap_window'], [0., 100.])
        self.assertEqual(coverage['analysis_scope'], 'whole_selected_recording')
        self.assertFalse(coverage['full_recording_replay'])
        traces.assert_not_called()
        bundle = next(made.supplement())
        import handling_traces
        trace = handling_traces.unpack(next(iter(bundle.files.values())))
        self.assertEqual(trace['source_ref'], summary['source_ref'])
        self.assertEqual(trace['source_coverage'], coverage)
        self.assertNotIn('private-driver-name', json.dumps([summary, trace]))
        self.assertNotIn(str(self.path), json.dumps([summary, trace]))

    def test_changed_source_during_analysis_is_excluded(self):
        def changed(path):
            path.write_bytes(path.read_bytes() + b'changed')
            return {'laps': []}, self.meta, None
        made = self.provider(analyze=changed)(self.run)
        self.assertFalse(made.files)
        self.assertEqual(made.info['reason'], 'recording_changed')
        self.assertEqual(made.info['selection']['changed_during_export'], 1)

    def test_changed_source_before_lazy_traces_does_not_emit_mislabeled_samples(self):
        callback = Mock()
        made = self.provider(traces=callback)(self.run)
        self.path.write_bytes(b'changed')
        self.assertEqual(list(made.supplement()), [])
        callback.assert_not_called()

    def test_hashing_and_analysis_wait_while_driving(self):
        analyze = Mock()
        with self.assertRaises(session_log.Deferred):
            self.provider(analyze=analyze, driving=lambda: True)(self.run)
        analyze.assert_not_called()
        with self.assertRaises(session_log.Deferred):
            extras.recording_ref(self.path, extras._stamp(self.path), driving=lambda: True)

    def test_no_recording_distinguishes_no_candidates_unknown_clock_and_other_session(self):
        made = extras.own_drive(lambda: [], lambda p: {}, None)(self.run)
        self.assertEqual(made.info['reason_detail'], 'no_candidates')
        self.path.write_bytes(b'invalid')
        made = self.provider()(self.run)
        self.assertEqual(made.info['reason_detail'], 'unreadable_window')
        write_recording(self.path, 1700000000., 1700000050.)
        made = self.provider()(self.run)
        self.assertEqual(made.info['reason_detail'], 'outside_run')
        write_recording(self.path, 1700000000., 1700000300.)
        made = extras.own_drive(lambda: [self.path], lambda p: {**self.meta, 'session_type': 'Practice'}, None)(self.run)
        self.assertEqual(made.info['selection']['session_kind_mismatch'], 1)

    def test_three_recording_limit_is_visible_without_exposing_candidates(self):
        paths = [self.folder / f'{i}.ibt' for i in range(5)]
        for i, path in enumerate(paths):
            write_recording(path, 1700000090 + i, 1700000300.)
        made = extras.own_drive(lambda: paths, lambda p: self.meta,
                               lambda p: ({'laps': []}, self.meta, None))(self.run)
        self.assertEqual(made.info['selection']['matched'], 5)
        self.assertEqual(made.info['selection']['selected'], 3)
        self.assertEqual(made.info['selection']['omitted_by_limit'], 2)


if __name__ == '__main__':
    unittest.main()
