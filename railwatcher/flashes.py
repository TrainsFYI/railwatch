"""Red-flash candidates in user-selected fixed landmarks, not train identities."""
import hashlib
import json
from itertools import pairwise

import cv2
import numpy as np

from .motion import MotionIndex

VERSION = 1


def red_strength(frame, box):
    height, width = frame.shape[:2]
    left, top, right, bottom = box
    crop = frame[int(top*height):max(int(top*height)+1, int(bottom*height)),
                 int(left*width):max(int(left*width)+1, int(right*width))]
    blue, green, red = cv2.split(crop.astype(np.float32))
    # Colour contrast rather than brightness alone rejects headlights/exposure changes.
    strength = np.maximum(0, red - np.maximum(green, blue))
    strength[red < 45] = 0
    # Independent fixed cells preserve alternating paired lamps: combining both
    # lamps into one brightness trace would look continuously red.
    cells = [cell for band in np.array_split(strength, 2, axis=0)
             for cell in np.array_split(band, 4, axis=1)]
    return [float(np.mean(np.sort(cell.ravel())[-min(3, cell.size):])) if cell.size else 0
            for cell in cells]


def flash_windows(samples):
    """Require three bounded pulses with comparable cadence and dark intervals."""
    if not samples:
        return []
    times = np.array([row[0] for row in samples])
    values = np.array([row[1] for row in samples])
    baseline = float(np.percentile(values, 10))
    high, low = max(18, baseline+15), max(8, baseline+5)
    pulses, start, active = [], None, False
    for stamp, value in zip(times, values):
        if not active and value >= high:
            start, active = stamp, True
        elif active and value <= low:
            if .15 <= stamp-start <= 1.6:
                pulses.append((float(start), float(stamp)))
            active = False
    windows, run = [], []

    def finish():
        if len(run) >= 3 and run[-1][1]-run[0][0] >= 2:
            windows.append({'started': round(max(0,run[0][0]-.5),2), 'ended': round(run[-1][1],2)})
        run.clear()

    for pulse in pulses:
        if run:
            gap = pulse[0]-run[-1][1]
            periods = [b[0]-a[0] for a,b in pairwise(run)] + [pulse[0]-run[-1][0]]
            if not .15 <= gap <= 1.6 or max(periods) > min(periods)*1.8:
                finish()
        run.append(pulse)
    finish()
    return windows


def analyse_flashes(path, landmarks, stop=None):
    capture = cv2.VideoCapture(str(path))
    fps = capture.get(cv2.CAP_PROP_FPS)
    expected = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    if not capture.isOpened() or not 0 < fps < 1000:
        capture.release()
        raise ValueError('Footage could not be decoded')
    samples = {item.id: [[] for _ in range(8)] for item in landmarks}
    number = 0
    try:
        while not (stop and stop.is_set()):
            ok, frame = capture.read()
            if not ok:
                break
            stamp = number/fps
            number += 1
            for item in landmarks:
                for cell, strength in zip(samples[item.id], red_strength(frame,item.box)):
                    cell.append((stamp,strength))
        if stop and stop.is_set():
            raise InterruptedError()
        if number < 2 or number < expected-1:
            raise ValueError('Footage has too few frames')
        windows = []
        for item in landmarks:
            candidates = sorted([window for cell in samples[item.id] for window in flash_windows(cell)], key=lambda row: row['started'])
            merged = []
            for window in candidates:
                if merged and window['started'] <= merged[-1]['ended']+.5:
                    merged[-1]['ended'] = max(merged[-1]['ended'],window['ended'])
                else:
                    merged.append(dict(window))
            windows.extend({**window, 'landmark_id':item.id, 'label':item.label} for window in merged)
        windows.sort(key=lambda row: row['started'])
        return {'state':'motion' if windows else 'quiet', 'windows':windows}
    finally:
        capture.release()


class FlashIndex(MotionIndex):
    def __init__(self, footage, config):
        super().__init__(footage, config)
        self.directory = footage.directory.parent / 'flashes'
        self.directory.mkdir(exist_ok=True)

    def signature(self, segment):
        settings = self.config.get()
        digest = hashlib.sha256(json.dumps([VERSION, settings.source,
            [item.model_dump() for item in settings.landmarks]]).encode()).hexdigest()
        return f"{segment['bytes']}:{digest}"

    def ordered_segments(self):
        return list(reversed(self.footage._segments()))

    def analyse_segment(self, segment):
        if not self.config.get().landmarks:
            return {'state':'quiet', 'windows':[]}
        return analyse_flashes(self.footage.find(segment['id']), self.config.get().landmarks, self.stop)
