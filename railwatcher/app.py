import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config import ConfigStore
from .engine import Engine
from .store import Store
from .uploads import Uploader

STATIC = Path(__file__).parent / "static"


def create_app(directory: Path | None = None, model: Path | None = None, workers=True):
    directory = directory or Path(os.environ.get("RAILWATCHER_DATA", "data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    config, store = ConfigStore(directory), Store(directory)
    engine = Engine(config, store, model or Path(os.environ.get("RAILWATCHER_MODEL", "models/train.onnx")))
    uploader = Uploader(config, store)

    @asynccontextmanager
    async def lifespan(app):
        if workers:
            engine.start()
            uploader.start()
        yield
        if workers:
            await asyncio.to_thread(engine.close)
            await asyncio.to_thread(uploader.close)

    app = FastAPI(title="Railwatcher", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config, app.state.store, app.state.engine = config, store, engine

    @app.middleware("http")
    async def browser_guard(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if (origin and origin != expected) or request.headers.get("x-railwatcher") != "1":
                return JSONResponse({"detail": "Open Railwatcher directly to make changes"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    @app.get("/api/config")
    def get_config():
        return config.public()

    @app.patch("/api/config")
    async def save_config(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "Expected a settings object")
        try:
            config.update(body)
        except (ValidationError, ValueError) as error:
            # Pydantic's full errors include input values (camera credentials/API keys).
            message = (
                error.errors()[0]["msg"] if isinstance(error, ValidationError) else "Invalid configuration"
            )
            raise HTTPException(422, message) from None
        return config.public()

    @app.get("/api/devices")
    def devices():
        return [
            {
                "path": str(path),
                "label": (Path("/sys/class/video4linux") / path.name / "name").read_text().strip()
                if (Path("/sys/class/video4linux") / path.name / "name").exists()
                else path.name,
            }
            for path in sorted(Path("/dev").glob("video[0-9]*"))
        ]

    @app.get("/api/status")
    def status():
        return {
            **engine.status(),
            "uploads": uploader.status,
            "sightings": store.summary(),
            "enabled": config.get().enabled,
        }

    @app.get("/api/preview")
    def preview():
        jpeg = engine.preview()
        if jpeg is None:
            raise HTTPException(503, "Waiting for a camera frame")
        return Response(jpeg, media_type="image/jpeg")

    @app.get("/api/sightings")
    def sightings(offset: int = 0):
        rows = store.list(50, max(0, offset))
        for row in rows:
            row.pop("metadata")
            row["video_url"] = f"/api/sightings/{row['id']}/video" if row["filename"] else None
            row.pop("filename")
        return rows

    @app.get("/api/sightings/{event_id}/video")
    def video(event_id: str):
        row = store.get(event_id)
        if not row or not row["filename"] or not (store.clips / row["filename"]).is_file():
            raise HTTPException(404, "Video is not available locally")
        return FileResponse(store.clips / row["filename"], media_type="video/mp4")

    @app.post("/api/sightings/{event_id}/retry")
    def retry(event_id: str):
        row = store.get(event_id)
        if not row or row["status"] != "pending":
            raise HTTPException(409, "Only queued uploads can be retried")
        store.update(event_id, next_attempt=0, error=None)
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
