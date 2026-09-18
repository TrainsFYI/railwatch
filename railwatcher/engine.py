import collections
import logging
import os
import queue
import select
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from .detection import TrainDetector, Trigger

log = logging.getLogger(__name__)
WIDTH, HEIGHT, FPS = 960, 540, 10


def capture_command(settings):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if settings.source_type == "rtsp":
        command += ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", settings.source]
    else:
        command += ["-f", "v4l2", "-framerate", str(FPS), "-video_size", "1280x720", "-i", settings.source]
    return command + [
        "-an",
        "-vf",
        f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def read_frame(process, stop, timeout=15):
    data = bytearray()
    deadline = time.monotonic() + timeout
    size = WIDTH * HEIGHT * 3
    while len(data) < size and not stop.is_set():
        if time.monotonic() >= deadline:
            raise TimeoutError("Camera stopped delivering frames")
        ready, _, _ = select.select([process.stdout], [], [], 0.5)
        if ready:
            chunk = os.read(process.stdout.fileno(), min(size - len(data), 65536))
            if not chunk:
                raise OSError("Camera disconnected")
            data.extend(chunk)
    if len(data) != size:
        raise OSError("Capture stopped")
    return np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)


class Recording:
    def __init__(self, store, settings, frames, score):
        self.store = store
        self.id = str(uuid.uuid4())
        self.started = frames[0][0]
        self.last_frame = self.started
        self.score = score
        self.limit = settings.max_clip_seconds
        self.byte_limit = (settings.max_clip_seconds + settings.pre_roll) * 1_000_000
        self.path = store.clips / f"{self.id}.partial.mp4"
        self.final = store.clips / f"{self.id}.mp4"
        metadata = settings.model_dump(include={"name", "latitude", "longitude", "bearing", "field_of_view"})
        metadata.update(schema_version=1, capture_fps=FPS, width=WIDTH, height=HEIGHT)
        store.create(self.id, self.started, score, metadata)
        self.frames = queue.Queue(maxsize=FPS * 40)
        self.done = threading.Event()
        self.error = None
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-framerate",
                str(FPS),
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(self.path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.thread = threading.Thread(target=self._write, name="clip-writer", daemon=True)
        self.thread.start()
        for stamp, jpeg in frames:
            self.add(stamp, jpeg)

    def add(self, stamp, jpeg):
        if self.done.is_set():
            return
        # Close a valid shorter clip if the encoder exceeds its reserved space.
        if self.path.exists() and self.path.stat().st_size >= self.byte_limit:
            self.done.set()
            return
        self.last_frame = stamp
        try:
            self.frames.put_nowait(jpeg)
        except queue.Full:
            self.error = "Storage could not keep up with recording"
            self.done.set()

    def finish(self, error=None):
        self.error = error or self.error
        self.done.set()

    def _write(self):
        try:
            while not self.done.is_set() or not self.frames.empty():
                try:
                    jpeg = self.frames.get(timeout=0.3)
                except queue.Empty:
                    continue
                view = memoryview(jpeg)
                deadline = time.monotonic() + 10
                while view:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Encoder stalled")
                    _, ready, _ = select.select([], [self.process.stdin], [], 0.5)
                    if ready:
                        written = os.write(self.process.stdin.fileno(), view[:4096])
                        view = view[written:]
            self.process.stdin.close()
            result = self.process.wait(timeout=30)
            if result or self.error:
                raise OSError(self.error or "Video encoder failed")
            self.path.replace(self.final)
            self.store.update(
                self.id,
                ended=self.last_frame,
                confidence=self.score,
                status="pending",
                filename=self.final.name,
                bytes=self.final.stat().st_size,
            )
        except (OSError, subprocess.SubprocessError):
            self.error = self.error or "Recording could not be saved"
            self.process.kill()
            self.process.wait()
            self.path.unlink(missing_ok=True)
            self.store.update(self.id, ended=self.last_frame, status="failed", error=self.error)
        finally:
            self.done.set()


class Engine:
    def __init__(self, config, store, model: Path):
        self.config, self.store, self.model_path = config, store, model
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.preview_requested = 0.0
        self.jpeg = None
        self.frame = None
        self.frame_time = 0.0
        self.buffer = collections.deque()
        self.recording = None
        self.writers = []
        self.process = None
        self.camera_status = "idle"
        self.detector_status = "starting"
        self.storage_status = None
        self.last_score = 0.0
        self.threads = []

    def start(self):
        for target in (self._capture, self._detect):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        for thread in self.threads:
            thread.join(timeout=20)
        with self.lock:
            self._finish()
        for writer in self.writers:
            writer.thread.join(timeout=40)

    def preview(self):
        self.preview_requested = time.monotonic()
        with self.lock:
            return self.jpeg if time.time() - self.frame_time < 5 else None

    def status(self):
        with self.lock:
            return {
                "camera": self.camera_status,
                "detector": self.detector_status,
                "storage_error": self.storage_status,
                "recording": self.recording.id if self.recording else None,
                "confidence": round(self.last_score, 3),
                "last_frame": self.frame_time or None,
                "buffer_seconds": round(len(self.buffer) / FPS, 1),
                "model_available": self.model_path.is_file(),
            }

    def _finish(self, error=None):
        if self.recording:
            self.recording.finish(error)
            self.recording = None

    def _capture(self):
        while not self.stop.is_set():
            settings = self.config.get()
            if not settings.enabled and time.monotonic() - self.preview_requested > 15:
                self.camera_status = "idle"
                self.stop.wait(0.5)
                continue
            source = (settings.source_type, settings.source)
            try:
                self.camera_status = "connecting"
                self.process = subprocess.Popen(
                    capture_command(settings), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
                )
                while not self.stop.is_set():
                    current = self.config.get()
                    if source != (current.source_type, current.source):
                        break
                    if not current.enabled and time.monotonic() - self.preview_requested > 15:
                        break
                    frame = read_frame(self.process, self.stop)
                    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if not ok:
                        continue
                    stamp, jpeg = time.time(), encoded.tobytes()
                    with self.lock:
                        self.frame, self.frame_time, self.jpeg = frame, stamp, jpeg
                        self.camera_status = "connected"
                        self.buffer.append((stamp, jpeg))
                        while self.buffer and (
                            stamp - self.buffer[0][0] > current.pre_roll + 10 or len(self.buffer) > FPS * 45
                        ):
                            self.buffer.popleft()
                        if self.recording:
                            self.recording.add(stamp, jpeg)
            except (OSError, subprocess.SubprocessError, cv2.error):
                self.camera_status = "unavailable — check camera address, credentials, or USB connection"
                log.warning("Camera unavailable; retrying in five seconds")
            finally:
                if self.process:
                    if self.process.poll() is None:
                        self.process.kill()
                    self.process.wait()
                with self.lock:
                    self._finish()
                    self.buffer.clear()
                    self.frame = self.jpeg = None
            self.stop.wait(5)

    def _detect(self):
        detector, trigger, previous, last_frame = None, None, None, 0
        while not self.stop.is_set():
            settings = self.config.get()
            if not settings.enabled:
                with self.lock:
                    self._finish()
                trigger = None
                self.detector_status = "paused" if settings.setup_complete else "awaiting setup"
                self.stop.wait(0.5)
                continue
            if not self.model_path.is_file():
                self.detector_status = "model missing — install the TrainsFYI ONNX model"
                self.stop.wait(3)
                continue
            try:
                if detector is None:
                    detector = TrainDetector(self.model_path)
                if trigger is None or previous != settings:
                    with self.lock:
                        self._finish()
                    trigger = Trigger(settings.confirmations, settings.post_roll)
                    previous = settings
                with self.lock:
                    frame, stamp = self.frame, self.frame_time
                if frame is None or stamp == last_frame or time.time() - stamp > 5:
                    self.detector_status = "waiting for camera"
                    self.stop.wait(0.5)
                    continue
                last_frame = stamp
                score = detector.confidence(frame)
                self.last_score = score
                action = trigger.feed(score >= settings.confidence, stamp)
                self.detector_status = "watching"
                with self.lock:
                    self.writers = [writer for writer in self.writers if writer.thread.is_alive()]
                    if self.recording and (
                        action == "end"
                        or self.recording.done.is_set()
                        or stamp - self.recording.started >= settings.max_clip_seconds
                    ):
                        self._finish()
                    if not self.recording and trigger.active:
                        # Reserve a conservative 1 MB/s for this recording, including pre-roll.
                        reserve = (settings.max_clip_seconds + settings.pre_roll) * 1_000_000
                        available = shutil.disk_usage(self.store.directory).free
                        if (
                            self.writers
                            or not self.store.make_room(int(settings.storage_gb * 1e9), reserve)
                            or available < reserve + 200_000_000
                        ):
                            self.storage_status = "Waiting for storage: uploaded clips can be pruned; pending clips are preserved"
                        else:
                            start = (
                                trigger.first_positive - settings.pre_roll
                                if action == "start"
                                else stamp - settings.pre_roll
                            )
                            frames = [(t, jpeg) for t, jpeg in self.buffer if t >= start]
                            if frames:
                                self.recording = Recording(self.store, settings, frames, score)
                                self.writers.append(self.recording)
                                self.storage_status = None
                    if self.recording:
                        self.recording.score = max(score, self.recording.score)
            except Exception:
                log.exception("Detection/recording failure")
                self.detector_status = "detector error — check model and service logs"
                with self.lock:
                    self._finish()
                detector, trigger = None, None
                self.stop.wait(5)
            self.stop.wait(0.5)
