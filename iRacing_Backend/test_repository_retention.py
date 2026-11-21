"""Active-file retention stays bounded and rejects changed recordings."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import numpy as np

from analysis_numeric import file_signature
from telemetry_repository import TelemetryRepository


class RepositoryRetentionTests(unittest.TestCase):
    def test_byte_budget_pins_target_and_reference_then_releases_old_selection(self):
        with TemporaryDirectory() as directory:
            def parse(path):
                native = np.zeros(80, dtype=np.uint8)
                return SimpleNamespace(path=path, signature=file_signature(path),
                                       channels={'native': native, 'view': native[20:]})

            repository = TelemetryRepository(reader_factory=lambda path: SimpleNamespace(parse=lambda: parse(path)),
                                             max_bytes=170)
            files = [Path(directory) / f'{i}.ibt' for i in range(4)]
            for path in files:
                path.write_bytes(b'fixture')
            target, reference = (repository.load(path) for path in files[:2])
            target._analysis_cache = {'grid': np.zeros(25, dtype=np.uint8)}
            repository.select(target, reference)
            preview = repository.load(files[2])
            self.assertFalse(repository.retain(preview))
            self.assertIs(repository.load(files[0]), target)
            self.assertIs(repository.load(files[1]), reference)
            self.assertNotIn(preview.signature, repository._cache)
            repository.select(preview)
            self.assertIn(preview.signature, repository._cache)
            self.assertNotIn(target.signature, repository._cache)
            repository.set_byte_budget(None)
            for path in files:
                repository.load(path)
            self.assertEqual(list(repository._cache), [file_signature(path) for path in files[1:]])

    def test_reference_discovery_cannot_force_active_recording_reparse(self):
        with TemporaryDirectory() as directory:
            parsed_paths = []
            def reader(path):
                def parse():
                    parsed_paths.append(path)
                    return SimpleNamespace(path=path, signature=file_signature(path), _analysis_cache={})
                return SimpleNamespace(parse=parse)
            repository = TelemetryRepository(max_files=2, reader_factory=reader)
            files = [Path(directory) / (str(i) + '.ibt') for i in range(4)]
            for path in files:
                path.write_bytes(b'fixture')
            active = repository.load(files[0])
            active._analysis_cache['handling'] = object()
            for path in files[1:]:
                repository.load(path)
            self.assertNotIn(active.signature, repository._cache)
            self.assertTrue(repository.retain(active))
            self.assertIs(repository.load(files[0]), active)
            self.assertEqual(len(parsed_paths), 4)
            self.assertEqual(len(repository._cache), 2)
            self.assertIs(repository._cache[active.signature]._analysis_cache, active._analysis_cache)

    def test_changed_signature_is_not_reinserted(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.ibt'
            path.write_bytes(b'old')
            stale = SimpleNamespace(path=path, signature=file_signature(path))
            path.write_bytes(b'changed')
            repository = TelemetryRepository(reader_factory=None)
            self.assertFalse(repository.retain(stale))
            self.assertFalse(repository._cache)
