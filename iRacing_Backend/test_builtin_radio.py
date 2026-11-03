"""Standard short voices are installed by VoiceSync without custom ZIP import."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import wave
import zipfile

from app_update import VoiceSync, VOICE_FORMAT
from builtin_radio import PACKS, PREFIX, VERSION
from custom_voice_pack_format import FORMAT
from custom_voice_packs import CustomVoicePacks


class BuiltinRadio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = CustomVoicePacks(self.root / 'custom', self.root / 'short')
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as audio:
            audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 24000)
        self.audio = stream.getvalue()
        self.sha = hashlib.sha256(self.audio).hexdigest()
        self.member = f'clips/{self.sha}.wav'

    def manifest(self, key):
        role, voice = PACKS[key]
        return {'format': FORMAT, 'schemaVersion': 3, 'packId': key.partition('@')[0],
                'version': VERSION, 'name': voice, 'targets': [role],
                'radioStyle': {'length': 'short', 'voice': voice},
                'entries': [{'id': 'line', 'role': role, 'sourceText': 'original line',
                             'text': 'short line', 'spoken': 'short line', 'audio': self.member,
                             'sha256': self.sha, 'duration': 1}]}

    def install(self, key, audio=True, manifest=None):
        folder = self.manager._folder(key)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'manifest.json').write_text(json.dumps(manifest or self.manifest(key)), encoding='utf8')
        if audio:
            target = folder / self.member
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(self.audio)
        return folder

    def test_normal_voice_sync_installs_all_seven_without_import_or_selection(self):
        payload = {}
        for key in PACKS:
            payload[PREFIX + key + '/manifest.json'] = json.dumps(self.manifest(key)).encode()
            payload[PREFIX + key + '/' + self.member] = self.audio
        required = {path: hashlib.sha256(data).hexdigest() for path, data in payload.items()}
        release = self.root / 'releases'
        release.mkdir()
        (release / f'voice-{VERSION}.json').write_text(json.dumps({'version': VERSION, 'files': required}))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as packed:
            for path, data in payload.items():
                packed.writestr(path, data)
        data = archive.getvalue()
        listing = {'format': VOICE_FORMAT, 'version': VERSION,
                   'files': {p: [sha, len(payload[p]), 'short.zip'] for p, sha in required.items()},
                   'chunks': {'short.zip': {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}}

        def get(url, target, progress, sha, stop):
            self.assertTrue(url.endswith('/short.zip'))
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            progress(len(data))

        sync = VoiceSync(lambda: {PREFIX: self.manager.builtin_root}, self.root, fetch=lambda url: listing, get=get)
        sync.rescan(wait=True)
        self.assertEqual(sync.status()['missing'], 14)
        sync._run_download()
        self.assertEqual(sync.status()['job']['state'], 'COMPLETE')
        self.assertEqual(sync.status()['missing'], 0)
        self.assertEqual(len(self.manager.list()['packs']), 7)
        self.assertEqual(self.manager.selections, {})
        self.assertFalse(self.manager.root.exists())
        for key, (role, voice) in PACKS.items():
            self.manager.configure({role: key})
            self.assertEqual(base64.b64decode(self.manager.audio(role, 'original line').split(',')[1]), self.audio)
            self.assertTrue(self.manager.list()['packs'][0]['builtin'])

    def test_partial_download_is_unavailable_until_all_audio_exists(self):
        key = next(iter(PACKS))
        self.install(key, audio=False)
        self.assertFalse(self.manager.list()['packs'][0]['canSelect'])
        with self.assertRaises((OSError, ValueError)):
            self.manager.configure({'spotter': key})
        self.install(key)
        self.assertTrue(self.manager.list()['packs'][0]['canSelect'])
        self.manager.configure({'spotter': key})

    def test_mismatched_standard_speaker_is_rejected_at_selection(self):
        key = next(iter(PACKS))
        manifest = self.manifest(key)
        manifest['radioStyle']['voice'] = 'Aoede'
        self.install(key, manifest=manifest)
        self.assertEqual(self.manager.list()['packs'], [])
        with self.assertRaises(ValueError):
            self.manager.configure({'spotter': key})

    def test_builtin_cannot_be_removed_or_overwritten_by_custom_import(self):
        key = next(iter(PACKS))
        folder = self.install(key)
        self.manager.configure({'spotter': key})
        with self.assertRaises(ValueError):
            self.manager.remove(key, delete=True)
        with self.assertRaises(ValueError):
            self.manager.install(folder)
        self.assertTrue((folder / self.member).is_file())
        self.assertEqual(self.manager.selections, {'spotter': key})


if __name__ == '__main__':
    unittest.main()
