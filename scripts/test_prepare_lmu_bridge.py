import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_lmu_bridge as prepare


class PrepareBridgeTests(unittest.TestCase):
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
