"""Keep user-selected recordings as new local copies, never modify the source."""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import uuid

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app_settings import data_dir
from lmu_telemetry import LmuTelemetryFormatError, LmuTelemetryUnavailable


def import_dir():
    return data_dir() / 'lmu-imports'


async def import_recording(request, filename, reader_factory):
    # A browser supplies only a name. Do not accept a client-supplied path.
    if (not filename or len(filename) > 240 or
            re.search(r'[\\/:*?"<>|\x00-\x1f]', filename) or
            Path(filename).suffix.casefold() != '.duckdb'):
        raise HTTPException(422, '완료된 LMU .duckdb 파일을 선택하세요.')
    folder = import_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # Staging is outside the catalog's non-recursive *.duckdb discovery.
        with tempfile.TemporaryDirectory(prefix='.pending-', dir=folder) as staging:
            pending = Path(staging) / 'recording.duckdb'
            size = 0
            with pending.open('xb') as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    await run_in_threadpool(handle.write, chunk)
            if not size:
                raise HTTPException(422, '선택한 파일이 비어 있습니다.')
            await run_in_threadpool(lambda: reader_factory(pending).summary())
            # A unique name preserves existing copies, even for equal source names.
            name = f'{Path(filename).stem[:180]}-{uuid.uuid4().hex[:12]}.duckdb'
            destination = folder / name
            pending.rename(destination)
            from lmu_recordings import file_id
            return {'provider': 'LMU', 'file': {'name': name, 'file_id': file_id(destination),
                    'file_name': name, 'display_name': name, 'analyzable': True}}
    except LmuTelemetryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except LmuTelemetryFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(422, 'LMU 파일을 가져오지 못했습니다. 파일과 데이터 폴더의 접근 권한·여유 공간을 확인하세요.') from exc
