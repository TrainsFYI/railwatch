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

    def confidence(self, frame: np.ndarray) -> float:
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
        return float(np.max(output[0, 4]))


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
