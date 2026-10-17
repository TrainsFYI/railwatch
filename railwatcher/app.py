import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config import ConfigStore
from .engine import Engine
from .flashes import FlashIndex
from .motion import MotionIndex
from .pairing import Pairing
from .store import Store
from .trains import TrainIndex
from .uploads import Uploader

STATIC = Path(__file__).parent / "static"


def create_app(directory: Path | None = None, model: Path | None = None, workers=True):
    directory = directory or Path(os.environ.get("RAILWATCHER_DATA", "data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    config, store = ConfigStore(directory), Store(directory)
    engine = Engine(config, store, model or Path(os.environ.get("RAILWATCHER_MODEL", "models/train.onnx")))
    pairing = Pairing(directory)
    uploader = Uploader(config, store, pairing, engine.passages)
    motion = MotionIndex(engine.footage, config)
    flashes = FlashIndex(engine.footage, config)
    trains = TrainIndex(engine.footage, config, engine.model_path)

    @asynccontextmanager
    async def lifespan(app):
        if workers:
            engine.start()
            motion.start()
            trains.start()
            pairing.start()
            uploader.start()
        yield
        if workers:
            await asyncio.to_thread(trains.close)
            await asyncio.to_thread(motion.close)
            await asyncio.to_thread(engine.close)
            await asyncio.to_thread(uploader.close)
            await asyncio.to_thread(pairing.close)

    app = FastAPI(title="Railwatcher", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config, app.state.store, app.state.engine = config, store, engine
    app.state.motion = motion
    app.state.flashes = flashes
    app.state.trains = trains

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

    @app.get("/api/device")
    def device_status():
        return pairing.public()

    @app.get("/api/device/qr")
    def pairing_qr():
        import io

        import qrcode
        import qrcode.image.svg
        status = pairing.public()
        if not status.get("approval_url"):
            raise HTTPException(404, "No active pairing session")
        result = io.BytesIO()
        qrcode.make(status["approval_url"], image_factory=qrcode.image.svg.SvgPathImage).save(result)
        return Response(result.getvalue(), media_type="image/svg+xml")

    @app.post("/api/device/pair")
    def pair_device():
        try:
            pairing.begin(config.get())
        except (httpx.HTTPError, OSError, ValueError, KeyError):
            raise HTTPException(503, "Could not start pairing; check connection") from None
        return pairing.public()

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
            "passages": engine.passages.summary(),
            "enabled": config.get().enabled,
        }

    @app.get("/api/preview")
    def preview():
        jpeg = engine.preview()
        if jpeg is None:
            raise HTTPException(503, "Waiting for a camera frame")
        return Response(jpeg, media_type="image/jpeg")

    @app.get("/api/buffer")
    def footage_buffer(movement: Literal["all", "motion", "quiet", "pending", "error"] = "all",
                       activity: Literal["movement", "red_flashes", "trains"] = "movement"):
        index = trains if activity == "trains" else flashes if activity == "red_flashes" else motion
        from .archive_passages import group_passages
        all_rows = index.annotate(engine.footage.list(), "all")
        selected = [row for row in all_rows["segments"] if movement == "all" or row["motion"]["state"] == movement]
        return {**all_rows, "segments": selected, "activity": activity,
                "archive_passages": group_passages(all_rows["segments"]) if activity == "trains" else [],
                "playback_segments": all_rows["segments"]}

    @app.get("/api/buffer/{filename}")
    def footage_segment(filename: str):
        path = engine.footage.find(filename)
        if not path:
            raise HTTPException(404, "That footage segment is no longer available")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/buffer/mark")
    async def mark_train(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "Expected a start and end time")
        try:
            started, ended = float(body.get("started")), float(body.get("ended"))
        except (TypeError, ValueError):
            raise HTTPException(422, "Enter valid start and end times") from None
        if not (0 <= started < ended) or ended - started > 900:
            raise HTTPException(422, "Choose a footage range of up to 15 minutes")
        try:
            row = engine.footage.export_mark(store, config.get(), started, ended)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except OSError as error:
            raise HTTPException(507, str(error)) from None
        row.pop("metadata", None)
        row.pop("filename", None)
        row["manual_review"] = True
        row["video_url"] = f"/api/sightings/{row['id']}/video"
        return row

    @app.get("/api/sightings")
    def sightings(offset: int = 0):
        rows = store.list(50, max(0, offset))
        for row in rows:
            metadata = json.loads(row["metadata"])
            row["manual_review"] = bool(metadata.get("manual_review"))
            row["review_status"] = metadata.get("user_review", {}).get("decision") or (
                "confirmed" if row["manual_review"] else "required" if row["confidence"] < .8 else "not_required"
            )
            row.pop("metadata")
            row["video_url"] = f"/api/sightings/{row['id']}/video" if row["filename"] else None
            row.pop("filename")
        return rows

    @app.post("/api/sightings/{event_id}/review")
    async def review_sighting(event_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict) or body.get("decision") not in {"confirmed", "dismissed"}:
            raise HTTPException(422, "Choose confirmed or dismissed")
        if not store.review(event_id, body["decision"]):
            raise HTTPException(404, "Detection not found")
        return {"review_status": body["decision"]}

    @app.get("/api/sightings/{event_id}/timeline")
    def sighting_timeline(event_id: str):
        row = store.get(event_id)
        if row is None:
            raise HTTPException(404, "Detection not found")
        return engine.passages.timeline(row)

    @app.get("/api/sightings/{event_id}/video")
    def video(event_id: str):
        row = store.get(event_id)
        if not row or not row["filename"] or not (store.clips / row["filename"]).is_file():
            raise HTTPException(404, "Video is not available locally")
        return FileResponse(store.clips / row["filename"], media_type="video/mp4")

    @app.post("/api/sightings/{event_id}/submit")
    def submit_historical(event_id: str):
        try:
            identity = engine.passages.submit_history(event_id, config.get(), engine.model_version)
        except ValueError:
            raise HTTPException(409, "Historical footage unavailable") from None
        return {"event_id": identity, "status": "pending"}

    @app.post("/api/sightings/{event_id}/retry")
    def retry(event_id: str):
        row = store.get(event_id)
        if not row or row["status"] != "pending":
            raise HTTPException(409, "Only queued uploads can be retried")
        if not engine.passages.retry_for_part(event_id):
            raise HTTPException(409, "Submit this historical event before retrying delivery")
        store.update(event_id, next_attempt=0, error=None)
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
