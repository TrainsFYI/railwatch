from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class TrainDetector:
    """Single-class YOLOv8 ONNX exported at 640px, without embedded NMS.

    Keeps PyTorch off the Pi. Full frame inference matches TrainsFYI training.
    """

    def __init__(self, path: Path):
        self.net = cv2.dnn.readNetFromONNX(str(path))
        cv2.setNumThreads(2)

    def confidence(self, frame: np.ndarray, detection_mask=()) -> float:
        height, width = frame.shape[:2]
        scale = min(640 / width, 640 / height)
        resized = cv2.resize(frame, (round(width * scale), round(height * scale)))
        canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
        y, x = (640 - resized.shape[0]) // 2, (640 - resized.shape[1]) // 2
        canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
        self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255, (640, 640), swapRB=True))
        output = self.net.forward()
        if output.ndim != 3 or output.shape[1] != 5:
            raise ValueError("Expected single-class YOLOv8 output [1,5,N]; export the TrainsFYI model")
        detections = output[0]
        scores = detections[4]
        if not detection_mask:
            return float(np.max(scores))

        # The model sees the padded 640x640 image. Convert detection centers back
        # to normalized coordinates in the original camera frame.
        centers_x = (detections[0] - x) / scale / width
        centers_y = (detections[1] - y) / scale / height
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

    def feed(self, positive: bool, now: float) -> str | None:
        if positive:
            if not self.positives:
                self.first_positive = now
            self.positives += 1
            self.last_positive = now
        else:
            self.positives = 0
        if not self.active and self.positives >= self.confirmations:
            self.active = True
            return "start"
        if self.active and now - self.last_positive >= self.post_roll:
            self.active = False
            self.positives = 0
            return "end"
        return None
