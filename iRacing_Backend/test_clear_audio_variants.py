"""Remove only the two reversed clear wordings without changing clear delivery."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
import wave

from custom_voice_pack_format import FORMAT, export_pack
from custom_voice_packs import CustomVoicePacks
from radio_audio import spoken_form
from spotter import PHRASES, VARIANTS, Spotter, SpotterAudio


REMOVED = {'clear_left': '클리어 왼쪽.', 'clear_right': '클리어 오른쪽.'}
RETAINED = {
    'clear_left': ('왼쪽 클리어.', '왼쪽 비었습니다.', '왼쪽 없습니다.', '왼쪽 빠졌습니다.', '왼쪽 깨끗합니다.',
                   '왼쪽 이제 없습니다.', '왼쪽 정리됐습니다.'),
    'clear_right': ('오른쪽 클리어.', '오른쪽 비었습니다.', '오른쪽 없습니다.', '오른쪽 빠졌습니다.', '오른쪽 깨끗합니다.',
                    '오른쪽 이제 없습니다.', '오른쪽 정리됐습니다.'),
}


class ClearAudioVariants(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.speech = MagicMock()
        self.speech.synthesize.side_effect = AssertionError('This regression must only reuse prepared audio.')

    @staticmethod
    def snapshot(root):
        return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size, p.stat().st_mtime_ns)
                for p in root.rglob('*') if p.is_file()}

    def default_audio(self, voice='synthetic-voice'):
        default = SpotterAudio(self.speech, lambda: voice)
        default.directory = self.root / voice
        default.directory.mkdir(exist_ok=True)
        texts = {t for choices in VARIANTS.values() for t in choices} | set(REMOVED.values())
        for text in texts:
            digest = hashlib.sha256(json.dumps([voice, spoken_form(text)], ensure_ascii=False).encode()).hexdigest()
            (default.directory / (digest + '.mp3')).write_bytes(b'ID3' + b'synthetic-audio' * 20)
        return default

    def custom_pack(self, *, retired_clear_only):
        texts = {t for choices in VARIANTS.values() for t in choices} | set(REMOVED.values())
        if retired_clear_only:
            texts.difference_update(t for key in REMOVED for t in VARIANTS[key])
            texts.update(REMOVED.values())
        data = io.BytesIO()
        with wave.open(data, 'wb') as audio:
            audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 24000)
        content = data.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        clip = self.root / 'synthetic.wav'
        clip.write_bytes(content)
        rows = [{'id': f'line{i}', 'role': 'spotter', 'sourceText': text, 'text': text, 'spoken': text,
                 'audio': f'clips/{digest}.wav', 'sha256': digest, 'duration': 1}
                for i, text in enumerate(sorted(texts))]
        manifest = {'format': FORMAT, 'schemaVersion': 1, 'packId': 'synthetic', 'version': '1',
                    'name': 'Synthetic legacy pack', 'entries': rows}
        source = self.root / 'synthetic.zip'
        export_pack(source, manifest, {rows[0]['audio']: clip})
        manager = CustomVoicePacks(self.root / 'installed')
        manager.install(source)
        manager.select('synthetic@1')
        return manager

    def test_clear_events_keep_each_allowed_wording_and_route_its_variant(self):
        spotter = Spotter()
        spotter.run_id = 'synthetic-clear-review'
        spotter.confirmed = 1
        for key, retained in RETAINED.items():
            with self.subTest(key=key):
                events = []
                for count in range(2 * len(retained)):
                    spotter._emit(key, float(count))
                    event = spotter.events[-1]
                    self.assertEqual(event['key'], key)
                    self.assertEqual(event['state'], 1)
                    self.assertEqual(event['run_id'], spotter.run_id)
                    self.assertEqual(VARIANTS[key][event['variant']], event['text'])
                    events.append(event['text'])
                self.assertEqual(events, list(retained) * 2)
                self.assertEqual(PHRASES[key], retained[0])

    def test_prepared_default_excludes_retired_wordings_but_preserves_old_files(self):
        for voice in ('Fenrir', 'Algieba', 'Schedar', 'Aoede'):
            with self.subTest(voice=voice):
                default = self.default_audio(voice)
                before = self.snapshot(default.directory)
                result = default.prepare()
                for key, retained in RETAINED.items():
                    self.assertEqual([c['text'] for c in result['clips'][key]['variants']], list(retained))
                    self.assertEqual(result['clips'][key]['text'], retained[0])
                self.assertEqual(self.snapshot(default.directory), before)
        self.speech.synthesize.assert_not_called()

    def test_legacy_custom_pack_ignores_retired_bindings_without_rewriting_pack(self):
        manager = self.custom_pack(retired_clear_only=False)
        before = self.snapshot(manager.root)
        result = manager.spotter()
        for key, retained in RETAINED.items():
            self.assertEqual([c['text'] for c in result['clips'][key]['variants']], list(retained))
        self.assertEqual(self.snapshot(manager.root), before)
        self.assertEqual(manager.selections, {'spotter': 'synthetic@1'})

    def test_legacy_custom_with_only_retired_clear_uses_prepared_default(self):
        manager = self.custom_pack(retired_clear_only=True)
        default = self.default_audio()
        before = self.snapshot(self.root)
        result = manager.spotter(default)
        for key, retained in RETAINED.items():
            self.assertEqual([c['text'] for c in result['clips'][key]['variants']], list(retained))
            self.assertNotIn(REMOVED[key], [c['text'] for c in result['clips'][key]['variants']])
            self.assertTrue(all(not c['audio_base64'].startswith('data:audio/wav') for c in result['clips'][key]['variants']))
        self.assertEqual(self.snapshot(self.root), before)
        self.speech.synthesize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
