"""Record the requested words together and put the pause at the cue boundary."""
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from threading import Lock

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt


FOLLOW_UPS = {
    'catch_another-after_1', 'catch_another-corner_0',
    'catch_another-corner_2', 'catch_another-straight_0',
    'catch_another', 'catch_another-corner_3',
    'catch_another-straight_1', 'catch_another-after_2',
}
SPEECH_BAND = butter(2, 65, fs=24000, btype='highpass', output='sos')


@dataclass(frozen=True)
class Phrasing:
    clauses: tuple
    gap_ms: int
    rates: tuple
    inside_gap_ms: int

    def signature(self):
        return {'version': 2, **asdict(self), 'clauses': list(self.clauses), 'rates': list(self.rates)}


def phrasing(key, style, phrase):
    from radio_audio import spoken_form

    if style == 'short' and key.startswith('catch_train'):
        prefix, location = phrase.split(', ', 1)
        # Read the class and the count as one cue, with a pause before the location.
        return Phrasing((prefix.replace(' ', '') + '.', spoken_form(location)),
                        235, (1.08, 1.08), 125)
    if style == 'short' and key == 'stay_for_gap':
        prefix, ending = phrase.split(' 나올 때 ', 1)
        return Phrasing((spoken_form(prefix) + '.', '나올 때 ' + ending),
                       280, (1.08, 1.08), 125)

    if style == 'default':
        if key in {'coast_half', 'coast_second'}:
            separator, gap, rate = ', ', 270 if key == 'coast_second' else 225, 1.05
        elif key == 'cut_margin':
            separator, gap, rate = '. ', 210, 1.04
        else:
            return None
    elif key in FOLLOW_UPS:
        separator, gap, rate = '. ', 145, 1.20
    elif key.startswith(('catch_straight', 'catch_corner')) or key in {
        'coast_here', 'coast_half', 'coast_second', 'cut_penalty', 'pack_slow',
    }:
        separator, gap, rate = ', ', 235, 1.08
    elif key == 'cut_margin':
        separator, gap, rate = '. ', 190, 1.08
    elif key in {'cut_repeat', 'stay_for_gap', 'tire_press_ready'}:
        separator, gap, rate = None, 0, 1.08
    else:
        return None

    parts = phrase.split(separator, 1) if separator else [phrase]
    if separator and len(parts) != 2:
        raise ValueError(f'Missing phrasing boundary for {key}: {phrase}')
    clauses = []
    for part in parts:
        words = spoken_form(part).rstrip('.')
        for original, joined in [('반 초', '반초'), ('일 초', '일초'), ('두 대', '두대'), ('한 대', '한대')]:
            words = words.replace(original, joined)
        clauses.append(words + '.')
    rates = [rate] * len(clauses)
    if key in FOLLOW_UPS and key != 'catch_another-straight_0':
        # These complete second clauses have a falling final syllable at this delivery rate.
        rates[-1] = 1.08
    return Phrasing(tuple(clauses), gap, tuple(rates), 185 if style == 'default' else 125)


class ClauseRecorder:
    def __init__(self, directory, synthesize):
        self.directory = directory
        self.synthesize = synthesize
        self.locks = {}
        self.guard = Lock()

    def recorded(self, voice, words, rate):
        identity = json.dumps([voice, words, rate, 'LINEAR16', 24000]).encode()
        key = hashlib.sha256(identity).hexdigest()
        with self.guard:
            lock = self.locks.setdefault(key, Lock())
        path = self.directory / (key + '.wav')
        with lock:
            if not path.is_file():
                raw = self.synthesize(voice, words, rate)
                decode(raw)
                self.directory.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.tmp')
                temp.write_bytes(raw)
                temp.replace(path)
            raw = path.read_bytes()
        return decode(raw), hashlib.sha256(raw).hexdigest()

    def render(self, voice, plan):
        chunks, sources, gaps = [], [], []
        end = previous_tail = 0
        for words, rate in zip(plan.clauses, plan.rates):
            pcm, digest = self.recorded(voice, words, rate)
            cropped, lead, tail = crop_clause(pcm, plan.inside_gap_ms)
            if chunks:
                silence = plan.gap_ms * 24 - previous_tail - lead
                if silence < 0:
                    raise ValueError(f'Insufficient room for {plan.gap_ms} ms pause')
                chunks.append(np.zeros(silence))
                gaps.append({'last_active_end': end - previous_tail, 'next_active_start': end + silence + lead})
                end += silence
            chunks.append(cropped)
            end += len(cropped)
            previous_tail = tail
            sources.append(digest)
        out = io.BytesIO()
        sf.write(out, np.concatenate(chunks), 24000, format='WAV', subtype='PCM_16')
        return out.getvalue(), dict(phrasing=plan.signature(), source_clauses_sha256=sources, boundaries_24khz=gaps)


def decode(raw):
    pcm, hz = sf.read(io.BytesIO(raw))
    if hz != 24000 or pcm.ndim != 1 or len(pcm) < 2400 or not np.isfinite(pcm).all() or np.max(abs(pcm)) < .02:
        raise ValueError('Invalid 24 kHz mono speech recording')
    return pcm


def crop_clause(pcm, inside_gap_ms):
    window = 120  # 5 ms at 24 kHz
    # The provider can place low rumble well before a word. Use the speech band
    # for timing only, and retain the original samples including soft consonants.
    detected = sosfilt(SPEECH_BAND, pcm)
    frames = np.pad(detected, (0, -len(pcm) % window)).reshape(-1, window)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    above = (rms > max(.008, np.quantile(rms, .9) * .055)).astype(int)
    active = np.flatnonzero(np.convolve(above, np.ones(3), 'same') >= 2)
    if not len(active):
        raise ValueError('No audible speech in clause')
    start, stop = int(active[0]) * window, min(len(pcm), int(active[-1] + 1) * window)
    begin = max(0, start - 960)  # keep soft onset and trailing consonants
    end = min(len(pcm), stop + 1680)
    clip = pcm[begin:end].copy()
    # Work backwards so removing a long quiet interval cannot shift earlier indices.
    for i in np.flatnonzero((np.diff(active) - 1) * 5 > inside_gap_ms + 90)[::-1]:
        left = (active[i] + 1) * window - begin + inside_gap_ms * 12
        right = active[i+1] * window - begin - inside_gap_ms * 12
        blend = clip[left:left+window] * np.linspace(1, 0, window)
        blend += clip[right:right+window] * np.linspace(0, 1, window)
        clip = np.concatenate([clip[:left], blend, clip[right+window:]])
    clip[:48] *= np.linspace(0, 1, 48)
    clip[-48:] *= np.linspace(1, 0, 48)
    return clip, start - begin, end - stop
