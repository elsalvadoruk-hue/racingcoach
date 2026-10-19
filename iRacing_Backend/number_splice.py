"""Numbers said from a few cut pieces instead of one clip for every value.

A whole clip per number made the voice cache large. Here each piece is cut out of a whole sentence
the voice was asked to say with pauses around the number ("…약 [pause] 이천 [pause] 삼백입니다."), so a
piece keeps the run-on intonation it had inside a sentence; a word synthesized on its own ends like a
sentence of its own, which is why joined fragments were dropped once (AGENTS 6-2).

Strength of field (100 to 10000, to the nearest hundred) is built from:
  prefix ("이번 경기 SOF는 약")  + thousands ("이천")  + hundreds with the ending ("삼백입니다.")
  prefix                       + thousands with the ending ("삼천입니다.", "만입니다.")
  prefix                       + hundreds with the ending ("팔백입니다.")
"""
import base64
import hashlib
import io
import json
import re
import threading
import wave
from pathlib import Path

import numpy as np

from radio_audio import sino

# The sentences said this way: their wordings end in "{rating}입니다." and the start is the prefix piece.
SPLICED = ('form_sof', 'form_sof_class')   # match() also looks for 'SOF' first: change it when adding keys
BASE_PREFIX = '이번 경기 SOF는 약'   # the number pieces are cut from sentences with this start, whatever the wording
# Silences between the pieces, chosen by ear (artifacts/voice-pilot/splice.html, 2026-10-19).
AFTER_PREFIX_S, INSIDE_NUMBER_S = .1, .01
TAKES = 5                  # the voice sometimes pauses inside a number; another take of the sentence usually does not
VERSION = 1                # raise when the carriers or the cutting change: pieces are kept under it

RATE = 24000
FRAME_S = .01
WORD_GAP_S = .3        # longer than this inside a piece, the voice broke a word apart
GAP_S = .4             # a [pause] left 0.55-1.2 s of silence; gaps between words were at most 0.5 s
QUIET = .03            # of the loudest frame: below this a frame is silence
SOFT, SOFT_REACH_S = .002, .15   # a piece's edges follow sound this faint, at most this far
LEAD_S, TAIL_S = .03, .05  # kept around the sound of a piece, so soft onsets and endings are not clipped


def pieces_for(value):
    """The piece names of a strength of field, rounded to the nearest hundred: [('thousand', 2), ('hundred_end', 3)]."""
    hundreds = int(value / 100 + .5)
    if not 1 <= hundreds <= 100:
        return None
    if hundreds == 100:
        return [('ten_thousand_end', 10)]
    thousands, rest = divmod(hundreds, 10)
    if not thousands:
        return [('hundred_end', rest)]
    return [('thousand_end', thousands)] if not rest else [('thousand', thousands), ('hundred_end', rest)]


def carriers(prefix):
    """{piece: (sentence to synthesize, which cut part is the piece)} for one prefix wording."""
    words = {
        ('prefix', 0): (f'{prefix} [pause] 이천 [pause] 삼백입니다.', 0),
        **{('thousand', d): (f'{prefix} [pause] {sino(d * 1000)} [pause] 오백입니다.', 1) for d in range(1, 10)},
        **{('thousand_end', d): (f'{prefix} [pause] {sino(d * 1000)}입니다.', 1) for d in range(1, 10)},
        ('ten_thousand_end', 10): (f'{prefix} [pause] 만입니다.', 1),
        **{('hundred_end', h): (f'{prefix} [pause] 이천 [pause] {sino(h * 100)}입니다.', 2) for h in range(1, 10)},
    }
    return words


def read_wav(data):
    with wave.open(io.BytesIO(data)) as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError('16-bit mono expected')
        return np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float32) / 32768, source.getframerate()


