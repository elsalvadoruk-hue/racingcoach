"""LMU reader using installed-header layouts and a tiny prebuilt lock DLL."""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import threading
from types import SimpleNamespace
import uuid

from lmu_shared_memory import (_platform_check, _unavailable, LmuLiveUnavailable,
                               resolve_sdk_dir, sdk_configuration)
from lmu_sdk_layout import load_layout, LayoutUnsupported, LayoutUnstable, REQUIRED_FIELDS, EVENT_NAMES

LOCK_DIR = Path(__file__).with_name('lmu_lock')
LOCK_SOURCE = Path(__file__).with_name('lmu_lock.c')


def _unsupported_sdk():
    return LmuLiveUnavailable('CONNECTION_SDK_UNSUPPORTED',
                              'LMU가 연결 방식을 바꿨습니다. 앱 업데이트가 필요합니다.')


def load_lock_dll(folder=None, *, source=None, existing=None):
    """Validate the distributed pair before executing any native code."""
    _platform_check()
    folder = Path(folder) if folder is not None else LOCK_DIR
    source = Path(source) if source is not None else LOCK_SOURCE
    path = folder / 'lmu_lock.dll'
    try:
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        dll_bytes = path.read_bytes()
        source_hash = hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        pe = struct.unpack_from('<I',dll_bytes,0x3c)[0]
        valid = (manifest.get('lock_version') == 1 and manifest.get('runtime') == 'STATIC_MSVC'
                 and manifest.get('source_sha256') == source_hash
                 and manifest.get('dll_sha256') == hashlib.sha256(dll_bytes).hexdigest()
                 and dll_bytes[:2] == b'MZ' and dll_bytes[pe:pe+4] == b'PE\0\0'
                 and struct.unpack_from('<H',dll_bytes,pe+4)[0] == 0x8664)
        if not valid:
            raise ValueError('lock DLL manifest mismatch')
    except (OSError, ValueError, TypeError, AttributeError, struct.error) as exc:
        raise LmuLiveUnavailable('ABI_MISMATCH', 'LMU 연결 구성 파일을 확인할 수 없습니다. 앱을 다시 설치해 주세요.') from exc
    try:
        dll = existing if existing is not None else ctypes.CDLL(str(path.resolve()))
        dll.lmu_lock_version.argtypes, dll.lmu_lock_version.restype = [], ctypes.c_int
        dll.lmu_copy_locked.argtypes = [ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
        dll.lmu_copy_locked.restype = ctypes.c_int
        if dll.lmu_lock_version() != 1:
            raise ValueError('unsupported lock DLL version')
        return dll
    except (OSError, AttributeError, ValueError) as exc:
        raise LmuLiveUnavailable('ABI_MISMATCH', 'LMU 연결 구성 파일을 확인할 수 없습니다. 앱을 다시 설치해 주세요.') from exc


class _Decoder:
    """Compile needed scalar fields into one unpack operation per SDK record."""
    def __init__(self, typ, only=None):
        leaves = []
        def tree(current, offset, expected=None):
            if current.kind in ('struct', 'union'):
                required = REQUIRED_FIELDS.get(current.name)
                if required is None:
                    raise LayoutUnsupported('no decoder requirements: '+current.name)
                requirements = {n:kind for kind,fields in required.items() for n in fields.split()}
                names = set(requirements)
                if current is typ and only is not None:
                    names &= set(only)
                return ('record', [(name,tree(member.type,offset+member.offset,requirements[name]))
                                   for name,member in current.fields.items() if name in names])
            if current.kind == 'array':
                if expected=='text':
                    index = len(leaves); leaves.append((offset,str(current.size)+'s',current.size)); return ('leaf',index)
                count=current.count
                if expected and '[' in expected:
                    count=int(expected.rsplit('[',1)[1].rstrip(']'))
                return ('array',[tree(current.element,offset+i*current.element.size) for i in range(count)])
            if current.kind == 'float':
                code = {4:'f',8:'d'}[current.size]
            elif current.kind == 'bool':
                code = '?'
            elif current.kind == 'pointer':
                code = 'Q'
            elif current.kind == 'integer':
                code = ({1:'b',2:'h',4:'i',8:'q'} if current.signed else {1:'B',2:'H',4:'I',8:'Q'})[current.size]
            else:
                raise LayoutUnsupported('unsupported decoder scalar')
            index = len(leaves); leaves.append((offset,code,current.size)); return ('leaf',index)
        nodes = tree(typ,0)
        order = sorted(range(len(leaves)),key=lambda i: leaves[i][0])
        indexes, offset, formats = {},0,['<']
        for slot,index in enumerate(order):
            position,code,size = leaves[index]
            if position < offset:
                raise LayoutUnsupported('overlapping requested decoder fields')
            if position > offset:
                formats.append(str(position-offset)+'x')
            formats.append(code); offset=position+size; indexes[index]=slot
        self.unpack = struct.Struct(''.join(formats)).unpack_from
        def make(node):
            kind,content = node
            if kind == 'leaf':
                index=indexes[content]
                return lambda values: values[index]
            if kind == 'array':
                children=tuple(make(child) for child in content)
                return lambda values: tuple(child(values) for child in children)
            children=tuple((name,make(child)) for name,child in content)
            return lambda values: SimpleNamespace(**{name: child(values) for name,child in children})
        self.construct=make(nodes)

    def __call__(self, buffer, offset=0):
        return self.construct(self.unpack(buffer,offset))


def _cstring(value):
    return value.split(b'\0',1)[0]


def _number(value):
    return value if math.isfinite(value) else None


def class_key(value):
    value=_cstring(value)
    if not value: return 0
    result=2166136261
    for byte in value:
        result=((result^byte)*16777619)&0xffffffff
    return (result&0x7fffffff)+1


def tyre_wet(value):
    text=_cstring(value).lower()
    if b'wet' in text or b'rain' in text: return True
    if b'slick' in text or text in (b'soft',b'medium',b'hard'): return False
    return None


def _finite_position(vector):
    return all(math.isfinite(value) for value in vector)


def _valid_orientation(rows):
    for i in range(3):
        if not _finite_position(rows[i]): return False
        for j in range(3):
            dot=rows[i][0]*rows[j][0]+rows[i][1]*rows[j][1]+rows[i][2]*rows[j][2]
            if abs(dot-(1.0 if i==j else 0.0))>.02: return False
    a,b,c=rows
    determinant=a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    return abs(determinant-1.0)<=.03


class FrameProcessor:
    """Stateful port of the released bridge; also accepts offline frame bytes."""
    def __init__(self, layout, *, pid=0, process_identity=0):
        self.layout=layout
        self.pid,self.process_identity=pid,process_identity
        self.output_type=layout.types['SharedMemoryObjectOut']
        self.copy=bytearray(self.output_type.size)
        self.telemetry=self.scoring=self.player_known=self.seen_scoring=False
        self.seen_session=0; self.seen_et=0.0
        self.player_id=-1; self.player_model=b''
        self.game_version=0; self.epoch=0
        self._offsets={name:self.output_type.find(name).offset for name in ('generic','paths','scoring','telemetry')}
        self._types=layout.types
        self._si_offset=self.output_type.find('scoring.scoringInfo').offset
        self._vs_offset=self.output_type.find('scoring.vehScoringInfo').offset
        self._ti_offset=self.output_type.find('telemetry.telemInfo').offset
        self._stream_offset=self.output_type.find('scoring.scoringStream').offset
        self._stream_size=self.output_type.find('scoring.scoringStream').size
        self._size_offset=self.output_type.find('scoring.scoringStreamSize').offset
        self._si_decoder=_Decoder(self._types['ScoringInfoV01'])
        self._vs_decoder=_Decoder(self._types['VehicleScoringInfoV01'])
        self._ti_decoder=_Decoder(self._types['TelemInfoV01'])
        self._traffic_decoder=_Decoder(self._types['TelemInfoV01'],only=('mID','mElapsedTime','mPos'))
        self._header_fields={name:self.output_type.find('telemetry.'+name) for name in ('activeVehicles','playerVehicleIdx','playerHasVehicle')}
        self._events=self.output_type.find('generic.events')
        self._event_struct=struct.Struct('<'+self._scalar_code(self._events.type.element)*self._events.type.count)
        self._version_offset=self.output_type.find('generic.gameVersion').offset
        self._version_struct=struct.Struct('<'+self._scalar_code(self.output_type.find('generic.gameVersion').type))
        self._size_struct=struct.Struct('<'+self._scalar_code(self.output_type.find('scoring.scoringStreamSize').type))
        self._header_structs={name:struct.Struct('<'+self._scalar_code(member.type)) for name,member in self._header_fields.items()}
        self._scoring_rows=[]; self._telemetry_rows=[]

    def _header(self, raw):
        return tuple(self._header_structs[name].unpack_from(raw,self._header_fields[name].offset)[0]
                     for name in ('activeVehicles','playerVehicleIdx','playerHasVehicle'))

    @staticmethod
    def _scalar_code(typ):
        if typ.kind=='bool': return '?'
        return ({1:'b',2:'h',4:'i',8:'q'} if typ.signed else {1:'B',2:'H',4:'I',8:'Q'})[typ.size]

    def _decode_telem(self, raw, index):
        row=self._ti_decoder(raw,self._ti_offset+index*self._types['TelemInfoV01'].size)
        # TelemVect3 decodes to its three named scalar members.
        row.mPos=(row.mPos.x,row.mPos.y,row.mPos.z)
        row.mLocalVel=(row.mLocalVel.x,row.mLocalVel.y,row.mLocalVel.z)
        row.mLocalRot=(row.mLocalRot.x,row.mLocalRot.y,row.mLocalRot.z)
        row.mOri=tuple((v.x,v.y,v.z) for v in row.mOri[:3])
        return row

    def _reset_cache(self):
        self.telemetry=self.scoring=self.player_known=False
        self.player_id=-1; self.player_model=b''; self.epoch+=1

    def _copy_scoring(self, raw, si):
        size=self._types['ScoringInfoV01'].size
        self.copy[self._si_offset:self._si_offset+size]=raw[self._si_offset:self._si_offset+size]
        size=si.mNumVehicles*self._types['VehicleScoringInfoV01'].size
        self.copy[self._vs_offset:self._vs_offset+size]=raw[self._vs_offset:self._vs_offset+size]
        stream_size=self._size_struct.unpack_from(raw,self._size_offset)[0]
        self.copy[self._stream_offset:self._stream_offset+stream_size]=raw[self._stream_offset:self._stream_offset+stream_size]
        self._size_struct.pack_into(self.copy,self._size_offset,stream_size)
        self.copy[self._stream_offset+stream_size]=0
        base=ctypes.addressof(ctypes.c_char.from_buffer(self.copy))
        for name,offset in [('mVehicle',self._vs_offset),('mResultsStream',self._stream_offset)]:
            field=self._types['ScoringInfoV01'].find(name)
            struct.pack_into('<Q',self.copy,self._si_offset+field.offset,base+offset)

    def validate_and_copy(self, raw):
        if len(raw)!=self.output_type.size:
            raise _unavailable('SHARED_LAYOUT_INVALID')
        events=set(self._event_struct.unpack_from(raw,self._events.offset))
        flagged=self.layout.events['SME_UPDATE_SCORING'] in events
        tu=self.layout.events['SME_UPDATE_TELEMETRY'] in events
        si=self._si_decoder(raw,self._si_offset)
        moved=self.seen_scoring and (si.mSession!=self.seen_session or si.mCurrentET!=self.seen_et)
        su=flagged or (moved and si.mNumVehicles>0)
        version=self._version_struct.unpack_from(raw,self._version_offset)[0]
        if version<=0: raise _unavailable('GAME_VERSION_INVALID')
        if self.game_version and self.game_version!=version: raise _unavailable('GAME_VERSION_CHANGED')
        active,index,has_vehicle=self._header(raw)
        stream_size=self._size_struct.unpack_from(raw,self._size_offset)[0]
        if (su and (si.mNumVehicles<0 or si.mNumVehicles>104 or stream_size>=self._stream_size)) or (tu and active>104):
            raise _unavailable('SHARED_LAYOUT_INVALID')
        self.game_version=version
        reset=any(self.layout.events[name] in events for name in ('SME_START_SESSION','SME_END_SESSION','SME_UNLOAD','SME_SHUTDOWN'))
        if su and self.scoring:
            old=self._si_decoder(self.copy,self._si_offset)
            reset=reset or old.mSession!=si.mSession or si.mCurrentET+.001<old.mCurrentET
        incoming=self._decode_telem(raw,index) if tu and has_vehicle and index<active else None
        model=_cstring(incoming.mVehicleModel) if incoming else b''
        if tu:
            if incoming:
                reset=reset or (self.player_known and (self.player_id!=incoming.mID or self.player_model!=model))
                old_active,old_index,old_has=self._header(self.copy)
                if self.telemetry and old_has and old_index<old_active:
                    old=self._decode_telem(self.copy,old_index)
                    reset=reset or incoming.mElapsedTime+.001<old.mElapsedTime
            else:
                reset=reset or self.player_known or self.telemetry
        if reset: self._reset_cache()
        if reset and not flagged: su=False
        # SDK CopySharedMemoryObj: generic always, other sections by events.
        offset=self._offsets['generic']; size=self._types['SharedMemoryGeneric'].size
        self.copy[offset:offset+size]=raw[offset:offset+size]
        if flagged: self._copy_scoring(raw,si)
        if tu:
            for field in self._header_fields.values():
                self.copy[field.offset:field.offset+field.size]=raw[field.offset:field.offset+field.size]
            size=active*self._types['TelemInfoV01'].size
            self.copy[self._ti_offset:self._ti_offset+size]=raw[self._ti_offset:self._ti_offset+size]
        if any(self.layout.events[name] in events for name in ('SME_ENTER','SME_EXIT','SME_SET_ENVIRONMENT')):
            offset=self._offsets['paths']; size=self._types['SharedMemoryPathData'].size
            self.copy[offset:offset+size]=raw[offset:offset+size]
        if su and not flagged: self._copy_scoring(raw,si)
        self.seen_scoring=True; self.seen_session=si.mSession; self.seen_et=si.mCurrentET
        if tu:
            self.telemetry=self.player_known=incoming is not None
            self.player_id=incoming.mID if incoming else -1; self.player_model=model
        if su: self.scoring=True
        self._cached_si=self._si_decoder(self.copy,self._si_offset)
        # Decode only after the lock has been released, and only once per row.
        count,player_index,player_has=self._header(self.copy)
        self._telemetry_rows=[]
        for i in range(count):
            if self.telemetry and player_has and i==player_index:
                row=self._decode_telem(self.copy,i)
            else:
                row=self._traffic_decoder(self.copy,self._ti_offset+i*self._types['TelemInfoV01'].size)
                row.mPos=(row.mPos.x,row.mPos.y,row.mPos.z)
            self._telemetry_rows.append(row)
        self._scoring_rows=[self._vs_decoder(self.copy,self._vs_offset+i*self._types['VehicleScoringInfoV01'].size)
                            for i in range(max(0,self._cached_si.mNumVehicles))]
        for row in self._scoring_rows:
            row.mLocalVel=(row.mLocalVel.x,row.mLocalVel.y,row.mLocalVel.z)
        return tu,su

    def process_frame(self, raw):
        tu,su=self.validate_and_copy(raw)
        return self.snapshot(tu,su)

    def traffic(self, player):
        unavailable={'status':'UNAVAILABLE','player':None,'vehicles':[], 'source':'TELEMETRY','complete':False}
        nt,ns=len(self._telemetry_rows),self._cached_si.mNumVehicles
        age=player.mElapsedTime-self._cached_si.mCurrentET if player else 0
        if (not player or not self.telemetry or not self.scoring or nt<1 or nt>104 or ns<1 or ns>104
            or not math.isfinite(player.mElapsedTime) or not _finite_position(player.mPos)
            or not _valid_orientation(player.mOri) or not math.isfinite(age) or age<-.25 or age>2.0):
            return unavailable
        tids=[row.mID for row in self._telemetry_rows]; sids=[row.mID for row in self._scoring_rows]
        if any(i<0 for i in tids) or len(set(tids))!=len(tids) or any(i<0 for i in sids) or len(set(sids))!=len(sids):
            return unavailable
        players=[row for row in self._scoring_rows if row.mIsPlayer]
        if len(players)!=1 or players[0].mID!=player.mID: return unavailable
        by_id={row.mID:row for row in self._telemetry_rows}
        complete,vehicles=True,[]
        for row in self._scoring_rows:
            if row.mID!=player.mID and row.mInPits: continue
            t=by_id.get(row.mID)
            if not t or not math.isfinite(t.mElapsedTime) or abs(player.mElapsedTime-t.mElapsedTime)>.25 or not _finite_position(t.mPos):
                complete=False; continue
            if row.mID==player.mID: continue
            if row.mControl not in (1,2): complete=False; continue
            if row.mInPits: continue
            vehicles.append({'vehicle_id':t.mID,'telemetry_et':_number(t.mElapsedTime),'position_m':list(t.mPos),
                             'in_pits':False,'control_code':row.mControl})
        scored_ids=set(sids)
        if any(i not in scored_ids for i in tids): complete=False
        return {'status':'AVAILABLE','player':{'vehicle_id':player.mID,'telemetry_et':_number(player.mElapsedTime),
                                               'position_m':list(player.mPos),'orientation':[list(row) for row in player.mOri]},
                'vehicles':vehicles,'source':'TELEMETRY','complete':complete}

    def scoring_cars(self, aligned):
        rows=self._scoring_rows
        if not aligned or self._cached_si.mNumVehicles<0 or self._cached_si.mNumVehicles>104: return None
        ids=[row.mID for row in rows]
        if any(i<0 for i in ids) or len(set(ids))!=len(ids): return None
        groups={}
        classes=[class_key(row.mVehicleClass) for row in rows]
        for row,cls in zip(rows,classes): groups.setdefault(cls,[]).append(row.mPlace)
        valid_groups={cls:bool(cls and all(places) and len(set(places))==len(places)) for cls,places in groups.items()}
        results=[]
        for row,cls in zip(rows,classes):
            places=groups[cls]
            valid=valid_groups[cls] and row.mPlace>0
            result={'vehicle_id':row.mID,'class_id':cls or None,
                    'class_position':1+sum(place<row.mPlace for place in places) if valid else None,
                    'overall_position':row.mPlace if row.mPlace>0 else None}
            def num(key,condition,value): result[key]=_number(value) if condition else None
            num('total_laps',row.mTotalLaps>=0,row.mTotalLaps)
            num('lap_distance_m',True,row.mLapDist)
            num('last_lap_time',row.mLastLapTime>0,row.mLastLapTime)
            num('best_lap_time',row.mBestLapTime>0,row.mBestLapTime)
            num('lap_start_et',row.mLapStartET>=0,row.mLapStartET)
            num('speed_mps',True,math.hypot(*row.mLocalVel))
            num('path_lateral_m',True,row.mPathLateral); num('track_edge_m',True,row.mTrackEdge)
            num('time_behind_next_s',row.mTimeBehindNext>=0,row.mTimeBehindNext)
            num('laps_behind_next',row.mLapsBehindNext>=0,row.mLapsBehindNext)
            num('time_behind_leader_s',row.mTimeBehindLeader>=0,row.mTimeBehindLeader)
            num('laps_behind_leader',row.mLapsBehindLeader>=0,row.mLapsBehindLeader)
            num('pit_stops',row.mNumPitstops>=0,row.mNumPitstops)
            result.update(finish_status=row.mFinishStatus,control_code=row.mControl,
                          sector=row.mSector if 0<=row.mSector<=2 else None,in_pits=bool(row.mInPits))
            results.append(result)
        return results

    def snapshot(self, tu, su):
        active,index,has_vehicle=self._header(self.copy)
        t=self._telemetry_rows[index] if self.telemetry and has_vehicle and index<active else None
        si=self._cached_si
        age=t.mElapsedTime-si.mCurrentET if t else 0
        aligned=bool(self.scoring and t and math.isfinite(age) and -.25<=age<=2.0)
        candidates=[row for row in self._scoring_rows if t and row.mID==t.mID and row.mIsPlayer] if aligned else []
        v=candidates[0] if len(candidates)==1 else None
        classPosition,classCars=0,0
        classOrderValid=bool(v and v.mPlace>0 and _cstring(v.mVehicleClass))
        if classOrderValid:
            occupied=set()
            for other in self._scoring_rows:
                if _cstring(other.mVehicleClass)!=_cstring(v.mVehicleClass): continue
                classCars+=1
                if not other.mPlace or other.mPlace in occupied: classOrderValid=False
                occupied.add(other.mPlace)
                if other.mPlace<=v.mPlace: classPosition+=1
        events=set(self._event_struct.unpack_from(self.copy,self._events.offset))
        session=si.mSession if self.scoring else 'unknown'
        out={'game_version':self.game_version,
             'events':[name for name in EVENT_NAMES if self.layout.events[name] in events],
             'session_id':f'{self.pid}:{self.process_identity}:{self.epoch}:{session}',
             'telemetry_updated':bool(tu),'scoring_updated':bool(su),'vehicle_id':t.mID if t else None}
        out['telemetry_et']=_number(t.mElapsedTime) if t else None
        out['scoring_et']=_number(si.mCurrentET) if self.scoring else None
        out['lap_start_et']=_number(t.mLapStartET) if t else None
        out['scoring_lap_start_et']=_number(v.mLapStartET) if v else None
        out['total_laps']=_number(v.mTotalLaps) if v else None
        out['last_lap_time']=_number(v.mLastLapTime) if v else None
        out['best_lap_time']=_number(v.mBestLapTime) if v  and  v.mBestLapTime>0 else None
        out['outstanding_penalties']=_number(v.mNumPenalties) if v  and  v.mNumPenalties>=0 else None
        out['fuel_l']=_number(t.mFuel) if t  and  t.mFuel>=0 else None
        out['virtual_energy_raw']=_number(t.mVirtualEnergy) if t  and  t.mVirtualEnergy>=0 else None
        out['fuel_capacity_l']=_number(t.mFuelCapacity) if t  and  t.mFuelCapacity>0 else None
        out['lap_distance_m']=_number(v.mLapDist) if v else None
        out['track_length_m']=_number(si.mLapDist) if aligned  and  si.mLapDist>0 else None
        out['session_type_code']=_number(si.mSession) if aligned else None
        out['session_phase']=_number(si.mGamePhase) if aligned else None
        out['session_remaining_s']=_number(si.mSessionTimeRemaining) if aligned  and  si.mSessionTimeRemaining>=0 else None
        out['session_end_et']=_number(si.mEndET) if aligned  and  si.mEndET>=0 else None
        out['session_max_laps']=_number(si.mMaxLaps) if aligned  and  si.mMaxLaps>0  and  si.mMaxLaps<2147483647 else None
        out['overall_position']=_number(v.mPlace) if v  and  v.mPlace>0 else None
        out['class_position']=_number(classPosition) if classOrderValid else None
        out['class_vehicle_count']=_number(classCars) if classOrderValid else None
        out['vehicle_count']=_number(si.mNumVehicles) if aligned else None
        out['control_code']=_number(v.mControl) if v else None
        out['primary_flag']=_number(v.mFlag) if v else None
        out['yellow_flag_state']=_number(si.mYellowFlagState) if aligned else None
        out['sector']=_number(v.mSector) if v  and  v.mSector>=0  and  v.mSector<=2 else None
        out['pit_state']=_number(v.mPitState) if v else None
        out['finish_status']=_number(v.mFinishStatus) if v else None
        out['under_yellow']=bool(v.mUnderYellow) if v else None
        out['in_garage_stall']=bool(v.mInGarageStall) if v else None
        out['rain_ratio']=_number(si.mRaining) if aligned  and  si.mRaining>=0  and  si.mRaining<=1 else None
        out['track_wetness_ratio']=_number(si.mAvgPathWetness) if aligned  and  si.mAvgPathWetness>=0  and  si.mAvgPathWetness<=1 else None
        out['ambient_temp_c']=_number(si.mAmbientTemp) if aligned  and  si.mAmbientTemp>-273.15 else None
        out['track_temp_c']=_number(si.mTrackTemp) if aligned  and  si.mTrackTemp>-273.15 else None
        out['lap_invalidated']=bool(t.mLapInvalidated) if t else None
        out['count_lap_flag']=_number(v.mCountLapFlag) if v else None
        out['track_limits_steps']=_number(t.mTrackLimitsSteps) if t else None
        out['in_pits']=bool(v.mInPits) if v else None
        out['realtime']=bool(si.mInRealtime) if self.scoring else None
        out['paused']=bool(si.mGamePhase == 9) if self.scoring else None
        out['speed_mps']=_number(math.hypot(*t.mLocalVel)) if t else None
        out['throttle_ratio']=_number(t.mUnfilteredThrottle) if t else None
        out['brake_ratio']=_number(t.mUnfilteredBrake) if t else None
        out['steering_ratio']=_number(t.mUnfilteredSteering) if t else None
        out['gear']=_number(t.mGear) if t  and  t.mGear>=-1 else None
        out['engine_rpm']=_number(t.mEngineRPM) if t  and  t.mEngineRPM>=0 else None
        out['engine_water_temp_c']=_number(t.mEngineWaterTemp) if t  and  t.mEngineWaterTemp>-273.15 else None
        out['engine_oil_temp_c']=_number(t.mEngineOilTemp) if t  and  t.mEngineOilTemp>-273.15 else None
        out['overheating']=bool(t.mOverheating) if t else None
        out['parts_detached']=bool(t.mDetached) if t else None
        out['last_impact_et']=_number(t.mLastImpactET) if t  and  t.mLastImpactET>0 else None
        out['last_impact_magnitude']=_number(t.mLastImpactMagnitude) if t  and  t.mLastImpactMagnitude>=0 else None
        out['yaw_rate']=_number(t.mLocalRot[1]) if t else None
        out['steering_rad']=_number(t.mUnfilteredSteering*t.mPhysicalSteeringWheelRange*0.008726646259971648) if t  and  t.mPhysicalSteeringWheelRange>0 else None
        out['brake_bias']=_number(100.*(1.-t.mRearBrakeBias)) if t  and  t.mRearBrakeBias>=0  and  t.mRearBrakeBias<=1 else None
        out['tc_setting']=_number(t.mTC) if t  and  t.mTCMax>0  and  t.mTC<=t.mTCMax else None
        out['abs_setting']=_number(t.mABS) if t  and  t.mABSMax>0  and  t.mABS<=t.mABSMax else None
        out['front_tyre_compound']=_number(t.mFrontTireCompoundIndex) if t else None
        out['rear_tyre_compound']=_number(t.mRearTireCompoundIndex) if t else None
        out['class_id']=_number(class_key(v.mVehicleClass)) if v and v.mVehicleClass[0] else None
        out['pit_box_lap_distance_m']=_number(v.mPitLapDist) if v  and  v.mPitLapDist>0  and  si.mLapDist>v.mPitLapDist else None
        out['time_gap_ahead_s']=_number(t.mTimeGapCarAhead) if t  and  t.mTimeGapCarAhead>0 else None
        out['time_gap_behind_s']=_number(t.mTimeGapCarBehind) if t  and  t.mTimeGapCarBehind>0 else None
        out['place_gap_ahead_s']=_number(t.mTimeGapPlaceAhead) if t  and  t.mTimeGapPlaceAhead>0 else None
        out['place_gap_behind_s']=_number(t.mTimeGapPlaceBehind) if t  and  t.mTimeGapPlaceBehind>0 else None
        out['start_light']=_number(si.mStartLight) if aligned else None
        out['start_red_lights']=_number(si.mNumRedLights) if aligned else None
        out['track_limits_steps_per_penalty']=_number(si.mTrackLimitsStepsPerPenalty) if aligned  and  si.mTrackLimitsStepsPerPenalty>0 else None
        out['clutch_ratio']=_number(t.mUnfilteredClutch) if t  and  t.mUnfilteredClutch>=0  and  t.mUnfilteredClutch<=1 else None
        out['abs_active']=bool(t.mABSActive) if t else None
        out['tc_active']=bool(t.mTCActive) if t else None
        out['speed_limiter_active']=bool(t.mSpeedLimiterActive) if t else None

        out.update(car_name=_cstring(t.mVehicleName).decode('utf-8') if t else None,
                   vehicle_model=_cstring(t.mVehicleModel).decode('utf-8') if t else None,
                   track_name=_cstring(t.mTrackName).decode('utf-8') if t else None,
                   sector_flags=[flag if -1<=flag<=7 else None for flag in si.mSectorFlag[:3]] if aligned else None,
                   front_tyre_wet=tyre_wet(t.mFrontTireCompoundName) if t else None,
                   rear_tyre_wet=tyre_wet(t.mRearTireCompoundName) if t else None)
        wheels=[]
        for index,position in enumerate(('FL','FR','RL','RR')):
            wheel=t.mWheel[index] if t else None
            wheels.append({'position':position,
                'pressure_kpa':_number(wheel.mPressure) if wheel and wheel.mPressure>=0 else None,
                'brake_temp_c':_number(wheel.mBrakeTemp) if wheel and wheel.mBrakeTemp>-273.15 else None,
                'suspension_deflection_m':_number(wheel.mSuspensionDeflection) if wheel else None,
                'ride_height_m':_number(wheel.mRideHeight) if wheel else None,
                'wear_fraction':_number(wheel.mWear) if wheel and 0<=wheel.mWear<=1 else None,
                'flat':bool(wheel.mFlat) if wheel else None,'detached':bool(wheel.mDetached) if wheel else None,
                'temperature_c':[_number(value-273.15) if value>0 else None for value in wheel.mTemperature[:3]] if wheel else [None]*3})
        out['wheels']=wheels
        out['traffic']=self.traffic(t)
        out['scoring_cars']=self.scoring_cars(aligned)
        return out


class _Windows:
    """Open existing game objects only; no mapping/event creation APIs."""
    def __init__(self):
        from ctypes import wintypes as w
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        class ProcessEntry(ctypes.Structure):
            _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('th32ProcessID',w.DWORD),
                      ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',w.DWORD),('cntThreads',w.DWORD),
                      ('th32ParentProcessID',w.DWORD),('pcPriClassBase',w.LONG),('dwFlags',w.DWORD),('szExeFile',w.WCHAR*260)]
        class MemoryInfo(ctypes.Structure):
            _fields_=[('BaseAddress',ctypes.c_void_p),('AllocationBase',ctypes.c_void_p),
                      ('AllocationProtect',w.DWORD),('PartitionId',w.WORD),('RegionSize',ctypes.c_size_t),
                      ('State',w.DWORD),('Protect',w.DWORD),('Type',w.DWORD)]
        self.ProcessEntry,self.MemoryInfo=ProcessEntry,MemoryInfo
        signatures={
            'CreateToolhelp32Snapshot':([w.DWORD,w.DWORD],w.HANDLE),
            'Process32FirstW':([w.HANDLE,ctypes.POINTER(ProcessEntry)],w.BOOL),
            'Process32NextW':([w.HANDLE,ctypes.POINTER(ProcessEntry)],w.BOOL),
            'OpenProcess':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            'GetProcessTimes':([w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4,w.BOOL),
            'OpenFileMappingW':([w.DWORD,w.BOOL,w.LPCWSTR],w.HANDLE),
            'OpenEventW':([w.DWORD,w.BOOL,w.LPCWSTR],w.HANDLE),
            'MapViewOfFile':([w.HANDLE,w.DWORD,w.DWORD,w.DWORD,ctypes.c_size_t],ctypes.c_void_p),
            'VirtualQuery':([ctypes.c_void_p,ctypes.POINTER(MemoryInfo),ctypes.c_size_t],ctypes.c_size_t),
            'WaitForSingleObject':([w.HANDLE,w.DWORD],w.DWORD),
            'UnmapViewOfFile':([ctypes.c_void_p],w.BOOL),'CloseHandle':([w.HANDLE],w.BOOL),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.kernel,name); fn.argtypes,fn.restype=args,result

    def game_pids(self):
        snapshot=self.kernel.CreateToolhelp32Snapshot(2,0)
        if snapshot in (None,ctypes.c_void_p(-1).value): return []
        try:
            entry=self.ProcessEntry(); entry.dwSize=ctypes.sizeof(entry)
            result=[]
            ok=self.kernel.Process32FirstW(snapshot,ctypes.byref(entry))
            while ok:
                if entry.szExeFile.lower()=='le mans ultimate.exe': result.append(entry.th32ProcessID)
                ok=self.kernel.Process32NextW(snapshot,ctypes.byref(entry))
            return result
        finally:
            self.close(snapshot)

    def open_process(self,pid):
        return self.kernel.OpenProcess(0x100000|0x1000,False,pid)

    def process_identity(self,process):
        from ctypes import wintypes as w
        times=[w.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(process,*(ctypes.byref(t) for t in times)): return None
        return (times[0].dwHighDateTime<<32)|times[0].dwLowDateTime

    def open_mapping(self,name,access):
        return self.kernel.OpenFileMappingW(access,False,name)

    def open_event(self,name,access):
        return self.kernel.OpenEventW(access,False,name)

    def map_view(self,handle,access,size):
        return self.kernel.MapViewOfFile(handle,access,0,0,size)

    def region_size(self,address):
        info=self.MemoryInfo()
        if self.kernel.VirtualQuery(address,ctypes.byref(info),ctypes.sizeof(info))!=ctypes.sizeof(info): return 0
        if info.State!=0x1000 or info.Protect & (0x100|0x01): return 0
        base=info.BaseAddress or 0
        return max(0,base+info.RegionSize-address) if base<=address else 0

    def wait(self,handle,timeout):
        return self.kernel.WaitForSingleObject(handle,timeout)

    def unmap(self,address):
        self.kernel.UnmapViewOfFile(address)

    def close(self,handle):
        self.kernel.CloseHandle(handle)


class PythonSharedMemorySource:
    supports_readonly_pit_rest=True

    def __init__(self, *, sdk_dir=None, api=None):
        self._sdk_source=('EXPLICIT' if sdk_dir is not None else 'ENVIRONMENT' if os.environ.get('LMU_SDK_DIR','').strip() else 'DEFAULT')
        self.sdk_dir=resolve_sdk_dir(sdk_dir)
        self._api=api
        self._dll=None; self._layout=None; self._processor=None
        self._handles=[]; self._views=[]; self._handle=None
        self._connection_id=None
        self._guard=threading.RLock()

    def configuration(self):
        return sdk_configuration(self.sdk_dir,source=self._sdk_source)

    def _load(self):
        _platform_check()
        try:
            layout=load_layout(self.sdk_dir)
        except LayoutUnstable as exc:
            raise _unavailable('SDK_UNAVAILABLE') from exc
        except LayoutUnsupported as exc:
            raise _unsupported_sdk() from exc
        except (OSError,UnicodeError) as exc:
            raise _unavailable('SDK_UNAVAILABLE') from exc
        if self._handle and self._layout.fingerprint!=layout.fingerprint:
            self.close()
        self._dll=load_lock_dll(existing=self._dll)
        self._layout=layout
        types=layout.types
        return {'bridge_version':1,'pointer_size':8,'header_fingerprint':layout.fingerprint,
                'sizes':{name:types[name].size for name in ('SharedMemoryLayout','SharedMemoryObjectOut','TelemInfoV01','VehicleScoringInfoV01','ScoringInfoV01')},
                'offsets':{'telemetry':types['SharedMemoryObjectOut'].find('telemetry').offset,
                           'scoring':types['SharedMemoryObjectOut'].find('scoring').offset,
                           'telemInfo':types['SharedMemoryTelemetryData'].find('telemInfo').offset,
                           'lap_invalidated':types['TelemInfoV01'].find('mLapInvalidated').offset,
                           'count_lap_flag':types['VehicleScoringInfoV01'].find('mCountLapFlag').offset},
                'lock_abi':{'size':layout.lock.size,'busy_offset':layout.lock.find('busy').offset}}

    def probe(self):
        with self._guard:
            abi=self._load()
            return {'provider':'LMU_SHARED_MEMORY','state':'READY','attached':bool(self._handle),
                    'sdk_dir':str(self.sdk_dir),'sdk_configuration':self.configuration(),
                    'game_version_support':'UNVERIFIED_INSTALLED_SDK_ONLY','abi':abi}

    def prepare(self, *, retry=False):
        return self.probe()

    def _owned_handle(self,handle,code):
        if not handle: raise _unavailable(code)
        self._handles.append(handle)
        return handle

    def open(self):
        with self._guard:
            diagnostics=self.prepare()
            if self._handle: return {**diagnostics,'state':'CONNECTED','attached':True}
            if self._api is None: self._api=_Windows()
            api=self._api
            try:
                pids=api.game_pids()
                if not pids: raise _unavailable('GAME_NOT_RUNNING')
                if len(pids)!=1: raise _unavailable('MULTIPLE_GAME_PROCESSES')
                process=self._owned_handle(api.open_process(pids[0]),'GAME_PROCESS_UNAVAILABLE')
                identity=api.process_identity(process)
                if identity is None: raise _unavailable('GAME_PROCESS_UNAVAILABLE')
                mapping=self._owned_handle(api.open_mapping('LMU_Data',4),'SHARED_MEMORY_UNAVAILABLE')
                lock_mapping=api.open_mapping('LMU_SharedMemoryLockData',0xf001f)
                if lock_mapping: self._handles.append(lock_mapping)
                lock_event=api.open_event('LMU_SharedMemoryLockEvent',0x100000|2)
                if lock_event: self._handles.append(lock_event)
                if not lock_mapping or not lock_event: raise _unavailable('SHARED_LOCK_UNAVAILABLE')
                hold=self._owned_handle(api.open_event('LMU_Data_HoldEvent',0x100000),'SHARED_EVENTS_UNAVAILABLE')
                data=self._owned_handle(api.open_event('LMU_Data_DataEvent',0x100000),'SHARED_EVENTS_UNAVAILABLE')
                view=api.map_view(mapping,4,self._layout.root.size)
                if not view: raise _unavailable('SHARED_LAYOUT_UNAVAILABLE')
                self._views.append(view)
                if api.region_size(view)<self._layout.root.size: raise _unavailable('SHARED_LAYOUT_UNAVAILABLE')
                lock_view=api.map_view(lock_mapping,0xf001f,self._layout.lock.size)
                if not lock_view: raise _unavailable('SHARED_LOCK_UNAVAILABLE')
                self._views.append(lock_view)
                if api.region_size(lock_view)<self._layout.lock.size: raise _unavailable('SHARED_LOCK_UNAVAILABLE')
                if api.wait(process,0)!=258: raise _unavailable('GAME_EXITED')
                self._raw_buffer=ctypes.create_string_buffer(self._layout.root.size)
                self._busy=ctypes.cast(lock_view+self._layout.lock.find('busy').offset,ctypes.POINTER(ctypes.c_long))
                self._waiters=ctypes.cast(lock_view+self._layout.lock.find('waiters').offset,ctypes.POINTER(ctypes.c_long))
                self._view,self._lock_event,self._hold,self._data=view,lock_event,hold,data
                self._processor=FrameProcessor(self._layout,pid=pids[0],process_identity=identity)
                self._connection_id=uuid.uuid4().hex
                self._handle=process
                return {**diagnostics,'state':'CONNECTED','attached':True}
            except BaseException:
                self.close()
                raise

    def _check_headers(self):
        try:
            current=load_layout(self.sdk_dir)
        except LayoutUnstable as exc:
            raise _unavailable('SDK_UNAVAILABLE') from exc
        except LayoutUnsupported as exc:
            raise _unsupported_sdk() from exc
        except (OSError,UnicodeError) as exc:
            raise _unavailable('SDK_UNAVAILABLE') from exc
        if current.fingerprint!=self._layout.fingerprint:
            raise _unavailable('ABI_MISMATCH')

    @staticmethod
    def _timeout(timeout_ms):
        if isinstance(timeout_ms,bool) or not isinstance(timeout_ms,int) or not 0<=timeout_ms<=1000:
            raise ValueError('대기 시간은 0~1000밀리초 정수여야 합니다.')

    def _copy_next_raw(self,timeout_ms):
        if not self._handle: raise _unavailable('NOT_OPEN')
        self._check_headers()
        api=self._api
        if api.wait(self._handle,0)!=258: raise _unavailable('GAME_EXITED')
        budget=timeout_ms//2
        if api.wait(self._hold,budget)!=0 or api.wait(self._data,budget)!=0:
            if api.wait(self._handle,0)!=258: raise _unavailable('GAME_EXITED')
            return None
        if not self._dll.lmu_copy_locked(self._busy,self._waiters,self._lock_event,self._view,self._raw_buffer,self._layout.root.size,1000):
            return None
        member=self._layout.root.find('data')
        return self._raw_buffer.raw[member.offset:member.offset+member.size]

    def capture_raw(self,timeout_ms=200):
        """For the local developer capture tool; contains private game bytes."""
        self._timeout(timeout_ms)
        with self._guard:
            try:
                raw=self._copy_next_raw(timeout_ms)
                if raw is not None and self._api.wait(self._handle,0)!=258: raise _unavailable('GAME_EXITED')
                return raw
            except LmuLiveUnavailable:
                self.close(); raise

    def read(self,timeout_ms=200):
        self._timeout(timeout_ms)
        with self._guard:
            try:
                raw=self._copy_next_raw(timeout_ms)
                if raw is None: return None
                tu,su=self._processor.validate_and_copy(raw)
                # Released lmu_read validates first, then checks process exit,
                # then serializes. Preserve that error precedence.
                if self._api.wait(self._handle,0)!=258: raise _unavailable('GAME_EXITED')
                result=self._processor.snapshot(tu,su)
                result['session_id']=self._connection_id+':'+result['session_id']
                return result
            except LmuLiveUnavailable:
                self.close(); raise
            except (ValueError,TypeError,KeyError,struct.error) as exc:
                self.close()
                raise _unavailable('SHARED_LAYOUT_INVALID') from exc

    def close(self):
        with self._guard:
            if self._api:
                for view in reversed(self._views): self._api.unmap(view)
                for handle in reversed(self._handles): self._api.close(handle)
            self._views=[]; self._handles=[]; self._handle=None
            self._connection_id=None; self._processor=None

    def __enter__(self):
        self.open(); return self

    def __exit__(self,*args):
        self.close()
