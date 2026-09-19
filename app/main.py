from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes.dev_chat import router as dev_chat_router
from app.api.routes.simulator import router as simulator_router
from app.core.config import get_settings
from app.core.db import get_db

app = FastAPI(title="GRIP / ATR Conversational Platform", version="0.1.0")

_SIMULATOR_UI_PATH = Path(__file__).parent / "static" / "simulator.html"

if get_settings().app_env != "production":
    app.include_router(dev_chat_router)
    app.include_router(simulator_router)

    @app.get("/simulator")
    def simulator_ui() -> FileResponse:
        return FileResponse(_SIMULATOR_UI_PATH)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
