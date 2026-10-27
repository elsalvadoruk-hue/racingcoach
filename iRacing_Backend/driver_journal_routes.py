"""Explicit local journal mutations; passive views do not evaluate goals."""
import asyncio
import sqlite3
from typing import Literal

from fastapi import HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict

from driver_history import DriverHistoryStore
from driver_journal import DriverJournal, JournalError, journal_context


class FeelingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    response: Literal['COMFORTABLE', 'NEUTRAL', 'DIFFICULT', 'UNSURE']
    difficulty: Literal['BRAKING', 'CORNERING', 'ACCELERATION', 'OVERALL'] | None = None


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str
    drive_id: str


def register_journal_routes(app, *, history_factory=None, journal_factory=None):
    history_factory = history_factory or DriverHistoryStore

    async def run(game, action=None, drive_id=None):
        def work():
            history = history_factory()
            journal = journal_factory() if journal_factory else DriverJournal(history.path)
            snapshot = history.report_snapshot(game)
            if action:
                action(journal, snapshot)
            return journal_context(game, snapshot, journal=journal, drive_id=drive_id)
        try:
            return await asyncio.to_thread(work)
        except JournalError as error:
            raise HTTPException(error.status_code, str(error)) from error
        except (OSError, sqlite3.Error, ValueError, TypeError) as error:
            raise HTTPException(503, '체감·도전 기록을 처리하지 못했습니다. 잠시 후 다시 시도하세요.') from error

    @app.get('/api/driver-journal')
    async def get_journal(game: Literal['iracing', 'lmu'] = 'iracing',
                          drive_id: str | None = Query(default=None, pattern=r'^[a-f0-9]{64}$')):
        return await run(game, drive_id=drive_id)

    @app.put('/api/driver-journal/feelings/{drive_id}')
    async def put_feeling(body: FeelingRequest, drive_id: str = Path(pattern=r'^[a-f0-9]{64}$'),
                          game: Literal['iracing', 'lmu'] = 'iracing'):
        return await run(game, lambda journal, snapshot: journal.save_feeling(snapshot, game, drive_id, body.response, body.difficulty), drive_id)

    @app.delete('/api/driver-journal/feelings/{drive_id}')
    async def delete_feeling(drive_id: str = Path(pattern=r'^[a-f0-9]{64}$'), game: Literal['iracing', 'lmu'] = 'iracing'):
        return await run(game, lambda journal, snapshot: journal.delete_feeling(snapshot, game, drive_id), drive_id)

    @app.post('/api/driver-journal/challenges')
    async def accept_challenge(body: ChallengeRequest, game: Literal['iracing', 'lmu'] = 'iracing'):
        return await run(game, lambda journal, snapshot: journal.accept_challenge(snapshot, game, body.drive_id, body.candidate_id), body.drive_id)

    @app.post('/api/driver-journal/challenges/{challenge_id}/cancel')
    async def cancel_challenge(challenge_id: str = Path(pattern=r'^[a-f0-9]{32}$'), game: Literal['iracing', 'lmu'] = 'iracing',
                               drive_id: str | None = Query(default=None, pattern=r'^[a-f0-9]{64}$')):
        return await run(game, lambda journal, snapshot: journal.cancel_challenge(game, challenge_id), drive_id)

    @app.post('/api/driver-journal/reconcile')
    async def reconcile(game: Literal['iracing', 'lmu'] = 'iracing',
                        drive_id: str | None = Query(default=None, pattern=r'^[a-f0-9]{64}$')):
        return await run(game, lambda journal, snapshot: journal.reconcile(snapshot, game), drive_id)
