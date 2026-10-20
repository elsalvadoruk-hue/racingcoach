"""Updates from the GitHub releases: the program itself and the voice clips.

The program: the newest vX.Y.Z release is compared with APP_VERSION. Its files are downloaded and
unpacked into .update/ beside the program while the app keeps running. The launcher
(scripts/launch_app.py) copies them over the program the next time it starts, before anything
runs; data/ is never touched. A release may carry racingcoach-app-<version>.zip with the built
screen in it (scripts/make_release_assets.py); without one the tag's source is used and the screen
is built as 설치.bat builds it.

The voice: the release tagged "voice" holds the clips packed in zip pieces and, per voice version,
a list (voice-chunks-<version>.json) of which piece holds each clip. Which clips this program
needs is releases/voice-<version>.json, so a program update brings its voice requirement with it.
Only the pieces holding clips this installation lacks are downloaded.

Standard library only: the launcher imports this before any package is known to be installed.
"""
from __future__ import annotations

import filecmp
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Callable
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = "elsalvadoruk-hue/racingcoach"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
DOWNLOAD = f"https://github.com/{REPO}/releases/download"
SOURCE_ZIP = f"https://github.com/{REPO}/archive/refs/tags/{{tag}}.zip"
VOICE_TAG = "voice"
# The same folder the release notes link to, for whoever prefers to download by hand.
VOICE_DRIVE_URL = "https://drive.google.com/drive/folders/1MPvrKi4Zw9fInU5f84mCwe_Bt0uhW631?usp=sharing"
APP_ASSET = "racingcoach-app-{version}.zip"
VOICE_LIST = "voice-chunks-{version}.json"
VOICE_LATEST = "voice-chunks-latest.json"
VOICE_FORMAT = "racingcoach-voice-chunks-v1"
# The backend exits with this when the screen asked to apply a staged update; the launcher restarts.
RESTART_EXIT = 75
# Set by the launcher for the backend it supervises: only then is there someone to restart it.
RESTART_ENV = "COACH_LAUNCHER_RESTART"
# Never replaced from a download: the receiver's records, and what a developer checkout keeps.
KEEP = ("data/", ".git/", ".update/", "node_modules/", "logs/")
CHECK_EVERY = 6 * 3600


def work_dir(root: Path = ROOT) -> Path:
    return root / ".update"


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    found = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    return tuple(int(part) for part in found.groups()) if found else None


def version_in(folder: Path) -> str | None:
    """APP_VERSION of a program folder, read as make_patch.version_of reads it."""
    try:
        text = (folder / "iRacing_Backend" / "backend_api.py").read_text(encoding="utf-8")
    except OSError:
        return None
    found = re.search(r'^APP_VERSION = "([^"]+)"', text, re.MULTILINE)
    return found[1] if found else None


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def _request(url: str, accept: str = "*/*") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "racingcoach-updater", "Accept": accept})


