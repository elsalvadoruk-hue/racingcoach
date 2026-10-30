"""Bounded, read-only sample rendering, separate from live HUD and controller locks."""
from collections import OrderedDict
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
CACHE_BYTES=8*1024*1024


def retained_size(value, seen=None):
    """Conservative per-entry size, including retained Unicode SVG/text strings."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(retained_size(k, seen) + retained_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        size += sum(retained_size(item, seen) for item in value)
    return size


class OverlayPreview:
    def __init__(self):
        self.lock=threading.Lock();self.cache=OrderedDict()
        self.cache_sizes={};self.cache_bytes=0

    def render(self,value):
        key=json.dumps(value,sort_keys=True,separators=(',',':'))
        if not self.lock.acquire(timeout=4):
            raise RuntimeError('미리보기를 준비 중입니다. 잠시 뒤 다시 변경해 주세요.')
        try:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            result=subprocess.run([sys.executable,str(ROOT/'scripts/overlay_preview.py')],
                input=key.encode('utf8'),capture_output=True,timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode:
                raise RuntimeError('실제 오버레이 미리보기를 만들지 못했습니다. 앱과 실행창을 다시 실행해 주세요.')
            response=json.loads(result.stdout)
            if response.get('renderer')!='native-tk-v1' or len(response.get('svg',''))>2_000_000:
                raise RuntimeError('오버레이 미리보기 형식을 확인할 수 없습니다.')
            size=retained_size((key,response))
            if size<=CACHE_BYTES:
                self.cache[key]=response
                self.cache_sizes[key]=size
                self.cache_bytes+=size
                while len(self.cache)>32 or self.cache_bytes>CACHE_BYTES:
                    oldest,_=self.cache.popitem(last=False)
                    self.cache_bytes-=self.cache_sizes.pop(oldest)
            return response
        except (subprocess.TimeoutExpired,ValueError,OSError) as error:
            raise RuntimeError('실제 오버레이 미리보기를 준비하지 못했습니다. 잠시 뒤 다시 시도하세요.') from error
        finally:
            self.lock.release()
