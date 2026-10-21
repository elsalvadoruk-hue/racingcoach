"""The update patch: the program always, large parts only when new, the receiver's data never."""
import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import make_patch
import make_share_copy


def write(folder, files):
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def copy(version, **changes):
    files = {
        "iRacing_Backend/backend_api.py": f'APP_VERSION = "{version}"\n',
        "iRacing_Backend/requirements_core.txt": "fastapi\n",
        "실행.bat": "@echo off\n",
        "build/index.html": "<html>one</html>",
        "build/track-maps/official/1/active.svg": "<svg>spa</svg>",
        "data/radio_audio/aaa.mp3": b"old clip",
        "data/spotter_audio/bbb.mp3": b"left",
        "data/corner-definitions.json": '{"shipped": true}',
        "data/pit-knowledge.json": '{"shipped": true}',
    }
    files.update(changes)
    return {name: text for name, text in files.items() if text is not None}


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        releases = patch.object(make_patch, "RELEASES", self.root / "releases")
        releases.start()
        self.addCleanup(releases.stop)
        self.old = self.root / "old"
        write(self.old, copy("1.0.0"))
        self.assertIsNotNone(make_patch.record(self.old))

    def make(self, **changes):
        new = self.root / "new"
        write(new, copy("1.0.1", **changes))
        (new / "패치 노트.txt").write_text("■ 1.0.1 (테스트)\n\n테스트 변경\n", encoding="utf-8")
        out, printed = self.root / "patch.zip", io.StringIO()
        with patch.object(sys, "argv", ["make_patch.py", "--from", "1.0.0", "--new", str(new), "--out", str(out)]), redirect_stdout(printed):
            code = make_patch.main()
        return code, (zipfile.ZipFile(out) if out.exists() else None), printed.getvalue()

    def test_the_program_always_goes_in_and_large_parts_only_when_new(self):
        code, archive, _ = self.make(**{
            "build/index.html": "<html>two</html>",
            "build/track-maps/official/2/active.svg": "<svg>monza</svg>",       # a new track map
            "data/radio_audio/ccc.mp3": b"new clip",                             # a new wording
            "data/corner-definitions.json": '{"shipped": "newer"}',              # ours changed too: still not sent
        })
        self.assertEqual(code, 0)
        names = set(archive.namelist())
        self.assertEqual(names, {"업데이트 안내.txt", "패치 노트.txt", "iRacing_Backend/backend_api.py", "iRacing_Backend/requirements_core.txt", "실행.bat",
                                 "build/index.html", "build/track-maps/official/2/active.svg", "data/radio_audio/ccc.mp3"})
        self.assertEqual(archive.read("data/radio_audio/ccc.mp3"), b"new clip")
        note = archive.read("업데이트 안내.txt").decode("utf-8-sig")
        self.assertIn("1.0.0 → 1.0.1", note)
        self.assertIn("새로 추가된 음성 1개", note)
        self.assertIn("테스트 변경", note)
        self.assertNotIn("설치.bat을 한 번 더", note)

    def test_unzipping_it_over_a_used_copy_leaves_what_the_receiver_made(self):
        _, archive, _ = self.make(**{"data/radio_audio/ccc.mp3": b"new clip", "data/pit-knowledge.json": '{"shipped": "newer"}'})
        used = self.root / "used"
        write(used, copy("1.0.0", **{"data/corner-definitions.json": '{"mine": "confirmed turns"}', "data/pit-knowledge.json": '{"mine": "my stops"}',
                                    "data/settings.json": '{"telemetry_dirs": ["D:/laps"]}', "data/gcp-key.json": "{}", "data/iracing_tuner.sqlite3": b"records"}))
        archive.extractall(used)
        self.assertEqual(make_patch.version_of(used), "1.0.1")
        for name, kept in (("data/corner-definitions.json", '{"mine": "confirmed turns"}'), ("data/pit-knowledge.json", '{"mine": "my stops"}'),
                           ("data/settings.json", '{"telemetry_dirs": ["D:/laps"]}'), ("data/gcp-key.json", "{}"), ("data/iracing_tuner.sqlite3", "records")):
            self.assertEqual((used / name).read_text(encoding="utf-8"), kept, name)
        self.assertEqual((used / "data/radio_audio/aaa.mp3").read_bytes(), b"old clip")
        self.assertEqual((used / "data/radio_audio/ccc.mp3").read_bytes(), b"new clip")

    def test_changed_packages_and_removed_files_are_pointed_out(self):
        code, archive, printed = self.make(**{"iRacing_Backend/requirements_core.txt": "fastapi\nnewpackage\n", "실행.bat": None})
        self.assertEqual(code, 0)
        self.assertIn("설치.bat을 한 번 더 실행하세요", archive.read("업데이트 안내.txt").decode("utf-8-sig"))
        self.assertIn("없어진 파일 1개", printed)

    def test_the_same_version_or_an_unknown_one_is_refused(self):
        same = self.root / "same"
        write(same, copy("1.0.0"))
        for arguments in (["--from", "1.0.0", "--new", str(same)], ["--from", "0.9.0", "--new", str(same)]):
            with patch.object(sys, "argv", ["make_patch.py", *arguments, "--out", str(self.root / "no.zip")]), redirect_stdout(io.StringIO()):
                self.assertEqual(make_patch.main(), 1)
        self.assertFalse((self.root / "no.zip").exists())
        # The first record of a version is what people received: a later copy of the same version does not replace it.
        self.assertIsNone(make_patch.record(same))

    def test_a_new_version_without_a_patch_note_block_is_refused(self):
        new = self.root / "new-without-notes"
        write(new, copy("1.0.1"))
        (new / "패치 노트.txt").write_text("■ 1.0.0\n\n옛 변경\n", encoding="utf-8")
        out = self.root / "missing-notes.zip"
        with patch.object(sys, "argv", ["make_patch.py", "--from", "1.0.0", "--new", str(new), "--out", str(out)]), redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(make_patch.main(), 1)
        self.assertIn("패치 노트.txt에 1.0.1 블록이 없습니다", printed.getvalue())
        self.assertFalse(out.exists())