def write_wav(samples, rate=RATE):
    out = io.BytesIO()
    with wave.open(out, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
    return out.getvalue()


def cut(samples, count, rate=RATE, word_gap=WORD_GAP_S, gap=GAP_S, ratio=1., keep_first=0):
    """The sentence in count spoken parts, split at its count - 1 longest silences (the pauses asked for).

    None when those silences are not clearly pauses (shorter than GAP_S): the sentence was not said as asked.
    """
    frame = int(rate * FRAME_S)
    frames = len(samples) // frame
    if not frames:
        return None
    level = np.sqrt(np.mean(samples[:frames * frame].reshape(frames, frame) ** 2, axis=1))
    loud = np.flatnonzero(level > level.max() * QUIET)
    if not len(loud):
        return None
    # Silent runs between loud frames: (length in frames, first silent frame).
    runs = sorted(((later - earlier - 1, earlier + 1) for earlier, later in zip(loud, loud[1:]) if later - earlier > 1), reverse=True)
    splits = sorted(runs[:count - 1], key=lambda run: run[1])
    if len(splits) != count - 1 or any(length * FRAME_S < gap for length, _ in splits):
        return None
    # Any other long silence means the voice paused inside a word ("칠… 천입니다"): that take is not used.
    # ratio < 1: the next silence must also be clearly shorter than the pauses (a short pause is only 0.3-0.7 s).
    if len(runs) >= count and (runs[count - 1][0] * FRAME_S >= word_gap or runs[count - 1][0] > ratio * min(length for length, _ in splits)):
        return None
    bounds = [loud[0]] + [edge for length, first in splits for edge in (first - 1, first + length)] + [loud[-1]]
    # A soft start (the ㅅ of "세션") stays under QUIET: follow the sound out to where it is really silent.
    soft = level > max(level.max() * SOFT, np.percentile(level, 5) * 4)
    reach = int(SOFT_REACH_S / FRAME_S)
    for index in range(0, len(bounds), 2):
        floor = bounds[index - 1] + 1 if index else 0
        while bounds[index] > floor and bounds[index] - 1 >= 0 and soft[bounds[index] - 1] and reach > 0:
            bounds[index] -= 1
            reach -= 1
        reach = int(SOFT_REACH_S / FRAME_S)
        ceiling = bounds[index + 2] - 1 if index + 2 < len(bounds) else frames - 1
        while bounds[index + 1] < ceiling and soft[bounds[index + 1] + 1] and reach > 0:
            bounds[index + 1] += 1
            reach -= 1
        reach = int(SOFT_REACH_S / FRAME_S)
    # keep_first: the first pauses are the voice's own ("랩타임. 7분"), found only to tell them from the one to cut at.
    bounds = bounds[:1] + bounds[1 + 2 * keep_first:]
    lead, tail = int(LEAD_S / FRAME_S), int(TAIL_S / FRAME_S)
    parts = []
    for first, last in zip(bounds[::2], bounds[1::2]):
        part = samples[max(0, first - lead) * frame:min(frames, last + 1 + tail) * frame].copy()
        fade = min(len(part) // 4, int(rate * .005))
        if fade:
            part[:fade] *= np.linspace(0, 1, fade)
            part[-fade:] *= np.linspace(1, 0, fade)
        parts.append(part)
    return parts


# Pieces are kept as Ogg Opus: about a sixth of the WAV, and the length comes back to the sample (checked on
# real pieces, 2026-10-19). 0.9 is about 37 kbit/s like the MP3 clips; the driver heard no difference from
# uncompressed or from 0.8 (artifacts/voice-pilot/ogg.html).
OPUS_LEVEL = .9


def save(path, samples, rate=RATE):
    """Keep a piece as Ogg Opus (written beside it first, so a reader never sees half a file)."""
    import soundfile
    temporary = path.with_name(path.stem + '.part.ogg')
    soundfile.write(str(temporary), samples, rate, format='OGG', subtype='OPUS', compression_level=OPUS_LEVEL)
    temporary.replace(path)


def load(path):
    """The samples of a piece kept as Ogg Opus or (from before) WAV."""
    if path.suffix == '.wav':
        return read_wav(path.read_bytes())[0]
    import soundfile
    samples, _rate = soundfile.read(str(path), dtype='float32')
    return samples


def join(parts, gaps, rate=RATE):
    """Pieces with the given silences between them (len(gaps) == len(parts) - 1)."""
    out = [parts[0]]
    for gap, part in zip(gaps, parts[1:]):
        out += [np.zeros(int(rate * gap), dtype=np.float32), part]
    return np.concatenate(out)


# --- Lap times ------------------------------------------------------------------------------------------
# A lap time sentence is two pieces cut at one short pause after the minutes: the start of the wording with the
# minutes ("이번 랩 1분") and the rest ("42초 3입니다."). A number right after a pause was drawn out ("오오초"), so
# a one-digit second keeps its minutes with it ("이번 랩" + "1분 5초 0입니다."). Chosen by ear 2026-10-19
# (artifacts/voice-pilot/lap-splice.html, scripts/voice_pilot_lap_splice.py); AGENTS 6-117 has what failed.
LAP_KEYS = ('lap_time_read', 'personal_best_read', 'form_lap')
LAP_BASE = '이번 랩'          # the start of the sentences the rest is cut from, whatever the wording
LAP_VERSION = 1
LAP_CUT = {'gap': .2, 'word_gap': .34, 'ratio': .7}   # a short pause left 0.27-0.8 s; inside a piece at most 0.33 s
AFTER_STOP_S = .15             # after a start that ends a sentence of its own ("랩타임.")
LAP_NUMBER = r'(?:(\d+)분 )?(\d+)초 (\d)'


class Piece:
    """One cut piece: what it says, the sentence it is cut from and which part of that it is."""

    def __init__(self, key, carrier, index, says=None, kind=None, cutting=None, keep_first=0):
        self.key, self.carrier, self.index = key, carrier, index
        self.says, self.kind, self.cutting, self.keep_first = says, kind, cutting or {}, keep_first

    @property
    def count(self):
        return self.carrier.count('[pause') + 1 + self.keep_first


def _sof_plan(text):
    found = _sof_match(text)
    names = pieces_for(found[1]) if found else None
    if not names:
        return None
    prefix = found[0]
    numbers = carriers(BASE_PREFIX)
    pieces = [Piece(('prefix', prefix), *carriers(prefix)[('prefix', 0)])]
    pieces += [Piece(('number', *name), *numbers[name]) for name in names]
    return pieces, [AFTER_PREFIX_S] + [INSIDE_NUMBER_S] * (len(pieces) - 2)


def _lap_plan(text):
    found = _lap_match(text)
    if not found:
        return None
    prefix, minutes, seconds, tenth, ending = found
    if seconds >= 10:
        head = f'{prefix} {minutes}분'.strip() if minutes else prefix
        rest = f'{seconds}초 {tenth}{ending}'
        source = f'{LAP_BASE} 1분 [pause short] {rest}'
    else:
        head = prefix
        rest = (f'{minutes}분 ' if minutes else '') + f'{seconds}초 {tenth}{ending}'
        source = f'{LAP_BASE} [pause short] {rest}'
    pieces, gaps = [], []
    if head:
        inside = head.count('. ')            # "랩타임. 7분": the voice's own pause there is not cut
        pieces.append(Piece(('lap', LAP_VERSION, 'head', head), f'{head} [pause short] 37초 2입니다.', 0,
                            says=head, kind='lap_head', cutting=LAP_CUT, keep_first=inside))
        gaps.append(AFTER_STOP_S if head.endswith('.') else 0.)
    pieces.append(Piece(('lap', LAP_VERSION, 'rest', rest), source, 1, says=rest, kind='lap_rest', cutting=LAP_CUT))
    return pieces, gaps


_PATTERNS = {}


def _patterns(kind):
    if kind not in _PATTERNS:
        import radio_phrases
        if kind == 'sof':
            _PATTERNS[kind] = [(before.strip(), '', re.compile(re.escape(before) + r'(\d+)' + re.escape(after)))
                               for key in SPLICED for before, _, after in (w.partition('{rating}') for w in radio_phrases.PHRASES[key][1])]
        else:
            _PATTERNS[kind] = [(before.strip(), after, re.compile(re.escape(before) + LAP_NUMBER + re.escape(after)))
                               for key in LAP_KEYS for before, _, after in (w.partition('{lap}') for w in radio_phrases.wordings(key)[1])]
    return _PATTERNS[kind]


def _sof_match(text):
    if 'SOF' not in text:                    # every other sentence: checked for each of 60,000 clips
        return None
    for prefix, _, pattern in _patterns('sof'):
        found = pattern.fullmatch(text)
        if found:
            return prefix, int(found[1])
    return None


def _lap_match(text):
    if '초 ' not in text:
        return None
    for prefix, ending, pattern in _patterns('lap'):
        found = pattern.fullmatch(text)
        if found:
            return prefix, int(found[1]) if found[1] else None, int(found[2]), int(found[3]), ending
    return None


def match(text):
    """(prefix, value) when text is a strength of field wording with a number, else None."""
    return _sof_match(text)


def plan(text):
    """([Piece], silences between them) when text is said from pieces, else None."""
    return _sof_plan(text) or _lap_plan(text)


def spliceable(text):
    """True when text is said from pieces; other values of the same wordings (above 10000) are whole clips."""
    return plan(text) is not None


DIGITS = str.maketrans('영일이삼사오육칠팔구', '0123456789')
# How far a take may be from the usual pitch and pace of its kind (log ratio). Ordinary pieces spread about
# ±0.25 with their first syllable; the one heard as too high and fast was +0.5. Tighter made nearly every
# piece be said five times (2026-10-19: ±0.12/±0.15 rejected 6 of 8).
PITCH_SPREAD, RATE_SPREAD = .3, .25
USUAL_FROM = 10                         # pieces of a kind kept before their pitch is a measure


def heard_as(heard, says):
    """Speech recognition heard the piece as written: the same digits in the same order and the same letters.

    It writes 4 as 사, 이번 as 2번 and "31초 5" as "31.5초", so the letters are compared as a set of letters.
    """
    def parts(text):
        text = re.sub(r'[\s.,!?-]', '', text).translate(DIGITS)
        return re.sub(r'\D', '', text), sorted(re.sub(r'\d', '', text))
    return parts(heard) == parts(says)


def syllables(text):
    return len(re.findall('[가-힣]', re.sub(r'\d+', lambda m: sino(int(m[0])), text))) or 1


class Pieces:
    """The cut pieces on disk (data/radio_audio/pieces/*.ogg), one per voice and piece, cut on first use.

    The voice is not the same from one request to the next (one take of a lap time came out a fifth higher and
    faster, one said "세션" as "리순"). A lap time piece is therefore only kept when speech recognition hears what
    it should say and its pitch and pace are near the usual ones of that kind of piece in that voice; after
    TAKES tries the nearest is kept. Strength of field pieces were chosen by ear and are cut as before.
    """

    def __init__(self, speech, directory):
        self.speech, self.directory = speech, Path(directory)
        self.lock, self.busy = threading.Lock(), {}
        self._usual = {}

    def path(self, role, key):
        from race_engineer import role_voice
        digest = hashlib.sha256(json.dumps([VERSION, role_voice(role), *key], ensure_ascii=False).encode()).hexdigest()
        return self.directory / f'{digest}.ogg'

    @staticmethod
    def stored(path):
        """The file a piece is kept in: Ogg Opus, or a WAV made before pieces were compressed."""
        return path if path.exists() else path.with_suffix('.wav') if path.with_suffix('.wav').exists() else None

    def on_disk(self, role, text):
        found = plan(text)
        return bool(found) and all(self.stored(self.path(role, piece.key)) for piece in found[0])

    def _usual_file(self, role):
        from race_engineer import role_voice
        # One file per voice: several voices are prepared side by side in separate processes.
        return self.directory / f'usual-{hashlib.sha256(role_voice(role).encode()).hexdigest()[:12]}.json'

    def usual(self, role, kind):
        """The usual (pitch at the start in Hz, seconds per syllable) of a kind of piece in this voice, once known."""
        path = self._usual_file(role)
        with self.lock:
            if path.name not in self._usual:
                try:
                    self._usual[path.name] = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    self._usual[path.name] = {}
            kept = self._usual[path.name].get(kind, [])
            if len(kept) < USUAL_FROM:
                return None
            return float(np.median([p for p, _ in kept])), float(np.median([r for _, r in kept]))

    def remember(self, role, kind, value):
        path = self._usual_file(role)
        with self.lock:
            kept = self._usual.setdefault(path.name, {}).setdefault(kind, [])
            kept.append(list(value))
            del kept[:-200]
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self._usual[path.name]), encoding='utf-8')
                temporary.replace(path)
            except OSError:
                pass

    def hear(self, samples):
        """What speech recognition hears in a piece, or None when it cannot be asked."""
        recognize = getattr(self.speech, 'recognize', None)
        if recognize is None:
            return None
        count = int(len(samples) * 16000 / RATE)
        low = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
        padded = np.concatenate([np.zeros(3200), low, np.zeros(3200)])
        try:
            return recognize(base64.b64encode(write_wav(padded, 16000)).decode('ascii'))
        except Exception:
            return None

    def make(self, role, piece):
        """Synthesize the source sentence until a take is fit to keep: the piece's samples, or None."""
        usual = self.usual(role, piece.kind) if piece.kind else None
        fallback = []
        for _ in range(TAKES):
            audio, _rate = read_wav(base64.b64decode(self.speech.synthesize(piece.carrier, role=role, encoding='wav')))
            parts = cut(audio, piece.count, keep_first=piece.keep_first, **piece.cutting)
            if not parts and piece.keep_first:
                # The voice did not stop at the full stop: the one pause there is the one asked for.
                parts = cut(audio, piece.count - piece.keep_first, **piece.cutting)
            if not parts:
                continue
            found = parts[piece.index]
            if not piece.kind:
                return found
            onset = onset_pitch(found)
            pace = len(found) / RATE / syllables(piece.says)
            near = (abs(np.log(onset / usual[0])), abs(np.log(pace / usual[1]))) if usual and onset else (0., 0.)
            heard = self.hear(found)
            right = heard is None or heard_as(heard, piece.says)
            if right and near[0] <= PITCH_SPREAD and near[1] <= RATE_SPREAD:
                if onset:
                    self.remember(role, piece.kind, (onset, pace))
                return found
            fallback.append((not right, sum(near), found))
        if not fallback:
            return None
        # None fit: the nearest of those heard right, else (the recognizer's habit, "방금 랩" as "방금 내") any.
        return min(fallback, key=lambda item: (item[0], item[1]))[2]

    def piece(self, role, piece):
        path = self.path(role, piece.key)
        with self.lock:
            event = self.busy.get(path.name)
            if event is None and not self.stored(path):
                event = self.busy[path.name] = threading.Event()
                mine = True
            else:
                mine = False
        if event is not None and not mine:
            event.wait(120)                      # the same piece is being made right now
        if not mine:
            return load(self.stored(path))
        try:
            found = self.make(role, piece)
            if found is None:
                raise ValueError('숫자 음성 조각을 만들지 못했습니다. 화면의 값을 확인하세요.')
            self.directory.mkdir(parents=True, exist_ok=True)
            save(path, found)
            return found
        finally:
            with self.lock:
                self.busy.pop(path.name).set()

    def sentence(self, role, text):
        """The sentence as one WAV data URL, or None when text is not said from pieces."""
        found = plan(text)
        if not found:
            return None
        pieces, gaps = found
        parts = [self.piece(role, piece) for piece in pieces]
        joined = join(parts, gaps) if len(parts) > 1 else parts[0]
        return 'data:audio/wav;base64,' + base64.b64encode(write_wav(joined)).decode('ascii')


