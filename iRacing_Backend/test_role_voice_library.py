import base64,hashlib,io,json,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import MagicMock,patch
from custom_voice_pack_format import FORMAT,export_pack,binding_signature
from custom_voice_packs import CustomVoicePacks
from radio_audio import RadioAudioCache

class RoleVoiceLibrary(unittest.TestCase):
    def setUp(self):
        environment=patch.dict('os.environ',{'IRACING_RADIO_AUDIO_DIR':''})
        environment.start();self.addCleanup(environment.stop)
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);b=io.BytesIO()
        with wave.open(b,'wb') as w:w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*24000)
        self.data=b.getvalue();self.sha=hashlib.sha256(self.data).hexdigest();self.wav=self.root/'voice.wav';self.wav.write_bytes(self.data)
        self.manager=CustomVoicePacks(self.root/'installed')
    def tearDown(self):self.tmp.cleanup()
    def install(self,ident,role,text):
        row={'id':'line','role':role,'sourceText':text,'text':text,'spoken':text,'audio':f'clips/{self.sha}.wav','sha256':self.sha,'duration':1}
        manifest={'format':FORMAT,'schemaVersion':3,'packId':ident,'version':'1','name':ident,'targets':[role],
                  'catalogBaseline':{role:[binding_signature(role,text)]},'entries':[row]}
        file=self.root/(ident+'.zip');export_pack(file,manifest,{row['audio']:self.wav});return self.manager.install(file)
    def test_independent_roles_partial_pack_and_prepared_default_fallback(self):
        a=self.install('spot','spotter','custom spotter');b=self.install('coach','coach','custom coach')
        self.manager.configure({'spotter':a['key'],'coach':b['key']})
        cache=RadioAudioCache(MagicMock(),self.root/'default');cache.custom_packs=self.manager
        self.assertTrue(cache.cached('coach','custom coach'));self.assertTrue(cache.cached('spotter','custom spotter'))
        path=cache.path('spotter','default spotter');path.parent.mkdir(parents=True);path.write_bytes(b'builtin'*30)
        self.assertEqual(base64.b64decode(cache.phrase('spotter','default spotter')),b'builtin'*30)
        with self.assertRaises((ValueError,FileNotFoundError)):cache.phrase('spotter','unprepared')
        cache.speech.synthesize.assert_not_called()
    def test_unapply_keeps_files_delete_only_managed_copy(self):
        info=self.install('coach','coach','line');self.manager.configure({'coach':info['key']})
        self.manager.remove(info['key'],False);self.assertTrue((self.manager.root/info['key']).is_dir());self.assertFalse(self.manager.selected)
        self.manager.remove(info['key'],True);self.assertEqual(self.manager.list()['packs'],[]);self.assertTrue(self.wav.exists())
        with self.assertRaises(ValueError):self.manager.remove('../escape@1',True)
    def test_new_catalog_lines_are_distinct_from_intentionally_partial_coverage(self):
        info=self.install('coach','coach','old')
        rows=[('coach','old'),('coach','new'),('strategy','not this role')]
        result=self.manager.updates(info['key'],rows)
        self.assertEqual(result['newCount'],1);self.assertEqual(result['roles']['coach'],1)
        self.assertEqual(result['newEntries'],[{'role':'coach','sourceText':'new'}])
        file=self.manager.root/info['key']/'manifest.json';m=json.loads(file.read_text(encoding='utf-8'))
        m['catalogBaseline']['coach'].append(binding_signature('coach','not made'));file.write_text(json.dumps(m),encoding='utf-8')
        result=self.manager.updates(info['key'],rows+[('coach','not made')]);self.assertEqual(result['newCount'],1)
    def test_missing_number_pieces_never_call_synthesis_and_prepared_pieces_work(self):
        import number_splice
        info=self.install('numbers','race','custom');self.manager.configure({'race':info['key']})
        cache=RadioAudioCache(MagicMock(),self.root/'builtin');cache.custom_packs=self.manager;text='이번 경기 SOF는 약 2300입니다.'
        self.assertTrue(number_splice.spliceable(text))
        with self.assertRaises(ValueError):cache.phrase('race',text)
        pieces=cache.pieces();pieces.directory.mkdir(parents=True,exist_ok=True)
        for piece in number_splice.plan(text)[0]:pieces.path('race',piece.key).with_suffix('.wav').write_bytes(self.data)
        self.assertTrue(cache.cached('race',text));self.assertTrue(cache.phrase('race',text).startswith('data:audio/wav'))
        cache.speech.synthesize.assert_not_called()
    def test_role_configuration_failure_is_atomic_and_unapply_keeps_other_roles(self):
        a=self.install('spot','spotter','line');b=self.install('coach','coach','line');self.manager.configure({'spotter':a['key'],'coach':b['key']})
        with self.assertRaises(ValueError):self.manager.configure({'race':a['key']})
        self.assertEqual(self.manager.selections,{'spotter':a['key'],'coach':b['key']})
        self.manager.remove(b['key']);self.assertEqual(self.manager.selections,{'spotter':a['key']})
    def test_partial_spotter_uses_readonly_default_clips_and_routes_report_updates(self):
        from spotter import SpotterAudio,VARIANTS
        from radio_audio import spoken_form
        info=self.install('partial','spotter',VARIANTS['test'][0]);self.manager.configure({'spotter':info['key']})
        default=SpotterAudio(MagicMock(),lambda:'test');default.directory=self.root/'spotter';default.directory.mkdir()
        for choices in VARIANTS.values():
            t=choices[0];sig=hashlib.sha256(json.dumps(['test',spoken_form(t)],ensure_ascii=False).encode()).hexdigest();(default.directory/(sig+'.mp3')).write_bytes(b'default'*50)
        result=self.manager.spotter(default);self.assertEqual(set(result['clips']),set(VARIANTS));default.speech.synthesize.assert_not_called()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from voice_coach import VoiceCoach,register_voice_routes
        from voice_notes import VoiceNoteStore
        from live_telemetry import LiveTelemetry
        coach=VoiceCoach(LiveTelemetry(),VoiceNoteStore(self.root/'notes.db'),speech=MagicMock());coach.custom_packs=self.manager
        app=FastAPI();register_voice_routes(app,coach,None,None,None,None)
        with TestClient(app) as client,patch('voice_catalog_diff.current_bindings',return_value=(('spotter','new'),)):
            r=client.put('/api/live-coach/spotter/settings',json={'voice_packs':{'spotter':info['key']}});self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(client.get('/api/live-coach/custom-voice-packs').json()['packs'][0]['updates']['newCount'],1)
            self.assertEqual(client.post('/api/live-coach/custom-voice-packs/updates',json={'key':info['key']}).json()['newEntries'],[{'role':'spotter','sourceText':'new'}])
            self.assertEqual(client.post('/api/live-coach/custom-voice-packs/remove',json={'key':info['key'],'delete':False}).status_code,200)
            self.assertFalse(self.manager.selected);self.assertTrue((self.manager.root/info['key']).exists())
