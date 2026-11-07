"""Bring Garage61 laps the driver downloaded from the website into the library.

Drivers without an API token can still open Garage61 in their own browser,
export a lap as CSV and hand it to the app. The app never drives the website:
it only reads files the driver chose (a picked file, or a Garage61 CSV in the
Downloads folder) and keeps a validated copy next to the other references.
The source file is never modified.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
import asyncio

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel

# Same shape as the names Garage61 gives its CSV exports. The lap ID is a
# ULID-like token; nothing in the name may act as a path.
WEB_CSV_RE = re.compile(
    r"^Garage 61 - (?P<driver>.+?) - (?P<car>.+?) - (?P<track>.+?) - "
    r"(?P<time>\d{2}\.\d{2}\.\d{3}) - (?P<lap_id>[0-9A-Za-z]{10,40})\.csv$",
    re.IGNORECASE,
)
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_LISTED = 30


class DownloadImport(BaseModel):
    name: str


class Garage61ImportError(ValueError):
    """The chosen file is not a Garage61 lap CSV the app can use."""


def valid_name(name: str) -> bool:
    text = str(name or '')
    return (0 < len(text) <= 240 and not _UNSAFE.search(text)
            and Path(text).name == text and bool(WEB_CSV_RE.match(text)))


def lap_id(name: str) -> Optional[str]:
    match = WEB_CSV_RE.match(str(name or ''))
    return match.group('lap_id').upper() if match else None


def downloads_dir() -> Path:
    """The user's Downloads folder (Windows known folder, else ~/Downloads)."""
    configured = os.getenv('IRACING_DOWNLOADS_DIR', '').strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                            ('Data3', wintypes.WORD), ('Data4', wintypes.BYTE * 8)]

            raw = uuid.UUID('{374DE290-123F-4565-9164-39C4925E467B}').bytes_le
            guid = GUID.from_buffer_copy(raw)
            pointer = ctypes.c_wchar_p()
            shell = ctypes.windll.shell32
            if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer)) == 0:
                try:
                    return Path(pointer.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(pointer)
        except Exception:
            pass
    return Path.home() / 'Downloads'


def _library_ids(reference_dir: Path) -> set[str]:
    ids: set[str] = set()
    if reference_dir.exists():
        for path in reference_dir.glob('Garage 61 - *.csv'):
            found = lap_id(path.name)
            if found:
                ids.add(found)
    return ids


def scan_downloads(reference_dir: Path, metadata: Callable[[Path], Optional[dict[str, Any]]],
                   folder: Optional[Path] = None, limit: int = MAX_LISTED) -> dict[str, Any]:
    """List Garage61 CSV exports in the Downloads folder, newest first.

    Only file names and sizes are read here; contents are checked on import.
    """
    folder = folder or downloads_dir()
    if not folder.is_dir():
        return {'directory': str(folder), 'available': False, 'files': []}
    known = _library_ids(reference_dir)
    rows = []
    for path in folder.glob('Garage 61 - *.csv'):
        if not valid_name(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_CSV_BYTES:
            continue
        info = metadata(path) or {}
        rows.append({
            'name': path.name, 'size_bytes': stat.st_size, 'modified_ns': stat.st_mtime_ns,
            'driver_name': info.get('driver_name'), 'car_name': info.get('car_name'),
            'track_name': info.get('track_name'), 'track_config': info.get('track_config'),
            'lap_time_s': info.get('lap_time_s'), 'imported': lap_id(path.name) in known,
        })
    rows.sort(key=lambda row: row['modified_ns'], reverse=True)
    return {'directory': str(folder), 'available': True, 'files': rows[:max(1, int(limit))]}


def _install(staged: Path, reference_dir: Path, validate: Callable[[Path], Any]) -> dict[str, Any]:
    """Validate a staged copy that already carries its final name, then keep it."""
    try:
        validate(staged)
    except Exception as exc:  # the reader explains what is wrong with the lap
        raise Garage61ImportError(f'Garage61 랩 CSV로 읽을 수 없습니다: {exc}') from exc
    destination = reference_dir / staged.name
    existing = [path for path in reference_dir.glob('Garage 61 - *.csv') if lap_id(path.name) == lap_id(staged.name)]
    if existing:
        # The same lap is already kept (perhaps from the API with richer
        # metadata): keep that copy. Imported laps are ranked like any other
        # reference, by the chosen collection mode and the current conditions.
        return {'name': existing[0].name, 'already_imported': True}
    os.replace(staged, destination)
    return {'name': destination.name, 'already_imported': False}


def import_from_path(source: Path, reference_dir: Path, validate: Callable[[Path], Any]) -> dict[str, Any]:
    if not valid_name(source.name):
        raise Garage61ImportError('Garage61에서 내려받은 랩 CSV(파일 이름이 "Garage 61 - "로 시작)를 선택하세요.')
    size = source.stat().st_size
    if size <= 0:
        raise Garage61ImportError('선택한 파일이 비어 있습니다.')
    if size > MAX_CSV_BYTES:
        raise Garage61ImportError('파일이 너무 큽니다. Garage61의 한 랩 CSV인지 확인하세요.')
    reference_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.import-', dir=reference_dir) as staging:
        staged = Path(staging) / source.name
        shutil.copyfile(source, staged)
        return _install(staged, reference_dir, validate)


def import_from_chunks(name: str, chunks: Iterable[bytes], reference_dir: Path,
                       validate: Callable[[Path], Any]) -> dict[str, Any]:
    if not valid_name(name):
        raise Garage61ImportError('Garage61에서 내려받은 랩 CSV(파일 이름이 "Garage 61 - "로 시작)를 선택하세요.')
    reference_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.import-', dir=reference_dir) as staging:
        staged = Path(staging) / name
        size = 0
        with staged.open('xb') as handle:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_CSV_BYTES:
                    raise Garage61ImportError('파일이 너무 큽니다. Garage61의 한 랩 CSV인지 확인하세요.')
                handle.write(chunk)
        if not size:
            raise Garage61ImportError('선택한 파일이 비어 있습니다.')
        return _install(staged, reference_dir, validate)


def register_routes(app, reference_dir: Callable[[], Path], validate: Callable[[Path], Any],
                    metadata: Callable[[Path], Optional[dict[str, Any]]]) -> None:
    @app.get('/api/garage61/downloads')
    async def list_garage61_downloads() -> dict[str, Any]:
        return await asyncio.to_thread(scan_downloads, reference_dir(), metadata)

    @app.post('/api/garage61/downloads/import')
    async def import_garage61_download(payload: DownloadImport) -> dict[str, Any]:
        # Only a bare name from the listing above: never a client-supplied path.
        if not valid_name(payload.name):
            raise HTTPException(422, 'Garage61 랩 CSV 파일 이름이 아닙니다.')
        source = downloads_dir() / payload.name
        if not source.is_file():
            raise HTTPException(404, '다운로드 폴더에서 파일을 찾지 못했습니다. 목록을 새로 고친 뒤 다시 선택하세요.')
        try:
            return await asyncio.to_thread(import_from_path, source, reference_dir(), validate)
        except Garage61ImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(422, 'Garage61 CSV를 가져오지 못했습니다. 파일과 데이터 폴더의 접근 권한·여유 공간을 확인하세요.') from exc

    @app.post('/api/garage61/import')
    async def import_garage61_file(request: Request, filename: str = Query(max_length=240)) -> dict[str, Any]:
        if not valid_name(filename):
            raise HTTPException(422, 'Garage61에서 내려받은 랩 CSV(파일 이름이 "Garage 61 - "로 시작)를 선택하세요.')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_CSV_BYTES:
                raise HTTPException(422, '파일이 너무 큽니다. Garage61의 한 랩 CSV인지 확인하세요.')
            chunks.append(chunk)
        try:
            return await asyncio.to_thread(import_from_chunks, filename, chunks, reference_dir(), validate)
        except Garage61ImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(422, 'Garage61 CSV를 가져오지 못했습니다. 파일과 데이터 폴더의 접근 권한·여유 공간을 확인하세요.') from exc
