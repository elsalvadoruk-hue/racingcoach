import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
import local_speech
import session_log_auto as auto
from voice_notes import VoiceNoteStore
from types import SimpleNamespace


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        env=patch.dict(os.environ,{'IRACING_DATA_DIR':folder.name,'IRACING_LOG_UPLOAD_URL':'https://script.google.com/macros/s/test/exec'})
        env.start();self.addCleanup(env.stop)
        self.store=VoiceNoteStore(Path(folder.name)/'notes.db')
        self.sender=Mock()
        self.uploader=auto.AutoUploader(self.store,'test',send=self.sender,clock=lambda:1000)

    def note(self,source='LOCAL_VOICE',consent=100,text='연료 얼마야',at=200):
        note=self.store.save(text,{'game':'iracing','private_key':'secret','device_id':'never-upload',
            'local_question':{'model':'sensevoice','topic':'fuel','result':'prepared','test':True,
                              'log_consent_at':consent,'audio_base64':'never-upload'}},source)
        with self.store.connect() as db:db.execute('UPDATE voice_notes SET created_at=? WHERE id=?',(at,note['id']))

    def test_speech_batch_without_driving_excludes_cloud_audio_devices_and_preconsent(self):
        auto.set_enabled(True,auto.CONSENT_VERSION,now=100)
        self.note();self.note('VOICE',text='private cloud');self.note(consent=50,text='old consent');self.note(at=50,text='old')
        self.uploader.run_once()
        self.sender.assert_called_once()
        self.assertRegex(self.sender.call_args.args[1],r'^coach-log-[A-Za-z0-9-]{1,80}\.zip$')
        data=self.sender.call_args.args[2]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            rows=archive.read('local-speech.jsonl').decode('utf8')
            self.assertEqual(len(rows.splitlines()),1)
            self.assertIn('연료 얼마야',rows)
            for secret in ('private cloud','old consent','never-upload','secret'):self.assertNotIn(secret,rows)
        self.uploader.run_once();self.sender.assert_called_once()
        self.assertEqual(auto.status()['speech']['records'],1)

    def test_disabled_or_old_consent_blocks_recognizer_and_upload(self):
        self.note()
        with self.assertRaises(HTTPException):local_speech.require_logs()
        self.uploader.run_once();self.sender.assert_not_called()
        auto.save({'consent':{'version':5,'given_at':100},'runs':{}})
        with self.assertRaises(HTTPException):local_speech.require_logs()
        self.uploader.run_once();self.sender.assert_not_called()

    def test_more_than_one_batch_at_same_timestamp_has_no_loss(self):
        auto.set_enabled(True,auto.CONSENT_VERSION,now=100)
        for i in range(105):self.note(text=str(i))
        self.uploader.run_once()
        self.assertEqual(auto.status()['speech']['records'],100)
        self.uploader.clock=lambda:1061
        self.uploader.run_once()
        self.assertEqual(auto.status()['speech']['records'],105)
        self.assertEqual(self.sender.call_count,2)

    def test_failure_keeps_watermark_and_off_stops_retry(self):
        auto.set_enabled(True,auto.CONSENT_VERSION,now=100);self.note()
        self.sender.side_effect=ValueError('offline')
        self.uploader.run_once()
        self.assertNotIn('through',auto.load()['speech'])
        self.assertEqual(auto.status()['speech']['error'],'offline')
        auto.set_enabled(False);self.uploader.run_once();self.sender.assert_called_once()

    def test_revocation_during_export_never_sends_or_reenables(self):
        auto.set_enabled(True,auto.CONSENT_VERSION,now=100);self.note()
        import speech_diagnostics
        original=speech_diagnostics.batch
        def batch(*args):
            result=original(*args);auto.set_enabled(False);return result
        with patch.object(speech_diagnostics,'batch',side_effect=batch):self.uploader.run_once()
        self.sender.assert_not_called();self.assertFalse(auto.status()['enabled'])

    def test_api_gate_precedes_activation_recognition_and_pit_transport(self):
        app,router=FastAPI(),APIRouter()
        runtime=Mock()
        coach=SimpleNamespace(store=self.store,live=Mock())
        service=local_speech.register(router,coach,Mock());service._runtime=runtime;app.include_router(router)
        with TestClient(app) as client:
            for path,body in [('activate',{}),('test',{'audio_base64':'x'}),
                ('utterances',{'capture_id':'one','text':'피트 연료 20리터 설정','allow_pit_commands':True})]:
                self.assertEqual(client.post('/local-speech/'+path,json={'owner':'test-owner',**body}).status_code,403)
        runtime.activate.assert_not_called();runtime.recognize.assert_not_called();coach.live.context.assert_not_called()

    def test_consent_revoked_while_model_active_heartbeat_unloads(self):
        app,router=FastAPI(),APIRouter();runtime=Mock()
        service=local_speech.register(router,SimpleNamespace(store=self.store),Mock());service._runtime=runtime
        app.include_router(router)
        with TestClient(app) as client:
            self.assertEqual(client.post('/local-speech/heartbeat',json={'owner':'test-owner'}).status_code,403)
        runtime.deactivate.assert_called_once_with('test-owner')
