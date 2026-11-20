"""Prepare a private race-radio draft or encode review recordings for the voice cache."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
import hashlib
import io
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
from race_call_audio import CALLS, EXTRA_SHORTS, RECORDINGS
from number_splice import OPUS_LEVEL

PROVENANCE = ROOT / 'logs/race-coach-audio'

TITLES = dict(race_pack='앞차 배틀', race_coast='레이스 연료와 코스팅', fuel_short='무급유 완주 목표', race_limits='트랙 리밋',
              race_rejoin='피트 복귀', race_pit_target='예정 피트까지 연료', race_energy_plan='예정 피트까지 가상 에너지', race_tire_push='피트 후 타이어',
              race_catch_zone='빠른 클래스 접근')


def encode_opus(pcm, hz):
    import soundfile as sf
    encoded = io.BytesIO()
    sf.write(encoded, pcm, hz, format='OGG', subtype='OPUS', compression_level=OPUS_LEVEL)
    return encoded.getvalue()


def export_review(source, out):
    """Re-encode the reviewed performance offline and keep its source byte-for-byte."""
    import numpy as np
    import soundfile as sf
    from custom_voice_pack_format import opus_info

    if out.exists():
        raise FileExistsError(out)
    files = {}
    for name in sorted(RECORDINGS):
        source_clip = source / (Path(name).stem + '.mp3')
        info = json.loads((PROVENANCE / (source_clip.stem + '.json')).read_bytes())
        raw = source_clip.read_bytes()
        if hashlib.sha256(raw).hexdigest() != info['sha256']:
            raise ValueError(f'Review recording differs: {source_clip.name}')
        pcm, hz = sf.read(io.BytesIO(raw))
        if hz != 24000 or pcm.ndim != 1 or not np.isfinite(pcm).all() or np.max(abs(pcm)) < .02:
            raise ValueError(f'Invalid review recording: {source_clip.name}')
        data = encode_opus(pcm, hz)
        opus_info(data)
        files[name] = data
    out.mkdir(parents=True)
    for name, data in files.items():
        (out / name).write_bytes(data)


def make_drafts():
    import numpy as np
    import soundfile as sf
    import app_settings
    from google.cloud import texttospeech as tts
    from radio_audio import spoken_form
    from race_engineer import role_voice
    from race_call_phrasing import ClauseRecorder, phrasing
    os.environ.setdefault('GOOGLE_APPLICATION_CREDENTIALS', str(app_settings.key_path()))
    client = tts.TextToSpeechClient()
    folder = PROVENANCE / 'drafts'
    folder.mkdir(parents=True, exist_ok=True)
    provenance = folder / 'provenance'
    provenance.mkdir(parents=True, exist_ok=True)

    def synthesize_clause(voice, words, rate):
        return client.synthesize_speech(request={
            'input': tts.SynthesisInput(text=words),
            'voice': tts.VoiceSelectionParams(language_code='ko-KR', name=voice),
            'audio_config': tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16,
                                            sample_rate_hertz=24000, speaking_rate=rate),
        }, retry=None, timeout=35).audio_content

    recorder = ClauseRecorder(provenance / 'phrasing-originals', synthesize_clause)

    def make(job):
        key, role, style, phrase = job
        name = f'{key}-{style}'
        dest = folder / (name + '.ogg')
        meta = provenance / (name + '.json')
        voice = role_voice(role)
        trim = style == 'short'
        plan = phrasing(key, style, phrase)
        signature = plan.signature() if plan else None
        if meta.is_file() and dest.is_file():
            old = json.loads(meta.read_bytes())
            data = dest.read_bytes()
            if (old['phrase'], old['voice'], old['sha256'], old.get('phrasing')) == (
                    phrase, voice, hashlib.sha256(data).hexdigest(), signature):
                if plan or not trim or old.get('edge_trim') == 'rms-10ms':
                    return
        processing = {}
        if plan:
            raw, processing = recorder.render(voice, plan)
        else:
            response = client.synthesize_speech(request={
                'input': tts.SynthesisInput(text=spoken_form(phrase)),
                'voice': tts.VoiceSelectionParams(language_code='ko-KR', name=voice),
                'audio_config': tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=24000),
            }, retry=None, timeout=35)
            raw = response.audio_content
        pcm, hz = sf.read(io.BytesIO(raw))
        if hz != 24000 or pcm.ndim != 1 or len(pcm) < hz * .25 or not np.isfinite(pcm).all() or np.max(abs(pcm)) < .02:
            raise ValueError(f'Invalid recording {name}')
        if trim and not plan:
            masters = provenance / 'provider-originals'
            masters.mkdir(exist_ok=True)
            original = masters / (hashlib.sha256(raw).hexdigest()+'.wav')
            if not original.exists():
                original.write_bytes(raw)
            frame = hz // 100
            frames = np.pad(pcm, (0, -len(pcm) % frame)).reshape(-1, frame)
            audible = np.flatnonzero(np.sqrt(np.mean(frames**2, axis=1)) > .006)
            if not len(audible):
                raise ValueError(f'No voiced frames in {name}')
            # Keep room for soft initial consonants and the end of the last syllable.
            first = max(0, (int(audible[0]) - 12) * frame)
            last = min(len(pcm), (int(audible[-1]) + 13) * frame)
            pcm = pcm[first:last]
            processing = dict(edge_trim='rms-10ms', source_sha256=original.stem)
        raw = encode_opus(pcm, hz)
        dest.with_suffix('.tmp').write_bytes(raw)
        dest.with_suffix('.tmp').replace(dest)
        meta.write_text(json.dumps(dict(phrase=phrase, voice=voice, duration=len(pcm)/hz, **processing,
                                        sha256=hashlib.sha256(raw).hexdigest()), ensure_ascii=False), encoding='utf8')

    jobs = [(key, role, style, phrase) for key, (_, role, normal, short) in CALLS.items()
            for style, phrase in [('default', normal), ('short', short)]]
    jobs.extend((stem, 'race', 'short', phrase) for stem, (_, phrase) in EXTRA_SHORTS.items())
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(make, jobs))
    html = ['<!doctype html><html lang="ko"><meta charset="utf-8"><link rel="icon" href="data:,"><meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>레이스 무전 청취</title><style>body{max-width:900px;margin:auto;padding:2rem 1rem;background:#101820;color:#f2f5f7;font:1rem/1.5 system-ui}h2{margin-top:2.5rem}article{border-top:1px solid #475666;padding:1rem 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:1rem}p{margin:.35rem 0}audio{width:100%}strong{color:#9ed2e2}</style><h1>레이스 무전 청취</h1>']
    for category, title in TITLES.items():
        html.append(f'<h2>{title}</h2>')
        for key, (group, _, normal, short) in CALLS.items():
            if group != category:
                continue
            html.append(f'<article id="{key}">')
            for style, label, phrase in [('default', '기본', normal), ('short', '짧은', short)]:
                html.append('<section>'+player(key, style, label, phrase, provenance)+'</section>')
            html.append('</article>')
            locations = [(stem, phrase) for stem, (base, phrase) in EXTRA_SHORTS.items() if base == key]
            if locations:
                html.append('<details><summary>예측 위치별 짧은 무전 듣기</summary>')
                for stem, phrase in locations:
                    html.append(f'<div id="{stem}-short">'+player(stem, 'short', '짧은', phrase, provenance)+'</div>')
                html.append('</details>')
    html.append('''<script>
function showLocation(){
  const row=document.getElementById(location.hash.slice(1));
  const group=row?.closest('details');
  if(group){group.open=true;row.scrollIntoView()}
}
showLocation();
addEventListener('hashchange',showLocation);
document.addEventListener('play',e=>{for(const a of document.querySelectorAll('audio'))if(a!==e.target)a.pause()},true);
</script></html>''')
    sheet = folder / 'index.html'
    content = '\n'.join(html)
    if not sheet.is_file() or sheet.read_text(encoding='utf8') != content:
        sheet.write_text(content, encoding='utf8')
    print(len(jobs), 'recordings ready')


def player(key, style, label, phrase, provenance):
    stem = f'{key}-{style}'
    info = json.loads((provenance / (stem+'.json')).read_bytes())
    src = f'{stem}.ogg'
    return (f'<strong>{label} · {info["duration"]:.2f}초</strong><p>{escape(phrase)}</p>'
            f'<audio controls preload="none" aria-label="{label} {escape(phrase)}" src="{src}"></audio>')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--from-review', type=Path, help='private review folder containing the original MP3 files')
    mode.add_argument('--synthesize-drafts', action='store_true', help='prepare new private drafts using Google TTS')
    parser.add_argument('--out', type=Path, help='new directory for the separate voice cache files')
    args = parser.parse_args()
    if args.from_review:
        if args.out is None:
            parser.error('--from-review requires --out')
        out = args.out.resolve()
        if any(out.is_relative_to(ROOT / folder) for folder in ('public', 'build')):
            parser.error('choose a voice cache or staging folder outside the app assets')
        export_review(args.from_review, out)
    else:
        make_drafts()
