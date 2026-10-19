"""SQLite persistence and migrations for deterministic analysis and A/B evidence."""
from __future__ import annotations
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional
import numpy as np
from analysis_numeric import round_or_none
from setup_evidence import canonical_setup_changes, setup_learning_eligibility, speed_class_label, coach_issue_label
logger = logging.getLogger('iRacingSetupTuner')


class LongTermMemoryStore:
    """SQLite-backed personal race-engineer memory.

    The database stores compact deterministic analysis outputs rather than raw
    60 Hz telemetry. The original IBT remains the source of truth and is read
    again when detailed traces are needed.
    """

    SCHEMA_VERSION = 3

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        logger.info("Long-term memory database: %s", self.path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _loads(value: Optional[str], default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        if column not in self._columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS setups (
                    setup_hash TEXT PRIMARY KEY,
                    car_name TEXT,
                    setup_name TEXT,
                    setup_json TEXT NOT NULL,
                    setup_summary_json TEXT NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    file_modified_at REAL NOT NULL,
                    recorded_at REAL NOT NULL,
                    analyzed_at REAL NOT NULL,
                    analysis_version TEXT NOT NULL,
                    session_mode TEXT NOT NULL,
                    pit_rule TEXT NOT NULL,
                    car_name TEXT,
                    car_path TEXT,
                    track_name TEXT,
                    track_config TEXT,
                    track_temp_c REAL,
                    air_temp_c REAL,
                    setup_hash TEXT,
                    setup_name TEXT,
                    best_lap_s REAL,
                    valid_lap_count INTEGER NOT NULL DEFAULT 0,
                    complete_lap_count INTEGER NOT NULL DEFAULT 0,
                    out_lap_count INTEGER NOT NULL DEFAULT 0,
                    classification TEXT,
                    classification_confidence REAL,
                    setup_decision TEXT,
                    target_lap INTEGER,
                    reference_lap INTEGER,
                    user_feedback TEXT,
                    issue_tags_json TEXT NOT NULL DEFAULT '[]',
                    out_lap_json TEXT NOT NULL DEFAULT '{}',
                    memory_summary_json TEXT NOT NULL DEFAULT '{}',
                    analysis_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(file_hash, session_mode),
                    FOREIGN KEY(setup_hash) REFERENCES setups(setup_hash)
                );

                CREATE TABLE IF NOT EXISTS laps (
                    session_id TEXT NOT NULL,
                    lap_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    lap_time_s REAL,
                    delta_to_file_best_s REAL,
                    start_pct REAL,
                    end_pct REAL,
                    on_track_ratio REAL,
                    fuel_used_l REAL,
                    invalid_reason TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(session_id, lap_number),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS corner_metrics (
                    session_id TEXT NOT NULL,
                    lap_number INTEGER NOT NULL,
                    corner_id TEXT NOT NULL,
                    label TEXT,
                    speed_class TEXT,
                    entry_pct REAL,
                    apex_pct REAL,
                    exit_pct REAL,
                    delta_s REAL,
                    issue_type TEXT,
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, lap_number, corner_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS driver_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    feedback_text TEXT NOT NULL,
                    parsed_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS experiment_plans (
                    plan_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    filename TEXT NOT NULL,
                    session_mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    session_id TEXT,
                    decision TEXT,
                    candidate_id TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS experiment_results (
                    result_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    plan_id TEXT,
                    baseline_session_id TEXT,
                    candidate_session_id TEXT,
                    session_mode TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    reason TEXT,
                    median_delta_s REAL,
                    best_delta_s REAL,
                    stdev_delta_s REAL,
                    pace_slope_delta_s_per_lap REAL,
                    track_temp_delta_c REAL,
                    setup_diff_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES experiment_plans(plan_id) ON DELETE SET NULL,
                    FOREIGN KEY(baseline_session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
                    FOREIGN KEY(candidate_session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
                );


                CREATE TABLE IF NOT EXISTS setup_sensitivity_events (
                    event_id TEXT PRIMARY KEY,
                    result_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    car_name TEXT,
                    track_name TEXT,
                    track_config TEXT,
                    session_mode TEXT NOT NULL,
                    setting TEXT NOT NULL,
                    setting_label TEXT,
                    direction INTEGER NOT NULL,
                    baseline_value REAL,
                    candidate_value REAL,
                    delta_value REAL,
                    unit TEXT,
                    verdict TEXT NOT NULL,
                    median_delta_s REAL,
                    best_delta_s REAL,
                    track_temp_delta_c REAL,
                    confidence REAL NOT NULL,
                    confounded INTEGER NOT NULL DEFAULT 0,
                    effects_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(result_id, setting),
                    FOREIGN KEY(result_id) REFERENCES experiment_results(result_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_context
                    ON sessions(car_name, track_name, track_config, analyzed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_setup
                    ON sessions(setup_hash, analyzed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_mode
                    ON sessions(session_mode, analyzed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_corner_issue
                    ON corner_metrics(issue_type, speed_class);
                CREATE INDEX IF NOT EXISTS idx_experiment_results_context
                    ON experiment_results(verdict, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_setup_sensitivity_context
                    ON setup_sensitivity_events(car_name, setting, direction, created_at DESC);
                """
            )
            # Upgrade databases created by v2.1 without discarding plans.
            for column, declaration in [
                ("session_id", "TEXT"),
                ("decision", "TEXT"),
                ("candidate_id", "TEXT"),
            ]:
                self._ensure_column(connection, "experiment_plans", column, declaration)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            # Backfill older A/B results into the v3 sensitivity table. Existing
            # experiment_results already contain the exact setup diff and verdict, so
            # no raw telemetry is required for this migration.
            legacy_rows = connection.execute(
                """
                SELECT r.result_id, r.session_mode, r.setup_diff_json, r.result_json,
                       s.car_name, s.track_name, s.track_config
                FROM experiment_results r
                LEFT JOIN sessions s ON s.session_id=r.baseline_session_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM setup_sensitivity_events e WHERE e.result_id=r.result_id
                )
                ORDER BY r.created_at ASC
                """
            ).fetchall()
            for row in legacy_rows:
                comparison = self._loads(row[3], {})
                setup_diff = self._loads(row[2], {})
                full_setup_diff = setup_diff.get("full_setup") if isinstance(setup_diff, dict) else {}
                if not isinstance(full_setup_diff, dict):
                    full_setup_diff = {}
                try:
                    self._store_setup_sensitivity_events(
                        connection,
                        result_id=str(row[0]),
                        comparison=comparison,
                        baseline={"session": {"car_name": row[4], "track_name": row[5], "track_config": row[6]}},
                        candidate={},
                        session_mode=str(row[1] or "BASELINE_3LAP"),
                        full_setup_diff=full_setup_diff,
                    )
                except Exception as exc:
                    logger.debug("Sensitivity backfill skipped for %s: %s", row[0], exc)

    def import_legacy_plans(self, legacy_path: Path) -> int:
        if not legacy_path.exists() or legacy_path.resolve() == self.path.resolve():
            return 0
        imported = 0
        try:
            legacy = sqlite3.connect(legacy_path, timeout=5.0)
            rows = legacy.execute(
                "SELECT plan_id, created_at, filename, session_mode, source, plan_json FROM experiment_plans"
            ).fetchall()
            legacy.close()
            with self._lock, self._connect() as connection:
                for row in rows:
                    payload = self._loads(row[5], {})
                    before = connection.total_changes
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO experiment_plans
                        (plan_id, created_at, filename, session_mode, source, plan_json,
                         session_id, decision, candidate_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row[0], row[1], row[2], row[3], row[4], row[5],
                            payload.get("session_id"), payload.get("decision"), payload.get("candidate_id"),
                        ),
                    )
                    imported += int(connection.total_changes > before)
        except Exception as exc:
            logger.info("Legacy plan import skipped: %s", exc)
        return imported

    def status(self) -> dict[str, Any]:
        counts = {
            "sessions": 0,
            "setups": 0,
            "laps": 0,
            "corner_metrics": 0,
            "driver_feedback": 0,
            "experiment_plans": 0,
            "experiment_results": 0,
            "setup_sensitivity_events": 0,
        }
        latest_at: Optional[float] = None
        with self._lock, self._connect() as connection:
            for table in counts:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            row = connection.execute("SELECT MAX(analyzed_at) FROM sessions").fetchone()
            latest_at = float(row[0]) if row and row[0] is not None else None
            schema_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
        database_bytes = self.path.stat().st_size if self.path.exists() else 0
        wal_path = Path(f"{self.path}-wal")
        shm_path = Path(f"{self.path}-shm")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        shm_bytes = shm_path.stat().st_size if shm_path.exists() else 0
        return {
            "enabled": True,
            "provider": "LOCAL_SQLITE",
            "path": str(self.path),
            "directory": str(self.path.parent),
            "exists": self.path.exists(),
            "size_bytes": database_bytes + wal_bytes + shm_bytes,
            "database_file_bytes": database_bytes,
            "wal_file_bytes": wal_bytes,
            "shared_memory_file_bytes": shm_bytes,
            "schema_version": int(schema_row[0]) if schema_row else self.SCHEMA_VERSION,
            "counts": counts,
            "latest_analysis_at": latest_at,
            "raw_telemetry_stored": False,
            "storage_policy": "COMPACT_ANALYSIS_ONLY",
        }

    def assigned_modes(self, file_hash: str) -> list[str]:
        """Return stored session classifications for one IBT, newest first."""
        if not file_hash:
            return []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_mode, MAX(analyzed_at) AS latest_at
                FROM sessions
                WHERE file_hash=?
                GROUP BY session_mode
                ORDER BY latest_at DESC
                """,
                (file_hash,),
            ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def session_exists(self, session_id: Optional[str]) -> bool:
        if not session_id:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id=? LIMIT 1",
                (session_id,),
            ).fetchone()
        return row is not None

    def save_analysis(
        self,
        snapshot: dict[str, Any],
        replace_existing_mode: bool = False,
    ) -> dict[str, Any]:
        session = snapshot["session"]
        setup = snapshot["setup"]
        now = float(session["analyzed_at"])
        target_mode = str(session["session_mode"])
        file_hash = str(session["file_hash"])
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_modes = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT session_mode FROM sessions WHERE file_hash=?",
                    (file_hash,),
                ).fetchall()
                if row[0]
            ]
            conflicting_modes = [mode for mode in existing_modes if mode != target_mode]
            if conflicting_modes and not replace_existing_mode:
                raise ValueError(
                    "SESSION_MODE_CONFLICT: "
                    + ", ".join(conflicting_modes)
                    + f" -> {target_mode}"
                )
            if replace_existing_mode and conflicting_modes:
                connection.execute(
                    "DELETE FROM sessions WHERE file_hash=? AND session_mode<>?",
                    (file_hash, target_mode),
                )
            connection.execute(
                """
                INSERT INTO setups
                (setup_hash, car_name, setup_name, setup_json, setup_summary_json,
                 first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(setup_hash) DO UPDATE SET
                    car_name=excluded.car_name,
                    setup_name=excluded.setup_name,
                    setup_json=excluded.setup_json,
                    setup_summary_json=excluded.setup_summary_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    setup["setup_hash"], session.get("car_name"), session.get("setup_name"),
                    self._json(setup.get("setup_json", {})),
                    self._json(setup.get("setup_summary", {})), now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO sessions
                (session_id, file_hash, filename, file_path, file_size_bytes,
                 file_modified_at, recorded_at, analyzed_at, analysis_version,
                 session_mode, pit_rule, car_name, car_path, track_name, track_config,
                 track_temp_c, air_temp_c, setup_hash, setup_name, best_lap_s,
                 valid_lap_count, complete_lap_count, out_lap_count, classification,
                 classification_confidence, setup_decision, target_lap, reference_lap,
                 user_feedback, issue_tags_json, out_lap_json, memory_summary_json,
                 analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash, session_mode) DO UPDATE SET
                    filename=excluded.filename,
                    file_path=excluded.file_path,
                    file_size_bytes=excluded.file_size_bytes,
                    file_modified_at=excluded.file_modified_at,
                    analyzed_at=excluded.analyzed_at,
                    analysis_version=excluded.analysis_version,
                    pit_rule=excluded.pit_rule,
                    track_temp_c=excluded.track_temp_c,
                    air_temp_c=excluded.air_temp_c,
                    setup_hash=excluded.setup_hash,
                    setup_name=excluded.setup_name,
                    best_lap_s=excluded.best_lap_s,
                    valid_lap_count=excluded.valid_lap_count,
                    complete_lap_count=excluded.complete_lap_count,
                    out_lap_count=excluded.out_lap_count,
                    classification=excluded.classification,
                    classification_confidence=excluded.classification_confidence,
                    setup_decision=excluded.setup_decision,
                    target_lap=excluded.target_lap,
                    reference_lap=excluded.reference_lap,
                    user_feedback=excluded.user_feedback,
                    issue_tags_json=excluded.issue_tags_json,
                    out_lap_json=excluded.out_lap_json,
                    memory_summary_json=excluded.memory_summary_json,
                    analysis_json=excluded.analysis_json
                """,
                (
                    session["session_id"], session["file_hash"], session["filename"],
                    session["file_path"], session["file_size_bytes"],
                    session["file_modified_at"], session["recorded_at"], now,
                    session["analysis_version"], session["session_mode"], session["pit_rule"],
                    session.get("car_name"), session.get("car_path"), session.get("track_name"),
                    session.get("track_config"), session.get("track_temp_c"),
                    session.get("air_temp_c"), setup["setup_hash"], session.get("setup_name"),
                    session.get("best_lap_s"), session.get("valid_lap_count", 0),
                    session.get("complete_lap_count", 0), session.get("out_lap_count", 0),
                    session.get("classification"), session.get("classification_confidence"),
                    session.get("setup_decision"), session.get("target_lap"),
                    session.get("reference_lap"), session.get("user_feedback", ""),
                    self._json(session.get("issue_tags", [])),
                    self._json(snapshot.get("out_lap", {})),
                    self._json(snapshot.get("memory_summary", {})),
                    self._json(snapshot.get("analysis", {})),
                ),
            )
            connection.execute("DELETE FROM laps WHERE session_id=?", (session["session_id"],))
            for lap in snapshot.get("laps", []):
                connection.execute(
                    """
                    INSERT INTO laps
                    (session_id, lap_number, status, lap_time_s, delta_to_file_best_s,
                     start_pct, end_pct, on_track_ratio, fuel_used_l, invalid_reason, metrics_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, lap_number) DO UPDATE SET
                        status=excluded.status,
                        lap_time_s=excluded.lap_time_s,
                        delta_to_file_best_s=excluded.delta_to_file_best_s,
                        start_pct=excluded.start_pct,
                        end_pct=excluded.end_pct,
                        on_track_ratio=excluded.on_track_ratio,
                        fuel_used_l=excluded.fuel_used_l,
                        invalid_reason=excluded.invalid_reason,
                        metrics_json=excluded.metrics_json
                    """,
                    (
                        session["session_id"], lap.get("lap_number"), lap.get("status"),
                        lap.get("lap_time_s"), lap.get("delta_to_file_best_s"),
                        lap.get("start_pct"), lap.get("end_pct"), lap.get("on_track_ratio"),
                        lap.get("fuel_used_l"), lap.get("invalid_reason"), self._json(lap),
                    ),
                )
            connection.execute("DELETE FROM corner_metrics WHERE session_id=?", (session["session_id"],))
            for corner in snapshot.get("corner_metrics", []):
                connection.execute(
                    """
                    INSERT INTO corner_metrics
                    (session_id, lap_number, corner_id, label, speed_class, entry_pct,
                     apex_pct, exit_pct, delta_s, issue_type, metrics_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, lap_number, corner_id) DO UPDATE SET
                        label=excluded.label,
                        speed_class=excluded.speed_class,
                        entry_pct=excluded.entry_pct,
                        apex_pct=excluded.apex_pct,
                        exit_pct=excluded.exit_pct,
                        delta_s=excluded.delta_s,
                        issue_type=excluded.issue_type,
                        metrics_json=excluded.metrics_json
                    """,
                    (
                        session["session_id"], corner.get("lap_number"), corner.get("id"),
                        corner.get("label"), corner.get("speed_class"), corner.get("entry_pct"),
                        corner.get("apex_pct"), corner.get("exit_pct"), corner.get("delta_s"),
                        corner.get("issue_type"), self._json(corner),
                    ),
                )
            feedback = str(session.get("user_feedback") or "").strip()
            if feedback:
                feedback_id = hashlib.sha256(
                    f"{session['session_id']}|{feedback}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT OR IGNORE INTO driver_feedback
                    (feedback_id, session_id, created_at, feedback_text, parsed_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (feedback_id, session["session_id"], now, feedback, "{}"),
                )
            connection.commit()
        return {
            "session_id": session["session_id"],
            "setup_hash": setup["setup_hash"],
            "saved": True,
            "saved_this_request": True,
            "replaced_existing_mode": bool(replace_existing_mode and conflicting_modes),
            "removed_modes": conflicting_modes if replace_existing_mode else [],
            "assigned_mode": target_mode,
            "assigned_modes": [target_mode],
            "path": str(self.path),
        }

    def save(self, plan: dict[str, Any]) -> None:
        """Compatibility wrapper used by the v2.1 planner."""
        self.save_plan(plan)

    def save_plan(self, plan: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO experiment_plans
                (plan_id, created_at, filename, session_mode, source, plan_json,
                 session_id, decision, candidate_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["plan_id"], float(plan["created_at"]), str(plan.get("filename") or ""),
                    str(plan.get("session_mode") or ""), str(plan.get("source") or ""),
                    self._json(plan), plan.get("session_id"), plan.get("decision"),
                    plan.get("candidate_id"),
                ),
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.recent_plans(limit)

    def recent_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT plan_json FROM experiment_plans ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._loads(row[0], {}) for row in rows]

    def recent_sessions(
        self,
        limit: int = 20,
        car_name: Optional[str] = None,
        track_name: Optional[str] = None,
        session_mode: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if car_name:
            clauses.append("car_name=?")
            values.append(car_name)
        if track_name:
            clauses.append("track_name=?")
            values.append(track_name)
        if session_mode:
            clauses.append("session_mode=?")
            values.append(session_mode)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(int(limit))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT session_id, file_hash, filename, recorded_at, analyzed_at, session_mode,
                       car_name, track_name, track_config, track_temp_c, setup_hash,
                       setup_name, best_lap_s, valid_lap_count, classification,
                       classification_confidence, setup_decision, issue_tags_json,
                       memory_summary_json, user_feedback
                FROM sessions
                {where}
                ORDER BY analyzed_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._session_row(row) for row in rows]

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            laps = connection.execute(
                "SELECT metrics_json FROM laps WHERE session_id=? ORDER BY lap_number",
                (session_id,),
            ).fetchall()
            corners = connection.execute(
                """
                SELECT metrics_json FROM corner_metrics
                WHERE session_id=? ORDER BY lap_number, corner_id
                """,
                (session_id,),
            ).fetchall()
            feedback = connection.execute(
                """
                SELECT created_at, feedback_text, parsed_json FROM driver_feedback
                WHERE session_id=? ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
            setup_row = connection.execute(
                """
                SELECT setup_hash, car_name, setup_name, setup_json,
                       setup_summary_json, first_seen_at, last_seen_at
                FROM setups WHERE setup_hash=?
                """,
                (row["setup_hash"],),
            ).fetchone()
            plans = connection.execute(
                """
                SELECT plan_json FROM experiment_plans
                WHERE session_id=? ORDER BY created_at DESC
                """,
                (session_id,),
            ).fetchall()
            results = connection.execute(
                """
                SELECT result_id, created_at, plan_id, baseline_session_id,
                       candidate_session_id, session_mode, verdict, reason,
                       median_delta_s, best_delta_s, stdev_delta_s,
                       pace_slope_delta_s_per_lap, track_temp_delta_c,
                       setup_diff_json, result_json
                FROM experiment_results
                WHERE baseline_session_id=? OR candidate_session_id=?
                ORDER BY created_at DESC
                """,
                (session_id, session_id),
            ).fetchall()
        payload = dict(row)
        for field, default in [
            ("issue_tags_json", []), ("out_lap_json", {}),
            ("memory_summary_json", {}), ("analysis_json", {}),
        ]:
            payload[field.removesuffix("_json")] = self._loads(payload.pop(field), default)
        payload["laps"] = [self._loads(item[0], {}) for item in laps]
        payload["corner_metrics"] = [self._loads(item[0], {}) for item in corners]
        payload["driver_feedback_history"] = [
            {
                "created_at": item[0],
                "feedback_text": item[1],
                "parsed": self._loads(item[2], {}),
            }
            for item in feedback
        ]
        if setup_row is not None:
            payload["setup"] = {
                "setup_hash": setup_row[0],
                "car_name": setup_row[1],
                "setup_name": setup_row[2],
                "setup_json": self._loads(setup_row[3], {}),
                "setup_summary": self._loads(setup_row[4], {}),
                "first_seen_at": setup_row[5],
                "last_seen_at": setup_row[6],
            }
        else:
            payload["setup"] = None
        payload["experiment_plans"] = [self._loads(item[0], {}) for item in plans]
        payload["experiment_results"] = [
            {
                "result_id": item[0],
                "created_at": item[1],
                "plan_id": item[2],
                "baseline_session_id": item[3],
                "candidate_session_id": item[4],
                "session_mode": item[5],
                "verdict": item[6],
                "reason": item[7],
                "median_delta_s": item[8],
                "best_delta_s": item[9],
                "stdev_delta_s": item[10],
                "pace_slope_delta_s_per_lap": item[11],
                "track_temp_delta_c": item[12],
                "setup_diff": self._loads(item[13], {}),
                "result": self._loads(item[14], {}),
            }
            for item in results
        ]
        return payload

    def _session_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["issue_tags"] = self._loads(payload.pop("issue_tags_json", None), [])
        payload["memory_summary"] = self._loads(payload.pop("memory_summary_json", None), {})
        return payload

    @staticmethod
    def _token_set(text: str) -> set[str]:
        return {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", text or "")
            if len(token) >= 2
        }

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, len(left | right))

    def similar_sessions(
        self,
        current: dict[str, Any],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        car_name = current.get("car_name")
        track_name = current.get("track_name")
        if not car_name or not track_name:
            return []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, filename, recorded_at, analyzed_at, session_mode,
                       car_name, track_name, track_config, track_temp_c, setup_hash,
                       setup_name, best_lap_s, valid_lap_count, classification,
                       classification_confidence, setup_decision, issue_tags_json,
                       memory_summary_json, user_feedback
                FROM sessions
                WHERE car_name=? AND track_name=? AND session_id<>? AND file_hash<>?
                ORDER BY analyzed_at DESC
                LIMIT 120
                """,
                (
                    car_name,
                    track_name,
                    str(current.get("session_id") or ""),
                    str(current.get("file_hash") or ""),
                ),
            ).fetchall()

        current_tags = set(current.get("issue_tags") or [])
        current_feedback = self._token_set(str(current.get("user_feedback") or ""))
        current_temp = current.get("track_temp_c")
        current_out_lap = current.get("out_lap_aggregate") or {}
        now = time.time()
        scored: list[dict[str, Any]] = []
        for row in rows:
            item = self._session_row(row)
            score = 0.45  # exact vehicle + track filter
            reasons = ["동일 차량·트랙"]
            if (item.get("track_config") or "") == (current.get("track_config") or ""):
                score += 0.12
                reasons.append("동일 레이아웃")
            if item.get("session_mode") == current.get("session_mode"):
                score += 0.08
                reasons.append("동일 세션 모드")
            if item.get("setup_hash") and item.get("setup_hash") == current.get("setup_hash"):
                score += 0.12
                reasons.append("동일 셋업")
            if current_temp is not None and item.get("track_temp_c") is not None:
                delta = abs(float(current_temp) - float(item["track_temp_c"]))
                temp_score = 0.10 * max(0.0, 1.0 - delta / 12.0)
                score += temp_score
                if delta <= 3.0:
                    reasons.append("유사 노면 온도")
            if item.get("classification") == current.get("classification"):
                score += 0.06
                reasons.append("동일 원인 분류")
            item_tags = set(item.get("issue_tags") or [])
            tag_similarity = self._jaccard(current_tags, item_tags)
            score += 0.12 * tag_similarity
            if tag_similarity >= 0.25:
                reasons.append("유사 코너 증상")
            feedback_similarity = self._jaccard(
                current_feedback, self._token_set(str(item.get("user_feedback") or ""))
            )
            score += 0.04 * feedback_similarity
            historical_out_lap = item.get("memory_summary", {}).get("out_lap_aggregate", {}) or {}
            current_steering = current_out_lap.get("steering_work_deg_median")
            historical_steering = historical_out_lap.get("steering_work_deg_median")
            if current_steering and historical_steering:
                relative_delta = abs(float(current_steering) - float(historical_steering)) / max(
                    abs(float(current_steering)), 1.0
                )
                if relative_delta <= 0.20:
                    score += 0.025
                    reasons.append("유사 아웃랩 조향 작업량")
            current_temp_gain = current_out_lap.get("surface_temp_delta_c_median")
            historical_temp_gain = historical_out_lap.get("surface_temp_delta_c_median")
            if current_temp_gain is not None and historical_temp_gain is not None:
                if abs(float(current_temp_gain) - float(historical_temp_gain)) <= 2.0:
                    score += 0.025
                    reasons.append("유사 아웃랩 온도 상승")
            age_days = max(0.0, (now - float(item.get("analyzed_at") or now)) / 86400.0)
            score += 0.01 * max(0.0, 1.0 - age_days / 180.0)
            item["similarity_score"] = round(min(score, 1.0), 3)
            item["similarity_reasons"] = reasons
            scored.append(item)
        scored.sort(key=lambda item: (item["similarity_score"], item.get("analyzed_at") or 0), reverse=True)
        selected = scored[: max(1, int(limit))]
        self._attach_experiment_outcomes(selected)
        return selected

    def _attach_experiment_outcomes(self, sessions: list[dict[str, Any]]) -> None:
        ids = [str(item.get("session_id")) for item in sessions if item.get("session_id")]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT result_id, created_at, baseline_session_id, candidate_session_id,
                       verdict, reason, median_delta_s, best_delta_s,
                       setup_diff_json, result_json
                FROM experiment_results
                WHERE baseline_session_id IN ({placeholders})
                   OR candidate_session_id IN ({placeholders})
                ORDER BY created_at DESC
                """,
                ids + ids,
            ).fetchall()
        by_session: dict[str, list[dict[str, Any]]] = {value: [] for value in ids}
        seen_results: set[str] = set()
        for row in rows:
            if str(row[0]) in seen_results:
                continue
            seen_results.add(str(row[0]))
            stored_diff = self._loads(row[8], {})
            stored_result = self._loads(row[9], {})
            full_diff = stored_diff.get("full_setup", stored_diff)
            eligibility = setup_learning_eligibility(stored_result, full_diff if isinstance(full_diff, dict) else {})
            payload = {
                "result_id": row[0], "created_at": row[1], "baseline_session_id": row[2],
                "candidate_session_id": row[3], "verdict": row[4], "reason": row[5],
                "median_delta_s": row[6], "best_delta_s": row[7],
                "setup_diff": full_diff,
                "result": stored_result,
                "learning_eligibility": eligibility,
            }
            for key in [row[2], row[3]]:
                if key in by_session:
                    by_session[key].append(payload)
        for item in sessions:
            item["experiment_outcomes"] = by_session.get(str(item.get("session_id")), [])[:3]

    @staticmethod
    def _setup_diff(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        left = baseline.get("session", {}).get("setup", {}) or {}
        right = candidate.get("session", {}).get("setup", {}) or {}
        keys = sorted(set(left) | set(right))
        return {
            key: {"baseline": left.get(key), "candidate": right.get(key)}
            for key in keys
            if left.get(key) != right.get(key)
        }

    @staticmethod
    def _flatten_setup(value: Any, prefix: str = "") -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                flattened.update(LongTermMemoryStore._flatten_setup(child, path))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                path = f"{prefix}[{index}]"
                flattened.update(LongTermMemoryStore._flatten_setup(child, path))
        else:
            flattened[prefix or "value"] = value
        return flattened

    def _stored_setup_diff(
        self,
        connection: sqlite3.Connection,
        baseline_session_id: Optional[str],
        candidate_session_id: Optional[str],
    ) -> dict[str, Any]:
        if not baseline_session_id or not candidate_session_id:
            return {}

        def load_setup(session_id: str) -> dict[str, Any]:
            row = connection.execute(
                """
                SELECT p.setup_json
                FROM sessions s
                JOIN setups p ON p.setup_hash=s.setup_hash
                WHERE s.session_id=?
                """,
                (session_id,),
            ).fetchone()
            return self._loads(row[0], {}) if row else {}

        left = self._flatten_setup(load_setup(baseline_session_id))
        right = self._flatten_setup(load_setup(candidate_session_id))
        differences: dict[str, Any] = {}
        for key in sorted(set(left) | set(right)):
            if left.get(key) != right.get(key):
                differences[key] = {
                    "baseline": left.get(key),
                    "candidate": right.get(key),
                }
        return differences

    def infer_setup_increment(self, car_name: Optional[str], setting: str) -> Optional[dict[str, Any]]:
        """Infer a Garage increment conservatively from locally stored setup history.

        The IBT contains the current setup value but not the legal menu grid.  We
        only expose a numeric inferred target when the same car has enough distinct
        historical setup values that align to a stable grid.  Otherwise callers
        must keep the recommendation directional (one Garage click).
        """
        if not car_name or not setting:
            return None
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT setup_summary_json
                FROM setups
                WHERE car_name=?
                ORDER BY last_seen_at DESC
                LIMIT 250
                """,
                (car_name,),
            ).fetchall()

        values: list[float] = []
        for row in rows:
            summary = self._loads(row[0], {})
            value = summary.get(setting)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                values.append(numeric)

        unique = sorted({round(value, 4) for value in values})
        if len(unique) < 4:
            return None
        deltas = [round(unique[index + 1] - unique[index], 4) for index in range(len(unique) - 1)]
        deltas = [delta for delta in deltas if delta > 1e-6]
        if len(deltas) < 3:
            return None

        candidate = min(deltas)
        tolerance = max(1e-4, abs(candidate) * 0.02)
        adjacent_support = sum(1 for delta in deltas if abs(delta - candidate) <= tolerance)
        if adjacent_support < 2:
            return None

        origin = unique[0]
        aligned = 0
        for value in unique:
            increments = (value - origin) / candidate
            if abs(increments - round(increments)) <= 0.03:
                aligned += 1
        alignment = aligned / len(unique)
        if alignment < 0.80:
            return None

        confidence = min(0.88, 0.58 + 0.05 * adjacent_support + 0.12 * alignment)
        return {
            "step": candidate,
            "confidence": round(confidence, 3),
            "unique_values": len(unique),
            "samples": len(values),
            "adjacent_support": adjacent_support,
            "grid_alignment": round(alignment, 3),
            "source": "LOCAL_SETUP_HISTORY",
        }

    def _store_setup_sensitivity_events(
        self,
        connection: sqlite3.Connection,
        *,
        result_id: str,
        comparison: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        session_mode: str,
        full_setup_diff: dict[str, Any],
    ) -> dict[str, Any]:
        changes = canonical_setup_changes(full_setup_diff)
        eligibility = setup_learning_eligibility(comparison, full_setup_diff)
        if not eligibility["eligible"]:
            # Re-running an A/B with corrected conditions must also invalidate
            # any previously learned attribution for the same result.
            connection.execute("DELETE FROM setup_sensitivity_events WHERE result_id=?", (result_id,))
            return {"stored_events": 0, "events": [], "learning_eligibility": eligibility,
                    "reason": " ".join(eligibility["reasons"])}
        if not changes:
            return {"stored_events": 0, "events": [], "reason": "학습 가능한 셋업 항목 차이가 없습니다."}

        confounded = eligibility["confounded"]
        verdict = str(comparison.get("verdict") or "INCONCLUSIVE").upper()
        base_conf = 0.78 if verdict in {"KEEP", "REVERT"} else 0.38
        temp_delta = comparison.get("track_temp_delta_c")
        if temp_delta is not None:
            base_conf -= min(0.18, max(0.0, abs(float(temp_delta)) - 1.0) * 0.05)
        if confounded:
            base_conf -= 0.24
        baseline_laps = int((comparison.get("baseline") or {}).get("valid_laps") or 0)
        candidate_laps = int((comparison.get("candidate") or {}).get("valid_laps") or 0)
        if min(baseline_laps, candidate_laps) >= 5:
            base_conf += 0.08
        elif min(baseline_laps, candidate_laps) >= 3:
            base_conf += 0.04
        ab_quality = comparison.get("ab_quality") or {}
        effect_vs_noise = ab_quality.get("effect_vs_noise_ratio")
        if effect_vs_noise is not None:
            if float(effect_vs_noise) >= 1.0:
                base_conf += 0.05
            elif float(effect_vs_noise) < 0.5:
                base_conf -= 0.08
        confidence = round(max(0.15, min(0.92, base_conf)), 3)

        session = baseline.get("session", {}) or {}
        effects = {
            "telemetry": comparison.get("effect_profile") or {},
            "performance_axes": comparison.get("performance_axes") or {},
            "ab_quality": comparison.get("ab_quality") or {},
            "learning_eligibility": eligibility,
            "conditions_quality": comparison.get("conditions_quality") or {},
            "goal": comparison.get("goal"),
        }
        stored: list[dict[str, Any]] = []
        for change in changes:
            event_id = hashlib.sha256(
                f"{result_id}|{change['setting']}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT OR REPLACE INTO setup_sensitivity_events
                (event_id, result_id, created_at, car_name, track_name, track_config,
                 session_mode, setting, setting_label, direction, baseline_value,
                 candidate_value, delta_value, unit, verdict, median_delta_s,
                 best_delta_s, track_temp_delta_c, confidence, confounded, effects_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, result_id, time.time(), session.get("car_name"),
                    session.get("track_name"), session.get("track_config"), session_mode,
                    change["setting"], change["label"], int(change["direction"]),
                    change.get("baseline_value"), change.get("candidate_value"),
                    change.get("delta_value"), change.get("unit"), verdict,
                    comparison.get("median_delta_s"), comparison.get("best_delta_s"),
                    comparison.get("track_temp_delta_c"), confidence, int(confounded),
                    self._json(effects),
                ),
            )
            stored.append({**change, "event_id": event_id, "confidence": confidence, "confounded": confounded})
        return {
            "stored_events": len(stored),
            "events": stored,
            "confounded": confounded,
            "policy": "한 번에 한 셋업 항목만 바꾼 A/B 결과를 강한 개인화 근거로 사용합니다.",
        }

    def setup_sensitivity_profile(
        self,
        car_name: Optional[str],
        track_name: Optional[str] = None,
        limit: int = 300,
    ) -> dict[str, Any]:
        if not car_name:
            return {"status": "NO_CAR", "event_count": 0, "settings": []}
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.event_id, e.created_at, e.car_name, e.track_name, e.track_config,
                       e.setting, e.setting_label, e.direction, e.baseline_value, e.candidate_value,
                       e.delta_value, e.unit, e.verdict, e.median_delta_s, e.best_delta_s,
                       e.confidence, e.confounded, e.effects_json, r.result_json, r.setup_diff_json
                FROM setup_sensitivity_events e
                JOIN experiment_results r ON r.result_id=e.result_id
                WHERE e.car_name=?
                ORDER BY e.created_at DESC
                LIMIT ?
                """,
                (car_name, max(20, int(limit))),
            ).fetchall()

        events = [dict(row) for row in rows]
        for event in events:
            stored_diff = self._loads(event.pop("setup_diff_json", "{}"), {})
            stored_result = self._loads(event.pop("result_json", "{}"), {})
            full_diff = stored_diff.get("full_setup", stored_diff)
            event["learning_eligible"] = setup_learning_eligibility(stored_result, full_diff if isinstance(full_diff, dict) else {})["eligible"]
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for item in events:
            grouped[(str(item.get("setting") or ""), int(item.get("direction") or 0))].append(item)

        summaries: list[dict[str, Any]] = []
        for (setting, direction), items in grouped.items():
            decisive = [item for item in items if str(item.get("verdict") or "").upper() in {"KEEP", "REVERT"} and not item.get("confounded") and item.get("learning_eligible")]
            keeps = [item for item in decisive if str(item.get("verdict") or "").upper() == "KEEP"]
            reverts = [item for item in decisive if str(item.get("verdict") or "").upper() == "REVERT"]
            inconclusive = [item for item in items if str(item.get("verdict") or "").upper() == "INCONCLUSIVE"]
            keep_rate = len(keeps) / len(decisive) if decisive else None
            weighted = [
                (float(item["median_delta_s"]), float(item.get("confidence") or 0.5))
                for item in decisive if item.get("median_delta_s") is not None
            ]
            weighted_delta = (
                sum(value * weight for value, weight in weighted) / max(1e-9, sum(weight for _, weight in weighted))
                if weighted else None
            )
            current_track_events = sum(1 for item in items if track_name and item.get("track_name") == track_name)
            tier = "NO_DECISIVE_DATA"
            if len(decisive) >= 4:
                tier = "STRONG"
            elif len(decisive) >= 2:
                tier = "LEARNING"
            elif len(decisive) == 1:
                tier = "EARLY"
            summaries.append({
                "setting": setting,
                "label": items[0].get("setting_label") or setting,
                "unit": items[0].get("unit"),
                "direction": direction,
                "direction_label": "증가" if direction > 0 else "감소" if direction < 0 else "변화 없음",
                "event_count": len(items),
                "decisive_count": len(decisive),
                "keep_count": len(keeps),
                "revert_count": len(reverts),
                "inconclusive_count": len(inconclusive),
                "keep_rate": round_or_none(keep_rate, 3),
                "weighted_median_delta_s": round_or_none(weighted_delta, 4),
                "mean_confidence": round_or_none(float(np.mean([float(item.get("confidence") or 0.0) for item in items])), 3),
                "current_track_events": current_track_events,
                "tier": tier,
                "recent": [
                    {
                        "created_at": item.get("created_at"), "track_name": item.get("track_name"),
                        "verdict": item.get("verdict"), "median_delta_s": item.get("median_delta_s"),
                        "delta_value": item.get("delta_value"), "confidence": item.get("confidence"),
                        "confounded": bool(item.get("confounded")),
                    }
                    for item in items[:3]
                ],
            })
        summaries.sort(
            key=lambda item: (
                int(item.get("decisive_count") or 0),
                abs(float(item.get("weighted_median_delta_s") or 0.0)),
                float(item.get("mean_confidence") or 0.0),
            ),
            reverse=True,
        )
        decisive_total = sum(int(item.get("decisive_count") or 0) for item in summaries)
        return {
            "status": "READY" if events else "NO_DATA",
            "car_name": car_name,
            "track_name": track_name,
            "event_count": len(events),
            "decisive_event_count": decisive_total,
            "settings": summaries[:16],
            "learning_stage": (
                "MATURE" if decisive_total >= 12 else
                "ESTABLISHED" if decisive_total >= 6 else
                "LEARNING" if decisive_total >= 2 else
                "COLD_START"
            ),
        }

    def driver_fingerprint(self, car_name: Optional[str], session_limit: int = 30) -> dict[str, Any]:
        if not car_name:
            return {"status": "NO_CAR", "confidence": 0.0, "weaknesses": [], "strengths": []}
        with self._lock, self._connect() as connection:
            session_rows = connection.execute(
                """
                SELECT session_id, track_name, analyzed_at
                FROM sessions
                WHERE car_name=?
                ORDER BY analyzed_at DESC
                LIMIT ?
                """,
                (car_name, max(5, int(session_limit))),
            ).fetchall()
            session_ids = [str(row[0]) for row in session_rows]
            if not session_ids:
                return {"status": "NO_DATA", "car_name": car_name, "confidence": 0.0, "weaknesses": [], "strengths": []}
            placeholders = ",".join("?" for _ in session_ids)
            corner_rows = connection.execute(
                f"""
                SELECT cm.session_id, cm.speed_class, cm.issue_type, cm.delta_s, cm.metrics_json
                FROM corner_metrics cm
                WHERE cm.session_id IN ({placeholders})
                """,
                session_ids,
            ).fetchall()
            lap_rows = connection.execute(
                f"""
                SELECT session_id, lap_time_s
                FROM laps
                WHERE session_id IN ({placeholders}) AND status='VALID' AND lap_time_s IS NOT NULL
                """,
                session_ids,
            ).fetchall()

        issue_rows: list[dict[str, Any]] = []
        speed_totals: Counter[str] = Counter()
        speed_issue_counts: Counter[str] = Counter()
        issue_counts: Counter[tuple[str, str]] = Counter()
        issue_sessions: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in corner_rows:
            speed = str(row[1] or "UNKNOWN")
            issue = str(row[2] or "")
            delta = float(row[3] or 0.0)
            speed_totals[speed] += 1
            if delta >= 0.02 and issue not in {"GAIN", "CONSISTENT", "NO_DELTA", "NO_REFERENCE"}:
                speed_issue_counts[speed] += 1
                issue_counts[(issue, speed)] += 1
                issue_sessions[(issue, speed)].add(str(row[0]))
                issue_rows.append({"issue_type": issue, "speed_class": speed, "delta_s": delta})

        weaknesses: list[dict[str, Any]] = []
        total_sessions = len(session_ids)
        for (issue, speed), count in issue_counts.items():
            support_sessions = len(issue_sessions[(issue, speed)])
            if count < 2:
                continue
            avg_loss = float(np.mean([item["delta_s"] for item in issue_rows if item["issue_type"] == issue and item["speed_class"] == speed]))
            weaknesses.append({
                "issue_type": issue,
                "issue_label": coach_issue_label(issue),
                "speed_class": speed,
                "occurrences": count,
                "session_support": support_sessions,
                "session_support_ratio": round(support_sessions / max(1, total_sessions), 3),
                "mean_loss_s": round_or_none(avg_loss, 3),
            })
        weaknesses.sort(
            key=lambda item: (
                float(item.get("session_support_ratio") or 0.0),
                float(item.get("mean_loss_s") or 0.0),
                int(item.get("occurrences") or 0),
            ), reverse=True,
        )

        strengths: list[dict[str, Any]] = []
        for speed, total in speed_totals.items():
            if total < 8:
                continue
            issue_rate = speed_issue_counts[speed] / max(1, total)
            if issue_rate <= 0.45:
                strengths.append({
                    "type": "SPEED_CLASS_REPEATABILITY",
                    "speed_class": speed,
                    "label": f"{speed_class_label(speed)} 코너 재현성",
                    "issue_rate": round(issue_rate, 3),
                    "sample_count": total,
                    "score": round(1.0 - issue_rate, 3),
                })
        strengths.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)

        by_session: dict[str, list[float]] = defaultdict(list)
        for row in lap_rows:
            by_session[str(row[0])].append(float(row[1]))
        stdevs = [float(np.std(values)) for values in by_session.values() if len(values) >= 2]
        median_lap_stdev = float(np.median(stdevs)) if stdevs else None
        if median_lap_stdev is not None:
            strengths.append({
                "type": "LAP_REPEATABILITY",
                "label": "유효 랩 페이스 재현성",
                "score": round(max(0.0, min(1.0, 1.0 - median_lap_stdev / 1.0)), 3),
                "median_stdev_s": round_or_none(median_lap_stdev, 3),
                "sample_count": len(stdevs),
            })

        tracks = {str(row[1]) for row in session_rows if row[1]}
        data_score = min(1.0, len(session_ids) / 12.0)
        coverage_score = min(1.0, len(corner_rows) / 250.0)
        confidence = round(0.65 * data_score + 0.35 * coverage_score, 3)
        return {
            "status": "READY" if len(session_ids) >= 2 else "EARLY",
            "car_name": car_name,
            "session_count": len(session_ids),
            "track_count": len(tracks),
            "corner_sample_count": len(corner_rows),
            "median_valid_lap_stdev_s": round_or_none(median_lap_stdev, 3),
            "confidence": confidence,
            "confidence_label": "HIGH" if confidence >= 0.75 else "MEDIUM" if confidence >= 0.45 else "LOW",
            "weaknesses": weaknesses[:6],
            "strengths": sorted(strengths, key=lambda item: float(item.get("score") or 0.0), reverse=True)[:4],
        }

    def latest_plan_id_for_session(self, session_id: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.plan_id
                FROM experiment_plans p
                LEFT JOIN experiment_results r ON r.plan_id=p.plan_id
                WHERE p.session_id=? AND p.decision='SETUP_TEST'
                ORDER BY (r.result_id IS NULL) DESC, p.created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def file_analysis_index(self) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT filename, session_mode, analyzed_at, best_lap_s,
                       classification, setup_decision, valid_lap_count
                FROM sessions
                ORDER BY analyzed_at DESC
                """
            ).fetchall()
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            mode = str(row["session_mode"] or "")
            item = output.setdefault(
                str(row["filename"]),
                {
                    "analyzed": True,
                    "analysis_count": 0,
                    "session_modes": [],
                    "assigned_mode": mode or None,
                    "has_mode_conflict": False,
                    "last_analyzed_at": row["analyzed_at"],
                    "best_lap_s": row["best_lap_s"],
                    "classification": row["classification"],
                    "setup_decision": row["setup_decision"],
                    "valid_lap_count": row["valid_lap_count"],
                },
            )
            item["analysis_count"] += 1
            if mode and mode not in item["session_modes"]:
                item["session_modes"].append(mode)
            item["has_mode_conflict"] = len(item["session_modes"]) > 1
        return output

    def save_experiment_result(
        self,
        comparison: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        session_mode: str,
        plan_id: Optional[str] = None,
    ) -> dict[str, Any]:
        baseline_id = baseline.get("memory", {}).get("session_id")
        candidate_id = candidate.get("memory", {}).get("session_id")
        result_id = hashlib.sha256(
            f"{baseline_id}|{candidate_id}|{session_mode}|{plan_id or ''}".encode("utf-8")
        ).hexdigest()
        with self._lock, self._connect() as connection:
            full_setup_diff = self._stored_setup_diff(connection, baseline_id, candidate_id)
            setup_diff = {
                "full_setup": full_setup_diff,
                "summary": self._setup_diff(baseline, candidate),
                "changed_field_count": len(full_setup_diff),
            }
            connection.execute(
                """
                INSERT OR REPLACE INTO experiment_results
                (result_id, created_at, plan_id, baseline_session_id, candidate_session_id,
                 session_mode, verdict, reason, median_delta_s, best_delta_s,
                 stdev_delta_s, pace_slope_delta_s_per_lap, track_temp_delta_c,
                 setup_diff_json, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id, time.time(), plan_id, baseline_id, candidate_id, session_mode,
                    comparison.get("verdict"), comparison.get("reason"),
                    comparison.get("median_delta_s"), comparison.get("best_delta_s"),
                    comparison.get("stdev_delta_s"), comparison.get("pace_slope_delta_s_per_lap"),
                    comparison.get("track_temp_delta_c"), self._json(setup_diff),
                    self._json(comparison),
                ),
            )
            learning_update = self._store_setup_sensitivity_events(
                connection,
                result_id=result_id,
                comparison=comparison,
                baseline=baseline,
                candidate=candidate,
                session_mode=session_mode,
                full_setup_diff=full_setup_diff,
            )
            connection.commit()
        profile = self.setup_sensitivity_profile(
            baseline.get("session", {}).get("car_name"),
            baseline.get("session", {}).get("track_name"),
        )
        return {
            "saved": True,
            "result_id": result_id,
            "path": str(self.path),
            "baseline_session_id": baseline_id,
            "candidate_session_id": candidate_id,
            "setup_diff": setup_diff,
            "learning_update": learning_update,
            "sensitivity_profile": profile,
        }
