import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from railwatcher.config import Settings
from railwatcher.footage import FootageBuffer


def config(**patch):
    return SimpleNamespace(get=lambda: Settings(**patch))


def test_review_survives_archive_expiry_and_is_playable(tmp_path, monkeypatch):
    now = dt.datetime(2026, 10, 17, 22, tzinfo=dt.UTC).timestamp()
    monkeypatch.setattr('railwatcher.footage.time.time', lambda: now)
    buffer = FootageBuffer(tmp_path/'buffer', config(night_review_enabled=True, night_review_timezone='America/Toronto'))
    name = '20261016T233000.mp4'  # 19:30 local; retained before archive expiry
    source = buffer.directory/name
    source.write_bytes(b'video')
    buffer._prune()
    assert (buffer.review_directory/name).exists()
    now += 3*3600
    buffer._prune()
    assert not source.exists()
    assert buffer.find(name) == buffer.review_directory/name
    assert buffer.list()['segments'][0]['retained_for_review']
    assert buffer.find('../config.json') is None
    now += 72*3600
    buffer._prune()
    assert buffer.find(name) is None


def test_default_off_and_daytime_not_retained(tmp_path, monkeypatch):
    now = dt.datetime(2026, 10, 17, 22, tzinfo=dt.UTC).timestamp()
    monkeypatch.setattr('railwatcher.footage.time.time', lambda: now)
    buffer = FootageBuffer(tmp_path/'buffer', config())
    (buffer.directory/'20261017T003000.mp4').write_bytes(b'x')
    buffer._prune()
    assert not list(buffer.review_directory.glob('*.mp4'))
    buffer.config = config(night_review_enabled=True, night_review_timezone='America/Toronto')
    (buffer.directory/'20261017T153000.mp4').write_bytes(b'x')
    buffer._prune()
    assert not (buffer.review_directory/'20261017T153000.mp4').exists()


def test_budget_removes_oldest_review_without_deleting_pending_media(tmp_path, monkeypatch):
    now = dt.datetime(2026, 10, 17, 22, tzinfo=dt.UTC).timestamp()
    monkeypatch.setattr('railwatcher.footage.time.time', lambda: now)
    buffer = FootageBuffer(tmp_path/'buffer', config(night_review_enabled=True))
    pending=tmp_path/'clips';pending.mkdir();(pending/'pending.mp4').write_bytes(b'pending')
    for name in ['20261017T013000.mp4','20261017T023000.mp4']:
        with (buffer.directory/name).open('wb') as handle:
            handle.truncate(160_000_000)
    buffer.config = config(night_review_enabled=True, night_review_gb=.25)
    buffer._prune()
    assert not (buffer.review_directory/'20261017T013000.mp4').exists()
    assert (buffer.review_directory/'20261017T023000.mp4').exists()
    assert (pending/'pending.mp4').read_bytes() == b'pending'


def test_timezone_validation_and_dst_window():
    with pytest.raises(ValueError):
        Settings(night_review_timezone='not/a/timezone')
    assert dt.datetime(2026, 10, 18, 1, tzinfo=dt.UTC).astimezone(ZoneInfo('America/Toronto')).hour == 21
