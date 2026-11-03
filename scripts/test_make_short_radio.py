import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import make_short_radio
from app_update import VOICE_FORMAT
from builtin_radio import PACKS, PREFIX, VERSION
from custom_voice_pack_format import FORMAT


class StandardShortAssets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packs = {}
        for key, (role, voice) in PACKS.items():
            data = (role + voice).encode() * 100
            sha = hashlib.sha256(data).hexdigest()
            row = {'id': 'line', 'role': role, 'sourceText': 'original', 'text': 'short',
                   'spoken': 'short', 'audio': f'clips/{sha}.ogg', 'duration': 1, 'sha256': sha}
            manifest = {'format': FORMAT, 'schemaVersion': 3, 'packId': 'reviewed', 'version': '1.1.0',
                        'name': voice, 'targets': [role], 'radioStyle': {'length': 'short', 'voice': voice}, 'entries': [row]}
            path = self.root / (key + '.zip')
            with zipfile.ZipFile(path, 'w') as packed:
                packed.writestr('manifest.json', json.dumps(manifest))
                packed.writestr(row['audio'], data)
            self.packs[f'{role}-{voice.lower()}'] = {'file': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.old = {'format': VOICE_FORMAT, 'version': '2.4.3',
                    'files': {'data/radio_audio/old.mp3': ['a' * 64, 200, 'old.zip']},
                    'chunks': {'old.zip': {'sha256': 'b' * 64, 'bytes': 300}}}

    def test_new_standard_payload_keeps_every_old_entry_and_chunk(self):
        out, required = self.root / 'out', self.root / 'required.json'
        result = make_short_radio.prepare(self.packs, self.old, out, required)
        self.assertEqual((result['new_clips'], result['reused_clips']), (14, 1))
        listing = json.loads((out / f'voice-chunks-{VERSION}.json').read_text())
        self.assertEqual(listing['files']['data/radio_audio/old.mp3'], self.old['files']['data/radio_audio/old.mp3'])
        self.assertEqual(listing['chunks']['old.zip'], self.old['chunks']['old.zip'])
        self.assertEqual(set(json.loads(required.read_text())['files']), set(listing['files']))
        for key, (role, voice) in PACKS.items():
            path = PREFIX + key + '/manifest.json'
            sha, size, chunk = listing['files'][path]
            with zipfile.ZipFile(out / chunk) as packed:
                data = packed.read(path)
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
            self.assertEqual(len(data), size)
            manifest = json.loads(data)
            self.assertEqual(manifest['packId'] + '@' + manifest['version'], key)
            self.assertEqual(manifest['targets'], [role])
            self.assertEqual(manifest['radioStyle']['voice'], voice)

    def test_changed_reviewed_zip_is_rejected_before_writing_payload(self):
        first = next(iter(self.packs.values()))
        Path(first['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            make_short_radio.prepare(self.packs, self.old, self.root / 'out', self.root / 'required.json')
        self.assertFalse((self.root / 'out').exists())
        self.assertFalse((self.root / 'required.json').exists())
