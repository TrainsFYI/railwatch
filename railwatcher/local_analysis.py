"""Bounded, device-only event analysis. Estimates never replace human labels."""
import hashlib
import json
import logging
import math
import threading
from pathlib import Path

import cv2
import numpy as np

VERSION = 'device-analysis-v1'
log = logging.getLogger(__name__)


def unknown(reason):
    return {'value': None, 'reason': reason}


def classification(probabilities):
    if len(probabilities) < 3:
        return unknown('insufficient_train_samples')
    scores = np.asarray(probabilities)
    if scores.ndim != 2 or scores.shape[1] != 2 or not np.isfinite(scores).all():
        return unknown('invalid_classifier_output')
    median = np.median(scores, axis=0)
    index = int(np.argmax(median))
    agreement = float(np.mean(scores.argmax(axis=1) == index))
    if median[index] < .9 or agreement < .8:
        return unknown('classifier_uncertain')
    return {'value': ['freight', 'passenger'][index], 'confidence': float(median[index]), 'agreement': agreement}


class TypeClassifier:
    """Optional ONNX artifact, installed outside the public source tree."""
    def __init__(self, path, context):
        self.metadata = json.loads(path.with_suffix('.json').read_text())
        self.digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.metadata.get('sha256') != self.digest or self.metadata.get('labels') != ['freight', 'passenger']:
            raise ValueError('Classifier identity or labels do not match')
        if not context.get('source_sha256') or self.metadata.get('source_sha256') != context['source_sha256']:
            raise ValueError('Classifier has not been scoped to this camera source')
        size = self.metadata.get('input_size', 224)
        size = [size,size] if type(size) is int else size
        if not isinstance(size,list) or len(size)!=2 or any(type(v) is not int or not 32<=v<=1536 for v in size) or size[0]*size[1]>524288:
            raise ValueError('Invalid classifier input size')
        self.size = tuple(size)
        crop = self.metadata.get('crop')
        if crop is not None and (not isinstance(crop,list) or len(crop)!=4 or not all(isinstance(v,(int,float)) and np.isfinite(v) and 0<=v<=1 for v in crop) or crop[0]>=crop[2] or crop[1]>=crop[3]):
            raise ValueError('Invalid classifier crop')
        luminance = self.metadata.get('minimum_context_luminance')
        if not isinstance(luminance, (int, float)) or not 0 <= luminance <= 255:
            raise ValueError('Classifier illumination scope is required')
        self.net = cv2.dnn.readNetFromONNX(str(path))

    def predict(self, frame, box):
        luminance = np.median(cv2.cvtColor(frame[:max(1, frame.shape[0]//3)], cv2.COLOR_BGR2GRAY))
        if luminance < self.metadata['minimum_context_luminance']:
            return None
        h, w = frame.shape[:2]
        left, top, right, bottom = np.clip(self.metadata.get('crop',box), 0, 1)
        crop = frame[int(top*h):int(bottom*h), int(left*w):int(right*w)]
        if not crop.size:
            return None
        image = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), self.size).astype(np.float32)/255
        tensor = ((image-np.array([.485,.456,.406], np.float32))/np.array([.229,.224,.225], np.float32)).transpose(2,0,1)[None]
        self.net.setInput(tensor)
        logits = self.net.forward().reshape(-1)
        if len(logits) != 2 or not np.isfinite(logits).all():
            raise ValueError('Invalid classifier output')
        scores = np.exp(logits-logits.max())
        return scores/scores.sum()


def rail_geometry(calibration, width, height):
    points = (calibration or {}).get('points', [])
    if len(points) < 2:
        return None
    a, b = points[:2]
    vector = (np.array(b['image'])-a['image'])*[width, height]
    length = float(np.linalg.norm(vector))
    lat1, lat2 = math.radians(a['latitude']), math.radians(b['latitude'])
    delta = math.radians(b['longitude']-a['longitude'])
    arc = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(delta/2)**2
    metres = 6371000*2*math.asin(min(1, math.sqrt(arc)))
    bearing = math.degrees(math.atan2(math.sin(delta)*math.cos(lat2), math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta))) % 360
    if length < 20 or not 5 <= metres <= 2000:
        return None
    return vector/length, metres/length, bearing


def motion_estimate(vectors, calibration, shape, speed_enabled=False):
    result = {'direction': unknown('insufficient_train_motion'), 'speed': unknown('insufficient_train_motion')}
    if len(vectors) < 5:
        return result
    values = np.asarray(vectors)
    median = np.median(values, axis=0)
    magnitude = float(np.linalg.norm(median))
    if magnitude < 1:
        return {'direction': unknown('stationary_or_unclear'), 'speed': unknown('stationary_or_unclear')}
    unit = median/magnitude
    agreement = float(np.mean(values@unit > 0))
    if agreement < .8:
        return result
    axis = int(np.argmax(np.abs(median)))
    screen = ('right' if median[0] > 0 else 'left') if axis == 0 else ('down' if median[1] > 0 else 'up')
    result['direction'] = {'value': screen, 'source': 'train_region_optical_flow', 'agreement': agreement}
    geometry = rail_geometry(calibration, shape[1], shape[0])
    if geometry is None:
        result['speed'] = unknown('rail_landmarks_required')
        return result
    rail_axis, scale, bearing = geometry
    if abs(float(unit@rail_axis)) < .8:
        result['speed'] = unknown('motion_does_not_follow_rail')
        return result
    projected = values@rail_axis
    signed = float(np.median(projected))
    result['direction']['bearing_degrees'] = (bearing+(180 if signed < 0 else 0)) % 360
    if not speed_enabled:
        result['speed'] = unknown('approximate_speed_not_enabled')
        return result
    spread = float(np.median(np.abs(projected-signed))) / max(1,abs(signed))
    speed = abs(signed)*scale*3.6
    if spread > .35 or not 1 <= speed <= 200:
        result['speed'] = unknown('inconsistent_speed_estimate')
        return result
    result['speed'] = {'value': round(speed,1), 'unit':'km/h', 'approximate':True,
                       'method':'two_landmark_affine_optical_flow', 'calibration_revision':calibration['revision']}
    return result


def flow_vector(before, after, box, elapsed, polygon=()):
    """Train-region features with forward/backward and background-motion checks."""
    h, w = before.shape
    mask = np.zeros_like(before)
    left, top, right, bottom = np.array(box)*[w,h,w,h]
    cv2.rectangle(mask, (int(left), int(top)), (int(right), int(bottom)), 255, -1)
    if polygon:
        rail_mask=np.zeros_like(before)
        cv2.fillPoly(rail_mask,[(np.asarray(polygon)*[w,h]).astype(np.int32)],255)
        mask=cv2.bitwise_and(mask,rail_mask)
    points = cv2.goodFeaturesToTrack(before, 150, .02, 5, mask=mask)
    background = cv2.goodFeaturesToTrack(before, 100, .02, 8, mask=255-mask)
    if points is None or len(points) < 12 or background is None or len(background) < 12:
        return None
    def track(seeds):
        moved, valid, _ = cv2.calcOpticalFlowPyrLK(before, after, seeds, None)
        if moved is None:
            return np.empty((0,2))
        returned, reverse_valid, _ = cv2.calcOpticalFlowPyrLK(after, before, moved, None)
        if returned is None:
            return np.empty((0,2))
        keep = valid.ravel().astype(bool)&reverse_valid.ravel().astype(bool)&(np.linalg.norm(returned-seeds,axis=2).ravel()<1)
        return (moved-seeds).reshape(-1,2)[keep]
    train, bg = track(points), track(background)
    if len(train) < 12 or len(bg) < 12:
        return None
    shift = np.median(bg,axis=0)
    if np.linalg.norm(shift) > 2:
        return None
    velocities = (train-shift)/elapsed
    center = np.median(velocities,axis=0)
    if np.linalg.norm(center) < 1 or np.mean(velocities@center > 0) < .7:
        return None
    return center.tolist()


def analyse_clip(path, row, observations, context, classifier_path, stop):
    result = {'version':VERSION, 'train_type':unknown('classifier_not_installed'),
              'direction':unknown('insufficient_train_motion'), 'speed':unknown('insufficient_train_motion')}
    capture = cv2.VideoCapture(str(path))
    duration = capture.get(cv2.CAP_PROP_FRAME_COUNT)/max(1,capture.get(cv2.CAP_PROP_FPS))
    if not capture.isOpened() or duration <= 0:
        capture.release()
        raise ValueError('Recording is unavailable')
    classifier = None
    if classifier_path.is_file():
        try:
            classifier = TypeClassifier(classifier_path, context)
        except (ValueError, OSError, cv2.error):
            result['train_type'] = unknown('classifier_incompatible_with_camera')
    wall_duration = row['ended']-row['started']
    samples = sorted([item for item in observations if row['started'] <= item['stamp'] <= row['ended'] and item.get('confidence',0) >= .55], key=lambda item:item['stamp'])
    # At most 48 samples per clip, in a separate background thread.
    if len(samples)>48:
        samples=[samples[index] for index in np.linspace(0,len(samples)-1,48).astype(int)]
    scores, velocities, previous, last = [], [], None, None
    # Multiple simultaneous train boxes need separate tracking before assigning a type or direction.
    stamps=[item['stamp'] for item in samples]
    if len(stamps)!=len(set(stamps)):
        capture.release()
        return {**result,'train_type':unknown('multiple_trains_unresolved'),'direction':unknown('multiple_trains_unresolved'),'speed':unknown('multiple_trains_unresolved')}
    shape = (360,640)
    try:
        for item in samples:
            if stop.is_set():
                raise InterruptedError()
            seconds = (item['stamp']-row['started'])*duration/wall_duration
            if last is not None and seconds-last < .4:
                continue
            capture.set(cv2.CAP_PROP_POS_MSEC,seconds*1000)
            ok, frame = capture.read()
            if not ok:
                continue
            box = item['bbox']
            if classifier is not None:
                value = classifier.predict(frame,box)
                if value is not None:
                    scores.append(value)
            gray = cv2.cvtColor(cv2.resize(frame,(640,360)),cv2.COLOR_BGR2GRAY)
            if previous is not None and .4 <= seconds-last <= 2:
                vector = flow_vector(previous,gray,box,seconds-last,context.get('detection_mask',[]))
                if vector is not None:
                    velocities.append(vector)
            previous, last = gray, seconds
        if classifier is not None and len(samples)>=3 and samples[-1]['stamp']-samples[0]['stamp']>=2:
            result['train_type'] = {**classification(scores), 'model_sha256':classifier.digest}
        result.update(motion_estimate(velocities,context.get('railway_calibration'),shape,context.get('approximate_speed_enabled',False)))
        if not .95 <= duration/wall_duration <= 1.05:
            result['speed']=unknown('recording_timebase_mismatch')
        result['samples'] = len(samples)
        return result
    finally:
        capture.release()


class LocalAnalysis:
    def __init__(self, store, passages, classifier_path):
        self.store, self.passages, self.classifier_path = store, passages, Path(classifier_path)
        self.stop = threading.Event()
        self.thread = None
        self.model_provider = None
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS local_analysis (event_id TEXT PRIMARY KEY, signature TEXT NOT NULL, result TEXT NOT NULL)')

    def results(self):
        with self.store.connect() as db:
            return {row['event_id']:json.loads(row['result']) for row in db.execute('SELECT event_id,result FROM local_analysis')}

    def start(self):
        self.thread = threading.Thread(target=self.run, name='local-event-analysis', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=30)

    def run(self):
        while not self.stop.is_set():
            try:
                self.scan()
            except Exception:
                log.exception('Local event analysis could not finish')
            self.stop.wait(10)

    def scan(self):
        if self.model_provider:
            self.classifier_path = self.model_provider()
        manifest = self.classifier_path.with_suffix('.json')
        model_signature = [(str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in [self.classifier_path,manifest] if p.exists()]
        for row in self.store.list(100):
            if self.stop.is_set():
                break
            if not row['ended'] or row['ended'] <= row['started'] or not row['filename'] or row['status']=='recording':
                continue
            path = self.store.clips/row['filename']
            if not path.is_file():
                continue
            context = json.loads(row['metadata']).get('analysis_context',{})
            observations = []
            with self.store.connect() as db:
                passages = db.execute('SELECT metadata FROM passages').fetchall()
                for passage in passages:
                    data = json.loads(passage['metadata'])
                    if row['id'] in data.get('parts',[]):
                        observations.extend(data.get('observations',[]))
                signature = hashlib.sha256(json.dumps([VERSION,row['ended'],context,observations,model_signature],sort_keys=True).encode()).hexdigest()
                existing = db.execute('SELECT signature FROM local_analysis WHERE event_id=?',(row['id'],)).fetchone()
            if existing and existing['signature']==signature:
                continue
            try:
                result = analyse_clip(path,row,observations,context,self.classifier_path,self.stop)
            except InterruptedError:
                break
            except (ValueError,OSError,cv2.error):
                result = {'version':VERSION,'train_type':unknown('analysis_unavailable'), 'direction':unknown('analysis_unavailable'),'speed':unknown('analysis_unavailable')}
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO local_analysis VALUES(?,?,?)',(row['id'],signature,json.dumps(result)))
            break  # One bounded recording per scan; capture always has priority.
