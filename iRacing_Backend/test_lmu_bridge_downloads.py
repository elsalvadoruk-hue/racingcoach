"""Automatic LMU connection tests use only synthetic SDKs and archive bytes."""
import io
import json
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

import lmu_shared_memory as sm
import lmu_bridge_downloads as dl


class BridgeDownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sdk = self.root / 'sdk'
        self.sdk.mkdir()
        for name in sm.HEADERS:
            (self.sdk / name).write_bytes(b'synthetic SDK')
        for key, value in (('BUILD_DIR', self.root / 'cache'), ('BUNDLED_DIR', self.root / 'bundles')):
            patcher = patch.object(sm, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        dll = bytearray(128)
        dll[:2] = b'MZ'
        struct.pack_into('<I', dll, 0x3c, 64)
        dll[64:68] = b'PE\0\0'
        struct.pack_into('<H', dll, 68, 0x8664)
        self.dll = bytes(dll)
        self.manifest = {'bridge_version': 1, 'header_fingerprint': sm.header_fingerprint(self.sdk),
                         'source_sha256': hashlib.sha256(sm.bridge_source_bytes()).hexdigest(),
                         'dll_sha256': hashlib.sha256(self.dll).hexdigest(), 'runtime': 'STATIC_MSVC'}

    def archive(self, *, manifest=None, dll=None, extra=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest or self.manifest))
            archive.writestr('lmu_native_bridge.dll', dll or self.dll)
            if extra:
                archive.writestr(extra, b'not allowed')
        return data.getvalue()

    def test_download_installs_verified_reader_and_new_sources_choose_it(self):
        target = dl.install_asset(self.archive(), self.sdk)
        self.assertEqual(target, sm.downloaded_bridge_dir(self.sdk))
        self.assertEqual(sm.SharedMemorySource(sdk_dir=self.sdk).build_dir, target)
        self.assertEqual((target / 'lmu_native_bridge.dll').read_bytes(), self.dll)
        self.assertEqual(dl.install_asset(self.archive(), self.sdk), target)
        self.assertFalse((self.sdk / 'manifest.json').exists())

    def test_source_zip_and_windows_checkout_share_one_compatible_reader(self):
        source = self.root / 'reader.cpp'
        source.write_bytes(b'first line\r\nsecond line\r\n')
        with patch.object(sm, 'BRIDGE_SOURCE', source):
            windows = dl.asset_name(self.sdk)
            source.write_bytes(b'first line\nsecond line\n')
            self.assertEqual(dl.asset_name(self.sdk), windows)

    def test_changed_sdk_source_dll_architecture_and_extra_paths_are_rejected_before_install(self):
        for case in ('sdk', 'source', 'dll', 'architecture', 'path'):
            with self.subTest(case=case):
                manifest = dict(self.manifest)
                dll = self.dll
                extra = None
                if case == 'sdk': manifest['header_fingerprint'] = '0' * 64
                if case == 'source': manifest['source_sha256'] = '0' * 64
                if case == 'dll': dll = b'tampered'
                if case == 'architecture':
                    bad = bytearray(self.dll)
                    struct.pack_into('<H', bad, 68, 0x14c)
                    dll = bytes(bad)
                    manifest['dll_sha256'] = hashlib.sha256(dll).hexdigest()
                if case == 'path': extra = '../escape.dll'
                with self.assertRaises(ValueError):
                    dl.install_asset(self.archive(manifest=manifest, dll=dll, extra=extra), self.sdk)
                self.assertFalse(sm.downloaded_bridge_dir(self.sdk).exists())

    def test_missing_release_waits_for_compatibility_and_network_failure_is_retryable(self):
        missing = urllib.error.HTTPError('https://example.invalid', 404, 'not found', {}, None)
        fetch = Mock(side_effect=missing)
        manager = dl.BridgeDownloads(fetch=fetch, clock=lambda: 10)
        identity = sm.build_artifact_dir(self.sdk).name
        manager._run(identity, self.sdk)
        self.assertEqual(manager.status(self.sdk)['state'], 'CONNECTION_UPDATE_PENDING')
        manager.request(self.sdk)
        self.assertEqual(fetch.call_count, 1)
        fetch.side_effect = OSError('offline')
        manager._run(identity, self.sdk)
        self.assertEqual(manager.status(self.sdk)['state'], 'CONNECTION_DOWNLOAD_FAILED')

    def test_connection_does_not_block_on_network_and_repeated_requests_share_one_worker(self):
        entered, finish = threading.Event(), threading.Event()
        def fetch(url):
            entered.set()
            finish.wait(3)
            return self.archive()
        manager = dl.BridgeDownloads(fetch=Mock(side_effect=fetch))
        state = manager.request(self.sdk)
        self.assertEqual(state['state'], 'CONNECTION_PREPARING')
        self.assertTrue(entered.wait(1))
        for _ in range(10):
            self.assertEqual(manager.request(self.sdk)['state'], 'CONNECTION_PREPARING')
        manager.fetch.assert_called_once()
        finish.set()
        # Wait for this test's one worker before its temporary directory closes.
        for worker in threading.enumerate():
            if worker.name == 'LMU-connection-download': worker.join(3)
        self.assertEqual(manager.status(self.sdk)['state'], 'READY')

    def test_open_waits_for_precompiled_reader_without_compiling_or_opening_game(self):
        manager = Mock()
        manager.request.return_value = {'state': 'CONNECTION_PREPARING'}
        source = sm.SharedMemorySource(sdk_dir=self.sdk)
        with patch.object(sm, '_platform_check'), patch.object(dl, 'downloads', manager), \
                patch.object(sm.ctypes, 'CDLL') as load, patch.object(sm.subprocess, 'run') as compiler:
            with self.assertRaises(sm.LmuLiveUnavailable) as error:
                source.open()
        self.assertEqual(error.exception.code, 'CONNECTION_PREPARING')
        load.assert_not_called()
        compiler.assert_not_called()

    def test_no_network_on_a_working_prebuilt_reader(self):
        dl.install_asset(self.archive(), self.sdk)
        source = sm.SharedMemorySource(sdk_dir=self.sdk)
        with patch.object(source, 'probe', return_value={'state': 'READY'}), patch.object(dl.downloads, 'request') as fetch:
            self.assertEqual(source.prepare()['state'], 'READY')
        fetch.assert_not_called()

    def test_previous_sdk_dll_is_discarded_before_using_new_download(self):
        source = sm.SharedMemorySource(sdk_dir=self.sdk)
        previous = source._dll = Mock()
        with patch.object(source, 'probe', side_effect=[sm._unavailable('ABI_MISMATCH'), {'state': 'READY'}]), \
                patch.object(dl.downloads, 'request', return_value={'state': 'READY'}):
            self.assertEqual(source.prepare()['state'], 'READY')
        self.assertIsNone(source._dll)
        previous.lmu_open.assert_not_called()

    def test_explicit_developer_build_never_starts_a_download(self):
        source = sm.SharedMemorySource(sdk_dir=self.sdk, build_dir=self.root / 'explicit')
        with patch.object(sm, '_platform_check'), patch.object(dl.downloads, 'request') as fetch:
            with self.assertRaises(sm.LmuLiveUnavailable):
                source.prepare()
        fetch.assert_not_called()

    def test_corrupt_cached_reader_is_preserved_and_replaced_with_verified_copy(self):
        target = dl.install_asset(self.archive(), self.sdk)
        (target / 'lmu_native_bridge.dll').write_bytes(b'corrupt')
        dl.install_asset(self.archive(), self.sdk)
        self.assertEqual((target / 'lmu_native_bridge.dll').read_bytes(), self.dll)
        preserved = list(target.parent.glob(target.name + '.invalid-*'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual((preserved[0] / 'lmu_native_bridge.dll').read_bytes(), b'corrupt')

    def test_connect_and_legacy_build_routes_only_prepare_precompiled_readers(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from lmu_routes import LmuRoutesServices, register_routes
        app = FastAPI()
        register_routes(app, LmuRoutesServices(practice_store=Mock(), corner_names_store=Mock()))
        with patch.dict(os.environ, {'IRACING_LMU_READER': 'bridge'}), TestClient(app) as client, patch.object(sm, 'SharedMemorySource') as source_type, \
                patch.object(sm, 'build_native_bridge') as compiler:
            source = source_type.return_value
            source.sdk_dir = self.sdk
            source.prepare.side_effect = sm._unavailable('CONNECTION_PREPARING')
            for path in ('/api/lmu/live/connect', '/api/lmu/live/bridge/build'):
                result = client.post(path)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['state'], 'CONNECTION_PREPARING')
                self.assertFalse(result.json()['buildable'])
            self.assertEqual(source.prepare.call_count, 2)
            compiler.assert_not_called()
