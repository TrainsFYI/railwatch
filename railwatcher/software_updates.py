"""Daily release discovery; installation is delegated to a narrow system service."""
import json
import os
import tempfile
import threading
import time
from importlib.metadata import version
from pathlib import Path

from .releases import API, RELEASES_URL, fetch_json, newest_release, version_key

INTERVAL = 24 * 60 * 60
RUNTIME = Path('/run/railwatcher-updates')


class SoftwareUpdates:
    def __init__(self, directory, runtime=RUNTIME, current=None):
        self.path = Path(directory) / 'software-updates.json'
        self.runtime = runtime
        self.current = current or version('railwatcher')
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        try:
            self.state = json.loads(self.path.read_text())
            if not isinstance(self.state, dict):
                self.state = {}
        except (OSError, ValueError):
            self.state = {}

    def public(self):
        with self.lock:
            state = dict(self.state)
        latest = state.get('latest_version')
        try:
            available = bool(latest and version_key(latest) > version_key(self.current))
        except ValueError:
            available = False
        try:
            operation = json.loads((self.runtime / 'status.json').read_text())
        except (OSError, ValueError):
            operation = {}
        # An old completion/failure must not mask a later release or a queued request.
        if operation.get('version') != latest:
            operation = {}
        if (self.runtime / 'inbox/request.json').exists():
            operation = {'status': 'queued', 'version': latest}
        return {**state, 'current_version': self.current, 'update_available': available,
                'one_click': (self.runtime / 'enabled').is_file(), 'operation': operation,
                'releases_url': RELEASES_URL, 'channel': 'prerelease'}

    def check(self, force=False):
        now = time.time()
        with self.lock:
            if not force and now - self.state.get('checked_at', 0) < INTERVAL:
                return
        # Network I/O must not hold the status lock or delay the dashboard.
        try:
            release = newest_release(fetch_json(API + '?per_page=100'))
            result = {'latest_version': release['tag_name'] if release else None,
                      'release_url': f'{RELEASES_URL}/tag/{release["tag_name"]}' if release else RELEASES_URL,
                      'prerelease': bool(release and release.get('prerelease')), 'error': None}
        except (OSError, ValueError, TypeError, KeyError):
            result = {'error': 'Could not check GitHub. We will retry automatically.'}
        with self.lock:
            self.state.update(result, checked_at=now)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.state))
            temporary.replace(self.path)

    def request_install(self):
        state = self.public()
        if not state['one_click']:
            raise ValueError('One-click updates require the Raspberry Pi installer. See the release instructions.')
        if not state['update_available']:
            raise ValueError('No newer release is available')
        if state['operation'].get('status') in {'queued', 'installing', 'restarting'}:
            raise ValueError('An update is already in progress')
        request = self.runtime / 'inbox/request.json'
        # Publish the complete request atomically: systemd may react immediately.
        with tempfile.NamedTemporaryFile(mode='w', dir=request.parent, prefix='.request-', delete=False) as output:
            temporary = Path(output.name)
            json.dump({'version': state['latest_version']}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, request)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.check()
                except OSError:
                    pass
                self.stop.wait(60)
        self.thread = threading.Thread(target=loop, daemon=True, name='software-updates')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=25)
