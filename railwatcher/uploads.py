import hashlib
import json
import threading
import time

import httpx


class Uploader:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.status = "local only"

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
                if not settings.upload_url or not settings.api_key:
                    self.status = "local only — TrainsFYI upload API not connected"
                else:
                    row = self.store.pending()
                    if row:
                        self.attempt(row, settings, client)
                    else:
                        self.status = "ready — no pending uploads"
                self.stop.wait(5)