class ShareManifestTests(unittest.TestCase):
    def test_backend_share_holds_bridge_source_and_only_verified_bundle_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root, out = Path(folder) / "source", Path(folder) / "share"
            bundle = root / "iRacing_Backend" / "lmu_bridges" / ("bridge-" + "a" * 24)
            relative = bundle.relative_to(root).as_posix()
            write(root, {
                "iRacing_Backend/lmu_native_bridge.cpp": "// redistributable source",
                "iRacing_Backend/lmu_shared_memory.py": "# transport",
                "iRacing_Backend/gcp-key.json": "private key",
                relative + "/manifest.json": "{}",
                relative + "/lmu_native_bridge.dll": b"MZprebuilt",
                "logs/lmu-native/build.log": "private compiler paths",
            })
            with patch("lmu_shared_memory.validate_bundled_bridges", return_value=[bundle]):
                make_share_copy.copy_backend(root, out)
            self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()), sorted([
                "iRacing_Backend/lmu_native_bridge.cpp", "iRacing_Backend/lmu_shared_memory.py",
                relative + "/manifest.json", relative + "/lmu_native_bridge.dll",
            ]))
            with patch("lmu_shared_memory.validate_bundled_bridges", side_effect=ValueError("invalid bundle")):
                with self.assertRaisesRegex(ValueError, "invalid bundle"):
                    make_share_copy.copy_backend(root, Path(folder) / "invalid")
            self.assertFalse((Path(folder) / "invalid").exists())

    def test_a_new_overlay_import_cannot_be_left_out_of_the_share(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in make_share_copy.REQUIRED_OVERLAY_SCRIPTS:
                write(root, {"scripts/" + name: ""})
            write(root, {"scripts/overlay_sample.py": "from overlay_future import FutureRenderer\n",
                         "scripts/overlay_future.py": "class FutureRenderer: pass\n",
                         "iRacing_Backend/requirements_core.txt": "Pillow>=11,<13\n"})
            with patch.object(make_share_copy, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "overlay_future.py"):
                    make_share_copy.validate_share_inputs()

    def test_overlay_runtime_scripts_are_in_the_share_allowlist(self):
        make_share_copy.validate_share_inputs()
        expected = {"overlay_sample.py", "overlay_trace.py", "overlay_celebration.py", "overlay_demo_data.py"}
        self.assertTrue(expected.issubset(set(make_share_copy.SCRIPTS)))
        for name in expected:
            self.assertTrue((make_share_copy.ROOT / "scripts" / name).is_file(), name)

    def test_overlay_dependency_is_installed_by_the_normal_installer(self):
        requirements = (make_share_copy.ROOT / "iRacing_Backend" / "requirements_core.txt").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.strip().lower().startswith("pillow") for line in requirements))


    def test_the_python_picker_the_bat_files_call_is_in_the_share(self):
        self.assertIn("find_python.bat", make_share_copy.root_bat_scripts())
        self.assertIn("find_python.bat", make_share_copy.SCRIPTS)
        installer = (make_share_copy.ROOT / "설치.bat").read_text(encoding="utf-8")
        self.assertIn("py install 3.12", installer)

    def test_a_script_a_bat_file_calls_cannot_be_left_out_of_the_share(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root, {"실행.bat": 'call "scripts\\pick_future.bat"\r\n', "scripts/pick_future.bat": "@echo off\r\n"})
            with patch.object(make_share_copy, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "pick_future.bat"):
                    make_share_copy.validate_share_inputs()


    def test_the_full_installer_zip_holds_the_folder_and_stores_clips(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "iracing-coach-9.9.9"
            write(out, {"실행.bat": "@echo off", "data/radio_audio/a.mp3": b"ID3clip", "README.md": "x" * 2000})
            archive = make_share_copy.zip_share(out)
            self.assertEqual(archive.name, "iracing-coach-9.9.9.zip")
            with zipfile.ZipFile(archive) as opened:
                self.assertEqual(sorted(opened.namelist()), ["iracing-coach-9.9.9/README.md", "iracing-coach-9.9.9/data/radio_audio/a.mp3",
                                                             "iracing-coach-9.9.9/실행.bat"])
                self.assertEqual(opened.getinfo("iracing-coach-9.9.9/data/radio_audio/a.mp3").compress_type, zipfile.ZIP_STORED)
                self.assertEqual(opened.read("iracing-coach-9.9.9/data/radio_audio/a.mp3"), b"ID3clip")
            self.assertFalse(archive.with_name(archive.name + ".part").exists())

if __name__ == "__main__":
    unittest.main()
