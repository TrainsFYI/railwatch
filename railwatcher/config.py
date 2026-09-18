import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="My railway camera", min_length=1, max_length=80)
    source_type: str = "usb"
    source: str = Field(default="/dev/video0", max_length=2048)
    latitude: float | None = Field(default=None, ge=-85, le=85)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    bearing: float = Field(default=0, ge=0, lt=360)
    field_of_view: float = Field(default=70, ge=10, le=160)
    confidence: float = Field(default=0.55, ge=0.1, le=0.99)
    detection_mask: list[tuple[float, float]] = Field(default_factory=list, max_length=32)
    confirmations: int = Field(default=3, ge=1, le=10)
    pre_roll: int = Field(default=12, ge=3, le=30)
    post_roll: int = Field(default=15, ge=3, le=60)
    max_clip_seconds: int = Field(default=300, ge=30, le=900)
    storage_gb: float = Field(default=8, ge=1, le=1000)
    api_key: str = Field(default="", max_length=512)
    upload_url: str = Field(default="", max_length=2048)
    enabled: bool = False
    setup_complete: bool = False

    @field_validator("detection_mask")
    @classmethod
    def validate_detection_mask(cls, points):
        if points and len(points) < 3:
            raise ValueError("A detection area needs at least three points")
        if any(not 0 <= coordinate <= 1 for point in points for coordinate in point):
            raise ValueError("Detection area points must be inside the camera image")
        return points

    @model_validator(mode="after")
    def validate_settings(self):
        if self.source_type not in {"usb", "rtsp"}:
            raise ValueError("Choose a USB camera or RTSP stream")
        if self.source_type == "usb" and not re.fullmatch(r"/dev/video\d+", self.source):
            raise ValueError("USB cameras must use a /dev/video device")
        if self.source_type == "rtsp":
            url = urlsplit(self.source)
            if (
                url.scheme not in {"rtsp", "rtsps"}
                or not url.hostname
                or any(c.isspace() for c in self.source)
            ):
                raise ValueError("Enter a valid rtsp:// or rtsps:// camera URL")
        if self.upload_url:
            url = urlsplit(self.upload_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
                raise ValueError("Upload endpoint must be an HTTPS URL without credentials or a fragment")
        if self.enabled and (not self.setup_complete or self.latitude is None or self.longitude is None):
            raise ValueError("Set the camera location and complete setup before starting")
        return self


class ConfigStore:
    def __init__(self, directory: Path):
        self.path = directory / "config.json"
        self.lock = threading.RLock()
        self.value = Settings.model_validate_json(self.path.read_text()) if self.path.exists() else Settings()

    def get(self) -> Settings:
        with self.lock:
            return self.value.model_copy(deep=True)

    def update(self, patch: dict) -> Settings:
        with self.lock:
            current = self.value.model_dump()
            # Null means keep the stored secret. An empty API key explicitly clears it.
            for key in ("api_key", "source"):
                if patch.get(key, ...) is None:
                    patch = {k: v for k, v in patch.items() if k != key}
            current.update(patch)
            value = Settings.model_validate(current)
            temporary = self.path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(value.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
            self.value = value
            return value.model_copy(deep=True)

    def public(self) -> dict:
        value = self.get()
        result = value.model_dump(exclude={"api_key", "source"})
        result["has_api_key"] = bool(value.api_key)
        result["source"] = value.source if value.source_type == "usb" else None
        result["source_label"] = (
            value.source if value.source_type == "usb" else urlsplit(value.source).hostname
        )
        return result
