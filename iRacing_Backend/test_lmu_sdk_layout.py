"""Synthetic declarations are authored here, never copied from the game SDK."""
import tempfile
from pathlib import Path
import unittest

import lmu_sdk_layout as layout
from lmu_shared_memory import resolve_sdk_dir


# Expected facts independently compiled with MSVC x64 by
# scripts/dev_lmu_layout_check.py --suite (check_synthetic).
SYNTHETIC_SOURCE = '''
struct BaseProbe { char mark; int number; };
struct InheritedProbe : public BaseProbe { double value; bool active; };
struct UnionProbe { union { struct { double x, y, z; }; double cells[3]; }; };
struct ArrayProbe { char a; short grid[2][3]; bool b; void* p; };
'''
SYNTHETIC_FACTS = {
    'BaseProbe': (8, 4, {'mark': 0, 'number': 4}),
    'InheritedProbe': (24, 8, {'mark': 0, 'number': 4, 'value': 8, 'active': 16}),
    'UnionProbe': (24, 8, {'x': 0, 'y': 8, 'z': 16, 'cells': 0}),
    'ArrayProbe': (24, 8, {'a': 0, 'grid': 2, 'b': 14, 'p': 16}),
}


def protocol_source(names=None):
    # A deliberately tiny fake protocol, with different implementation text.
    names = list(layout.EVENT_NAMES) + ['SME_MAX'] if names is None else names
    return ('class SharedMemoryUpdateWaiter { public: bool next() { return true; } };\n'
            'class SharedMemoryLock { struct LockData { long waiters; long busy; }; };\n'
            'enum SharedMemoryEvent : uint32_t {' + ','.join(names) + '};\n'
            'static void CopySharedMemoryObj(int& out, int& in) { out = in; }\n')


