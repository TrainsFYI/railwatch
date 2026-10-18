"""Frontend-approved credentials; camera connection secrets stay local."""
import json
import os
import threading
import time
from pathlib import Path

import httpx

API = 'https://api.trains.fyi/api/v5/cameras/'


def private_write(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


class Pairing:
    def __init__(self, directory):
        self.path = Path(directory) / 'device-credential.json'
        self.session_path = Path(directory) / 'device-pairing.json'
        self.lock = threading.Lock()
        self.status = 'not connected'
        self.profile_checked = None
        self.thread = None
        self.stop = threading.Event()

    def credential(self):
        with self.lock:
            return json.loads(self.path.read_text()) if self.path.exists() else None

    def refresh_account(self):
        credential = self.credential()
        if not credential or (self.profile_checked is not None and time.monotonic()-self.profile_checked<300):
            return
        self.profile_checked=time.monotonic()
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                response=client.get(API+'account/',headers={'Authorization':'Bearer '+credential['credential']})
                response.raise_for_status()
                account=response.json()['account']
                profile={key:account.get(key) for key in ('display_name','email') if isinstance(account.get(key),str)}
            with self.lock:
                if self.path.exists() and json.loads(self.path.read_text()).get('credential')==credential['credential']:
                    credential['account']=profile
                    private_write(self.path,credential)
        except (httpx.HTTPError,ValueError,KeyError,OSError):
            pass  # A temporary profile failure must not disconnect the camera.

    def logout(self):
        with self.lock:
            self.path.unlink(missing_ok=True)
            self.session_path.unlink(missing_ok=True)
            self.status='not connected'
            self.profile_checked=None

    def public(self):
        credential = self.credential()
        result = {'connected': credential is not None, 'status': self.status}
        if credential:
            result['camera'] = credential['camera']
            result['account'] = {key:credential.get('account',{}).get(key) for key in ('display_name','email')}
        if self.session_path.exists():
            session = json.loads(self.session_path.read_text())
            result.update({key: session[key] for key in ['code', 'approval_url', 'expires_at']})
        return result

    def begin(self, settings):
        with self.lock:
            if self.thread and self.thread.is_alive() and self.session_path.exists():
                return
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.post(API + 'pairing/', json={'name': settings.name, 'location': {'latitude': settings.latitude, 'longitude': settings.longitude}})
                response.raise_for_status()
                private_write(self.session_path, response.json())
            self.thread = threading.Thread(target=self.poll, daemon=True)
            self.thread.start()

    def poll(self):
        with self.lock:
            if not self.session_path.exists():return
            session = json.loads(self.session_path.read_text())
        self.status = 'waiting for account approval'
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            while not self.stop.wait(5):
                try:
                    response = client.post(API + f"pairing/{session['session_id']}/exchange/", json={'pairing_secret': session['pairing_secret']})
                    if response.status_code in {401, 403, 410}:
                        self.status = 'pairing expired; connect again'
                        return
                    if response.status_code in {202, 429}:
                        continue
                    response.raise_for_status()
                    result = response.json()
                    with self.lock:
                        if not self.session_path.exists() or json.loads(self.session_path.read_text()).get('session_id')!=session['session_id']:
                            return
                        private_write(self.path, {'credential': result['credential'], 'camera': result['camera']})
                        self.session_path.unlink(missing_ok=True)
                    self.status = 'connected'
                    return
                except (httpx.HTTPError, ValueError, OSError):
                    self.status = 'pairing connection unavailable; retrying'

    def start(self):
        if not self.credential() and self.session_path.exists():
            self.thread = threading.Thread(target=self.poll, daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=25)
