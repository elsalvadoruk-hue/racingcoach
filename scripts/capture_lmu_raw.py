"""Capture up to five minutes locally for offline C++/Python parity checking.

The output contains private game memory. It stays under logs/lmu-raw, excluded
from Git. This tool does not upload data or control the game or running app.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
import time
import uuid
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'iRacing_Backend'))
from lmu_python_reader import PythonSharedMemorySource
from lmu_shared_memory import LmuLiveUnavailable

MAGIC=b'LMURAW1\n'
OUTPUT_DIR=ROOT/'logs/lmu-raw'


def capture(source,seconds=300,*,output_dir=OUTPUT_DIR,now=time.monotonic):
    if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=300:
        raise ValueError('capture duration must be 1..300 seconds')
    folder=Path(output_dir).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    name='lmu-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.lmuraw'
    destination=folder/name
    frames=0; state='COMPLETE'
    with source:
        header={'format_version':1,'pid':source._processor.pid,'process_identity':source._processor.process_identity,
                'header_fingerprint':source._layout.fingerprint,'protocol_fingerprint':source._layout.protocol_fingerprint,
                'layout_size':source._layout.root.size,'frame_size':source._layout.types['SharedMemoryObjectOut'].size,
                'captured_at_utc':datetime.now(timezone.utc).isoformat()}
        encoded=json.dumps(header,separators=(',',':')).encode('utf-8')
        compressor=zlib.compressobj(level=1)
        with destination.open('xb') as stream:
            stream.write(MAGIC+struct.pack('<I',len(encoded))+encoded)
            stop=now()+seconds
            try:
                while now()<stop:
                    raw=source.capture_raw(200)
                    if raw is None: continue
                    if len(raw)!=header['frame_size']: raise ValueError('capture frame size changed')
                    stream.write(compressor.compress(struct.pack('<I',len(raw))+raw))
                    frames+=1
            except KeyboardInterrupt:
                state='INTERRUPTED'
            except LmuLiveUnavailable as exc:
                state=exc.code
            finally:
                stream.write(compressor.flush())
    return {'state':state,'frames':frames,'file':destination.name,'compressed_bytes':destination.stat().st_size}


def read_header(stream):
    if stream.read(len(MAGIC))!=MAGIC: raise ValueError('unsupported capture format')
    size=stream.read(4)
    if len(size)!=4: raise ValueError('truncated capture header')
    length=struct.unpack('<I',size)[0]
    if not 0<length<=65536: raise ValueError('invalid capture header length')
    encoded=stream.read(length)
    if len(encoded)!=length: raise ValueError('truncated capture header')
    header=json.loads(encoded)
    if header.get('format_version')!=1: raise ValueError('unsupported capture version')
    for key in ('pid','process_identity','layout_size','frame_size'):
        if isinstance(header.get(key),bool) or not isinstance(header.get(key),int) or header[key]<0:
            raise ValueError('invalid capture metadata')
    if not 0<header['frame_size']<=64*1024*1024: raise ValueError('invalid capture frame size')
    return header


def iter_frames(stream,header):
    """Bound decompression memory and reject partial or oversized records."""
    decoder=zlib.decompressobj(); pending=bytearray(); maximum=header['frame_size']+4
    while True:
        chunk=stream.read(65536)
        if not chunk: break
        while chunk:
            pending.extend(decoder.decompress(chunk,maximum))
            chunk=decoder.unconsumed_tail
            while len(pending)>=4:
                size=struct.unpack_from('<I',pending)[0]
                if size!=header['frame_size']: raise ValueError('capture record size mismatch')
                if len(pending)<size+4: break
                yield bytes(pending[4:size+4])
                del pending[:size+4]
            if decoder.unused_data: raise ValueError('extra data after capture stream')
    if not decoder.eof or pending: raise ValueError('incomplete capture stream')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--probe',action='store_true',help='validate readiness without opening the game')
    args=parser.parse_args()
    if not 1<=args.seconds<=300: parser.error('--seconds must be between 1 and 300')
    source=PythonSharedMemorySource()
    try:
        if args.probe:
            info=source.probe()
            result={'state':info['state'],'attached':info['attached'],'sizes':info['abi']['sizes']}
        else:
            print('LMU 원본을 logs/lmu-raw/에만 저장합니다. 최대 5분이며 Ctrl+C로 일찍 끝낼 수 있습니다.',flush=True)
            result=capture(source,args.seconds)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result.get('frames',1)>0 else 1
    except LmuLiveUnavailable as exc:
        print(json.dumps({'state':exc.code},ensure_ascii=False))
        return 1


if __name__=='__main__': raise SystemExit(main())