class SdkLayoutTests(unittest.TestCase):
    def parse(self, source, root='Root', defines=None):
        parser = layout._Parser(source, defines or {'MAX_PATH': '260'})
        return parser.resolve(root)

    def test_msvc_synthetic_facts(self):
        parser = layout._Parser(SYNTHETIC_SOURCE, {})
        for name, (size, alignment, fields) in SYNTHETIC_FACTS.items():
            typ = parser.resolve(name)
            self.assertEqual((typ.size, typ.alignment), (size, alignment), name)
            self.assertEqual({n: f.offset for n, f in typ.fields.items()}, fields, name)
        array = parser.resolve('ArrayProbe')
        self.assertEqual(array.find('grid[1][2]').offset, 12)
        with self.assertRaises(layout.LayoutUnsupported):
            array.find('grid[2][0]')

    def test_pack_and_member_kinds(self):
        result = self.parse('__LMU_PACK_4 struct Root { char a; double b; bool ok; volatile LONG v; const char* p; };')
        self.assertEqual(result.size, 28)
        self.assertEqual(result.alignment, 4)
        self.assertEqual([v.offset for v in result.fields.values()], [0, 4, 12, 16, 20])
        self.assertEqual(result.find('p').kind, 'pointer')
        self.assertTrue(result.find('v').type.signed)
        self.assertEqual(result.find('ok').kind, 'bool')

    def test_enums_macros_arrays_methods_and_static(self):
        result = self.parse('''enum class Count : uint8_t { FIRST=2, LAST, SIZE };
            struct Root {
                static const int skip=20; using Alias = int;
                double samples[Count::SIZE]; char text[MAX_PATH]; unsigned short flags[CAP];
                void set(int x) { if(x) { return; } }
                Root() = default; double operator[](int i) const { return samples[i]; }
            };''', defines={'MAX_PATH': '260', 'CAP': '3'})
        self.assertEqual(result.size, 304)
        self.assertEqual(list(result.fields), ['samples', 'text', 'flags'])
        self.assertEqual(result.find('samples').size, 32)
        self.assertEqual(result.find('flags').offset, 292)

    def test_unreachable_virtual_class_is_ignored(self):
        self.assertEqual(self.parse('class Unused { virtual void f()=0; }; struct Root { int x; };').size, 4)

    def test_fail_closed_declarations(self):
        declarations = [
            'unsigned x:1;', 'Mystery x;', 'int x[UNKNOWN];', 'virtual void f();',
            'Holder<int> value;', 'int& value;', 'int (*callback)(int);', 'int x',
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration), self.assertRaises(layout.LayoutUnsupported):
                self.parse('struct Root {' + declaration + '};')
        for prefix in (': public A, public B', ': virtual public A', ': private A'):
            with self.assertRaises(layout.LayoutUnsupported):
                self.parse('struct A { int x; }; struct B { int y; }; struct Root ' + prefix + ' { int z; };')
        with self.assertRaises(layout.LayoutUnsupported):
            self.parse('struct Root { int x[3; };')

    def test_preprocessing_includes_pack_and_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'part.hpp').write_text('#pragma once\n#pragma pack(push, 2)\nstruct Part { char a; int b; };\n#pragma pack(pop)\n')
            (root / 'main.hpp').write_text('''#ifndef MY_GUARD
#define MY_GUARD
#include <windows.h>
#include "part.hpp"
#include "part.hpp"
#define CAP 3
struct Root { Part p[CAP]; };
#endif
''')
            headers = layout._Headers(root)
            parser = layout._Parser(headers.read('main.hpp'), headers.defines)
            self.assertEqual(parser.resolve('Root').size, 18)
            self.assertEqual(parser.resolve('Part').alignment, 2)
            self.assertEqual(set(headers.files), {'main.hpp', 'part.hpp'})
            for directive in ('#if 0', '#ifdef OTHER', '#pragma mysterious', '#pragma pack(pop)', '#include "../escape.hpp"'):
                (root / 'bad.hpp').write_text(directive+'\nstruct Root { int x; };\n')
                with self.subTest(directive=directive), self.assertRaises((layout.LayoutUnsupported, ValueError)):
                    layout._Headers(root).read('bad.hpp')

    def test_protocol_normalizes_whitespace_and_comments(self):
        source = protocol_source()
        def fingerprint(text):
            return layout._protocol(layout._Parser(layout._uncomment(text), {'LMU_TEST': '"fake"'}))[0]
        self.assertEqual(fingerprint(source), fingerprint(source.replace(' ', '  \n')+'\n// irrelevant\n'))
        self.assertNotEqual(fingerprint(source), fingerprint(source.replace('out = in;', 'out = in + 1;')))

    def test_append_event_preserves_protocol_changes_array_layout(self):
        names = list(layout.EVENT_NAMES)
        def result(entries):
            parser = layout._Parser(protocol_source(entries) + 'struct Root { SharedMemoryEvent events[SME_MAX]; int value; };', {})
            return layout._protocol(parser)[0], parser.resolve('Root')
        old, old_root = result(names+['SME_MAX'])
        new, new_root = result(names+['SME_EXTRA', 'SME_MAX'])
        self.assertEqual(old, new)
        self.assertEqual(new_root.size-old_root.size, 4)
        self.assertEqual(new_root.find('value').offset-old_root.find('value').offset, 4)
        mutations = [names[:4]+['SME_EXTRA']+names[4:]+['SME_MAX'],
                     ['SME_RENAMED']+names[1:]+['SME_MAX'],
                     [names[0]+'=1']+names[1:]+['SME_MAX'],
                     names+['SME_MAX', 'SME_EXTRA']]
        for entries in mutations:
            with self.subTest(entries=entries), self.assertRaises(layout.LayoutUnsupported):
                result(entries)

    def test_installed_sdk_and_content_cache(self):
        folder = resolve_sdk_dir()
        if not all((folder / name).is_file() for name in layout.HEADERS):
            self.skipTest('installed SDK unavailable')
        result = layout.load_layout(folder)
        self.assertEqual(result.root.size, 324824)
        self.assertEqual(result.types['TelemInfoV01'].size, 1888)
        self.assertEqual(result.find('data.telemetry').offset, 128464)
        self.assertEqual(result.types['TelemInfoV01'].find('mLapInvalidated').offset, 745)
        self.assertEqual(result.types['VehicleScoringInfoV01'].find('mCountLapFlag').offset, 506)
        self.assertIs(result, layout.load_layout(folder))
        # Check every required field's kind, including scalar signedness.
        types = dict(result.types)
        telem = types['TelemInfoV01']
        from dataclasses import replace
        members = dict(telem.fields)
        members['mFuel'] = replace(members['mFuel'], type=layout.Type('bad', 8, 8, 'integer'))
        types[telem.name] = replace(telem, fields=members)
        with self.assertRaises(layout.LayoutUnsupported):
            layout.validate_required_fields(types, result.lock)


if __name__ == '__main__':
    unittest.main()
