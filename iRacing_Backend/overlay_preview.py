"""Bounded, read-only sample rendering, separate from live HUD and controller locks."""
from collections import OrderedDict
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]


class OverlayPreview:
    def __init__(self):
        self.lock=threading.Lock();self.cache=OrderedDict()

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
            self.cache[key]=response
            while len(self.cache)>32:self.cache.popitem(last=False)
            return response
        except (subprocess.TimeoutExpired,ValueError,OSError) as error:
            raise RuntimeError('실제 오버레이 미리보기를 준비하지 못했습니다. 잠시 뒤 다시 시도하세요.') from error
        finally:
            self.lock.release()
