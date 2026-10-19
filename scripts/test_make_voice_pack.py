"""Voice pack checks use tiny synthetic files and temporary data only."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import make_voice_pack as packs


class VoicePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.radio, self.spotter = self.root / 'radio', self.root / 'spotter'
        self.radio.mkdir(); self.spotter.mkdir()
        (self.radio / 'same.mp3').write_bytes(b'ID3fixed-same')
        (self.radio / 'new.mp3').write_bytes(b'ID3fixed-new')
        (self.spotter / 'changed.MP3').write_bytes(b'ID3fixed-changed')
        (self.radio / 'settings.json').write_text('never ship')
        self.releases = self.root / 'releases'; self.releases.mkdir()
        self.prior = {'version': '1.2.0', 'files': {
            'data/radio_audio/same.mp3': hashlib.sha256(b'ID3fixed-same').hexdigest(),
            'data/spotter_audio/changed.MP3': hashlib.sha256(b'old').hexdigest(),
            'data/settings.json': 'private-setting', 'README.md': 'program-file'}}
        (self.releases / '1.2.0.json').write_text(json.dumps(self.prior))

    def create(self, out='out'):
        return packs.create(self.radio, self.spotter, '1.3.0', ['1.2.0'], self.releases, self.root / out)

    def test_full_and_direct_patch_extract_into_the_existing_app_and_preserve_settings(self):
        result = self.create()
        self.assertEqual([p['voice_files'] for p in result], [3, 2])
        app = self.root / 'app'; (app / 'data').mkdir(parents=True)
        (app / '실행.bat').write_text('launcher')
        (app / 'data/settings.json').write_text('keep me')
        for pack in result:
            file = self.root / 'out' / pack['filename']
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), pack['sha256'])
            with zipfile.ZipFile(file) as archive:
                names = archive.namelist()
                self.assertIn('data/radio_audio/', names)
                self.assertIn('data/spotter_audio/', names)
                self.assertNotIn('data/settings.json', names)
                metadata = json.loads(archive.read('_voice-pack.json'))
                for name, digest in metadata['files'].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
                archive.extractall(app)
        self.assertEqual((app / 'data/settings.json').read_text(), 'keep me')
        self.assertTrue((app / 'data/radio_audio/new.mp3').is_file())
        self.assertEqual((app / '실행.bat').read_text(), 'launcher')

    def test_same_voice_set_makes_a_zero_audio_patch_with_an_explanation(self):
        clips = packs.sources(self.radio, self.spotter)
        self.prior['files'] = {n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in clips.items()}
        (self.releases / '1.2.0.json').write_text(json.dumps(self.prior))
        result = self.create()
        self.assertEqual(result[1]['voice_files'], 0)
        with zipfile.ZipFile(self.root / 'out' / result[1]['filename']) as archive:
            self.assertIn('받을 필요가 없습니다', archive.read('음성 설치 안내.txt').decode('utf-8-sig'))
            self.assertFalse(any(name.lower().endswith('.mp3') for name in archive.namelist()))

    def test_paths_cannot_escape_or_include_other_data(self):
        for name in ['../x.mp3', '/data/radio_audio/x.mp3', 'data/radio_audio/../x.mp3',
                     'data/settings.json', 'data/radio_audio/x.json', 'data\\radio_audio\\x.mp3']:
            self.assertFalse(packs.voice_path(name), name)

    def test_changed_source_does_not_leave_a_finished_or_partial_archive(self):
        target = self.root / 'broken.zip'
        with self.assertRaises(ValueError):
            packs.write_pack(target, packs.sources(self.radio, self.spotter), '1.3.0',
                             expected={'data/radio_audio/new.mp3': 'wrong'})
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.zip.partial').exists())

    def test_missing_baseline_fails_before_creating_a_pack(self):
        with self.assertRaises(FileNotFoundError):
            packs.create(self.radio, self.spotter, '1.3.0', ['1.1.0'], self.releases, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_original_baseline_is_not_replaced_and_outputs_are_not_overwritten(self):
        old = (self.releases / '1.2.0.json').read_bytes()
        self.create()
        self.assertEqual((self.releases / '1.2.0.json').read_bytes(), old)
        with self.assertRaises(FileExistsError):
            self.create()
        (self.radio / 'new.mp3').write_bytes(b'newer-audio')
        with self.assertRaises(ValueError):
            self.create('another-output')
        saved = json.loads((self.releases / 'voice-1.3.0.json').read_text())
        self.assertEqual(saved['files']['data/radio_audio/new.mp3'], hashlib.sha256(b'ID3fixed-new').hexdigest())


if __name__ == '__main__':
    unittest.main()
