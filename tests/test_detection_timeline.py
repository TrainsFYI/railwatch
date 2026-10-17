import json

from fastapi.testclient import TestClient

from railwatcher.app import create_app


def test_timeline_uses_stored_passage_and_clips_to_each_recording(tmp_path):
    app = create_app(tmp_path, workers=False)
    store = app.state.store
    for identity, start, end in [('first', 100, 150), ('second', 145, 200), ('outside', 300, 350)]:
        store.create(identity, start, .9, {})
        store.update(identity, ended=end, status='uploaded')
    passage = {'origin': 'confirmed_live', 'parts': ['first', 'second', 'outside'],
               'detected_started': 120, 'detected_ended': 180}
    with store.connect() as db:
        db.execute("INSERT INTO passages(id,metadata,status) VALUES(?,?,'uploaded')", ('passage', json.dumps(passage)))
    client = TestClient(app)
    assert client.get('/api/sightings/first/timeline').json() == {
        'capture_seconds': 50, 'windows': [{'start': 20, 'end': 50}], 'lead_in_seconds': 3,
    }
    assert client.get('/api/sightings/second/timeline').json()['windows'] == [{'start': 0, 'end': 35}]
    assert client.get('/api/sightings/outside/timeline').json()['windows'] == []
    assert client.get('/api/sightings/missing/timeline').status_code == 404


def test_legacy_and_manually_marked_recordings_do_not_invent_detection_times(tmp_path):
    app = create_app(tmp_path, workers=False)
    store = app.state.store
    store.create('legacy', 100, .9, {})
    store.update('legacy', ended=150, status='uploaded')
    client = TestClient(app)
    assert client.get('/api/sightings/legacy/timeline').json()['windows'] == []
    passage = {'origin': 'owner_submitted_archive', 'parts': ['legacy'],
               'detected_started': 100, 'detected_ended': 150}
    with store.connect() as db:
        db.execute("INSERT INTO passages(id,metadata,status) VALUES(?,?,'uploaded')", ('manual', json.dumps(passage)))
    assert client.get('/api/sightings/legacy/timeline').json()['windows'] == []
