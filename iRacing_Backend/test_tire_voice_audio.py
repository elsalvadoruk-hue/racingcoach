from unittest.mock import Mock
import unittest
from tire_reader.voice_audio import TireVoiceAudio
from tire_reader.advisor import PHRASES


class AudioTests(unittest.TestCase):
    def test_missing_audio_never_requests_synthesis_or_different_voice(self):
        cache=Mock();cache.custom_packs=None;cache.cached.return_value=False
        audio=TireVoiceAudio(cache)
        self.assertFalse(audio.ready())
        with self.assertRaises(ValueError):audio(next(iter(PHRASES.values())))
        cache.phrase.assert_not_called();cache.read_prepared.assert_not_called()
        cache.cached.return_value=True
        text=next(iter(PHRASES.values()));audio(text)
        cache.read_prepared.assert_called_once_with('coach',text)

    def test_custom_selection_does_not_fall_back_to_a_different_default_voice(self):
        cache=Mock();packs=cache.custom_packs
        packs.has_selection.return_value=True;packs.selections={'coach':'external@1.0'}
        packs.is_builtin.return_value=False;packs.has.return_value=False;cache.cached.return_value=True
        audio=TireVoiceAudio(cache)
        with self.assertRaises(ValueError):audio(next(iter(PHRASES.values())))
        cache.read_prepared.assert_not_called()
