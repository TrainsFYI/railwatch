#!/usr/bin/env bash
# Public entry point: curl -fsSL https://raw.githubusercontent.com/TrainsFYI/railwatch/main/install.sh | bash
set -euo pipefail
say() { printf '\n%s\n' "$*"; }
ask() {
  printf '%s ' "$1" >/dev/tty
  IFS= read -r REPLY </dev/tty
}
if [[ "$(uname -s)" != Linux || "$(uname -m)" != aarch64 ]]; then
  say 'RailWatch needs 64-bit Raspberry Pi OS on a Raspberry Pi (Pi 5 recommended).'
  say 'Install Raspberry Pi OS Lite (64-bit), boot the Pi, then run this command there.'
  exit 1
fi
if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
  say 'Run the installer in an interactive terminal or SSH session on your Pi.'
  exit 1
fi
say 'Welcome to RailWatch — your freight train camera'
say 'This wizard installs RailWatch and enables one-click updates from official GitHub releases.'
say 'You need: a powered USB or RTSP camera, internet, and a safe view of the railway.'
say 'Your camera settings, recordings and account connection are kept when upgrading.'
ask 'Ready to install? [Y/n]'
case "$REPLY" in n|N|no|NO) exit 0;; esac
if [[ "$EUID" == 0 ]]; then SUDO=(); else SUDO=(sudo); "${SUDO[@]}" -v; fi
say '1 of 4 · Checking the Pi and installing download tools'
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y python3 curl ca-certificates
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "Please upgrade to Raspberry Pi OS Bookworm or newer (Python 3.11+).")'
say '2 of 4 · Downloading the newest official release (including pilot prereleases)'
WORK="$(mktemp -d)"
# WORK contains only this invocation's downloaded files. Keep it for troubleshooting on failure.
python3 - "$WORK" <<'PY'
import hashlib
import json
import pathlib
import re
import tarfile
import sys
import urllib.request

root = pathlib.Path(sys.argv[1])
repo = 'TrainsFYI/railwatch'
request = urllib.request.Request(f'https://api.github.com/repos/{repo}/releases?per_page=100',
                                headers={'User-Agent': 'RailWatch-installer', 'Accept': 'application/vnd.github+json'})
with urllib.request.urlopen(request, timeout=30) as response:
    releases = json.load(response)
def key(release):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?', release.get('tag_name', ''))
    if not match:
        return None
    a,b,c,stage,n = match.groups()
    return (int(a),int(b),int(c),{'alpha':0,'beta':1,'rc':2,None:3}[stage],int(n or 0))
candidates = []
for release in releases:
    if release.get('draft') or not release.get('published_at') or key(release) is None:
        continue
    tag = release['tag_name']
    name = f'railwatch-{tag.removeprefix("v")}.tar.gz'
    url = f'https://github.com/{repo}/releases/download/{tag}/{name}'
    for asset in release.get('assets', []):
        if (asset.get('name') == name and asset.get('browser_download_url') == url
                and re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest', ''))
                and asset.get('state') == 'uploaded' and 0 < asset.get('size', 0) <= 20*1024*1024):
            candidates.append((release, asset))
if not candidates:
    raise SystemExit('No installable release is available yet. Please try again later.')
release, asset = max(candidates, key=lambda item:key(item[0]))
print('Installing', release['tag_name'], '(prerelease)' if release.get('prerelease') else '')
with urllib.request.urlopen(asset['browser_download_url'], timeout=60) as response:
    data = response.read(asset['size']+1)
if len(data) != asset['size'] or 'sha256:'+hashlib.sha256(data).hexdigest() != asset['digest']:
    raise SystemExit('Release verification failed; nothing was installed.')
archive = root/'release.tar.gz'
archive.write_bytes(data)
with tarfile.open(archive, 'r:gz') as source:
    members = source.getmembers()
    if len(members)>2000 or sum(m.size for m in members)>50*1024*1024:
        raise SystemExit('Release archive is too large.')
    for member in members:
        p=pathlib.Path(member.name)
        if p.is_absolute() or '..' in p.parts or not (member.isfile() or member.isdir()):
            raise SystemExit('Unsafe release archive.')
    for member in members:
        target=root/'source'/member.name
        if member.isdir():
            target.mkdir(parents=True,exist_ok=True)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            with source.extractfile(member) as incoming,target.open('xb') as output:
                output.write(incoming.read())
roots=list((root/'source').iterdir())
if len(roots)!=1 or not (roots[0]/'scripts/install.sh').is_file():
    raise SystemExit('Invalid release bundle.')
(root/'source-path').write_text(str(roots[0]))
PY
say '3 of 4 · Installing RailWatch and its background service'
SOURCE="$(cat "$WORK/source-path")"
# Move the verified source into a root-owned directory before running privileged code.
# This prevents another local user replacing files between verification and installation.
ROOT_WORK="$("${SUDO[@]}" mktemp -d /opt/railwatch-install.XXXXXX)"
"${SUDO[@]}" cp -R "$SOURCE/." "$ROOT_WORK/"
"${SUDO[@]}" bash "$ROOT_WORK/scripts/install.sh"
say '4 of 4 · Finish the guided camera setup in your browser'
printf '\nOpen http://%s.local:8787 from a phone or computer on the same network.\n' "$(hostname)"
say 'The browser wizard will walk you through:'
say '  1. Your USB camera or Wyze/local RTSP stream and its live preview'
say '  2. The railway location, landmarks and detection area'
say '  3. Connecting your trains.fyi account and checking the first train'
say 'Keep this dashboard on your home network; no router port forwarding is needed.'
say 'RailWatch starts automatically when your Pi boots. Release checks run daily.'
say 'When an update is available, choose “Please update” in the navigation, then “Update now”.'
