import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from railwatcher.app import create_app
from railwatcher.config import ConfigStore, Settings
from railwatcher.detection import Trigger
from railwatcher.engine import Recording, capture_command
from railwatcher.footage import FootageBuffer
from railwatcher.store import Store
from railwatcher.uploads import Uploader


def test_trigger_confirms_recent_detections_despite_brief_dips_and_keeps_tail():
    trigger = Trigger(3, 15)
    assert trigger.feed(True, 1) is None
    assert trigger.feed(False, 2) is None
    assert trigger.feed(True, 3) is None
    assert trigger.feed(True, 4) == "start"
    assert trigger.first_positive == 1
    assert trigger.feed(False, 15) is None
    assert trigger.feed(True, 16) is None
    assert trigger.feed(False, 30) is None
    assert trigger.feed(False, 31) == "end"
    assert not trigger.active


def test_trigger_does_not_accumulate_isolated_detections():
    trigger = Trigger(3, 15)
    for stamp in (1, 7, 13, 19):
        assert trigger.feed(True, stamp) is None
    assert trigger.feed(True, 20) is None
    assert trigger.feed(False, 21) is None
    assert trigger.feed(True, 22) == 'start'
    assert trigger.first_positive == 19


@pytest.mark.parametrize(
    "patch",
    [
        {"source": "/etc/passwd"},
        {"source_type": "file"},
        {"source_type": "rtsp", "source": "file:///etc/passwd"},
        {"source_type": "rtsp", "source": "rtsp://"},
        {"upload_url": "http://api.example/upload"},
        {"upload_url": "https://key:secret@example.com"},
        {"latitude": 91},
        {"longitude": float("nan")},
        {"detection_mask": [[0.2, 0.2], [0.8, 0.8]]},
        {"detection_mask": [[-0.1, 0], [0.8, 0.8], [0.5, 0.2]]},
        {"detection_mask": [[0.5, 0.5]] * 33},
        {"bearing": 360},
        {"enabled": True},
        {"unexpected": 1},
    ],
)
def test_invalid_config_cannot_replace_saved_settings(tmp_path, patch):
    config = ConfigStore(tmp_path)
    config.update({"name": "West junction"})
    with pytest.raises(ValidationError):
        config.update(patch)
    assert ConfigStore(tmp_path).get().name == "West junction"


def test_secrets_preserved_and_redacted(tmp_path):
    config = ConfigStore(tmp_path)
    config.update(
        {
            "source_type": "rtsp",
            "source": "rtsp://user:secret@192.168.1.5/live?key=hidden",
            "api_key": "private-key",
        }
    )
    config.update({"source": None, "api_key": None, "bearing": 15})
    public = json.dumps(config.public())
    assert all(word not in public for word in ("secret", "hidden", "private-key"))
    assert config.get().api_key == "private-key"
    assert config.path.stat().st_mode & 0o777 == 0o600
    config.update({"api_key": ""})
    assert not config.public()["has_api_key"]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, workers=False)
    with TestClient(app) as client:
        client.headers["X-Railwatcher"] = "1"
        yield client


def test_local_routes_open_and_cross_origin_changes_are_rejected(client, tmp_path):
    assert client.get("/").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/sightings").status_code == 200
    assert client.get("/api/buffer").json()["segments"] == []
    assert client.get("/api/devices").status_code == 200
    assert client.get("/api/preview").status_code == 503
    assert client.get("/api/sightings/missing/video").status_code == 404
    assert not (tmp_path / "admin.token").exists()
    assert "railwatcher" not in client.cookies

    response = client.patch(
        "/api/config",
        json={"name": "Bad"},
        headers={"Origin": "https://malicious.example", "X-Railwatcher": "1"},
    )
    assert response.status_code == 403
    assert client.get("/api/config").json()["name"] != "Bad"