def pitch(samples, rate=RATE, low=70, high=320, window=.04, hop=.01, threshold=.15):
    """Fundamental frequency (Hz) of each voiced frame, by YIN's cumulative mean normalized difference."""
    size, step, longest, shortest = int(rate * window), int(rate * hop), rate // low, rate // high
    found = []
    for start in range(0, len(samples) - size - longest, step):
        frame = samples[start:start + size + longest]
        if np.sqrt(np.mean(frame[:size] ** 2)) < .02:
            continue
        # Difference d(t) = sum (x[i] - x[i+t])^2 over the window, from FFT correlation and running energies.
        head = frame[:size]
        n = 1 << int(np.ceil(np.log2(len(frame) + size)))
        correlation = np.fft.irfft(np.fft.rfft(frame, n) * np.conj(np.fft.rfft(head, n)), n)[:longest]
        energy = np.cumsum(frame ** 2)
        shifted = energy[size - 1 + np.arange(longest)] - np.concatenate([[0], energy[:longest - 1]])
        difference = energy[size - 1] + shifted - 2 * correlation
        difference[0] = 0
        normalized = difference[1:] * np.arange(1, longest) / np.maximum(np.cumsum(difference[1:]), 1e-12)
        below = np.flatnonzero(normalized[shortest:] < threshold)
        if len(below):
            lag = shortest + below[0]
            while lag + 1 < len(normalized) and normalized[lag + 1] < normalized[lag]:
                lag += 1
            found.append(rate / (lag + 1))
    return np.array(found)


def onset_pitch(samples, rate=RATE, seconds=.35):
    """Median pitch of the first voiced part of a piece: how high the voice starts it."""
    values = pitch(samples[:int(rate * seconds) + int(rate * .1)], rate)
    return float(np.median(values)) if len(values) else None
