import datetime as dt

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.config import ConfigStore
from railwatcher.footage import FootageBuffer
from railwatcher.motion import MotionIndex, analyse


def video(path, kind):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (320, 180))
    assert writer.isOpened()
    for index in range(40):
        frame = np.full((180, 320, 3), 50, np.uint8)
        if kind == "exposure":
            frame.fill(50 + index * 3)
        elif kind in {"inside", "outside", "flash"}:
            x = 30 if kind == "outside" else 170
            if kind != "flash" or index == 9:
                y = 40 + (index // 3 % 5) * 18
                frame[y:y+60, x:x+80] = 230
        writer.write(frame)
    writer.release()


@pytest.mark.parametrize("kind, expected", [("inside", "motion"), ("outside", "quiet"),
                                           ("exposure", "quiet"), ("still", "quiet"), ("flash", "quiet")])
def test_motion_is_sustained_and_inside_polygon(tmp_path, kind, expected):
    path = tmp_path / "sample.mp4"
    video(path, kind)
    result = analyse(path, [(0.5, 0.1), (0.95, 0.1), (0.95, 0.9), (0.5, 0.9)])
    assert result["state"] == expected
    assert bool(result["windows"]) == (expected == "motion")
    for window in result["windows"]:
        assert 0 <= window["started"] < window["ended"] < 8


def test_full_frame_when_no_polygon(tmp_path):
    path = tmp_path / "sample.mp4"
    video(path, "outside")
    assert analyse(path, [])["state"] == "motion"


def test_persistent_index_invalidates_changed_area_and_keeps_errors_separate(tmp_path):
    footage = FootageBuffer(tmp_path / "buffer")
    config = ConfigStore(tmp_path)
    name = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.mp4")
    path = footage.directory / name
    video(path, "inside")
    index = MotionIndex(footage, config)
    segment = footage.list()["segments"][0]
    assert index.result(segment)["state"] == "pending"
    index.scan(segment)
    assert index.result(segment)["state"] == "motion"
    restored = MotionIndex(footage, config)
    assert restored.result(segment)["state"] == "motion"
    config.update({"detection_mask": [[0, 0], [0.4, 0], [0.4, 1], [0, 1]]})
    assert restored.result(segment)["state"] == "pending"
    restored.scan(segment)
    assert restored.result(segment)["state"] == "quiet"
    path.write_bytes(b"broken mp4")
    segment["bytes"] = path.stat().st_size
    restored.scan(segment)
    assert restored.result(segment)["state"] == "error"


def test_buffer_filter_counts_and_validation(tmp_path):
    app = create_app(tmp_path, workers=False)
    footage = app.state.engine.footage
    names = ["20261010T120000.mp4", "20261010T120200.mp4", "20261010T120400.mp4"]
    for name in names:
        (footage.directory / name).write_bytes(b"placeholder")
    rows = [{"id": name, "bytes": 11, "started": i*120, "ended": (i+1)*120,
             "video_url": f"/api/buffer/{name}"} for i, name in enumerate(names)]
    # Isolate retention/time; actual video decoding is covered above.
    footage.list = lambda: {"segments": rows, "available_seconds": 360}
    for row, state in zip(rows[:2], ["motion", "quiet"]):
        app.state.motion.cache[row["id"]] = {"signature": app.state.motion.signature(row),
                                              "result": {"state": state, "windows": []}}
    with TestClient(app) as client:
        data = client.get("/api/buffer?movement=motion").json()
        assert [row["id"] for row in data["segments"]] == names[:1]
        assert data["motion_counts"] == {"motion": 1, "quiet": 1, "pending": 1, "error": 0}
        assert len(client.get("/api/buffer").json()["segments"]) == 3
        assert len(client.get("/api/buffer?movement=pending").json()["segments"]) == 1
        assert client.get("/api/buffer?movement=bogus").status_code == 422


def test_segment_listing_ignores_pruned_files_and_directories(tmp_path, monkeypatch):
    footage = FootageBuffer(tmp_path / "buffer")
    name = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.mp4")
    path = footage.directory / name
    path.mkdir()
    assert footage.list()["segments"] == []
    path.rmdir()
    path.write_bytes(b"video")
    original_stat = type(path).stat

    def vanished(candidate, *args, **kwargs):
        if candidate == path:
            raise FileNotFoundError()
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "stat", vanished)
    assert footage.list()["segments"] == []