def test_setup_validation_and_secret_errors(client):
    response = client.patch("/api/config", json={"setup_complete": True, "enabled": True})
    assert response.status_code == 422
    response = client.patch("/api/config", json={"api_key": "SUPERSECRET", "confidence": 2})
    assert "SUPERSECRET" not in response.text
    assert (
        client.patch(
            "/api/config", json={"latitude": 0, "longitude": 0, "setup_complete": True, "enabled": True}
        ).status_code
        == 200
    )
    assert client.get("/api/status").json()["enabled"]
    assert client.get("/api/preview").status_code == 503


def test_detection_mask_is_saved_and_returned_in_image_coordinates(client, tmp_path):
    points = [[0.12, 0.25], [0.8, 0.3], [0.7, 0.9]]
    response = client.patch("/api/config", json={"detection_mask": points})
    assert response.status_code == 200
    assert response.json()["detection_mask"] == points
    assert ConfigStore(tmp_path).get().detection_mask == [tuple(point) for point in points]


def add_clip(store, event_id="event", status="pending", size=100):
    store.create(event_id, time.time(), 0.9, {"schema_version": 1, "latitude": 43})
    path = store.clips / f"{event_id}.mp4"
    path.write_bytes(b"x" * size)
    store.update(event_id, status=status, ended=time.time(), filename=path.name, bytes=size)
    return store.get(event_id)


def test_retention_preserves_pending_clips(tmp_path):
    store = Store(tmp_path)
    add_clip(store, "pending", "pending")
    add_clip(store, "uploaded", "uploaded")
    assert store.make_room(150)
    assert (store.clips / "pending.mp4").exists()
    assert not (store.clips / "uploaded.mp4").exists()
    assert store.get("uploaded")["status"] == "uploaded"
    assert store.get("uploaded")["filename"] is None
    assert not store.make_room(50)


