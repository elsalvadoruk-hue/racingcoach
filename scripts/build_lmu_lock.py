"""Developer-only reproducible MSVC build of the SDK-independent copy DLL."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from dev_lmu_layout_check import ROOT, find_msvc, quote


def build():
    source = ROOT / 'iRacing_Backend/lmu_lock.c'
    target = source.with_suffix('')
    with tempfile.TemporaryDirectory(prefix='lmu-lock-build-') as temp:
        folder = Path(temp)
        dll_path = folder / 'lmu_lock.dll'
        script = folder / 'build.cmd'
        script.write_text('@echo off\r\ncall '+quote(find_msvc())+' >nul\r\nif errorlevel 1 exit /b 1\r\n'
                          + 'cl /nologo /O2 /MT /LD /Brepro /W4 /WX /Fo'+quote(folder/'lmu_lock.obj')
                          + ' /Fe'+quote(dll_path)+' '+quote(source)+' /link /Brepro /IMPLIB:'+quote(folder/'lmu_lock.lib')
                          + '\r\nif errorlevel 1 exit /b 1\r\ndumpbin /dependents '+quote(dll_path)+'\r\n'
                          + 'dumpbin /exports '+quote(dll_path)+'\r\n', encoding='utf-8')
        result = subprocess.run(['cmd.exe','/d','/c',str(script)], cwd=folder, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8','replace')+result.stderr.decode('utf-8','replace'))
        output = result.stdout.decode('utf-8','replace')
        dependencies = sorted(set(re.findall(r'^\s+([A-Za-z0-9_.-]+\.dll)\s*$', output, re.M|re.I)))
        exports = sorted(re.findall(r'^\s+\d+\s+[0-9A-F]+\s+[0-9A-F]+\s+(lmu_\w+)\s*$', output, re.M))
        if [name.lower() for name in dependencies] != ['kernel32.dll']:
            raise RuntimeError('Unexpected native dependencies: '+repr(dependencies))
        if exports != ['lmu_copy_locked','lmu_lock_version']:
            raise RuntimeError('Unexpected exported functions: '+repr(exports))
        dll = dll_path.read_bytes()
        manifest = {'lock_version': 1, 'source_sha256': hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
                    'dll_sha256': hashlib.sha256(dll).hexdigest(), 'runtime': 'STATIC_MSVC',
                    'dependencies': dependencies, 'exports': exports}
        target.mkdir(parents=True,exist_ok=True)
        destination = target / 'lmu_lock.dll'
        if not destination.exists() or destination.read_bytes() != dll:
            destination.write_bytes(dll)
        (target/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,indent=2))
    return manifest


if __name__ == '__main__':
    build()
