"""Numbers joined from cut pieces: which pieces a value needs, and cutting a sentence at the pauses asked for."""
import unittest

import numpy as np

import number_splice
from number_splice import RATE, cut, join, pieces_for


def tone(seconds):
    return (np.sin(np.arange(int(RATE * seconds)) * .2) * .5).astype(np.float32)


def silence(seconds):
    return np.zeros(int(RATE * seconds), dtype=np.float32)


class NumberSpliceTests(unittest.TestCase):
    def test_each_value_is_said_with_the_right_pieces(self):
        self.assertEqual(pieces_for(2347), [('thousand', 2), ('hundred_end', 3)])
        self.assertEqual(pieces_for(3000), [('thousand_end', 3)])
        self.assertEqual(pieces_for(1350), [('thousand', 1), ('hundred_end', 4)])     # nearest hundred, half up
        self.assertEqual(pieces_for(820), [('hundred_end', 8)])
        self.assertEqual(pieces_for(9960), [('ten_thousand_end', 10)])
        self.assertIsNone(pieces_for(40))
        self.assertIsNone(pieces_for(10100))
        for value in range(100, 10001, 100):
            for piece in pieces_for(value):
                self.assertIn(piece, number_splice.carriers('약'))

    def test_a_sentence_is_cut_at_its_pauses_and_a_broken_word_is_refused(self):
        words = [tone(1.2), tone(.5), tone(.7)]
        sentence = np.concatenate([silence(.3), words[0], silence(.12), tone(.3), silence(.8), words[1], silence(.7), words[2], silence(.2)])
        parts = cut(sentence, 3)
        self.assertEqual(len(parts), 3)
        self.assertAlmostEqual(len(parts[1]) / RATE, .5 + number_splice.LEAD_S + number_splice.TAIL_S, delta=.02)
        # A pause inside a word as long as the ones asked for: not this take.
        broken = np.concatenate([words[0], silence(.8), tone(.2), silence(.5), tone(.3), silence(.7), words[2]])
        self.assertIsNone(cut(broken, 3))
        self.assertIsNone(cut(np.concatenate([words[0], silence(.2), words[1]]), 2))  # no pause at all
        joined = join(parts, [.3, .02])
        self.assertAlmostEqual(len(joined) / RATE, sum(len(p) for p in parts) / RATE + .32, delta=.001)


if __name__ == '__main__':
    unittest.main()


class FakeSpeech:
    """Says each part between [pause] marks as a tone as long as its text, with 0.8 s of silence between."""

    def __init__(self, broken=()):
        self.calls, self.broken = [], set(broken)

    def synthesize(self, text, role='coach', encoding='mp3'):
        assert encoding == 'wav'
        self.calls.append(text)
        words = [tone(.05 * len(part.strip())) for part in text.split('[pause]')]
        if text in self.broken:          # this take paused inside a word
            self.broken.discard(text)
            words[-1] = np.concatenate([words[-1][:2000], silence(.6), words[-1][2000:]])
        audio = [silence(.3)]
        for word in words:
            audio += [word, silence(.8)]
        import base64
        return base64.b64encode(number_splice.write_wav(np.concatenate(audio))).decode()


class SplicedClipTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from radio_audio import RadioAudioCache
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.speech = FakeSpeech(broken=['이번 경기 SOF는 약 [pause] 이천 [pause] 오백입니다.'])
        self.cache = RadioAudioCache(self.speech, Path(self.folder.name))

    def test_a_sof_sentence_is_joined_from_pieces_kept_on_disk(self):
        text = '이번 경기 SOF는 약 2300입니다.'
        self.assertFalse(self.cache.cached('race', text))
        clip = self.cache.phrase('race', text)
        self.assertTrue(clip.startswith('data:audio/wav;base64,'))
        self.assertTrue(self.cache.cached('race', text))
        # Prefix, 이천 (a second take: the first broke the word), 삼백입니다.
        self.assertEqual(len(self.speech.calls), 4)
        import base64
        joined, _ = number_splice.read_wav(base64.b64decode(clip.split(',', 1)[1]))
        spoken = (.05 * len('이번 경기 SOF는 약') + .05 * len('이천') + .05 * len('삼백입니다.'))
        edges = 3 * (number_splice.LEAD_S + number_splice.TAIL_S)
        self.assertAlmostEqual(len(joined) / RATE, spoken + edges + .1 + .01, delta=.05)
        # Another wording only needs its own start; the numbers are shared.
        self.cache.phrase('race', '우리 클래스 SOF는 약 2300입니다.')
        self.assertEqual(len(self.speech.calls), 5)
        self.assertFalse(list((self.cache.directory).glob('*.mp3')))    # no whole clip was made
        # Out of range: a whole clip as before.
        self.assertFalse(self.cache.cached('race', '이번 경기 SOF는 약 12000입니다.'))
        self.assertFalse(number_splice.spliceable('이번 경기 SOF는 약 12000입니다.'))


class PausedSpeech(FakeSpeech):
    """Says each part between any pause mark; hears a piece as what it was asked to say, unless told otherwise."""

    def __init__(self, misheard=()):
        super().__init__()
        self.misheard, self.asked = list(misheard), 0

    def synthesize(self, text, role='coach', encoding='mp3'):
        import re as regex
        return super().synthesize(regex.sub(r'\[pause[^\]]*\]', '[pause]', text), role, encoding)

    def recognize(self, encoded):
        self.asked += 1
        return self.misheard.pop(0) if self.misheard else None


class LapTimeTests(unittest.TestCase):
    def test_a_lap_time_is_the_start_with_its_minutes_and_the_rest(self):
        pieces, gaps = number_splice.plan('이번 랩 1분 42초 3입니다.')
        self.assertEqual([p.says for p in pieces], ['이번 랩 1분', '42초 3입니다.'])
        self.assertEqual(gaps, [0.])
        # The rest is cut from a sentence with another start: it does not depend on the wording.
        self.assertEqual(pieces[1].carrier, '이번 랩 1분 [pause short] 42초 3입니다.')
        # A one-digit second keeps its minutes: a number right after a pause was drawn out ("오오초").
        pieces, _ = number_splice.plan('1분 5초 0입니다.')
        self.assertEqual([p.says for p in pieces], ['1분 5초 0입니다.'])
        pieces, gaps = number_splice.plan('랩타임. 2분 9초 2.')
        self.assertEqual(([p.says for p in pieces], gaps), (['랩타임.', '2분 9초 2.'], [.15]))
        # A start with a full stop inside keeps the voice's own pause there.
        pieces, _ = number_splice.plan('세션 베스트입니다. 1분 47초 5입니다.')
        self.assertEqual((pieces[0].says, pieces[0].count, pieces[0].keep_first), ('세션 베스트입니다. 1분', 3, 1))
        self.assertEqual([p.says for p in number_splice.plan('47초 5 나왔습니다.')[0]], ['47초 5 나왔습니다.'])
        self.assertIsNone(number_splice.plan('클래스 7위에서 출발합니다.'))

    def test_heard_wrong_takes_are_tried_again_and_the_recognizer_habit_is_allowed(self):
        import tempfile
        from pathlib import Path
        from radio_audio import RadioAudioCache
        with tempfile.TemporaryDirectory() as folder:
            speech = PausedSpeech(misheard=['리순 베스트입니다'])
            cache = RadioAudioCache(speech, Path(folder))
            clip = cache.phrase('spotter', '이번 랩 1분 42초 3입니다.')
            self.assertTrue(clip.startswith('data:audio/wav;base64,'))
            self.assertEqual(len(speech.calls), 3)          # the start was heard wrong once and said again
            self.assertTrue(cache.cached('spotter', '이번 랩 1분 42초 3입니다.'))
            self.assertTrue(number_splice.heard_as('방금 랩 12초 사입니다', '방금 랩 12초 4입니다.'))
            self.assertFalse(number_splice.heard_as('리순 베스트입니다', '세션 베스트입니다.'))