def test_restart_recovers_queue_and_marks_interrupted_recording(tmp_path):
    store = Store(tmp_path)
    add_clip(store, "uploading", "uploading")
    store.create("recording", time.time(), 0.8, {})
    partial = store.clips / "recording.partial.mp4"
    partial.write_bytes(b"unfinished")
    reopened = Store(tmp_path)
    assert reopened.get("uploading")["status"] == "pending"
    assert reopened.get("recording")["status"] == "failed"
    assert not partial.exists()


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (201, {"id": "remote-1"}, "uploaded"),
        (202, {"id": "remote-1"}, "uploaded"),
        (200, {}, "pending"),
        (401, {"detail": "invalid"}, "pending"),
        (503, {}, "pending"),
        (302, {}, "pending"),
        (200, {"id": 5}, "pending"),
    ],
)
def test_upload_contract_and_retries(tmp_path, status, body, expected):
    store = Store(tmp_path)
    row = add_clip(store)
    config = ConfigStore(tmp_path)
    settings = config.update({"upload_url": "https://example.com/upload", "api_key": "secret"})
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.headers["Idempotency-Key"] == row["id"]
        assert b'name="metadata"' in request.content
        assert b'name="video"' in request.content
        assert b'"sha256"' in request.content
        return httpx.Response(status, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        Uploader(config, store).attempt(row, settings, client)
    result = store.get(row["id"])
    assert len(seen) == 1
    assert result["status"] == expected
    if expected == "pending":
        assert result["attempts"] == 1
        assert result["next_attempt"] > time.time()
        assert "secret" not in result["error"]
    assert (store.clips / row["filename"]).exists()


def test_upload_exception_does_not_persist_credentials(tmp_path):
    store = Store(tmp_path)
    row = add_clip(store)
    config = ConfigStore(tmp_path)
    settings = config.update({"upload_url": "https://example.com/upload?token=secret", "api_key": "hidden"})

    def handler(request):
        raise httpx.ConnectError("Failed URL with secret and hidden")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        Uploader(config, store).attempt(row, settings, client)
    assert "secret" not in store.get(row["id"])["error"]


def test_open_video_route_supports_range_requests(client):
    row = add_clip(client.app.state.store)
    response = client.get(f"/api/sightings/{row['id']}/video", headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert len(response.content) == 10
    assert client.get("/api/sightings/missing/video").status_code == 404


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_review_buffer_can_be_played_and_marked_as_a_manual_sighting(client, tmp_path):
    archive = client.app.state.engine.footage
    started = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)).replace(microsecond=0)
    filename = started.strftime("%Y%m%dT%H%M%S") + ".mp4"
    path = archive.directory / filename
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=blue:s=640x360:r=5", "-t", "6", "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
        ],
        check=True,
    )
    index = client.get("/api/buffer").json()
    assert index["retention_seconds"] == 86400
    assert [segment["id"] for segment in index["segments"]] == [filename]
    assert client.get(f"/api/buffer/{filename}").status_code == 200
    start_epoch = started.timestamp()
    response = client.post(
        "/api/buffer/mark", json={"started": start_epoch + 1, "ended": start_epoch + 4}
    )
    assert response.status_code == 200, response.text
    sighting = response.json()
    assert sighting["status"] == "pending"
    assert sighting["manual_review"] is True
    assert sighting["video_url"] == f"/api/sightings/{sighting['id']}/video"
    assert client.get(sighting["video_url"]).status_code == 200
    assert client.get("/api/sightings").json()[0]["manual_review"] is True
    assert client.post(
        "/api/buffer/mark", json={"started": start_epoch, "ended": start_epoch + 901}
    ).status_code == 422


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_rolling_buffer_encodes_and_closes_a_segment(tmp_path):
    archive = FootageBuffer(tmp_path / "buffer")
    image = np.zeros((540, 960, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    archive.start()
    for index in range(40):
        archive.add(time.time(), encoded.tobytes())
    archive.close()
    segments = archive.list()["segments"]
    assert len(segments) == 1
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(archive.directory / segments[0]["id"])],
        capture_output=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["width"] == 640


def test_rtsp_credentials_are_one_argument_not_shell_commands():
    source = "rtsp://user:$(touch_owned)@192.168.0.1/live"
    command = capture_command(Settings(source_type="rtsp", source=source))
    assert command[command.index("-i") + 1] == source
    assert command[command.index("-timeout") + 1] == "10000000"
    assert command[-1] == "pipe:1"


def test_detection_mask_limits_confidence_to_detections_inside_polygon():
    from railwatcher.detection import TrainDetector

    class FakeNetwork:
        def setInput(self, _blob):
            pass

        def forward(self):
            # Model input coordinates are 640x640: .65 is inside the ROI;
            # .99 is outside and must not trigger the detector.
            return np.array([[[320, 32], [320, 32], [10, 10], [10, 10], [0.65, 0.99]]], dtype=np.float32)

    detector = object.__new__(TrainDetector)
    detector.net = FakeNetwork()
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    mask = [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)]

    assert detector.confidence(frame, mask) == pytest.approx(0.65)
    assert detector.confidence(frame) == pytest.approx(0.99)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_recording_produces_playable_h264_and_includes_buffer(tmp_path):
    store = Store(tmp_path)
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    frame[:, :, 1] = 80
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    jpeg = encoded.tobytes()
    buffered = [(100 + i / 10, jpeg) for i in range(10)]
    recording = Recording(store, Settings(), buffered, 0.85)
    for index in range(10):
        recording.add(101 + index / 10, jpeg)
    recording.finish()
    recording.thread.join(20)
    row = store.get(recording.id)
    assert row["status"] == "pending", row
    assert row["started"] == 100
    assert row["ended"] == 101.9
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,nb_frames,width,height",
            "-of",
            "json",
            str(store.clips / row["filename"]),
        ],
        capture_output=True,
        check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    assert stream["codec_name"] == "h264"
    assert int(stream["nb_frames"]) == 20
    assert stream["width"] == 960


@pytest.mark.skipif(not Path("models/train.onnx").exists(), reason="Exported model not installed")
def test_exported_model_runs_with_no_torch_runtime():
    from railwatcher.detection import TrainDetector

    score = TrainDetector(Path("models/train.onnx")).confidence(np.zeros((540, 960, 3), dtype=np.uint8))
    assert 0 <= score <= 1


