"""Use the selected pit-wall voice's prepared clips; never create audio here."""
from .advisor import ADVICE_PHRASES, PHRASES


class TireVoiceAudio:
    def __init__(self, cache):
        self.cache = cache

    def available(self, text):
        packs = self.cache.custom_packs
        if packs is not None and packs.has_selection('coach'):
            selected = packs.selections['coach']
            if not packs.is_builtin(selected):
                return packs.has('coach', text)
        return self.cache.cached('coach', text)

    def ready(self):
        return all(self.available(text) for text in PHRASES.values())

    def advice_ready(self):
        # Separate from ready(): advice clips are produced after the original tire set.
        return all(self.available(text) for text in ADVICE_PHRASES.values())

    def __call__(self, text):
        if text not in (*PHRASES.values(), *ADVICE_PHRASES.values()) or not self.available(text):
            raise ValueError('타이어 안내 음원이 준비되지 않았습니다.')
        return self.cache.read_prepared('coach', text)
