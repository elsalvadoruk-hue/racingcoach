import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import prepare_lmu_bridge as prepare


class PrepareBridgeTests(unittest.TestCase):
    def test_download_asset_contains_only_the_reader_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / 'bridge-test'
            artifact.mkdir()
            for name in ('lmu_native_bridge.dll', 'manifest.json', 'private.hpp', 'build.cmd'):
                (artifact / name).write_bytes(name.encode())
            with patch.object(prepare.sm, 'validate_bundled_bridges'), \
                    patch.object(prepare, 'asset_name', return_value='bridge-test-win-x64.zip'):
                target = prepare.make_download_asset(artifact, root / 'out')
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(set(archive.namelist()), {'manifest.json', 'lmu_native_bridge.dll'})
                self.assertEqual(archive.read('lmu_native_bridge.dll'), b'lmu_native_bridge.dll')

    def test_invalid_reader_cannot_be_packed_for_automatic_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(prepare.sm, 'validate_bundled_bridges', side_effect=ValueError('invalid')):
                with self.assertRaises(ValueError):
                    prepare.make_download_asset(root / 'bridge-invalid', root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_export_copies_only_dll_and_manifest_without_sdk_or_build_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def build(**kwargs):
                staging = kwargs['build_dir']
                staging.mkdir(parents=True)
                for name in ('lmu_native_bridge.dll', 'manifest.json', 'private.hpp', 'build.cmd', 'build.log'):
                    (staging / name).write_bytes(b'fixture')
            with patch.object(prepare.sm, 'build_artifact_dir', return_value=Path('bridge-test')), \
                    patch.object(prepare.sm, 'BUILD_DIR', root / 'logs'), \
                    patch.object(prepare.sm, 'build_native_bridge', side_effect=build) as compiler, \
                    patch.object(prepare.sm, 'validate_bundled_bridges') as validate:
                target = prepare.prepare(root / 'sdk', root / 'bundles')
            self.assertEqual({p.name for p in target.iterdir()}, {'manifest.json', 'lmu_native_bridge.dll'})
            compiler.assert_called_once()
            validate.assert_called_once_with(root / 'bundles')

    def test_failed_build_never_exports_an_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'bundles'
            with patch.object(prepare.sm, 'build_artifact_dir', return_value=Path('bridge-test')), \
                    patch.object(prepare.sm, 'build_native_bridge', side_effect=RuntimeError('build failed')):
                with self.assertRaises(RuntimeError):
                    prepare.prepare(output=output)
            self.assertFalse(output.exists())
