"""Corner-note endpoints with explicit application service bindings."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import logging
from typing import Callable, Literal, Any
from fastapi import HTTPException, Query
from api_route_models import CornerNoteRequest
from corner_notes import position_bucket

logger = logging.getLogger('iRacingSetupTuner')


@dataclass(frozen=True)
class CornerNotesRoutesServices:
    get_store: Callable[[], Any]
    get_corner_definition_store: Callable[[], Any]


def register_routes(app, services: CornerNotesRoutesServices):
    @app.get('/api/corner-notes')
    async def get_corner_notes(game: Literal['iracing', 'lmu'],
                               track_id: str = Query(min_length=1, max_length=160),
                               track_config: str = Query(default='', max_length=160)):
        try:
            return {'notes': await asyncio.to_thread(services.get_store().list, game, track_id, track_config)}
        except (OSError, ValueError, TypeError):
            logger.warning('Corner notes read failed')
            raise HTTPException(500, '메모 파일을 읽지 못했습니다. 파일 접근 권한과 파일 상태를 확인해 주세요.')

    def save(payload):
        confirmed = services.get_corner_definition_store().track_names(payload.track_id, payload.track_config)
        candidates = []
        if confirmed and confirmed.get('confirmed'):
            for point in confirmed.get('points', []):
                try:
                    if position_bucket(point['pct']) == position_bucket(payload.pct):
                        candidates.append(point)
                except (KeyError, TypeError, ValueError):
                    continue
        point = min(candidates, key=lambda row: abs(row['pct'] - payload.pct)) if candidates else {}
        return services.get_store().put(payload.game, payload.track_id, payload.track_config, payload.pct,
                                        payload.text, number=point.get('number'), name=point.get('name'))

    @app.put('/api/corner-notes')
    async def put_corner_note(payload: CornerNoteRequest):
        try:
            return await asyncio.to_thread(save, payload)
        except (OSError, ValueError, TypeError):
            logger.warning('Corner note write failed')
            raise HTTPException(500, '메모를 저장하지 못했습니다. 파일 접근 권한과 파일 상태를 확인한 뒤 다시 시도해 주세요.')
