from collections import namedtuple
from types import SimpleNamespace

import numpy as np

from railwatcher.config import Settings
from railwatcher.engine import Engine
from railwatcher.store import Store


def test_engine_file_boundaries_do_not_end_the_train(tmp_path, monkeypatch):
    import railwatcher.engine as runtime
    clock = [1000.0]
    settings = Settings(enabled=True, setup_complete=True, source='rtsp://camera/live', source_type='rtsp',
                        latitude=43, longitude=-79, max_clip_seconds=30, confirmations=1)
    store = Store(tmp_path)
    model = tmp_path / 'model.onnx'
    model.write_bytes(b'fake-model')
    engine = Engine(SimpleNamespace(get=lambda: settings), store, model)
    engine.frame = np.zeros((4, 4, 3), dtype=np.uint8)
    engine.frame_time = clock[0]
    engine.camera_status = 'connected'
    engine.buffer.append((clock[0], b'frame'))
    monkeypatch.setattr(runtime.time, 'time', lambda: clock[0])
    monkeypatch.setattr(runtime.shutil, 'disk_usage', lambda _: namedtuple('Disk', 'total used free')(10**12, 0, 10**12))
    class Detector:
        def __init__(self):
            self.last_boxes = [[.1, .2, .8, .9]]
        def confidence(self, frame, mask):
            return .9 if clock[0] <= 1080 else 0
    monkeypatch.setattr(runtime, 'TrainDetector', lambda _: Detector())
    class Writer:
        def __init__(self, store, settings, frames, score):
            self.id = 'part-' + str(int(clock[0]))
            self.started = frames[0][0]
            self.last_frame = frames[-1][0]
            self.score = score
            self.cutoff = False
            self.done = SimpleNamespace(is_set=lambda: False)
            self.thread = SimpleNamespace(is_alive=lambda: False)
            store.create(self.id, self.started, score, {})
        def add(self, stamp, frame):
            self.last_frame = stamp
        def finish(self):
            path = store.clips / (self.id + '.mp4')
            path.write_bytes(b'video')
            store.update(self.id, ended=self.last_frame, status='pending', filename=path.name, bytes=5)
    monkeypatch.setattr(runtime, 'Recording', Writer)
    class Stop:
        def is_set(self):
            return clock[0] > 1100
        def wait(self, seconds):
            clock[0] += 10
            engine.frame_time = clock[0]
            engine.buffer.append((clock[0], b'frame'))
            if engine.recording:
                engine.recording.add(clock[0], b'frame')
    engine.stop = Stop()
    engine._detect()
    row = engine.passages.pending()
    assert row is not None
    assert len(row['parts']) >= 3
    assert row['passage']['complete']
    assert row['passage']['detected_ended'] == 1080
    assert row['parts'][-1]['ended'] == 1100
    assert engine.passages.active is None


def test_recording_accepts_two_minutes_of_preroll_without_overflow(tmp_path, monkeypatch):
    import railwatcher.engine as runtime
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(runtime.threading.Thread, 'start', lambda self: None)
    settings = Settings(pre_roll=120)
    frames = [(float(i) / runtime.FPS, b'jpeg') for i in range(runtime.FPS * 120)]
    recording = runtime.Recording(Store(tmp_path), settings, frames, .9)
    assert recording.error is None
    assert not recording.done.is_set()
    assert recording.frames.qsize() == 1200
    for i in range(runtime.FPS * 40):
        recording.add(120 + i / runtime.FPS, b'live-frame')
    assert recording.error is None
    recording.add(160, b'overflow')
    assert recording.error == 'Storage could not keep up with recording'


def test_passage_ends_even_when_storage_prevents_any_recording(tmp_path, monkeypatch):
    import railwatcher.engine as runtime
    clock = [1000.]
    settings = Settings(enabled=True, setup_complete=True, confirmations=1, post_roll=15,
                        source="rtsp://camera/live", source_type="rtsp", latitude=43, longitude=-79)
    model = tmp_path / 'model.onnx'
    model.write_bytes(b'fake')
    engine = Engine(SimpleNamespace(get=lambda: settings), Store(tmp_path), model)
    engine.frame = np.zeros((4, 4, 3), dtype=np.uint8)
    engine.frame_time = clock[0]
    engine.camera_status = 'connected'
    engine.buffer.append((clock[0], b'frame'))
    monkeypatch.setattr(runtime.time, 'time', lambda: clock[0])
    monkeypatch.setattr(runtime.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(runtime, 'TrainDetector', lambda _: SimpleNamespace(
        confidence=lambda *args: .9 if clock[0] == 1000 else 0, last_boxes=[]))
    class Stop:
        def is_set(self):
            return clock[0] > 1030
        def wait(self, seconds):
            clock[0] += 10
            engine.frame_time = clock[0]
    engine.stop = Stop()
    engine._detect()
    assert engine.passages.active is None
    with engine.store.connect() as db:
        assert db.execute('SELECT status FROM passages').fetchone()[0] == 'pending'