def test_restart_recovers_video_committed_before_database_update(tmp_path):
    store = Store(tmp_path)
    store.create("committed", 10, 0.9, {})
    path = store.clips / "committed.mp4"
    path.write_bytes(b"a finished mp4")
    reopened = Store(tmp_path)
    row = reopened.get("committed")
    assert row["status"] == "pending"
    assert row["filename"] == path.name
    assert row["bytes"] == path.stat().st_size


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_capture_detection_and_recording_pipeline(tmp_path, monkeypatch):
    """Exercise real capture and encoding with synthetic video and deterministic inference."""
    import railwatcher.engine as engine_module

    model = tmp_path / "model.onnx"
    model.touch()
    config = ConfigStore(tmp_path)
    config.update(
        {
            "latitude": 43,
            "longitude": -79,
            "setup_complete": True,
            "enabled": True,
            "confirmations": 1,
            "pre_roll": 3,
            "post_roll": 3,
            "max_clip_seconds": 30,
        }
    )
    store = Store(tmp_path)

    class DeterministicDetector:
        def __init__(self, path):
            self.calls = 0

        def confidence(self, frame, detection_mask=()):
            self.calls += 1
            return 0.9 if 3 <= self.calls <= 6 else 0.01

    monkeypatch.setattr(engine_module, "TrainDetector", DeterministicDetector)
    monkeypatch.setattr(
        engine_module,
        "capture_command",
        lambda settings: [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=960x540:rate=10",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
    )
    engine = engine_module.Engine(config, store, model)
    engine.start()
    try:
        deadline = time.monotonic() + 20
        rows = []
        while time.monotonic() < deadline:
            rows = store.list()
            if rows and rows[0]["status"] == "pending":
                break
            time.sleep(0.1)
        assert rows and rows[0]["status"] == "pending", (rows, engine.status())
        assert engine.preview() is not None
        assert rows[0]["ended"] - rows[0]["started"] >= 4
        assert (store.clips / rows[0]["filename"]).stat().st_size > 1000
    finally:
        engine.close()
    assert not any(thread.is_alive() for thread in engine.threads)


def test_train_trigger_continues_only_spatially_matched_strong_train():
    def box(score, bbox=(.2,.3,.7,.45)):
        return [{"bbox": list(bbox), "confidence": score}]
    trigger = Trigger(3,15)
    assert trigger.feed(False, 0, box(.2)) is None
    assert trigger.feed(True, 1, box(.7)) is None
    assert trigger.feed(False, 2, box(.18)) is None
    assert trigger.feed(False, 3, box(.12)) is None
    assert trigger.feed(False, 5, box(.15)) == "start"
    assert trigger.feed(False, 10, box(.2, (.8,.7,.9,.9))) is None
    assert trigger.feed(False, 20, []) == "end"
    trigger = Trigger(3,15)
    for stamp in range(20):
        assert trigger.feed(False, stamp, box(.3)) is None
    assert not trigger.active


def test_train_trigger_does_not_confirm_disjoint_boxes_or_stale_anchors():
    trigger = Trigger(3,15)
    assert trigger.feed(True, 0, [{"bbox":[.1,.1,.3,.3],"confidence":.8}]) is None
    for stamp in range(1,5):
        assert trigger.feed(False, stamp, [{"bbox":[.7,.7,.9,.9],"confidence":.2}]) is None
    for stamp in range(10,20):
        assert trigger.feed(False, stamp, [{"bbox":[.1,.1,.3,.3],"confidence":.2}]) is None
    assert not trigger.active



def test_weak_continuation_does_not_confirm_a_brief_road_vehicle():
    trigger = Trigger(3,15)
    box = [.2,.3,.6,.45]
    for stamp, score in [(10,.56),(11,.24),(12,.79)]:
        assert trigger.feed(score>=.55, stamp, [{"bbox":box,"confidence":score}]) is None
    for stamp in range(13,31):
        assert trigger.feed(False, stamp, []) is None
    assert not trigger.active
