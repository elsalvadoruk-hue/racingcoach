"""Bounded parsed-file caches with explicitly supplied reader factories."""
from __future__ import annotations
from collections import OrderedDict
import threading
from pathlib import Path
import numpy as np
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
    def __init__(self, max_files: int = 3, *, reader_factory, max_bytes: int | None = None):
        self.reader_factory = reader_factory
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, int, int], ParsedTelemetry] = OrderedDict()
        self._selected = ()

    @staticmethod
    def _array_bytes(parsed):
        """Count shared NumPy backing arrays once, including derived lap grids."""
        seen = set()

        def size(value):
            if id(value) in seen:
                return 0
            if isinstance(value, np.ndarray):
                seen.add(id(value))
                return size(value.base) if isinstance(value.base, np.ndarray) else value.nbytes
            if isinstance(value, (dict, tuple, list)):
                seen.add(id(value))
                items = tuple(value.values()) if isinstance(value, dict) else value
                return sum(map(size, items))
            return 0

        return size((getattr(parsed, 'channels', {}), vars(parsed).get('_analysis_cache', {})))

    def _trim(self, keep=()):
        if self.max_bytes is None:
            while len(self._cache) > self.max_files:
                self._cache.popitem(last=False)
            return
        protected = {*self._selected, *keep}
        if len(self._cache) <= 1:
            return
        sizes = {key: self._array_bytes(parsed) for key, parsed in self._cache.items()}
        total = sum(sizes.values())
        for key in sizes:
            if len(self._cache) <= self.max_files and total <= self.max_bytes:
                break
            if key not in protected:
                total -= sizes[key]
                del self._cache[key]

    def set_byte_budget(self, max_bytes):
        with self._lock:
            if self.max_bytes != max_bytes:
                self.max_bytes = max_bytes
                self._trim()

    def select(self, *recordings):
        """Keep the last displayed target and local reference, even if they exceed the budget."""
        with self._lock:
            self._selected = tuple(parsed.signature for parsed in recordings)
            if self.max_bytes is not None:
                for parsed in recordings:
                    self._cache[parsed.signature] = parsed
                    self._cache.move_to_end(parsed.signature)
                self._trim()

    def retain(self, parsed: ParsedTelemetry) -> bool:
        """Reinsert an active request's parsed file when the cache has room."""
        if file_signature(parsed.path) != parsed.signature:
            return False
        with self._lock:
            self._cache[parsed.signature] = parsed
            self._cache.move_to_end(parsed.signature)
            self._trim(keep=(parsed.signature,) if not self._selected else ())
            return parsed.signature in self._cache

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
            parsed = self._cache.setdefault(signature, parsed)
            self._cache.move_to_end(signature)
            # Active requests hold their own references if the cache is full.
            self._trim(keep=(signature,) if not self._selected else ())
        return parsed
