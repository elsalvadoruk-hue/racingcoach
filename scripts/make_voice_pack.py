"""Create voice-only ZIPs to extract directly beside the app's launcher.

python scripts/make_voice_pack.py --version 1.3.0 --from 1.0.0 1.0.1 1.0.2 1.1.0 1.2.0
Only fixed-cache MP3 files are read. No synthesis, database or network is used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIRS = ('data/radio_audio/', 'data/spotter_audio/')
PIECES_DIR = 'data/radio_audio/pieces/'
PIECE_TYPES = ('.ogg', '.wav')


def voice_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts and '\\' not in name
            and name.startswith(VOICE_DIRS) and (path.suffix.lower() == '.mp3'
                                                 # Number pieces joined at play time (number_splice): Ogg Opus,
                                                 # or WAV from before they were compressed.
                                                 or (path.suffix.lower() in PIECE_TYPES and name.startswith(PIECES_DIR))))


def sources(radio: Path, spotter: Path, skip=frozenset()) -> dict[str, Path]:
    """Every voice file to share; skip holds radio clip names the app no longer plays (replaced_clips)."""
    result = {}
    for prefix, folder in zip(VOICE_DIRS, (radio, spotter)):
        folder = folder.resolve()
        if not folder.is_dir():
            raise ValueError('Both fixed voice cache folders must exist')
        for file in sorted(folder.rglob('*')):
            if not file.is_file() or file.suffix.lower() not in ('.mp3', *PIECE_TYPES) or file.name.endswith('.part.ogg'):
                continue
            if prefix == VOICE_DIRS[0] and file.parent == folder and file.name in skip:
                continue
            if file.is_symlink() or not file.resolve().is_relative_to(folder):
                raise ValueError('A voice file points outside its cache folder')
            name = prefix + file.relative_to(folder).as_posix()
            if file.suffix.lower() in PIECE_TYPES and not name.startswith(PIECES_DIR):
                continue
            if not voice_path(name):
                raise ValueError('Invalid voice path')
            result[name] = file
    if not result:
        raise ValueError('No fixed voice MP3 files were found')
    return result


def replaced_clips(radio: Path) -> frozenset:
    """Names of whole-sentence clips for sentences now said from cut pieces (lap times, strength of field),
    in every spotter voice: they stay on disk but are not shared any more."""
    sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
    import os
    import number_splice
    import radio_phrases
    from radio_audio import RadioAudioCache
    from spotter import SPOTTER_VOICES
    cache, names = RadioAudioCache(None, radio), set()
    sentences = [(role, text) for role, text in radio_phrases.catalog() if number_splice.spliceable(text)]
    before = os.environ.get('IRACING_TTS_SPOTTER_VOICE')
    try:
        for voice in SPOTTER_VOICES:
            os.environ['IRACING_TTS_SPOTTER_VOICE'] = f'ko-KR-Chirp3-HD-{voice}'
            names.update(cache.path(role, text).name for role, text in sentences)
    finally:
        if before is None:
            os.environ.pop('IRACING_TTS_SPOTTER_VOICE', None)
        else:
            os.environ['IRACING_TTS_SPOTTER_VOICE'] = before
    return frozenset(names)


def previous(releases: Path, version: str) -> dict[str, str]:
    voice = releases / f'voice-{version}.json'
    file = voice if voice.is_file() else releases / f'{version}.json'
    value = json.loads(file.read_text(encoding='utf-8-sig'))
    if value.get('version') != version:
        raise ValueError('Baseline version does not match its filename')
    result = {n: h for n, h in value['files'].items() if voice_path(n)}
    if not result:
        raise ValueError('Baseline contains no voice files')
    return result


def instructions(version: str, old: str | None, count: int) -> str:
    kind = '전체 음성' if old is None else f'음성 업데이트 {old} → {version}'
    # A full pack replaces the voice folders: since 1.4.0 the lap time clips are cut pieces, and the old
    # whole-sentence clips (about 1.4 GB) would only stay behind unused.
    replace = '' if old else (
        '   이미 쓰던 사람은 먼저 데이터 폴더의 radio_audio, spotter_audio 두 폴더만 지웁니다\n'
        '   (보통 프로그램 폴더\\data\\radio_audio, 프로그램 폴더\\data\\spotter_audio).\n'
        '   data 폴더 자체와 그 안의 다른 파일(기록, 설정, Google 키, Garage61 토큰)은 지우지 않습니다.\n')
    change = (f'음성 {count:,}개를 포함합니다.' if count else
              '이 버전 사이에는 추가·변경된 음성이 없습니다. 기존 음성이 정상이라면 이 ZIP은 받을 필요가 없습니다.')
    return f'''iRacing 주행 코치 {version} · {kind}

{change}
음성만 들어 있습니다. 프로그램 버전은 바뀌지 않습니다.

1. 프로그램과 실행.bat으로 연 창을 종료합니다.
{replace}2. 이 ZIP을 실행.bat이 있는 프로그램 폴더에 압축 해제합니다.
3. ZIP 안의 data 폴더를 기존 data 폴더와 합치고 같은 파일은 덮어씁니다.
4. 실행.bat으로 다시 실행하고 음성 코치에서 소리를 확인합니다.

올바른 위치: 프로그램 폴더/data/radio_audio/*.mp3
              프로그램 폴더/data/radio_audio/pieces/*.ogg (랩타임·SOF 숫자 음성 조각)
              프로그램 폴더/data/spotter_audio/*.mp3
프로그램 폴더/음성팩이름/data/ 또는 프로그램 폴더/data/data/처럼 중첩하지 마세요.

처음 설치하거나 기존 음성이 빠졌다면 전체 음성팩을 사용하세요.
업데이트팩은 표시된 출발 버전의 전체 음성이 이미 있는 사람용입니다.
현재 제공하는 업데이트팩은 해당 출발 버전에서 {version}으로 바로 적용합니다.
기록·설정·Google 키·Garage61 토큰은 들어 있지 않으며 기존 파일을 삭제하지 않습니다.

설정 화면의 데이터 폴더를 별도로 지정한 경우에는 ZIP의 data 안에 있는
radio_audio, spotter_audio 폴더를 그 실제 데이터 폴더 안에 합쳐 주세요.
음성 경로 환경변수를 별도로 지정했다면 각 폴더를 지정된 경로에 합칩니다.
'''


def write_pack(target: Path, files: dict[str, Path], version: str, old: str | None = None,
               expected: dict[str, str] | None = None) -> dict:
    if target.exists():
        raise FileExistsError('Refusing to overwrite an existing voice pack')
    partial = target.with_suffix('.zip.partial')
    if partial.exists():
        raise FileExistsError('A previous partial pack exists; choose another output folder')
    hashes, total = {}, 0
    def read_clip(item):
        name, file = item
        if not voice_path(name):
            raise ValueError('Only fixed voice paths may be packaged')
        before = file.stat()
        content = file.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        after = file.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('A voice file changed while being packaged')
        if expected is not None and expected.get(name) != digest:
            raise ValueError('A voice file changed after the full pack was made')
        return name, content, digest

    try:
        with zipfile.ZipFile(partial, 'w', allowZip64=True) as archive:
            for directory in ('data/', *VOICE_DIRS):
                archive.writestr(directory, b'')
            ordered = sorted(files.items())
            # Bound pending reads so a large cache never accumulates all MP3 bytes in RAM.
            with ThreadPoolExecutor(max_workers=8) as readers:
                completed = 0
                for start in range(0, len(ordered), 128):
                    for name, content, digest in readers.map(read_clip, ordered[start:start + 128]):
                        archive.writestr(name, content, compress_type=zipfile.ZIP_STORED)
                        hashes[name] = digest
                        total += len(content)
                        completed += 1
                        if completed % 10000 == 0:
                            print(f'{target.name}: {completed:,}/{len(files):,} voices', flush=True)
            metadata = {'format': 'iracing-coach-voice-pack-v1', 'version': version,
                        'from_version': old, 'kind': 'full' if old is None else 'patch',
                        'voice_files': len(hashes), 'voice_bytes': total, 'files': hashes}
            archive.writestr('_voice-pack.json', json.dumps(metadata, ensure_ascii=False),
                             compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr('음성 설치 안내.txt', '\ufeff' + instructions(version, old, len(hashes)),
                             compress_type=zipfile.ZIP_DEFLATED)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    with target.open('rb') as stream:
        sha = hashlib.sha256()
        for block in iter(lambda: stream.read(1 << 20), b''):
            sha.update(block)
        checksum = sha.hexdigest()
    return {'filename': target.name, 'sha256': checksum, 'bytes': target.stat().st_size,
            'voice_files': len(hashes), 'voice_bytes': total, 'from_version': old,
            'version': version, 'files': hashes}


def create(radio: Path, spotter: Path, version: str, baselines: list[str],
           releases: Path, out: Path, skip=frozenset()) -> list[dict]:
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or any(
            not re.fullmatch(r'\d+\.\d+\.\d+', v) or v == version for v in baselines):
        raise ValueError('Use distinct source and target semantic versions')
    baselines = list(dict.fromkeys(baselines))
    old = {v: previous(releases, v) for v in baselines}
    clips = sources(radio, spotter, skip)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError('Use a new empty output folder')
    manifest = releases / f'voice-{version}.json'
    full = write_pack(out / f'iracing-coach-voice-full-{version}.zip', clips, version)
    record = {'version': version, 'format': 'voice-only-v1', 'files': full['files']}
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')) != record:
        raise ValueError('The voice set of a recorded version must not be overwritten')
    packs = [full]
    for baseline, prior in old.items():
        chosen = {n: p for n, p in clips.items() if prior.get(n) != full['files'][n]}
        packs.append(write_pack(out / f'iracing-coach-voice-patch-{baseline}-to-{version}.zip',
                                chosen, version, baseline, full['files']))
    if not manifest.exists():
        releases.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(record), encoding='utf-8')
    summary = [{k: v for k, v in pack.items() if k != 'files'} for pack in packs]
    (out / 'voice-packs.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'SHA256SUMS.txt').write_text(''.join(f"{p['sha256']}  {p['filename']}\n" for p in packs), encoding='utf-8')
    (out / '음성 설치 및 업데이트 안내.txt').write_text('\ufeff' + instructions(version, None, len(clips)), encoding='utf-8')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--from', dest='baselines', nargs='*', default=[])
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
    import app_settings
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    out = args.out or ROOT / 'share' / 'voice-packs' / args.version
    app_settings.apply_environment()
    radio = app_settings.data_dir() / 'radio_audio'
    result = create(radio, app_settings.spotter_audio_dir(), args.version, args.baselines, ROOT / 'releases', out,
                    replaced_clips(radio))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
