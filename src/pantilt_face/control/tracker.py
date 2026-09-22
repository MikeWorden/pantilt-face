"""
Ties detection + PID + servo actuation together into one control-loop step,
and burns the HUD onto the frame for the stream.

This is the "Vision & Face Tracking Thread" box from CLAUDE.md's
architecture diagram: detection and actuation are deliberately kept in the
same call stack so there's no cross-thread hop between seeing the face and
moving the servo, which is what makes the <40ms capture-to-I2C budget
achievable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from pantilt_face.config import CONFIG, TrackerConfig
from pantilt_face.control.pid import PID
from pantilt_face.hardware.pantilt import PanTiltDriver
from pantilt_face.vision.detector import Face, FaceDetector


@dataclass
class TrackerMetrics:
    face_found: bool = False
    error_x_px: float = 0.0
    error_y_px: float = 0.0
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    detect_ms: float = 0.0
    total_ms: float = 0.0
    fps: float = 0.0
    backend: str = ""
    last_updated: float = field(default_factory=time.monotonic)


class FaceTracker:
    """Runs one detect -> PID -> actuate -> HUD step per call to `process(frame)`."""

    def __init__(
        self,
        detector: Optional[FaceDetector] = None,
        driver: Optional[PanTiltDriver] = None,
        cfg: TrackerConfig = CONFIG.tracker,
    ) -> None:
        self.cfg = cfg
        self.detector = detector or FaceDetector()
        self.driver = driver or PanTiltDriver()
        self.pan_pid = PID(cfg.pan)
        self.tilt_pid = PID(cfg.tilt)
        self.metrics = TrackerMetrics(backend=self.detector.backend_name)
        self._last_tick: Optional[float] = None
        self._fps_ema: Optional[float] = None
        self._smoothed_center: Optional[tuple[float, float]] = None
        self._missed_frames = 0

    def process(self, frame_rgb: np.ndarray) -> np.ndarray:
        """Runs a full tracking step; returns the frame with HUD burned in
        (still RGB -- caller/server handles the final color conversion for
        JPEG encoding)."""
        t_start = time.monotonic()
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        t_detect0 = time.monotonic()
        face = self.detector.detect(frame_bgr)
        detect_ms = (time.monotonic() - t_detect0) * 1000.0

        h, w = frame_bgr.shape[:2]
        cx, cy = w // 2, h // 2

        now = time.monotonic()
        dt = 0.0 if self._last_tick is None else max(now - self._last_tick, 1e-6)
        self._last_tick = now

        if face is not None and face.size >= self.cfg.min_face_size_px:
            self._missed_frames = 0
            fx, fy = self._smooth_center(face.center)
            err_x = fx - cx
            err_y = fy - cy

            # Positive err_x (face right of center) should pan the camera
            # right, and positive err_y (face below center, since image y
            # grows downward) should tilt down -- i.e. the PID input is
            # -err_y so it drives toward zero the same way. Whether that
            # actually matches "right"/"down" on the physical servo is a
            # wiring/mount fact, not something derivable from the image, so
            # it's controlled by TrackerConfig.invert_pan/invert_tilt.
            pan_input = -err_x if self.cfg.invert_pan else err_x
            tilt_input = err_y if self.cfg.invert_tilt else -err_y

            pan_delta = self.pan_pid.step(pan_input, now=now)
            tilt_delta = self.tilt_pid.step(tilt_input, now=now)

            target_pan = self.driver.pan_deg + pan_delta
            target_tilt = self.driver.tilt_deg + tilt_delta
            actual_pan, actual_tilt = self.driver.update(target_pan, target_tilt, dt)

            self.metrics.face_found = True
            self.metrics.error_x_px = err_x
            self.metrics.error_y_px = err_y
        else:
            # No face: hold position (PID state persists so we don't get a
            # derivative kick when tracking resumes).
            self._missed_frames += 1
            if self._missed_frames > self.cfg.smoothing_reset_after_missed_frames:
                self._smoothed_center = None
            actual_pan, actual_tilt = self.driver.pan_deg, self.driver.tilt_deg
            self.metrics.face_found = False
            self.metrics.error_x_px = 0.0
            self.metrics.error_y_px = 0.0

        self.metrics.pan_deg = actual_pan
        self.metrics.tilt_deg = actual_tilt
        self.metrics.detect_ms = detect_ms

        if self.cfg.hud_enabled:
            self._draw_hud(frame_bgr, face, (cx, cy))

        total_ms = (time.monotonic() - t_start) * 1000.0
        self.metrics.total_ms = total_ms
        inst_fps = 1000.0 / total_ms if total_ms > 0 else 0.0
        self._fps_ema = inst_fps if self._fps_ema is None else (0.9 * self._fps_ema + 0.1 * inst_fps)
        self.metrics.fps = self._fps_ema
        self.metrics.last_updated = time.monotonic()

        return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    def _smooth_center(self, raw_center: tuple[int, int]) -> tuple[float, float]:
        """EMA-debounce the detected face center so per-frame detector noise
        (a static face's bbox still wobbles a few px frame to frame) doesn't
        get chased as if it were real movement."""
        alpha = self.cfg.face_center_smoothing_alpha
        if self._smoothed_center is None:
            self._smoothed_center = (float(raw_center[0]), float(raw_center[1]))
        else:
            prev_x, prev_y = self._smoothed_center
            self._smoothed_center = (
                alpha * raw_center[0] + (1 - alpha) * prev_x,
                alpha * raw_center[1] + (1 - alpha) * prev_y,
            )
        return self._smoothed_center

    def _draw_hud(self, frame_bgr: np.ndarray, face: Optional[Face], center: tuple[int, int]) -> None:
        h, w = frame_bgr.shape[:2]
        cx, cy = center
        color_ok = (0, 220, 0)
        color_bad = (0, 0, 220)
        color_crosshair = (200, 200, 200)

        # Frame crosshair
        cv2.line(frame_bgr, (cx - 12, cy), (cx + 12, cy), color_crosshair, 1)
        cv2.line(frame_bgr, (cx, cy - 12), (cx, cy + 12), color_crosshair, 1)

        if face is not None:
            cv2.rectangle(frame_bgr, (face.x, face.y), (face.x + face.w, face.y + face.h), color_ok, 2)
            # Raw detection center (small hollow dot) vs. the smoothed
            # center the control loop actually tracks (filled dot + line) --
            # the gap between them is exactly the per-frame jitter being
            # debounced out.
            raw_fx, raw_fy = face.center
            cv2.circle(frame_bgr, (raw_fx, raw_fy), 3, color_crosshair, 1)
            if self._smoothed_center is not None:
                sfx, sfy = int(self._smoothed_center[0]), int(self._smoothed_center[1])
                cv2.circle(frame_bgr, (sfx, sfy), 4, color_ok, -1)
                cv2.line(frame_bgr, (cx, cy), (sfx, sfy), color_ok, 1)

        status_color = color_ok if self.metrics.face_found else color_bad
        lines = [
            f"{self.metrics.backend}  fps:{self.metrics.fps:5.1f}  detect:{self.metrics.detect_ms:5.1f}ms",
            f"err: ({self.metrics.error_x_px:+.0f}, {self.metrics.error_y_px:+.0f}) px",
            f"pan:{self.metrics.pan_deg:+6.1f} deg  tilt:{self.metrics.tilt_deg:+6.1f} deg"
            + ("  [MOCK]" if self.driver.is_mock else ""),
        ]
        for i, text in enumerate(lines):
            y = h - 10 - (len(lines) - 1 - i) * 16
            cv2.putText(frame_bgr, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, status_color, 1, cv2.LINE_AA)

    def shutdown(self) -> None:
        """Return to center on exit rather than leaving the gimbal wherever
        the last tracking frame left it."""
        self.driver.center()
