"""Synthetic frames and a fake OS layer; real lock DLL, no game attachment."""
import ctypes
import json
import math
import os
from pathlib import Path
import statistics
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from lmu_python_reader import PythonSharedMemorySource, FrameProcessor, class_key, tyre_wet
from lmu_sdk_layout import load_layout
from lmu_shared_memory import LmuLiveUnavailable, resolve_sdk_dir


class FrameBuilder:
    """Write values through calculated field metadata, without SDK declarations."""
    def __init__(self,layout):
        self.layout=layout
        self.typ=layout.types['SharedMemoryObjectOut']
        self.data=bytearray(self.typ.size)

    def set(self,path,value):
        field=self.typ.find(path)
        typ=field.type
        if typ.kind=='array':
            if isinstance(value,(bytes,str)):
                value=value.encode('utf-8') if isinstance(value,str) else value
                if len(value)>typ.size: raise ValueError('synthetic string too large')
                struct.pack_into('<'+str(typ.size)+'s',self.data,field.offset,value)
            else:
                if len(value)>typ.count: raise ValueError('synthetic array too large')
                for i,item in enumerate(value): self.set(path+f'[{i}]',item)
        elif typ.kind in ('struct','union'):
            for name,item in value.items(): self.set(path+'.'+name,item)
        else:
            if typ.kind=='float': code={4:'f',8:'d'}[typ.size]
            elif typ.kind=='bool': code='?'
            elif typ.kind=='pointer': code='Q'
            else: code=({1:'b',2:'h',4:'i',8:'q'} if typ.signed else {1:'B',2:'H',4:'I',8:'Q'})[typ.size]
            struct.pack_into('<'+code,self.data,field.offset,value)
        return self

    def events(self,*names):
        count=self.typ.find('generic.events').type.count
        values=[self.layout.events['SME_MAX']]*count
        for index,name in enumerate(names): values[index]=self.layout.events[name]
        return self.set('generic.events',values)


def base_frame(layout,count=3):
    b=FrameBuilder(layout).events('SME_UPDATE_TELEMETRY','SME_UPDATE_SCORING')
    for path,value in {
        'generic.gameVersion':100,'telemetry.activeVehicles':count,
        'telemetry.playerVehicleIdx':0,'telemetry.playerHasVehicle':bool(count),
        'scoring.scoringInfo.mNumVehicles':count,'scoring.scoringInfo.mSession':1,
        'scoring.scoringInfo.mCurrentET':24.9,'scoring.scoringInfo.mLapDist':5793,
        'scoring.scoringInfo.mInRealtime':True,'scoring.scoringInfo.mGamePhase':5,
        'scoring.scoringInfo.mEndET':3600,'scoring.scoringInfo.mSessionTimeRemaining':123,
        'scoring.scoringInfo.mSectorFlag':(0,1,-1),
    }.items(): b.set(path,value)
    for i in range(count):
        t=f'telemetry.telemInfo[{i}]'; v=f'scoring.vehScoringInfo[{i}]'
        identifier=77+i
        for name,value in {'mID':identifier,'mElapsedTime':25.,'mLapStartET':5.,
                           'mVehicleName':'Example GT3 #32','mVehicleModel':'Example GT3','mTrackName':'Monza',
                           'mPos':{'x':100.+i,'y':2.5,'z':-4.25-i},'mLocalVel':{'x':3.,'y':4.,'z':0.},
                           'mOri':({'x':1.,'y':0.,'z':0.},{'x':0.,'y':1.,'z':0.},{'x':0.,'y':0.,'z':1.}),
                           'mGear':4,'mEngineRPM':6500.,'mFuel':25.5,'mFuelCapacity':100.,
                           'mPhysicalSteeringWheelRange':540.,'mUnfilteredSteering':.5,
                           'mRearBrakeBias':.4875,'mFrontTireCompoundName':'Wet','mRearTireCompoundName':'Medium'}.items():
            b.set(t+'.'+name,value)
        for name,value in {'mID':identifier,'mIsPlayer':i==0,'mPlace':i+1,'mControl':2,
                           'mTotalLaps':3,'mLastLapTime':120.,'mBestLapTime':117.035,'mCountLapFlag':2,
                           'mVehicleClass':'GT3','mLocalVel':{'x':3.,'y':4.,'z':0.},'mLapStartET':5.}.items():
            b.set(v+'.'+name,value)
    return b


