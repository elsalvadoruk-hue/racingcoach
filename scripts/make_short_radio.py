"""Prepare standard short radio chunks from the seven reviewed compact packs. No upload or TTS.

python scripts/make_short_radio.py --packs <compact-packages.json> --previous <voice-chunks-2.4.3.json>
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from make_release_assets import make_voice, ROOT
from builtin_radio import PACKS, PREFIX, VERSION
from custom_voice_pack_format import validate_manifest, radio_style


def prepare(packs, previous, out, required):
    files = {path: entry[0] for path, entry in previous['files'].items()}
    sources, manifests = {}, {}
    with ExitStack() as stack:
        for key, (role, voice) in PACKS.items():
            item = packs[f'{role}-{voice.lower()}']
            source = Path(item['file'])
            with source.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    raise ValueError('Reviewed compact pack hash mismatch')
            archive = stack.enter_context(zipfile.ZipFile(source))
            manifest = validate_manifest(json.loads(archive.read('manifest.json')))
            if manifest['targets'] != [role] or radio_style(manifest) != {'length': 'short', 'voice': voice}:
                raise ValueError('Compact pack speaker or role mismatch')
            manifest.update(packId=key.partition('@')[0], version=VERSION, name=f'{voice} · 짧은 무전')
            validate_manifest(manifest)
            prefix = PREFIX + key + '/'
            data = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf8')
            manifests[prefix + 'manifest.json'] = data
            for row in manifest['entries']:
                path = prefix + row['audio']
                files[path] = row['sha256']
                sources[path] = (archive, row['audio'])
            files[prefix + 'manifest.json'] = hashlib.sha256(data).hexdigest()

        def read(path):
            if path in manifests:
                return manifests[path]
            archive, member = sources[path]
            return archive.read(member)

        summary = make_voice(VERSION, files, read, out, previous)
    required.parent.mkdir(parents=True, exist_ok=True)
    required.write_text(json.dumps({'version': VERSION, 'files': files}, separators=(',', ':')), encoding='utf8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packs', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'share' / 'voice-release' / VERSION)
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('Output folder must be empty')
    packs = json.loads(args.packs.read_text(encoding='utf-8-sig'))
    previous = json.loads(args.previous.read_text(encoding='utf8'))
    print(json.dumps(prepare(packs, previous, args.out, ROOT / 'releases' / f'voice-{VERSION}.json')))


if __name__ == '__main__':
    main()
