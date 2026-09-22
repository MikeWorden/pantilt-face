#!/usr/bin/env bash
# Syncs this project to the Raspberry Pi and (optionally) restarts the
# systemd service.
#
# Usage:
#   PI_HOST=raspberrypi.local PI_USER=pi ./scripts/deploy.sh
#   PI_HOST=192.168.1.42 PI_USER=pi ./scripts/deploy.sh --restart
set -euo pipefail

PI_HOST="${PI_HOST:?Set PI_HOST, e.g. PI_HOST=raspberrypi.local}"
PI_USER="${PI_USER:-pi}"
REMOTE_DIR="${REMOTE_DIR:-/home/${PI_USER}/pantilt-face}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Syncing ${PROJECT_DIR} -> ${PI_USER}@${PI_HOST}:${REMOTE_DIR}"
rsync -avz --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude '*.pyc' \
  --exclude 'models/*.onnx' \
  "${PROJECT_DIR}/" "${PI_USER}@${PI_HOST}:${REMOTE_DIR}/"

if [[ "${1:-}" == "--restart" ]]; then
  echo "==> Restarting pantilt-face.service on ${PI_HOST}"
  ssh "${PI_USER}@${PI_HOST}" "sudo systemctl restart pantilt-face.service"
fi

echo "Done. SSH in with: ssh ${PI_USER}@${PI_HOST}"