class FakeWindows:
    def __init__(self,layout,frame):
        self.layout=layout
        self.frame=ctypes.create_string_buffer(bytes(frame),len(frame))
        self.lock=ctypes.create_string_buffer(layout.lock.size)
        self.pids=[4321]; self.alive=True; self.fail=None; self.timeout=False
        self.calls=[]; self.closed=[]; self.unmapped=[]; self.truncated=None
        self.handles={'process':1,'LMU_Data':2,'LMU_SharedMemoryLockData':3,
                      'LMU_SharedMemoryLockEvent':4,'LMU_Data_HoldEvent':5,'LMU_Data_DataEvent':6}

    def game_pids(self): self.calls.append(('pids',)); return self.pids
    def open_process(self,pid): self.calls.append(('process',pid)); return None if self.fail=='process' else 1
    def process_identity(self,handle): return None if self.fail=='identity' else 123456789
    def open_mapping(self,name,access):
        self.calls.append(('mapping',name,access)); return None if self.fail==name else self.handles[name]
    def open_event(self,name,access):
        self.calls.append(('event',name,access)); return None if self.fail==name else self.handles[name]
    def map_view(self,handle,access,size):
        self.calls.append(('map',handle,access,size))
        if self.fail==('map',handle): return None
        return ctypes.addressof(self.frame if handle==2 else self.lock)
    def region_size(self,address):
        is_data=address==ctypes.addressof(self.frame)
        size=len(self.frame) if is_data else len(self.lock)
        return size-1 if self.truncated==('data' if is_data else 'lock') else size
    def wait(self,handle,timeout):
        self.calls.append(('wait',handle,timeout))
        return (258 if self.alive else 0) if handle==1 else (258 if self.timeout else 0)
    def close(self,handle): self.closed.append(handle)
    def unmap(self,address): self.unmapped.append(address)


def benchmark_read(iterations=1000):
    layout=load_layout()
    frame=base_frame(layout,104)
    api=FakeWindows(layout,frame.data)
    source=PythonSharedMemorySource(api=api)
    samples=[]
    with source:
        for i in range(iterations+20):
            start=time.perf_counter_ns(); result=source.read()
            duration=(time.perf_counter_ns()-start)/1e6
            if len(result['scoring_cars'])!=104: raise RuntimeError('104-car benchmark not exercised')
            if i>=20: samples.append(duration)
    samples.sort()
    return {'iterations':iterations,'cars':104,'median_ms':statistics.median(samples),
            'p95_ms':samples[int(.95*len(samples))-1],
            'includes':'Header-content hashing, real native copy, decode, cache, traffic and snapshot; fake Windows waits'}


_SDK=resolve_sdk_dir()
_HAS_SDK=all((_SDK/name).is_file() for name in ('SharedMemoryInterface.hpp','InternalsPlugin.hpp','PluginObjects.hpp'))


