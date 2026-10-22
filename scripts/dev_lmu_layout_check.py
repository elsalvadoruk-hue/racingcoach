"""Developer-only MSVC oracle. SDK text exists only in temporary directories."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
from lmu_shared_memory import resolve_sdk_dir
from lmu_sdk_layout import LayoutUnsupported, load_layout


def find_msvc():
    vswhere = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    install = subprocess.run([str(vswhere), '-latest', '-products', '*', '-requires',
                              'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                             capture_output=True, text=True, check=True, timeout=20).stdout.strip()
    vcvars = Path(install) / 'VC/Auxiliary/Build/vcvars64.bat'
    if not install or not vcvars.is_file():
        raise RuntimeError('MSVC development tools unavailable')
    return vcvars


def quote(value):
    value = str(value)
    if any(c in value for c in '\r\n%"'):
        raise ValueError('unsupported command path')
    return '"' + value + '"'


def compile_cpp(folder, text, *, sdk_dir=None, name='oracle', options=''):
    """Build only explicit developer harnesses, never imported by the app."""
    folder = Path(folder)
    source = folder / (name + '.cpp')
    source.write_text(text, encoding='utf-8')
    executable = folder / (name + '.exe')
    include = '/I' + quote(sdk_dir) if sdk_dir else ''
    script = folder / (name + '.cmd')
    script.write_text('@echo off\r\ncall ' + quote(find_msvc()) + ' >nul\r\nif errorlevel 1 exit /b 1\r\n'
                      + f'cl /nologo /std:c++17 /EHsc /O2 /MT {include} {options} '
                      + '/Fo' + quote(folder / (name + '.obj')) + ' /Fe' + quote(executable) + ' ' + quote(source) + '\r\n', encoding='utf-8')
    result = subprocess.run(['cmd.exe', '/d', '/c', str(script)], cwd=folder, capture_output=True, timeout=120)
    if result.returncode:
        # Compiler diagnostics may contain SDK text. Do not persist or print it.
        raise RuntimeError('MSVC oracle compilation failed (diagnostics suppressed to protect SDK text)')
    return executable


def check_layout(sdk_dir):
    layout = load_layout(sdk_dir, require_protocol=False, use_cache=False)
    expected, lines = {}, []
    for name, typ in list(layout.types.items()) + [('SharedMemoryLock::LockData', layout.lock)]:
        if typ.kind not in ('struct', 'union') and not typ.values:
            continue
        for suffix, expr, value in [('size', f'sizeof({name})', typ.size),
                                    ('alignment', f'alignof({name})', typ.alignment)]:
            key = name + '.' + suffix
            expected[key] = value
            lines.append(f'std::cout << "{key} " << {expr} << "\\n";')
        for field_name, member in typ.fields.items():
            for suffix, expr, value in [('offset', f'offsetof({name},{field_name})', member.offset),
                                        ('size', f'sizeof((({name}*)0)->{field_name})', member.size)]:
                key = name + '.' + field_name + '.' + suffix
                expected[key] = value
                lines.append(f'std::cout << "{key} " << {expr} << "\\n";')
    code = '''#define NOMINMAX
#include <windows.h>
#include <optional>
#include <utility>
#include <cstdint>
#include <cmath>
#include <iostream>
#include <cstddef>
#define private public
#include "SharedMemoryInterface.hpp"
#undef private
static_assert(sizeof(float_t)==4 && sizeof(double_t)==8,"MSVC math aliases");
int main() {
''' + '\n'.join(lines) + '\n}\n'
    with tempfile.TemporaryDirectory(prefix='lmu-layout-') as temp:
        exe = compile_cpp(temp, code, sdk_dir=sdk_dir)
        output = subprocess.run([str(exe)], capture_output=True, text=True, check=True, timeout=20).stdout
    actual = {line.rsplit(' ', 1)[0]: int(line.rsplit(' ', 1)[1]) for line in output.splitlines()}
    differences = {key: {'python': value, 'msvc': actual.get(key)} for key, value in expected.items() if actual.get(key) != value}
    if set(actual) != set(expected):
        raise RuntimeError('MSVC oracle output keys differ')
    return {'assertions': len(expected), 'differences': differences,
            'facts': actual, 'protocol': layout.protocol_fingerprint, 'fingerprint': layout.fingerprint}


def insert_member(text, name, declaration, *, end=False):
    start = re.search(r'\bstruct\s+' + re.escape(name) + r'\b[^;{]*\{', text)
    if not start:
        raise RuntimeError('mutation target missing: ' + name)
    opening = start.end()-1
    depth, closing = 1, opening+1
    while depth and closing < len(text):
        if text[closing] == '{':
            depth += 1
        elif text[closing] == '}':
            depth -= 1
        closing += 1
    index = closing-1 if end else opening+1
    return text[:index] + '\n' + declaration + '\n' + text[index:]


def check_synthetic():
    from test_lmu_sdk_layout import SYNTHETIC_SOURCE, SYNTHETIC_FACTS
    lines, expected = [], {}
    for name, (size, alignment, fields) in SYNTHETIC_FACTS.items():
        for suffix, expression, value in [('size', f'sizeof({name})', size), ('alignment', f'alignof({name})', alignment)]:
            key = name+'.'+suffix
            lines.append(f'std::cout << "{key} " << {expression} << "\\n";')
            expected[key] = value
        for member, value in fields.items():
            key = name+'.'+member
            lines.append(f'std::cout << "{key} " << offsetof({name},{member}) << "\\n";')
            expected[key] = value
    # Independent checks for the other synthetic fixtures' literal expectations.
    extra = '''
#pragma pack(push,4)
struct PackedProbe { char a; double b; bool ok; volatile LONG v; const char* p; };
#pragma pack(pop)
enum class CountProbe : uint8_t { FIRST=2, LAST, SIZE };
struct MethodProbe { static const int skip=20; using Alias=int;
 double samples[(unsigned)CountProbe::SIZE]; char text[MAX_PATH]; unsigned short flags[3];
 void set(int x) { if(x) return; } MethodProbe()=default;
 double operator[](int i) const { return samples[i]; }
};
#pragma pack(push,2)
struct PartProbe { char a; int b; };
#pragma pack(pop)
struct RootProbe { PartProbe p[3]; };
static_assert(sizeof(PackedProbe)==28 && alignof(PackedProbe)==4);
static_assert(offsetof(PackedProbe,b)==4 && offsetof(PackedProbe,ok)==12 && offsetof(PackedProbe,v)==16 && offsetof(PackedProbe,p)==20);
static_assert(sizeof(MethodProbe)==304 && offsetof(MethodProbe,flags)==292);
static_assert(sizeof(RootProbe)==18 && alignof(PartProbe)==2);
'''
    code = '#include <windows.h>\n#include <cstdint>\n#include <iostream>\n#include <cstddef>\n' + SYNTHETIC_SOURCE + extra + '\nint main(){\n' + '\n'.join(lines) + '\n}\n'
    with tempfile.TemporaryDirectory(prefix='lmu-synthetic-layout-') as temp:
        exe = compile_cpp(temp, code)
        output = subprocess.run([str(exe)], capture_output=True, text=True, check=True, timeout=20).stdout
    actual = {line.rsplit(' ',1)[0]: int(line.rsplit(' ',1)[1]) for line in output.splitlines()}
    return {'assertions': len(expected), 'facts': actual, 'differences': {key: [value, actual.get(key)] for key,value in expected.items() if actual.get(key)!=value}}


def suite(sdk_dir):
    report = {'installed': check_layout(sdk_dir), 'synthetic': check_synthetic(), 'variants': {}, 'rejected': {}}
    original_protocol = report['installed']['protocol']
    def variant(index, folder):
        internal = folder / 'InternalsPlugin.hpp'
        interface = folder / 'SharedMemoryInterface.hpp'
        text, shared = internal.read_text(encoding='utf-8-sig'), interface.read_text(encoding='utf-8-sig')
        if index == 1:
            text = insert_member(text, 'TelemInfoV01', 'double addedLayoutProbe;')
        elif index == 2:
            text = insert_member(text, 'VehicleScoringInfoV01', 'unsigned char tailLayoutProbe[7];', end=True)
        elif index == 3:
            text, count = re.subn(r'(\bmExpansion\s*\[\s*)\d+(\s*\])', r'\g<1>217\2', text, count=1)
            if count != 1: raise RuntimeError('array mutation missing')
        elif index == 4:
            shared, count = re.subn(r'(\bvehScoringInfo\s*\[\s*)104(\s*\])', r'\g<1>128\2', shared)
            if count != 1: raise RuntimeError('vehicle mutation missing')
        elif index in (5, 6):
            definition = 'struct LayoutProbe { char marker; double value; };\n'
            if index == 6:
                (folder / 'LayoutProbe.hpp').write_text('#pragma once\n' + definition, encoding='utf-8')
                text = '#include "LayoutProbe.hpp"\n' + text
            else:
                text = definition + text
            text = insert_member(text, 'TelemInfoV01', 'LayoutProbe additionalLayoutProbe;')
        elif index == 7:
            # Surround the whole record, preserving all original SDK text only in RAM/temp.
            start = re.search(r'\bstruct\s+TelemInfoV01\b', text).start()
            following = re.search(r'\bstruct\s+GraphicsInfoV01\b', text[start:]).start()+start
            text = text[:start] + '#pragma pack(push, 8)\n' + text[start:following] + '#pragma pack(pop)\n' + text[following:]
        elif index == 8:
            shared = re.sub(r'\bSME_MAX\s*([,}])', 'SME_ADDED_LAYOUT_PROBE,\n    SME_MAX\\1', shared, count=1)
        internal.write_text(text, encoding='utf-8')
        interface.write_text(shared, encoding='utf-8')
    for index in range(1, 9):
        with tempfile.TemporaryDirectory(prefix='lmu-sdk-variant-') as temp:
            folder = Path(temp)
            shutil.copytree(sdk_dir, folder, dirs_exist_ok=True)
            variant(index, folder)
            result = check_layout(folder)
            result['protocol_unchanged'] = result['protocol'] == original_protocol
            report['variants'][str(index)] = result
    for label, member in [('bitfield', 'unsigned int probe : 1;'), ('virtual', 'virtual void probe();'), ('unknown', 'UnknownProbe probe;')]:
        with tempfile.TemporaryDirectory(prefix='lmu-sdk-reject-') as temp:
            folder = Path(temp)
            shutil.copytree(sdk_dir, folder, dirs_exist_ok=True)
            path = folder / 'InternalsPlugin.hpp'
            path.write_text(insert_member(path.read_text(encoding='utf-8-sig'), 'TelemInfoV01', member), encoding='utf-8')
            try:
                load_layout(folder, require_protocol=False, use_cache=False)
                report['rejected'][label] = False
            except LayoutUnsupported:
                report['rejected'][label] = True
    with tempfile.TemporaryDirectory(prefix='lmu-sdk-protocol-') as temp:
        folder = Path(temp)
        shutil.copytree(sdk_dir, folder, dirs_exist_ok=True)
        path = folder / 'SharedMemoryInterface.hpp'
        text = path.read_text(encoding='utf-8-sig')
        text, count = re.subn(r'(\bCopySharedMemoryObj\s*\([^)]*\)\s*\{)', r'\1\n(void)0;', text, count=1)
        if count != 1: raise RuntimeError('copy mutation missing')
        path.write_text(text, encoding='utf-8')
        report['copy_protocol_changed'] = load_layout(folder, require_protocol=False, use_cache=False).protocol_fingerprint != original_protocol
    elapsed = []
    for _ in range(20):
        start = time.perf_counter_ns()
        load_layout(sdk_dir, require_protocol=False, use_cache=False)
        elapsed.append((time.perf_counter_ns()-start)/1e6)
    report['parse_ms'] = {'median': statistics.median(elapsed), 'max': max(elapsed)}
    report['passed'] = (not report['installed']['differences'] and not report['synthetic']['differences'] and
                        all(not row['differences'] and row['protocol_unchanged'] for row in report['variants'].values()) and
                        all(report['rejected'].values()) and report['copy_protocol_changed'] and max(elapsed) <= 50)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-dir', type=Path)
    parser.add_argument('--suite', action='store_true')
    args = parser.parse_args()
    sdk_dir = resolve_sdk_dir(args.sdk_dir)
    result = suite(sdk_dir) if args.suite else check_layout(sdk_dir)
    output = ROOT / 'logs/lmu-python-reader'
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ('layout-check-' + time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '.json')
    destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'passed': result.get('passed', not result.get('differences')), 'report': str(destination)}))
    return 0 if result.get('passed', not result.get('differences')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
