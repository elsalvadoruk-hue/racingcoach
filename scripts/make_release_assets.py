"""Make the files the in-app updater downloads from GitHub releases (iRacing_Backend/app_update.py).

    python scripts/make_release_assets.py app                     # racingcoach-app-<version>.zip for the vX.Y.Z release
    python scripts/make_release_assets.py voice --version 1.4.0   # voice pieces and their list for the "voice" release

app: the committed program (git ls-files) and the built screen, so an update needs no Node.js.
     Without this file on a release the updater falls back to the tag's source and builds the screen.
voice: the clips of releases/voice-<version>.json packed into pieces of about 48 MB, plus
     voice-chunks-<version>.json saying which piece holds each clip. Clips unchanged since the
     previous list stay in their old pieces, so an installation that has them downloads only the new
     pieces. --repack starts over when old pieces hold too many clips nobody needs any more.

Nothing is uploaded; the gh commands to upload are printed at the end.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "iRacing_Backend"))
import app_update  # noqa: E402
import make_patch  # noqa: E402

CHUNK_BYTES = 48 << 20


def git_files(root: Path) -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return sorted(name for name in listed.decode("utf-8").split("\0") if name and (root / name).is_file())


def make_app(root: Path, out: Path, allow_dirty: bool = False) -> Path:
    from launch_app import build_is_current
    version = app_update.version_in(root)
    package = json.loads((root / "package.json").read_text(encoding="utf-8")).get("version")
    if not version or package != version:
        raise ValueError(f"APP_VERSION({version})과 package.json 버전({package})이 다릅니다.")
    if not build_is_current():
        raise ValueError("build/가 없거나 소스보다 오래됐습니다. 먼저 npm run build를 실행하세요.")
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
    if dirty and not allow_dirty:
        raise ValueError("커밋하지 않은 변경이 있습니다. 릴리스 태그와 같은 내용이 되도록 커밋한 뒤 만드세요.")
    from make_share_copy import bundled_bridge_files
    bridges = bundled_bridge_files(root)
    committed = git_files(root)
    missing = sorted(set(bridges) - set(committed))
    if missing:
        raise ValueError("LMU 사전 빌드 파일을 먼저 Git에 추가하세요: " + ", ".join(missing))
    names = [name for name in committed if not name.replace('\\', '/').lower().startswith('runtime/')] + sorted(path.relative_to(root).as_posix() for path in (root / "build").rglob("*") if path.is_file())
    target = out / app_update.APP_ASSET.format(version=version)
    names = sorted(set(names))
    out.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            source = root / name
            archive.write(source, f"racingcoach-{version}/{name}",
                          zipfile.ZIP_STORED if source.suffix.lower() in make_patch.STORED else zipfile.ZIP_DEFLATED)
    partial.replace(target)
    return target


def runtime_archive(root: Path) -> tuple[Path, dict]:
    """Obtain the pinned archive; verify even a previously cached download."""
    spec = json.loads((root / 'scripts' / 'python_runtime.json').read_text(encoding='utf-8'))
    if (not re.fullmatch(r'3\.12\.[0-9]+', spec['version'])
            or not re.fullmatch(r'[0-9a-f]{64}', spec['sha256'])
            or not isinstance(spec['bytes'], int) or not 0 < spec['bytes'] < 200_000_000):
        raise ValueError('Python runtime specification is invalid')
    url = urllib.parse.urlparse(spec['url'])
    name = urllib.parse.unquote(url.path.rsplit('/', 1)[-1])
    if (url.scheme != 'https' or url.netloc != 'github.com'
            or name != f"cpython-{spec['version']}+{spec['release']}-x86_64-pc-windows-msvc-install_only.tar.gz"
            or url.path.rsplit('/', 1)[0] != '/astral-sh/python-build-standalone/releases/download/' + spec['release']):
        raise ValueError('Python runtime URL is invalid')
    cache = root / 'share' / 'python-runtime'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    if not target.exists():
        partial = target.with_name(target.name + '.part')
        try:
            request = urllib.request.Request(spec['url'], headers={'User-Agent': 'racingcoach-runtime-packager'})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as stream:
                count = 0
                while block := response.read(1 << 20):
                    count += len(block)
                    if count > spec['bytes']:
                        raise ValueError('Python runtime download is larger than pinned size')
                    stream.write(block)
            if partial.stat().st_size != spec['bytes'] or make_patch.digest(partial) != spec['sha256']:
                raise ValueError('Python runtime download hash/size mismatch')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    if target.stat().st_size != spec['bytes'] or make_patch.digest(target) != spec['sha256']:
        raise ValueError('Cached Python runtime hash/size mismatch')
    return target, spec


def extract_runtime(archive: Path, destination: Path, spec: dict) -> Path:
    """Extract ordinary files under python/ only, preserving all bundled licenses."""
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Python runtime extraction requires an empty folder')
    with tarfile.open(archive, 'r:gz') as packed:
        members = packed.getmembers()
        seen = set()
        total = 0
        for member in members:
            path = PurePosixPath(member.name)
            parts = member.name.rstrip('/').split('/')
            invalid = (path.is_absolute() or not parts or parts[0] != 'python'
                       or any(part in ('', '.', '..') or ':' in part or '\\' in part
                              or part.rstrip('. ') != part
                              or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
                              for part in parts)
                       or not (member.isfile() or member.isdir()))
            key = path.as_posix().casefold()
            if invalid or key in seen or not (destination / path).resolve().is_relative_to(destination.resolve()):
                raise ValueError('Unsafe Python runtime archive member')
            seen.add(key)
            total += member.size if member.isfile() else 0
            if total > spec['extracted_bytes']:
                raise ValueError('Python runtime extracted size exceeds pinned size')
        required = {'python/python.exe', 'python/DLLs/_tkinter.pyd', 'python/DLLs/_ssl.pyd',
                    'python/DLLs/_sqlite3.pyd', 'python/DLLs/_ctypes.pyd', 'python/LICENSE.txt',
                    'python/Lib/site-packages/pip/__init__.py'}
        files = {m.name for m in members if m.isfile()}
        if total != spec['extracted_bytes'] or not required <= files:
            raise ValueError('Python runtime lacks required modules or has an unexpected size')
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with packed.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
    return destination / 'python'


def make_setup(root: Path, out: Path, allow_dirty: bool = False) -> Path:
    archive, spec = runtime_archive(root)
    with tempfile.TemporaryDirectory(prefix='racingcoach-runtime-') as temp:
        runtime = extract_runtime(archive, Path(temp), spec)
        app = make_app(root, out, allow_dirty)
        version = app_update.version_in(root)
        target = out / f'racingcoach-setup-{version}.zip'
        partial = target.with_name(target.name + '.part')
        try:
            with zipfile.ZipFile(app) as program, zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as setup:
                for info in program.infolist():
                    with program.open(info) as source, setup.open(info, 'w') as destination:
                        shutil.copyfileobj(source, destination)
                for path in sorted(runtime.rglob('*')):
                    if path.is_file():
                        setup.write(path, f'racingcoach-{version}/runtime/python/{path.relative_to(runtime).as_posix()}')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    return target


def previous_list(source: str | None) -> dict | None:
    """The piece list published before, from a file or the voice release; None for a first upload."""
    if source:
        return json.loads(Path(source).read_text(encoding="utf-8"))
    try:
        return app_update.fetch_json(f"{app_update.DOWNLOAD}/{app_update.VOICE_TAG}/{app_update.VOICE_LATEST}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def make_voice(version: str, files: dict[str, str], read, out: Path, previous: dict | None,
               chunk_bytes: int = CHUNK_BYTES) -> dict:
    """Write new pieces for clips not already in a previous piece, and the list of all of them.

    read(path) gives the bytes of one listed clip, from the data folders or from a full voice pack zip.
    """
    if previous is not None and previous.get("format") != app_update.VOICE_FORMAT:
        raise ValueError("이전 음성 목록 형식이 맞지 않습니다.")
    old_files, old_chunks = (previous or {}).get("files", {}), (previous or {}).get("chunks", {})
    entries, chunks, fresh = {}, {}, []
    for path in sorted(files):
        entry = old_files.get(path)
        if entry and entry[0] == files[path] and entry[2] in old_chunks:
            entries[path] = entry
            chunks[entry[2]] = old_chunks[entry[2]]
        else:
            fresh.append(path)
    out.mkdir(parents=True, exist_ok=True)
    archive, size, written, members = None, 0, [], []

    def close() -> None:
        # Named by content: a published piece never changes, so a list someone already holds stays valid.
        nonlocal archive
        if archive is None:
            return
        archive.close()
        sha = make_patch.digest(Path(archive.filename))
        target = Path(archive.filename).with_name(f"voice-{version}-{sha[:12]}.zip")
        Path(archive.filename).replace(target)
        chunks[target.name] = {"sha256": sha, "bytes": target.stat().st_size}
        for path in members:
            entries[path][2] = target.name
        written.append(target)
        archive = None
    for path in fresh:
        data = read(path)
        if hashlib.sha256(data).hexdigest() != files[path]:
            raise ValueError(f"음성 파일이 목록과 다릅니다: {path}")
        if archive is None or size + len(data) > chunk_bytes:
            close()
            archive, size, members = zipfile.ZipFile(out / "piece.part", "w", zipfile.ZIP_STORED), 0, []
        archive.writestr(path, data)
        entries[path] = [files[path], len(data), None]
        members.append(path)
        size += len(data)
    close()
    listing = {"format": app_update.VOICE_FORMAT, "version": version, "files": entries, "chunks": chunks}
    for name in (app_update.VOICE_LIST.format(version=version), app_update.VOICE_LATEST):
        (out / name).write_text(json.dumps(listing, separators=(",", ":")), encoding="utf-8")
    used = sum(entry[1] for entry in entries.values())
    return {"new_pieces": [path.name for path in written], "new_clips": len(fresh), "reused_clips": len(files) - len(fresh),
            "pieces": len(chunks), "piece_bytes": sum(item["bytes"] for item in chunks.values()), "clip_bytes": used}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    app = commands.add_parser("app", help="프로그램과 빌드된 화면")
    app.add_argument("--out", type=Path, default=ROOT / "share" / "release")
    app.add_argument("--allow-dirty", action="store_true", help="커밋하지 않은 변경이 있어도 만듭니다(시험용)")
    setup = commands.add_parser('setup', help='처음 설치용 프로그램·화면·Python 런타임')
    setup.add_argument('--out', type=Path, default=ROOT / 'share' / 'release')
    setup.add_argument('--allow-dirty', action='store_true', help='커밋하지 않은 변경 허용(시험용)')
    voice = commands.add_parser("voice", help="음성 조각과 목록")
    voice.add_argument("--version", required=True, help="releases/voice-<version>.json의 버전")
    voice.add_argument("--previous", help="이전 voice-chunks 목록 파일 (없으면 GitHub voice 릴리스에서 받습니다)")
    voice.add_argument("--repack", action="store_true", help="이전 조각을 쓰지 않고 전부 새로 묶습니다")
    voice.add_argument("--from-zip", type=Path, help="데이터 폴더 대신 전체 음성팩 zip(iracing-coach-voice-full-<version>.zip)에서 읽습니다")
    voice.add_argument("--out", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.command in ('app', 'setup'):
            builder = make_setup if args.command == 'setup' else make_app
            target = builder(ROOT, args.out.resolve(), args.allow_dirty)
            version = app_update.version_in(ROOT)
            print(f"프로그램: {target} ({target.stat().st_size / 1e6:.1f} MB)")
            print(f'\n올리기:\n  gh release upload v{version} "{target}" --clobber')
            return 0
        listed = ROOT / "releases" / f"voice-{args.version}.json"
        files = json.loads(listed.read_text(encoding="utf-8"))["files"]
        out = (args.out or ROOT / "share" / "voice-release" / args.version).resolve()
        if out.exists() and any(out.iterdir()):
            raise ValueError(f"비어 있지 않은 폴더입니다: {out}")
        previous = None if args.repack else previous_list(args.previous)
        if args.from_zip:
            with zipfile.ZipFile(args.from_zip) as pack:
                summary = make_voice(args.version, files, pack.read, out, previous)
        else:
            import app_settings
            app_settings.apply_environment()
            folders = {"data/radio_audio/": app_settings.data_dir() / "radio_audio", "data/spotter_audio/": app_settings.spotter_audio_dir()}
            read = lambda path: next(Path(folder) / path[len(prefix):] for prefix, folder in folders.items()
                                     if path.startswith(prefix)).read_bytes()
            summary = make_voice(args.version, files, read, out, previous)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(f"만들지 못했습니다: {error}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    waste = summary["piece_bytes"] - summary["clip_bytes"]
    if waste > summary["clip_bytes"] * .3:
        print(f"\n이전 조각 안에 더는 쓰지 않는 음성이 {waste / 1e6:.0f} MB 있습니다. 처음 받는 사람의 다운로드가 늘어나니 --repack을 고려하세요.")
    tag = app_update.VOICE_TAG
    print("\n올리기 (voice 릴리스가 없으면 첫 줄로 만듭니다. 최신 릴리스로 표시되지 않게 --latest=false):")
    print(f'  gh release create {tag} --title "음성 파일 (앱 자동 다운로드용)" --notes "앱이 자동으로 받는 음성 조각입니다. 직접 받을 필요는 없습니다." --latest=false')
    print(f'  gh release upload {tag} "{out}"/*.zip "{out}"/*.json --clobber')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
