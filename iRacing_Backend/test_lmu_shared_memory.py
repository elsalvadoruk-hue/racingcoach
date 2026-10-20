import ctypes
import hashlib
import json
from pathlib import Path
import tempfile
import os
import subprocess
import unittest
from unittest.mock import patch

import lmu_shared_memory as sm


class FakeBridge:
    def __init__(self):
        self.opens = 0
        self.closed = []
        self.result = 1
        self.payload = {"session_id": "process:1", "vehicle_id": 7, "telemetry_et": 10,
                        "lap_invalidated": True, "events": ["SME_UPDATE_TELEMETRY"]}

    def lmu_open(self):
        self.opens += 1
        return 123

    def lmu_close(self, handle):
        self.closed.append(handle)

    def lmu_read(self, handle, timeout, buffer, size):
        buffer.value = json.dumps(self.payload).encode()
        return self.result

    def lmu_error(self):
        return b"GAME_EXITED"


class SharedMemoryBoundaryTests(unittest.TestCase):
    def source(self):
        source = sm.SharedMemorySource()
        fake = FakeBridge()
        source._dll = fake
        source._fingerprint = "fixture"
        self.addCleanup(source.close)
        return source, fake

    def test_construct_and_probe_do_not_open(self):
        source, fake = self.source()
        with patch.object(source, "_load", return_value={"pointer_size": 8}):
            self.assertFalse(source.probe()["attached"])
        self.assertEqual(fake.opens, 0)

    def test_explicit_open_idempotent_read_and_connection_epoch(self):
        source, fake = self.source()
        with patch.object(source, "_load", return_value={"pointer_size": 8}), patch.object(sm, "header_fingerprint", return_value="fixture"):
            source.open()
            source.open()
            first = source.read()
            self.assertEqual(first["vehicle_id"], 7)
            self.assertTrue(first["lap_invalidated"])
            self.assertEqual(fake.opens, 1)
            source.close()
            source.open()
            self.assertNotEqual(first["session_id"], source.read()["session_id"])

    def test_timeout_is_not_a_snapshot_and_failure_closes(self):
        source, fake = self.source()
        source._handle = 123
        with patch.object(sm, "header_fingerprint", return_value="fixture"):
            fake.result = 0
            self.assertIsNone(source.read())
            fake.result = -1
            with self.assertRaises(sm.LmuLiveUnavailable) as error:
                source.read()
        self.assertEqual(error.exception.code, "GAME_EXITED")
        self.assertEqual(fake.closed, [123])
        source.close()
        self.assertEqual(fake.closed, [123])

    def test_header_change_closes_before_native_read(self):
        source, fake = self.source()
        source._handle = 123
        with patch.object(sm, "header_fingerprint", return_value="changed"):
            with self.assertRaises(sm.LmuLiveUnavailable) as error:
                source.read()
        self.assertEqual(error.exception.code, "ABI_MISMATCH")
        self.assertEqual(fake.closed, [123])

    def test_missing_header_and_invalid_budget_and_not_open(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(sm.LmuLiveUnavailable) as error:
                sm.header_fingerprint(directory)
        self.assertEqual(error.exception.code, "SDK_UNAVAILABLE")
        source = sm.SharedMemorySource()
        for value in (True, -1, 1001, .5, "200"):
            with self.assertRaises(ValueError):
                source.read(value)
        with self.assertRaises(sm.LmuLiveUnavailable) as error:
            source.read()
        self.assertEqual(error.exception.code, "NOT_OPEN")

    def test_header_fingerprint_covers_all_headers_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in sm.HEADERS:
                (folder / name).write_bytes(b"fixture")
            original = sm.header_fingerprint(folder)
            (folder / sm.HEADERS[-1]).write_bytes(b"changed")
            self.assertNotEqual(original, sm.header_fingerprint(folder))

    def test_manifest_rejects_changed_source_or_library_without_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "lmu_native_bridge.dll").write_bytes(b"not-loaded")
            manifest = {"header_fingerprint": "fixture", "source_sha256": "wrong",
                        "dll_sha256": hashlib.sha256(b"not-loaded").hexdigest()}
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            source = sm.SharedMemorySource(build_dir=folder)
            with patch.object(sm, "_platform_check"), patch.object(sm, "header_fingerprint", return_value="fixture"), patch.object(ctypes, "CDLL") as load:
                with self.assertRaises(sm.LmuLiveUnavailable) as error:
                    source.probe()
            self.assertEqual(error.exception.code, "ABI_MISMATCH")
            load.assert_not_called()


