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
