"""Synthetic frames and a fake OS layer; real lock DLL, no game attachment."""
import ctypes
import json
import math
import os
from pathlib import Path
import statistics
import struct
import sys
import time
import unittest
from unittest.mock import patch

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


class ReaderPureTests(unittest.TestCase):
    def test_class_hash_and_compound_bytes(self):
        self.assertEqual(class_key(b''),0)
        self.assertEqual(class_key(b'GT3\0ignored'),class_key(b'GT3'))
        self.assertTrue(tyre_wet(b'RAIN'))
        self.assertFalse(tyre_wet(b'Medium'))
        self.assertIsNone(tyre_wet(b'unknown'))


if __name__=='__main__': unittest.main()
