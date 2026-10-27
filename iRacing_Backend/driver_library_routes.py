"""Small job-control API; the library service owns discovery and parsing."""
from typing import Literal

from fastapi import HTTPException


def register_library_routes(app, service, *, blocked=None):
    @app.get('/api/driver-history/library')
    async def library_status(game: Literal['iracing', 'lmu'] = 'iracing'):
        return service.status(game)

    @app.post('/api/driver-history/library')
    async def library_start(game: Literal['iracing', 'lmu'] = 'iracing'):
        if blocked and blocked():
            raise HTTPException(409, '기존 기록을 다시 확인 중입니다. 완료한 뒤 전체 파일 확인을 이어갈 수 있습니다.')
        return service.start(game)

    @app.post('/api/driver-history/library/pause')
    async def library_pause(game: Literal['iracing', 'lmu'] = 'iracing'):
        return service.pause(game)
