"""LMU file analysis endpoints and the live bridge's setup.

Live coaching (spotter, pit wall, questions, overlay) is not here: the shared
live coach reads LMU through ``lmu_irsdk.LmuIrsdk`` exactly like iRacing.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
import hashlib
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Body, HTTPException, Query, Request
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
    practice_store: Any = None
    corner_names_store: Any = None
    history_store: Any = None
    personal_reference_index: Any = None


# A recording's file name (as the shared workspace uses) or its 24-hex ID.
RECORDING = r'^(?:[a-f0-9]{24}|[^\\/:*?"<>|]{1,240}\.duckdb)$'


class ReplaySelection(BaseModel):
    file_id: str = Field(pattern=RECORDING)
    lap_number: int = Field(ge=0, strict=True)


class ReplayRequest(BaseModel):
    selections: list[ReplaySelection] = Field(min_length=2, max_length=6)
    points: int = Field(default=501, ge=3, le=1001, strict=True)


class CornerConsistencyRequest(BaseModel):
    selections: list[ReplaySelection] = Field(min_length=3, max_length=6)


def _matches(path: Path, name: str) -> bool:
    return name in (path.name, _file_id(path))


def _known(paths) -> dict[str, Path]:
    known = {}
    for path in paths:
        known.setdefault(_file_id(path), path)
        known.setdefault(path.name, path)
    return known


def _file_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()[:24]


def _file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    wal_present = Path(str(path) + '.wal').exists()
    return {"file_id": _file_id(path), "file_name": path.name,
            "size_bytes": stat.st_size, "modified_at": stat.st_mtime,
            "wal_present": wal_present,
            "snapshot_status": "WAL_PRESENT" if wal_present else "UNVERIFIED"}


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
    before = _file_fingerprint(path)
    info = _file_info(path)
    summary = reader_factory(path).summary()
    if before != _file_fingerprint(path):
        raise LmuTelemetryFormatError("검사 중 LMU 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.")
    info.update({"status": "INSPECTED", "metadata": summary.metadata,
                 "channels": [asdict(spec) for spec in summary.channels],
                 "events": list(summary.events), "sample_counts": summary.sample_counts,
                 "laps": [asdict(lap) for lap in summary.laps],
                 "reference_status": "VALIDITY_UNVERIFIED", "best_lap_time_s": None})
    return info


def register_routes(app, services: LmuRoutesServices = LmuRoutesServices()):
    from lmu_corner_names import LmuCornerNamesStore
    if services.corner_names_store is None:
        services = replace(services, corner_names_store=LmuCornerNamesStore())
    practice_store = services.practice_store if services.practice_store is not None else LmuPracticeStore()
    def bridge_status(*, prepare=False):
        from lmu_shared_memory import LmuLiveUnavailable, SharedMemorySource, _unavailable
        from lmu_irsdk import shared_memory_source_factory
        source_factory = shared_memory_source_factory()
        reader = 'bridge' if source_factory is SharedMemorySource else 'python'
        source = source_factory()
        try:
            if prepare:
                source.prepare(retry=True)
            else:
                source.probe()
            return {'state': 'READY', 'message': 'LMU 실시간 연결 준비됨 · LMU를 실행하고 차에 타면 자동으로 연결됩니다.',
                    'sdk_dir': str(source.sdk_dir), 'reader': reader}
        except LmuLiveUnavailable as exc:
            if source_factory is SharedMemorySource and exc.code in ('NATIVE_NOT_BUILT', 'ABI_MISMATCH'):
                from lmu_bridge_downloads import downloads
                state = downloads.status(source.sdk_dir)['state']
                if state == 'READY':
                    state = 'CONNECTION_DOWNLOAD_FAILED'
                exc = _unavailable(state)
            message = exc.message
            if reader == 'python' and exc.code in ('ABI_MISMATCH', 'NATIVE_NOT_BUILT'):
                message = 'LMU 연결 구성 파일을 확인할 수 없습니다. 앱을 다시 설치해 주세요.'
            return {'state': exc.code, 'message': message, 'sdk_dir': str(source.sdk_dir),
                    'buildable': False, 'reader': reader}
        finally:
            source.close()

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

    from lmu_settings import register_routes as register_settings_routes
    register_settings_routes(app, services.discover_dirs)

    @app.post('/api/lmu/telemetry/import')
    async def import_lmu_file(request: Request, filename: str = Query(max_length=240)):
        from lmu_imports import import_recording
        dependency_check()
        return await import_recording(request, filename, services.reader_factory)

    from lmu_history import register_history_routes
    async def history_workspace(body):
        shared = getattr(app.state, 'workspace_request', None)
        if shared is not None:
            return await shared(route='session', body=body)

        def legacy():
            from lmu_workspace import workspace_payload
            return workspace_payload(services, files(), _file_id, _file_fingerprint, 'session', body)
        return await asyncio.to_thread(legacy)
    register_history_routes(app, history_workspace, services.history_store)

    @app.post('/api/lmu/workspace/{route}')
    async def lmu_workspace_request(request: Request, route: Literal['files', 'file-laps', 'references', 'session', 'comparison', 'overlay', 'setup-plan'],
                                    body: dict[str, Any] = Body(default_factory=dict)):
        shared = getattr(request.app.state, 'workspace_request', None)
        if shared is not None:
            # LMU recordings run through the iRacing analysis workspace (lmu_ibt).
            try:
                services.dependency_check()
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if route == 'files':
                import lmu_recordings
                visibility = request.app.state.telemetry_file_visibility
                return await asyncio.to_thread(lmu_recordings.files_payload, visibility)
            try:
                result = await shared(route=route, body=body)
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if isinstance(result, dict):
                result.setdefault('provider', 'LMU')
            return result

        def legacy():
            # Standalone LMU routes (no main application): measurement-only payloads.
            from lmu_workspace import workspace_payload
            try:
                return workspace_payload(services, files(), _file_id, _file_fingerprint, route, body)
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail='LMU 기록 파일이 이동되거나 삭제됐습니다.') from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=422, detail='LMU 기록 파일에 접근할 수 없습니다. 기록을 종료한 뒤 다시 선택하세요.') from exc
        return await asyncio.to_thread(legacy)

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
                    "file_count": len(files()), "bridge": bridge_status(),
                    "diagnostic": journal().status(),
                    "analysis": "MANUAL_LAP_COMPARISON", "reference": "VALIDITY_UNVERIFIED",
                    "capabilities": ["DISTANCE_COMPARISON", "NORMALIZED_LAP_INPUTS",
                                     "SCHEMATIC_TRACK_PATH", "BRAKING_ZONES", "FULL_THROTTLE_ZONES",
                                     "MANUAL_FILE_COMPARISON", "TWO_LAP_PATH_OVERLAY", "MULTI_LAP_OFFLINE_REPLAY",
                                     "CORNER_REGION_COMPARISON", "MANUAL_CORNER_CONSISTENCY",
                                     "LOCAL_PRACTICE_HISTORY",
                                     "VEHICLE_CHANNEL_OBSERVATIONS", "VEHICLE_DISTANCE_COMPARISON",
                                     "RECORDING_SETUP_SUMMARY", "RECORDING_SETUP_COMPARISON",
                                     "LIVE_COACH_SHARED_WITH_IRACING"]}
        return await asyncio.to_thread(status)

    async def corner_names_request(body):
        from lmu_workspace import workspace_payload
        try:
            return await asyncio.to_thread(workspace_payload, services, files(),
                _file_id, _file_fingerprint, 'corner-names', body)
        except LmuTelemetryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail='선택한 LMU 파일을 찾을 수 없습니다.') from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get('/api/lmu/corner-names')
    async def get_lmu_corner_names(filename: str = Query(pattern=RECORDING),
            target_lap: int | None = Query(default=None, ge=0),
            reference_lap: int | None = Query(default=None, ge=0),
            reference_filename: str | None = Query(default=None, pattern=RECORDING)):
        return await corner_names_request({key: value for key, value in
            dict(filename=filename, target_lap=target_lap, reference_lap=reference_lap,
                 reference_filename=reference_filename).items() if value is not None})

    @app.put('/api/lmu/corner-names')
    async def put_lmu_corner_names(body: dict[str, Any] = Body(...)):
        if 'corners' not in body:
            raise HTTPException(status_code=422, detail='확정할 전체 구간 번호를 보내세요.')
        return await corner_names_request(body)

    @app.get('/api/lmu/live/bridge')
    async def get_lmu_bridge():
        return await asyncio.to_thread(bridge_status)

    @app.post('/api/lmu/live/connect')
    async def connect_lmu_live():
        return await asyncio.to_thread(lambda: bridge_status(prepare=True))

    @app.post('/api/lmu/live/bridge/build')
    async def legacy_build_lmu_bridge():
        # Older cached screens may still send this request. Prepare a prebuilt
        # reader for them too; an application route must never invoke a compiler.
        return await asyncio.to_thread(lambda: bridge_status(prepare=True))

    def journal():
        from lmu_irsdk import shared
        return shared().journal

    @app.get("/api/lmu/live/journal")
    async def get_lmu_journal():
        return await asyncio.to_thread(lambda: journal().status())

    @app.post("/api/lmu/live/journal/start")
    async def start_lmu_journal():
        return await asyncio.to_thread(lambda: journal().start())

    @app.post("/api/lmu/live/journal/stop")
    async def stop_lmu_journal():
        return await asyncio.to_thread(lambda: journal().stop())

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
    async def get_lmu_file(file_id: str = Query(pattern=RECORDING)):
        def inspect():
            dependency_check()
            path = next((item for item in files() if _matches(item, file_id)), None)
            if path is None:
                raise HTTPException(status_code=404, detail="선택한 LMU 파일을 찾을 수 없습니다.")
            try:
                payload = _inspect(path, services.reader_factory)
            except LmuTelemetryUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="LMU 파일이 이동되거나 삭제됐습니다.") from exc
            except LmuTelemetryFormatError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=422, detail="LMU 기록 파일에 접근할 수 없습니다. 기록을 종료한 뒤 다시 검사하세요.") from exc
            return {"status": "success", "provider": "LMU_DUCKDB", "file": payload}
        return await asyncio.to_thread(inspect)

    def setup_read(file_ids):
        from lmu_setup import setup_report, compare_setups
        dependency_check()
        known = _known(files())
        if any(key not in known for key in file_ids):
            raise HTTPException(status_code=404, detail='선택한 LMU 파일을 찾을 수 없습니다.')
        paths = [known[key] for key in file_ids]
        if len(paths) == 2 and paths[0].resolve() == paths[1].resolve():
            raise HTTPException(status_code=422, detail='서로 다른 두 기록 파일을 선택하세요.')
        try:
            if len(paths) == 2 and paths[0].samefile(paths[1]):
                raise ValueError('서로 다른 두 기록 파일을 선택하세요.')
            before = [_file_fingerprint(path) for path in paths]
            reports = [setup_report(services.reader_factory(path)) for path in paths]
            report = reports[0] if len(reports) == 1 else compare_setups(*reports)
            if before != [_file_fingerprint(path) for path in paths]:
                raise ValueError('셋업 정보를 읽는 중 기록 파일이 변경됐습니다. 기록을 종료한 뒤 다시 검사하세요.')
        except LmuTelemetryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail='LMU 파일이 이동하거나 삭제됐습니다.') from exc
        except OSError as exc:
            raise HTTPException(status_code=422, detail='LMU 기록 파일을 읽을 수 없습니다. 기록 종료와 파일 접근 권한을 확인하세요.') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sources = [{'file_id': key, 'file_name': path.name} for key, path in zip(file_ids, paths)]
        return {'status': 'success', 'provider': 'LMU_DUCKDB',
                'setup' if len(paths) == 1 else 'comparison': report, 'sources': sources}

    @app.get('/api/lmu/telemetry/setup')
    async def get_setup(file_id: str = Query(pattern=RECORDING)):
        return await asyncio.to_thread(setup_read, [file_id])

    @app.get('/api/lmu/telemetry/setup-comparison')
    async def get_setup_comparison(reference_file_id: str = Query(pattern=RECORDING),
                                   target_file_id: str = Query(pattern=RECORDING)):
        return await asyncio.to_thread(setup_read, [reference_file_id, target_file_id])

    @app.get("/api/lmu/telemetry/lap-analysis")
    async def analyze_lmu_lap(file_id: str = Query(pattern=RECORDING),
                              lap_number: int = Query(ge=0),
                              points: int = Query(default=301, ge=3, le=501)):
        def analyze():
            dependency_check()
            path = next((item for item in files() if _matches(item, file_id)), None)
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
            known = _known(files())
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
    async def analyze_vehicle_channels(file_id: str = Query(pattern=RECORDING),
                                       lap_number: int = Query(ge=0),
                                       channel_name: str = Query(default='TyresPressure', max_length=64),
                                       points: int = Query(default=301, ge=3, le=501)):
        def analyze():
            from lmu_vehicle import vehicle_report
            dependency_check()
            path = next((p for p in files() if _matches(p, file_id)), None)
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
    async def compare_vehicle_channels(reference_file_id: str = Query(pattern=RECORDING),
                                       target_file_id: str = Query(pattern=RECORDING),
                                       reference_lap_number: int = Query(ge=0),
                                       target_lap_number: int = Query(ge=0),
                                       channel_name: str = Query(default='TyresPressure', max_length=64),
                                       points: int = Query(default=301, ge=3, le=501)):
        def compare():
            from lmu_vehicle_compare import compare_vehicle_laps
            dependency_check()
            known = _known(files())
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
            known = _known(files())
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
        known = _known(files())
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
    async def compare_lmu_files(reference_file_id: str = Query(pattern=RECORDING),
                                target_file_id: str = Query(pattern=RECORDING),
                                reference_lap: int = Query(ge=0), target_lap: int = Query(ge=0),
                                points: int = Query(default=301, ge=3, le=501),
                                sector_length_m: int = Query(default=500, ge=100, le=2000)):
        def compare():
            dependency_check()
            known = _known(files())
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
    async def compare_lmu_laps(file_id: str = Query(pattern=RECORDING),
                               reference_lap: int = Query(ge=0), target_lap: int = Query(ge=0),
                               points: int = Query(default=201, ge=2, le=501),
                               sector_length_m: int = Query(default=500, ge=100, le=2000)):
        def compare():
            dependency_check()
            path = next((item for item in files() if _matches(item, file_id)), None)
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
