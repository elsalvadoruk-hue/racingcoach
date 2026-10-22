"""The updater with local files in place of GitHub: nothing is downloaded and no real install is changed."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import app_update


def wait_until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(.02)


def release(tag, draft=False, prerelease=False, assets=()):
    return {"tag_name": tag, "name": f"{tag} title", "body": "notes", "html_url": f"https://example/{tag}",
            "draft": draft, "prerelease": prerelease, "published_at": "2026-10-20T00:00:00Z", "assets": list(assets)}


def program(version, **extra):
    files = {"iRacing_Backend/backend_api.py": f'APP_VERSION = "{version}"\n',
             "iRacing_Backend/requirements_core.txt": "fastapi\n", "src/App.js": "new screen\n",
             "실행.bat": "@echo off\r\n"}
    files.update(extra)
    return files


def write_zip(path, files, top="racingcoach-x/"):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(top + name, text)
    return path


class ReleaseChoiceTests(unittest.TestCase):
    def test_versions_are_compared_as_numbers(self):
        self.assertGreater(app_update.parse_version("v1.10.0"), app_update.parse_version("1.9.9"))
        self.assertIsNone(app_update.parse_version("voice"))

    def test_newest_published_version_is_chosen_and_its_prebuilt_asset_found(self):
        asset = {"name": "racingcoach-app-1.5.0.zip", "browser_download_url": "https://example/app.zip", "size": 9,
                 "digest": "sha256:" + "a" * 64}
        found = app_update.newest_release([release("v1.4.1"), release("voice"), release("v2.0.0", draft=True),
                                           release("v1.6.0", prerelease=True), release("v1.5.0", assets=[asset])])
        self.assertEqual((found["version"], found["tag"]), ("1.5.0", "v1.5.0"))
        self.assertEqual(found["asset"], {"url": "https://example/app.zip", "size": 9, "sha256": "a" * 64})
        self.assertIsNone(app_update.newest_release([release("voice")]))


class DownloadTests(unittest.TestCase):
    def test_a_damaged_download_is_not_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.bin"
            source.write_bytes(b"clip")
            target = Path(temp) / "out" / "clip.bin"
            seen = []
            app_update.download(source.as_uri(), target, seen.append, hashlib.sha256(b"clip").hexdigest())
            self.assertEqual((target.read_bytes(), sum(seen)), (b"clip", 4))
            with self.assertRaisesRegex(ValueError, "손상"):
                app_update.download(source.as_uri(), Path(temp) / "bad.bin", sha256="0" * 64)
            self.assertEqual(sorted(path.name for path in Path(temp).iterdir()), ["out", "source.bin"])


class StageAndApplyTests(unittest.TestCase):
    def test_runtime_is_preserved_even_when_an_update_contains_case_variant_paths(self):
        self.assertIn('runtime/', app_update.KEEP)
        runtime = self.root / 'runtime/python'
        runtime.mkdir(parents=True)
        interpreter = runtime / 'python.exe'
        interpreter.write_bytes(b'keep the running interpreter')
        before = interpreter.stat()
        self.stage(program('1.5.0', **{'runtime/python/python.exe':'replace',
                                      'Runtime/Python/python.exe':'case variant',
                                      'runtime/python/new.dll':'must not be added'}))
        app_update.apply_staged(self.root, log=lambda _: None)
        self.assertEqual(interpreter.read_bytes(), b'keep the running interpreter')
        self.assertEqual(interpreter.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertFalse((runtime / 'new.dll').exists())
        self.assertEqual(app_update.version_in(self.root), '1.5.0')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "app"
        for name, text in program("1.4.1").items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(text.encode("utf-8"))
        (self.root / "src" / "App.js").write_bytes(b"old screen\n")
        (self.root / "data").mkdir()
        (self.root / "data" / "settings.json").write_text("mine", encoding="utf-8")

    def stage(self, files, version="1.5.0", asset=None):
        archive = write_zip(Path(self.temp.name) / "release.zip", files)
        fake = lambda url, target, progress=None, sha256=None, stop=None: shutil.copyfile(archive, target)
        with patch.object(app_update, "download", side_effect=fake) as got:
            result = app_update.stage_app({"version": version, "tag": f"v{version}", "asset": asset}, self.root)
        return result, got.call_args.args[0]

    def test_source_release_is_staged_then_applied_without_touching_data(self):
        files = program("1.5.0", **{"iRacing_Backend/requirements_core.txt": "fastapi\nsoundfile\n",
                                    "data/settings.json": "theirs", "iRacing_Backend/new_module.py": "x = 1\n"})
        pending, url = self.stage(files)
        self.assertEqual(url, "https://github.com/elsalvadoruk-hue/racingcoach/archive/refs/tags/v1.5.0.zip")
        self.assertEqual(pending["has_build"], False)
        self.assertEqual(app_update.version_in(self.root), "1.4.1")        # the running program is unchanged
        unchanged = self.root / "실행.bat"
        os.utime(unchanged, (1000, 1000))
        result = app_update.apply_staged(self.root, log=lambda text: None)
        self.assertEqual(result, {"version": "1.5.0", "files": 4, "has_build": False, "requirements_changed": True})
        self.assertEqual(app_update.version_in(self.root), "1.5.0")
        self.assertEqual((self.root / "src" / "App.js").read_text(encoding="utf-8"), "new screen\n")
        self.assertEqual((self.root / "data" / "settings.json").read_text(encoding="utf-8"), "mine")
        self.assertEqual(unchanged.stat().st_mtime, 1000)                   # identical files are not rewritten
        self.assertIsNone(app_update.read_pending(self.root))
        self.assertIsNone(app_update.apply_staged(self.root))

    def test_files_checked_while_downloading_are_not_read_again_when_applying(self):
        files = program("1.5.0", **{"README.md": "same\n", "LICENSE.md": "same\n"})
        for name in ("README.md", "LICENSE.md"):
            (self.root / name).write_bytes(b"same\n")
        pending, _ = self.stage(files)
        self.assertEqual(sorted(pending["unchanged"]), ["LICENSE.md", "README.md", "iRacing_Backend/requirements_core.txt", "실행.bat"])
        self.assertEqual(pending["files"]["src/App.js"], hashlib.sha256(b"new screen\n").hexdigest())
        os.utime(self.root / "LICENSE.md", (1000, 1000))                     # touched since, same content
        (self.root / "실행.bat").write_bytes(b"@echo on\r\n")                 # changed since staging
        read = []
        real = app_update.digest
        with patch.object(app_update, "digest", side_effect=lambda path: read.append(Path(path).name) or real(path)), \
             patch.object(app_update.filecmp, "cmp", side_effect=AssertionError("staged files are compared by hash")):
            result = app_update.apply_staged(self.root, log=lambda text: None)
        self.assertNotIn("README.md", read)                                  # unchanged since staging: not read
        self.assertIn("LICENSE.md", read)
        self.assertEqual((result["files"], result["requirements_changed"]), (3, False))   # backend_api, App.js, 실행.bat
        self.assertEqual((self.root / "실행.bat").read_bytes(), b"@echo off\r\n")
        self.assertEqual((self.root / "LICENSE.md").stat().st_mtime, 1000)    # same content: left as it was

    def test_prebuilt_screen_is_left_newer_than_the_sources(self):
        asset = {"url": "https://example/app.zip", "sha256": None}
        pending, url = self.stage(program("1.5.0", **{"build/index.html": "<html>"}), asset=asset)
        self.assertEqual((url, pending["has_build"]), ("https://example/app.zip", True))
        result = app_update.apply_staged(self.root, log=lambda text: None)
        self.assertFalse(result["requirements_changed"])
        built = (self.root / "build" / "index.html").stat().st_mtime
        self.assertGreaterEqual(built, (self.root / "src" / "App.js").stat().st_mtime)

    def test_a_release_whose_program_has_another_version_is_refused(self):
        with self.assertRaisesRegex(ValueError, "1.4.9"):
            self.stage(program("1.4.9"))
        self.assertIsNone(app_update.read_pending(self.root))

    def test_paths_leaving_the_folder_are_refused(self):
        archive = Path(self.temp.name) / "evil.zip"
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr("top/../../escape.txt", "x")
        with self.assertRaisesRegex(ValueError, "잘못된 경로"):
            app_update.unpack(archive, Path(self.temp.name) / "out")
        self.assertFalse((Path(self.temp.name) / "escape.txt").exists())

    def test_a_git_checkout_is_never_overwritten(self):
        self.stage(program("1.5.0"))
        (self.root / ".git").mkdir()
        messages = []
        self.assertIsNone(app_update.apply_staged(self.root, log=messages.append))
        self.assertIn("git pull", messages[0])
        self.assertEqual(app_update.version_in(self.root), "1.4.1")


class AppUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_check_reports_a_newer_release_and_stages_it(self):
        def stage(found, root, progress=None, stop=None):
            progress(5)
            (root / ".update" / "app").mkdir(parents=True)
            (root / ".update" / "pending.json").write_text(json.dumps({"version": found["version"]}), encoding="utf-8")
        updater = app_update.AppUpdater("1.4.1", self.root, fetch=lambda url: [release("v1.5.0")], stage=stage)
        updater.check(wait=True)
        status = updater.status()
        self.assertTrue(status["available"])
        self.assertEqual((status["latest"]["version"], status["latest"]["prebuilt"], status["mode"]), ("1.5.0", False, "zip"))
        updater.start_download()
        wait_until(lambda: updater.status()["download"]["state"] != "RUNNING")
        status = updater.status()
        self.assertEqual((status["download"]["state"], status["download"]["done"], status["staged"]), ("STAGED", 5, "1.5.0"))

    def test_nothing_newer_or_a_git_checkout_cannot_download(self):
        updater = app_update.AppUpdater("1.5.0", self.root, fetch=lambda url: [release("v1.5.0")])
        updater.check(wait=True)
        self.assertFalse(updater.status()["available"])
        with self.assertRaises(LookupError):
            updater.start_download()
        (self.root / ".git").mkdir()
        with self.assertRaises(PermissionError):
            updater.start_download()

    def test_a_failed_check_keeps_the_last_answer(self):
        answers = iter([[release("v1.5.0")], OSError("offline")])
        def fetch(url):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        updater = app_update.AppUpdater("1.4.1", self.root, fetch=fetch)
        updater.check(wait=True)
        updater.check(wait=True)
        status = updater.status()
        self.assertTrue(status["available"])
        self.assertIn("offline", status["error"])


class VoiceSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.server = base / "app", base / "server"
        self.radio, self.spotter = base / "data" / "radio_audio", base / "data" / "spotter_audio"
        for folder in (self.root / "releases", self.server, self.radio, self.spotter):
            folder.mkdir(parents=True)
        self.clips = {"data/radio_audio/a.mp3": b"aaa", "data/radio_audio/b.mp3": b"bbbb",
                      "data/radio_audio/pieces/c.ogg": b"cc", "data/spotter_audio/d.mp3": b"d"}
        sha = {path: hashlib.sha256(data).hexdigest() for path, data in self.clips.items()}
        old = {"data/radio_audio/a.mp3": sha["data/radio_audio/a.mp3"], "data/radio_audio/old.mp3": "x"}
        (self.root / "releases" / "voice-1.3.0.json").write_text(json.dumps({"version": "1.3.0", "files": old}), encoding="utf-8")
        (self.root / "releases" / "voice-1.4.0.json").write_text(json.dumps({"version": "1.4.0", "files": sha}), encoding="utf-8")
        (self.radio / "a.mp3").write_bytes(b"aaa")
        (self.radio / "old.mp3").write_bytes(b"obsolete")
        (self.radio / "made-here.mp3").write_bytes(b"synthesized with the user's key")
        pieces = {"one.zip": ["data/radio_audio/b.mp3", "data/radio_audio/a.mp3"],
                  "two.zip": ["data/radio_audio/pieces/c.ogg", "data/spotter_audio/d.mp3"]}
        chunks, files = {}, {}
        for name, paths in pieces.items():
            with zipfile.ZipFile(self.server / name, "w") as archive:
                for path in paths:
                    archive.writestr(path, self.clips[path])
                    files[path] = [sha[path], len(self.clips[path]), name]
            chunks[name] = {"sha256": app_update.digest(self.server / name), "bytes": (self.server / name).stat().st_size}
        self.listing = {"format": app_update.VOICE_FORMAT, "version": "1.4.0", "files": files, "chunks": chunks}
        self.requested = []

    def fetch(self, url):
        self.requested.append(url)
        return self.listing

    def get(self, url, target, progress=None, sha256=None, stop=None):
        self.requested.append(url)
        source = self.server / url.rsplit("/", 1)[1]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        progress(source.stat().st_size)

    def sync(self):
        return app_update.VoiceSync(lambda: {"data/radio_audio/": self.radio, "data/spotter_audio/": self.spotter},
                                    self.root, fetch=self.fetch, get=self.get)

    def test_only_missing_clips_are_fetched_into_the_installation_folders(self):
        sync = self.sync()
        sync.rescan(wait=True)
        status = sync.status()
        self.assertEqual((status["version"], status["total"], status["missing"]), ("1.4.0", 4, 3))
        self.assertEqual((status["obsolete_files"], status["obsolete_bytes"]), (1, 8))
        self.assertEqual(self.requested, [])                               # the scan alone downloads nothing
        sync.start_download()
        wait_until(lambda: sync.status()["job"]["state"] != "RUNNING")
        status = sync.status()
        self.assertEqual((status["job"]["state"], status["job"]["files_done"], status["missing"]), ("COMPLETE", 3, 0))
        self.assertEqual((self.radio / "pieces" / "c.ogg").read_bytes(), b"cc")
        self.assertEqual((self.spotter / "d.mp3").read_bytes(), b"d")
        self.assertEqual(sorted(url.rsplit("/", 1)[1] for url in self.requested), ["one.zip", "two.zip", "voice-chunks-1.4.0.json"])
        self.assertEqual(sorted(path.name for path in (self.root / ".update").iterdir()), ["voice", "voice-chunks-1.4.0.json"])

    def test_cleanup_removes_only_clips_an_earlier_version_shipped(self):
        sync = self.sync()
        sync.start_cleanup()
        wait_until(lambda: sync.status()["job"]["state"] != "RUNNING")
        self.assertFalse((self.radio / "old.mp3").exists())
        self.assertTrue((self.radio / "a.mp3").exists())
        self.assertTrue((self.radio / "made-here.mp3").exists())
        self.assertEqual(sync.status()["obsolete_files"], 0)

    def test_a_damaged_clip_is_not_written(self):
        with zipfile.ZipFile(self.server / "two.zip", "w") as archive:
            archive.writestr("data/radio_audio/pieces/c.ogg", b"XX")
            archive.writestr("data/spotter_audio/d.mp3", b"d")
        sync = self.sync()
        sync.start_download()
        wait_until(lambda: sync.status()["job"]["state"] != "RUNNING")
        self.assertEqual(sync.status()["job"]["state"], "FAILED")
        self.assertFalse((self.radio / "pieces" / "c.ogg").exists())

    def test_a_copy_without_voice_lists_asks_for_the_newest_one(self):
        shutil.rmtree(self.root / "releases")
        sync = self.sync()
        sync.rescan(wait=True)
        self.assertEqual((sync.status()["version"], sync.status()["missing"]), ("1.4.0", 3))
        self.assertTrue(self.requested[0].endswith("/voice/voice-chunks-latest.json"))
        self.assertTrue((self.root / ".update" / "voice-chunks-1.4.0.json").is_file())

    def test_clips_of_the_wrong_size_count_as_missing_once_the_list_is_known(self):
        (self.spotter / "d.mp3").write_bytes(b"truncated")
        sync = self.sync()
        sync.rescan(wait=True)
        self.assertEqual(sync.status()["missing"], 2)
        sync.start_download()
        wait_until(lambda: sync.status()["job"]["state"] != "RUNNING")
        self.assertEqual((self.spotter / "d.mp3").read_bytes(), b"d")


if __name__ == "__main__":
    unittest.main()
