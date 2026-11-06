"""Per-car lowest cold pressure, learned from the driver's own iRacing telemetry files.

Drivers almost always run the lowest pressure the garage allows, so the lowest cold
pressure in their saved setups is that car's practical minimum. Each .ibt file's session
information holds the car and its setup; only that header part is read, never the samples.
Results are cached by file size and modification time, so later scans only read new files.
Nothing here is entered by the driver; a typed minimum still overrides it (pressure_advice).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import threading
import time

from .snapshot import WHEELS

HEADER_SIZE = 112
MAX_SESSION_BYTES = 4 * 1024 * 1024
SCAN_LIMIT = 1000
RESCAN_S = 30 * 60


def read_ibt_setup(path):
    """(car_path, {wheel: cold kPa}) from an .ibt file's session information, or None."""
    import yaml
    from ibt_reader import _YAML_LOADER, sanitize_iracing_session_yaml
    from setup_capabilities import session_setup_values
    with open(path, 'rb') as handle:
        header = handle.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            return None
        session_len, session_offset = struct.unpack('<2i', header[16:24])
        if not 0 < session_len <= MAX_SESSION_BYTES or session_offset <= 0:
            return None
        handle.seek(session_offset)
        text = handle.read(session_len).decode('latin-1', errors='replace')
    info = yaml.load(sanitize_iracing_session_yaml(text)[0], Loader=_YAML_LOADER) or {}
    drivers = info.get('DriverInfo') or {}
    driver = next((d for d in drivers.get('Drivers') or [] if d.get('CarIdx') == drivers.get('DriverCarIdx')), {})
    car = str(driver.get('CarPath') or '').strip('/\\').lower()[:160]
    values = session_setup_values(info.get('CarSetup') or {})
    cold = {w: values.get('pressure_' + sdk) for w, sdk in zip(WHEELS, ('lf', 'rf', 'lr', 'rr'))}
    cold = {w: v for w, v in cold.items() if isinstance(v, (int, float)) and 40 <= v <= 400}
    return (car, cold) if car and cold else None


class PressureFloors:
    def __init__(self, cache_path, folders, *, reader=read_ibt_setup, clock=time.monotonic):
        self.cache_path, self.folders, self.reader, self.clock = Path(cache_path), [Path(f) for f in folders], reader, clock
        self.lock = threading.Lock()
        self.files, self.scanned_at, self.scanning = None, None, False

    def _load(self):
        if self.files is None:
            try:
                data = json.loads(self.cache_path.read_text(encoding='utf-8'))
                self.files = data.get('files', {}) if data.get('version') == 1 else {}
            except (OSError, ValueError, AttributeError):
                self.files = {}

    def scan(self):
        """Read new or changed telemetry files (newest first). Returns how many were read."""
        with self.lock:
            self._load()
            known = dict(self.files)
        paths = []
        for folder in self.folders:
            try:
                paths += [p for p in folder.glob('*.ibt') if p.is_file()]
            except OSError:
                continue
        paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:SCAN_LIMIT]
        found, read = {}, 0
        for path in paths:
            stat = path.stat()
            key = str(path)
            entry = known.get(key)
            if entry and entry.get('size') == stat.st_size and entry.get('mtime') == stat.st_mtime:
                found[key] = entry
                continue
            try:
                result = self.reader(path)
            except Exception:  # a damaged or still-recording file is skipped
                result = None
            read += 1
            found[key] = {'size': stat.st_size, 'mtime': stat.st_mtime,
                          'car': result[0] if result else None, 'cold_kpa': result[1] if result else {}}
        with self.lock:
            self.files, self.scanned_at = found, self.clock()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps({'version': 1, 'files': found}), encoding='utf-8')
                os.replace(temporary, self.cache_path)
            except OSError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        return read

    def scan_in_background(self):
        """At most one scan at a time, and not more often than RESCAN_S."""
        with self.lock:
            due = self.scanned_at is None or self.clock() - self.scanned_at >= RESCAN_S
            if self.scanning or not due:
                return False
            self.scanning = True

        def run():
            try:
                self.scan()
            finally:
                with self.lock:
                    self.scanning = False
        threading.Thread(target=run, name='tire-pressure-floors', daemon=True).start()
        return True

    def lowest(self, car):
        """{wheel: lowest cold kPa} over the car's telemetry files, and how many files had it."""
        with self.lock:
            self._load()
            entries = [e for e in self.files.values() if e.get('car') == str(car or '').lower()]
        lowest = {w: None for w in WHEELS}
        for entry in entries:
            for w, value in (entry.get('cold_kpa') or {}).items():
                if w in lowest and isinstance(value, (int, float)):
                    lowest[w] = value if lowest[w] is None else min(lowest[w], value)
        return lowest, len(entries)

    def status(self):
        with self.lock:
            self._load()
            cars = {e.get('car') for e in self.files.values() if e.get('car')}
            return {'files': len(self.files), 'cars': len(cars), 'scanning': self.scanning}
