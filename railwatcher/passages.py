"""Persistent passage identity independent of recording file boundaries."""
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from itertools import pairwise


def utc(stamp):
    return datetime.fromtimestamp(stamp, UTC).isoformat()


class Passages:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS passages (id TEXT PRIMARY KEY, metadata TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, error TEXT)')
            rows = db.execute("SELECT * FROM passages WHERE status='recording'").fetchall()
            for row in rows:
                metadata = json.loads(row['metadata'])
                metadata.update(complete=False, incomplete_reason='service_restart')
                db.execute("UPDATE passages SET metadata=?,status='pending' WHERE id=?", (json.dumps(metadata), row['id']))
            db.execute("UPDATE passages SET status='pending' WHERE status='uploading'")
        self.active = None

    def context(self, settings, model_version, source='capture_snapshot', model_identity=None):
        if len(model_version) != 64 or any(c not in '0123456789abcdef' for c in model_version):
            return None  # Legacy/imported model identity remains v2.0.
        path = self.store.directory / 'models' / 'train.json'
        manifest = json.loads(path.read_text()) if path.exists() else {}
        revision = hashlib.sha256(json.dumps(settings.model_dump(exclude={"source", "api_key", "upload_url"}), sort_keys=True).encode()).hexdigest()
        return {'detection_polygon': [list(point) for point in settings.detection_mask],
                'configuration_revision': revision,
                'model_identity': model_identity or {'name': manifest.get('architecture', 'device-installed'), 'sha256': model_version,
                                   'train_class_id': manifest.get('train_class_id', 0), 'input_size': manifest.get('input_size', 640)},
                'configuration_source': source, 'parent_event_id': None, 'training_intervals': []}

    def start(self, settings, stamp, model_version, model_identity=None):
        identity = str(uuid.uuid4())
        metadata = {'queued_at': time.time(), 'detected_started': stamp, 'detected_ended': stamp, 'complete': True,
                    'incomplete_reason': None, 'parts': [], 'observations': [],
                    'model_version': model_version,
                    'configuration_revision': hashlib.sha256(json.dumps(settings.model_dump(exclude={"source", "api_key", "upload_url"}), sort_keys=True).encode()).hexdigest(),
                    'calibration_revision': settings.railway_calibration.revision if settings.railway_calibration else None,
                    'origin': 'confirmed_live', 'camera_context': self.context(settings, model_version, model_identity=model_identity)}
        with self.store.connect() as db:
            db.execute("INSERT INTO passages(id,metadata,status) VALUES(?,?,'recording')", (identity, json.dumps(metadata)))
        self.active = identity
        return identity

    def mutate(self, identity, operation):
        with self.store.connect() as db:
            row = db.execute('SELECT metadata FROM passages WHERE id=?', (identity,)).fetchone()
            metadata = json.loads(row['metadata'])
            operation(metadata)
            db.execute('UPDATE passages SET metadata=? WHERE id=?', (json.dumps(metadata), identity))

    def add_part(self, identity, part_id):
        self.mutate(identity, lambda value: value['parts'].append(part_id))

    def observation(self, stamp, score, boxes, positive):
        if not self.active:
            return
        def update(value):
            if positive:
                value['detected_ended'] = stamp
            if len(value['observations']) < 10000:
                for box in boxes[:min(20, 10000 - len(value['observations']))]:
                    value['observations'].append({'stamp': stamp, 'confidence': box['confidence'] if isinstance(box, dict) else score, 'bbox': box['bbox'] if isinstance(box, dict) else box})
        self.mutate(self.active, update)

    def end(self, reason=None):
        if not self.active:
            return
        identity, self.active = self.active, None
        if reason:
            self.mutate(identity, lambda value: value.update(complete=False, incomplete_reason=reason))
        with self.store.connect() as db:
            db.execute("UPDATE passages SET status='pending' WHERE id=?", (identity,))

    def submit_history(self, part_id, settings, model_version, *, incomplete_reason="historical_interval_not_confirmed"):
        part = self.store.get(part_id)
        if not part or not part['filename'] or not part['ended']:
            raise ValueError('Historical footage is unavailable')
        identity = str(uuid.uuid4())
        metadata = {'queued_at': time.time(), 'detected_started': part['started'], 'detected_ended': part['ended'],
                    'complete': False, 'incomplete_reason': incomplete_reason,
                    'parts': [part_id], 'observations': [], 'model_version': model_version,
                    'configuration_revision': hashlib.sha256(json.dumps(settings.model_dump(exclude={"source", "api_key", "upload_url"}), sort_keys=True).encode()).hexdigest(),
                    'origin': 'owner_submitted_archive', 'camera_context': self.context(settings, model_version, 'backfill_current_settings')}
        with self.store.connect() as db:
            existing = db.execute('SELECT id,metadata FROM passages').fetchall()
            for row in existing:
                if part_id in json.loads(row['metadata'])['parts']:
                    return row['id']
            db.execute("INSERT INTO passages(id,metadata,status) VALUES(?,?,'pending')", (identity, json.dumps(metadata)))
        return identity

    def timeline(self, part):
        """Expose captured detection bounds, never inferred from current pre-roll settings."""
        duration = max(0., (part['ended'] or part['started']) - part['started'])
        windows = []
        with self.store.connect() as db:
            passages = db.execute("SELECT metadata FROM passages").fetchall()
        for row in passages:
            passage = json.loads(row['metadata'])
            if part['id'] not in passage.get('parts', []) or passage.get('origin') != 'confirmed_live':
                continue
            start = max(0., passage['detected_started'] - part['started'])
            end = min(duration, passage['detected_ended'] - part['started'])
            if end > start:
                windows.append({'start': start, 'end': end})
        windows.sort(key=lambda window: window['start'])
        return {'capture_seconds': duration, 'windows': windows, 'lead_in_seconds': 3}

    def pending(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM passages WHERE status='pending' AND next_attempt<=? ORDER BY rowid", (time.time(),)).fetchall()
        for row in rows:
            metadata = json.loads(row['metadata'])
            parts = [self.store.get(identity) for identity in metadata['parts']]
            if not parts or any(part and part['status'] == 'recording' for part in parts):
                continue
            if any(not part or not part['filename'] for part in parts):
                parts = [part for part in parts if part and part['filename']]
                if not parts:
                    self.update(row['id'], status='failed', error='Recording unavailable; passage incomplete')
                    continue
                metadata.update(complete=False, incomplete_reason='recording_part_unavailable', parts=[part['id'] for part in parts])
                self.mutate(row['id'], lambda value, saved=metadata: value.update(saved))
            return {**dict(row), 'passage': metadata, 'parts': parts}
        return None

    def retry_for_part(self, part_id):
        with self.store.connect() as db:
            rows = db.execute("SELECT id,metadata FROM passages WHERE status='pending'").fetchall()
            for row in rows:
                if part_id in json.loads(row['metadata'])['parts']:
                    db.execute("UPDATE passages SET next_attempt=0,error=NULL WHERE id=?", (row['id'],))
                    return True
        return False

    def delivery_status(self):
        with self.store.connect() as db:
            row = db.execute("SELECT error FROM passages WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
        return (row['error'] or 'Waiting to retry — pending media retained') if row else 'ready — no confirmed passages waiting'

    def summary(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT status,COUNT(*) AS n FROM passages GROUP BY status').fetchall()
            row = db.execute("SELECT metadata FROM passages WHERE status IN ('pending','recording','uploading') ORDER BY rowid LIMIT 1").fetchone()
            metadata = json.loads(row[0]) if row else {}
            oldest = metadata.get("queued_at", metadata.get("detected_started"))
        return {'counts': {row['status']: row['n'] for row in rows},
                'oldest_pending_age_seconds': max(0, time.time()-oldest) if oldest is not None else None}

    def update(self, identity, **values):
        if set(values) - {'status', 'attempts', 'next_attempt', 'error'}:
            raise ValueError('Invalid passage update')
        with self.store.connect() as db:
            db.execute(f"UPDATE passages SET {','.join(key + '=?' for key in values)} WHERE id=?", (*values.values(), identity))

    def contract(self, row, camera_id):
        parts = row['parts']
        start = parts[0]['started']
        end = parts[-1]['ended']
        passage = row['passage']
        if passage['complete'] and any(right['started'] > left['ended'] for left, right in pairwise(parts)):
            passage.update(complete=False, incomplete_reason='recording_gap')
            self.mutate(row['id'], lambda value: value.update(complete=False, incomplete_reason='recording_gap'))
        videos = []
        for index, part in enumerate(parts):
            path = self.store.clips / part['filename']
            with path.open('rb') as handle:
                sha = hashlib.file_digest(handle, 'sha256').hexdigest()
            videos.append({'index': index, 'started_at': utc(part['started']), 'ended_at': utc(part['ended']),
                           'video': {'uri': 'local:' + path.name, 'sha256': sha, 'size_bytes': path.stat().st_size,
                                     'mime_type': 'video/mp4', 'duration_seconds': part['ended'] - part['started']}})
        result = {'schema_version': '2.0.0', 'event_id': row['id'], 'created_at': utc(end),
                'observed_at': utc(passage['detected_started']),
                'source': {'source_type': 'edge_camera', 'source_id': camera_id, 'camera_id': camera_id},
                'capture': {'kind': 'completed_passage' if passage['complete'] else 'observation',
                            'started_at': utc(start), 'ended_at': utc(end), 'timebase': 'utc', 'frame_timestamps': []},
                'media': {'parts': videos, 'poster_frame': None},
                'camera': {'camera_id': camera_id, 'name': None, 'location': None, 'heading_true_deg': None,
                           'heading_accuracy_deg': None, 'calibration_revision': passage.get('calibration_revision'), 'rail_mapping': None},
                'edge_observations': [{'kind': 'train', 'pts_seconds': item['stamp'] - start, 'bbox': item['bbox'],
                                       'confidence': item['confidence'], 'state': 'unknown'}
                                      for item in passage['observations'] if start <= item['stamp'] <= end],
                'passage': {'detected_started_at': utc(max(start, passage['detected_started'])),
                            'detected_ended_at': utc(min(end, passage['detected_ended'])),
                            **{key: passage[key] for key in ['complete', 'incomplete_reason', 'model_version', 'configuration_revision', 'origin']}}}

        context = passage.get('camera_context')
        if context is not None:
            context = json.loads(json.dumps(context))
            # Match the serialized microsecond UTC timebase exactly.
            capture_start = datetime.fromisoformat(result['capture']['started_at'])
            duration = (datetime.fromisoformat(result['capture']['ended_at']) - capture_start).total_seconds()
            detected_offset = (datetime.fromisoformat(result['passage']['detected_started_at']) - capture_start).total_seconds()
            negative_end = max(0., min(duration, detected_offset))
            context['training_intervals'] = ([{'start_seconds': 0., 'end_seconds': negative_end,
                                               'label': 'unreviewed_pre_detection'}] if negative_end > 0 else [])
            if duration > negative_end:
                context['training_intervals'].append({'start_seconds': negative_end, 'end_seconds': duration,
                                                      'label': 'unreviewed_train_candidate'})
            result.update(schema_version='2.1.0', camera_context=context)
        return result
