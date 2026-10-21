"""HTTP settings endpoints with explicit runtime dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Literal, Optional
import logging
from fastapi import HTTPException
from pathlib import Path
from fastapi import Query
from fastapi.responses import Response
import app_settings
import re
import session_log
from api_route_models import AppSettingsBody, SecretBody, SessionLogAuto, SessionLogSend
import session_log_auto

logger = logging.getLogger("iRacingSetupTuner")


@dataclass(frozen=True)
class SettingsRoutesServices:
    get_app_version: Callable[..., Any]
    get_garage61_api_token_file: Callable[..., Any]
    _reload_settings: Callable[..., Any]
    app_settings_view: Callable[..., Any]
    get_voice_note_store: Callable[..., Any]


def register_routes(app, services: SettingsRoutesServices):
    @app.get("/api/settings")
    def get_app_settings():
        return services.app_settings_view()

    @app.put("/api/settings")
    def put_app_settings(body: AppSettingsBody):
        values = body.model_dump(exclude_none=True)
        if "telemetry_dirs" in values:
            folders = []
            for item in values["telemetry_dirs"]:
                text = item.strip().strip('"')
                if not text:
                    continue
                path = Path(text).expanduser()
                if not path.is_absolute():
                    raise HTTPException(status_code=422, detail=f"폴더는 전체 경로로 입력하세요: {text}")
                if not path.is_dir():
                    raise HTTPException(status_code=422, detail=f"폴더를 찾을 수 없습니다: {text}")
                folders.append(str(path))
            values["telemetry_dirs"] = folders
        for name in ("gcp_project_id", "gcp_location", "speech_region"):
            if name in values:
                values[name] = values[name].strip()
                if not re.fullmatch(r"[A-Za-z0-9._:-]*", values[name]):
                    raise HTTPException(status_code=422, detail="영문·숫자·하이픈만 입력할 수 있습니다.")
        for name, fallback in (("gcp_location", "global"), ("speech_region", "us")):
            if name in values and not values[name]:
                values[name] = fallback
        app_settings.save(values)
        services._reload_settings()
        return services.app_settings_view()

    @app.put("/api/settings/google-key")
    def put_google_key(body: SecretBody):
        try:
            app_settings.store_key(body.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        services._reload_settings()
        return services.app_settings_view()

    @app.delete("/api/settings/google-key")
    def delete_google_key():
        app_settings.remove_key()
        services._reload_settings()
        return services.app_settings_view()

    @app.put("/api/settings/garage61-token")
    def put_garage61_token(body: SecretBody):
        token = body.value.strip().removeprefix("Authorization:").strip().removeprefix("Bearer ").strip()
        if not token or len(token) > 4000 or any(ch.isspace() for ch in token):
            raise HTTPException(status_code=422, detail="토큰 형식을 확인하세요.")
        services.get_garage61_api_token_file().parent.mkdir(parents=True, exist_ok=True)
        services.get_garage61_api_token_file().write_text(token, encoding="utf-8")
        return services.app_settings_view()

    @app.delete("/api/settings/garage61-token")
    def delete_garage61_token():
        try:
            services.get_garage61_api_token_file().unlink()
        except OSError:
            pass
        return services.app_settings_view()

    @app.get("/api/session-logs")
    def list_session_logs(limit: int = Query(default=20, ge=1, le=100)):
        try:
            return {"sessions": session_log.sessions(services.get_voice_note_store(), limit), "send_available": bool(app_settings.log_upload_url())}
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/session-logs/auto")
    def session_log_auto_status():
        return session_log_auto.status()

    @app.put("/api/session-logs/auto")
    def set_session_log_auto(body: SessionLogAuto):
        """One switch for iRacing and LMU. Turning it on needs the consent version the screen showed."""
        try:
            return session_log_auto.set_enabled(body.enabled, body.consent_version)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/session-logs/{run_id}/send")
    def send_session_log(run_id: str, body: SessionLogSend):
        """Send one session's log to the maker of the app. Only ever on the driver's own click."""
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", run_id):
            raise HTTPException(status_code=422, detail="세션을 다시 선택하세요.")
        try:
            found = session_log.export(services.get_voice_note_store(), run_id, services.get_app_version(), include_questions=body.questions)
            if not found:
                raise HTTPException(status_code=404, detail="그 세션의 기록이 없습니다.")
            session_log.send(app_settings.log_upload_url(), found[0], found[1], body.note, services.get_app_version())
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"sent": True, "name": found[0]}

    @app.get("/api/session-logs/{run_id}/export")
    def export_session_log(run_id: str, questions: bool = False):
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", run_id):
            raise HTTPException(status_code=422, detail="세션을 다시 선택하세요.")
        try:
            found = session_log.export(services.get_voice_note_store(), run_id, services.get_app_version(), include_questions=questions)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail="그 세션의 기록이 없습니다.")
        name, data = found
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Log-File-Name": name})

    return SimpleNamespace(
        get_app_settings=get_app_settings,
        put_app_settings=put_app_settings,
        put_google_key=put_google_key,
        delete_google_key=delete_google_key,
        put_garage61_token=put_garage61_token,
        delete_garage61_token=delete_garage61_token,
        list_session_logs=list_session_logs,
        send_session_log=send_session_log,
        export_session_log=export_session_log,
    )
