from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.store import Store


def test_detection_review_threshold_and_manual_events(tmp_path):
    store = Store(tmp_path)
    for event_id, confidence, metadata in [
        ('low', .79, {}), ('high', .8, {}), ('manual', .2, {'manual_review': True}),
    ]:
        store.create(event_id, 100, confidence, metadata)
        store.update(event_id, ended=120, status='uploaded')
    client = TestClient(create_app(tmp_path, workers=False))
    rows = {row['id']: row for row in client.get('/api/sightings').json()}
    assert rows['low']['review_status'] == 'required'
    assert rows['high']['review_status'] == 'not_required'
    assert rows['manual']['review_status'] == 'confirmed'
    assert 'metadata' not in rows['low']


def test_review_persists_without_mutating_capture_or_upload_state(tmp_path):
    store = Store(tmp_path)
    store.create('low', 100, .6, {'capture': 'original'})
    store.update('low', ended=120, status='pending')
    client = TestClient(create_app(tmp_path, workers=False))
    headers = {'X-Railwatcher': '1'}
    for decision in ['confirmed', 'dismissed']:
        response = client.post('/api/sightings/low/review', json={'decision': decision}, headers=headers)
        assert response.status_code == 200
        assert client.get('/api/sightings').json()[0]['review_status'] == decision
    saved = Store(tmp_path).get('low')
    assert saved['status'] == 'pending'
    assert saved['confidence'] == .6
    assert 'original' in saved['metadata']
    assert client.post('/api/sightings/low/review', json={'decision': 'bad'}, headers=headers).status_code == 422
    assert client.post('/api/sightings/low/review', json=[], headers=headers).status_code == 422
    assert client.post('/api/sightings/missing/review', json={'decision': 'confirmed'}, headers=headers).status_code == 404
    assert client.post('/api/sightings/low/review', json={'decision': 'confirmed'}).status_code == 403
