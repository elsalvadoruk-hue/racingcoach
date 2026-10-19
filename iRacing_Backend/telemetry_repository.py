"""Bounded parsed-file caches with explicitly supplied reader factories."""
from __future__ import annotations
from collections import OrderedDict
import threading
from pathlib import Path
from analysis_numeric import file_signature
from telemetry_types import ParsedTelemetry, ExternalReferenceLap
from ibt_reader import TelemetryFileBusy


class ExternalReferenceRepository:
    def __init__(self, max_files: int = 12, *, reader_factory):
        self.reader_factory = reader_factory
        self.max_files = max(3, int(max_files))
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, int, int], ExternalReferenceLap] = OrderedDict()

    def load(self, path: Path) -> ExternalReferenceLap:
        signature = file_signature(path)
        with self._lock:
            cached = self._cache.get(signature)
            if cached is not None:
                self._cache.move_to_end(signature)
                return cached
        parsed = self.reader_factory(path).parse()
        with self._lock:
            self._cache[signature] = parsed
            self._cache.move_to_end(signature)
            while len(self._cache) > self.max_files:
                self._cache.popitem(last=False)
        return parsed


class TelemetryRepository:
    def __init__(self, max_files: int = 3, *, reader_factory):
        self.reader_factory = reader_factory
        self.max_files = max_files
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, int, int], ParsedTelemetry] = OrderedDict()

    def load(self, path: Path) -> ParsedTelemetry:
        try:
            signature = file_signature(path)
        except PermissionError as exc:
            raise TelemetryFileBusy(path) from exc
        with self._lock:
            cached = self._cache.get(signature)
            if cached is not None:
                self._cache.move_to_end(signature)
                return cached

        try:
            parsed = self.reader_factory(path).parse()
        except PermissionError as exc:
            raise TelemetryFileBusy(path) from exc
        with self._lock:
            self._cache[signature] = parsed
            self._cache.move_to_end(signature)
            while len(self._cache) > self.max_files:
                self._cache.popitem(last=False)
        return parsed
