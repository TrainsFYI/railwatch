import hashlib
from unittest.mock import patch

import httpx
import pytest

from railwatcher.config import ConfigStore
from railwatcher.model_updates import SOURCE, ModelUpdates


def catalog(payload=b'model'):
    return {'schema_version':1,'models':[{'id':'detector','version':'test-v1','sha256':hashlib.sha256(payload).hexdigest(),
            'bytes':len(payload),'url':SOURCE+'test.onnx','manifest':{'architecture':'test'}}]}


def client_for(data, payload=b'model'):
    def respond(request):
        return httpx.Response(200,json=data) if request.url.path.endswith('.json') else httpx.Response(200,content=payload)
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_download_is_checked_and_activation_persists(tmp_path):
    manager=ModelUpdates(tmp_path,tmp_path/'bundled.onnx',ConfigStore(tmp_path))
    with client_for(catalog()) as client, patch('railwatcher.model_updates.TrainDetector'):
        manager.install('detector',client)
    assert manager.path('detector').read_bytes()==b'model'
    restarted=ModelUpdates(tmp_path,tmp_path/'bundled.onnx',ConfigStore(tmp_path))
    assert restarted.path('detector')==manager.path('detector')
    assert restarted.public()['models'][0]['version']=='test-v1'


def test_checksum_failure_preserves_previous_active_model(tmp_path):
    manager=ModelUpdates(tmp_path,tmp_path/'bundled.onnx',ConfigStore(tmp_path))
    with client_for(catalog()) as client, patch('railwatcher.model_updates.TrainDetector'):
        manager.install('detector',client)
    previous=manager.state_path.read_text()
    changed=catalog(b'other');changed['models'][0]['version']='v2'
    with client_for(changed,b'wrong') as client, pytest.raises(ValueError,match='checksum'):
        manager.install('detector',client)
    assert manager.state_path.read_text()==previous
    assert manager.path('detector').read_bytes()==b'model'


def test_camera_specific_and_off_bucket_downloads_rejected(tmp_path):
    manager=ModelUpdates(tmp_path,tmp_path/'bundled.onnx',ConfigStore(tmp_path))
    scoped=catalog();scoped['models'][0]['source_sha256']='another-camera'
    with client_for(scoped) as client,pytest.raises(ValueError,match='compatible'):
        manager.install('detector',client)
    scoped=catalog();scoped['models'][0]['url']='http://127.0.0.1/private'
    with client_for(scoped) as client,pytest.raises(ValueError,match='release bucket'):
        manager.check(client)
    assert not manager.state_path.exists()


def test_models_settings_api_and_builtin_algorithms(tmp_path):
    from fastapi.testclient import TestClient

    from railwatcher.app import create_app

    app=create_app(tmp_path,workers=False)
    client=TestClient(app)
    response=client.get('/api/models').json()
    assert [item['id'] for item in response['models']]==['detector','train_type']
    assert len(response['algorithms'])==2
    assert client.post('/api/models/check').status_code==403
    assert client.post('/api/models/bad/download',headers={'X-Railwatcher':'1'}).status_code==404
