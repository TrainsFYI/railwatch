"""Durable direct-to-GCS multipart delivery; no internal shared keys."""
import json
import time
from urllib.parse import urlparse

import httpx

from .pairing import API, private_write

CHUNK = 8 * 1024**2


def storage_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.username or parsed.password or not (
        parsed.hostname == 'storage.googleapis.com' or (parsed.hostname or '').endswith('.storage.googleapis.com')
    ):
        raise ValueError('Unexpected storage session host')
    return value


def deliver(row, passages, pairing, client):
    credential = pairing.credential()
    headers = {'Authorization': 'Bearer ' + credential['credential']}
    state_path = passages.store.directory / ('upload-' + row['id'] + '.json')
    contract = passages.contract(row, credential['camera']['id'])
    state = json.loads(state_path.read_text()) if state_path.exists() else {'sessions': {}}
    if state.get('camera_id') != credential['camera']['id']:
        state = {'camera_id': credential['camera']['id'], 'sessions': {}}
    response = client.post(API + 'uploads/', headers=headers, json=contract)
    if response.status_code == 429:
        raise ValueError('Daily upload quota reached; pending media retained')
    if response.status_code == 401:
        raise ValueError('Device access revoked; pending media retained')
    response.raise_for_status()
    result = response.json()
    for key, upload in result.get('uploads', {}).items():
        if upload['uploaded']:
            continue
        index = int(key.removeprefix('part_'))
        path = passages.store.clips / row['parts'][index]['filename']
        size = path.stat().st_size
        session = state['sessions'].get(key) or storage_url(upload['upload_url'])
        state['sessions'][key] = session
        private_write(state_path, state)
        # Recover acknowledged bytes after a crash or interrupted PUT.
        probe = client.put(session, headers={'Content-Range': f'bytes */{size}', 'Content-Length': '0'}, content=b'')
        if probe.status_code in {404, 410}:
            session = storage_url(upload['upload_url'])
            state['sessions'][key] = session
            private_write(state_path, state)
            offset = 0
        elif probe.status_code in {200, 201}:
            continue
        elif probe.status_code == 308:
            offset = int(probe.headers.get('Range', 'bytes=0--1').split('-')[-1]) + 1 if 'Range' in probe.headers else 0
        else:
            probe.raise_for_status()
            raise ValueError('Storage probe returned unexpected response')
        with path.open('rb') as handle:
            handle.seek(offset)
            while offset < size:
                chunk = handle.read(CHUNK)
                end = offset + len(chunk) - 1
                sent = client.put(session, headers={'Content-Type': 'video/mp4', 'Content-Range': f'bytes {offset}-{end}/{size}'}, content=chunk)
                if sent.status_code not in {200, 201, 308}:
                    sent.raise_for_status()
                    raise ValueError('Storage returned unexpected response')
                if end + 1 < size and sent.status_code != 308:
                    raise ValueError('Storage finalized before all bytes were sent')
                offset = end + 1
    final = client.post(API + f"events/{row['id']}/finalize/", headers=headers, json={})
    final.raise_for_status()
    if final.json().get('status') != 'accepted':
        raise ValueError('Media finalization not acknowledged')
    passages.update(row['id'], status='uploaded', error=None)
    for part in row['parts']:
        passages.store.update(part['id'], status='uploaded', remote_id=result['event_id'], error=None)
    state_path.unlink(missing_ok=True)


def attempt(row, passages, pairing, client):
    passages.update(row['id'], status='uploading')
    try:
        deliver(row, passages, pairing, client)
        return 'connected'
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
        reason = str(error) if isinstance(error, ValueError) and str(error).startswith(('Daily ', 'Device ')) else 'Upload interrupted; pending media retained'
        attempts = row['attempts'] + 1
        passages.update(row['id'], status='pending', attempts=attempts,
                        next_attempt=time.time() + min(3600, 15 * 2**min(attempts, 8)), error=reason)
        return reason
