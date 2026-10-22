import datetime as dt
import itertools
import json
import logging
import os
import queue
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
SEGMENT_SECONDS = 120
RETENTION_SECONDS = 24 * 60 * 60
SEGMENT_NAME = re.compile(r"^(\d{8}T\d{6})\.mp4$")


class FootageBuffer:
    """Low-rate, rolling disk archive of the camera feed."""

    def __init__(self, directory: Path, config=None):
        self.config = config
        self.review_directory = directory.parent / "night-review"
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.frames = queue.Queue(maxsize=100)
        self.stop = threading.Event()
        self.thread = None
        self.process = None
        self.active = False
        self.frame_number = 0
        self.dropped_frames = 0
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop.clear()
            self.frames = queue.Queue(maxsize=100)
            self.frame_number = 0
            self.thread = threading.Thread(target=self._write, name="footage-buffer", daemon=True)
            self.thread.start()

    def add(self, stamp: float, jpeg: bytes):
        del stamp  # The segment muxer timestamps frames from a constant-rate input.
        if self.stop.is_set():
            return
        self.frame_number += 1
        if self.frame_number % 2:
            return
        try:
            self.frames.put_nowait(jpeg)
        except queue.Full:
            self.dropped_frames += 1

    def close(self):
        self.stop.set()
        thread = self.thread
        if thread:
            thread.join(timeout=35)

    def _write(self):
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", "5", "-i", "pipe:0",
            "-an", "-vf", "scale=640:-2:flags=fast_bilinear", "-c:v", "libx264",
            "-preset", "ultrafast", "-tune", "zerolatency", "-b:v", "350k",
            "-maxrate", "350k", "-bufsize", "700k", "-g", "25", "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-reset_timestamps", "1",
            "-strftime", "1", "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
            str(self.directory / "%Y%m%dT%H%M%S.mp4"),
        ]
        env = os.environ.copy()
        env["TZ"] = "UTC"
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env,
            )
            self.active = True
            next_prune = time.monotonic() + 60
            while not self.stop.is_set() or not self.frames.empty():
                if time.monotonic() >= next_prune:
                    self._prune()
                    next_prune = time.monotonic() + 60
                try:
                    jpeg = self.frames.get(timeout=0.25)
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise OSError("Footage encoder stopped")
                    continue
                if self.process.poll() is not None:
                    raise OSError("Footage encoder stopped")
                self.process.stdin.write(jpeg)
            self.process.stdin.close()
            if self.process.wait(timeout=30):
                raise OSError("Footage encoder failed")
        except (OSError, subprocess.SubprocessError):
            log.exception("Rolling footage buffer stopped")
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait()
        finally:
            self.active = False
            self.process = None
            self._prune()

    def _segments(self, now=None):
        now = time.time() if now is None else now
        found = []
        paths = {path.name: path for path in self.review_directory.glob("*.mp4")}
        paths.update({path.name: path for path in self.directory.glob("*.mp4")})
        for path in paths.values():
            match = SEGMENT_NAME.fullmatch(path.name)
            if not match:
                continue
            try:
                file_stat = path.stat()
                size = file_stat.st_size
            except FileNotFoundError:
                continue
            if size == 0 or not stat.S_ISREG(file_stat.st_mode):
                continue
            try:
                started = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(
                    tzinfo=dt.UTC
                ).timestamp()
            except ValueError:
                continue
            if path.parent == self.review_directory or started >= now - RETENTION_SECONDS - SEGMENT_SECONDS:
                found.append((started, path, size))
        found.sort()
        if self.active and found and found[-1][1].parent == self.directory:
            # The last live segment is still open and has no MP4 index yet.
            found.pop()
        result = []
        for index, (started, path, size) in enumerate(found):
            next_start = found[index + 1][0] if index + 1 < len(found) else started + SEGMENT_SECONDS
            ended = min(next_start, started + SEGMENT_SECONDS, now)
            if ended > started:
                result.append({
                    "id": path.name,
                    "started": started,
                    "ended": ended,
                    "bytes": size,
                    "video_url": f"/api/buffer/{path.name}",
                    "retained_for_review": (self.review_directory / path.name).is_file(),
                })
        return result

    def list(self):
        now = time.time()
        segments = self._segments(now)
        return {
            "retention_seconds": RETENTION_SECONDS,
            "segment_seconds": SEGMENT_SECONDS,
            "active": self.active,
            "dropped_frames": self.dropped_frames,
            "bytes": sum(item["bytes"] for item in segments),
            "available_seconds": sum(item["ended"] - item["started"] for item in segments),
            "segments": segments,
        }

    def find(self, filename: str):
        if not SEGMENT_NAME.fullmatch(filename):
            return None
        for item in self._segments():
            if item["id"] == filename:
                live = self.directory / filename
                return live if live.is_file() else self.review_directory / filename
        return None

    def _prune(self):
        now = time.time()
        segments = []
        for path in self.directory.glob("*.mp4"):
            match = SEGMENT_NAME.fullmatch(path.name)
            if not match:
                continue
            try:
                started = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(
                    tzinfo=dt.UTC
                ).timestamp()
            except ValueError:
                continue
            segments.append((started, path))
        segments.sort()
        active_path = segments[-1][1] if self.active and segments else None
        self._retain_night(segments, active_path, now)
        free_bytes = shutil.disk_usage(self.directory).free
        for started, path in segments:
            too_old = now - started > RETENTION_SECONDS
            low_space = free_bytes < 1_000_000_000
            if path != active_path and (too_old or low_space):
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                free_bytes += size

    def _retain_night(self, segments, active_path, now):
        if self.config is None:
            return
        settings = self.config.get()
        self.review_directory.mkdir(exist_ok=True)
        if settings.night_review_enabled:
            zone = ZoneInfo(settings.night_review_timezone)
            start, end = settings.night_review_start_hour, settings.night_review_end_hour
            for started, path in segments:
                hour = dt.datetime.fromtimestamp(started, zone).hour
                in_window = start <= hour < end if start < end else hour >= start or hour < end
                if path == active_path or not in_window or now - started > settings.night_review_hours * 3600:
                    continue
                target = self.review_directory / path.name
                if not target.exists() and shutil.disk_usage(self.directory).free >= 1_000_000_000:
                    try:
                        os.link(path, target)
                    except FileExistsError:
                        pass
                    except OSError:
                        log.warning("Could not retain a night review segment", exc_info=True)
        # Budget includes retained originals, independent of pending upload media.
        retained = sorted(self.review_directory.glob("*.mp4"))
        total = sum(path.stat().st_size for path in retained)
        limit = int(settings.night_review_gb * 1_000_000_000)
        for path in retained:
            if not SEGMENT_NAME.fullmatch(path.name):
                continue
            try:
                started = dt.datetime.strptime(path.stem, "%Y%m%dT%H%M%S").replace(tzinfo=dt.UTC).timestamp()
            except ValueError:
                continue
            if now-started > settings.night_review_hours*3600 or total > limit or shutil.disk_usage(self.directory).free < 1_000_000_000:
                total -= path.stat().st_size
                path.unlink(missing_ok=True)

    def export_mark(self, store, settings, started: float, ended: float, *, event_id=None, candidate_evidence=None):
        rows = self._segments()
        if not rows or started < rows[0]["started"] or ended > rows[-1]["ended"]:
            raise ValueError("That time range is no longer in the available footage buffer")
        selected = [row for row in rows if row["started"] < ended and row["ended"] > started]
        if not selected or selected[0]["started"] > started or selected[-1]["ended"] < ended:
            raise ValueError("The selected footage is not available")
        for before, after in itertools.pairwise(selected):
            if after["started"] - before["ended"] > 5:
                raise ValueError("The selected range crosses a gap in camera footage")

        event_id = event_id or str(uuid.uuid4())
        existing = store.get(event_id)
        if existing:
            if existing['started'] != started or (existing['ended'] is not None and existing['ended'] != ended):
                raise ValueError('Recovery identity does not match its capture interval')
            if existing['filename'] and (store.clips/existing['filename']).is_file():
                return existing
        duration = ended - started
        reserve = int(duration * 100_000) + 200_000_000
        if not store.make_room(int(settings.storage_gb * 1_000_000_000), reserve):
            raise OSError("Not enough recording storage is available for this clip")
        if shutil.disk_usage(store.directory).free < reserve:
            raise OSError("Not enough free disk space is available for this clip")
        metadata = settings.model_dump(include={"name", "latitude", "longitude", "bearing", "field_of_view"})
        metadata.update(schema_version=1, capture_fps=5, width=640, height=360, manual_review=True)
        if candidate_evidence:
            metadata.update(manual_review=False, review_reset=True, night_candidate_evidence=candidate_evidence)
        if existing:
            with store.connect() as db:
                db.execute("UPDATE sightings SET status='recording',metadata=?,error=NULL WHERE id=?",
                           (json.dumps(metadata), event_id))
        else:
            store.create(event_id, started, 0, metadata)
        concat = self.directory / f"{event_id}.concat.txt"
        partial = store.clips / f"{event_id}.partial.mp4"
        final = store.clips / f"{event_id}.mp4"
        try:
            concat.write_text("".join(f"file '{self.find(row['id']).as_posix()}'\n" for row in selected))
            result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-ss", f"{started - selected[0]['started']:.3f}", "-t", f"{duration:.3f}",
                    "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial),
                ],
                capture_output=True, timeout=max(60, duration * 2), check=False,
            )
            if result.returncode or not partial.is_file() or partial.stat().st_size == 0:
                raise OSError("Could not create a clip from the selected footage")
            partial.replace(final)
            store.update(
                event_id, ended=ended, status="pending", filename=final.name, bytes=final.stat().st_size,
            )
            return store.get(event_id)
        except Exception as error:
            partial.unlink(missing_ok=True)
            store.update(event_id, ended=ended, status="failed", error="Could not create a clip from reviewed footage")
            if isinstance(error, (OSError, subprocess.SubprocessError)):
                raise OSError("Could not create a clip from the selected footage") from None
            raise
        finally:
            concat.unlink(missing_ok=True)
