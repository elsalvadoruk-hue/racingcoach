"""HTTP endpoints for the update window: the program from GitHub releases and the voice clips."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException

from app_update import AppUpdater, VoiceSync


@dataclass(frozen=True)
class UpdateRoutesServices:
    app_updater: AppUpdater
    voice_sync: VoiceSync
    # True when a launcher will restart the backend after it exits (it then applies the staged update).
    request_restart: Callable[[], bool]


def register_routes(app, services: UpdateRoutesServices):
    def view():
        return {"app": services.app_updater.status(), "voice": services.voice_sync.status()}

    @app.get("/api/update")
    def get_update_status():
        return view()

    @app.post("/api/update/check")
    def check_updates():
        services.app_updater.check()
        services.voice_sync.rescan()
        return view()

    @app.post("/api/update/app/download")
    def download_app_update():
        try:
            services.app_updater.start_download()
        except (PermissionError, LookupError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return view()

    @app.post("/api/update/app/restart")
    def restart_for_update():
        if not services.app_updater.status()["staged"]:
            raise HTTPException(status_code=409, detail="적용할 업데이트가 아직 준비되지 않았습니다.")
        if not services.request_restart():
            raise HTTPException(status_code=409, detail="실행.bat 창을 닫고 다시 실행하면 업데이트가 적용됩니다.")
        return {"restarting": True}

    @app.post("/api/update/voice/download")
    def download_voice():
        services.voice_sync.start_download()
        return view()

    @app.post("/api/update/voice/stop")
    def stop_voice():
        services.voice_sync.stop()
        return view()

    @app.post("/api/update/voice/cleanup")
    def clean_voice():
        services.voice_sync.start_cleanup()
        return view()

    return get_update_status
