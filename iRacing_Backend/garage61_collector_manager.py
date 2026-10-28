from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from garage61_auth import TOKEN_MISSING_MESSAGE

TERMINAL_STATES = {
    "COMPLETE", "PARTIAL", "FAILED", "CANCELLED",
    "SKIPPED_CACHE_SUFFICIENT", "SKIPPED_COOLDOWN",
}


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    token = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    temp = path.with_name(f".{path.name}.{token}.tmp")
    temp.write_text(data, encoding="utf-8")
    last_error: OSError | None = None
    try:
        for attempt in range(12):
            try:
                os.replace(temp, path)
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(min(0.025 * (2 ** min(attempt, 5)), 0.60))
        path.write_text(data, encoding="utf-8")
        return
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
    if last_error:
        raise last_error


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


class Garage61CollectorManager:
    """Single-worker manager with Garage61 API-first collection.

    Automatic collection requires an API token. The old Playwright crawler is
    available only when explicitly requested for manual troubleshooting.
    """

    def __init__(
        self,
        app_dir: Path,
        reference_dir: Path,
        job_dir: Path,
        api_collector_name: str = "garage61_api_collector.py",
        crawler_name: str = "garage61_crawler.py",
        token_file: Optional[Path] = None,
    ) -> None:
        self.app_dir = app_dir
        self.reference_dir = reference_dir
        self.job_dir = job_dir
        self.api_collector_path = app_dir / api_collector_name
        self.crawler_path = app_dir / crawler_name
        self.token_file = token_file or (reference_dir.parent / "garage61_api_token.txt")
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen[Any]] = None
        self._active_job_id: Optional[str] = None
        self._last_job_id: Optional[str] = None
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, job_id: str) -> dict[str, Path]:
        return {
            "request": self.job_dir / f"{job_id}.request.json",
            "status": self.job_dir / f"{job_id}.status.json",
            "stdout": self.job_dir / f"{job_id}.stdout.log",
            "stderr": self.job_dir / f"{job_id}.stderr.log",
        }

    def api_token_configured(self) -> bool:
        if os.getenv("GARAGE61_API_TOKEN", "").strip():
            return True
        configured = os.getenv("GARAGE61_API_TOKEN_FILE", "").strip()
        candidates = [Path(configured).expanduser()] if configured else []
        candidates.extend([self.token_file, self.app_dir / "garage61_api_token.txt"])
        for path in candidates:
            try:
                if path.exists() and bool(path.read_text(encoding="utf-8-sig").strip()):
                    return True
            except OSError:
                continue
        return False

    def preferred_mode(self, requested: str = "AUTO") -> str:
        mode = str(requested or "AUTO").strip().upper()
        if mode == "API":
            return "API"
        if mode in {"BROWSER", "PLAYWRIGHT"}:
            return "BROWSER"
        return "API"

    def capabilities(self) -> dict[str, Any]:
        return {
            "api_collector_available": self.api_collector_path.exists(),
            "browser_fallback_available": self.crawler_path.exists(),
            "api_token_configured": self.api_token_configured(),
            "preferred_mode": self.preferred_mode(),
            "token_file": str(self.token_file),
        }

    def _refresh_process_locked(self) -> None:
        if self._process is None:
            return
        code = self._process.poll()
        if code is None:
            return
        job_id = self._active_job_id
        self._process = None
        self._active_job_id = None
        if job_id:
            self._last_job_id = job_id
        if not job_id:
            return
        paths = self._paths(job_id)
        status = _load_json(paths["status"])
        if status.get("state") in TERMINAL_STATES:
            return
        status.update({
            "job_id": job_id,
            "state": "COMPLETE" if code == 0 else "FAILED",
            "message": "Collector process exited." if code == 0 else f"Collector process exited with code {code}.",
            "finished_at": time.time(),
            "return_code": code,
            "busy": False,
        })
        _atomic_json_write(paths["status"], status)

    def active_job(self) -> Optional[dict[str, Any]]:
        with self._lock:
            self._refresh_process_locked()
            resolved = self._active_job_id or self._last_job_id
            return self.status(resolved) if resolved else None

    def start(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_locked()
            if self._process is not None and self._process.poll() is None:
                active = self.status(self._active_job_id) if self._active_job_id else {}
                active["busy"] = True
                return active

            mode = self.preferred_mode(str(request_payload.get("collector_mode") or "AUTO"))
            worker_path = self.api_collector_path if mode == "API" else self.crawler_path
            if not worker_path.exists():
                return {
                    "state": "FAILED",
                    "message": "Garage61 수집 파일이 없습니다. 앱을 최신 버전으로 업데이트한 뒤 다시 확인하세요.",
                    "can_start": False,
                    "collector_mode": mode,
                    "busy": False,
                }
            if mode == "API" and not self.api_token_configured():
                return {
                    "state": "FAILED",
                    "message": TOKEN_MISSING_MESSAGE,
                    "error_code": "API_TOKEN_MISSING",
                    "can_start": False,
                    "collector_mode": mode,
                    "busy": False,
                }

            job_id = str(uuid.uuid4())
            paths = self._paths(job_id)
            now = time.time()
            payload = dict(request_payload)
            payload.update({
                "job_id": job_id,
                "created_at": now,
                "reference_dir": str(self.reference_dir),
                "collector_mode": mode,
            })
            _atomic_json_write(paths["request"], payload)
            initial_status = {
                "job_id": job_id,
                "state": "QUEUED",
                "message": f"Garage61 {mode} collector queued.",
                "created_at": now,
                "updated_at": now,
                "context_key": payload.get("context_key"),
                "filename": payload.get("filename"),
                "target_count": payload.get("target_count"),
                "progress": 0.0,
                "collector_mode": mode,
                "busy": True,
            }
            _atomic_json_write(paths["status"], initial_status)

            if mode == "API":
                command = [
                    sys.executable,
                    str(worker_path),
                    "--request-file", str(paths["request"]),
                    "--status-file", str(paths["status"]),
                    "--reference-dir", str(self.reference_dir),
                    "--token-file", str(self.token_file),
                ]
            else:
                max_wall = int(payload.get("max_wall_seconds") or 600)
                nav_timeout = int(payload.get("navigation_timeout_ms") or 180000)
                action_timeout = int(payload.get("action_timeout_ms") or 60000)
                settle_ms = int(payload.get("settle_ms") or 2200)
                retry_count = int(payload.get("retry_count") or 3)
                command = [
                    sys.executable,
                    str(worker_path),
                    "--auto",
                    "--request-file", str(paths["request"]),
                    "--status-file", str(paths["status"]),
                    "--reference-dir", str(self.reference_dir),
                    "--max-wall-seconds", str(max_wall),
                    "--navigation-timeout-ms", str(nav_timeout),
                    "--action-timeout-ms", str(action_timeout),
                    "--settle-ms", str(settle_ms),
                    "--retry-count", str(retry_count),
                ]
                if bool(payload.get("headless")):
                    command.append("--headless")
                if bool(payload.get("force")):
                    command.append("--force")

            stdout_handle = paths["stdout"].open("ab")
            stderr_handle = paths["stderr"].open("ab")
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            try:
                child_env = os.environ.copy()
                child_env["PYTHONUTF8"] = "1"
                child_env["PYTHONIOENCODING"] = "utf-8"
                self._process = subprocess.Popen(
                    command,
                    cwd=str(self.app_dir),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    creationflags=creationflags,
                    env=child_env,
                )
            except Exception as exc:
                failed = {
                    **initial_status,
                    "state": "FAILED",
                    "message": f"Could not start Garage61 {mode} collector: {exc}",
                    "finished_at": time.time(),
                    "busy": False,
                }
                _atomic_json_write(paths["status"], failed)
                return failed
            finally:
                stdout_handle.close()
                stderr_handle.close()

            self._active_job_id = job_id
            self._last_job_id = job_id
            return self.status(job_id)

    def status(self, job_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_locked()
            resolved = job_id or self._active_job_id or self._last_job_id
            if not resolved:
                return {"state": "IDLE", "message": "No Garage61 collector job is running.", "busy": False}
            paths = self._paths(resolved)
            payload = _load_json(paths["status"])
            if not payload:
                payload = {"job_id": resolved, "state": "UNKNOWN", "message": "Collector status file is not available yet."}
            # Older workers replaced the queued status and dropped its identity.
            # The matching request remains authoritative for both collector modes.
            request = _load_json(paths["request"])
            if request.get("job_id") == resolved:
                payload.update({key: request.get(key) for key in ("filename", "context_key", "created_at", "target_count")})
            running = bool(resolved == self._active_job_id and self._process is not None and self._process.poll() is None)
            payload["busy"] = running
            payload["status_file"] = str(paths["status"])
            payload["stdout_log"] = str(paths["stdout"])
            payload["stderr_log"] = str(paths["stderr"])
            return payload

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_locked()
            if self._process is None or self._process.poll() is not None or not self._active_job_id:
                return {"state": "IDLE", "message": "No active collector job.", "busy": False}
            job_id = self._active_job_id
            try:
                self._process.terminate()
                self._process.wait(timeout=8)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            self._active_job_id = None
            self._last_job_id = job_id
            paths = self._paths(job_id)
            payload = _load_json(paths["status"])
            payload.update({
                "job_id": job_id,
                "state": "CANCELLED",
                "message": "Garage61 collection cancelled by user.",
                "finished_at": time.time(),
                "updated_at": time.time(),
                "busy": False,
            })
            _atomic_json_write(paths["status"], payload)
            return payload
