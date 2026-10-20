"""LMU-only, lazy local practice records; never opens an iRacing database."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import uuid


SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "lmu-practice" / "practice.sqlite3"
_COLUMNS = {
    "tasks": ("task_id", "created_at", "payload"),
    "comparisons": ("comparison_id", "task_id", "created_at", "payload"),
}


def _reject_constant(value):
    raise ValueError("JSON에 유한하지 않은 수가 있습니다.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON에 중복된 필드가 있습니다.")
        result[key] = value
    return result


def _validate_json(value, depth=0):
    if depth > 64:
        raise ValueError("저장할 JSON의 중첩 깊이가 제한을 초과했습니다.")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON 필드 이름은 문자열이어야 합니다.")
        for item in value.values():
            _validate_json(item, depth+1)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, depth+1)
    elif value is None or isinstance(value, (str, bool, int)):
        pass
    elif isinstance(value, float) and math.isfinite(value):
        pass
    else:
        raise ValueError("저장할 값은 유한한 수를 포함한 JSON 형식이어야 합니다.")


def _encode(payload):
    if not isinstance(payload, dict):
        raise ValueError("저장할 내용은 JSON 객체여야 합니다.")
    _validate_json(payload)
    if "schema_version" in payload and (type(payload["schema_version"]) is not int or
                                         payload["schema_version"] != SCHEMA_VERSION):
        raise ValueError("지원하지 않는 연습 기록 schema_version입니다.")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        byte_count = len(encoded.encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError("저장할 내용을 JSON으로 변환할 수 없습니다.") from exc
    if byte_count > MAX_PAYLOAD_BYTES:
        raise ValueError("저장할 연습 기록이 2MiB 제한을 초과했습니다.")
    return encoded


def _decode(encoded):
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("저장된 연습 기록의 크기 또는 형식이 올바르지 않습니다.")
    try:
        result = json.loads(encoded, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        _encode(result)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("저장된 연습 기록의 JSON 또는 스키마가 올바르지 않습니다.") from exc
    return result


class LmuPracticeStore:
    def __init__(self, path=None):
        # Neither construction nor reads create directories or database files.
        self.path = Path(path) if path is not None else DEFAULT_PATH

    @staticmethod
    def _schema(connection, create):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if version == 0 and not tables and create:
            connection.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE comparisons (comparison_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE INDEX comparisons_task ON comparisons(task_id, created_at, comparison_id)")
            connection.execute("PRAGMA user_version=1")
            return
        if version != SCHEMA_VERSION:
            raise ValueError("지원하지 않는 연습 저장소 schema_version입니다.")
        if tables != set(_COLUMNS):
            raise ValueError("연습 저장소의 테이블 스키마가 올바르지 않습니다.")
        for table, expected in _COLUMNS.items():
            columns = tuple(connection.execute("PRAGMA table_info("+table+")"))
            actual = tuple(row[1] for row in columns)
            if (actual != expected or any(row[2].upper() != "TEXT" for row in columns) or
                    tuple(row[5] for row in columns) != (1,)+(0,)*(len(expected)-1) or
                    any(row[3] != 1 for row in columns[1:])):
                raise ValueError("연습 저장소의 필드 스키마가 올바르지 않습니다.")
        foreign_keys = tuple(connection.execute("PRAGMA foreign_key_list(comparisons)"))
        if len(foreign_keys) != 1 or foreign_keys[0][2:5] != ("tasks", "task_id", "task_id"):
            raise ValueError("연습 저장소의 비교 이력 연결 스키마가 올바르지 않습니다.")

    @contextmanager
    def _connection(self, write=False):
        connection = None
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True, timeout=30)
            self._schema(connection, create=write)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            if connection is not None and write:
                connection.rollback()
            raise ValueError("로컬 LMU 연습 저장소를 읽거나 저장할 수 없습니다.") from exc
        except BaseException:
            if connection is not None and write:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _task(row):
        return {"task_id": row[0], "created_at": row[1], "payload": _decode(row[2])}

    @staticmethod
    def _comparison(row):
        return {"comparison_id": row[0], "task_id": row[1], "created_at": row[2], "payload": _decode(row[3])}

    def list_tasks(self):
        if not self.path.exists():
            return []
        with self._connection() as connection:
            tasks = [self._task(row) for row in connection.execute(
                "SELECT task_id, created_at, payload FROM tasks ORDER BY created_at, task_id")]
        result = []
        for task in tasks:
            payload = task["payload"]
            definition = payload.get("definition", {})
            definition_id = payload.get("definition_id")
            if definition_id is None and isinstance(definition, dict):
                definition_id = definition.get("definition_id")
            lap_count = payload.get("lap_count")
            if lap_count is None and isinstance(payload.get("laps"), list):
                lap_count = len(payload["laps"])
            result.append({"task_id": task["task_id"], "created_at": task["created_at"],
                           "definition_id": definition_id, "lap_count": lap_count,
                           "focus": payload.get("focus")})
        return result

    def get_task(self, task_id):
        if not self.path.exists():
            raise KeyError("연습 과제를 찾을 수 없습니다.")
        with self._connection() as connection:
            row = connection.execute("SELECT task_id, created_at, payload FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("연습 과제를 찾을 수 없습니다.")
            return self._task(row)

    def create_task(self, payload):
        encoded = _encode(payload)
        row = (uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(), encoded)
        with self._connection(write=True) as connection:
            connection.execute("INSERT INTO tasks(task_id, created_at, payload) VALUES(?,?,?)", row)
        return self._task(row)

    def add_comparison(self, task_id, payload):
        encoded = _encode(payload)
        if not self.path.exists():
            raise KeyError("연습 과제를 찾을 수 없습니다.")
        row = (uuid.uuid4().hex, task_id, datetime.now(timezone.utc).isoformat(), encoded)
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is None:
                raise KeyError("연습 과제를 찾을 수 없습니다.")
            connection.execute("INSERT INTO comparisons(comparison_id, task_id, created_at, payload) VALUES(?,?,?,?)", row)
        return _decode(encoded) | {"comparison_id": row[0], "created_at": row[2]}

    def list_comparisons(self, task_id):
        if not self.path.exists():
            raise KeyError("연습 과제를 찾을 수 없습니다.")
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is None:
                raise KeyError("연습 과제를 찾을 수 없습니다.")
            return [self._comparison(row) for row in connection.execute(
                "SELECT comparison_id, task_id, created_at, payload FROM comparisons WHERE task_id=? ORDER BY created_at, comparison_id", (task_id,))]

    def create(self, baseline):
        task = self.create_task(baseline)
        return {"task_id": task["task_id"], "created_at": task["created_at"], "baseline": task["payload"]}

    def list(self):
        if not self.path.exists():
            return []
        with self._connection() as connection:
            tasks = [self._task(row) for row in connection.execute(
                "SELECT task_id, created_at, payload FROM tasks ORDER BY created_at, task_id")]
        return [{"task_id": task["task_id"], "created_at": task["created_at"], "baseline": task["payload"]}
                for task in tasks]

    def get(self, task_id):
        task = self.get_task(task_id)
        return {"task_id": task["task_id"], "created_at": task["created_at"], "baseline": task["payload"]}

    def comparisons(self, task_id):
        return [report["payload"] | {"comparison_id": report["comparison_id"], "created_at": report["created_at"]}
                for report in self.list_comparisons(task_id)]
