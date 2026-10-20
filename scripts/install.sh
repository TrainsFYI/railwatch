#!/usr/bin/env bash
# Run on the target Pi: sudo bash scripts/install.sh [path/to/train.onnx]
set -euo pipefail
if [[ "$EUID" != 0 ]]; then
  echo 'Run this installer with sudo on the Raspberry Pi.' >&2
  exit 1
fi
if [[ "$(uname -m)" != aarch64 ]]; then
  echo 'Railwatcher requires 64-bit Raspberry Pi OS (aarch64).' >&2
  exit 1
fi
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_FILE="${1:-$SOURCE_DIR/models/train.onnx}"
apt-get update
apt-get install -y python3 python3-venv ffmpeg avahi-daemon
id railwatcher >/dev/null 2>&1 || useradd --system --home-dir /var/lib/railwatcher --create-home --groups video railwatcher
usermod -a -G video railwatcher
install -d -m 0755 /opt/railwatcher /opt/railwatcher/models
install -d -o railwatcher -g railwatcher -m 0700 /var/lib/railwatcher
python3 -m venv /opt/railwatcher/venv
/opt/railwatcher/venv/bin/pip install "$SOURCE_DIR"
if [[ -f "$MODEL_FILE" ]]; then
  install -m 0644 "$MODEL_FILE" /opt/railwatcher/models/train.onnx
  if [[ -f "${MODEL_FILE%.onnx}.json" ]]; then
    install -m 0644 "${MODEL_FILE%.onnx}.json" /opt/railwatcher/models/train.json
  else
    # Do not retain a previous multi-class mapping beside legacy weights.
    rm -f /opt/railwatcher/models/train.json
  fi
else
  echo 'Model missing. Setup will work; detection waits for /opt/railwatcher/models/train.onnx.'
fi
ln -sfn /opt/railwatcher/venv /opt/railwatcher/current
install -m 0644 "$SOURCE_DIR/railwatcher/releases.py" /opt/railwatcher/release_support.py
install -m 0644 "$SOURCE_DIR/scripts/update_helper.py" /opt/railwatcher/update-helper.py
install -m 0644 "$SOURCE_DIR/scripts/railwatcher-update.service" /etc/systemd/system/railwatcher-update.service
install -m 0644 "$SOURCE_DIR/scripts/railwatcher-update.path" /etc/systemd/system/railwatcher-update.path
install -m 0644 "$SOURCE_DIR/scripts/railwatcher-updates.conf" /etc/tmpfiles.d/railwatcher-updates.conf
systemd-tmpfiles --create /etc/tmpfiles.d/railwatcher-updates.conf
install -m 0644 "$SOURCE_DIR/scripts/railwatcher.service" /etc/systemd/system/railwatcher.service
systemctl daemon-reload
systemctl enable --now avahi-daemon
systemctl enable --now railwatcher-update.path
systemctl enable railwatcher
systemctl restart railwatcher
for attempt in {1..20}; do
  systemctl is-active --quiet railwatcher && break
  sleep 1
done
if ! systemctl is-active --quiet railwatcher; then
  echo 'Service did not start. Inspect: journalctl -u railwatcher -n 50' >&2
  exit 1
fi
printf '\nOpen http://%s.local:8787 on your local network.\n' "$(hostname)"
