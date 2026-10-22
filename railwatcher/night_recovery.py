"""Owner-enabled crossing-signal capture fallback, never a train/publication claim."""
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import cv2

from .flashes import analyse_flashes

log = logging.getLogger(__name__)
QUIET_SECONDS = 90
TAIL_SECONDS = 45
MAX_SIGNAL_SECONDS = 600


def in_night_window(stamp, settings):
    hour = datetime.fromtimestamp(stamp, UTC).astimezone(ZoneInfo(settings.night_review_timezone)).hour
    start, end = settings.night_review_start_hour, settings.night_review_end_hour
    return start <= hour < end if start < end else hour >= start or hour < end


class NightRecovery:
    def __init__(self, footage, config, store, passages, pairing, model_provider):
        self.footage, self.config, self.store = footage, config, store
        self.passages, self.pairing, self.model_provider = passages, pairing, model_provider
        self.path = store.directory / 'night-recovery.json'
        self.stop = threading.Event()
        self.thread = None
        self.error = None
        initial = {'cursor': time.time()-300, 'episodes': []}
        try:
            self.state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.state = initial
        if not isinstance(self.state, dict) or not isinstance(self.state.get('episodes'), list):
            self.state = initial

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.path)

    @staticmethod
    def signature(settings):
        values = [settings.source, settings.night_review_timezone, settings.night_review_start_hour,
                  settings.night_review_end_hour, [item.model_dump() for item in settings.landmarks]]
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def add_windows(self, segment, windows, signature):
        for window in sorted(windows, key=lambda row: row['started']):
            started, ended = segment['started']+window['started'], segment['started']+window['ended']
            if not 0 <= window['started'] < window['ended'] <= segment['ended']-segment['started']+.5:
                raise ValueError('Invalid crossing signal interval')
            waiting = [row for row in self.state['episodes'] if row['state']=='waiting' and row['signature']==signature
                       and started <= row['last_signal']+QUIET_SECONDS
                       and ended-row['first_signal'] <= MAX_SIGNAL_SECONDS]
            if waiting:
                episode = waiting[-1]
                episode['last_signal'] = max(episode['last_signal'], ended)
            else:
                identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f'railwatch-night-v1:{signature}:{started:.3f}'))
                episode = {'id': identity, 'clip_id': identity, 'first_signal': started, 'last_signal': ended,
                           'signature': signature, 'state': 'waiting', 'landmark_ids': []}
                self.state['episodes'].append(episode)
            if window['landmark_id'] not in episode['landmark_ids']:
                episode['landmark_ids'].append(window['landmark_id'])

    def already_covered(self, episode):
        with self.store.connect() as db:
            rows = db.execute("SELECT metadata FROM passages WHERE status IN ('pending','uploading','uploaded')").fetchall()
        for row in rows:
            meta = json.loads(row['metadata'])
            if (meta.get('origin')=='confirmed_live' and meta.get('complete')
                    and meta['detected_started'] <= episode['first_signal']
                    and meta['detected_ended'] >= episode['last_signal']):
                return True
        return False

    def finish_ready(self, segments, settings, signature):
        if not segments:
            return
        available_start, available_end = segments[0]['started'], segments[-1]['ended']
        for episode in self.state['episodes']:
            if episode['state'] not in {'waiting', 'exporting'} or episode['signature'] != signature:
                continue
            # Cursor is the end of the last inspected segment. Wall time alone
            # must not close an episode before later signal windows were read.
            if self.state['cursor'] < episode['last_signal']+QUIET_SECONDS:
                continue
            if episode['state']=='waiting':
                if self.already_covered(episode):
                    episode['state'] = 'covered_by_live_detection'
                    self.save()
                    continue
                episode.update(started=max(available_start, episode['first_signal']-settings.pre_roll),
                               ended=episode['last_signal']+max(TAIL_SECONDS, settings.post_roll))
                if episode['ended'] > available_end:
                    continue
                episode['state'] = 'exporting'
                self.save()
            if episode['started'] < available_start:
                episode.update(state='footage_expired', error='Recording expired before candidate export')
                self.save()
                continue
            evidence = {'method': 'fixed_landmark_red_flash_v1', 'status': 'unconfirmed',
                        'landmark_ids': episode['landmark_ids'], 'first_signal_at': episode['first_signal'],
                        'last_signal_at': episode['last_signal'], 'requires_train_review': True}
            try:
                self.footage.export_mark(self.store, settings, episode['started'], episode['ended'],
                                         event_id=episode['clip_id'], candidate_evidence=evidence)
            except ValueError:
                episode.update(state='held_export_error', error='Recording interval requires manual recovery')
                self.save()
                continue
            identity = self.passages.submit_history(episode['clip_id'], settings, self.model_provider(),
                                                    incomplete_reason='night_crossing_signal_unconfirmed')
            episode.update(state='submitted', passage_id=identity)
            self.save()
            log.info('Night crossing candidate queued for private review: %s', episode['clip_id'])

    def scan_once(self):
        settings = self.config.get()
        if not (settings.enabled and settings.night_recovery_enabled and settings.landmarks
                and self.pairing.credential()):
            return
        signature = self.signature(settings)
        if self.state.get('signature') != signature:
            # A changed view/landmark cannot inherit an old episode's evidence.
            for episode in self.state['episodes']:
                if episode['state'] in {'waiting', 'exporting'}:
                    episode['state'] = 'held_configuration_changed'
            self.state.update(signature=signature, cursor=time.time()-300)
            self.save()
        segments = self.footage._segments()
        for segment in segments:
            if segment['started'] < self.state['cursor'] or segment['ended'] > time.time()-5:
                continue
            if in_night_window(segment['started'], settings):
                windows = analyse_flashes(self.footage.find(segment['id']), settings.landmarks, self.stop)['windows']
                self.add_windows(segment, windows, signature)
            self.state['cursor'] = segment['ended']
            self.save()
            break  # Keep live capture responsive while catching up.
        self.finish_ready(segments, settings, signature)
        self.error = None
        # Bound completed audit history; never prune pending recovery work.
        self.state['episodes'] = [row for row in self.state['episodes'] if row['state'] in {'waiting','exporting'}
                                  or row['last_signal'] > time.time()-7*86400]

    def public(self):
        return {'enabled': self.config.get().night_recovery_enabled, 'last_error': self.error,
                'last_inspected_at': self.state.get('cursor'),
                'pending_candidates': sum(row['state'] in {'waiting','exporting'} for row in self.state['episodes']),
                'submitted_candidates': sum(row['state']=='submitted' for row in self.state['episodes']),
                'held_candidates': sum(row['state'].startswith('held_') or row['state']=='footage_expired'
                                       for row in self.state['episodes'])}

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.scan_once()
                except InterruptedError:
                    return
                except (OSError, ValueError, KeyError, sqlite3.Error, cv2.error):
                    self.error = 'Night candidate recovery could not complete; retained footage will be retried.'
                    log.exception('Night candidate recovery failed')
                self.stop.wait(10)
        self.thread = threading.Thread(target=loop, daemon=True, name='night-recovery')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=35)

