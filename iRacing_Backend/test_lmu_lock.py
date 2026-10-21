"""Real Windows primitive tests, using only synthetic buffers and events."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
import unittest
from unittest.mock import patch

from lmu_python_reader import LOCK_DIR, LOCK_SOURCE, load_lock_dll
from lmu_shared_memory import LmuLiveUnavailable


def benchmark(count=10_000, size=324824):
    dll = load_lock_dll()
    busy, waiters = ctypes.c_long(), ctypes.c_long()
    src, dst = ctypes.create_string_buffer(size), ctypes.create_string_buffer(size)
    arguments = ctypes.byref(busy), ctypes.byref(waiters), None, src, dst, size, 1000
    times = []
    for _ in range(count):
        start = time.perf_counter_ns()
        result = dll.lmu_copy_locked(*arguments)
        times.append((time.perf_counter_ns()-start)/1000)
        if result != 1:
            raise RuntimeError('uncontended benchmark failed')
    times.sort()
    return {'iterations': count, 'bytes': size, 'median_us': statistics.median(times),
            'p99_us': times[int(len(times)*.99)-1],
            'measurement': 'Full ctypes call including lock/copy/unlock; upper bound on lock hold time'}


@unittest.skipUnless(os.name == 'nt', 'Windows lock primitive')
class LockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dll = load_lock_dll()

    def test_version_and_copy_unlock(self):
        self.assertEqual(self.dll.lmu_lock_version(), 1)
        busy, waiters = ctypes.c_long(), ctypes.c_long()
        src = ctypes.create_string_buffer(b'fake-frame\0with-bytes')
        dst = ctypes.create_string_buffer(len(src))
        self.assertEqual(self.dll.lmu_copy_locked(ctypes.byref(busy),ctypes.byref(waiters),None,src,dst,len(src),1000), 1)
        self.assertEqual(dst.raw, src.raw)
        self.assertEqual(busy.value, 0)

    def test_busy_and_zero_spins_do_not_copy(self):
        for busy_value, spins in [(1,1000),(0,0)]:
            busy, waiters = ctypes.c_long(busy_value), ctypes.c_long()
            src, dst = ctypes.create_string_buffer(b'new'), ctypes.create_string_buffer(b'old')
            self.assertEqual(self.dll.lmu_copy_locked(ctypes.byref(busy),ctypes.byref(waiters),None,src,dst,4,spins),0)
            self.assertEqual(dst.value,b'old')
            self.assertEqual(busy.value,busy_value)

    def test_existing_waiter_event_signaled(self):
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateEventW.argtypes = [ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE],wintypes.BOOL
        event = kernel.CreateEventW(None,False,False,None)
        self.assertTrue(event)
        try:
            self.assertEqual(kernel.WaitForSingleObject(event,0),258)
            busy,waiters = ctypes.c_long(),ctypes.c_long(1)
            src,dst = ctypes.create_string_buffer(b'frame'),ctypes.create_string_buffer(6)
            self.assertEqual(self.dll.lmu_copy_locked(ctypes.byref(busy),ctypes.byref(waiters),event,src,dst,6,1000),1)
            self.assertEqual(kernel.WaitForSingleObject(event,0),0)
            self.assertEqual(busy.value,0)
            self.assertEqual(waiters.value,1)
        finally:
            kernel.CloseHandle(event)

    def test_concurrent_copy_never_observes_torn_frame(self):
        size = 512*1024
        busy,waiters = ctypes.c_long(),ctypes.c_long()
        shared = ctypes.create_string_buffer(size)
        def worker(index):
            source = ctypes.create_string_buffer(bytes([index+1])*size,size)
            readback = ctypes.create_string_buffer(size)
            for _ in range(100):
                for src,dst in ((source,shared),(shared,readback)):
                    for attempt in range(100):
                        if self.dll.lmu_copy_locked(ctypes.byref(busy),ctypes.byref(waiters),None,src,dst,size,100000):
                            break
                    else:
                        raise AssertionError('copy lock starvation')
                data = readback.raw
                if data.count(data[:1]) != size:
                    raise AssertionError('overlapping copies produced a torn frame')
            return True
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertTrue(all(pool.map(worker,range(8))))
        self.assertEqual(busy.value,0)

    def test_manifest_and_source_checked_before_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            dll = (LOCK_DIR/'lmu_lock.dll').read_bytes()
            manifest = json.loads((LOCK_DIR/'manifest.json').read_text())
            source = folder/'lmu_lock.c'
            source.write_bytes(LOCK_SOURCE.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
            (folder/'lmu_lock.dll').write_bytes(dll)
            (folder/'manifest.json').write_text(json.dumps(manifest))
            # Verify CRLF normalization while reusing the already loaded real
            # module; Windows cannot delete a loaded temporary DLL.
            with patch('lmu_python_reader.ctypes.CDLL', return_value=self.dll) as load:
                self.assertEqual(load_lock_dll(folder,source=source).lmu_lock_version(),1)
                load.assert_called_once_with(str((folder/'lmu_lock.dll').resolve()))
            for kind in ('dll','source','manifest'):
                (folder/'lmu_lock.dll').write_bytes(dll + (b'bad' if kind=='dll' else b''))
                source.write_bytes(LOCK_SOURCE.read_bytes() + (b'bad' if kind=='source' else b''))
                altered = dict(manifest,lock_version=2) if kind=='manifest' else manifest
                (folder/'manifest.json').write_text(json.dumps(altered))
                with patch('lmu_python_reader.ctypes.CDLL') as load:
                    with self.assertRaises(LmuLiveUnavailable):
                        load_lock_dll(folder,source=source)
                    load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
