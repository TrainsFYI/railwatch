"""Persistent, local motion candidates for completed rolling-footage segments."""
import hashlib
import json
import logging
import threading

import cv2
import numpy as np

log = logging.getLogger(__name__)
VERSION = 1


def area_mask(shape, points):
    height, width = shape
    mask = np.ones(shape, dtype=np.uint8)
    if points:
        mask.fill(0)
        polygon = np.array([(round(x * (width - 1)), round(y * (height - 1))) for x, y in points], np.int32)
        cv2.fillPoly(mask, [polygon], 1)
    return mask.astype(bool)


def changed_fraction(before, after, mask):
    # Remove uniform exposure shifts; blur/open suppress codec noise and isolated pixels.
    delta = after.astype(np.int16) - before.astype(np.int16)
    delta = np.abs(delta - np.median(delta))
    changed = cv2.morphologyEx((delta >= 18).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return float(np.mean(changed[mask])) if mask.any() else 0.0


def analyse(path, points, stop=None):
    capture = cv2.VideoCapture(str(path))
    previous, mask, windows, run = None, None, [], []
    frame_number, next_sample, peak = 0, 0.0, 0.0
    fps = capture.get(cv2.CAP_PROP_FPS)
    expected_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    if not capture.isOpened() or not 0 < fps < 1000:
        capture.release()
        raise ValueError("Footage could not be decoded")

    def finish_run():
        if len(run) >= 3:
            windows.append({"started": round(max(0, run[0] - 0.5), 2), "ended": round(run[-1], 2)})
        run.clear()

    try:
        while not (stop and stop.is_set()):
            ok, frame = capture.read()
            if not ok:
                break
            stamp = frame_number / fps
            frame_number += 1
            if stamp < next_sample:
                continue
            next_sample = stamp + 0.5
            gray = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
            if mask is None:
                mask = area_mask(gray.shape, points)
            if previous is not None:
                fraction = changed_fraction(previous, gray, mask)
                peak = max(peak, fraction)
                if fraction >= 0.03:
                    run.append(stamp)
                else:
                    finish_run()
            previous = gray
        finish_run()
        if stop and stop.is_set():
            raise InterruptedError()
        if frame_number < 2 or frame_number < expected_frames - 1:
            raise ValueError("Footage has too few frames")
        # Combine nearby motion runs into reviewable windows, without claiming train identity.
        merged = []
        for window in windows:
            if merged and window["started"] - merged[-1]["ended"] <= 3:
                merged[-1]["ended"] = window["ended"]
            else:
                merged.append(window)
        return {"state": "motion" if merged else "quiet", "windows": merged, "peak_fraction": round(peak, 4)}
    finally:
        capture.release()


class MotionIndex:
    def __init__(self, footage, config):
        self.footage, self.config = footage, config
        self.directory = footage.directory.parent / "motion"
        self.directory.mkdir(exist_ok=True)
        self.stop = threading.Event()
        self.thread = None
        self.lock = threading.RLock()
        self.cache = {}
        cv2.setNumThreads(1)

    def signature(self, segment):
        settings = self.config.get()
        # Source changes also invalidate results without exposing credentials in cache files.
        digest = hashlib.sha256(json.dumps([VERSION, settings.source, settings.detection_mask]).encode()).hexdigest()
        return f"{segment['bytes']}:{digest}"

    def result(self, segment):
        signature = self.signature(segment)
        with self.lock:
            row = self.cache.get(segment["id"])
            if row is None:
                try:
                    row = json.loads((self.directory / f"{segment['id']}.json").read_text())
                    self.cache[segment["id"]] = row
                except (OSError, ValueError):
                    row = {}
            return row["result"] if row.get("signature") == signature else {"state": "pending", "windows": []}

    def scan(self, segment):
        signature = self.signature(segment)
        try:
            result = analyse(self.footage.directory / segment["id"], self.config.get().detection_mask, self.stop)
        except InterruptedError:
            return
        except (ValueError, OSError, cv2.error):
            result = {"state": "error", "windows": []}
        row = {"signature": signature, "result": result}
        path = self.directory / f"{segment['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(row))
        temporary.replace(path)
        with self.lock:
            self.cache[segment["id"]] = row

    def start(self):
        self.thread = threading.Thread(target=self._work, name="footage-motion", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=15)

    def _work(self):
        while not self.stop.is_set():
            try:
                segments = self.footage._segments()
                # Oldest first: scan footage before its 24-hour retention expires.
                for segment in segments:
                    if self.stop.is_set():
                        return
                    if self.result(segment)["state"] == "pending":
                        self.scan(segment)
                        self.stop.wait(0.05)
                live = {segment["id"] for segment in segments}
                with self.lock:
                    self.cache = {key: row for key, row in self.cache.items() if key in live}
                for path in self.directory.glob("*.mp4.json"):
                    if path.name.removesuffix(".json") not in live:
                        path.unlink(missing_ok=True)
            except Exception:
                log.exception("Motion indexing failed; retrying")
            self.stop.wait(5)

    def annotate(self, data, filter_state="all"):
        counts = {state: 0 for state in ("motion", "quiet", "pending", "error")}
        rows = []
        for segment in data["segments"]:
            result = self.result(segment)
            counts[result["state"]] += 1
            if filter_state == "all" or filter_state == result["state"]:
                rows.append({**segment, "motion": result})
        return {**data, "segments": rows, "motion_counts": counts}
