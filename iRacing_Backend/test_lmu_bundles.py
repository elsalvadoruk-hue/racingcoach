"""Prebuilt bridge selection uses synthetic SDK files and never opens a game."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

import lmu_shared_memory as sm


class BundledBridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sdk = self.root / 'sdk'
        self.sdk.mkdir()
        for name in sm.HEADERS:
            (self.sdk / name).write_bytes(b'synthetic SDK')
        self.bundles = self.root / 'bundles'
        for key, value in (('BUILD_DIR', self.root / 'local'), ('BUNDLED_DIR', self.bundles)):
            patcher = patch.object(sm, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.artifact = self.bundles / sm.build_artifact_dir(self.sdk).name
        self.artifact.mkdir(parents=True)
        dll = bytearray(128)
        dll[:2] = b'MZ'
        struct.pack_into('<I', dll, 0x3c, 64)
        dll[64:68] = b'PE\0\0'
        struct.pack_into('<H', dll, 68, 0x8664)
        (self.artifact / 'lmu_native_bridge.dll').write_bytes(dll)
        self.manifest = {'bridge_version': 1, 'header_fingerprint': sm.header_fingerprint(self.sdk),
                         'source_sha256': hashlib.sha256(sm.bridge_source_bytes()).hexdigest(),
                         'dll_sha256': hashlib.sha256(dll).hexdigest(), 'runtime': 'STATIC_MSVC'}
        self.save_manifest()

    def save_manifest(self):
        (self.artifact / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_matching_bundle_is_ready_without_compiling_or_attaching(self):
        source = sm.SharedMemorySource(sdk_dir=self.sdk)
        dll = Mock()
        def abi(buffer, size):
            buffer.value = json.dumps({'pointer_size': 8, 'bridge_version': 1,
                                       'header_fingerprint': self.manifest['header_fingerprint']}).encode()
            return 1
        dll.lmu_abi.side_effect = abi
        with patch.object(sm, '_platform_check'), patch.object(ctypes, 'CDLL', return_value=dll) as load, \
                patch.object(sm.subprocess, 'run') as compiler:
            self.assertEqual(source.probe()['state'], 'READY')
            self.assertFalse(source.probe()['attached'])
            sm.build_native_bridge(sdk_dir=self.sdk)
        self.assertEqual(source.build_dir, self.artifact)
        self.assertTrue(all(Path(call.args[0]) == self.artifact / 'lmu_native_bridge.dll' for call in load.call_args_list))
        compiler.assert_not_called()
        dll.lmu_open.assert_not_called()
        self.assertFalse(sm.BUILD_DIR.exists())

    def test_different_sdk_or_source_uses_local_build_path_and_does_not_load_bundle(self):
        for change in ('sdk', 'source'):
            with self.subTest(change=change):
                file = self.sdk / sm.HEADERS[0] if change == 'sdk' else self.root / 'new-source.cpp'
                file.write_bytes(b'updated')
                with patch.object(sm, 'BRIDGE_SOURCE', file if change == 'source' else sm.BRIDGE_SOURCE), \
                        patch.object(sm, '_platform_check'), patch.object(ctypes, 'CDLL') as load:
                    source = sm.SharedMemorySource(sdk_dir=self.sdk)
                    self.assertEqual(source.build_dir.parent, sm.BUILD_DIR)
                    with self.assertRaises(sm.LmuLiveUnavailable) as error:
                        source.probe()
                    self.assertEqual(error.exception.code, 'NATIVE_NOT_BUILT')
                    load.assert_not_called()
                (self.sdk / sm.HEADERS[0]).write_bytes(b'synthetic SDK')

    def test_tampered_bundle_is_rejected_before_loading(self):
        (self.artifact / 'lmu_native_bridge.dll').write_bytes(b'changed')
        with patch.object(sm, '_platform_check'), patch.object(ctypes, 'CDLL') as load:
            with self.assertRaises(sm.LmuLiveUnavailable) as error:
                sm.SharedMemorySource(sdk_dir=self.sdk).probe()
            self.assertEqual(error.exception.code, 'ABI_MISMATCH')
            load.assert_not_called()

    def test_explicit_local_path_still_wins(self):
        explicit = self.root / 'manual'
        self.assertEqual(sm.SharedMemorySource(sdk_dir=self.sdk, build_dir=explicit).build_dir, explicit.resolve())

    def test_malformed_manifest_is_a_controlled_unavailable_state(self):
        for value in ('[]', 'null', '"text"'):
            (self.artifact / 'manifest.json').write_text(value)
            with patch.object(sm, '_platform_check'), patch.object(ctypes, 'CDLL') as load:
                with self.assertRaises(sm.LmuLiveUnavailable) as error:
                    sm.SharedMemorySource(sdk_dir=self.sdk).probe()
                self.assertEqual(error.exception.code, 'ABI_MISMATCH')
                load.assert_not_called()

    def test_damaged_bundle_can_be_rebuilt_locally_without_overwriting_it(self):
        from types import SimpleNamespace
        (self.artifact / 'lmu_native_bridge.dll').write_bytes(b'damaged')
        local = sm.build_artifact_dir(self.sdk)
        vs = self.root / 'VS'
        (vs / 'VC/Auxiliary/Build').mkdir(parents=True)
        (vs / 'VC/Auxiliary/Build/vcvars64.bat').write_text('fixture')
        def run(command, **kwargs):
            if command[0] == 'cmd.exe':
                (local / 'lmu_native_bridge.dll').write_bytes(b'new local DLL')
                return SimpleNamespace(returncode=0, stdout=b'built', stderr=b'')
            return SimpleNamespace(stdout=str(vs))
        def probe(source):
            if source.build_dir == self.artifact:
                raise sm._unavailable('ABI_MISMATCH')
            return {'state': 'READY'}
        with patch.object(sm, '_platform_check'), patch.object(sm.SharedMemorySource, 'probe', autospec=True, side_effect=probe), \
                patch.object(sm.subprocess, 'run', side_effect=run) as compiler:
            self.assertEqual(sm.build_native_bridge(sdk_dir=self.sdk)['state'], 'READY')
        self.assertEqual(compiler.call_count, 2)
        self.assertIn('/MT', (local / 'build.cmd').read_text())
        self.assertEqual((self.artifact / 'lmu_native_bridge.dll').read_bytes(), b'damaged')
        self.assertEqual(sm.SharedMemorySource(sdk_dir=self.sdk).build_dir, local)

    def test_release_validation_checks_hash_source_platform_runtime_and_export_contents(self):
        self.assertEqual(sm.validate_bundled_bridges(), [self.artifact])
        for key, value in (('dll_sha256', 'wrong'), ('source_sha256', 'old'), ('runtime', 'DYNAMIC_MSVC')):
            with self.subTest(key=key):
                original = self.manifest[key]
                self.manifest[key] = value
                self.save_manifest()
                with self.assertRaises(ValueError):
                    sm.validate_bundled_bridges()
                self.manifest[key] = original
                self.save_manifest()
        (self.artifact / 'SharedMemoryInterface.hpp').write_bytes(b'must not ship')
        with self.assertRaises(ValueError):
            sm.validate_bundled_bridges()

    def test_empty_or_malformed_bundle_is_not_publishable(self):
        with self.assertRaises(ValueError):
            sm.validate_bundled_bridges(self.root / 'missing')
        (self.artifact / 'manifest.json').write_text('[]')
        with self.assertRaises(ValueError):
            sm.validate_bundled_bridges()
