import math
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Landmark(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=80)
    box: tuple[float, float, float, float]

    @field_validator("label")
    @classmethod
    def validate_label(cls, label):
        label = label.strip()
        if not label:
            raise ValueError("Give each landmark a label")
        return label

    @field_validator("box")
    @classmethod
    def validate_box(cls, box):
        left, top, right, bottom = box
        if not all(0 <= value <= 1 for value in box) or left >= right or top >= bottom:
            raise ValueError("Landmark boxes must have positive size inside the camera image")
        return box


class RailPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    image: tuple[float, float]
    latitude: float = Field(ge=-85, le=85)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("image")
    @classmethod
    def image_bounds(cls, point):
        if any(not 0 <= x <= 1 for x in point):
            raise ValueError("Rail points must be inside the image")
        return point


class RailCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    points: list[RailPoint] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def distinct_points(self):
        for i, point in enumerate(self.points):
            for other in self.points[:i]:
                if math.dist(point.image, other.image) < .03:
                    raise ValueError("Choose image points farther apart")
                distance = math.hypot((point.longitude-other.longitude)*math.cos(math.radians(point.latitude)), point.latitude-other.latitude)*111195
                if distance < 5 or distance > 2000:
                    raise ValueError("Map points must be between 5 and 2000 metres apart")
        return self


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
    landmarks: list[Landmark] = Field(default_factory=list, max_length=32)
    approximate_speed_enabled: bool = False
    railway_calibration: RailCalibration | None = None
    railway_calibration_draft: list[RailPoint] = Field(default_factory=list, max_length=3)
    confirmations: int = Field(default=3, ge=1, le=10)
    pre_roll: int = Field(default=120, ge=3, le=120)
    post_roll: int = Field(default=15, ge=3, le=60)
    max_clip_seconds: int = Field(default=300, ge=30, le=900)
    night_review_enabled: bool = False
    night_review_timezone: str = Field(default="UTC", max_length=80)
    night_review_start_hour: int = Field(default=19, ge=0, le=23)
    night_review_end_hour: int = Field(default=7, ge=0, le=23)
    night_review_hours: int = Field(default=72, ge=24, le=168)
    night_review_gb: float = Field(default=2, ge=.25, le=20)
    storage_gb: float = Field(default=8, ge=1, le=1000)
    api_key: str = Field(default="", max_length=512)
    upload_url: str = Field(default="", max_length=2048)
    enabled: bool = False
    setup_complete: bool = False

    @field_validator("night_review_timezone")
    @classmethod
    def review_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid timezone, for example America/Toronto") from None
        return value

    @field_validator("landmarks")
    @classmethod
    def validate_landmarks(cls, landmarks):
        if len({landmark.id for landmark in landmarks}) != len(landmarks):
            raise ValueError("Landmark identifiers must be unique")
        return landmarks

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
            # A source or pose change invalidates the old image-to-rail mapping.
            pose_fields = ("source", "source_type", "latitude", "longitude", "bearing", "field_of_view")
            if any(key in patch and patch[key] != current[key] for key in pose_fields):
                patch = {**patch, "railway_calibration": None, "railway_calibration_draft": []}
            elif isinstance(patch.get("railway_calibration"), dict):
                patch = {**patch, "railway_calibration": {**patch["railway_calibration"], "revision": uuid4().hex}}
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