@unittest.skipUnless(os.name == "nt" and sm.SDK_DIR.is_dir(), "설치된 Windows LMU SDK가 필요한 검증입니다.")
class NativeSharedMemoryFixtureTests(unittest.TestCase):
    def test_native_copy_lock_bounds_versions_and_cache(self):
        vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
        if not vswhere.is_file():
            self.skipTest("Visual Studio C++ 빌드 도구가 필요한 검증입니다.")
        install = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        if not install:
            self.skipTest("Visual Studio C++ 빌드 도구가 필요한 검증입니다.")
        folder = sm.BUILD_DIR / "fixture"
        folder.mkdir(parents=True, exist_ok=True)
        code = r'''
#include <iostream>
#include <stdexcept>
void require(bool condition,const char* name) {
    if(!condition)throw std::runtime_error(name);
    std::cout << name << '\n';
}
std::unique_ptr<SharedMemoryObjectOut> frame(bool tu=true,bool su=true) {
    auto p=std::make_unique<SharedMemoryObjectOut>();
    for(auto& e:p->generic.events)e=SME_MAX;
    p->generic.gameVersion=100;
    if(tu)p->generic.events[0]=SME_UPDATE_TELEMETRY;
    if(su)p->generic.events[1]=SME_UPDATE_SCORING;
    p->telemetry.activeVehicles=1;p->telemetry.playerHasVehicle=true;
    auto& t=p->telemetry.telemInfo[0];
    t.mID=77;t.mElapsedTime=25;t.mLapStartET=5;t.mLapInvalidated=true;
    t.mLocalVel.x=3;t.mLocalVel.y=4;t.mUnfilteredBrake=.7;
    p->scoring.scoringInfo.mNumVehicles=1;p->scoring.scoringInfo.mSession=1;
    p->scoring.scoringInfo.mCurrentET=24.9;p->scoring.scoringInfo.mInRealtime=true;
    auto& v=p->scoring.vehScoringInfo[0];
    v.mID=77;v.mIsPlayer=true;v.mTotalLaps=3;v.mLastLapTime=120;v.mCountLapFlag=2;
    return p;
}
int main() {
    try {
        auto s=std::make_unique<Source>();auto p=frame();bool tu=false,su=false;
        ExistingLockWords words{0,1};
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==0 && words.busy==1 && !s->telemetry,"LOCK_BUSY_NO_COPY");
        words.busy=0;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==1 && words.busy==0,"LOCK_COPY_UNLOCK");
        auto good=snapshot(s.get(),tu,su);
        require(good.find("\"total_laps\":3")!=std::string::npos && good.find("\"speed_mps\":5")!=std::string::npos,"ID_MATCH_INPUTS");
        p->telemetry.activeVehicles=105;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==-1 && words.busy==0 && error=="SHARED_LAYOUT_INVALID","TELEMETRY_BOUNDS_UNLOCK");
        p=frame();p->scoring.scoringInfo.mNumVehicles=105;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==-1 && words.busy==0,"SCORING_BOUNDS_UNLOCK");
        p=frame();p->scoring.scoringStreamSize=sizeof(p->scoring.scoringStream);
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==-1 && words.busy==0,"STREAM_BOUNDS_UNLOCK");
        p=frame();p->generic.gameVersion=0;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==-1 && error=="GAME_VERSION_INVALID","ZERO_VERSION_REJECT");
        p->generic.gameVersion=101;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==-1 && error=="GAME_VERSION_CHANGED" && words.busy==0,"VERSION_CHANGE_UNLOCK");
        p=frame(true,false);p->telemetry.playerHasVehicle=false;
        auto epoch=s->epoch;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==1 && !s->telemetry && !s->scoring && s->epoch>epoch,"ABSENCE_CLEARS_CACHE");
        p=frame(true,false);
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==1 && !s->scoring,"REUSED_ID_NO_OLD_SCORING");
        p=frame();copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        p=frame(true,false);p->telemetry.telemInfo[0].mID=88;epoch=s->epoch;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==1 && !s->scoring && s->epoch>epoch,"PLAYER_CHANGE_CLEARS_CACHE");
        p=frame();copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        p=frame(false,true);p->scoring.scoringInfo.mSession=2;epoch=s->epoch;
        require(copy_existing_frame(s.get(),*p,&words,nullptr,tu,su)==1 && !s->telemetry && s->epoch>epoch,"SESSION_CHANGE_CLEARS_TELEMETRY");
        p=frame();copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        p=frame(true,false);p->telemetry.telemInfo[0].mElapsedTime=40;
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        require(snapshot(s.get(),tu,su).find("\"total_laps\":null")!=std::string::npos,"STALE_SCORING_NOT_JOINED");
        p=frame();p->scoring.vehScoringInfo[0].mID=99;
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        require(snapshot(s.get(),tu,su).find("\"total_laps\":null")!=std::string::npos,"ID_MISMATCH_NOT_JOINED");
        p=frame();p->telemetry.playerVehicleIdx=2;
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        require(snapshot(s.get(),tu,su).find("\"vehicle_id\":null")!=std::string::npos,"BAD_PLAYER_INDEX_NO_POSITION");
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
'''
        source = folder / "native-fixture.cpp"
        source.write_text('#define LMU_HEADER_FINGERPRINT "fixture"\n#include "' + sm.BRIDGE_SOURCE.as_posix() + '"\n' + code, encoding="utf-8")
        vcvars = Path(install) / "VC/Auxiliary/Build/vcvars64.bat"
        executable = folder / "native-fixture.exe"
        script = folder / "build.cmd"
        script.write_text(f'@echo off\nset "PATH={vswhere.parent};%PATH%"\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\ncl /nologo /std:c++17 /EHsc /O2 /I"{sm.SDK_DIR}" /Fo"{folder / "native-fixture.obj"}" /Fe"{executable}" "{source}"\n', encoding="utf-8")
        result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=folder, capture_output=True, timeout=120)
        (folder / "build.log").write_bytes(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace"))
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = result.stdout.splitlines()
        self.assertEqual(len(cases), 15)
        (folder / "verification.json").write_text(json.dumps({"state": "PASS", "scope": "NATIVE_SYNTHETIC_NO_GAME_ATTACH", "cases": cases}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
