import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.clips = directory / "clips"
        self.clips.mkdir(exist_ok=True)
        self.database = directory / "sightings.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sightings (
                id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL, confidence REAL NOT NULL,
                status TEXT NOT NULL, filename TEXT, metadata TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                error TEXT, remote_id TEXT, bytes INTEGER NOT NULL DEFAULT 0)""")
            for row in db.execute("SELECT id FROM sightings WHERE status='recording'").fetchall():
                final = self.clips / f"{row['id']}.mp4"
                if final.is_file():
                    db.execute(
                        "UPDATE sightings SET status='pending',filename=?,bytes=?,ended=? WHERE id=?",
                        (final.name, final.stat().st_size, final.stat().st_mtime, row["id"]),
                    )
                else:
                    db.execute(
                        "UPDATE sightings SET status='failed',error='Recording interrupted by restart' WHERE id=?",
                        (row["id"],),
                    )
            db.execute("UPDATE sightings SET status='pending' WHERE status='uploading'")
        self.database.chmod(0o600)
        for path in self.clips.glob("*.partial.mp4"):
            path.unlink()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, event_id, started, confidence, metadata):
        with self.connect() as db:
            db.execute(
                "INSERT INTO sightings(id,started,confidence,status,metadata) VALUES(?,?,?,'recording',?)",
                (event_id, started, confidence, json.dumps(metadata)),
            )

    def update(self, event_id, **values):
        allowed = {
            "ended",
            "confidence",
            "status",
            "filename",
            "attempts",
            "next_attempt",
            "error",
            "remote_id",
            "bytes",
        }
        if not values or set(values) - allowed:
            raise ValueError("Invalid sighting update")
        with self.connect() as db:
            db.execute(
                f"UPDATE sightings SET {','.join(k + '=?' for k in values)} WHERE id=?",
                (*values.values(), event_id),
            )

    def list(self, limit=100, offset=0):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM sightings ORDER BY started DESC LIMIT ? OFFSET ?", (limit, offset)
                )
            ]

    def get(self, event_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sightings WHERE id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def pending(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sightings WHERE status='pending' AND next_attempt<=? ORDER BY started LIMIT 1",
                (time.time(),),
            ).fetchone()
            return dict(row) if row else None

    def summary(self):
        with self.connect() as db:
            rows = db.execute("SELECT status,COUNT(*) AS n FROM sightings GROUP BY status")
            counts = {row["status"]: row["n"] for row in rows}
            total_bytes = db.execute("SELECT COALESCE(SUM(bytes),0) FROM sightings").fetchone()[0]
        return {"total": sum(counts.values()), "counts": counts, "bytes": total_bytes}

    def make_room(self, limit_bytes: int, reserve_bytes: int = 0) -> bool:
        """Only evict uploaded clips. Offline/unsubmitted sightings are never silently lost."""
        with self.connect() as db:
            used = db.execute("SELECT COALESCE(SUM(bytes),0) FROM sightings").fetchone()[0]
            rows = db.execute(
                "SELECT id,filename,bytes FROM sightings WHERE status='uploaded' AND filename IS NOT NULL ORDER BY started"
            ).fetchall()
            for row in rows:
                if used + reserve_bytes <= limit_bytes:
                    break
                (self.clips / row["filename"]).unlink(missing_ok=True)
                db.execute("UPDATE sightings SET filename=NULL,bytes=0 WHERE id=?", (row["id"],))
                used -= row["bytes"]
        return used + reserve_bytes <= limit_bytes
