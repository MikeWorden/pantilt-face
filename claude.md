# Project Definition: Face-Tracking Pan-Tilt Camera (v1)

## 1. Project Overview & Scope
- **Core Function:** Real-time face detection and closed-loop visual servoing using a 2-axis Pan-Tilt HAT on a Raspberry Pi 4 (4GB). The gimbal automatically pans and tilts to keep the primary detected face centered in the camera frame.
- **Remote Video Feed:** Headless operation serving an HTTP MJPEG video stream accessible from any standard web browser on the local network (e.g., `http://<pi-ip>:8080`).
- **Telemetry & Overlays:** Stream renders an optional HUD with target bounding box, face center point, frame crosshairs, and tracking error metrics.
- **Latency & Performance Targets:**
  - Vision Pipeline: ≥ 20 FPS at 640x480 capture resolution.
  - Video Stream: Low latency (< 100 ms transmission lag over local LAN).
  - Tracking Latency: < 40 ms from frame capture to I2C servo update.

---

## 2. Hardware Profile & Constraints
- **Host:** Raspberry Pi 4 Model B (4GB RAM, Broadcom BCM2711, 4x Cortex-A72).
- **OS:** Raspberry Pi OS 64-bit (Debian 12 Bookworm / Linux kernel 6.x+).
- **Camera:** Raspberry Pi Camera Module (CSI via `libcamera` / `Picamera2`).
- **Actuator:** Pimoroni Pan-Tilt HAT via I2C Bus 1 (`/dev/i2c-1`, address `0x15`).
  - **Pan Travel Safe Clamp:** `-75°` to `+75°`
  - **Tilt Travel Safe Clamp:** `-40°` to `+50°` (prevents mechanical strain on CSI ribbon cable)
  - **Slew Rate Cap:** Max 120 deg/s to prevent servo gear stripping and Pi power brownouts.

---

## 3. Technology Stack & Dependencies
- **Runtime:** Python 3.11+ virtual environment (`--system-site-packages`).
- **Capture:** `picamera2` (captures BGR/RGB directly into NumPy arrays via shared memory buffer).
- **Face Detection Options:**
  - *Primary:* OpenCV YuNet (`cv2.FaceDetectorYN`) — high accuracy, hardware-friendly ONNX model optimized for edge devices (~15–25ms on RPi 4 CPU).
  - *Fallback / Baseline:* MediaPipe Face Detection or Haar Cascades (for fast bootstrapping).
- **Control Loop:** 2-Axis discrete PID controller with integral anti-windup and error deadzones.
- **Actuation Driver:** `pantilthat` (wrapped with custom auto-mocking and I2C retry logic).
- **Web Streaming Server:** Lightweight `aiohttp` or `FastAPI` + `uvicorn` serving a multipart `image/jpeg` MJPEG stream with an atomic frame lock.

---

## 4. System Architecture & Concurrency Model

```text
               ┌──────────────────────────────────────┐
               │    Picamera2 Capture Thread (30 FPS) │
               └──────────────────┬───────────────────┘
                                  │ (Latest Frame Only)
                                  ▼
               ┌──────────────────────────────────────┐
               │    Vision & Face Tracking Thread     │
               │  - Run FaceDetectorYN                │
               │  - Compute Error (ex, ey) from (0,0) │
               │  - Execute PID Step                  │
               │  - Command I2C Pan-Tilt HAT          │
               │  - Burn HUD / Bounding Box onto Frame│
               └──────────────────┬───────────────────┘
                                  │ (Atomic Frame Buffer Update)
                                  ▼
               ┌──────────────────────────────────────┐
               │    Asynchronous HTTP Server (8080)   │
               │  - Serves index.html UI              │
               │  - Serves /stream.mjpg endpoint      │
               └──────────────────────────────────────┘
