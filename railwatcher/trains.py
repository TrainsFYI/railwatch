"""Archive review using the same model, area and threshold as live detection."""
import hashlib
import json

import cv2

from .detection import TrainDetector, Trigger
from .motion import MotionIndex

VERSION = 4


def analyse_trains(path, detector, settings, stop=None):
    capture = cv2.VideoCapture(str(path))
    fps, expected = capture.get(cv2.CAP_PROP_FPS), capture.get(cv2.CAP_PROP_FRAME_COUNT)
    if not capture.isOpened() or not 0 < fps < 1000:
        capture.release()
        raise ValueError('Footage could not be decoded')
    trigger = Trigger(settings.confirmations, settings.post_roll)
    windows, current = [], None
    number, next_sample, peak = 0, 0, 0
    try:
        while not (stop and stop.is_set()):
            ok, frame = capture.read()
            if not ok:
                break
            stamp = number/fps
            number += 1
            if stamp < next_sample:
                continue
            next_sample = stamp+1
            score = detector.confidence(frame, settings.detection_mask)
            peak = max(peak,score)
            action = trigger.feed(score >= settings.confidence,stamp,
                                  detections=getattr(detector, 'last_detections', None), threshold=settings.confidence)
            if action == 'start':
                current = {'started':max(0,trigger.first_positive-settings.pre_roll), 'ended':stamp}
            if current:
                current['ended'] = min(number/fps,trigger.last_positive+settings.post_roll)
            if action == 'end' and current:
                windows.append(current)
                current = None
        if stop and stop.is_set():
            raise InterruptedError()
        if number < 2 or number < expected-1:
            raise ValueError('Footage has too few frames')
        if current:
            current['ended'] = min(number/fps,trigger.last_positive+settings.post_roll)
            windows.append(current)
        for window in windows:
            window['started'] = round(window['started'],2)
            window['ended'] = round(window['ended'],2)
        return {'state':'motion' if windows else 'quiet', 'windows':windows, 'peak_confidence':round(peak,4)}
    finally:
        capture.release()


class TrainIndex(MotionIndex):
    # Refresh the segment list after each expensive inference pass, so new
    # footage is never stuck behind the entire historical backfill.
    scan_limit = 1

    def __init__(self, footage, config, model):
        super().__init__(footage,config)
        self.directory = footage.directory.parent/'trains'
        self.directory.mkdir(exist_ok=True)
        self.model = model
        self.detector = None
        digest = hashlib.sha256()
        if model.is_file():
            with model.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024*1024),b''):
                    digest.update(chunk)
        manifest = model.with_suffix('.json')
        if manifest.is_file():
            digest.update(manifest.read_bytes())
        self.model_digest = digest.hexdigest()

    def signature(self, segment):
        settings = self.config.get()
        digest = hashlib.sha256(json.dumps([VERSION,self.model_digest,settings.source,
            settings.detection_mask,settings.confidence,settings.confirmations,
            settings.pre_roll,settings.post_roll]).encode()).hexdigest()
        return f"{segment['bytes']}:{digest}"

    def ordered_segments(self):
        return list(reversed(self.footage._segments()))

    def analyse_segment(self, segment):
        if self.detector is None:
            self.detector = TrainDetector(self.model)
        return analyse_trains(self.footage.find(segment['id']),self.detector,self.config.get(),self.stop)
