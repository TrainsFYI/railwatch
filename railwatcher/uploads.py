import hashlib
import json
import threading
import time

import httpx


class Uploader:
    def __init__(self, config, store, pairing=None, passages=None):
        self.config, self.store = config, store
        self.pairing, self.passages = pairing, passages
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.status = "local only"
        self.last_heartbeat = 0

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=35)

    def attempt(self, row, settings, client):
        self.store.update(row["id"], status="uploading")
        try:
            path = self.store.clips / row["filename"]
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
                handle.seek(0)
                metadata = json.loads(row["metadata"])
                metadata.update(
                    id=row["id"],
                    started_at=row["started"],
                    ended_at=row["ended"],
                    confidence=row["confidence"],
                    sha256=digest,
                )
                response = client.post(
                    settings.upload_url,
                    headers={"Authorization": f"Bearer {settings.api_key}", "Idempotency-Key": row["id"]},
                    data={"metadata": json.dumps(metadata)},
                    files={"video": (path.name, handle, "video/mp4")},
                )
            if response.status_code not in {200, 201, 202}:
                raise ValueError(f"Server returned HTTP {response.status_code}")
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("id"), str) or not body["id"]:
                raise ValueError("Server response must include a sighting id")
            self.store.update(row["id"], status="uploaded", remote_id=body["id"][:256], error=None)
            self.status = "connected"
        except (OSError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            attempts = row["attempts"] + 1
            # Never persist raw exception messages: request URLs and headers may contain secrets.
            reason = (
                str(error)
                if isinstance(error, ValueError) and str(error).startswith("Server ")
                else "Upload failed; check connection and endpoint"
            )
            self.store.update(
                row["id"],
                status="pending",
                attempts=attempts,
                next_attempt=time.time() + min(3600, 15 * 2 ** min(attempts, 8)),
                error=reason,
            )
            self.status = reason

    def run(self):
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
            while not self.stop.is_set():
                settings = self.config.get()
                if self.pairing is not None:
                    if self.pairing.credential():
                        if time.time() - self.last_heartbeat >= 60:
                            from .pairing import API
                            credential = self.pairing.credential()
                            with self.store.connect() as db:
                                queued = db.execute("SELECT COUNT(*) FROM passages WHERE status IN ('pending','recording','uploading')").fetchone()[0]
                            queue_summary = self.passages.summary()
                            queue_age = queue_summary['oldest_pending_age_seconds']
                            try:
                                heartbeat = client.post(API + 'heartbeat/', headers={'Authorization': 'Bearer ' + credential['credential']},
                                                        json={'queue_events': queued, 'oldest_pending_at': time.time() - queue_age if queue_age is not None else None, 'upload_error': self.status if 'retained' in self.status else None})
                                heartbeat.raise_for_status()
                            except httpx.HTTPError:
                                self.status = 'Device heartbeat unavailable'
                            self.last_heartbeat = time.time()
                        row = self.passages.pending()
                        if row:
                            from .resumable import attempt
                            self.status = attempt(row, self.passages, self.pairing, client)
                        else:
                            self.status = self.passages.delivery_status()
                    else:
                        self.status = "local only — authorize this camera with your account"
                elif not settings.upload_url or not settings.api_key:
                    self.status = "local only — TrainsFYI upload API not connected"
                else:
                    row = self.store.pending()
                    if row:
                        self.attempt(row, settings, client)
                    else:
                        self.status = "ready — no pending uploads"
                self.stop.wait(5)
