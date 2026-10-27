"""Optional driver feelings and explicit, evidence-based follow-up goals.

Only compact history snapshots are used. Reads never create storage, change goal
state, open recordings, or infer feelings from telemetry.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from statistics import median
from uuid import uuid4

from driver_history import DriverHistoryStore
from driver_report import _lap_selection, _replay_status


_SHA = re.compile(r'[a-f0-9]{64}\Z')
_FEELINGS = {'COMFORTABLE', 'NEUTRAL', 'DIFFICULT', 'UNSURE'}
_DIFFICULTIES = {'BRAKING', 'CORNERING', 'ACCELERATION', 'OVERALL'}
_COLUMNS = {
    'feelings': ('game', 'drive_id', 'response', 'difficulty', 'updated_at'),
    'challenges': ('id', 'game', 'status', 'payload'),
}
_REASONS = {
    'NEW_DRIVE_REQUIRED': '선택한 뒤 새로 달린 같은 차량·코스의 독립 주행을 기다립니다.',
    'NATIVE_IDENTITY_REQUIRED': '후속 기록의 독립 주행 식별이 미확정이라 달성을 보류합니다.',
    'REPLAY_UNCONFIRMED': '후속 기록이 실제 주행인지 확인되지 않아 달성을 보류합니다.',
    'RECORDED_AT_REQUIRED': '후속 기록의 실제 주행 날짜가 없어 달성을 보류합니다.',
    'BEFORE_ACCEPTANCE': '목표 선택 전에 시작한 주행은 달성에 소급 반영하지 않습니다.',
    'FUTURE_RECORDING': '실제 주행 날짜가 현재보다 뒤에 있어 달성을 보류합니다.',
    'INSUFFICIENT_LAPS': '목표에 필요한 측정 랩 수가 아직 부족합니다.',
    'PACE_NOT_MET': '측정 페이스가 선택한 목표 범위에 아직 닿지 않았습니다.',
    'CONSISTENCY_NOT_MET': '랩 간 편차가 선택한 목표 범위에 아직 닿지 않았습니다.',
    'ACHIEVED': '선택 뒤의 독립 주행에서 측정 목표를 확인했습니다.',
}


class JournalError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def _game(game):
    if game not in {'iracing', 'lmu'}:
        raise JournalError('지원하지 않는 게임입니다.')


def _id(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise JournalError('주행 식별자가 올바르지 않습니다.')
    return value


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_dump(value).encode('utf-8')).hexdigest()


def _drives(snapshot, game):
    _game(game)
    return sorted((row for row in snapshot.get('drives', []) if row.get('game') == game),
                  key=lambda row: (row.get('recorded_at') or '', row['drive_id']), reverse=True)


def _drive(snapshot, game, drive_id):
    _id(drive_id)
    found = next((row for row in _drives(snapshot, game) if row['drive_id'] == drive_id), None)
    if found is None:
        raise JournalError('이 게임에 저장된 대상 주행을 찾을 수 없습니다.', 404)
    return found


def _scope(drive):
    return {key: drive.get(key, '') for key in ('car_key', 'track_key', 'layout_key')}


def _stats(laps):
    values = [row['lap_time_s'] for row in laps]
    center = median(values)
    return dict(lap_count=len(values), median_lap_s=round(center, 6),
                mad_pct=round(median(abs(value-center) for value in values)/center*100., 6))


def _candidates(drive, completed):
    if drive is None:
        return [], '저장된 주행을 선택하면 측정 근거에 맞는 목표를 확인할 수 있습니다.'
    if _date(drive.get('recorded_at')) is None:
        return [], '실제 주행 날짜가 확인된 기록에서 목표를 선택할 수 있습니다.'
    if any(not value or value.upper() == 'UNKNOWN' for value in _scope(drive).values()):
        return [], '차량·코스·레이아웃을 확인할 수 없어 도전을 아직 고정할 수 없습니다.'
    laps, _ = _lap_selection(drive)
    if len(laps) < 3:
        return [], '측정 가능한 랩이 3개 이상인 주행에서 목표를 선택할 수 있습니다.'
    baseline = _stats(laps)
    baseline['replay_status'] = ('CONFIRMED_NON_REPLAY' if all(
        lap.get('replay_status') == 'CONFIRMED_NON_REPLAY' for lap in laps) else 'UNKNOWN')
    pace = round(baseline['median_lap_s'] * 1.01, 6)
    rows = []
    for kind, title, target in [
        ('PACE_REPEAT', '측정 페이스 3랩 재현', dict(min_laps=3, pace_limit_s=pace, mad_limit_pct=None)),
        ('CONSISTENCY_HOLD', '페이스와 편차 5랩 유지', dict(min_laps=5, pace_limit_s=pace,
            mad_limit_pct=round(max(.2, baseline['mad_pct'] + .1), 6))),
    ]:
        basis = _hash([kind, _scope(drive), target])
        if basis in completed:
            continue
        rows.append(dict(candidate_id=_hash([drive['drive_id'], drive['source_ids'], baseline, basis]),
            basis_key=basis, drive_id=drive['drive_id'], kind=kind, title=title,
            description=(f"같은 차량·코스·레이아웃의 새 독립 주행에서 측정 랩 {target['min_laps']}개 이상, "
                         f"중앙 랩타임 {pace:.3f}초 이내"
                         + (f", 편차 {target['mad_limit_pct']:.3f}% 이내를 확인합니다." if kind == 'CONSISTENCY_HOLD' else '를 확인합니다.')),
            target=target, baseline=baseline, scope=_scope(drive),
            reason='관측한 기록을 기준으로 정한 측정 목표입니다. 조건이 달라질 수 있으며 실력 성장이나 공식 유효 판정은 아닙니다.'))
    return rows, ('' if rows else '같은 기준의 목표는 이미 달성했습니다. 다른 측정 기준이 생기면 다음 목표를 제안합니다.')


def _evaluation(reason, *, evidence=None, status='UNVERIFIED'):
    return dict(status=status, reason_codes=[reason], reason=_REASONS[reason], evidence=evidence)


class DriverJournal:
    def __init__(self, history_path=None, *, path=None, clock=None):
        ledger = Path(history_path) if history_path is not None else DriverHistoryStore().path
        override = os.environ.get('IRACING_DRIVER_JOURNAL_PATH', '').strip()
        self.path = Path(path if path is not None else override or ledger.with_name(ledger.stem + '.journal.sqlite3')).expanduser()
        if self.path.resolve() == ledger.resolve():
            raise JournalError('체감·도전 저장소는 주행 원장과 다른 파일이어야 합니다.')
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise JournalError('저장 시각을 확인할 수 없습니다.', 503)
        return value.astimezone(timezone.utc)

    @contextmanager
    def _connection(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path if write else self.path.resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(target, uri=not write, timeout=1.)
        try:
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if write and not tables and version == 0:
                connection.execute('CREATE TABLE feelings(game TEXT NOT NULL,drive_id TEXT NOT NULL,response TEXT NOT NULL,difficulty TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(game,drive_id))')
                connection.execute('CREATE TABLE challenges(id TEXT PRIMARY KEY,game TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)')
                connection.execute("CREATE UNIQUE INDEX one_active_challenge ON challenges(game) WHERE status='ACTIVE'")
                connection.execute('PRAGMA user_version=1')
            elif version != 1 or tables != set(_COLUMNS) or any(tuple(row[1] for row in connection.execute(f'PRAGMA table_info({table})')) != columns for table, columns in _COLUMNS.items()):
                raise JournalError('지원하지 않는 체감·도전 저장소 형식입니다.', 503)
            yield connection
            if write:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def read(self, game):
        _game(game)
        if not self.path.exists():
            return {'feelings': {}, 'challenges': []}
        with self._connection() as db:
            feelings = {drive_id: dict(response=response, difficulty=difficulty, updated_at=stamp)
                        for drive_id, response, difficulty, stamp in db.execute(
                            'SELECT drive_id,response,difficulty,updated_at FROM feelings WHERE game=?', (game,))}
            challenges = [json.loads(raw) for raw, in db.execute('SELECT payload FROM challenges WHERE game=? ORDER BY rowid DESC', (game,))]
        return {'feelings': feelings, 'challenges': challenges}

    def has_active(self, game):
        """Cheap post-index gate; does not load feelings, history or snapshots."""
        _game(game)
        if not self.path.exists():
            return False
        with self._connection() as db:
            return db.execute("SELECT 1 FROM challenges WHERE game=? AND status='ACTIVE' LIMIT 1", (game,)).fetchone() is not None

    def save_feeling(self, snapshot, game, drive_id, response, difficulty=None):
        _drive(snapshot, game, drive_id)
        if not isinstance(response, str) or response not in _FEELINGS:
            raise JournalError('체감은 편함·보통·어려움·잘 모르겠음 중에서 선택하세요.')
        if difficulty is not None and (response != 'DIFFICULT' or not isinstance(difficulty, str) or difficulty not in _DIFFICULTIES):
            raise JournalError('어려움의 범위는 어려웠어요를 고른 경우에만 선택할 수 있습니다.')
        value = dict(response=response, difficulty=difficulty, updated_at=self._now().isoformat())
        with self._connection(write=True) as db:
            db.execute('INSERT INTO feelings VALUES(?,?,?,?,?) ON CONFLICT(game,drive_id) DO UPDATE SET response=excluded.response,difficulty=excluded.difficulty,updated_at=excluded.updated_at',
                       (game, drive_id, response, difficulty, value['updated_at']))
        return value

    def delete_feeling(self, snapshot, game, drive_id):
        _drive(snapshot, game, drive_id)
        if self.path.exists():
            with self._connection(write=True) as db:
                db.execute('DELETE FROM feelings WHERE game=? AND drive_id=?', (game, drive_id))

    def accept_challenge(self, snapshot, game, drive_id, candidate_id):
        drive = _drive(snapshot, game, drive_id)
        _id(candidate_id)
        now = self._now()
        if _date(drive.get('recorded_at')) is None or _date(drive['recorded_at']) > now:
            raise JournalError('대상 주행의 실제 날짜를 확인한 뒤 목표를 선택할 수 있습니다.')
        # BEGIN IMMEDIATE makes concurrent selections and one-active enforcement atomic.
        with self._connection(write=True) as db:
            existing = [json.loads(raw) for raw, in db.execute('SELECT payload FROM challenges WHERE game=?', (game,))]
            active = next((row for row in existing if row['status'] == 'ACTIVE'), None)
            if active:
                if active['candidate_id'] == candidate_id and active['drive_id'] == drive_id:
                    return active
                raise JournalError('진행 중인 도전이 있습니다. 취소한 뒤 다른 목표를 선택하세요.', 409)
            completed = {row['basis_key'] for row in existing if row['status'] == 'ACHIEVED'}
            candidates, _ = _candidates(drive, completed)
            candidate = next((row for row in candidates if row['candidate_id'] == candidate_id), None)
            if candidate is None:
                raise JournalError('목표의 측정 기준이 바뀌었습니다. 새로 확인한 목표를 선택하세요.', 409)
            drives = _drives(snapshot, game)
            result = dict(candidate, id=uuid4().hex, game=game, status='ACTIVE', accepted_at=now.isoformat(),
                baseline_drive_ids=sorted(row['drive_id'] for row in drives),
                baseline_source_ids=sorted({item for row in drives for item in row['source_ids']}),
                evaluation=_evaluation('NEW_DRIVE_REQUIRED'))
            db.execute('INSERT INTO challenges VALUES(?,?,?,?)', (result['id'], game, 'ACTIVE', _dump(result)))
        return result

    def cancel_challenge(self, game, challenge_id):
        _game(game)
        if not isinstance(challenge_id, str) or not re.fullmatch(r'[a-f0-9]{32}', challenge_id):
            raise JournalError('도전 식별자가 올바르지 않습니다.')
        if not self.path.exists():
            raise JournalError('선택한 도전을 찾을 수 없습니다.', 404)
        with self._connection(write=True) as db:
            found = db.execute('SELECT payload FROM challenges WHERE id=? AND game=?', (challenge_id, game)).fetchone()
            if found is None:
                raise JournalError('이 게임의 도전을 찾을 수 없습니다.', 404)
            row = json.loads(found[0])
            if row['status'] == 'ACHIEVED':
                raise JournalError('달성한 도전은 취소할 수 없습니다.', 409)
            if row['status'] != 'CANCELLED':
                row.update(status='CANCELLED', cancelled_at=self._now().isoformat())
                db.execute('UPDATE challenges SET status=?,payload=? WHERE id=?', ('CANCELLED', _dump(row), challenge_id))
        return row

    def reconcile(self, snapshot, game):
        """Explicit write after indexing; a GET never calls this method."""
        _game(game)
        if not self.path.exists():
            return None
        now = self._now()
        with self._connection(write=True) as db:
            found = db.execute("SELECT payload FROM challenges WHERE game=? AND status='ACTIVE'", (game,)).fetchone()
            if found is None:
                return None
            row = json.loads(found[0])
            evaluation = _evaluation('NEW_DRIVE_REQUIRED')
            for drive in reversed(_drives(snapshot, game)):
                if (_scope(drive) != row['scope'] or drive['drive_id'] in row['baseline_drive_ids']
                        or set(drive['source_ids']) & set(row['baseline_source_ids'])):
                    continue
                recorded = _date(drive.get('recorded_at'))
                reason = ('RECORDED_AT_REQUIRED' if recorded is None else
                          'BEFORE_ACCEPTANCE' if recorded <= _date(row['accepted_at']) else
                          'FUTURE_RECORDING' if recorded > now else
                          'NATIVE_IDENTITY_REQUIRED' if drive['identity_status'] != 'NATIVE' else None)
                laps, _ = _lap_selection(drive)
                # Explicit non-replay evidence is required in both driving and lap facts.
                confirmed = all(_replay_status(observation) == 'CONFIRMED_NON_REPLAY'
                                and observation.get('metrics', {}).get('driving', {}).get('status') == 'OBSERVED'
                                for observation in drive['observations'])
                confirmed &= bool(laps) and all(lap.get('replay_status') == 'CONFIRMED_NON_REPLAY' for lap in laps)
                evidence = dict(drive_id=drive['drive_id'], source_ids=drive['source_ids'], recorded_at=drive.get('recorded_at'),
                                **(_stats(laps) if laps else dict(lap_count=0, median_lap_s=None, mad_pct=None)))
                reason = reason or ('REPLAY_UNCONFIRMED' if not confirmed else
                    'INSUFFICIENT_LAPS' if len(laps) < row['target']['min_laps'] else
                    'PACE_NOT_MET' if evidence['median_lap_s'] > row['target']['pace_limit_s'] else
                    'CONSISTENCY_NOT_MET' if row['target']['mad_limit_pct'] is not None and evidence['mad_pct'] > row['target']['mad_limit_pct'] else 'ACHIEVED')
                evaluation = _evaluation(reason, evidence=evidence,
                    status='ACHIEVED' if reason == 'ACHIEVED' else 'NOT_MET' if reason in {'PACE_NOT_MET', 'CONSISTENCY_NOT_MET'} else 'UNVERIFIED')
                if reason == 'ACHIEVED':
                    row.update(status='ACHIEVED', achieved_at=drive['recorded_at'], achieved_drive_id=drive['drive_id'])
                    break
            if row['evaluation'] != evaluation or row['status'] == 'ACHIEVED':
                row['evaluation'] = evaluation
                db.execute('UPDATE challenges SET status=?,payload=? WHERE id=?', (row['status'], _dump(row), row['id']))
        return row


def journal_context(game, snapshot, *, journal=None, drive_id=None):
    """Pure view of saved input and proposals; no reconciliation or file scans."""
    _game(game)
    journal = journal or DriverJournal()
    saved = journal.read(game)
    drives = _drives(snapshot, game)
    selected = _drive(snapshot, game, drive_id) if drive_id is not None else next(iter(drives), None)
    completed = {row['basis_key'] for row in saved['challenges'] if row['status'] == 'ACHIEVED'}
    candidates, reason = _candidates(selected, completed)
    visible_drives = drives[:200]
    if selected and selected not in visible_drives:
        visible_drives = visible_drives[:199] + [selected]
    history = [row for row in saved['challenges'] if row['status'] != 'ACTIVE']

    def public(row):
        # Baseline membership is only needed by the evaluator. Avoid sending
        # an entire library's identity set back with each small UI mutation.
        return {key: value for key, value in row.items()
                if key not in {'baseline_drive_ids', 'baseline_source_ids', 'basis_key'}}

    return dict(game=game, selected_drive_id=selected['drive_id'] if selected else None,
        feeling=saved['feelings'].get(selected['drive_id']) if selected else None,
        drives=[dict(drive_id=row['drive_id'], game=game, recorded_at=row['recorded_at'], labels=row.get('labels', {}),
                     identity_status=row['identity_status'], feeling=saved['feelings'].get(row['drive_id'])) for row in visible_drives],
        candidates=[public(row) for row in candidates], candidate_reason=reason,
        active_challenge=next((public(row) for row in saved['challenges'] if row['status'] == 'ACTIVE'), None),
        challenge_history=[public(row) for row in history[:50]],
        coverage=dict(drives=dict(total=len(drives), returned=len(visible_drives), limit=200),
                      challenge_history=dict(total=len(history), returned=min(50, len(history)), limit=50,
                                             achieved=sum(row['status'] == 'ACHIEVED' for row in history))))
