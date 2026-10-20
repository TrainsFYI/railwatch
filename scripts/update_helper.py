#!/usr/bin/env python3
"""Root-owned systemd entry point. Accept a version, never a command or URL."""
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

spec = importlib.util.spec_from_file_location('release_support', '/opt/railwatcher/release_support.py')
release_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_support)
API = release_support.API
download_bundle = release_support.download_bundle
extract_bundle = release_support.extract_bundle
fetch_json = release_support.fetch_json
version_key = release_support.version_key

BASE = Path('/opt/railwatcher')
RUNTIME = Path('/run/railwatcher-updates')


def run(*arguments, timeout=900):
    return subprocess.run(arguments, check=True, timeout=timeout, capture_output=True, text=True).stdout.strip()


def status(state, tag, message):
    temporary = RUNTIME / 'status.tmp'
    temporary.write_text(json.dumps({'status': state, 'version': tag, 'message': message, 'at': time.time()}))
    temporary.chmod(0o644)
    temporary.replace(RUNTIME / 'status.json')


def activate(target):
    link = BASE / 'current.next'
    link.unlink(missing_ok=True)
    link.symlink_to(target, target_is_directory=True)
    link.replace(BASE / 'current')


def healthy(expected):
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8787/api/software', timeout=2) as response:
                if json.load(response).get('current_version') == expected:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return False


def wait_until_idle():
    # Leave an active train recording alone; wait for a quiet capture boundary.
    quiet = 0
    for _ in range(120):
        with urllib.request.urlopen('http://127.0.0.1:8787/api/status', timeout=5) as response:
            recording = json.load(response).get('recording')
        quiet = 0 if recording else quiet + 1
        if quiet >= 2:
            return
        time.sleep(5)
    raise ValueError('Camera stayed busy. Try the update again after the train passes.')


def main():
    request = RUNTIME / 'inbox/request.json'
    tag = ''
    previous = None
    switched = False
    try:
        descriptor = os.open(request, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as incoming:
            body = incoming.read(1025)
        request.unlink()
        if len(body) > 1024:
            raise ValueError('Invalid update request')
        tag = json.loads(body)['version']
        version_key(tag)
        previous = (BASE / 'current').resolve(strict=True)
        current = run(str(previous / 'bin/python'), '-I', '-c',
                      'from importlib.metadata import version; print(version("railwatcher"))', timeout=30)
        if version_key(tag) <= version_key(current):
            raise ValueError('Only newer releases can be installed')
        release = fetch_json(API + '/tags/' + tag)
        if release.get('tag_name') != tag:
            raise ValueError('Release tag did not match')
        status('installing', tag, 'Downloading and verifying the release. Monitoring continues.')
        releases = BASE / 'releases'
        releases.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='railwatch-release-') as temporary:
            archive = Path(temporary) / 'release.tar.gz'
            download_bundle(release, archive)
            source = extract_bundle(archive, Path(temporary) / 'source')
            candidate = Path(tempfile.mkdtemp(prefix=tag + '-', dir=releases)) / 'venv'
            run('/usr/bin/python3', '-m', 'venv', str(candidate))
            run(str(candidate / 'bin/pip'), 'install', '--disable-pip-version-check', str(source))
            installed = run(str(candidate / 'bin/python'), '-I', '-c',
                            'from importlib.metadata import version; print(version("railwatcher"))', timeout=30)
            if version_key(installed) != version_key(tag):
                raise ValueError('Installed package version did not match its release')
            wait_until_idle()
            status('restarting', tag, 'Restarting RailWatch. Monitoring will pause briefly.')
            activate(candidate)
            switched = True
            run('/usr/bin/systemctl', 'restart', 'railwatcher', timeout=150)
            if not healthy(installed):
                raise ValueError('The new version failed its health check')
        status('complete', tag, 'Update installed. RailWatch is running again.')
    except Exception:
        # Never expose pip output, stream credentials or local filesystem details.
        restored = False
        if switched and previous:
            try:
                activate(previous)
                run('/usr/bin/systemctl', 'restart', 'railwatcher', timeout=150)
                restored = healthy(current)
            except (OSError, ValueError, subprocess.SubprocessError):
                restored = False
        request.unlink(missing_ok=True)
        message = ('Update failed; the previous version was restored.' if restored else
                   'Update failed. The previous version is still selected.' if not switched else
                   'Update failed and automatic recovery needs attention. Check the RailWatch service.')
        status('failed', tag, message)
        raise


if __name__ == '__main__':
    main()
