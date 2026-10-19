"""Make a small update to unzip over a shared copy that people already use.

    1. raise APP_VERSION in iRacing_Backend/backend_api.py (and "version" in package.json), build the screen
    2. python scripts/make_share_copy.py --out share/iracing-coach-1.0.1     (the full new copy, checked for personal data)
    3. python scripts/make_patch.py --from 1.0.0 --new share/iracing-coach-1.0.1

The patch holds the program (always all of it: a few megabytes) and, of the large parts
(track maps, voice clips), only what the old version did not have or has differently.
It never holds anything from data/ except new voice clips, so unzipping it over the old
folder leaves the receiver's records, settings, Google key, Garage61 token, confirmed
corner numbers and measured pit facts as they are.

What each version contained is kept in releases/<version>.json, written by make_share_copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "releases"
# Compared with the old version file by file; everything else is small and always goes in.
LARGE = ("data/radio_audio/", "data/spotter_audio/", "build/track-maps/", "public/track-maps/")
# The receiver's own: shipped once with the first copy, changed by their use ever since.
NEVER = ("data/",)
STORED = {".mp3", ".png", ".jpg", ".jpeg", ".webp", ".zip", ".woff", ".woff2"}

NOTE = """iRacing 주행 코치 업데이트 {old} → {new}

■ 먼저 확인: 순서대로 적용합니다

이 파일은 {old} 버전에만 맞습니다. 적용하기 전에 "설정" 탭 맨 위의 버전이 {old}인지 확인하세요.

- 업데이트는 버전 순서대로 하나씩 적용합니다. 예: 1.0.0 → 1.0.1 → 1.0.2 → 1.1.0
- 건너뛰지 마세요. 업데이트 파일에는 바로 앞 버전 뒤에 새로 생긴 음성만 들어 있습니다.
  건너뛰면 그 사이에 추가된 음성이 빠져서 일부 안내가 소리 없이 지나갑니다.
- 이미 건너뛰었다면: 빠뜨린 버전의 파일부터 순서대로 다시 풀고, 마지막에 가장 새 버전의 파일을 한 번 더 풉니다.
- 처음 설치하는 사람은 1.0.0 설치본을 받은 뒤 업데이트 파일을 버전 순서대로 모두 풉니다.

■ 적용 방법

1. 주행 코치를 끕니다. (실행.bat으로 뜬 검은 창을 닫습니다)
2. 이 압축 파일을 주행 코치 폴더(실행.bat이 있는 폴더)에 풉니다.
   "같은 이름의 파일이 있습니다"라고 물으면 모두 덮어쓰기를 고릅니다.
3. 실행.bat으로 다시 켭니다. "설정" 탭 맨 위의 버전이 {new}(으)로 바뀌었는지 확인하세요.
{install}
■ 그대로 남는 것

이 업데이트는 프로그램만 바꿉니다. data 폴더의 내 것은 건드리지 않습니다.
- 분석 기록, 음성 메모, 주행 파일 폴더 설정
- Google 키, Garage61 토큰
- 확정한 코너 번호, 쌓인 피트 기록
- 스포터·피트월 설정, 출력장치 선택 (브라우저에 저장된 것)

새로 추가된 음성 {clips:,}개는 data 폴더에 더해집니다. 기존 음성은 그대로입니다.

■ 잘못됐을 때

