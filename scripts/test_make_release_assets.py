"""Release files for the in-app updater, made from temporary clips and a temporary repository."""
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import make_release_assets


class VoicePieceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "clips").mkdir()

    def clips(self, contents):
        for name, data in contents.items():
            (self.base / "clips" / name).write_bytes(data)
        return {f"data/radio_audio/{name}": hashlib.sha256(data).hexdigest() for name, data in contents.items()}

    def read(self, path):
        return (self.base / "clips" / path.rsplit("/", 1)[1]).read_bytes()

    def test_pieces_are_named_by_content_and_reused_by_the_next_version(self):
        first = self.clips({"a.mp3": b"a" * 60, "b.mp3": b"b" * 60, "c.mp3": b"c" * 60})
        summary = make_release_assets.make_voice("1.4.0", first, self.read, self.base / "v1", None, chunk_bytes=100)
        self.assertEqual((summary["new_clips"], len(summary["new_pieces"])), (3, 3))
        listing = json.loads((self.base / "v1" / "voice-chunks-1.4.0.json").read_text(encoding="utf-8"))
        self.assertEqual(listing, json.loads((self.base / "v1" / "voice-chunks-latest.json").read_text(encoding="utf-8")))
        for path, (sha, size, piece) in listing["files"].items():
            self.assertRegex(piece, r"^voice-1\.4\.0-[0-9a-f]{12}\.zip$")
            with zipfile.ZipFile(self.base / "v1" / piece) as archive:
                self.assertEqual(hashlib.sha256(archive.read(path)).hexdigest(), sha)
            self.assertEqual(listing["chunks"][piece]["sha256"][:12], piece[len("voice-1.4.0-"):-len(".zip")])

        second = {**first, **self.clips({"b.mp3": b"B" * 60, "d.mp3": b"d" * 10})}
        summary = make_release_assets.make_voice("1.5.0", second, self.read, self.base / "v2", listing, chunk_bytes=100)
        self.assertEqual((summary["new_clips"], summary["reused_clips"], len(summary["new_pieces"])), (2, 2, 1))
        updated = json.loads((self.base / "v2" / "voice-chunks-1.5.0.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["files"]["data/radio_audio/a.mp3"], listing["files"]["data/radio_audio/a.mp3"])
        self.assertEqual(updated["files"]["data/radio_audio/b.mp3"][2], updated["files"]["data/radio_audio/d.mp3"][2])
        self.assertEqual(set(updated["chunks"]), {entry[2] for entry in updated["files"].values()})

    def test_a_clip_that_differs_from_the_list_stops_the_build(self):
        files = self.clips({"a.mp3": b"a"})
        (self.base / "clips" / "a.mp3").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "a.mp3"):
            make_release_assets.make_voice("1.4.0", files, self.read, self.base / "out", None)


class AppAssetTests(unittest.TestCase):
    def test_app_zip_validates_and_includes_the_prebuilt_bridge(self):
        import lmu_shared_memory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backend = root / "iRacing_Backend"
            backend.mkdir()
            (backend / "backend_api.py").write_text('APP_VERSION = "2.0.1"\n', encoding="utf-8")
            (backend / "lmu_shared_memory.py").write_text("# bundle support\n", encoding="utf-8")
            source = lmu_shared_memory.bridge_source_bytes()
            (backend / "lmu_native_bridge.cpp").write_bytes(source)
            fingerprint = "a" * 64
            identity = hashlib.sha256(source + fingerprint.encode("ascii")).hexdigest()[:24]
            bundle = backend / "lmu_bridges" / ("bridge-" + identity)
            bundle.mkdir(parents=True)
            dll = bytearray(128)
            dll[:2] = b"MZ"
            struct.pack_into("<I", dll, 0x3c, 64)
            dll[64:68] = b"PE\0\0"
            struct.pack_into("<H", dll, 68, 0x8664)
            (bundle / "lmu_native_bridge.dll").write_bytes(dll)
            (bundle / "manifest.json").write_text(json.dumps({
                "header_fingerprint": fingerprint, "source_sha256": hashlib.sha256(source).hexdigest(),
                "dll_sha256": hashlib.sha256(dll).hexdigest(), "bridge_version": 1, "runtime": "STATIC_MSVC",
            }), encoding="utf-8")
            (root / "package.json").write_text('{"version":"2.0.1"}', encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "index.html").write_text("<html>", encoding="utf-8")
            run = lambda *args: subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            run("init", "-q")
            run("add", "iRacing_Backend", "package.json")
            run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x")
            with patch("launch_app.build_is_current", return_value=True):
                target = make_release_assets.make_app(root, root / "out")
                with zipfile.ZipFile(target) as archive:
                    member = "racingcoach-2.0.1/" + (bundle / "lmu_native_bridge.dll").relative_to(root).as_posix()
                    self.assertEqual(archive.read(member), dll)
                (bundle / "lmu_native_bridge.dll").write_bytes(b"damaged")
                with self.assertRaisesRegex(ValueError, "LMU"):
                    make_release_assets.make_app(root, root / "bad", allow_dirty=True)
                self.assertFalse((root / "bad").exists())

    def test_app_zip_holds_committed_files_and_the_built_screen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "iRacing_Backend").mkdir()
            (root / "iRacing_Backend" / "backend_api.py").write_text('APP_VERSION = "1.5.0"\n', encoding="utf-8")
            (root / "package.json").write_text('{"version": "1.5.0"}', encoding="utf-8")
            (root / "notes.txt").write_text("not committed", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "index.html").write_text("<html>", encoding="utf-8")
            run = lambda *args: subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            run("init", "-q")
            run("add", "iRacing_Backend/backend_api.py", "package.json")
            run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x")
            with patch("launch_app.build_is_current", return_value=True):
                target = make_release_assets.make_app(root, root / "out")
                self.assertEqual(target.name, "racingcoach-app-1.5.0.zip")
                with zipfile.ZipFile(target) as archive:
                    self.assertEqual(sorted(archive.namelist()), ["racingcoach-1.5.0/build/index.html",
                                                                  "racingcoach-1.5.0/iRacing_Backend/backend_api.py",
                                                                  "racingcoach-1.5.0/package.json"])
                (root / "package.json").write_text('{"version": "1.4.1"}', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "1.4.1"):
                    make_release_assets.make_app(root, root / "out")
                (root / "package.json").write_text('{"version": "1.5.0" }', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "커밋"):
                    make_release_assets.make_app(root, root / "out")


if __name__ == "__main__":
    unittest.main()
