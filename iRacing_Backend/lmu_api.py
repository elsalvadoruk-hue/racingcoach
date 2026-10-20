"""Run separately: python -m uvicorn lmu_api:app --app-dir iRacing_Backend --host 127.0.0.1 --port 24158"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from lmu_routes import register_routes
from lmu_ui import PAGE


def create_lmu_application() -> FastAPI:
    app = FastAPI(title="LMU 텔레메트리 진단", version="0.1.0")
    register_routes(app)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home():
        return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})

    return app


app = create_lmu_application()
