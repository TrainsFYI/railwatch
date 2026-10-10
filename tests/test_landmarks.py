import json

import pytest
from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.config import ConfigStore


def test_landmarks_persist_and_preserve_existing_settings(tmp_path):
    app = create_app(tmp_path, workers=False)
    headers = {"X-Railwatcher": "1"}
    landmark = {"id": "crossing-left", "label": " Left crossing light ", "box": [.1, .2, .15, .3]}
    with TestClient(app) as client:
        assert client.get('/api/config').json()['landmarks'] == []
        assert client.patch('/api/config', headers=headers, json={'landmarks': [landmark]}).status_code == 200
        saved = client.get('/api/config').json()
        assert saved['landmarks'][0]['label'] == 'Left crossing light'
        assert saved['detection_mask'] == []
        assert json.loads(json.dumps(ConfigStore(tmp_path).public()['landmarks'])) == saved['landmarks']
        landmark['label'] = 'Gate light'
        landmark['box'] = [.3, .4, .5, .6]
        assert client.patch('/api/config', headers=headers, json={'landmarks': [landmark]}).status_code == 200
        assert client.get('/api/config').json()['landmarks'][0]['box'] == landmark['box']
        assert client.patch('/api/config', headers=headers, json={'landmarks': []}).status_code == 200
        assert ConfigStore(tmp_path).public()['landmarks'] == []


@pytest.mark.parametrize('change', [
    {'box': [-.1, 0, .5, .5]}, {'box': [0, 0, 1.1, .5]},
    {'box': [.5, .2, .1, .4]}, {'box': [.1, .2, .1, .4]},
    {'box': [.1, .2, .3]}, {'label': '   '}, {'label': 'x'*81}, {'id': '<script>'},
])
def test_invalid_landmark_does_not_replace_saved_settings(tmp_path, change):
    config = ConfigStore(tmp_path)
    landmark = {'id': 'lamp', 'label': 'Crossing lamp', 'box': [.1, .2, .3, .4]}
    config.update({'landmarks': [landmark]})
    with pytest.raises(ValueError):
        config.update({'landmarks': [{**landmark, **change}]})
    assert json.loads(json.dumps(ConfigStore(tmp_path).public()['landmarks'])) == [landmark]


def test_landmark_ids_are_unique_and_count_bounded(tmp_path):
    config = ConfigStore(tmp_path)
    landmark = {'id': 'lamp', 'label': 'Lamp', 'box': [0, 0, 1, 1]}
    with pytest.raises(ValueError):
        config.update({'landmarks': [landmark, landmark]})
    with pytest.raises(ValueError):
        config.update({'landmarks': [{**landmark, 'id': str(i)} for i in range(33)]})
