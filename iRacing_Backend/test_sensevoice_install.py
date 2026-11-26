import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_speech_worker import download_sensevoice
from local_speech_runtime import LocalSpeechRuntime


class SenseVoiceInstallTests(unittest.TestCase):
    def test_verified_existing_model_uses_no_network(self):
        with tempfile.TemporaryDirectory() as folder:
            payload=b'model';target=Path(folder)/'model.int8.onnx';target.write_bytes(payload)
            manifest=[{'file':target.name,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'url':'https://example.test/model'}]
            with patch.object(Path,'read_text',return_value=json.dumps(manifest)),patch('urllib.request.urlopen') as network:
                download_sensevoice(folder)
                network.assert_not_called()

    def test_bad_download_preserves_old_file_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'model.int8.onnx';target.write_bytes(b'old')
            manifest=[{'file':target.name,'size':3,'sha256':hashlib.sha256(b'new').hexdigest(),'url':'https://example.test/model'}]
            with patch.object(Path,'read_text',return_value=json.dumps(manifest)),patch('urllib.request.urlopen',return_value=io.BytesIO(b'bad')):
                with self.assertRaises(ValueError):download_sensevoice(folder)
            self.assertEqual(target.read_bytes(),b'old')
            self.assertFalse(target.with_suffix('.onnx.part').exists())

    def test_sensevoice_installation_is_separate_from_whisper(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=LocalSpeechRuntime(folder)
            try:
                self.assertNotEqual(runtime.package_dir('sensevoice'),runtime.package_dir('base'))
                self.assertEqual(runtime.status()['model'],'sensevoice')
                self.assertFalse(runtime.installed('sensevoice'))
                for name in ('sherpa_onnx','numpy'):(runtime.package_dir('sensevoice')/name).mkdir(parents=True)
                (Path(folder)/'sensevoice').mkdir()
                for name in ('model.int8.onnx','tokens.txt'):(Path(folder)/'sensevoice'/name).touch()
                self.assertTrue(runtime.installed('sensevoice'))
                self.assertFalse(runtime.installed('base'))
            finally:runtime.close()
