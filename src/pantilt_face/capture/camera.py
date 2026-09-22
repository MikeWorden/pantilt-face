"""
Frame capture thread.

On the Pi this drives Picamera2 (CSI camera) into a shared-memory NumPy
array. Off the Pi -- or if Picamera2 fails to initialize -- it falls back to
OpenCV's cv2.VideoCapture (a USB webcam, or index 0) so the rest of the
pipeline can be developed and tested without the target hardware.

Both backends run in a background thread and expose only the latest frame
behind a lock ("latest frame only", per the architecture diagram in
CLAUDE.md) -- a slow consumer skips frames rather than building a backlog.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

from pantilt_face.config import CONFIG, CameraConfig

logger = logging.getLogger(__name__)


class _CaptureBackend:
    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _Picamera2Backend(_CaptureBackend):
    def __init__(self, cfg: CameraConfig) -> None:
        from picamera2 import Picamera2  # type: ignore

        self._cam = Picamera2()
        config = self._cam.create_video_configuration(
            main={"size": (cfg.width, cfg.height), "format": cfg.pixel_format}
        )
        self._cam.configure(config)
        self._cam.start()
        logger.info("Picamera2 started at %dx%d", cfg.width, cfg.height)

    def read(self) -> Optional[np.ndarray]:
        return self._cam.capture_array()

    def close(self) -> None:
        self._cam.stop()


class _OpenCVBackend(_CaptureBackend):
    """Dev-machine fallback: USB webcam or any cv2-openable source."""

    def __init__(self, cfg: CameraConfig, source: int | str = 0) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(source)
        if not self._cap.isOpened():
            raise RuntimeError(f"cv2.VideoCapture could not open source {source!r}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        self._cap.set(cv2.CAP_PROP_FPS, cfg.capture_fps)
        logger.info("OpenCV VideoCapture(%r) opened as camera fallback", source)

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self._cap.read()
        if not ok:
            return None
        # BGR -> RGB to match Picamera2's RGB888 output, so downstream code
        # (detector, HUD, JPEG encode-back-to-BGR) doesn't need to branch.
        return self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        self._cap.release()


class _SyntheticBackend(_CaptureBackend):
    """Last-resort fallback: no CSI camera, no webcam (or no OS camera
    permission granted to this process -- common when running headless/in a
    sandbox). Generates a moving test pattern so the rest of the pipeline
    (detection returning "no face", PID holding position, HUD, MJPEG
    stream) can still be exercised end-to-end without any camera attached.
    """

    def __init__(self, cfg: CameraConfig) -> None:
        self._cfg = cfg
        self._t0 = time.monotonic()
        logger.warning(
            "No real or webcam camera available; using synthetic test-pattern "
            "source. Face tracking will report no face found -- this is for "
            "pipeline/server smoke-testing only."
        )

    def read(self) -> Optional[np.ndarray]:
        import cv2

        w, h = self._cfg.width, self._cfg.height
        t = time.monotonic() - self._t0
        frame = np.full((h, w, 3), (24, 26, 30), dtype=np.uint8)

        # A slowly orbiting dot so the stream is visibly live, not a static image.
        cx = int(w / 2 + (w / 3) * np.cos(t * 0.5))
        cy = int(h / 2 + (h / 3) * np.sin(t * 0.5))
        cv2.circle(frame, (cx, cy), 18, (60, 140, 220), -1)
        cv2.putText(
            frame,
            "SYNTHETIC SOURCE - no camera available",
            (10, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (180, 180, 180),
            1,
            cv2.LINE_AA,
        )
        time.sleep(1 / self._cfg.capture_fps)
        return frame

    def close(self) -> None:
        pass


def _build_backend(cfg: CameraConfig) -> _CaptureBackend:
    try:
        return _Picamera2Backend(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Picamera2 unavailable (%s); falling back to OpenCV VideoCapture. "
            "Expected when developing off the Raspberry Pi.",
            exc,
        )
    try:
        return _OpenCVBackend(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "No webcam available either (%s); falling back to synthetic test pattern.",
            exc,
        )
        return _SyntheticBackend(cfg)


class FrameGrabber:
    """Background capture thread exposing only the most recent frame."""

    def __init__(self, cfg: CameraConfig = CONFIG.camera) -> None:
        self.cfg = cfg
        self._backend = _build_backend(cfg)
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_ts: float = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="FrameGrabber")

    def start(self) -> "FrameGrabber":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._backend.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            frame = self._backend.read()
            if frame is not None:
                with self._lock:
                    self._frame = frame
                    self._frame_ts = time.monotonic()
            else:
                time.sleep(0.01)

    def get_latest(self) -> tuple[Optional[np.ndarray], float]:
        """Returns (frame, capture_timestamp_monotonic). frame may be None
        briefly at startup before the first frame arrives."""
        with self._lock:
            return self._frame, self._frame_ts
