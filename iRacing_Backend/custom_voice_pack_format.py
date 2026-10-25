"""Portable v3 role packs; v1 WAV/v2 Opus compatible. Shared standard-library contract."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import wave
import zipfile

FORMAT = 'racingcoach-custom-voice-pack'
VERSION = 3
ROLES = {'spotter', 'race', 'strategy', 'coach'}
MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 100000
MAX_ENTRIES = 200000
MAX_MANIFEST = 128 * 1024 * 1024
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$')

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def binding_signature(role, text):
    return hashlib.sha256(json.dumps([role,text],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def validate_baseline(value, roles):
    if not isinstance(value,dict) or set(value)!=set(roles) or set(value)-ROLES:raise ValueError('제작 당시 역할별 음성 목록을 확인하세요.')
    total=0
    for items in value.values():
        if not isinstance(items,list) or len(items)!=len(set(x for x in items if isinstance(x,str))) or any(not isinstance(x,str) or not re.fullmatch('[a-f0-9]{64}',x) for x in items):raise ValueError('제작 당시 음성 목록의 확인값이 올바르지 않습니다.')
        total+=len(items)
    if total>MAX_ENTRIES:raise ValueError('제작 당시 목록이 너무 큽니다.')
    return value

def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('팩 내부 파일 경로가 올바르지 않아.')
    parts = name.split('/')
    if PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') or p.rstrip(' .') != p for p in parts):
        raise ValueError('팩 폴더 밖을 가리키는 경로는 사용할 수 없어.')
    if any(p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for p in parts):
        raise ValueError('예약된 파일 이름이야.')
    return name

def wav_info(content):
    try:
        with wave.open(io.BytesIO(content), 'rb') as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1,2,24000,'NONE'):
                raise ValueError('음성은 24kHz 모노 PCM16 WAV여야 해.')
            frames = w.getnframes()
            if not 12000 <= frames <= 24000 * 60 or len(w.readframes(frames)) != frames * 2:
                raise ValueError('음성 길이 또는 WAV 데이터가 올바르지 않아.')
            return frames / 24000
    except (wave.Error, EOFError) as e: raise ValueError('WAV 파일을 읽을 수 없어.') from e


def _crc_entry(value):
    value <<= 24
    for _ in range(8):value=((value<<1)^0x04c11db7 if value&0x80000000 else value<<1)&0xffffffff
    return value

_CRC=tuple(_crc_entry(i) for i in range(256))

def opus_info(content):
    """Validate the Ogg pages, single mono Opus stream and exact granule duration.

    This is structural verification, not a substitute for a codec decoder. The
    player additionally decodes each unique file during installation.
    """
    offset=sequence=packets=0;serial=None;pending=bytearray();skip=None;last=0;ended=False;audio_packets=0
    while offset<len(content):
        if ended or offset+27>len(content) or content[offset:offset+5]!=b'OggS\0':raise ValueError('Ogg 파일이 잘렸거나 스트림 형식이 올바르지 않아.')
        flags=content[offset+5];granule,stream,number,checksum=struct.unpack_from('<QIII',content,offset+6)
        segments=content[offset+26];header=offset+27+segments
        if flags&~7 or number!=sequence or bool(flags&1)!=bool(pending) or (sequence==0 and flags!=2) or (sequence>0 and flags&2):raise ValueError('Ogg 페이지 연결이 올바르지 않아.')
        if serial is None:serial=stream
        if stream!=serial or header>len(content):raise ValueError('한 개의 Ogg Opus 음성 스트림만 사용할 수 있어.')
        sizes=content[offset+27:header];end=header+sum(sizes)
        if end>len(content):raise ValueError('Ogg 음성 데이터가 잘렸어.')
        raw=bytearray(content[offset:end]);raw[22:26]=b'\0'*4;value=0
        for byte in raw:value=((value<<8)&0xffffffff)^_CRC[((value>>24)^byte)&255]
        if value!=checksum:raise ValueError('Ogg 페이지 검사가 실패했어.')
        cursor=header
        for size in sizes:
            pending.extend(content[cursor:cursor+size]);cursor+=size
            if len(pending)>65536:raise ValueError('Opus 패킷이 너무 커.')
            if size==255:continue
            packet=bytes(pending);pending.clear()
            if packets==0:
                if len(packet)!=19 or packet[:8]!=b'OpusHead' or packet[8]!=1 or packet[9]!=1 or packet[18]!=0 or struct.unpack_from('<I',packet,12)[0]!=24000:raise ValueError('24kHz 모노 Ogg Opus 음성만 사용할 수 있어.')
                skip=struct.unpack_from('<H',packet,10)[0]
            elif packets==1:
                if not packet.startswith(b'OpusTags') or len(packet)<16:raise ValueError('Opus 설명 헤더가 올바르지 않아.')
            else:
                if not packet:raise ValueError('빈 Opus 음성 패킷이 있어.')
                audio_packets+=1
            packets+=1
        if granule!=0xffffffffffffffff:
            if granule<last or granule>48000*60+65535:raise ValueError('Opus 재생 시간이 올바르지 않아.')
            last=granule
        ended=bool(flags&4);offset=end;sequence+=1
    if not ended or pending or skip is None or not audio_packets or not .5<=(last-skip)/48000<=60:raise ValueError('Opus 끝 페이지 또는 재생 시간이 올바르지 않아.')
    return (last-skip)/48000

def audio_mime(row):return 'audio/ogg;codecs=opus' if row['audio'].endswith('.ogg') else 'audio/wav'

def limits(manifest):
    return (512*1024*1024,50000,50000,16*1024*1024) if manifest['schemaVersion']==1 else (MAX_BYTES,MAX_ENTRIES,MAX_FILES,MAX_MANIFEST)

def validate_manifest(m):
    if not isinstance(m, dict) or m.get('format') != FORMAT or type(m.get('schemaVersion')) is not int or m['schemaVersion'] not in (1,2,3):
        raise ValueError('지원하지 않는 커스텀팩 규격이야.')
    if any(not isinstance(m.get(k),str) or not ID.fullmatch(m[k]) for k in ('packId','version')):
        raise ValueError('팩 ID나 버전이 올바르지 않아.')
    if not isinstance(m.get('name'), str) or not 1 <= len(m['name']) <= 100: raise ValueError('팩 이름을 확인해줘.')
    entries = m.get('entries')
    if not isinstance(entries,list) or not 1 <= len(entries) <= limits(m)[1]: raise ValueError('팩 음성 목록이 올바르지 않아.')
    ids, bindings, audio_files = set(), set(), {}
    for r in entries:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not ID.fullmatch(r['id']) or r['id'] in ids: raise ValueError('음성 ID가 중복되거나 올바르지 않아.')
        ids.add(r['id'])
        if not isinstance(r.get('role'),str) or r['role'] not in ROLES: raise ValueError('지원하지 않는 음성 역할이야.')
        for key in ('sourceText','text','spoken'):
            if not isinstance(r.get(key),str) or not 1 <= len(r[key]) <= 2000: raise ValueError('대사 정보를 확인해줘.')
        binding=(r['role'],r['sourceText'])
        if binding in bindings: raise ValueError('같은 역할·상황에 음성이 중복돼.')
        bindings.add(binding)
        path=safe_name(r.get('audio'))
        extension=path.rsplit('.',1)[-1]
        if extension not in ({'wav'} if m['schemaVersion']==1 else {'wav','ogg'}) or not re.fullmatch(r'clips/[a-f0-9]{64}\.(wav|ogg)',path) or path != f"clips/{r.get('sha256')}.{extension}": raise ValueError('음성 파일 해시와 경로가 맞지 않아.')
        if type(r.get('duration')) not in (int,float) or not .5 <= r['duration'] <= 60: raise ValueError('음성 길이가 올바르지 않아.')
        metadata=(r['sha256'],r['duration'])
        if path in audio_files and audio_files[path]!=metadata:raise ValueError('같은 음성 파일의 길이 정보가 서로 달라.')
        audio_files[path]=metadata
    if len(audio_files)>limits(m)[2]:raise ValueError('팩 음성 파일 수 제한을 초과했어.')
    if m['schemaVersion']==3:
        targets=m.get('targets')
        if not isinstance(targets,list) or any(not isinstance(r,str) for r in targets) or len(targets)!=len(set(targets)) or set(targets)!={r['role'] for r in entries}:raise ValueError('팩 적용 역할과 담긴 음성의 역할이 다릅니다.')
        if 'catalogBaseline' in m:validate_baseline(m['catalogBaseline'],targets)
    return m

def inspect_pack(source,audio_validator=None):
    """Validate every byte before installation. Does not extract anything."""
    source=Path(source)
    if source.is_dir():
        root=source.resolve(); manifest=root/'manifest.json'
        if manifest.is_symlink() or manifest.stat().st_size>MAX_MANIFEST: raise ValueError('팩 설명 파일을 확인해줘.')
        def read(name):
            file=root/safe_name(name)
            if file.is_symlink() or not file.resolve().is_relative_to(root): raise ValueError('팩 밖의 파일을 읽을 수 없어.')
            if file.stat().st_size>16*1024*1024: raise ValueError('음성 파일이 너무 커.')
            return file.read_bytes()
        m=json.loads(manifest.read_text(encoding='utf-8-sig')); validate_manifest(m)
        total=manifest.stat().st_size;maximum,_,_,manifest_limit=limits(m)
        if total>manifest_limit:raise ValueError('팩 설명 파일이 너무 커.')
        seen=set()
        for row in m['entries']:
            if row['audio'] in seen:continue
            data=read(row['audio']);total+=len(data);seen.add(row['audio'])
            if total>maximum: raise ValueError('팩 크기 제한을 초과했어.')
            check_audio(row,data)
            if audio_validator:audio_validator(row,data)
        return m
    if source.stat().st_size>MAX_BYTES: raise ValueError('팩 크기 제한을 초과했어.')
    with zipfile.ZipFile(source) as z:
        files=z.infolist()
        if len(files)>MAX_FILES+1 or sum(f.file_size for f in files)>MAX_BYTES: raise ValueError('팩 크기나 파일 수 제한을 초과했어.')
        names=set()
        for f in files:
            if f.file_size>(MAX_MANIFEST if f.filename=='manifest.json' else 16*1024*1024):raise ValueError('팩 안의 개별 파일이 너무 커.')
            safe_name(f.filename)
            if f.filename.casefold() in names or f.flag_bits & 1 or (f.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('중복·암호화·링크 파일은 사용할 수 없어.')
            names.add(f.filename.casefold())
        if 'manifest.json' not in z.namelist(): raise ValueError('팩 설명 파일이 없어.')
        m=json.loads(z.read('manifest.json').decode('utf-8-sig')); validate_manifest(m)
        maximum,_,file_limit,manifest_limit=limits(m)
        if len(files)>file_limit+1 or sum(f.file_size for f in files)>maximum or z.getinfo('manifest.json').file_size>manifest_limit:raise ValueError('이 규격의 팩 크기 제한을 초과했어.')
        allowed={'manifest.json',*[r['audio'] for r in m['entries']]}
        if set(z.namelist()) != allowed: raise ValueError('목록에 없는 파일 또는 누락된 음성이 있어.')
        seen=set()
        for row in m['entries']:
            if row['audio'] in seen:continue
            seen.add(row['audio']);data=z.read(row['audio']);check_audio(row,data)
            if audio_validator:audio_validator(row,data)
        return m

def check_audio(row,data):
    if hashlib.sha256(data).hexdigest()!=row['sha256']: raise ValueError('음성 파일이 변경됐거나 손상됐어.')
    duration=opus_info(data) if row['audio'].endswith('.ogg') else wav_info(data)
    if abs(duration-row['duration'])>1/48000: raise ValueError('음성 길이가 설명과 맞지 않아.')

def export_pack(target,manifest,files):
    target=Path(target);validate_manifest(manifest)
    if target.exists(): raise ValueError('같은 이름의 팩이 있어. 새 버전으로 내보내줘.')
    target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_suffix('.partial')
    if tmp.exists(): raise ValueError('이전 내보내기가 남아 있어. 다른 파일 이름을 선택해줘.')
    try:
        with zipfile.ZipFile(tmp,'x',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            for name,path in sorted(files.items()): z.write(path,safe_name(name),compress_type=zipfile.ZIP_STORED if name.endswith('.ogg') else zipfile.ZIP_DEFLATED)
        inspect_pack(tmp);tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True);raise
    return {'file':str(target),'sha256':digest(target),'entries':len(manifest['entries']),'bytes':target.stat().st_size}
