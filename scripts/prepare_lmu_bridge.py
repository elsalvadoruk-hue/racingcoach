"""Build the installed LMU SDK bridge for distribution, without attaching to LMU.

    python scripts/prepare_lmu_bridge.py [--sdk-dir PATH]

Only the x64 DLL and its manifest are exported. SDK headers stay in the game.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
import lmu_shared_memory as sm


def prepare(sdk_dir=None, output=None):
    output = Path(output) if output is not None else sm.BUNDLED_DIR
    name = sm.build_artifact_dir(sdk_dir).name
    target = output / name
    if target.exists():
        sm.validate_bundled_bridges(output)
        sm.SharedMemorySource(sdk_dir=sdk_dir, build_dir=target).probe()
        return target
    # Loaded DLLs cannot be removed on Windows. Keep intermediate files in logs,
    # outside the tracked/exported artifact, also preserving the build log.
    staging = sm.BUILD_DIR / ('distribution-' + uuid.uuid4().hex)
    sm.build_native_bridge(sdk_dir=sdk_dir, build_dir=staging)
    pending = output / ('.preparing-' + uuid.uuid4().hex)
    pending.mkdir(parents=True)
    for name in ('lmu_native_bridge.dll', 'manifest.json'):
        shutil.copyfile(staging / name, pending / name)
    pending.rename(target)
    sm.validate_bundled_bridges(output)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-dir', type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        target = prepare(args.sdk_dir)
    except (OSError, ValueError, sm.LmuLiveUnavailable) as exc:
        print(f'LMU 배포 브리지를 만들지 못했습니다: {exc}')
        return 1
    manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
    print(f'LMU 배포 브리지 준비됨: {target.relative_to(ROOT)}')
    print(f"SDK SHA-256: {manifest['header_fingerprint']}")
    print('DLL과 manifest.json을 함께 커밋한 뒤 앱 릴리스 ZIP을 만드세요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
