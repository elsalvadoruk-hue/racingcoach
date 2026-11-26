import base64
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pit_command_audio as audio
from radio_audio import RadioAudioCache
from radio_button_library import VoiceLibrary


class ReadbackAudioTests(unittest.TestCase):
    def test_success_catalogue_covers_approved_boundaries_and_never_rounds_commands(self):
        rows=list(audio.catalog());self.assertEqual(len(rows),978)
        self.assertEqual(len({r['text'] for r in rows}),969)
        for style in (False,True):
            for value in (1,150):
                self.assertIn(str(value),audio.wordings({'id':'fuel_set','fuel':value},{'result':'command_applied'},style)[0])
            for value in (0,150.5,151,200):
                self.assertEqual(audio.wordings({'id':'fuel_set','fuel':value},{'result':'command_applied'},style),[])
            for mask in audio.TARGETS:
                for value in (100,179,400):
                    words=audio.wordings({'id':'pressure_set','mask':mask,'pressures':{'lf':value}},
                                        {'result':'command_applied'},style)
                    self.assertEqual(len(words),2)
                    self.assertIn(f'{value}킬로파스칼',words[1])
                    self.assertTrue(all(word in audio.texts() for word in words))

    def test_prepared_bank_and_library_use_the_same_file_without_online_synthesis(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{},clear=False):
            cache=RadioAudioCache(Mock(),folder);cache.directory=Path(folder)
            words='추가 급유 150리터로 설정했습니다.'
            path=audio.path_for(folder,words);path.parent.mkdir();path.write_bytes(b'OggS'+b'x'*200)
            packs=SimpleNamespace(lock=threading.RLock(),selections={},loaded={},is_builtin=lambda key:False)
            library=VoiceLibrary(cache,packs,None)
            self.assertTrue(cache.cached('strategy',words))
            self.assertEqual(base64.b64decode(cache.phrase('strategy',words).split(',')[1]),path.read_bytes())
            found=library.browse(group='피트 명령 복창',q='150')
            self.assertEqual(found['total'],1);self.assertEqual(found['clips'][0]['name'],words)
            path.unlink()
            with self.assertRaises(FileNotFoundError):cache.phrase('strategy',words)
            cache.speech.synthesize.assert_not_called()

    def test_partial_pressure_audio_is_withheld_instead_of_reading_only_the_target(self):
        command={'id':'pressure_set','mask':15,'pressures':{'lf':180}}
        buttons=SimpleNamespace(coach=SimpleNamespace(),read_clip=Mock(side_effect=['first',FileNotFoundError()]))
        result=audio.render(command,{'result':'command_applied'},buttons)
        self.assertTrue(result['missing']);self.assertFalse(result['parts']);self.assertFalse(result['spoken'])
