import io
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from custom_voice_packs import CustomVoicePacks
from custom_voice_pack_format import FORMAT, export_pack
from voice_pack_installation import InstallProgress, InstallBusy


class InstallationProgressTests(unittest.TestCase):
    def test_reservation_counts_and_terminal_elapsed_time(self):
        now = [10.0]
        state = InstallProgress(clock=lambda: now[0])
        self.assertEqual(state.snapshot()['state'], 'IDLE')
        ident = state.begin('test-one', 'uploading')
        with self.assertRaises(InstallBusy):
            state.begin('test-two', 'checking')
        now[0] = 12.0
        state.update(ident, 'checking', done=4)
        self.assertEqual(state.snapshot()['done'], 4)
        self.assertIsNone(state.snapshot()['total'])
        now[0] = 15.0
        state.complete(ident, {'key': 'pack@1', 'name': '시험팩'})
        now[0] = 20.0
        view = state.snapshot()
        self.assertEqual(view['state'], 'COMPLETE')
        self.assertEqual(view['elapsed_s'], 5.0)
        self.assertEqual(view['imported']['key'], 'pack@1')
        state.begin('test-two', 'checking')
        state.fail(ident, 'late old failure')
        self.assertEqual(state.snapshot()['state'], 'RUNNING')

    def test_real_pack_reports_unique_files_and_keeps_validation_atomic(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream = io.BytesIO()
            with wave.open(stream, 'wb') as output:
                output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                output.writeframes(b'\0\0' * 24000)
            data = stream.getvalue()
            digest = hashlib.sha256(data).hexdigest()
            audio = root / 'audio.wav'
            audio.write_bytes(data)
            rows = [dict(id=f'line{i}', role='coach', sourceText=f'line {i}', text='대본', spoken='대본',
                         audio=f'clips/{digest}.wav', sha256=digest, duration=1) for i in range(3)]
            source = root / 'pack.zip'
            export_pack(source, dict(format=FORMAT, schemaVersion=3, packId='progress', version='1',
                                    name='시험팩', targets=['coach'], entries=rows), {rows[0]['audio']: audio})
            manager = CustomVoicePacks(root / 'installed')
            events = []
            manager.install(source, progress=lambda stage, **values: events.append((stage, values)))
            for stage in ('checking', 'copying', 'verifying'):
                completed = [value for kind, value in events if kind == stage and value.get('done')]
                self.assertEqual(completed[-1]['done'], 1, stage)
            self.assertEqual(manager.list()['packs'][0]['entries'], 3)
            other = CustomVoicePacks(root / 'failed')
            with patch('custom_voice_packs.decode_audio', side_effect=ValueError('손상 음성')):
                with self.assertRaisesRegex(ValueError, '손상 음성'):
                    other.install(source, progress=lambda *args, **kwargs: None)
            self.assertEqual(other.list()['packs'], [])
            self.assertFalse(list(other.root.glob('.import-*')))

    def test_status_never_waits_for_installation_lock(self):
        manager = CustomVoicePacks(Path(tempfile.gettempdir()) / 'unused-progress-test')
        manager.installation.begin('active', 'checking')
        with manager.lock:
            done = threading.Event()
            thread = threading.Thread(target=lambda: (manager.installation.snapshot(), done.set()))
            thread.start()
            self.assertTrue(done.wait(1))
            thread.join()
