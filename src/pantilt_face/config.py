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


@dataclass(frozen=True)
class CameraConfig:
    width: int = 640
    height: int = 480
    capture_fps: int = 30
    # picamera2 format; ignored by the webcam/mock fallback.
    pixel_format: str = "RGB888"


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


@dataclass(frozen=True)
class AppConfig:
    servo_limits: ServoLimits = field(default_factory=ServoLimits)
    camera: CameraConfig = field(default_factory=CameraConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)


CONFIG = AppConfig()
