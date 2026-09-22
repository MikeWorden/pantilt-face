#!/usr/bin/env bash
# One-time setup on the Raspberry Pi (Raspberry Pi OS 64-bit / Bookworm).
# Installs system packages for the camera + HAT, enables I2C, creates the
# venv with --system-site-packages (required so picamera2/libcamera/
# pantilthat, all apt-installed, are importable inside it), then installs
# the pip-only requirements.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

echo "==> Installing system packages"
sudo apt update
sudo apt install -y \
  python3-full python3-venv python3-pip \
  python3-picamera2 python3-libcamera \
  python3-opencv \
  i2c-tools libcap-dev

# python3-pantilthat isn't in all Raspberry Pi OS repos; pip install falls
# back to it if the apt package is unavailable.
sudo apt install -y python3-pantilthat || echo "python3-pantilthat not in apt; will pip install instead"

echo "==> Enabling I2C interface"
sudo raspi-config nonint do_i2c 0

echo "==> Adding $(whoami) to the i2c group (for /dev/i2c-1 access)"
sudo usermod -aG i2c "$(whoami)"

echo "==> Creating venv with --system-site-packages at ${VENV_DIR}"
python3 -m venv --system-site-packages "${VENV_DIR}"

echo "==> Installing Python requirements"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"
"${VENV_DIR}/bin/pip" install pantilthat || true  # no-op if apt package already provided it

echo "==> Installing pantilt_face itself (editable) so 'python -m pantilt_face.main' works"
"${VENV_DIR}/bin/pip" install -e "${PROJECT_DIR}"

echo "==> Downloading face detection model"
"${PROJECT_DIR}/scripts/download_models.sh"

cat <<MSG

Setup complete.

A reboot (or re-login) is needed for the i2c group membership to take
effect: sudo reboot

Then run:
  source ${VENV_DIR}/bin/activate
  python -m pantilt_face.main

MSG
