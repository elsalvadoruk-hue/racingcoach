"""Real codec round trips with synthetic silence; no model, GPU or personal store."""
import hashlib,io,json
from pathlib import Path
from unittest.mock import patch
import soundfile as sf
from custom_voice_pack_format import FORMAT,export_pack
from custom_voice_packs import decode_audio
from spotter import VARIANTS
import test_custom_voice_packs as base_packs

class OpusPacks(base_packs.Packs):
    def setUp(self):
        super().setUp();encoded=io.BytesIO()
        pcm,rate=sf.read(io.BytesIO(self.data),dtype='int16')
        sf.write(encoded,pcm,rate,format='OGG',subtype='OPUS',compression_level=.9)
        self.data=encoded.getvalue();self.hash=hashlib.sha256(self.data).hexdigest()
        self.audio=self.root/'synthetic.ogg';self.audio.write_bytes(self.data)
    def pack(self,complete=True,version='1',extra_texts=()):
        texts=list(dict.fromkeys([v[0] for v in VARIANTS.values()] if complete else ['원문']))
        texts.extend(text for text in extra_texts if text not in texts)
        rows=[{'id':f't{i}','role':'spotter','sourceText':text,'text':'시험 음성','spoken':'시험 음성','eventKey':'test' if text==VARIANTS['test'][0] else 'fixture',
               'audio':f'clips/{self.hash}.ogg','sha256':self.hash,'duration':1} for i,text in enumerate(texts)]
        m={'format':FORMAT,'schemaVersion':2,'packId':'test','version':version,'name':'Opus 시험팩','entries':rows}
        target=self.root/f'opus-{version}.zip';export_pack(target,m,{rows[0]['audio']:self.audio});return target
    def test_preview_does_not_select_and_returns_correct_mime(self):
        info=self.manager.install(self.pack());result=self.manager.preview(info['key'])
        self.assertIsNone(self.manager.selected)
        self.assertTrue(result['audio_base64'].startswith('data:audio/ogg;codecs=opus;base64,'))
        self.assertEqual(info['audioFormat'],'Ogg Opus');self.assertEqual(info['schemaVersion'],2)
    def test_unique_audio_is_decoded_once_on_install(self):
        pack=self.pack()
        with patch('custom_voice_packs.decode_audio',wraps=decode_audio) as decode:self.manager.install(pack);self.assertEqual(decode.call_count,1)

if __name__=='__main__':__import__('unittest').main()
