from unittest.mock import Mock

import httpx
import pytest

from railwatcher.pairing import private_write
from railwatcher.resumable import deliver, storage_url


def test_private_state_and_storage_host(tmp_path):
    path = tmp_path / 'credential.json'
    private_write(path, {'secret': 'private'})
    assert path.stat().st_mode & 0o777 == 0o600
    for value in ['http://storage.googleapis.com/upload', 'https://evil.example/upload', 'https://storage.googleapis.com.evil.example/upload']:
        with pytest.raises(ValueError):
            storage_url(value)
    assert storage_url('https://storage.googleapis.com/upload')


def test_interruption_resumes_without_device_credentials_on_storage(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'abcde')
    store = Mock(clips=tmp_path, directory=tmp_path)
    passages = Mock(store=store)
    passages.contract.return_value = {'event_id': 'passage-id'}
    pairing = Mock()
    pairing.credential.return_value = {'credential': 'device-secret', 'camera': {'id': 'camera-id'}}
    row = {'id': 'passage-id', 'parts': [{'id': 'part-id', 'filename': 'clip.mp4'}]}
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.host == 'storage.googleapis.com':
            assert 'Authorization' not in request.headers
            if request.headers['Content-Range'].startswith('bytes */'):
                return httpx.Response(308, headers={'Range': 'bytes=0-1'})
            assert request.content == b'cde'
            assert request.headers['Content-Range'] == 'bytes 2-4/5'
            return httpx.Response(200)
        assert request.headers['Authorization'] == 'Bearer device-secret'
        if request.url.path.endswith('/uploads/'):
            return httpx.Response(200, json={'event_id': 'cloud-event', 'status': 'upload_required', 'uploads': {'part_0000': {'uploaded': False, 'upload_url': 'https://storage.googleapis.com/session'}}})
        return httpx.Response(200, json={'status': 'accepted'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        deliver(row, passages, pairing, client)
    passages.update.assert_called_once_with('passage-id', status='uploaded', error=None)
    assert len(calls) == 4
    assert not (tmp_path / 'upload-passage-id.json').exists()
