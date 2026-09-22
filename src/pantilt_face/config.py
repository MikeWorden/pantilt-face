"""
Central configuration for the face-tracking pan-tilt camera.

Values here mirror the hardware/performance contract in CLAUDE.md.
Nothing outside this module should hardcode a clamp, gain, or resolution --
import from here so a single source of truth stays authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServoLimits:
    """Mechanical safe envelope for the Pimoroni Pan-Tilt HAT."""

    pan_min_deg: float = -75.0
    pan_max_deg: float = 75.0
    tilt_min_deg: float = -40.0
    tilt_max_deg: float = 50.0
    max_slew_deg_s: float = 120.0  # gear-stripping / brownout guard

    # Position the gimbal snaps to immediately on startup, and returns to on
    # shutdown -- there's no feedback from the HAT on where it physically
    # is, so the driver can't assume (0, 0) is actually where it's sitting
    # (e.g. after a power cycle without a clean shutdown). Defaults to
    # dead-center; override if you want it looking at a specific spot when
    # the service comes up cold. Out-of-range values are clamped to the
    # limits above rather than rejected.
    start_pan_deg: float = 0.0
    start_tilt_deg: float = 0.0


@dataclass(frozen=True)
class CameraConfig:
    width: int = 640
    height: int = 480
    capture_fps: int = 30
    # picamera2 format; ignored by the webcam/mock fallback.
    pixel_format: str = "RGB888"
    # The Pimoroni Pan-Tilt HAT's bracket mounts the camera board upside
    # down relative to a normal handheld orientation. True by default to
    # match that mount; set False if your camera is mounted right-side up.
    rotate_180: bool = True


@dataclass(frozen=True)
class PIDGains:
    kp: float = 0.045
    ki: float = 0.0015
    kd: float = 0.012
    integral_limit: float = 40.0   # anti-windup clamp on the accumulated term
    deadzone_px: float = 8.0       # ignore error smaller than this (pixel space)


@dataclass(frozen=True)
class TrackerConfig:
    pan: PIDGains = field(default_factory=PIDGains)
    tilt: PIDGains = field(default_factory=PIDGains)
    target_fps: int = 20            # vision pipeline floor from CLAUDE.md
    max_capture_to_servo_ms: float = 40.0
    hud_enabled: bool = True
    # Faces smaller than this (px, on the longer bbox side) are treated as
    # noise rather than a trackable subject.
    min_face_size_px: int = 24
    # Whether positive pixel error should map to positive servo delta, per
    # axis. Which way is "positive" for a given servo depends on how the
    # HAT/motors are wired and mounted, not something derivable from the
    # image alone -- if the camera pans/tilts away from the face instead of
    # toward it, flip the corresponding flag. See README "Tracking moves
    # the wrong way" for the empirical test.
    invert_pan: bool = True
    invert_tilt: bool = True
    # EMA smoothing applied to the detected face center before computing
    # error, to debounce per-frame detector jitter (a static face's bbox
    # still wobbles a few px frame-to-frame) so the loop doesn't chase
    # measurement noise. 1.0 = no smoothing (raw detection each frame);
    # lower = smoother but slower to react to real movement.
    face_center_smoothing_alpha: float = 0.4
    # If the face is lost for this many consecutive frames, drop the
    # smoothed estimate instead of blending toward wherever it reappears --
    # otherwise a long absence would make the first few frames back lag
    # toward the stale pre-loss position.
    smoothing_reset_after_missed_frames: int = 15
    # Onboard HAT LED (and HUD status dot) color for face-found / no-face,
    # as (r, g, b) 0-255. Set led_enabled=False to skip touching the LED
    # entirely -- the HUD indicator is drawn either way.
    led_enabled: bool = True
    led_color_found: tuple[int, int, int] = (0, 255, 0)
    led_color_lost: tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True)
class DetectorConfig:
    backend: str = "yunet"  # "yunet" | "haar"
    yunet_model_path: str = "models/face_detection_yunet_2023mar.onnx"
    haar_cascade_path: str = "haarcascade_frontalface_default.xml"
    score_threshold: float = 0.75
    nms_threshold: float = 0.3
    top_k: int = 5000


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    jpeg_quality: int = 80
    boundary: str = "frame"


@dataclass(frozen=True)
class HardwareConfig:
    i2c_bus: int = 1
    i2c_address: int = 0x15
    i2c_retries: int = 3
    i2c_retry_backoff_s: float = 0.05
    # Force the mock driver even if pantilthat imports cleanly (e.g. running
    # on the Pi with the HAT unplugged for a bench test).
    force_mock: bool = False
    # Debounce: skip writing to the servo when the slew-limited target is
    # closer than this to the last commanded position. Without it, residual
    # sub-degree noise from the control loop keeps re-commanding the servo
    # every tick, which reads as constant small buzzing/hunting even when
    # the subject is holding still. Small pending deltas aren't lost -- they
    # just accumulate against the last *committed* position until they
    # cross this threshold, or until real movement pushes past it.
    min_command_delta_deg: float = 0.3


@dataclass(frozen=True)
class AppConfig:
    servo_limits: ServoLimits = field(default_factory=ServoLimits)
    camera: CameraConfig = field(default_factory=CameraConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)


CONFIG = AppConfig()
