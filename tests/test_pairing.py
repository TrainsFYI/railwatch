import json
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.pairing import Pairing, private_write


def test_profile_is_cached_allowlisted_and_logout_keeps_recordings(tmp_path):
    app=create_app(tmp_path,workers=False)
    private_write(tmp_path/'device-credential.json',{'credential':'private-token','camera':{'id':'one','name':'Camera'}})
    app.state.store.create('saved',100,.8,{})
    response=httpx.Response(200,json={'account':{'display_name':'Alice','email':'alice@example.com','secret':'hidden'}},request=httpx.Request('GET','https://example.com'))
    client=TestClient(app)
    with patch('railwatcher.pairing.time.monotonic',return_value=1), patch('railwatcher.pairing.httpx.Client') as remote:
        remote.return_value.__enter__.return_value.get.return_value=response
        data=client.get('/api/device').json()
        assert data['connected'] is True
        assert data['account']=={'display_name':'Alice','email':'alice@example.com'}
        assert 'private-token' not in json.dumps(data)
        assert 'hidden' not in json.dumps(data)
        client.get('/api/device')
        assert remote.return_value.__enter__.return_value.get.call_count==1
    assert client.post('/api/device/logout',headers={'X-Railwatcher':'1'}).json()['connected'] is False
    assert not (tmp_path/'device-credential.json').exists()
    assert app.state.store.get('saved') is not None


def test_profile_failure_preserves_connected_account(tmp_path):
    pairing=Pairing(tmp_path)
    private_write(pairing.path,{'credential':'secret','camera':{'name':'Camera'},'account':{'display_name':'Alice'}})
    with patch('railwatcher.pairing.httpx.Client',side_effect=httpx.ConnectError('offline')):
        pairing.refresh_account()
    assert pairing.public()['account']['display_name']=='Alice'
    assert pairing.public()['connected'] is True


def test_logout_during_pairing_exchange_cannot_restore_credentials(tmp_path):
    pairing=Pairing(tmp_path)
    private_write(pairing.session_path,{'session_id':'one','pairing_secret':'private'})
    class Immediate:
        def wait(self,seconds):return False
    pairing.stop=Immediate()
    response=httpx.Response(200,json={'credential':'secret','camera':{'name':'Camera'}},request=httpx.Request('POST','https://example.com'))
    def exchange(*args,**kwargs):
        pairing.logout()
        return response
    with patch('railwatcher.pairing.httpx.Client') as remote:
        remote.return_value.__enter__.return_value.post.side_effect=exchange
        pairing.poll()
    assert not pairing.path.exists()
    assert pairing.public()['connected'] is False
