"""
Face detection.

Primary: OpenCV YuNet (cv2.FaceDetectorYN) -- an ONNX model tuned for edge
CPUs, ~15-25ms on a Pi 4. Requires the model file (see
scripts/download_models.sh).

Fallback: Haar cascade, bundled with every OpenCV install, for bootstrapping
before the YuNet model is downloaded, or if ONNX loading fails for any
reason. Meaningfully less accurate, so a bench-run comparison is worth doing
before relying on it for anything but a smoke test.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from pantilt_face.config import CONFIG, DetectorConfig

logger = logging.getLogger(__name__)


@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def size(self) -> int:
        return max(self.w, self.h)


class FaceDetector:
    """Detects faces in a frame; `detect()` returns the primary (largest) face."""

    def __init__(self, cfg: DetectorConfig = CONFIG.detector, frame_size: tuple[int, int] = (640, 480)):
        self.cfg = cfg
        self._frame_size = frame_size
        self._backend_name, self._detector = self._build_backend(cfg, frame_size)
        logger.info("FaceDetector backend: %s", self._backend_name)

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def _build_backend(self, cfg: DetectorConfig, frame_size: tuple[int, int]):
        if cfg.backend == "yunet" and os.path.exists(cfg.yunet_model_path):
            try:
                detector = cv2.FaceDetectorYN.create(
                    cfg.yunet_model_path,
                    "",
                    frame_size,
                    score_threshold=cfg.score_threshold,
                    nms_threshold=cfg.nms_threshold,
                    top_k=cfg.top_k,
                )
                return "yunet", detector
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load YuNet model (%s); falling back to Haar", exc)
        elif cfg.backend == "yunet":
            logger.warning(
                "YuNet model not found at %s (run scripts/download_models.sh); falling back to Haar",
                cfg.yunet_model_path,
            )

        cascade_path = cfg.haar_cascade_path
        if not os.path.isabs(cascade_path):
            cascade_path = os.path.join(cv2.data.haarcascades, cascade_path)
        classifier = cv2.CascadeClassifier(cascade_path)
        if classifier.empty():
            raise RuntimeError(f"Could not load Haar cascade from {cascade_path}")
        return "haar", classifier

    def set_frame_size(self, width: int, height: int) -> None:
        """YuNet's input size must match the frame; call if resolution changes."""
        if self._backend_name == "yunet" and (width, height) != self._frame_size:
            self._frame_size = (width, height)
            self._detector.setInputSize((width, height))

    def detect(self, frame_bgr: np.ndarray) -> Optional[Face]:
        """Runs detection and returns the largest face, or None."""
        faces = self._detect_all(frame_bgr)
        if not faces:
            return None
        return max(faces, key=lambda f: f.w * f.h)

    def _detect_all(self, frame_bgr: np.ndarray) -> list[Face]:
        if self._backend_name == "yunet":
            h, w = frame_bgr.shape[:2]
            self.set_frame_size(w, h)
            _, results = self._detector.detect(frame_bgr)
            if results is None:
                return []
            faces = []
            for row in results:
                x, y, w_, h_ = row[:4].astype(int)
                conf = float(row[-1])
                faces.append(Face(x=int(x), y=int(y), w=int(w_), h=int(h_), confidence=conf))
            return faces

        # Haar fallback
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        detections = self._detector.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
        )
        return [Face(x=int(x), y=int(y), w=int(w), h=int(h), confidence=1.0) for (x, y, w, h) in detections]
