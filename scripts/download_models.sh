#!/usr/bin/env bash
# Downloads the YuNet ONNX face detector into models/.
# Source: OpenCV Zoo (https://github.com/opencv/opencv_zoo).
set -euo pipefail

MODEL_URL="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
DEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models"
DEST="${DEST_DIR}/face_detection_yunet_2023mar.onnx"

mkdir -p "${DEST_DIR}"

if [ -f "${DEST}" ]; then
  echo "Model already present at ${DEST}"
  exit 0
fi

echo "Downloading YuNet model to ${DEST}..."
curl -fL --progress-bar -o "${DEST}" "${MODEL_URL}"
echo "Done."
