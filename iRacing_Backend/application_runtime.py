"""Application construction, display state and owned resource lifecycle."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import os
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ibt_reader import TelemetryFileBusy, IBT_FILE_WAIT_MESSAGE
from overlay_control import routes as overlay_routes

DEFAULT_CORS_ORIGINS = ",".join(f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (24157, 3001, 3000))


def new_analysis_state():
    return {"last_analysis_time": 0.0, "analysis_result": None, "file_signature": None}


def analysis_state_summary(state):
    # Polls and SSE must not carry the multi-megabyte analysis result.
    return {"last_analysis_time": state["last_analysis_time"],
            "file_signature": state["file_signature"],
            "analysis_available": state["analysis_result"] is not None}


@asynccontextmanager
async def managed_lifespan(live, overlay, warm_references):
    live.start()
    overlay.start_monitor(live.overlay_state)
    # Reference checks remain in the background; first analysis never waits.
    threading.Thread(target=warm_references, name="garage61-check", daemon=True).start()
    try:
        yield
    finally:
        await asyncio.to_thread(overlay.close)
        live.stop()
        live.drill.stop()
        live.practice.reset('APP_CLOSED')
        await asyncio.to_thread(live.drill.history.close)


async def telemetry_file_busy_response(_request, exc):
    return JSONResponse(status_code=409, headers={'Retry-After': '2'},
                        content={'detail': IBT_FILE_WAIT_MESSAGE, 'code': 'TELEMETRY_FILE_BUSY',
                                 'retryable': True, 'filename': exc.path.name})


def create_application(version, lifespan, overlay):
    app = FastAPI(title="iRacing Telemetry Setup Tuner", version=version, lifespan=lifespan)
    app.include_router(overlay_routes(overlay))
    app.add_exception_handler(TelemetryFileBusy, telemetry_file_busy_response)
    # Preserve the existing localhost origin, ports and explicit CORS override.
    app.add_middleware(CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if origin.strip()],
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
        expose_headers=["X-Log-File-Name"])
    # LMU file analysis and bridge setup. Live LMU coaching shares the iRacing
    # live coach (game_sdk.AutoGameSdk); DuckDB stays a request-time dependency.
    from lmu_routes import register_routes as register_lmu_routes
    register_lmu_routes(app)
    return app