@unittest.skipUnless(os.name=='nt' and _HAS_SDK,'Windows and installed SDK required; frames are synthetic')
class PythonReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.layout=load_layout()

    def source(self):
        frame=base_frame(self.layout)
        api=FakeWindows(self.layout,frame.data)
        return PythonSharedMemorySource(api=api),api

    def assert_code(self,code,operation):
        with self.assertRaises(LmuLiveUnavailable) as error: operation()
        self.assertEqual(error.exception.code,code)

    def test_probe_and_prepare_do_not_attach_build_or_download(self):
        source,api=self.source()
        with patch('subprocess.run',side_effect=AssertionError('no compiler')),patch.dict(sys.modules,{'lmu_bridge_downloads':None}):
            for result in (source.probe(),source.prepare(retry=True)):
                self.assertEqual(result['state'],'READY')
                self.assertFalse(result['attached'])
                self.assertEqual(result['abi']['sizes']['SharedMemoryLayout'],self.layout.root.size)
        self.assertEqual(api.calls,[])

    def test_open_order_existing_objects_and_computed_mapping_sizes(self):
        source,api=self.source()
        with source:
            calls=[call for call in api.calls if call[0]!='wait']
            self.assertEqual([call[:2] for call in calls],
                [('pids',),('process',4321),('mapping','LMU_Data'),('mapping','LMU_SharedMemoryLockData'),
                 ('event','LMU_SharedMemoryLockEvent'),('event','LMU_Data_HoldEvent'),('event','LMU_Data_DataEvent'),('map',2),('map',3)])
            self.assertEqual(calls[-2][3],self.layout.root.size)
            self.assertEqual(calls[-1][3],self.layout.lock.size)
            self.assertEqual(calls[2][2],4)
        self.assertEqual(len(api.closed),6)
        self.assertEqual(len(api.unmapped),2)

    def test_process_and_mapping_error_order_cleanup(self):
        cases=[('process','GAME_PROCESS_UNAVAILABLE'),('identity','GAME_PROCESS_UNAVAILABLE'),
               ('LMU_Data','SHARED_MEMORY_UNAVAILABLE'),('LMU_SharedMemoryLockData','SHARED_LOCK_UNAVAILABLE'),
               ('LMU_SharedMemoryLockEvent','SHARED_LOCK_UNAVAILABLE'),('LMU_Data_HoldEvent','SHARED_EVENTS_UNAVAILABLE'),
               ('LMU_Data_DataEvent','SHARED_EVENTS_UNAVAILABLE'),(('map',2),'SHARED_LAYOUT_UNAVAILABLE'),
               (('map',3),'SHARED_LOCK_UNAVAILABLE')]
        for failure,code in cases:
            with self.subTest(failure=failure):
                source,api=self.source(); api.fail=failure
                self.assert_code(code,source.open)
                self.assertIsNone(source._handle)
                self.assertEqual(source._handles,[])
                self.assertEqual(source._views,[])
        for pids,code in [([],'GAME_NOT_RUNNING'),([1,2],'MULTIPLE_GAME_PROCESSES')]:
            source,api=self.source(); api.pids=pids
            self.assert_code(code,source.open)
            self.assertEqual(api.calls,[('pids',)])

    def test_truncated_data_and_lock_mapping_rejected_before_copy(self):
        for name,code in [('data','SHARED_LAYOUT_UNAVAILABLE'),('lock','SHARED_LOCK_UNAVAILABLE')]:
            source,api=self.source(); api.truncated=name
            with patch('lmu_python_reader.FrameProcessor',side_effect=AssertionError('must not decode')):
                self.assert_code(code,source.open)
            self.assertIsNone(source._handle)

    def test_read_prefix_fields_events_and_native_copy(self):
        source,api=self.source()
        with source:
            result=source.read(201)
            self.assertEqual(result['vehicle_id'],77)
            self.assertEqual(result['speed_mps'],5)
            self.assertEqual(result['sector_flags'],[0,1,-1])
            self.assertEqual(result['front_tyre_wet'],True)
            self.assertTrue(result['traffic']['complete'])
            self.assertEqual(len(result['scoring_cars']),3)
            self.assertTrue(result['session_id'].endswith(':4321:123456789:0:1'))
            self.assertIn(('wait',5,100),api.calls)
            self.assertIn(('wait',6,100),api.calls)
            self.assertEqual(struct.unpack_from('<l',api.lock,4)[0],0)

    def test_timeout_busy_exit_and_timeout_validation(self):
        source,api=self.source()
        for value in (True,1.5,-1,1001):
            with self.assertRaises(ValueError): source.read(value)
        self.assert_code('NOT_OPEN',source.read)
        with source:
            api.timeout=True; self.assertIsNone(source.read())
            api.timeout=False
            struct.pack_into('<l',api.lock,4,1); self.assertIsNone(source.read())
            struct.pack_into('<l',api.lock,4,0)
            api.alive=False; self.assert_code('GAME_EXITED',source.read)
        self.assertIsNone(source._handle)

    def test_version_change_closes_connection(self):
        source,api=self.source()
        with source:
            source.read()
            offset=self.layout.types['SharedMemoryObjectOut'].find('generic.gameVersion').offset
            struct.pack_into('<i',api.frame,offset,101)
            self.assert_code('GAME_VERSION_CHANGED',source.read)
            self.assertIsNone(source._handle)

    def test_invalid_frame_precedes_post_copy_game_exit(self):
        source,api=self.source()
        with source:
            offset=self.layout.types['SharedMemoryObjectOut'].find('generic.gameVersion').offset
            struct.pack_into('<i',api.frame,offset,0)
            process_checks=[]
            def wait(handle,timeout):
                if handle==1:
                    process_checks.append(handle)
                    return 258 if len(process_checks)==1 else 0
                return 0
            with patch.object(api,'wait',side_effect=wait):
                self.assert_code('GAME_VERSION_INVALID',source.read)
            self.assertEqual(len(process_checks),1)

    def test_changed_headers_close_before_native_copy(self):
        from dataclasses import replace
        source,api=self.source()
        with source:
            with patch('lmu_python_reader.load_layout',return_value=replace(self.layout,fingerprint='changed')):
                self.assert_code('ABI_MISMATCH',source.read)
        self.assertIsNone(source._handle)

    def test_unknown_protocol_maps_to_unsupported(self):
        from lmu_sdk_layout import LayoutUnsupported
        source,api=self.source()
        with patch('lmu_python_reader.load_layout',side_effect=LayoutUnsupported('fake unknown protocol')):
            self.assert_code('CONNECTION_SDK_UNSUPPORTED',source.prepare)
        self.assertEqual(api.calls,[])

    def test_invalid_utf8_preserves_released_failure(self):
        source,api=self.source()
        offset=self.layout.types['SharedMemoryObjectOut'].find('telemetry.telemInfo[0].mVehicleName').offset
        ctypes.memmove(ctypes.addressof(api.frame)+offset,b'\xff\0',2)
        with source:
            self.assert_code('SHARED_LAYOUT_INVALID',source.read)
            self.assertIsNone(source._handle)

    def test_cache_resets_and_unflagged_scoring_adoption(self):
        b=base_frame(self.layout)
        processor=FrameProcessor(self.layout,pid=1,process_identity=2)
        processor.process_frame(b.data)
        b.events('SME_UPDATE_TELEMETRY').set('scoring.scoringInfo.mCurrentET',25.1)
        first=processor.process_frame(b.data)
        self.assertTrue(first['scoring_updated'])
        self.assertFalse(processor.process_frame(b.data)['scoring_updated'])
        b.set('telemetry.telemInfo[0].mID',88).set('scoring.scoringInfo.mCurrentET',25.3)
        result=processor.process_frame(b.data)
        self.assertFalse(result['scoring_updated'])
        self.assertIsNone(result['scoring_et'])
        self.assertTrue(result['session_id'].endswith(':1:unknown'))

    def test_nonfinite_null_and_traffic_boundary(self):
        b=base_frame(self.layout).set('telemetry.telemInfo[1].mElapsedTime',24.75)
        p=FrameProcessor(self.layout)
        self.assertTrue(p.process_frame(b.data)['traffic']['complete'])
        b.set('telemetry.telemInfo[1].mElapsedTime',24.749999)
        self.assertFalse(p.process_frame(b.data)['traffic']['complete'])
        b.set('telemetry.telemInfo[0].mEngineRPM',float('nan'))
        b.set('telemetry.telemInfo[0].mWheel[0].mTemperature[0]',float('inf'))
        result=p.process_frame(b.data)
        self.assertIsNone(result['engine_rpm'])
        self.assertIsNone(result['wheels'][0]['temperature_c'][0])
        json.dumps(result,allow_nan=False)

    def test_capture_round_trip_and_truncation_rejection(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from capture_lmu_raw import capture,read_header,iter_frames
        from io import BytesIO
        source,api=self.source()
        with tempfile.TemporaryDirectory() as temp:
            ticks=iter((0,0,2))
            result=capture(source,2,output_dir=temp,now=lambda:next(ticks))
            self.assertEqual(result['frames'],1)
            data=(Path(temp)/result['file']).read_bytes()
            stream=BytesIO(data); header=read_header(stream)
            self.assertEqual(header['pid'],4321)
            self.assertEqual(list(iter_frames(stream,header)),[api.frame.raw])
            stream=BytesIO(data[:-2]); header=read_header(stream)
            with self.assertRaises(ValueError): list(iter_frames(stream,header))
        self.assertIsNone(source._handle)
        with self.assertRaises(ValueError): capture(source,301)

    def test_appended_event_is_not_exposed_in_output(self):
        import re,shutil
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            shutil.copytree(resolve_sdk_dir(),folder,dirs_exist_ok=True)
            path=folder/'SharedMemoryInterface.hpp'
            text=path.read_text(encoding='utf-8-sig')
            text,count=re.subn(r'\bSME_MAX\s*([,}])','SME_READER_EXTRA,\nSME_MAX\\1',text,count=1)
            self.assertEqual(count,1)
            path.write_text(text,encoding='utf-8')
            layout=load_layout(folder)
            frame=base_frame(layout).events('SME_UPDATE_TELEMETRY','SME_UPDATE_SCORING','SME_READER_EXTRA')
            result=FrameProcessor(layout).process_frame(frame.data)
            self.assertEqual(result['events'],['SME_UPDATE_SCORING','SME_UPDATE_TELEMETRY'])

    def test_released_cpp_fixture_parity(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from dev_lmu_oracle import run_fixture
        from dev_lmu_layout_check import find_msvc
        try: find_msvc()
        except (OSError,RuntimeError): self.skipTest('MSVC developer tools unavailable')
        result=run_fixture()
        self.assertEqual(result['differences'],[])
        self.assertEqual(result['traffic_outputs'],22)
        self.assertEqual(result['measured_outputs'],5)
        self.assertIn('UNFLAGGED_NEW_SCORING_ADOPTED',result['assertions'])

    def test_selected_app_reader_opens_and_reads_without_build_or_download(self):
        from lmu_irsdk import LmuIrsdk
        api=FakeWindows(self.layout,base_frame(self.layout).data)
        sdk=LmuIrsdk(clock=lambda: 0.)
        with (patch.dict(os.environ,{'IRACING_LMU_READER':''}),
              patch.dict(sys.modules,{'lmu_bridge_downloads':None}),
              patch('lmu_python_reader._Windows',return_value=api),
              patch('subprocess.run',side_effect=AssertionError('no compiler')),
              patch('subprocess.Popen',side_effect=AssertionError('no child process')),
              patch('urllib.request.urlopen',side_effect=AssertionError('no download')),
              patch('lmu_shared_memory.build_artifact_dir',side_effect=AssertionError('no old bundle')),
              patch('lmu_shared_memory.downloaded_bridge_dir',side_effect=AssertionError('no old cache')),
              patch('lmu_shared_memory.build_native_bridge',side_effect=AssertionError('no build'))):
            try:
                self.assertTrue(sdk.startup())
                self.assertIsInstance(sdk.source,PythonSharedMemorySource)
                sdk.freeze_var_buffer_latest()
                self.assertEqual(sdk.vars['Speed'],5.)
                self.assertTrue(sdk.is_connected)
                self.assertTrue(sdk.info)
            finally:
                sdk.shutdown()
        self.assertTrue(api.unmapped)
        self.assertEqual(len(api.closed),6)

    def test_selected_setup_routes_probe_prepare_and_fail_without_download_fallback(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from lmu_routes import LmuRoutesServices,register_routes
        app=FastAPI()
        register_routes(app,LmuRoutesServices(discover_dirs=lambda: (),list_files=lambda _: [],
                        dependency_check=lambda: None,practice_store=Mock(),corner_names_store=Mock()))
        with (patch.dict(os.environ,{'IRACING_LMU_READER':''}),
              patch.dict(sys.modules,{'lmu_bridge_downloads':None}),
              patch('lmu_python_reader._Windows',side_effect=AssertionError('no game API')),
              patch('subprocess.run',side_effect=AssertionError('no compiler')),
              patch('subprocess.Popen',side_effect=AssertionError('no child process')),
              patch('urllib.request.urlopen',side_effect=AssertionError('no download')),
              patch('lmu_shared_memory.build_native_bridge',side_effect=AssertionError('no build')),
              patch('lmu_irsdk.shared',return_value=Mock()),TestClient(app) as client):
            for method,path in (('GET','/api/lmu/live/bridge'),('POST','/api/lmu/live/connect'),
                                ('POST','/api/lmu/live/bridge/build')):
                result=client.request(method,path)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['state'],'READY')
                for code in ('ABI_MISMATCH','NATIVE_NOT_BUILT','SDK_UNAVAILABLE','CONNECTION_SDK_UNSUPPORTED'):
                    with self.subTest(path=path,code=code),patch.object(
                            PythonSharedMemorySource,'_load',side_effect=LmuLiveUnavailable(code,'test unavailable')):
                        result=client.request(method,path)
                        self.assertEqual(result.status_code,200)
                        self.assertEqual(result.json()['state'],code)
                        self.assertFalse(result.json()['buildable'])
            # The settings page embeds the same selected-reader status.
            with patch('lmu_irsdk.shared',return_value=Mock(journal=Mock(status=lambda: {}))):
                self.assertEqual(client.get('/api/lmu/status').json()['bridge']['state'],'READY')

    def test_released_oracle_outputs_translate_to_identical_sdk_variables_and_sessions(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from dev_lmu_oracle import run_fixture
        from dev_lmu_layout_check import find_msvc
        try: find_msvc()
        except (OSError,RuntimeError): self.skipTest('MSVC developer tools unavailable')
        with patch('lmu_lap_verdicts.LapVerdicts.observe',side_effect=AssertionError('no user history')) as observer:
            result=run_fixture(adapters=True)
        observer.assert_not_called()
        self.assertEqual(result['differences'],[])
        self.assertEqual(result['adapter_frames'],45)
        self.assertEqual(result['adapter_differences'],[])


class ReaderPureTests(unittest.TestCase):
    def test_adapter_allowance_is_only_for_four_approved_variable_paths(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from dev_lmu_oracle import difference
        for name in ('Speed','LapDist','LapDistPct','CarIdxLapDistPct'):
            def state(value):
                return {'vars':{name:[value] if name=='CarIdxLapDistPct' else value}}
            with self.subTest(name=name):
                self.assertIsNone(difference(state(3.),state(3.+1e-13),adapter=True))
                self.assertIsNotNone(difference(state(3.),state(3.+1e-10),adapter=True))
                self.assertIsNotNone(difference(state(0.),state(1e-15),adapter=True))
                self.assertIsNotNone(difference(state(True),state(1.),adapter=True))
                self.assertIsNotNone(difference(state(None),state(0.),adapter=True))
                self.assertIsNotNone(difference(state(3.),state(3.+1e-13)))
                self.assertIsNotNone(difference({'info':state(3.)['vars']},
                                               {'info':state(3.+1e-13)['vars']},adapter=True))
        self.assertIsNotNone(difference({'vars':{'FuelLevel':3.}}, {'vars':{'FuelLevel':3.+1e-13}},adapter=True))
        self.assertIsNotNone(difference({'vars':{'nested':{'Speed':3.}}},
                                       {'vars':{'nested':{'Speed':3.+1e-13}}},adapter=True))
        self.assertIsNotNone(difference({'vars':{'CarIdxLapDistPct':3.}},
                                       {'vars':{'CarIdxLapDistPct':3.+1e-13}},adapter=True))
        self.assertIsNotNone(difference({'snapshot':{'speed_mps':3.}},
                                       {'snapshot':{'speed_mps':3.+1e-13}},adapter=True))
        self.assertIsNotNone(difference({'vars':{'CarIdxLapDistPct':[3.]}},
                                       {'vars':{'CarIdxLapDistPct':[3.,3.]}},adapter=True))

    def test_reader_selection_defaults_to_python_and_preserves_explicit_bridge_and_source_override(self):
        from lmu_irsdk import LmuIrsdk,shared_memory_source_factory
        from lmu_shared_memory import SharedMemorySource
        for value in ('','python','native','PYTHON','invalid'):
            with self.subTest(value=value),patch.dict(os.environ,{'IRACING_LMU_READER':value}):
                self.assertIs(shared_memory_source_factory(),PythonSharedMemorySource)
        with patch.dict(os.environ,{},clear=True):
            self.assertIs(shared_memory_source_factory(),PythonSharedMemorySource)
        with patch.dict(os.environ,{'IRACING_LMU_READER':'bridge'}):
            self.assertIs(shared_memory_source_factory(),SharedMemorySource)
            source=Mock()
            sdk=LmuIrsdk(source_factory=lambda: source)
            try:
                self.assertTrue(sdk.startup())
                self.assertIs(sdk.source,source)
                source.open.assert_called_once_with()
            finally:
                sdk.shutdown()

    def test_class_hash_and_compound_bytes(self):
        self.assertEqual(class_key(b''),0)
        self.assertEqual(class_key(b'GT3\0ignored'),class_key(b'GT3'))
        self.assertTrue(tyre_wet(b'RAIN'))
        self.assertFalse(tyre_wet(b'Medium'))
        self.assertIsNone(tyre_wet(b'unknown'))

    def test_oracle_allowance_is_only_for_hypot_fields(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from dev_lmu_oracle import difference
        self.assertIsNone(difference({'snapshot':{'speed_mps':3.0}}, {'snapshot':{'speed_mps':3.0+1e-13}}))
        self.assertIsNone(difference({'snapshot':{'scoring_cars':[{'speed_mps':3.0}]}},
                                     {'snapshot':{'scoring_cars':[{'speed_mps':3.0+1e-13}]}}))
        self.assertIsNotNone(difference({'snapshot':{'fuel_l':3.0}}, {'snapshot':{'fuel_l':3.0+1e-13}}))
        self.assertIsNotNone(difference({'snapshot':{'speed_mps':0.0}}, {'snapshot':{'speed_mps':1e-15}}))
        self.assertIsNotNone(difference({'snapshot':{'wheels':[{'speed_mps':3.0}]}},
                                        {'snapshot':{'wheels':[{'speed_mps':3.0+1e-13}]}}))


if __name__=='__main__': unittest.main()