덮어쓰기 전에 폴더를 통째로 복사해 두면 그 복사본으로 되돌릴 수 있습니다.
{notes}"""


def release_notes(folder: Path, version: str) -> str:
    """The block of this version from the copy's patch notes, to go under the instructions."""
    file = folder / "패치 노트.txt"
    if not file.is_file():
        return ""
    found = re.search(rf"^■ {re.escape(version)}\b[^\n]*\n(.*?)(?=^■ |\Z)", file.read_text(encoding="utf-8-sig"), re.MULTILINE | re.DOTALL)
    return f"\n■ {version}에서 달라진 것\n\n{found[1].strip()}\n" if found else ""


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def listing(folder: Path) -> dict[str, str]:
    """Every file of a shared copy with its hash, by its path with forward slashes."""
    return {path.relative_to(folder).as_posix(): digest(path)
            for path in sorted(folder.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def version_of(folder: Path) -> str:
    found = re.search(r'^APP_VERSION = "([^"]+)"', (folder / "iRacing_Backend" / "backend_api.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not found:
        raise ValueError("버전을 찾을 수 없습니다: " + str(folder))
    return found[1]


def record(folder: Path, overwrite: bool = False) -> Path | None:
    """Keep what this version contains, for patches made later. The first record of a version stays."""
    target = RELEASES / f"{version_of(folder)}.json"
    if target.exists() and not overwrite:
        return None
    RELEASES.mkdir(exist_ok=True)
    target.write_text(json.dumps({"version": version_of(folder), "files": listing(folder)}, ensure_ascii=False), encoding="utf-8")
    return target


def choose(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Paths of the new copy that belong in the patch."""
    chosen = []
    for path, value in new.items():
        large = path.startswith(LARGE)
        if path.startswith(NEVER) and not large:
            continue                                  # the receiver's own data: never replaced
        if large and old.get(path) == value:
            continue                                  # they have it already
        chosen.append(path)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="old", required=True, help="받는 사람들이 쓰고 있는 버전 (예: 1.0.0)")
    parser.add_argument("--new", type=Path, required=True, help="make_share_copy로 만든 새 버전 폴더")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    baseline = RELEASES / f"{args.old}.json"
    if not baseline.is_file():
        print(f"{args.old} 버전의 기록이 없습니다: {baseline}")
        return 1
    folder = args.new.resolve()
    new_version = version_of(folder)
    if new_version == args.old:
        print(f"새 폴더의 버전이 {args.old} 그대로입니다. APP_VERSION을 올리고 공유본을 다시 만드세요.")
        return 1
    old = json.loads(baseline.read_text(encoding="utf-8"))["files"]
    new = listing(folder)
    chosen = choose(old, new)
    clips = sum(1 for path in chosen if path.startswith(LARGE[:2]))
    requirements = any(path.startswith("iRacing_Backend/requirements") and old.get(path) != new[path] for path in chosen)
    install = "\n   이번 업데이트는 필요한 패키지가 바뀌었습니다. 켜기 전에 설치.bat을 한 번 더 실행하세요.\n" if requirements else ""
    out = (args.out or ROOT / "share" / f"iracing-coach-patch-{args.old}-to-{new_version}.zip").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    note = NOTE.format(old=args.old, new=new_version, clips=clips, install=install, notes=release_notes(folder, new_version))
    # The same text beside the zip, to read or post without opening it.
    out.with_name(f"패치 방법 {args.old}-to-{new_version}.txt").write_text(note, encoding="utf-8-sig")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("업데이트 안내.txt", "\ufeff" + note)
        for path in chosen:
            source = folder / path
            archive.write(source, path, zipfile.ZIP_STORED if source.suffix.lower() in STORED else zipfile.ZIP_DEFLATED)
    record(folder)
    gone = sorted(path for path in old if path not in new and not path.startswith(NEVER))
    print(f"패치: {out}\n크기 {out.stat().st_size / 1e6:.1f} MB · 파일 {len(chosen):,}개 (새 음성 {clips:,}개)")
    print(f"data 폴더에서 들어간 것: 새 음성뿐 · 받는 사람의 기록·설정·키·코너 번호·피트 기록은 들어 있지 않습니다.")
    if requirements:
        print("필요한 패키지가 바뀌었습니다. 안내문에 설치.bat을 다시 실행하라고 적었습니다.")
    if gone:
        print(f"새 버전에서 없어진 파일 {len(gone)}개는 받는 쪽 폴더에 남습니다(동작에는 영향 없음). 예: {gone[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
