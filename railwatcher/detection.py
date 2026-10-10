import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


class TrainDetector:
    """YOLOv8 ONNX with explicit train class and optional track-area crop."""

    def __init__(self, path: Path):
        manifest_path = path.with_suffix('.json')
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        self.train_class = int(manifest.get('train_class_id', 0))
        self.class_count = len(manifest.get('classes', {'0':'train'}))
        if manifest and manifest.get('classes', {}).get(str(self.train_class)) != 'train':
            raise ValueError('Model manifest must identify the train class')
        if not 0 <= self.train_class < self.class_count:
            raise ValueError('Invalid train class in model manifest')
        self.focus_on_area = bool(manifest.get('focus_on_area', False))
        self.net = cv2.dnn.readNetFromONNX(str(path))
        cv2.setNumThreads(2)

    def confidence(self, frame: np.ndarray, detection_mask=()) -> float:
        height, width = frame.shape[:2]
        offset_x = offset_y = 0
        if detection_mask and getattr(self, 'focus_on_area', False):
            points = np.asarray(detection_mask)
            left, top = points.min(axis=0) * [width, height]
            right, bottom = points.max(axis=0) * [width, height]
            pad_x, pad_y = max(16, (right-left)*.1), max(16, (bottom-top)*.5)
            offset_x, offset_y = max(0, int(left-pad_x)), max(0, int(top-pad_y))
            end_x, end_y = min(width, int(right+pad_x)+1), min(height, int(bottom+pad_y)+1)
            frame = frame[offset_y:end_y, offset_x:end_x]
        crop_height, crop_width = frame.shape[:2]
        scale = min(640 / crop_width, 640 / crop_height)
        resized = cv2.resize(frame, (round(crop_width * scale), round(crop_height * scale)))
        canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
        y, x = (640 - resized.shape[0]) // 2, (640 - resized.shape[1]) // 2
        canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
        self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255, (640, 640), swapRB=True))
        output = self.net.forward()
        if output.ndim != 3 or output.shape[1] != 4 + getattr(self, "class_count", 1):
            raise ValueError("YOLO output classes do not match the model manifest")
        detections = output[0]
        scores = detections[4 + getattr(self, "train_class", 0)]
        if not detection_mask:
            return float(np.max(scores))

        # The model sees the padded 640x640 image. Convert detection centers back
        # to normalized coordinates in the original camera frame.
        centers_x = ((detections[0] - x) / scale + offset_x) / width
        centers_y = ((detections[1] - y) / scale + offset_y) / height
        polygon = np.asarray(detection_mask, dtype=np.float32)
        inside = np.zeros(centers_x.shape, dtype=bool)
        previous = polygon[-1]
        for current in polygon:
            x1, y1 = previous
            x2, y2 = current
            crosses = (y1 > centers_y) != (y2 > centers_y)
            boundary_x = (x2 - x1) * (centers_y - y1) / (y2 - y1 + 1e-12) + x1
            inside ^= crosses & (centers_x < boundary_x)
            previous = current
        inside &= (centers_x >= 0) & (centers_x <= 1) & (centers_y >= 0) & (centers_y <= 1)
        return float(np.max(scores[inside])) if np.any(inside) else 0.0


@dataclass
class Trigger:
    confirmations: int
    post_roll: float
    positives: int = 0
    active: bool = False
    first_positive: float = 0
    last_positive: float = 0
    recent: deque = field(default_factory=deque)

    def feed(self, positive: bool, now: float) -> str | None:
        # Brief confidence dips must not erase a train already seen repeatedly.
        # Expire isolated positives so unrelated events cannot accumulate.
        while self.recent and now - self.recent[0] > 5:
            self.recent.popleft()
        if positive:
            self.recent.append(now)
            self.last_positive = now
        self.positives = len(self.recent)
        if not self.active and self.positives >= self.confirmations:
            self.first_positive = self.recent[0]
            self.active = True
            return "start"
        if self.active and now - self.last_positive >= self.post_roll:
            self.active = False
            self.positives = 0
            self.recent.clear()
            return "end"
        return None
