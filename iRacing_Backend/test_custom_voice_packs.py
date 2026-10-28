import base64,hashlib,io,json,tempfile,unittest,wave,zipfile
from pathlib import Path
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from custom_voice_pack_format import FORMAT,export_pack
from custom_voice_packs import CustomVoicePacks,MissingCustomAudio
from radio_audio import RadioAudioCache
from spotter import VARIANTS
from live_telemetry import LiveTelemetry
from voice_notes import VoiceNoteStore
from voice_coach import VoiceCoach,register_voice_routes

class Packs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        b=io.BytesIO()
        with wave.open(b,'wb') as w:w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*24000)
        self.data=b.getvalue();self.hash=hashlib.sha256(self.data).hexdigest();self.audio=self.root/'clip.wav';self.audio.write_bytes(self.data)
        self.manager=CustomVoicePacks(self.root/'installed')
    def tearDown(self):self.temp.cleanup()
    def pack(self,complete=True,version='1',extra_texts=()):
        rows=[];texts=list(dict.fromkeys(([opts[0] for opts in VARIANTS.values()] if complete else ['원문'])+list(extra_texts)))
        for i,t in enumerate(texts):rows.append({'id':f't{i}','role':'spotter','sourceText':t,'text':'커스텀 대본','spoken':'커스텀 대본','audio':f'clips/{self.hash}.wav','sha256':self.hash,'duration':1})
        m={'format':FORMAT,'schemaVersion':1,'packId':'test','version':version,'name':'시험팩','entries':rows};z=self.root/f'pack-{version}.zip'
        export_pack(z,m,{rows[0]['audio']:self.audio});return z
    def test_install_select_and_no_cloud_fallback(self):
        z=self.pack();info=self.manager.install(z);self.assertTrue(info['canSelect']);self.manager.select(info['key'])
        speech=MagicMock();cache=RadioAudioCache(speech,self.root/'builtin');cache.custom_packs=self.manager
        source=VARIANTS['test'][0];result=cache.phrase('spotter',source)
        self.assertEqual(base64.b64decode(result.split(',')[1]),self.data);self.assertTrue(cache.cached('spotter',source))
        with self.assertRaises((ValueError,FileNotFoundError)):cache.phrase('spotter','없는 숫자 8.2초')
        speech.synthesize.assert_not_called();self.assertFalse(cache.cached('race','없는 문장'))
        prepared=self.manager.spotter();self.assertEqual(set(prepared['clips']),set(VARIANTS))
    def test_partial_can_select_but_missing_default_spotter_has_actionable_error(self):
        info=self.manager.install(self.pack(False));self.assertTrue(info['canSelect'])
        self.manager.select(info['key'])
        with self.assertRaisesRegex(MissingCustomAudio,'기본 음성'):self.manager.spotter()
    def test_oval_clip_is_optional_and_uses_the_selected_custom_pack(self):
        from oval_spotter import OPTIONAL_CLIPS
        text=OPTIONAL_CLIPS['oval_inside']
        info=self.manager.install(self.pack(extra_texts=[text]));self.manager.select(info['key'])
        prepared=self.manager.spotter()
        self.assertEqual(prepared['clips']['oval_inside']['text'],text)
        self.assertEqual(base64.b64decode(prepared['clips']['oval_inside']['audio_base64'].split(',')[1]),self.data)
        self.assertNotIn('oval_outside',prepared['clips'])
    def test_tamper_rejected_and_previous_version_preserved(self):
        self.manager.install(self.pack());self.manager.select('test@1');old=self.manager.audio('spotter',VARIANTS['test'][0])
        z=self.pack(version='2')
        with zipfile.ZipFile(z,'a') as f:f.writestr('script.py','bad')
        with self.assertRaises(ValueError):self.manager.install(z)
        self.assertEqual(self.manager.audio('spotter',VARIANTS['test'][0]),old);self.assertFalse((self.root/'installed/test@2').exists())
    def test_repeat_install_and_folder_import(self):
        z=self.pack();self.manager.install(z);self.assertEqual(self.manager.install(z)['key'],'test@1')
        copy=CustomVoicePacks(self.root/'copy');self.assertEqual(copy.install(self.root/'installed/test@1')['key'],'test@1')
    def test_routes_upload_settings_and_prepare_without_synthesis(self):
        speech=MagicMock();coach=VoiceCoach(LiveTelemetry(),VoiceNoteStore(self.root/'notes.db'),speech=speech)
        app=FastAPI();register_voice_routes(app,coach,None,None,None,None)
        with TestClient(app) as client:
            r=client.post('/api/live-coach/custom-voice-packs/upload',content=self.pack().read_bytes(),headers={'Content-Type':'application/zip'})
            self.assertEqual(r.status_code,200,r.text)
            r=client.post('/api/live-coach/spotter/prepare',json={'custom_pack':'test@1'})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['custom_pack'],'test@1')
            self.assertIn('voice_packs',client.get('/api/live-coach/radio-audio').json())
            speech.synthesize.assert_not_called()
            self.assertEqual(client.put('/api/live-coach/spotter/settings',json={'custom_pack':None}).status_code,200)
            self.assertIsNone(coach.custom_packs.selected)
            self.assertFalse(list(coach.custom_packs.root.glob('.upload-*')))
    def test_install_status_is_responsive_and_duplicate_upload_is_rejected(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        entered,release=threading.Event(),threading.Event()
        def blocked(row,data):entered.set();self.assertTrue(release.wait(5))
        coach=VoiceCoach(LiveTelemetry(),VoiceNoteStore(self.root/'progress.db'),speech=MagicMock())
        coach.custom_packs=self.manager
        app=FastAPI();register_voice_routes(app,coach,None,None,None,None)
        data=self.pack().read_bytes()
        with TestClient(app) as client,ThreadPoolExecutor(max_workers=1) as pool,patch('custom_voice_packs.decode_audio',side_effect=blocked):
            future=pool.submit(client.post,'/api/live-coach/custom-voice-packs/upload?operation_id=progress-test',content=data)
            try:
                self.assertTrue(entered.wait(3))
                view=client.get('/api/live-coach/custom-voice-packs/install-status').json()
                self.assertEqual((view['id'],view['state'],view['stage']),('progress-test','RUNNING','checking'))
                self.assertEqual(client.post('/api/live-coach/custom-voice-packs/upload',content=data).status_code,409)
            finally:release.set()
            self.assertEqual(future.result(timeout=5).status_code,200)
            view=client.get('/api/live-coach/custom-voice-packs/install-status').json()
            self.assertEqual(view['state'],'COMPLETE');self.assertEqual(view['imported']['key'],'test@1')
            self.assertEqual(view['done'],1)
            self.assertFalse(list(self.manager.root.glob('.upload-*')))
    def test_bad_upload_is_visible_and_retry_selection_verifies_real_files(self):
        coach=VoiceCoach(LiveTelemetry(),VoiceNoteStore(self.root/'retry.db'),speech=MagicMock());coach.custom_packs=self.manager
        app=FastAPI();register_voice_routes(app,coach,None,None,None,None)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/live-coach/custom-voice-packs/upload',content=b'not a zip').status_code,422)
            failed=client.get('/api/live-coach/custom-voice-packs/install-status').json()
            self.assertEqual(failed['state'],'FAILED');self.assertTrue(failed['error'])
            self.assertFalse(list(self.manager.root.glob('.upload-*')))
            self.assertEqual(client.post('/api/live-coach/custom-voice-packs/import',json={'path':str(self.pack())}).status_code,200)
            self.assertEqual(client.put('/api/live-coach/spotter/settings',json={'voice_packs':{'spotter':'test@1'}}).status_code,200)
            selected=client.get('/api/live-coach/custom-voice-packs/install-status').json()
            self.assertEqual((selected['state'],selected['kind'],selected['done']),('COMPLETE','selection',1))
            coach.speech.synthesize.assert_not_called()
if __name__=='__main__':unittest.main()
