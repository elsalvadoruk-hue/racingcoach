"""LMU recording folders, independent of iRacing settings and credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

_LOCK = threading.RLock()
DEFAULT_PATH = Path(__file__).resolve().parent.parent / 'logs' / 'lmu-settings.json'


def settings_path():
    return Path(os.getenv('LMU_SETTINGS_PATH') or DEFAULT_PATH)


def normalize_folders(values):
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError('LMU 기록 폴더는 최대 12개까지 지정할 수 있습니다.')
    result, seen = [], set()
    for value in values:
        if (not isinstance(value, str) or not value.strip() or len(value) > 2048 or
                any(ord(char) < 32 for char in value)):
            raise ValueError('올바른 LMU 기록 폴더의 전체 경로를 입력하세요.')
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise ValueError('LMU 기록 폴더는 전체 경로로 입력하세요.')
        path = path.resolve()
        key = str(path).casefold()
        if key not in seen:
            result.append(str(path))
            seen.add(key)
    return result


def saved_folders():
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema_version') != 1:
            return []
        return normalize_folders(data.get('telemetry_dirs', []))
    except (OSError, ValueError, TypeError):
        return []


def save_folders(values):
    folders = normalize_folders(values)
    path = settings_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({'schema_version': 1, 'telemetry_dirs': folders}, handle, ensure_ascii=False, indent=2)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return folders


class FolderSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    telemetry_dirs: list[str] = Field(max_length=12)


def register_routes(app, discover_dirs):
    def view():
        configured = saved_folders()
        environment = bool(os.getenv('LMU_TELEMETRY_DIRS', '').strip())
        return {'provider': 'LMU', 'settings': {'telemetry_dirs': configured},
                'source': 'ENVIRONMENT' if environment else 'SAVED' if configured else 'DEFAULT',
                'telemetry_folders': [{'path': str(path), 'exists': path.is_dir()}
                                      for path in discover_dirs()],
                'restart_required': False}

    @app.get('/api/lmu/settings')
    def get_settings():
        return view()

    @app.put('/api/lmu/settings')
    def put_settings(body: FolderSettings):
        try:
            save_folders(body.telemetry_dirs)
            return view()
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
