"""Compare installed-header Python frames with the released C++ logic.

Developer-only MSVC harnesses and frame bytes live in temporary directories.
Reports contain counts, scenario names, seeds and difference paths only.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from dev_lmu_layout_check import ROOT, compile_cpp
from lmu_python_reader import FrameProcessor
from lmu_sdk_layout import load_layout
from lmu_shared_memory import LmuLiveUnavailable, resolve_sdk_dir
from test_lmu_python_reader import FrameBuilder, base_frame

BRIDGE=ROOT/'iRacing_Backend/lmu_native_bridge.cpp'
SEEDS=tuple(2026102200+i for i in range(20))


def difference(a,b,path=()):
    if isinstance(a,dict) and isinstance(b,dict):
        if set(a)!=set(b): return '.'.join(map(str,path))+':keys'
        for key in a:
            mismatch=difference(a[key],b[key],path+(key,))
            if mismatch: return mismatch
        return None
    if isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b): return '.'.join(map(str,path))+':length'
        for index,(x,y) in enumerate(zip(a,b)):
            mismatch=difference(x,y,path+(index,))
            if mismatch: return mismatch
        return None
    if isinstance(a,bool)!=isinstance(b,bool): return '.'.join(map(str,path))+':type'
    if a==b: return None
    field_path=path[1:] if path[:1]==('snapshot',) else path
    allowed=(field_path==('speed_mps',) or (len(field_path)==3 and field_path[0]=='scoring_cars' and field_path[2]=='speed_mps'))
    if allowed and isinstance(a,(int,float)) and isinstance(b,(int,float)) and math.isclose(a,b,rel_tol=1e-12,abs_tol=0): return None
    return '.'.join(map(str,path))+':value'


def python_result(processor,raw):
    try:
        return {'snapshot':processor.process_frame(raw)}
    except LmuLiveUnavailable as exc:
        return {'error':exc.code}
    except (UnicodeError,ValueError):
        return {'error':'SHARED_LAYOUT_INVALID'}


class AdapterParity:
    """Feed both results through the unchanged SDK adapter; retain no values."""
    def __init__(self):
        from lmu_irsdk import LmuIrsdk
        self.now = 0.
        self.sources = [self.Feed(), self.Feed()]
        self.adapters = [LmuIrsdk(source_factory=lambda source=source: source,
                                  clock=lambda: self.now) for source in self.sources]
        for sdk in self.adapters:
            # Offline comparison must not write replayed laps to user history.
            sdk.lap_verdicts = SimpleNamespace(observe=lambda _snapshot: None)
        self.frames = 0

    class Feed:
        def open(self): pass
        def close(self): pass
        def read(self, timeout_ms=0):
            if 'error' in self.result:
                raise LmuLiveUnavailable(self.result['error'], 'offline frame error')
            return self.result['snapshot']

    def frame(self, expected, actual):
        self.now += 1 / 90
        states = []
        for sdk, source, result in zip(self.adapters, self.sources, (expected, actual)):
            source.result = result
            sdk.startup()
            sdk.freeze_var_buffer_latest()
            states.append({'vars': sdk.vars, 'info': sdk.info,
                           'connected': sdk.is_connected, 'error': sdk.unavailable})
        self.frames += 1
        # Exact comparison here: no extra tolerance for translated variables.
        mismatch = difference(*states)
        for sdk in self.adapters:
            if not sdk.is_connected:
                sdk.shutdown()
        return mismatch


def bridge_include():
    # The branch must still use exactly the released main bridge as its oracle.
    main_source=subprocess.check_output(['git','show','main:iRacing_Backend/lmu_native_bridge.cpp'],cwd=ROOT)
    current=BRIDGE.read_bytes()
    if main_source.replace(b'\r\n',b'\n')!=current.replace(b'\r\n',b'\n'):
        raise RuntimeError('working oracle differs from main')
    return '#define LMU_HEADER_FINGERPRINT "offline-oracle"\n#include "'+BRIDGE.as_posix()+'"\n'


def fixture_code():
    tree=ast.parse((ROOT/'iRacing_Backend/test_lmu_shared_memory.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node,ast.FunctionDef) and node.name=='test_native_copy_lock_bounds_versions_and_cache':
            for item in ast.walk(node):
                if isinstance(item,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='code' for t in item.targets):
                    return ast.literal_eval(item.value)
    raise RuntimeError('released native fixture not found')


def run_fixture(sdk_dir=None, *, adapters=False):
    sdk_dir=resolve_sdk_dir(sdk_dir)
    layout=load_layout(sdk_dir)
    original=fixture_code()
    wrappers=r'''
#include <iostream>
#include <fstream>
#include <unordered_map>
std::ofstream trace;
std::unordered_map<Source*,unsigned> identities;
unsigned nextIdentity=0;
unsigned identity(Source* s) {
    if(!identities.count(s) || (!s->gameVersion && !s->seenScoring && !s->epoch)) identities[s]=++nextIdentity;
    return identities[s];
}
void record(unsigned id,Source* s,SharedMemoryObjectOut& raw,int result,bool tu,bool su) {
    trace.write(reinterpret_cast<const char*>(&id),sizeof(id));
    trace.write(reinterpret_cast<const char*>(&raw),sizeof(raw));
    std::cout<<"PARITY:"<<"{\"result\":"<<result<<",\"state\":"<<id<<',';
    if(result==1)std::cout<<"\"snapshot\":"<<snapshot(s,tu,su);
    else if(result<0)std::cout<<"\"error\":\""<<error<<'"';
    else std::cout<<"\"busy\":true";
    std::cout<<"}\n";
}
bool tracked_validate(Source* s,SharedMemoryObjectOut& raw,bool& tu,bool& su) {
    unsigned id=identity(s);bool ok=validate_and_copy(s,raw,tu,su);record(id,s,raw,ok?1:-1,tu,su);return ok;
}
int tracked_copy(Source* s,SharedMemoryObjectOut& raw,ExistingLockWords* words,HANDLE event,bool& tu,bool& su) {
    unsigned id=identity(s);int ok=copy_existing_frame(s,raw,words,event,tu,su);record(id,s,raw,ok,tu,su);return ok;
}
#define validate_and_copy tracked_validate
#define copy_existing_frame tracked_copy
#define main fixture_main
'''
    tail='''
#undef main
#undef validate_and_copy
#undef copy_existing_frame
int main(int argc,char** argv) {
 if(argc!=2)return 2;
 trace.open(argv[1],std::ios::binary);if(!trace)return 3;
 int result=fixture_main();trace.close();return result;
}
'''
    with tempfile.TemporaryDirectory(prefix='lmu-oracle-fixture-') as temp:
        folder=Path(temp)
        exe=compile_cpp(folder,bridge_include()+wrappers+original+tail,sdk_dir=sdk_dir)
        raw_path=folder/'frames.bin'
        result=subprocess.run([str(exe),str(raw_path)],capture_output=True,timeout=60)
        if result.returncode:
            raise RuntimeError('released fixture failed; refusing parity verification')
        lines=result.stdout.decode('utf-8').splitlines()
        parity=[json.loads(line.split('PARITY:',1)[1]) for line in lines if 'PARITY:' in line]
        processors={}; differences=[]; frames=0; adapter_states={}; adapter_differences=[]
        with raw_path.open('rb') as stream:
            for expected in parity:
                state=struct.unpack('<I',stream.read(4))[0]
                raw=stream.read(layout.types['SharedMemoryObjectOut'].size)
                if state!=expected['state'] or len(raw)!=layout.types['SharedMemoryObjectOut'].size:
                    raise RuntimeError('fixture trace framing invalid')
                if state not in processors: processors[state]=FrameProcessor(layout)
                processor=processors[state]
                if expected['result']==0: continue
                frames+=1
                actual=python_result(processor,raw)
                wanted={key:expected[key] for key in ('snapshot','error') if key in expected}
                mismatch=difference(wanted,actual)
                if mismatch: differences.append({'frame':frames,'path':mismatch})
                if adapters:
                    if state not in adapter_states: adapter_states[state]=AdapterParity()
                    mismatch=adapter_states[state].frame(wanted,actual)
                    if mismatch: adapter_differences.append({'frame':frames,'path':mismatch})
            if stream.read(1): raise RuntimeError('extra fixture trace frames')
    # Collect only scenario labels; never persist snapshot values.
    outputs=sorted(set(line.split(':',1)[0] for line in lines if line.startswith(('TRAFFIC_','MEASURED_')) and ':' in line))
    assertions=sorted(set(line for line in lines if re.fullmatch('[A-Z_]+',line)))
    expected_assertions=set(re.findall(r'require\(.*?,\s*"([A-Z_]+)"\s*\);',original,re.S))
    expected_outputs=set(re.findall(r'std::cout\s*<<\s*"((?:TRAFFIC|MEASURED)_[A-Z_]+):"',original))
    if set(outputs)!=expected_outputs or set(assertions)!=expected_assertions:
        raise RuntimeError('released fixture scenario coverage is incomplete')
    return {'frames':frames,'busy_calls':sum(row['result']==0 for row in parity),
            'outputs':outputs,'assertions':assertions,'differences':differences,
            'adapter_frames':frames if adapters else 0,'adapter_differences':adapter_differences,
            'traffic_outputs':sum(n.startswith('TRAFFIC_') for n in outputs),
            'measured_outputs':sum(n.startswith('MEASURED_') for n in outputs)}


def build_stream_oracle(folder,sdk_dir):
    code=bridge_include()+r'''
#include <iostream>
#include <unordered_map>
#include <io.h>
#include <fcntl.h>
int main(int argc,char** argv) {
 if(argc!=3)return 2;
 _setmode(_fileno(stdin),_O_BINARY);
 std::unordered_map<unsigned,std::unique_ptr<Source>> sources;
 unsigned id=0;
 auto raw=std::make_unique<SharedMemoryObjectOut>();
 while(std::cin.read(reinterpret_cast<char*>(&id),sizeof(id))) {
   if(!std::cin.read(reinterpret_cast<char*>(raw.get()),sizeof(*raw)))return 3;
   auto& entry=sources[id];
   if(!entry) {entry=std::make_unique<Source>();entry->pid=static_cast<DWORD>(std::stoul(argv[1]));entry->processIdentity=std::stoull(argv[2]);}
   bool tu=false,su=false;
   if(validate_and_copy(entry.get(),*raw,tu,su))std::cout<<"{\"snapshot\":"<<snapshot(entry.get(),tu,su)<<"}\n";
   else std::cout<<"{\"error\":\""<<error<<"\"}\n";
   std::cout.flush();
 }
 return std::cin.eof()?0:4;
}
'''
    return compile_cpp(folder,code,sdk_dir=sdk_dir,name='stream-oracle')


class StreamOracle:
    def __init__(self,exe,*,pid=4321,process_identity=123456789):
        self.process=subprocess.Popen([str(exe),str(pid),str(process_identity)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    def frame(self,state,raw):
        self.process.stdin.write(struct.pack('<I',state)+raw)
        self.process.stdin.flush()
        line=self.process.stdout.readline()
        if not line: raise RuntimeError('native oracle ended unexpectedly')
        try: return json.loads(line)
        except (ValueError,UnicodeError): return {'error':'SHARED_LAYOUT_INVALID'}

    def close(self):
        if self.process.stdin: self.process.stdin.close()
        try:
            status=self.process.wait(timeout=20)
            if status: raise RuntimeError('native oracle exit failure')
        finally:
            self.process.stdout.close(); self.process.stderr.close()

    def __enter__(self): return self
    def __exit__(self,kind,value,traceback):
        if kind: self.process.kill()
        self.close()


def random_frames(layout,seed,count):
    rng=random.Random(seed)
    bases={n:bytes(base_frame(layout,n).data) for n in (0,1,104)}
    numbers=[float('nan'),float('inf'),-float('inf'),-273.15,-1.,0.,.001,.02,.03,.25,1.,2.,100.]
    for index in range(count):
        cars=rng.choice((0,1,104))
        b=FrameBuilder(layout); b.data[:]=bases[cars]
        events=[]
        if rng.random()<.8: events.append('SME_UPDATE_TELEMETRY')
        if rng.random()<.45: events.append('SME_UPDATE_SCORING')
        if rng.random()<.2: events.append(rng.choice(('SME_START_SESSION','SME_END_SESSION','SME_UNLOAD','SME_SHUTDOWN','SME_ENTER','SME_EXIT','SME_SET_ENVIRONMENT')))
        b.events(*events)
        b.set('generic.gameVersion',rng.choices((100,0,101),(97,1,2))[0])
        b.set('scoring.scoringInfo.mSession',rng.choice((1,1,1,2,10)))
        b.set('scoring.scoringInfo.mCurrentET',rng.choice((index*.02,index*.02-.25,index*.02+2.,0.,float('nan'))))
        b.set('scoring.scoringInfo.mNumVehicles',rng.choices((cars,0,1,104,-1,105),(80,4,4,4,4,4))[0])
        b.set('scoring.scoringStreamSize',rng.choices((0,65535,65536),(96,2,2))[0])
        b.set('telemetry.activeVehicles',rng.choices((cars,105),(99,1))[0])
        b.set('telemetry.playerHasVehicle',rng.random()<.9)
        b.set('telemetry.playerVehicleIdx',rng.choice((0,0,0,104,cars-1 if cars else 0)))
        if cars:
            t='telemetry.telemInfo[0]'; v='scoring.vehScoringInfo[0]'
            b.set(t+'.mElapsedTime',rng.choice((index*.02,25.,0.,float('nan'),float('inf'))))
            b.set(t+'.mID',rng.choice((77,77,88,-1)))
            b.set(t+'.mVehicleModel',rng.choice(('Example GT3','Example GT3','Other car')))
            b.set(t+'.mFuel',rng.choice(numbers))
            b.set(t+'.mEngineRPM',rng.choice(numbers))
            b.set(t+'.mVirtualEnergy',rng.choice(numbers))
            b.set(t+'.mWheel[0].mPressure',rng.choice(numbers))
            b.set(t+'.mWheel[0].mWear',rng.choice(numbers))
            b.set(t+'.mWheel[0].mTemperature[0]',rng.choice(numbers))
            b.set(v+'.mPlace',rng.choice((0,1,2,255)))
            b.set(v+'.mVehicleClass',rng.choice(('GT3','LMP2','')))
            b.set(v+'.mLocalVel.x',rng.choice(numbers))
            b.set(v+'.mInPits',rng.random()<.3)
            b.set(v+'.mControl',rng.choice((-1,0,1,2,3)))
            b.set('scoring.scoringInfo.mGamePhase',rng.choice((0,5,9)))
            b.set('scoring.scoringInfo.mSectorFlag[0]',rng.choice((-128,-1,0,7,8,127)))
        if cars==104:
            b.set('telemetry.telemInfo[1].mID',rng.choice((78,77,-1)))
            b.set('scoring.vehScoringInfo[1].mID',rng.choice((78,77,-1)))
            b.set('telemetry.telemInfo[1].mPos.x',rng.choice(numbers))
        yield bytes(b.data)


def run_random(exe,layout,*,seeds=SEEDS,count=500):
    frames=0; differences=[]
    with StreamOracle(exe) as oracle:
        for state,seed in enumerate(seeds):
            processor=FrameProcessor(layout,pid=4321,process_identity=123456789)
            for index,raw in enumerate(random_frames(layout,seed,count)):
                wanted=oracle.frame(state,raw); actual=python_result(processor,raw)
                mismatch=difference(wanted,actual)
                frames+=1
                if mismatch:
                    differences.append({'seed':seed,'frame':index,'path':mismatch})
                    # No allowance changes or downstream stages after a difference.
                    return {'frames':frames,'seeds':list(seeds),'frames_per_seed':count,'differences':differences}
    return {'frames':frames,'seeds':list(seeds),'frames_per_seed':count,'differences':differences}


def save_report(result):
    folder=ROOT/'logs/lmu-python-reader'; folder.mkdir(parents=True,exist_ok=True)
    path=folder/('oracle-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime())+'.json')
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return path


def run_capture(path,exe,layout,*,adapters=False):
    from capture_lmu_raw import read_header,iter_frames
    frames=0; differences=[]; mismatch_count=0
    adapter=AdapterParity() if adapters else None
    adapter_count=0; adapter_differences=[]
    with Path(path).open('rb') as stream:
        header=read_header(stream)
        if (header['header_fingerprint']!=layout.fingerprint or header['protocol_fingerprint']!=layout.protocol_fingerprint
            or header['frame_size']!=layout.types['SharedMemoryObjectOut'].size or header['layout_size']!=layout.root.size):
            raise RuntimeError('capture SDK identity or calculated sizes differ from installed SDK')
        if header['pid']>0xffffffff or header['process_identity']>0xffffffffffffffff:
            raise ValueError('capture process identity out of range')
        processor=FrameProcessor(layout,pid=header['pid'],process_identity=header['process_identity'])
        with StreamOracle(exe,pid=header['pid'],process_identity=header['process_identity']) as oracle:
            for raw in iter_frames(stream,header):
                wanted=oracle.frame(0,raw); actual=python_result(processor,raw)
                mismatch=difference(wanted,actual)
                frames+=1
                if mismatch:
                    mismatch_count+=1
                    if len(differences)<10: differences.append({'frame':frames,'path':mismatch})
                if adapter:
                    mismatch=adapter.frame(wanted,actual)
                    if mismatch:
                        adapter_count+=1
                        if len(adapter_differences)<10: adapter_differences.append({'frame':frames,'path':mismatch})
    return {'frames':frames,'difference_count':mismatch_count,'differences':differences,
            'adapter_frames':adapter.frames if adapter else 0,'adapter_difference_count':adapter_count,
            'adapter_differences':adapter_differences,'passed':frames>0 and mismatch_count==0 and adapter_count==0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-dir',type=Path)
    parser.add_argument('--capture',type=Path,help='compare an existing local capture; never uploads raw frames')
    parser.add_argument('--adapter',action='store_true',help='also compare translated SDK variables and session information exactly')
    args=parser.parse_args()
    sdk=resolve_sdk_dir(args.sdk_dir); layout=load_layout(sdk)
    if args.capture:
        with tempfile.TemporaryDirectory(prefix='lmu-capture-oracle-') as temp:
            result={'capture':run_capture(args.capture,build_stream_oracle(temp,sdk),layout,adapters=args.adapter)}
        result['passed']=result['capture']['passed']
        path=save_report(result)
        print(json.dumps({'passed':result['passed'],'frames':result['capture']['frames'],
                          'difference_count':result['capture']['difference_count'],
                          'adapter_frames':result['capture']['adapter_frames'],
                          'adapter_difference_count':result['capture']['adapter_difference_count'],'report':str(path)}))
        return 0 if result['passed'] else 1
    result={'fixture':run_fixture(sdk,adapters=args.adapter),'header_fingerprint':layout.fingerprint,
            'oracle_sha256':hashlib.sha256(BRIDGE.read_bytes().replace(b'\r\n',b'\n')).hexdigest()}
    if not result['fixture']['differences']:
        with tempfile.TemporaryDirectory(prefix='lmu-oracle-') as temp:
            exe=build_stream_oracle(temp,sdk)
            result['random']=run_random(exe,layout)
    result['passed']=not result['fixture']['differences'] and not result['fixture']['adapter_differences'] and 'random' in result and not result['random']['differences']
    path=save_report(result)
    print(json.dumps({'passed':result['passed'],'fixture_frames':result['fixture']['frames'],
                      'random_frames':result.get('random',{}).get('frames',0),'report':str(path)}))
    return 0 if result['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
