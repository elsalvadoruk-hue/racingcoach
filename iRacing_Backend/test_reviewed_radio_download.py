"""Downloaded approved radio uses the same managed location as playback."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import backend_api
from app_update import VoiceSync, VOICE_FORMAT


class ReviewedDownloadTests(unittest.TestCase):
    def test_download_destination_matches_the_playback_folder(self):
        expected = backend_api.voice_coach.custom_packs.reviewed.directory
        self.assertEqual(backend_api.voice_folders()['data/reviewed_radio/'], expected)

    def test_voice_sync_installs_reviewed_manifest_and_clips_and_preserves_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);review=root/'data/reviewed_radio'
            preserved=root/'data/radio_audio/original.mp3'
            preserved.parent.mkdir(parents=True);preserved.write_bytes(b'original')
            files={'data/reviewed_radio/manifest.json':b'{"approved":true}',
                   'data/reviewed_radio/clips/approved.ogg':b'approved synthetic clip'}
            hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
            (root/'releases').mkdir();(root/'releases/voice-2.5.6.json').write_text(json.dumps({'files':hashes}))
            packed=root/'source.zip'
            with zipfile.ZipFile(packed,'w') as archive:
                for name,data in files.items():archive.writestr(name,data)
            remote=dict(format=VOICE_FORMAT,version='2.5.6',files={n:[hashes[n],len(data),'approved.zip'] for n,data in files.items()},
                        chunks={'approved.zip':dict(bytes=packed.stat().st_size,sha256=hashlib.sha256(packed.read_bytes()).hexdigest())})
            def download(url,target,progress,checksum,stop):
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(packed.read_bytes())
            with patch.object(backend_api.voice_coach.custom_packs.reviewed,'directory',review):
                sync=VoiceSync(backend_api.voice_folders,root,fetch=lambda url:remote,get=download)
                sync._run_download()
            self.assertEqual(sync.status()['job']['state'],'COMPLETE')
            self.assertEqual(sync.status()['job']['files_done'],2)
            for name,data in files.items():self.assertEqual((root/name).read_bytes(),data)
            self.assertEqual(preserved.read_bytes(),b'original')


if __name__=='__main__':unittest.main()