def fetch_json(url: str, timeout: float = 30, limit: int = 64 << 20):
    accept = "application/vnd.github+json" if url.startswith("https://api.github.com/") else "application/json"
    with urllib.request.urlopen(_request(url, accept), timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("응답이 너무 큽니다: " + url)
    return json.loads(data.decode("utf-8"))


def download(url: str, target: Path, progress: Callable[[int], None] | None = None,
             sha256: str | None = None, stop: threading.Event | None = None, timeout: float = 60) -> str:
    """Stream one file to target through a .part file; the hash is checked before it is kept."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    sha = hashlib.sha256()
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as response, partial.open("wb") as file:
            for block in iter(lambda: response.read(1 << 16), b""):
                if stop is not None and stop.is_set():
                    raise InterruptedError("멈췄습니다.")
                file.write(block)
                sha.update(block)
                if progress:
                    progress(len(block))
        found = sha.hexdigest()
        if sha256 and found != sha256.lower():
            raise ValueError(f"받은 파일이 손상되었습니다: {target.name}")
        os.replace(partial, target)
        return found
    finally:
        partial.unlink(missing_ok=True)


def newest_release(releases: list) -> dict | None:
    """The highest vX.Y.Z release that is published; drafts, pre-releases and the voice store are skipped."""
    best = None
    for item in releases if isinstance(releases, list) else []:
        version = parse_version(item.get("tag_name"))
        if not version or item.get("draft") or item.get("prerelease"):
            continue
        if best is None or version > best[0]:
            best = (version, item)
    if best is None:
        return None
    item = best[1]
    name = ".".join(map(str, best[0]))
    asset = next((entry for entry in item.get("assets") or [] if entry.get("name") == APP_ASSET.format(version=name)), None)
    digest_text = str((asset or {}).get("digest") or "")
    return {"version": name, "tag": item["tag_name"], "title": item.get("name") or item["tag_name"],
            "notes": item.get("body") or "", "url": item.get("html_url") or "",
            "published": item.get("published_at") or "",
            "asset": {"url": asset["browser_download_url"], "size": asset.get("size") or 0,
                      "sha256": digest_text.split(":", 1)[1] if digest_text.startswith("sha256:") else None} if asset else None}


def unpack(archive: Path, target: Path) -> dict[str, str]:
    """Unzip with the archive's single top folder removed (GitHub source zips have one) and no path leaving target.

    Returns the sha256 of every file written, by its path with forward slashes.
    """
    hashes = {}
    with zipfile.ZipFile(archive) as source:
        names = [info for info in source.infolist() if not info.is_dir()]
        tops = {info.filename.split("/", 1)[0] for info in names}
        strip = len(tops) == 1 and all("/" in info.filename for info in names)
        base = target.resolve()
        for info in names:
            name = info.filename.split("/", 1)[1] if strip else info.filename
            destination = (target / name).resolve()
            if not name or base not in destination.parents:
                raise ValueError(f"압축 파일에 잘못된 경로가 있습니다: {info.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            sha = hashlib.sha256()
            with source.open(info) as reader, destination.open("wb") as writer:
                for block in iter(lambda: reader.read(1 << 20), b""):
                    writer.write(block)
                    sha.update(block)
            hashes[name] = sha.hexdigest()
    return hashes


def read_pending(root: Path = ROOT) -> dict | None:
    try:
        pending = json.loads((work_dir(root) / "pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return pending if isinstance(pending, dict) and (work_dir(root) / "app").is_dir() else None


def clear_staged(root: Path = ROOT) -> None:
    (work_dir(root) / "pending.json").unlink(missing_ok=True)
    shutil.rmtree(work_dir(root) / "app", ignore_errors=True)


def stage_app(release: dict, root: Path = ROOT, progress: Callable[[int], None] | None = None,
              stop: threading.Event | None = None) -> dict:
    """Download and unpack a release into .update/app; the running program is not changed."""
    work = work_dir(root)
    clear_staged(root)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / f"app-{release['version']}.zip"
    asset = release.get("asset")
    url = asset["url"] if asset else SOURCE_ZIP.format(tag=release["tag"])
    try:
        download(url, archive, progress, (asset or {}).get("sha256"), stop)
        hashes = unpack(archive, work / "app")
    finally:
        archive.unlink(missing_ok=True)
    found = version_in(work / "app")
    if found != release["version"]:
        clear_staged(root)
        raise ValueError(f"받은 프로그램의 버전({found or '알 수 없음'})이 릴리스 {release['version']}와 다릅니다.")
    # Reading the installed files is done now, while the app keeps running, so the restart that applies
    # the update only checks their size and time instead of reading every one of them again.
    unchanged = {}
    for relative, sha in hashes.items():
        if _kept(relative):
            continue
        try:
            stat = (root / relative).stat()
        except OSError:
            continue
        if stat.st_size == (work / "app" / relative).stat().st_size and digest(root / relative) == sha:
            unchanged[relative] = [stat.st_size, stat.st_mtime_ns]
    pending = {"version": release["version"], "has_build": (work / "app" / "build" / "index.html").is_file(),
               "staged_at": time.time(), "files": hashes, "unchanged": unchanged}
    (work / "pending.json").write_text(json.dumps(pending), encoding="utf-8")
    return pending


def _kept(relative: str) -> bool:
    return relative.startswith(KEEP) or "__pycache__" in relative.split("/")


def _same(staged: Path, target: Path, sha: str | None, known: list | None) -> bool:
    """Whether the installed file already is the staged one; a file unchanged since staging is not read again."""
    try:
        stat = target.stat()
    except OSError:
        return False
    if known is not None and [stat.st_size, stat.st_mtime_ns] == known:
        return True
    if stat.st_size != staged.stat().st_size:
        return False
    return digest(target) == sha if sha else filecmp.cmp(staged, target, shallow=False)


def _requirements(root: Path) -> dict[str, str]:
    folder = root / "iRacing_Backend"
    return {path.name: digest(path) for path in folder.glob("requirements*.txt")} if folder.is_dir() else {}


def apply_staged(root: Path = ROOT, log: Callable[[str], None] = print) -> dict | None:
    """Copy a staged program over this one while nothing of it runs. Only files that differ are written."""
    pending = read_pending(root)
    if not pending:
        return None
    if (root / ".git").exists():
        log("Git으로 받은 폴더라 자동 업데이트를 적용하지 않습니다. git pull로 업데이트하세요.")
        clear_staged(root)
        return None
    tree = work_dir(root) / "app"
    before = _requirements(root)
    hashes, unchanged = pending.get("files") or {}, pending.get("unchanged") or {}
    written = 0
    for path in sorted(tree.rglob("*")):
        relative = path.relative_to(tree).as_posix()
        if not path.is_file() or _kept(relative):
            continue
        target = root / relative
        if _same(path, target, hashes.get(relative), unchanged.get(relative)):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".update-tmp")
        shutil.copyfile(path, temporary)
        os.replace(temporary, target)
        written += 1
    if pending.get("has_build"):
        # Written last, so the launcher sees the built screen as newer than the sources copied with it.
        os.utime(root / "build" / "index.html")
    result = {"version": pending.get("version"), "files": written, "has_build": bool(pending.get("has_build")),
              "requirements_changed": _requirements(root) != before}
    clear_staged(root)
    return result


class AppUpdater:
    """Checks the releases and stages the newest program for the launcher to apply."""

    def __init__(self, version: str, root: Path = ROOT, fetch=fetch_json, stage=stage_app,
                 check_every: float = CHECK_EVERY):
        self.version, self.root, self._fetch, self._stage, self._check_every = version, root, fetch, stage, check_every
        self._lock = threading.Lock()
        self._checking = False
        self._checked_at = 0.0
        self._error = ""
        self._latest: dict | None = None
        self._download = {"state": "IDLE", "done": 0, "total": 0, "message": ""}

    @property
    def git_checkout(self) -> bool:
        return (self.root / ".git").exists()

    def _run_check(self) -> None:
        try:
            latest, error = newest_release(self._fetch(RELEASES_API)), ""
        except (OSError, ValueError, KeyError, urllib.error.URLError) as failure:
            latest, error = None, f"업데이트를 확인하지 못했습니다: {failure}"
        with self._lock:
            self._latest = latest if latest or error == "" else self._latest
            self._error, self._checked_at, self._checking = error, time.time(), False

    def check(self, wait: bool = False) -> None:
        with self._lock:
            if self._checking:
                return
            self._checking = True
        if wait:
            self._run_check()
        else:
            threading.Thread(target=self._run_check, name="update-check", daemon=True).start()

    def available(self) -> bool:
        latest, current = self._latest, parse_version(self.version)
        return bool(latest and current and parse_version(latest["version"]) > current)

    def status(self) -> dict:
        if not self._checking and time.time() - self._checked_at > self._check_every:
            self.check()
        pending = read_pending(self.root)
        with self._lock:
            latest = self._latest
            return {"current": self.version, "available": self.available(), "checking": self._checking,
                    "error": self._error, "checked_at": self._checked_at or None,
                    "latest": {key: latest[key] for key in ("version", "title", "notes", "url", "published")} | {
                        "prebuilt": bool(latest.get("asset"))} if latest else None,
                    "mode": "git" if self.git_checkout else "zip",
                    "staged": pending.get("version") if pending else None,
                    "download": dict(self._download),
                    "restart_supported": os.getenv(RESTART_ENV) == "1"}

    def _run_download(self, release: dict) -> None:
        def progress(count: int) -> None:
            with self._lock:
                self._download["done"] += count
        try:
            self._stage(release, self.root, progress)
            state, message = "STAGED", f"{release['version']} 준비 완료. 재시작하면 적용됩니다."
        except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as failure:
            state, message = "FAILED", f"업데이트를 받지 못했습니다: {failure}"
        with self._lock:
            self._download.update(state=state, message=message)

    def start_download(self) -> dict:
        if self.git_checkout:
            raise PermissionError("Git으로 받은 폴더입니다. git pull로 업데이트하세요.")
        with self._lock:
            if self._download["state"] == "RUNNING":
                return dict(self._download)
            if not self.available():
                raise LookupError("받을 새 버전이 없습니다.")
            release = dict(self._latest)
            self._download = {"state": "RUNNING", "done": 0, "total": (release.get("asset") or {}).get("size") or 0,
                              "message": f"{release['version']} 받는 중"}
        threading.Thread(target=self._run_download, args=(release,), name="update-download", daemon=True).start()
        return dict(self._download)


def voice_lists(root: Path = ROOT) -> list[tuple[tuple[int, int, int], Path]]:
    """The voice lists kept with the program (releases/voice-<version>.json), oldest first."""
    found = []
    for path in (root / "releases").glob("voice-*.json"):
        version = parse_version(path.stem[len("voice-"):])
        if version:
            found.append((version, path))
    return sorted(found)


def _read_list(path: Path) -> dict[str, str]:
    files = json.loads(path.read_text(encoding="utf-8")).get("files")
    return files if isinstance(files, dict) else {}


class VoiceSync:
    """Finds the clips this program needs but this installation lacks, and fetches only those."""

    def __init__(self, folders: Callable[[], dict[str, Path]], root: Path = ROOT, fetch=fetch_json, get=download):
        # folders(): where each packed prefix lives here, e.g. {"data/radio_audio/": <radio folder>}
        self.folders, self.root, self._fetch, self._get = folders, root, fetch, get
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scan: dict | None = None
        self._scanning = False
        self._job = {"state": "IDLE", "done_bytes": 0, "total_bytes": 0, "files_done": 0, "files_total": 0, "message": ""}

    def target(self, path: str) -> Path | None:
        for prefix, folder in self.folders().items():
            if path.startswith(prefix) and ".." not in path.split("/"):
                return Path(folder) / path[len(prefix):]
        return None

    def _remote(self, version: str | None) -> dict:
        """The piece list for this voice version, kept in .update/ after the first download.

        Without a version (a copy that has no releases/ folder) the newest list is asked for each time
        and kept under its own version, so the download that follows does not fetch it again.
        """
        cache = work_dir(self.root) / VOICE_LIST.format(version=version) if version else None
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (AttributeError, OSError, ValueError):
            data = self._fetch(f"{DOWNLOAD}/{VOICE_TAG}/{cache.name if cache else VOICE_LATEST}")
            if not isinstance(data, dict) or data.get("format") != VOICE_FORMAT or not parse_version(data.get("version")):
                raise ValueError("음성 목록 형식이 맞지 않습니다.")
            cache = work_dir(self.root) / VOICE_LIST.format(version=data["version"])
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
        if version and data.get("version") != version:
            raise ValueError(f"음성 목록 버전({data.get('version')})이 필요한 {version}와 다릅니다.")
        return data

    def required(self) -> tuple[str, dict[str, str]]:
        """Version and {path: sha256} of the clips this program needs. A copy without releases/ asks GitHub."""
        lists = voice_lists(self.root)
        if lists:
            version, path = lists[-1]
            return ".".join(map(str, version)), _read_list(path)
        remote = self._remote(None)
        return remote["version"], {path: entry[0] for path, entry in remote["files"].items()}

    def _missing(self, files: dict[str, str], sizes: dict[str, int]) -> list[str]:
        missing = []
        for path in files:
            target = self.target(path)
            if target is None:
                continue
            try:
                size = target.stat().st_size
            except OSError:
                missing.append(path)
                continue
            if path in sizes and sizes[path] != size:
                missing.append(path)
        return missing

    def _obsolete(self, current: dict[str, str]) -> list[Path]:
        """Clips an earlier voice version shipped that this one no longer uses (only files once shipped)."""
        old: set[str] = set()
        for _, path in voice_lists(self.root)[:-1]:
            old.update(name for name in _read_list(path) if name not in current)
        found = []
        for name in sorted(old):
            target = self.target(name)
            if target is not None and target.is_file():
                found.append(target)
        return found

    def _cached_sizes(self, version: str) -> dict[str, int]:
        try:
            files = json.loads((work_dir(self.root) / VOICE_LIST.format(version=version)).read_text(encoding="utf-8"))["files"]
            return {path: entry[1] for path, entry in files.items()}
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return {}

    def _run_scan(self) -> None:
        try:
            version, files = self.required()
            missing = self._missing(files, self._cached_sizes(version))
            obsolete = self._obsolete(files)
            scan = {"version": version, "total": len(files), "missing": len(missing),
                    "obsolete_files": len(obsolete), "obsolete_bytes": sum(path.stat().st_size for path in obsolete),
                    "error": ""}
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as failure:
            scan = {"version": None, "total": 0, "missing": 0, "obsolete_files": 0, "obsolete_bytes": 0,
                    "error": f"음성 상태를 확인하지 못했습니다: {failure}"}
        with self._lock:
            self._scan, self._scanning = scan, False

    def rescan(self, wait: bool = False) -> None:
        with self._lock:
            if self._scanning:
                return
            self._scanning = True
        if wait:
            self._run_scan()
        else:
            threading.Thread(target=self._run_scan, name="voice-scan", daemon=True).start()

    def status(self) -> dict:
        if self._scan is None and not self._scanning:
            self.rescan()
        with self._lock:
            return {**(self._scan or {}), "scanning": self._scanning, "job": dict(self._job), "drive_url": VOICE_DRIVE_URL}

    def _set(self, **values) -> None:
        with self._lock:
            self._job.update(values)

    def _finish(self, state: str, message: str) -> None:
        """Count again before the job reports its end, so a screen that stops polling then shows the new count."""
        self._run_scan()
        self._set(state=state, message=message)

    def _run_download(self) -> None:
        work = work_dir(self.root) / "voice"
        try:
            version, files = self.required()
            remote = self._remote(version)
            sizes = {path: entry[1] for path, entry in remote["files"].items()}
            missing = self._missing(files, sizes)
            pieces: dict[str, list[str]] = {}
            unavailable = 0
            for path in missing:
                entry = remote["files"].get(path)
                if not entry or entry[0] != files[path]:
                    unavailable += 1
                    continue
                pieces.setdefault(entry[2], []).append(path)
            self._set(total_bytes=sum(remote["chunks"][name]["bytes"] for name in pieces),
                      files_total=sum(map(len, pieces.values())), message=f"음성 {sum(map(len, pieces.values())):,}개 받는 중")

            def progress(count: int) -> None:
                with self._lock:
                    self._job["done_bytes"] += count
            for name, paths in sorted(pieces.items()):
                archive = work / name
                self._get(f"{DOWNLOAD}/{VOICE_TAG}/{name}", archive, progress, remote["chunks"][name]["sha256"], self._stop)
                try:
                    with zipfile.ZipFile(archive) as source:
                        for path in paths:
                            data = source.read(path)
                            if hashlib.sha256(data).hexdigest() != files[path]:
                                raise ValueError(f"음성 파일이 손상되었습니다: {path}")
                            target = self.target(path)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            temporary = target.with_name(target.name + ".part")
                            temporary.write_bytes(data)
                            os.replace(temporary, target)
                            with self._lock:
                                self._job["files_done"] += 1
                finally:
                    archive.unlink(missing_ok=True)
            message = "음성을 모두 받았습니다." if not unavailable else f"음성을 받았습니다. 서버에 없는 음성 {unavailable:,}개는 처음 쓰일 때 합성합니다."
            self._finish("COMPLETE", message)
        except InterruptedError:
            self._finish("STOPPED", "멈췄습니다. 받은 음성은 그대로 씁니다.")
        except urllib.error.HTTPError as failure:
            self._finish("FAILED", "GitHub에 이 버전의 음성이 아직 없습니다. 아래 Google Drive 링크로 받으세요."
                         if failure.code == 404 else f"음성을 받지 못했습니다: {failure}")
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, urllib.error.URLError) as failure:
            self._finish("FAILED", f"음성을 받지 못했습니다: {failure}")

    def _run_cleanup(self) -> None:
        try:
            version, files = self.required()
            removed = 0
            for path in self._obsolete(files):
                path.unlink(missing_ok=True)
                removed += 1
            self._finish("COMPLETE", f"이전 버전 음성 {removed:,}개를 정리했습니다.")
        except (OSError, ValueError, KeyError, urllib.error.URLError) as failure:
            self._finish("FAILED", f"정리하지 못했습니다: {failure}")

    def _start(self, runner, message: str) -> dict:
        with self._lock:
            if self._job["state"] == "RUNNING":
                return dict(self._job)
            self._stop.clear()
            self._job = {"state": "RUNNING", "done_bytes": 0, "total_bytes": 0, "files_done": 0, "files_total": 0, "message": message}
        threading.Thread(target=runner, name="voice-sync", daemon=True).start()
        return dict(self._job)

    def start_download(self) -> dict:
        return self._start(self._run_download, "음성 목록 확인 중")

    def start_cleanup(self) -> dict:
        return self._start(self._run_cleanup, "이전 음성 정리 중")

    def stop(self) -> None:
        self._stop.set()
