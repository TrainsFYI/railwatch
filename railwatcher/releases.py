"""Fixed upstream release policy shared by the app and privileged updater (stdlib only)."""
import hashlib
import json
import re
import tarfile
import urllib.request
from pathlib import Path

REPOSITORY = 'TrainsFYI/railwatch'
API = f'https://api.github.com/repos/{REPOSITORY}/releases'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
MAX_ARCHIVE = 20 * 1024 * 1024


def version_key(value):
    if isinstance(value, str):
        value = re.sub(r'(?<=\d)(a|b|rc)(\d+)$',
                       lambda m: '-' + {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[m[1]] + '.' + m[2], value)
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(\d+))?', value or '')
    if not match:
        raise ValueError('Unsupported release version')
    major, minor, patch, stage, number = match.groups()
    return (int(major), int(minor), int(patch), {'alpha': 0, 'beta': 1, 'rc': 2, None: 3}[stage], int(number or 0))


def release_asset(release):
    tag = release.get('tag_name', '')
    version_key(tag)
    if release.get('draft') or not release.get('published_at'):
        raise ValueError('Release is not published')
    name = f'railwatch-{tag.removeprefix("v")}.tar.gz'
    expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    for asset in release.get('assets', []):
        digest = asset.get('digest', '')
        if (asset.get('name') == name and asset.get('browser_download_url') == expected
                and asset.get('state') == 'uploaded' and re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
                and isinstance(asset.get('size'), int) and 0 < asset['size'] <= MAX_ARCHIVE):
            return asset
    raise ValueError('Release has no verified install bundle')


def newest_release(releases):
    candidates = []
    for release in releases:
        try:
            release_asset(release)
            candidates.append(release)
        except (ValueError, TypeError, AttributeError):
            continue
    return max(candidates, key=lambda item: version_key(item['tag_name']), default=None)


def fetch_json(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                   'User-Agent': 'RailWatch-updater',
                                                   'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('Release response is too large')
    return json.loads(data)


def download_bundle(release, destination):
    asset = release_asset(release)
    request = urllib.request.Request(asset['browser_download_url'], headers={'User-Agent': 'RailWatch-updater'})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=60) as response, Path(destination).open('xb') as output:
        while chunk := response.read(128 * 1024):
            size += len(chunk)
            if size > asset['size'] or size > MAX_ARCHIVE:
                raise ValueError('Release bundle exceeds its declared size')
            digest.update(chunk)
            output.write(chunk)
    if size != asset['size'] or 'sha256:' + digest.hexdigest() != asset['digest']:
        raise ValueError('Release checksum did not match GitHub')


def extract_bundle(archive, directory):
    """Only regular files/directories, with bounded expansion and no path traversal."""
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if len(members) > 2000 or sum(item.size for item in members) > 50 * 1024 * 1024:
            raise ValueError('Release bundle expands beyond its limit')
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe release archive member')
        for member in members:
            target = Path(directory) / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open('xb') as output:
                    output.write(incoming.read())
    roots = list(Path(directory).iterdir())
    if len(roots) != 1 or not (roots[0] / 'pyproject.toml').is_file():
        raise ValueError('Invalid RailWatch source bundle')
    return roots[0]
