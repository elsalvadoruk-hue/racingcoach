"""LMU-only endpoints. No iRacing service or state is accessed."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import threading
from typing import Any, Callable

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from lmu_analysis import lap_report
from lmu_comparison import compare_laps as compare_normalized_laps
from lmu_corners import compare_corners
from lmu_consistency import corner_consistency
from lmu_replay import replay_laps
from lmu_practice import create_practice_baseline, compare_practice
from lmu_practice_store import LmuPracticeStore
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
    live_service: Any = None
    practice_store: Any = None


class ReplaySelection(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    lap_number: int = Field(ge=0, strict=True)


class ReplayRequest(BaseModel):
    selections: list[ReplaySelection] = Field(min_length=2, max_length=6)
    points: int = Field(default=501, ge=3, le=1001, strict=True)


class CornerConsistencyRequest(BaseModel):
    selections: list[ReplaySelection] = Field(min_length=3, max_length=6)


def _file_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()[:24]


def _file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"file_id": _file_id(path), "file_name": path.name,
            "size_bytes": stat.st_size, "modified_at": stat.st_mtime}


def _file_fingerprint(path: Path):
    # Pending DuckDB writes may change only its WAL, leaving the main file intact.
    main = path.stat()
    try:
        wal = Path(str(path) + '.wal').stat()
        wal_signature = (wal.st_size, wal.st_mtime_ns)
    except FileNotFoundError:
        wal_signature = None
    return main.st_size, main.st_mtime_ns, wal_signature


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
    practice_store = services.practice_store if services.practice_store is not None else LmuPracticeStore()
    # Resolve the default only on a live request. Importing/serving the app must
    # neither attach to the game nor start a collector.
    live_service_instance = services.live_service
    live_service_lock = threading.Lock()

    def live_service():
        nonlocal live_service_instance
        with live_service_lock:
            if live_service_instance is None:
                from lmu_live import get_live_service
                live_service_instance = get_live_service()
            return live_service_instance

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lmu_lifespan(application):
        async with previous_lifespan(application) as state:
            try:
                yield state
            finally:
                if live_service_instance is not None:
                    await asyncio.to_thread(live_service_instance.stop)

    app.router.lifespan_context = lmu_lifespan

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
                    "file_count": len(files()), "live": live_service().status(),
                    "analysis": "MANUAL_LAP_COMPARISON", "reference": "VALIDITY_UNVERIFIED",
                    "capabilities": ["DISTANCE_COMPARISON", "NORMALIZED_LAP_INPUTS",
                                     "SCHEMATIC_TRACK_PATH", "BRAKING_ZONES", "FULL_THROTTLE_ZONES",
                                     "MANUAL_FILE_COMPARISON", "TWO_LAP_PATH_OVERLAY", "MULTI_LAP_OFFLINE_REPLAY",
                                     "CORNER_REGION_COMPARISON", "MANUAL_CORNER_CONSISTENCY",
                                     "LOCAL_PRACTICE_HISTORY",
                                     "VEHICLE_CHANNEL_OBSERVATIONS", "VEHICLE_DISTANCE_COMPARISON",
                                     "LIVE_DIAGNOSTICS_CONTROL"]}
        return await asyncio.to_thread(status)

    @app.get("/api/lmu/live/status")
    async def get_lmu_live_status():
        return await asyncio.to_thread(lambda: live_service().status())

    @app.post("/api/lmu/live/start")
    async def start_lmu_live():
        return await asyncio.to_thread(lambda: live_service().start())

    @app.post("/api/lmu/live/stop")
    async def stop_lmu_live():
        return await asyncio.to_thread(lambda: live_service().stop())

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

    @app.get("/api/lmu/telemetry/lap-analysis")
    async def analyze_lmu_lap(file_id: str = Query(pattern=r"^[a-f0-9]{24}$"),
                              lap_number: int = Query(ge=0),
                              points: int = Query(default=301, ge=3, le=501)):
        def analyze():
            dependency_check()
            path = next((item for item in files() if _file_id(item) == file_id), None)
            if path is None:
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            try:
                result = lap_report(services.reader_factory(path).analysis_lap(lap_number), points)
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"status": "success", "provider": "LMU_DUCKDB", "file_id": file_id,
                    "analysis": result}
        return await asyncio.to_thread(analyze)

    @app.post("/api/lmu/telemetry/replay")
    async def replay_lmu_laps(request: ReplayRequest):
        def replay():
            dependency_check()
            selected = [(item.file_id, item.lap_number) for item in request.selections]
            if len(set(selected)) != len(selected):
                raise HTTPException(status_code=422, detail="같은 파일의 같은 랩을 중복 선택할 수 없습니다.")
            known = {_file_id(path): path for path in files()}
            if any(key not in known for key, _ in selected):
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            paths = [known[key] for key, _ in selected]
            try:
                before = [_file_fingerprint(path) for path in paths]
                laps = [services.reader_factory(path).analysis_lap(number)
                        for path, (_, number) in zip(paths, selected)]
                result = replay_laps(laps, request.points)
                after = [_file_fingerprint(path) for path in paths]
                if before != after:
                    raise ValueError("재생 데이터 생성 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.")
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            result["sources"] = [{"selection_index": index, "file_id": key, "file_name": path.name,
                                  "lap_number": number, "recording_time": lap.metadata.get("recording_time")}
                                 for index, (path, (key, number), lap) in enumerate(zip(paths, selected, laps))]
            return {"status": "success", "provider": "LMU_DUCKDB", "replay": result}
        return await asyncio.to_thread(replay)

    @app.get('/api/lmu/telemetry/vehicle-channels')
    async def analyze_vehicle_channels(file_id: str = Query(pattern=r'^[a-f0-9]{24}$'),
                                       lap_number: int = Query(ge=0),
                                       channel_name: str = Query(default='TyresPressure', max_length=64),
                                       points: int = Query(default=301, ge=3, le=501)):
        def analyze():
            from lmu_vehicle import vehicle_report
            dependency_check()
            path = next((p for p in files() if _file_id(p) == file_id), None)
            if path is None:
                raise HTTPException(status_code=404, detail='선택한 LMU 파일을 찾을 수 없습니다.')
            try:
                before = _file_fingerprint(path)
                report = vehicle_report(services.reader_factory(path), lap_number, channel_name, points)
                if before != _file_fingerprint(path):
                    raise ValueError('차량 상태 분석 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.')
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail='LMU 파일이 이동되거나 삭제됐습니다.') from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {'status': 'success', 'provider': 'LMU_DUCKDB', 'vehicle': report}
        return await asyncio.to_thread(analyze)

    @app.get('/api/lmu/telemetry/vehicle-comparison')
    async def compare_vehicle_channels(reference_file_id: str = Query(pattern=r'^[a-f0-9]{24}$'),
                                       target_file_id: str = Query(pattern=r'^[a-f0-9]{24}$'),
                                       reference_lap_number: int = Query(ge=0),
                                       target_lap_number: int = Query(ge=0),
                                       channel_name: str = Query(default='TyresPressure', max_length=64),
                                       points: int = Query(default=301, ge=3, le=501)):
        def compare():
            from lmu_vehicle_compare import compare_vehicle_laps
            dependency_check()
            known = {_file_id(path): path for path in files()}
            if reference_file_id not in known or target_file_id not in known:
                raise HTTPException(status_code=404, detail='선택한 LMU 파일을 찾을 수 없습니다.')
            if reference_file_id == target_file_id and reference_lap_number == target_lap_number:
                raise HTTPException(status_code=422, detail='같은 파일의 같은 랩은 비교할 수 없습니다.')
            paths = [known[key] for key in (reference_file_id, target_file_id)]
            try:
                before = [_file_fingerprint(path) for path in paths]
                report = compare_vehicle_laps(services.reader_factory(paths[0]), reference_lap_number,
                                              services.reader_factory(paths[1]), target_lap_number,
                                              channel_name, points)
                if before != [_file_fingerprint(path) for path in paths]:
                    raise ValueError('차량 상태 비교 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.')
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail='LMU 파일이 이동하거나 삭제됐습니다.') from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {'status': 'success', 'provider': 'LMU_DUCKDB', 'comparison': report,
                    'sources': {role: {'file_id': key, 'file_name': path.name}
                                for role, key, path in zip(('reference', 'target'),
                                    (reference_file_id, target_file_id), paths)}}
        return await asyncio.to_thread(compare)

    @app.post("/api/lmu/telemetry/corner-consistency")
    async def analyze_lmu_corner_consistency(request: CornerConsistencyRequest):
        def analyze():
            dependency_check()
            selected = [(item.file_id, item.lap_number) for item in request.selections]
            if len(set(selected)) != len(selected):
                raise HTTPException(status_code=422, detail="같은 파일의 같은 랩을 중복 선택할 수 없습니다.")
            known = {_file_id(path): path for path in files()}
            if any(key not in known for key, _ in selected):
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            paths = [known[key] for key, _ in selected]
            try:
                before = [_file_fingerprint(path) for path in paths]
                laps = [services.reader_factory(path).analysis_lap(number)
                        for path, (_, number) in zip(paths, selected)]
                result = corner_consistency(laps)
                after = [_file_fingerprint(path) for path in paths]
                if before != after:
                    raise ValueError("일관성 분석 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.")
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            result["sources"] = [{"selection_index": index, "file_id": key, "file_name": path.name,
                                  "lap_number": number}
                                 for index, (path, (key, number)) in enumerate(zip(paths, selected))]
            return {"status": "success", "provider": "LMU_DUCKDB", "consistency": result}
        return await asyncio.to_thread(analyze)

    def practice_operation(operation):
        try:
            return operation()
        except LmuTelemetryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="선택한 연습 과제 또는 LMU 파일을 찾을 수 없습니다.") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def practice_laps(request, operation):
        dependency_check()
        selected = [(item.file_id, item.lap_number) for item in request.selections]
        if len(set(selected)) != len(selected):
            raise ValueError("같은 파일의 같은 랩을 중복 선택할 수 없습니다.")
        known = {_file_id(path): path for path in files()}
        paths = [known[key] for key, _ in selected]
        before = [_file_fingerprint(path) for path in paths]
        def hashes():
            result = {}
            for path in set(paths):
                with path.open('rb') as stream:
                    main = hashlib.file_digest(stream, 'sha256').hexdigest()
                wal_path = Path(str(path)+'.wal')
                try:
                    with wal_path.open('rb') as stream:
                        wal = hashlib.file_digest(stream, 'sha256').hexdigest()
                except FileNotFoundError:
                    wal = None
                result[path] = (main, wal)
            return result
        content = hashes()
        laps = [services.reader_factory(path).analysis_lap(number)
                for path, (_, number) in zip(paths, selected)]
        sources = [{'selection_index': i, 'file_id': key, 'file_name': path.name,
                    'lap_number': number, 'sha256': content[path][0], 'wal_sha256': content[path][1]}
                   for i, (path, (key, number)) in enumerate(zip(paths, selected))]
        result = operation(laps, sources)
        if before != [_file_fingerprint(path) for path in paths] or content != hashes():
            raise ValueError("연습 분석 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.")
        return result

    @app.get('/api/lmu/practice/tasks')
    async def list_practice_tasks():
        return await asyncio.to_thread(lambda: practice_operation(lambda:
            {'status': 'success', 'tasks': practice_store.list()}))

    @app.post('/api/lmu/practice/tasks')
    async def create_practice_task(request: CornerConsistencyRequest):
        def create():
            baseline = practice_laps(request, create_practice_baseline)
            return {'status': 'success', 'task': practice_store.create(baseline)}
        return await asyncio.to_thread(lambda: practice_operation(create))

    @app.get('/api/lmu/practice/tasks/{task_id}')
    async def get_practice_task(task_id: str):
        return await asyncio.to_thread(lambda: practice_operation(lambda:
            {'status': 'success', 'task': practice_store.get(task_id),
             'comparisons': practice_store.comparisons(task_id)}))

    @app.post('/api/lmu/practice/tasks/{task_id}/compare')
    async def compare_practice_task(task_id: str, request: CornerConsistencyRequest):
        def compare():
            baseline = practice_store.get(task_id)['baseline']
            report = practice_laps(request, lambda laps, sources: compare_practice(baseline, laps, sources))
            return {'status': 'success', 'comparison': practice_store.add_comparison(task_id, report)}
        return await asyncio.to_thread(lambda: practice_operation(compare))

    @app.get("/api/lmu/telemetry/compare-files")
    async def compare_lmu_files(reference_file_id: str = Query(pattern=r"^[a-f0-9]{24}$"),
                                target_file_id: str = Query(pattern=r"^[a-f0-9]{24}$"),
                                reference_lap: int = Query(ge=0), target_lap: int = Query(ge=0),
                                points: int = Query(default=301, ge=3, le=501),
                                sector_length_m: int = Query(default=500, ge=100, le=2000)):
        def compare():
            dependency_check()
            known = {_file_id(path): path for path in files()}
            if reference_file_id not in known or target_file_id not in known:
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            if reference_file_id == target_file_id and reference_lap == target_lap:
                raise HTTPException(status_code=422, detail="같은 파일의 같은 랩은 비교할 수 없습니다.")
            paths = [known[key] for key in (reference_file_id, target_file_id)]
            try:
                before = [_file_fingerprint(path) for path in paths]
                laps = [services.reader_factory(path).analysis_lap(number)
                        for path, number in zip(paths, (reference_lap, target_lap))]
                result = compare_normalized_laps(*laps, points=points, sector_length_m=sector_length_m)
                result["corner_comparison"] = compare_corners(*laps)
                after = [_file_fingerprint(path) for path in paths]
                if before != after:
                    raise ValueError("비교 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.")
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            sources = {role: {"file_id": key, "file_name": path.name,
                              "recording_time": lap.metadata.get("recording_time")}
                       for role, key, path, lap in zip(("reference", "target"),
                           (reference_file_id, target_file_id), paths, laps)}
            return {"status": "success", "provider": "LMU_DUCKDB", "sources": sources,
                    "reference_status": "VALIDITY_UNVERIFIED", "comparison": result}
        return await asyncio.to_thread(compare)

    @app.get("/api/lmu/telemetry/compare")
    async def compare_lmu_laps(file_id: str = Query(pattern=r"^[a-f0-9]{24}$"),
                               reference_lap: int = Query(ge=0), target_lap: int = Query(ge=0),
                               points: int = Query(default=201, ge=2, le=501),
                               sector_length_m: int = Query(default=500, ge=100, le=2000)):
        def compare():
            dependency_check()
            path = next((item for item in files() if _file_id(item) == file_id), None)
            if path is None:
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            try:
                result = services.reader_factory(path).compare_laps(reference_lap, target_lap, points, sector_length_m)
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"status": "success", "provider": "LMU_DUCKDB", "file_id": file_id,
                    "reference_status": "VALIDITY_UNVERIFIED", "comparison": asdict(result)}
        return await asyncio.to_thread(compare)
