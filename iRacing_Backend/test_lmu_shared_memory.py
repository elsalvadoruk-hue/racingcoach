import ctypes
import hashlib
import json
from pathlib import Path
import tempfile
import os
import subprocess
from types import SimpleNamespace
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
    def test_default_artifact_changes_with_source_or_headers_and_explicit_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sdk = folder / 'sdk'
            sdk.mkdir()
            for name in sm.HEADERS:
                (sdk / name).write_bytes(b'headers')
            source_file = folder / 'bridge.cpp'
            source_file.write_bytes(b'first source')
            with patch.object(sm, 'BRIDGE_SOURCE', source_file), patch.object(sm, 'BUILD_DIR', folder / 'artifacts'):
                original = sm.SharedMemorySource(sdk_dir=sdk).build_dir
                self.assertEqual(original, sm.SharedMemorySource(sdk_dir=sdk).build_dir)
                source_file.write_bytes(b'second source')
                changed_source = sm.SharedMemorySource(sdk_dir=sdk).build_dir
                self.assertNotEqual(original, changed_source)
                (sdk / sm.HEADERS[0]).write_bytes(b'new ABI')
                self.assertNotEqual(changed_source, sm.SharedMemorySource(sdk_dir=sdk).build_dir)
                explicit = folder / 'manual'
                self.assertEqual(sm.SharedMemorySource(sdk_dir=sdk, build_dir=explicit).build_dir, explicit.resolve())
                self.assertFalse((folder / 'artifacts').exists())

    def test_default_rebuild_reuses_verified_artifact_without_invoking_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sdk = folder / 'sdk'
            sdk.mkdir()
            for name in sm.HEADERS:
                (sdk / name).write_bytes(b'headers')
            with patch.object(sm, 'BUILD_DIR', folder / 'artifacts'):
                artifact = sm.SharedMemorySource(sdk_dir=sdk).build_dir
                artifact.mkdir(parents=True)
                (artifact / 'manifest.json').write_text('{}')
                with patch.object(sm, '_platform_check'), patch.object(sm.subprocess, 'run') as compiler, \
                        patch.object(sm.SharedMemorySource, 'probe', return_value={'state': 'READY'}) as probe:
                    self.assertEqual(sm.build_native_bridge(sdk_dir=sdk), {'state': 'READY'})
                    compiler.assert_not_called()
                    probe.assert_called_once()

    def test_sdk_resolution_reads_environment_at_call_time_and_explicit_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            env_folder, explicit_folder = Path(directory) / "other Steam SDK", Path(directory) / "explicit SDK"
            with patch.dict(os.environ, {"LMU_SDK_DIR": str(env_folder)}):
                self.assertEqual(sm.resolve_sdk_dir(), env_folder.resolve())
                source = sm.SharedMemorySource()
                self.assertEqual(source.configuration()["source"], "ENVIRONMENT")
                self.assertEqual(sm.SharedMemorySource(sdk_dir=explicit_folder).sdk_dir, explicit_folder.resolve())
                os.environ["LMU_SDK_DIR"] = str(explicit_folder)
                self.assertEqual(sm.SharedMemorySource().sdk_dir, explicit_folder.resolve())
                self.assertEqual(source.sdk_dir, env_folder.resolve())
                self.assertEqual(source.configuration()["source"], "ENVIRONMENT")
            with patch.dict(os.environ, {"LMU_SDK_DIR": "  "}):
                self.assertEqual(sm.resolve_sdk_dir(), sm.SDK_DIR.resolve())
                self.assertEqual(sm.sdk_configuration()["source"], "DEFAULT")

    def test_environment_headers_diagnostics_fingerprint_and_probe_without_attach(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LMU_SDK_DIR": directory}):
            folder = Path(directory)
            self.assertEqual(sm.sdk_configuration(), {"source": "ENVIRONMENT", "headers_available": False,
                                                      "missing_headers": list(sm.HEADERS)})
            for name in sm.HEADERS:
                (folder / name).write_bytes(b"fixture")
            self.assertEqual(sm.header_fingerprint(), sm.header_fingerprint(folder))
            source = sm.SharedMemorySource()
            with patch.object(source, "_load", return_value={"pointer_size": 8}), patch.object(ctypes, "CDLL") as load:
                result = source.probe()
            self.assertEqual(result["sdk_dir"], str(folder.resolve()))
            self.assertEqual(result["sdk_configuration"], {"source": "ENVIRONMENT", "headers_available": True,
                                                          "missing_headers": []})
            self.assertFalse(result["attached"])
            self.assertIsNone(source._handle)
            load.assert_not_called()
            (folder / sm.HEADERS[-1]).unlink()
            self.assertEqual(source.configuration()["missing_headers"], [sm.HEADERS[-1]])
            with self.assertRaises(sm.LmuLiveUnavailable) as error:
                sm.header_fingerprint()
            self.assertEqual(error.exception.code, "SDK_UNAVAILABLE")

    def test_build_uses_same_selected_sdk_for_headers_compiler_and_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            env_sdk, explicit_sdk = folder / "other Steam SDK", folder / "explicit SDK"
            vcvars = folder / "VS/VC/Auxiliary/Build/vcvars64.bat"
            vcvars.parent.mkdir(parents=True)
            vcvars.write_bytes(b"fixture")
            for sdk in (env_sdk, explicit_sdk):
                sdk.mkdir()
                for name in sm.HEADERS:
                    (sdk / name).write_bytes(str(sdk).encode())
            for argument, expected, label in ((None, env_sdk, "ENVIRONMENT"),
                                               (explicit_sdk, explicit_sdk, "EXPLICIT")):
                with self.subTest(argument=argument):
                    build_dir = folder / label

                    def run(command, **kwargs):
                        if command[0] == "cmd.exe":
                            (build_dir / "lmu_native_bridge.dll").write_bytes(b"fixture DLL")
                            return SimpleNamespace(returncode=0, stdout=b"built", stderr=b"")
                        return SimpleNamespace(stdout=str(folder / "VS"))

                    with patch.dict(os.environ, {"LMU_SDK_DIR": str(env_sdk)}), patch.object(sm, "_platform_check"), \
                            patch.object(sm.subprocess, "run", side_effect=run), \
                            patch.object(sm.SharedMemorySource, "probe", autospec=True,
                                         side_effect=lambda source: {"sdk_dir": str(source.sdk_dir),
                                                                     "sdk_configuration": source.configuration()}) as probe:
                        result = sm.build_native_bridge(sdk_dir=argument, build_dir=build_dir)
                    self.assertEqual(result["sdk_dir"], str(expected.resolve()))
                    self.assertEqual(result["sdk_configuration"]["source"], label)
                    self.assertIn('/I"' + str(expected.resolve()) + '"', (build_dir / "build.cmd").read_text())
                    manifest = json.loads((build_dir / "manifest.json").read_text())
                    self.assertEqual(manifest["header_fingerprint"], sm.header_fingerprint(expected))
                    probe.assert_called_once()

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

    def test_bounded_full_grid_payload_exceeding_old_buffer_is_preserved(self):
        source, fake = self.source()
        source._handle = 123
        source._connection_id = 'fixture-connection'
        fake.payload['traffic'] = {
            'status': 'AVAILABLE', 'source': 'TELEMETRY', 'complete': True,
            'player': {'vehicle_id': 7, 'telemetry_et': 10,
                       'position_m': [1234.567891234, 12.12345678, -9876.54321987],
                       'orientation': [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
            'vehicles': [{'vehicle_id': i + 100, 'telemetry_et': 9.999999999999,
                          'position_m': [-123456789.12345679, -987654321.9876543, 123456789.98765433],
                          'in_pits': False, 'control_code': 2} for i in range(103)]}
        self.assertGreater(len(json.dumps(fake.payload).encode()), 16384)
        with patch.object(sm, 'header_fingerprint', return_value='fixture'):
            self.assertEqual(source.read()['traffic'], fake.payload['traffic'])
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


@unittest.skipUnless(os.name == "nt", "설치된 Windows LMU SDK가 필요한 검증입니다.")
class NativeSharedMemoryFixtureTests(unittest.TestCase):
    def test_native_copy_lock_bounds_versions_and_cache(self):
        sdk_dir = sm.resolve_sdk_dir()
        if not sm.sdk_configuration()["headers_available"]:
            self.skipTest("설치된 Windows LMU SDK가 필요한 검증입니다.")
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
#include <limits>
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
    t.mGear=4;t.mEngineRPM=6500;
    t.mFuel=25.5;t.mFuelCapacity=100;
    t.mEngineWaterTemp=0;t.mEngineOilTemp=-20;t.mUnfilteredClutch=0;
    t.mABSActive=false;t.mTCActive=true;t.mSpeedLimiterActive=true;
    strcpy_s(t.mVehicleName,"Example GT3 #32");strcpy_s(t.mTrackName,"Monza");
    strcpy_s(t.mVehicleModel,"Example GT3");
    t.mOverheating=true;t.mDetached=false;t.mLastImpactET=12;t.mLastImpactMagnitude=250;
    t.mLocalRot.y=-.13;t.mPhysicalSteeringWheelRange=540;t.mUnfilteredSteering=.5;
    t.mRearBrakeBias=.4875;t.mTCMax=10;t.mTC=7;t.mABSMax=10;t.mABS=6;
    strcpy_s(t.mFrontTireCompoundName,"Wet");strcpy_s(t.mRearTireCompoundName,"Medium");
    for(unsigned wheel=0;wheel<4;++wheel) {
        t.mWheel[wheel].mPressure=wheel==0?0:170+wheel;
        t.mWheel[wheel].mBrakeTemp=wheel==0?0:300+wheel;
        t.mWheel[wheel].mSuspensionDeflection=-.03+.02*wheel;
        t.mWheel[wheel].mRideHeight=-.01+.02*wheel;
        t.mWheel[wheel].mWear=wheel==0?0:wheel==1?1:.5;
        t.mWheel[wheel].mFlat=wheel==1;t.mWheel[wheel].mDetached=wheel==2;
        for(unsigned point=0;point<3;++point)t.mWheel[wheel].mTemperature[point]=273.15+10*wheel+point;
    }
    p->scoring.scoringInfo.mNumVehicles=3;p->scoring.scoringInfo.mSession=1;
    p->scoring.scoringInfo.mCurrentET=24.9;p->scoring.scoringInfo.mInRealtime=true;
    p->scoring.scoringInfo.mLapDist=5793;p->scoring.scoringInfo.mSessionTimeRemaining=123;
    p->scoring.scoringInfo.mEndET=3600;
    p->scoring.scoringInfo.mGamePhase=5;p->scoring.scoringInfo.mMaxLaps=2147483647;
    p->scoring.scoringInfo.mSectorFlag[0]=0;
    p->scoring.scoringInfo.mSectorFlag[1]=1;
    p->scoring.scoringInfo.mSectorFlag[2]=-1;
    p->scoring.scoringInfo.mRaining=.25;p->scoring.scoringInfo.mAvgPathWetness=.5;
    p->scoring.scoringInfo.mAmbientTemp=22;p->scoring.scoringInfo.mTrackTemp=30;
    auto& v=p->scoring.vehScoringInfo[0];
    v.mID=77;v.mIsPlayer=true;v.mTotalLaps=3;v.mLastLapTime=120;v.mCountLapFlag=2;
    v.mBestLapTime=117.035;v.mNumPenalties=2;
    v.mLapDist=1234;v.mPlace=2;v.mFlag=6;v.mPitState=4;v.mControl=0;
    v.mSector=2;
    strcpy_s(v.mVehicleClass,"GT3");
    p->scoring.vehScoringInfo[1].mID=78;p->scoring.vehScoringInfo[1].mPlace=1;
    strcpy_s(p->scoring.vehScoringInfo[1].mVehicleClass,"GT3");
    p->scoring.vehScoringInfo[2].mID=79;p->scoring.vehScoringInfo[2].mPlace=3;
    strcpy_s(p->scoring.vehScoringInfo[2].mVehicleClass,"LMP2");
    return p;
}
std::unique_ptr<SharedMemoryObjectOut> traffic_frame() {
    auto p=frame();p->telemetry.activeVehicles=3;
    auto& own=p->telemetry.telemInfo[0];
    own.mPos.x=100.125;own.mPos.y=2.5;own.mPos.z=-4.25;
    own.mOri[0].z=-1;own.mOri[1].y=1;own.mOri[2].x=1;
    // Deliberately different array order from scoring: only IDs may join.
    for(unsigned i=1;i<3;++i) {
        auto& t=p->telemetry.telemInfo[i];t.mID=i==1?79:78;t.mElapsedTime=24.875;
        t.mPos.x=100+i;t.mPos.y=2.5+i;t.mPos.z=-4.25-i;
        p->scoring.vehScoringInfo[i].mControl=i==1?1:2;
    }
    return p;
}
std::string traffic_fixture(SharedMemoryObjectOut& p) {
    auto s=std::make_unique<Source>();bool tu=false,su=false;
    if(!validate_and_copy(s.get(),p,tu,su))throw std::runtime_error("TRAFFIC_COPY_FAILED");
    std::ostringstream o;traffic_json(o,s.get(),&s->copy.telemetry.telemInfo[0]);return o.str();
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
        std::cout<<"MEASURED_VALID:"<<good<<'\n';
        require(good.find("\"gear\":4")!=std::string::npos && good.find("\"engine_rpm\":6500")!=std::string::npos,"MEASURED_ENGINE_INPUTS");
        auto& measured=p->telemetry.telemInfo[0];
        measured.mGear=-2;measured.mEngineRPM=-1;
        measured.mEngineWaterTemp=-273.15;measured.mEngineOilTemp=std::numeric_limits<double>::quiet_NaN();
        measured.mUnfilteredClutch=1.01;
        measured.mPhysicalSteeringWheelRange=0;measured.mTCMax=0;measured.mABSMax=0;
        strcpy_s(measured.mFrontTireCompoundName,"Unknown");
        measured.mFuel=-1;measured.mFuelCapacity=0;
        p->scoring.scoringInfo.mSectorFlag[0]=8;
        p->scoring.vehScoringInfo[0].mSector=-1;
        measured.mWheel[0].mPressure=-1;
        measured.mWheel[0].mBrakeTemp=-273.15;
        measured.mWheel[0].mSuspensionDeflection=std::numeric_limits<double>::quiet_NaN();
        measured.mWheel[0].mRideHeight=std::numeric_limits<double>::infinity();
        measured.mWheel[0].mWear=-.01;
        measured.mWheel[0].mTemperature[0]=0;
        measured.mWheel[0].mTemperature[1]=-1;
        measured.mWheel[0].mTemperature[2]=std::numeric_limits<double>::quiet_NaN();
        measured.mWheel[1].mPressure=std::numeric_limits<double>::infinity();
        measured.mWheel[1].mBrakeTemp=std::numeric_limits<double>::infinity();
        measured.mWheel[1].mWear=1.01;
        measured.mWheel[1].mTemperature[2]=std::numeric_limits<double>::infinity();
        measured.mWheel[2].mPressure=std::numeric_limits<double>::quiet_NaN();
        measured.mWheel[2].mBrakeTemp=std::numeric_limits<double>::quiet_NaN();
        measured.mWheel[2].mWear=std::numeric_limits<double>::quiet_NaN();
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        auto invalid=snapshot(s.get(),tu,su);
        std::cout<<"MEASURED_INVALID:"<<invalid<<'\n';
        p->scoring.vehScoringInfo[1].mPlace=2;
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        require(snapshot(s.get(),tu,su).find("\"class_position\":null")!=std::string::npos,"DUPLICATE_CLASS_PLACE_REJECT");
        require(invalid.find("\"gear\":null")!=std::string::npos && invalid.find("\"engine_rpm\":null")!=std::string::npos,"MEASURED_INVALID_VALUES_NULL");
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
        auto stale=snapshot(s.get(),tu,su);
        require(stale.find("\"total_laps\":null")!=std::string::npos && stale.find("\"overall_position\":null")!=std::string::npos && stale.find("\"session_remaining_s\":null")!=std::string::npos && stale.find("\"track_length_m\":null")!=std::string::npos,"STALE_SCORING_NOT_JOINED");
        std::cout<<"MEASURED_STALE_SCORING:"<<stale<<'\n';
        p=frame();p->scoring.vehScoringInfo[0].mID=99;
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        require(snapshot(s.get(),tu,su).find("\"total_laps\":null")!=std::string::npos,"ID_MISMATCH_NOT_JOINED");
        p=frame();p->telemetry.playerVehicleIdx=2;
        copy_existing_frame(s.get(),*p,&words,nullptr,tu,su);
        require(snapshot(s.get(),tu,su).find("\"vehicle_id\":null")!=std::string::npos,"BAD_PLAYER_INDEX_NO_POSITION");
        std::cout<<"MEASURED_ABSENT:"<<snapshot(s.get(),tu,su)<<'\n';
        auto traffic=traffic_frame();
        std::cout<<"TRAFFIC_VALID:"<<traffic_fixture(*traffic)<<'\n';
        traffic->telemetry.telemInfo[2].mElapsedTime=24.75;
        std::cout<<"TRAFFIC_BOUNDARY:"<<traffic_fixture(*traffic)<<'\n';
        traffic->telemetry.telemInfo[2].mElapsedTime=24.749999;
        std::cout<<"TRAFFIC_STALE_OPPONENT:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.activeVehicles=2;
        std::cout<<"TRAFFIC_MISSING_OPPONENT:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.telemInfo[2].mID=79;
        std::cout<<"TRAFFIC_DUPLICATE_TELEMETRY:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.vehScoringInfo[2].mID=78;
        std::cout<<"TRAFFIC_DUPLICATE_SCORING:"<<traffic_fixture(*traffic)<<'\n';
        // Scoring metadata updates independently, about 0.6 s apart in the
        // recorded race. Opponent positions remain bounded to 0.25 s below.
        traffic=traffic_frame();traffic->scoring.scoringInfo.mCurrentET=24.4;
        std::cout<<"TRAFFIC_SCORING_REGULAR_INTERVAL:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.scoringInfo.mCurrentET=23;
        std::cout<<"TRAFFIC_SCORING_PAST_BOUNDARY:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.scoringInfo.mCurrentET=25.25;
        std::cout<<"TRAFFIC_SCORING_FUTURE_BOUNDARY:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.scoringInfo.mCurrentET=22.999999;
        std::cout<<"TRAFFIC_STALE_SCORING:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.scoringInfo.mCurrentET=25.250001;
        std::cout<<"TRAFFIC_FUTURE_SCORING:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.telemInfo[0].mOri[2].x=-1;
        std::cout<<"TRAFFIC_REFLECTED_ORIENTATION:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.telemInfo[0].mPos.y=std::numeric_limits<double>::quiet_NaN();
        std::cout<<"TRAFFIC_INVALID_PLAYER:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.telemInfo[2].mPos.z=std::numeric_limits<double>::infinity();
        std::cout<<"TRAFFIC_INVALID_OPPONENT:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.vehScoringInfo[1].mInPits=true;traffic->scoring.vehScoringInfo[2].mInPits=true;
        std::cout<<"TRAFFIC_ALL_PITS:"<<traffic_fixture(*traffic)<<'\n';
        traffic->telemetry.activeVehicles=2;
        std::cout<<"TRAFFIC_MISSING_PIT_TELEMETRY:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.activeVehicles=2;
        traffic->scoring.vehScoringInfo[1].mInPits=true;
        std::cout<<"TRAFFIC_PIT_WITH_LIVE_OPPONENT:"<<traffic_fixture(*traffic)<<'\n';
        traffic->scoring.vehScoringInfo[1].mInPits=false;
        std::cout<<"TRAFFIC_PIT_EXIT_WITHOUT_TELEMETRY:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.vehScoringInfo[1].mControl=-1;
        std::cout<<"TRAFFIC_UNKNOWN_CONTROL:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->scoring.vehScoringInfo[0].mIsPlayer=false;
        std::cout<<"TRAFFIC_NO_SCORED_PLAYER:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.activeVehicles=4;traffic->telemetry.telemInfo[3].mID=80;
        std::cout<<"TRAFFIC_JOINING_VEHICLE:"<<traffic_fixture(*traffic)<<'\n';
        traffic=traffic_frame();traffic->telemetry.activeVehicles=104;traffic->scoring.scoringInfo.mNumVehicles=104;
        for(unsigned i=1;i<104;++i) {
            auto& t=traffic->telemetry.telemInfo[i];t.mID=100+i;t.mElapsedTime=24.912345678912345;
            t.mPos.x=-123456789.12345679;t.mPos.y=-987654321.9876543;t.mPos.z=123456789.98765433;
            auto& v=traffic->scoring.vehScoringInfo[i];v.mID=t.mID;v.mControl=2;v.mInPits=false;
        }
        const auto fullGrid=traffic_fixture(*traffic);
        require(fullGrid.size()<262144,"TRAFFIC_FULL_GRID_BUFFER_FITS");
        std::cout<<"TRAFFIC_FULL_GRID:"<<fullGrid<<'\n';
        auto fullSource=std::make_unique<Source>();
        validate_and_copy(fullSource.get(),*traffic,tu,su);
        const auto fullSnapshot=snapshot(fullSource.get(),tu,su);
        if(fullSnapshot.size()+1>262144)throw std::runtime_error("FULL_SNAPSHOT_BUFFER_OVERFLOW");
        std::cout<<"MEASURED_FULL_GRID:"<<fullSnapshot<<'\n';
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
'''
        source = folder / "native-fixture.cpp"
        source.write_text('#define LMU_HEADER_FINGERPRINT "fixture"\n#include "' + sm.BRIDGE_SOURCE.as_posix() + '"\n' + code, encoding="utf-8")
        vcvars = Path(install) / "VC/Auxiliary/Build/vcvars64.bat"
        executable = folder / "native-fixture.exe"
        script = folder / "build.cmd"
        script.write_text(f'@echo off\nset "PATH={vswhere.parent};%PATH%"\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\ncl /nologo /std:c++17 /EHsc /O2 /I"{sdk_dir}" /Fo"{folder / "native-fixture.obj"}" /Fe"{executable}" "{source}"\n', encoding="utf-8")
        result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=folder, capture_output=True, timeout=120)
        (folder / "build.log").write_bytes(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace"))
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = [line for line in result.stdout.splitlines() if ':' not in line]
        self.assertEqual(len(cases), 19)
        traffic = {line.split(':', 1)[0]: json.loads(line.split(':', 1)[1])
                   for line in result.stdout.splitlines() if line.startswith('TRAFFIC_') and ':' in line}
        self.assertEqual(len(traffic), 22)
        good = traffic['TRAFFIC_VALID']
        self.assertEqual(good['status'], 'AVAILABLE')
        self.assertTrue(good['complete'])
        self.assertEqual(good['source'], 'TELEMETRY')
        self.assertEqual(good['player'], {'vehicle_id': 77, 'telemetry_et': 25,
                                         'position_m': [100.125, 2.5, -4.25],
                                         'orientation': [[0, 0, -1], [0, 1, 0], [1, 0, 0]]})
        self.assertEqual(good['vehicles'], [
            {'vehicle_id': 78, 'telemetry_et': 24.875, 'position_m': [102, 4.5, -6.25],
             'in_pits': False, 'control_code': 1},
            {'vehicle_id': 79, 'telemetry_et': 24.875, 'position_m': [101, 3.5, -5.25],
             'in_pits': False, 'control_code': 2}])
        self.assertTrue(traffic['TRAFFIC_BOUNDARY']['complete'])
        for key in ('REGULAR_INTERVAL', 'PAST_BOUNDARY', 'FUTURE_BOUNDARY'):
            with self.subTest(case=key):
                self.assertEqual(traffic['TRAFFIC_SCORING_' + key]['status'], 'AVAILABLE')
                self.assertTrue(traffic['TRAFFIC_SCORING_' + key]['complete'])
        for key in ('STALE_OPPONENT', 'MISSING_OPPONENT', 'INVALID_OPPONENT', 'UNKNOWN_CONTROL'):
            with self.subTest(case=key):
                row = traffic['TRAFFIC_' + key]
                self.assertEqual(row['status'], 'AVAILABLE')
                self.assertFalse(row['complete'])
                self.assertEqual([v['vehicle_id'] for v in row['vehicles']], [79])
        for key in ('DUPLICATE_TELEMETRY', 'DUPLICATE_SCORING', 'STALE_SCORING', 'FUTURE_SCORING',
                    'REFLECTED_ORIENTATION', 'INVALID_PLAYER', 'NO_SCORED_PLAYER'):
            with self.subTest(case=key):
                self.assertEqual(traffic['TRAFFIC_' + key], {'status': 'UNAVAILABLE', 'player': None,
                                                            'vehicles': [], 'source': 'TELEMETRY', 'complete': False})
        self.assertTrue(traffic['TRAFFIC_ALL_PITS']['complete'])
        self.assertEqual(traffic['TRAFFIC_ALL_PITS']['vehicles'], [])
        self.assertTrue(traffic['TRAFFIC_MISSING_PIT_TELEMETRY']['complete'])
        self.assertEqual(traffic['TRAFFIC_MISSING_PIT_TELEMETRY']['vehicles'], [])
        self.assertTrue(traffic['TRAFFIC_PIT_WITH_LIVE_OPPONENT']['complete'])
        self.assertEqual([v['vehicle_id'] for v in traffic['TRAFFIC_PIT_WITH_LIVE_OPPONENT']['vehicles']], [79])
        self.assertFalse(traffic['TRAFFIC_PIT_EXIT_WITHOUT_TELEMETRY']['complete'])
        self.assertFalse(traffic['TRAFFIC_JOINING_VEHICLE']['complete'])
        self.assertTrue(traffic['TRAFFIC_FULL_GRID']['complete'])
        self.assertEqual(len(traffic['TRAFFIC_FULL_GRID']['vehicles']), 103)
        measured = {line.split(':', 1)[0]: json.loads(line.split(':', 1)[1])
                    for line in result.stdout.splitlines() if line.startswith('MEASURED_') and ':' in line}
        valid = measured['MEASURED_VALID']
        self.assertEqual((valid['car_name'], valid['track_name'], valid['vehicle_model']),
                         ('Example GT3 #32', 'Monza', 'Example GT3'))
        self.assertIs(valid['overheating'], True)
        self.assertIs(valid['parts_detached'], False)
        self.assertEqual((valid['last_impact_et'], valid['last_impact_magnitude']), (12, 250))
        self.assertAlmostEqual(valid['steering_rad'], 2.356194490192345)
        self.assertAlmostEqual(valid['brake_bias'], 51.25)
        self.assertAlmostEqual(valid['yaw_rate'], -.13)
        self.assertEqual((valid['tc_setting'], valid['abs_setting']), (7, 6))
        self.assertIs(valid['front_tyre_wet'], True)
        self.assertIs(valid['rear_tyre_wet'], False)
        self.assertEqual(len(valid['scoring_cars']), 3)
        self.assertEqual(valid['scoring_cars'][0]['class_id'], valid['class_id'])
        self.assertEqual(len(measured['MEASURED_FULL_GRID']['scoring_cars']), 104)
        self.assertEqual((valid['fuel_l'], valid['fuel_capacity_l']), (25.5, 100))
        self.assertEqual((valid['lap_distance_m'], valid['track_length_m']), (1234, 5793))
        self.assertEqual((valid['session_remaining_s'], valid['session_phase'], valid['session_type_code']), (123, 5, 1))
        self.assertEqual((valid['best_lap_time'],valid['outstanding_penalties'],valid['session_end_et']), (117.035,2,3600))
        self.assertIsNone(valid['session_max_laps'])
        self.assertEqual((valid['overall_position'], valid['class_position'], valid['class_vehicle_count']), (2, 2, 2))
        self.assertEqual((valid['primary_flag'], valid['pit_state'], valid['control_code']), (6, 4, 0))
        self.assertEqual(valid['sector_flags'], [0, 1, -1])
        self.assertEqual(valid['sector'], 2)
        self.assertEqual((valid['rain_ratio'], valid['track_wetness_ratio'], valid['ambient_temp_c'], valid['track_temp_c']), (.25, .5, 22, 30))
        self.assertEqual((valid['gear'], valid['engine_rpm']), (4, 6500))
        self.assertEqual((valid['engine_water_temp_c'], valid['engine_oil_temp_c'], valid['clutch_ratio']), (0, -20, 0))
        self.assertIs(valid['abs_active'], False)
        self.assertIs(valid['tc_active'], True)
        self.assertIs(valid['speed_limiter_active'], True)
        self.assertEqual([wheel['position'] for wheel in valid['wheels']], ['FL', 'FR', 'RL', 'RR'])
        self.assertEqual([wheel['pressure_kpa'] for wheel in valid['wheels']], [0, 171, 172, 173])
        for index, wheel in enumerate(valid['wheels']):
            self.assertEqual(wheel['brake_temp_c'], 0 if index == 0 else 300 + index)
            self.assertAlmostEqual(wheel['suspension_deflection_m'], -.03 + .02 * index)
            self.assertAlmostEqual(wheel['ride_height_m'], -.01 + .02 * index)
            self.assertEqual(wheel['wear_fraction'], 0 if index == 0 else 1 if index == 1 else .5)
            self.assertIs(wheel['flat'], index == 1)
            self.assertIs(wheel['detached'], index == 2)
            for point, value in enumerate(wheel['temperature_c']):
                self.assertAlmostEqual(value, 10 * index + point)
        invalid = measured['MEASURED_INVALID']
        self.assertEqual(invalid['sector_flags'], [None, 1, -1])
        self.assertIsNone(invalid['sector'])
        self.assertIsNone(measured['MEASURED_STALE_SCORING']['sector_flags'])
        self.assertIsNone(measured['MEASURED_STALE_SCORING']['sector'])
        for key in ('steering_rad', 'tc_setting', 'abs_setting', 'front_tyre_wet'):
            self.assertIsNone(invalid[key], key)
        self.assertIsNone(invalid['gear'])
        self.assertIsNone(invalid['engine_rpm'])
        self.assertEqual(invalid['wheels'][0]['temperature_c'], [None] * 3)
        self.assertIsNone(invalid['wheels'][1]['temperature_c'][2])
        self.assertTrue(all(wheel['pressure_kpa'] is None for wheel in invalid['wheels'][:3]))
        self.assertTrue(all(invalid[key] is None for key in ('engine_water_temp_c', 'engine_oil_temp_c', 'clutch_ratio')))
        self.assertTrue(all(wheel['brake_temp_c'] is None and wheel['wear_fraction'] is None
                            for wheel in invalid['wheels'][:3]))
        self.assertIsNone(invalid['wheels'][0]['suspension_deflection_m'])
        self.assertIsNone(invalid['wheels'][0]['ride_height_m'])
        absent = measured['MEASURED_ABSENT']
        self.assertIsNone(absent['gear'])
        self.assertIsNone(absent['engine_rpm'])
        self.assertTrue(all(wheel['pressure_kpa'] is None and wheel['temperature_c'] == [None] * 3
                            for wheel in absent['wheels']))
        self.assertTrue(all(absent[key] is None for key in ('engine_water_temp_c', 'engine_oil_temp_c', 'clutch_ratio',
                                                          'abs_active', 'tc_active', 'speed_limiter_active')))
        self.assertTrue(all(wheel[key] is None for wheel in absent['wheels']
                            for key in ('brake_temp_c', 'suspension_deflection_m', 'ride_height_m',
                                        'wear_fraction', 'flat', 'detached')))
        (folder / "verification.json").write_text(json.dumps({"state": "PASS", "scope": "NATIVE_SYNTHETIC_NO_GAME_ATTACH", "cases": cases,
                                                              "traffic_cases": list(traffic),
                                                              "maximum_vehicles": len(traffic['TRAFFIC_FULL_GRID']['vehicles'])}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
