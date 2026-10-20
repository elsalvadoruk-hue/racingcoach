"""LMU-only endpoints. No iRacing service or state is accessed."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, Query

from lmu_live import live_status
from lmu_telemetry import (
    LmuTelemetryFormatError, LmuTelemetryReader, LmuTelemetryUnavailable,
    discover_lmu_telemetry_dirs, list_lmu_telemetry_files, require_lmu_dependency,
)


@dataclass(frozen=True)
class LmuRoutesServices:
    discover_dirs: Callable = discover_lmu_telemetry_dirs
    list_files: Callable = list_lmu_telemetry_files
    reader_factory: Callable = LmuTelemetryReader
    dependency_check: Callable = require_lmu_dependency


def _file_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()[:24]


def _file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"file_id": _file_id(path), "file_name": path.name,
            "size_bytes": stat.st_size, "modified_at": stat.st_mtime}


def _inspect(path: Path, reader_factory: Callable) -> dict[str, Any]:
    info = _file_info(path)
    summary = reader_factory(path).summary()
    info.update({"status": "INSPECTED", "metadata": summary.metadata,
                 "channels": [asdict(spec) for spec in summary.channels],
                 "events": list(summary.events), "sample_counts": summary.sample_counts,
                 "laps": [asdict(lap) for lap in summary.laps],
                 "reference_status": "VALIDITY_UNVERIFIED", "best_lap_time_s": None})
    return info


def register_routes(app, services: LmuRoutesServices = LmuRoutesServices()):
    def dependency_check():
        try:
            services.dependency_check()
        except LmuTelemetryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def files():
        try:
            return services.list_files(services.discover_dirs())
        except OSError as exc:
            raise HTTPException(status_code=503, detail="LMU 폴더를 읽을 수 없습니다.") from exc

    @app.get("/api/lmu/status")
    async def get_lmu_status():
        def status():
            dependency, error = "READY", None
            try:
                services.dependency_check()
            except LmuTelemetryUnavailable as exc:
                dependency, error = "MISSING", str(exc)
            directories = services.discover_dirs()
            return {"status": "success", "provider": "LMU_DUCKDB",
                    "dependency": dependency, "error": error,
                    "telemetry_dirs": [{"path": str(path), "exists": path.is_dir()}
                                       for path in directories],
                    "file_count": len(files()), "live": live_status(),
                    "analysis": "NOT_IMPLEMENTED", "reference": "VALIDITY_UNVERIFIED"}
        return await asyncio.to_thread(status)

    @app.get("/api/lmu/live/status")
    async def get_lmu_live_status():
        return live_status()

    @app.get("/api/lmu/telemetry/files")
    async def get_lmu_files():
        def listing():
            # Listing does not open any database or require DuckDB.
            result = []
            for path in files():
                try:
                    result.append(_file_info(path))
                except FileNotFoundError:
                    continue  # The game/user may remove a file during discovery.
            return {"status": "success", "provider": "LMU_DUCKDB", "files": result}
        return await asyncio.to_thread(listing)

    @app.get("/api/lmu/telemetry/file")
    async def get_lmu_file(file_id: str = Query(pattern=r"^[a-f0-9]{24}$")):
        def inspect():
            dependency_check()
            path = next((item for item in files() if _file_id(item) == file_id), None)
            if path is None:
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            try:
                payload = _inspect(path, services.reader_factory)
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except (LmuTelemetryFormatError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"status": "success", "provider": "LMU_DUCKDB", "file": payload}
        return await asyncio.to_thread(inspect)
