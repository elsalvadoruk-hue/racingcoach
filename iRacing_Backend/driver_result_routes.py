"""Read-only result history routes; raw/manual rankings cannot become official."""
import asyncio
import sqlite3
from typing import Literal

from fastapi import HTTPException, Query

from driver_results import DriverResultsStore, read_driver_results


def register_result_routes(app, *, store_factory=None):
    factory = store_factory or DriverResultsStore

    @app.get('/api/driver-results')
    async def driver_results(game: Literal['iracing', 'lmu'] = 'iracing', period_days: int = Query(default=30)):
        if period_days not in {0, 30, 90}:
            raise HTTPException(422, '조회 기간은 전체(0), 30일 또는 90일로 선택해 주세요.')
        try:
            return await asyncio.to_thread(lambda: read_driver_results(game, period_days, store=factory()))
        except (OSError, sqlite3.Error, ValueError):
            raise HTTPException(503, '보관한 경기 결과를 읽지 못했습니다. 잠시 후 다시 시도해 주세요.') from None
