import json

from railwatcher.config import Settings
from railwatcher.passages import Passages
from railwatcher.store import Store


def part(store, identity, started, ended):
    store.create(identity, started, .9, {})
    path = store.clips / (identity + '.mp4')
    path.write_bytes(b'video')
    store.update(identity, ended=ended, filename=path.name, status='pending', bytes=5)


def test_passage_identity_crosses_parts_and_keeps_detected_interval(tmp_path):
    store = Store(tmp_path)
    passages = Passages(store)
    identity = passages.start(Settings(), 12, 'open-model-1')
    part(store, 'first', 0, 300)
    part(store, 'second', 288, 420)
    passages.add_part(identity, 'first')
    passages.add_part(identity, 'second')
    passages.observation(400, .9, [[.1,.2,.8,.9]], True)
    assert passages.pending() is None
    passages.end()
    row = passages.pending()
    contract = passages.contract(row, 'camera-id')
    assert contract['event_id'] == identity
    assert len(contract['media']['parts']) == 2
    assert contract['passage']['complete']
    assert contract['edge_observations'][0]['pts_seconds'] == 400
    assert store.make_room(1) is False
    assert (store.clips / 'first.mp4').exists()


def test_restart_marks_passage_incomplete(tmp_path):
    store = Store(tmp_path)
    passages = Passages(store)
    identity = passages.start(Settings(), 12, 'model')
    part(store, 'first', 0, 30)
    passages.add_part(identity, 'first')
    restarted = Passages(store)
    row = restarted.pending()
    assert row['id'] == identity
    assert row['passage']['complete'] is False
    assert row['passage']['incomplete_reason'] == 'service_restart'
    with store.connect() as db:
        assert 'source' not in json.loads(db.execute('SELECT metadata FROM passages').fetchone()[0])


def test_adjacent_archive_detections_group_across_file_boundary():
    from railwatcher.archive_passages import group_passages
    segments = [{'id': 'a', 'started': 0, 'ended': 120, 'motion': {'windows': [{'started': 100, 'ended': 119}]}},
                {'id': 'b', 'started': 120, 'ended': 240, 'motion': {'windows': [{'started': 1, 'ended': 45}]}}]
    groups = group_passages(segments)
    assert len(groups) == 1
    assert groups[0]['segments'] == ['a', 'b']
    assert groups[0]['detected_ended'] == 165


def test_recording_gap_cannot_claim_complete_passage(tmp_path):
    store = Store(tmp_path)
    passages = Passages(store)
    identity = passages.start(Settings(), 12, 'model')
    part(store, 'first', 0, 300)
    part(store, 'second', 320, 420)
    passages.add_part(identity, 'first')
    passages.add_part(identity, 'second')
    passages.observation(400, .9, [], True)
    passages.end()
    contract = passages.contract(passages.pending(), 'camera-id')
    assert not contract['passage']['complete']
    assert contract['passage']['incomplete_reason'] == 'recording_gap'


def test_quota_warning_remains_visible_and_retry_cannot_close_live_passage(tmp_path):
    store = Store(tmp_path)
    passages = Passages(store)
    identity = passages.start(Settings(), 12, 'model')
    part(store, 'first', 0, 30)
    passages.add_part(identity, 'first')
    assert not passages.retry_for_part('first')
    passages.end()
    passages.update(identity, next_attempt=10**12, error='Daily upload quota reached; pending media retained')
    assert passages.pending() is None
    assert passages.delivery_status().startswith('Daily upload quota')
    assert passages.retry_for_part('first')
    assert passages.pending()['id'] == identity
