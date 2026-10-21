"""LMU reader using installed-header layouts and a tiny prebuilt lock DLL."""
from __future__ import annotations

import ctypes
import hashlib
import json
from pathlib import Path
import struct

from lmu_shared_memory import _platform_check, _unavailable

LOCK_DIR = Path(__file__).with_name('lmu_lock')
LOCK_SOURCE = Path(__file__).with_name('lmu_lock.c')


def load_lock_dll(folder=None, *, source=None):
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
        raise _unavailable('ABI_MISMATCH') from exc
    try:
        dll = ctypes.CDLL(str(path.resolve()))
        dll.lmu_lock_version.argtypes, dll.lmu_lock_version.restype = [], ctypes.c_int
        dll.lmu_copy_locked.argtypes = [ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long),
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
        dll.lmu_copy_locked.restype = ctypes.c_int
        if dll.lmu_lock_version() != 1:
            raise ValueError('unsupported lock DLL version')
        return dll
    except (OSError, AttributeError, ValueError) as exc:
        raise _unavailable('ABI_MISMATCH') from exc
