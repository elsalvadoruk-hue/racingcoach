import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from custom_voice_pack_format import FORMAT
from custom_voice_packs import CustomVoicePacks
from radio_audio import RadioAudioCache
from reviewed_radio import ReviewedRadio
from spotter import SpotterAudio


class ReviewedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.review = self.root/'reviewed_radio'; self.review.mkdir()
        self.entries = []
        for n in range(3):
            out=io.BytesIO()
            with wave.open(out,'wb') as w:
                w.setparams((1,2,24000,0,'NONE','')); w.writeframes(bytes([n,0])*24000)
            data=out.getvalue(); sha=hashlib.sha256(data).hexdigest()
            path=self.review/f'clips/{sha}.wav'; path.parent.mkdir(exist_ok=True); path.write_bytes(data)
            self.entries.append(dict(id=f'take{n}',role='spotter',sourceText=f'take{n}',text='new line',spoken='new line',
                eventKey='left',reviewedVoice='Fenrir',sourceTexts=['old line','old alternate'],audio=f'clips/{sha}.wav',sha256=sha,duration=1.))
        self.manifest=dict(format=FORMAT,schemaVersion=3,packId='reviewed',version='1',name='Reviewed',targets=['spotter'],entries=self.entries)
        (self.review/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf-8')
        self.env=patch.dict('os.environ',IRACING_RADIO_AUDIO_DIR='',IRACING_SPOTTER_AUDIO_DIR=str(self.root/'spotter_audio'),IRACING_TTS_SPOTTER_VOICE='ko-KR-Chirp3-HD-Fenrir')
        self.env.start(); self.addCleanup(self.env.stop)

    def test_reviewed_selector_cycles_without_synthesis(self):
        review=ReviewedRadio(self.review)
        audio=[review.audio('spotter','Fenrir',text) for text in ['old line','old alternate','old line','old alternate']]
        self.assertEqual(len(set(audio)),3); self.assertEqual(audio[0],audio[3])
        self.assertTrue(all(a.startswith('data:audio/wav;base64,') for a in audio))

    def _default_cache(self, manager=False):
        cache=RadioAudioCache(Mock(),self.root/'radio_audio')
        if manager:
            cache.custom_packs=CustomVoicePacks(self.root/'custom',self.root/'short')
        path=cache.path('spotter','old line'); path.parent.mkdir(exist_ok=True)
        data=b'original-default-recording-'*20; path.write_bytes(data)
        return cache,base64.b64encode(data).decode('ascii')

    def test_default_radio_uses_original_cache_even_with_reviewed_short_recordings(self):
        cache,expected=self._default_cache()
        self.assertTrue(cache.cached('spotter','old line'))
        self.assertEqual(cache.read_prepared('spotter','old line'),expected)
        self.assertEqual(cache.phrase('spotter','old line'),expected)
        cache.speech.synthesize.assert_not_called()

    def test_unselected_short_pack_does_not_override_default_or_make_missing_cache_ready(self):
        cache,expected=self._default_cache(manager=True)
        self.assertEqual(cache.phrase('spotter','old line'),expected)
        self.assertFalse(cache.cached('spotter','old alternate'))
        with self.assertRaises(FileNotFoundError):cache.read_prepared('spotter','old alternate')
        cache.speech.synthesize.assert_not_called()

    def test_default_spotter_preserves_its_original_wordings_and_recordings(self):
        speech=Mock(); output=SpotterAudio(speech,lambda:'ko-KR-Chirp3-HD-Fenrir')
        output.directory.mkdir()
        expected=[]
        for index,text in enumerate(('old line','old alternate')):
            digest=hashlib.sha256(json.dumps([output.voice(),text],ensure_ascii=False).encode()).hexdigest()
            data=(f'original-spotter-{index}-'.encode())*20
            (output.directory/f'{digest}.mp3').write_bytes(data)
            expected.append(base64.b64encode(data).decode('ascii'))
        with patch('spotter.VARIANTS',{'left':('old line','old alternate')}),patch('spotter.OPTIONAL_CLIPS',{}):
            clips=output.prepare()['clips']['left']
        self.assertEqual([r['audio_base64'] for r in clips['variants']],expected)
        self.assertEqual([r['text'] for r in clips['variants']],['old line','old alternate'])
        self.assertNotIn('reviewed',clips); speech.synthesize.assert_not_called()

    def test_switching_short_pack_off_restores_original_default_recording(self):
        cache,expected=self._default_cache(manager=True)
        manager=cache.custom_packs
        key='standard-short-spotter-fenrir@2.5.0'
        manager.selections={'spotter':key};manager.loaded={key:({}, {})}
        self.assertTrue(cache.phrase('spotter','old line').startswith('data:audio/wav;base64,'))
        manager.selections={}
        self.assertEqual(cache.phrase('spotter','old line'),expected)
        cache.speech.synthesize.assert_not_called()

    def test_broken_short_audio_cannot_break_default_radio(self):
        cache,expected=self._default_cache()
        (self.review/self.entries[0]['audio']).write_bytes(b'broken')
        self.assertEqual(cache.read_prepared('spotter','old line'),expected)
        cache.speech.synthesize.assert_not_called()

    def test_other_speakers_and_explicit_custom_selection_keep_their_audio(self):
        cache=RadioAudioCache(Mock(),self.root/'radio_audio')
        with patch.dict('os.environ',IRACING_TTS_SPOTTER_VOICE='ko-KR-Chirp3-HD-Aoede'):
            self.assertFalse(cache.cached('spotter','old line'))
        manager=CustomVoicePacks(self.root/'custom',self.root/'short'); cache.custom_packs=manager
        manager.selections={'spotter':'custom@1'}
        manager.loaded={'custom@1':({}, {})}
        self.assertFalse(cache.cached('spotter','old line'))
        with self.assertRaises(FileNotFoundError): cache.phrase('spotter','old line')
        cache.speech.synthesize.assert_not_called()

    def test_builtin_selection_uses_reviewed_takes_even_without_old_binding(self):
        manager=CustomVoicePacks(self.root/'custom',self.root/'short')
        manager.selections={'spotter':'standard-short-spotter-fenrir@2.5.0'}
        manager.loaded={next(iter(manager.selections.values())):({}, {})}
        self.assertTrue(manager.has('spotter','old line'))
        self.assertEqual(len({manager.audio('spotter','old line') for _ in range(3)}),3)

    def test_corrupt_or_missing_reviewed_take_fails_without_old_audio_fallback(self):
        review=ReviewedRadio(self.review); self.assertTrue(review.has('spotter','Fenrir','old line'))
        (self.review/self.entries[0]['audio']).write_bytes(b'broken')
        with self.assertRaises(ValueError): review.audio('spotter','Fenrir','old line')
