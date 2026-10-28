"""Automatic session-log sending: only with consent, only finished drives after it, once each."""
import os
import tempfile
import unittest
from unittest import mock

import session_log
import session_log_auto
from session_log_auto import AutoUploader, CONSENT_VERSION

URL = 'https://script.google.com/macros/s/test/exec'


def run(run_id, started, ended, calls=40, track='Monza'):
    return {'run_id': run_id, 'started_at': started, 'ended_at': ended, 'minutes': (ended - started) / 60,
            'calls': calls, 'track': track, 'car': 'GT3'}


class AutoSendTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {'IRACING_DATA_DIR': folder.name, 'IRACING_LOG_UPLOAD_URL': URL})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runs = []
        self.sent = []
        for name, value in (('sessions', lambda store, limit: list(self.runs)),
                            ('export', self._export)):
            p = mock.patch.object(session_log, name, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        self.exported = []
        self.export_options, self.defer = [], False
        self.now = 100_000.
        self.uploader = AutoUploader(None, '1.4.3', current_run=lambda: 'live-run',
                                     send=lambda url, name, data, note, version: self.sent.append((url, name, note)),
                                     clock=lambda: self.now)

    def _export(self, store, run_id, version, include_questions=False, extras=(), limit=None):
        self.exported.append(include_questions)
        self.export_options.append((extras, limit))
        if self.defer:
            raise session_log.Deferred('driver on track')
        return f'log-{run_id}.zip', b'zip'

    def test_extras_are_sent_within_the_upload_limit_and_wait_while_driving(self):
        self.export_options, self.defer = [], True
        uploader = AutoUploader(None, '2.1.0', current_run=lambda: 'live-run', extras=('provider',),
                                send=lambda url, name, data, note, version: self.sent.append((url, name, note)),
                                clock=lambda: self.now)
        session_log_auto.set_enabled(True, CONSENT_VERSION, now=1000.)
        self.runs = [run('drive-a', 2000., 3000.)]
        self.assertEqual(uploader.run_once(), 0)
        self.assertEqual(self.export_options, [(('provider',), session_log.SEND_LIMIT)])
        entry = session_log_auto.load()['runs'].get('drive-a')
        self.assertIsNone(entry)           # waiting is not a failed attempt
        self.defer = False
        self.assertEqual(uploader.run_once(), 1)
        self.assertEqual(self.sent[0][1], 'log-drive-a.zip')

    def test_the_new_notice_with_oval_practice_invalidates_previous_consent(self):
        self.assertEqual(CONSENT_VERSION, 4)
        with mock.patch.object(session_log_auto, 'CONSENT_VERSION', 3):
            session_log_auto.set_enabled(True, 3, now=1000.)
        self.assertFalse(session_log_auto.status()['enabled'])
        self.runs = [run('drive-a', 2000., 3000.)]
        self.assertEqual(self.uploader.run_once(), 0)
        self.assertEqual(self.sent, [])

    def test_changed_consent_is_reported_read_only_with_history_and_no_export(self):
        session_log_auto.save({'consent':{'version':3,'given_at':1000.},
                              'runs':{'already-sent':{'sent_at':2000.,'ended_at':1500.}}})
        before=session_log_auto.path().read_bytes()
        status=session_log_auto.status()
        self.assertIs(status.get('reconsent_required'),True)
        self.assertEqual(status.get('previous_consent_version'),3)
        self.assertTrue(status.get('consent_changes'))
        self.assertFalse(status['enabled'])
        self.assertEqual(status['sent'],1)
        self.runs=[run('after-patch',2000.,3000.)]
        self.assertEqual(self.uploader.run_once(),0)
        self.assertEqual(self.export_options,[])
        self.assertEqual(self.sent,[])
        self.assertEqual(session_log_auto.path().read_bytes(),before)

    def test_reagree_uses_new_time_and_explicit_opt_out_clears_renewal(self):
        session_log_auto.save({'consent':{'version':3,'given_at':1000.},'runs':{}})
        renewed=session_log_auto.set_enabled(True,CONSENT_VERSION,now=50_000.)
        self.assertFalse(renewed.get('reconsent_required'))
        self.assertEqual(renewed['consented_at'],50_000.)
        self.runs=[run('while-paused',2000.,3000.),run('after-renewal',60_000.,61_000.)]
        self.assertEqual(self.uploader.run_once(),1)
        self.assertEqual([x[1] for x in self.sent],['log-after-renewal.zip'])
        session_log_auto.save({'consent':{'version':3,'given_at':1000.},'runs':{}})
        off=session_log_auto.set_enabled(False)
        self.assertFalse(off.get('reconsent_required'))
        self.assertFalse(off['enabled'])

    def test_current_consent_and_never_enabled_install_do_not_need_renewal(self):
        self.assertIs(session_log_auto.status().get('reconsent_required'),False)
        session_log_auto.set_enabled(True,CONSENT_VERSION,now=1000.)
        value=session_log_auto.status()
        self.assertTrue(value['enabled'])
        self.assertIs(value.get('reconsent_required'),False)
        self.assertEqual(value['consented_at'],1000.)

    def test_http_status_and_consent_version_guard_survive_an_upgrade(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        import settings_routes
        session_log_auto.save({'consent':{'version':3,'given_at':1000.},'runs':{}})
        app=FastAPI()
        settings_routes.register_routes(app,SimpleNamespace())
        with TestClient(app) as client:
            endpoint='/api/session-logs/auto'
            state=client.get(endpoint).json()
            self.assertTrue(state['reconsent_required'])
            self.assertEqual(state['previous_consent_version'],3)
            self.assertEqual(client.put(endpoint,json={'enabled':True,'consent_version':3}).status_code,422)
            self.assertFalse(client.get(endpoint).json()['enabled'])
            response=client.put(endpoint,json={'enabled':True,'consent_version':CONSENT_VERSION})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['enabled'])
            self.assertFalse(response.json()['reconsent_required'])

    def test_nothing_is_sent_without_consent_and_turning_on_needs_the_shown_notice(self):
        self.runs = [run('a', 10_000, 20_000)]
        self.assertEqual(self.uploader.run_once(), 0)
        with self.assertRaises(ValueError):
            session_log_auto.set_enabled(True, consent_version=None)
        self.assertFalse(session_log_auto.status()['enabled'])
        self.assertEqual(self.sent, [])

    def test_only_finished_drives_after_consent_are_sent_once_without_questions(self):
        session_log_auto.set_enabled(True, CONSENT_VERSION, now=50_000)
        self.runs = [run('before-consent', 40_000, 41_000), run('finished', 60_000, 61_000),
                     run('still-running', 99_000, 99_900), run('live-run', 60_000, 61_000),
                     run('too-short', 70_000, 70_060), run('too-few-calls', 70_000, 71_000, calls=3)]
        self.assertEqual(self.uploader.run_once(), 1)
        self.assertEqual([name for _, name, _ in self.sent], ['log-finished.zip'])
        self.assertEqual(self.exported, [False])
        self.assertEqual(self.uploader.run_once(), 0)
        status = session_log_auto.status()
        self.assertEqual(status['sent'], 1)
        self.assertEqual(status['recent'][0]['track'], 'Monza')

    def test_failures_are_shown_and_retried_at_most_three_times(self):
        session_log_auto.set_enabled(True, CONSENT_VERSION, now=50_000)
        self.runs = [run('flaky', 60_000, 61_000)]
        def fail(*args):
            raise ValueError('로그를 보내지 못했습니다.')
        self.uploader.send_file = fail
        for _ in range(5):
            self.uploader.run_once()
            self.now += session_log_auto.RETRY_AFTER_S
        entry = session_log_auto.status()['recent'][0]
        self.assertEqual(entry['attempts'], 3)
        self.assertEqual(entry['error'], '로그를 보내지 못했습니다.')
        self.assertIsNone(entry.get('sent_at'))

    def test_turning_off_stops_sending_and_forgets_consent(self):
        session_log_auto.set_enabled(True, CONSENT_VERSION, now=50_000)
        session_log_auto.set_enabled(False)
        self.runs = [run('after', 60_000, 61_000)]
        self.assertEqual(self.uploader.run_once(), 0)
        self.assertIsNone(session_log_auto.status()['consented_at'])

    def test_a_changed_notice_asks_again(self):
        session_log_auto.set_enabled(True, CONSENT_VERSION, now=50_000)
        with mock.patch.object(session_log_auto, 'CONSENT_VERSION', CONSENT_VERSION + 1):
            self.assertFalse(session_log_auto.status()['enabled'])

    def test_ratings_and_ids_never_leave_in_a_log(self):
        cleaned = session_log.scrub({'cars': [{'idx': 3, 'name': 'Real Name', 'irating': 2500, 'user_id': 77, 'class_position': 2}]})
        self.assertEqual(cleaned, {'cars': [{'idx': 3, 'name': 'Driver 3', 'class_position': 2}]})


if __name__ == '__main__':
    unittest.main()
