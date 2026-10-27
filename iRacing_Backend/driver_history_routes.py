"""Read-only local access to observed recording history, independent of the report UI."""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Literal

from fastapi import HTTPException, Path, Query

from driver_history import DriverHistoryStore


def register_routes(app, *, store_factory=None):
    factory = store_factory or DriverHistoryStore

    async def read(method, **kwargs):
        try:
            return await asyncio.to_thread(lambda: getattr(factory(), method)(**kwargs))
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise HTTPException(503, '주행 기록을 읽지 못했습니다. 저장 위치와 접근 권한을 확인하세요.') from exc

    @app.get('/api/driver-report')
    async def driver_report(game: Literal['iracing', 'lmu'] = 'iracing', period_days: int = Query(default=30)):
        if period_days not in {0, 30, 90}:
            raise HTTPException(422, '조회 기간은 전체(0), 30일 또는 90일로 선택하세요.')
        from driver_report import read_driver_report
        try:
            return await asyncio.to_thread(read_driver_report, game, period_days, store=factory())
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise HTTPException(503, '드라이버 리포트를 읽지 못했습니다. 잠시 후 다시 시도하세요.') from exc

    @app.get('/api/driver-history/status')
    async def status(game: Literal['iracing', 'lmu'] | None = None):
        return await read('status', game=game)

    @app.get('/api/driver-history/drives')
    async def drives(game: Literal['iracing', 'lmu'] | None = None,
                     limit: int = Query(default=100, ge=1, le=200),
                     offset: int = Query(default=0, ge=0)):
        items = await read('list_drives', game=game, limit=limit, offset=offset)
        return {'drives': items, 'limit': limit, 'offset': offset}

    @app.get('/api/driver-history/drives/{drive_id}')
    async def drive(drive_id: str = Path(pattern=r'^[a-f0-9]{64}$')):
        item = await read('get_drive', drive_id=drive_id)
        if item is None:
            raise HTTPException(404, '저장된 주행 기록을 찾을 수 없습니다.')
        return {'drive': item}
