"""Finish boundaries and collection provenance, with disposable stores only."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from race_radio import RaceRadio
from voice_notes import VoiceNoteStore
import session_log
import voice_coach


class FinishVoiceTests(unittest.TestCase):
    def setUp(self):
        self.radio = RaceRadio()
        self.sample = dict(session_time=10., session_state=4, session_flags=4, lap_completed=2,
                           class_position=5, on_track=True, is_replay=False, on_pit_road=False)
        self.meta = dict(game='lmu', session_type='Race')
        self.now = 100.
        self.tick()

    def tick(self, advance=.1, run='race', **changes):
        self.now += advance
        self.sample.update(changes, session_time=self.now)
        self.radio.ingest(self.sample, run, self.now, self.meta)

    def test_explicit_finish_works_before_with_or_after_checker(self):
        for order in ('before', 'together', 'after', 'no_checker'):
            with self.subTest(order=order):
                self.setUp()
                if order == 'before':
                    self.tick(session_flags=1)
                self.tick(lap_completed=3, finish_status=1, session_flags=1 if order in ('before','together') else 4)
                if order == 'after':
                    self.tick(session_flags=1)
                self.assertTrue(self.radio.result_called)
                self.tick(advance=.1)
                self.assertEqual(sum(e['key']=='race_result' for e in self.radio.pending), 1)

    def test_running_retired_disqualified_replay_and_offtrack_do_not_finish(self):
        for changes in (dict(finish_status=0), dict(finish_status=2), dict(finish_status=3),
                        dict(finish_status=1, session_flags=0x20001),
                        dict(finish_status=1, is_replay=True), dict(finish_status=1, on_track=False)):
            with self.subTest(changes=changes):
                self.setUp()
                self.tick(session_flags=1)
                self.tick(lap_completed=3, **changes)
                self.assertFalse(self.radio.result_called)

    def test_completed_finish_survives_same_run_gap_but_new_run_can_finish(self):
        self.tick(finish_status=1)
        self.assertTrue(self.radio.result_called)
        self.radio.pending.clear()
        self.tick(advance=1)
        self.assertTrue(self.radio.result_called)
        self.assertFalse(any(e['key']=='race_result' for e in self.radio.pending))
        self.tick(run='new-race', finish_status=0, lap_completed=0, session_flags=4)
        self.assertFalse(self.radio.result_called)
        self.tick(run='new-race', finish_status=1, lap_completed=1)
        self.assertTrue(self.radio.result_called)

    def test_practice_never_gets_a_race_result_and_missing_position_waits(self):
        self.meta['session_type']='Practice'
        self.tick(finish_status=1)
        self.assertFalse(self.radio.result_called)
        self.meta['session_type']='Race'
        self.tick(class_position=None)
        self.assertFalse(self.radio.result_called)
        self.tick(class_position=3)
        self.assertEqual(self.radio.result['class_position'],3)


class CollectionVersionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.store=VoiceNoteStore(Path(tmp.name)/'notes.sqlite3')

    def event(self, **extra):
        return dict(event_id='version-event',run_id='version-run',key='lap_time',status='completed',**extra)

    def manifest(self, version='2.2.0'):
        return session_log.read(session_log.export(self.store,'version-run',version)[1])[0]

    def test_export_version_changes_without_relabelling_collection_versions(self):
        self.store.record_radio([self.event(frontend_version='2.1.2',backend_version='2.1.1')])
        for version in ('2.2.0','2.3.0'):
            manifest=self.manifest(version)
            self.assertEqual(manifest['app_version'],version)
            self.assertEqual(manifest['export_app_version'],version)
            self.assertEqual(manifest['collection_versions'],dict(frontend=['2.1.2'],backend=['2.1.1'],
                              unknown_frontend_events=0,unknown_backend_events=0))

    def test_legacy_and_mixed_records_preserve_unknown_and_all_observed_versions(self):
        self.store.record_radio([self.event(),self.event(frontend_version='2.1.2',backend_version='2.1.2'),
                                 self.event(frontend_version='2.2.0',backend_version='2.2.0')])
        self.assertEqual(self.manifest()['collection_versions'],dict(frontend=['2.1.2','2.2.0'],backend=['2.1.2','2.2.0'],
                         unknown_frontend_events=1,unknown_backend_events=1))

    def test_websocket_identifies_the_backend_that_produced_the_frame(self):
        live=SimpleNamespace(lock=threading.Lock(),radio=SimpleNamespace(welcome=lambda now:None),clock=lambda:0,
             spotter_status=lambda **kw:dict(serial=0,active=False,raw_state=1,state=1,run_id='r',events=[]))
        coach=SimpleNamespace(live=live,speech=None)
        app=FastAPI(version='2.2.0')
        with patch.object(voice_coach,'SpotterAudio'):
            voice_coach.register_voice_routes(app,coach,None,None,None,None)
        with TestClient(app).websocket_connect('/api/live-coach/spotter/events') as ws:
            self.assertEqual(ws.receive_json()['backend_version'],'2.2.0')

    def test_delivery_endpoint_preserves_origin_versions_even_on_a_newer_backend(self):
        live=SimpleNamespace(lock=threading.Lock(),run_id='version-run',identity={'game':'lmu'},
                             pit_wall=SimpleNamespace(race={'session_type':'Race'}))
        coach=SimpleNamespace(live=live,speech=None,store=self.store)
        app=FastAPI(version='2.3.0')
        with patch.object(voice_coach,'SpotterAudio'):
            voice_coach.register_voice_routes(app,coach,None,None,None,None)
        client=TestClient(app)
        response=client.post('/api/live-coach/radio-delivery',json={'events':[
            self.event(frontend_version='2.2.0',backend_version='2.1.2'), self.event()]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.manifest('2.3.0')['collection_versions'],dict(frontend=['2.2.0'],backend=['2.1.2'],
                         unknown_frontend_events=1,unknown_backend_events=1))
        self.assertEqual(client.post('/api/live-coach/radio-delivery',json={'events':[
            self.event(backend_version='not-a-version')]}).status_code,422)


if __name__=='__main__':
    unittest.main()
