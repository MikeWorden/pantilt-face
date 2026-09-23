# Project Definition: Face-Tracking Pan-Tilt Camera (v1)

> This document supersedes `claude.md` as the working reference. Section 1–4
> are the original project definition, preserved as written. Section 5
> onward captures what was learned building against the real hardware —
> config defaults that had to change from a reasonable-sounding guess to
> what this specific unit actually needs, and why. If you're picking up
> this project fresh, read section 5 before touching `config.py`; it'll
> save you re-discovering the same four hardware quirks from scratch.

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
```

Implemented as (`src/pantilt_face/`):

| Diagram box | Module |
|---|---|
| Picamera2 Capture Thread | `capture/camera.py` — `FrameGrabber` |
| Vision & Face Tracking Thread | `vision/detector.py` + `control/pid.py` + `control/tracker.py` + `hardware/pantilt.py` |
| Asynchronous HTTP Server | `server/app.py` + `server/templates/index.html` |
| — | `main.py` wires the three together; `config.py` is the single source of truth for every tunable below |

Repo: https://github.com/MikeWorden/pantilt-face

---

## 5. Empirically-Determined Defaults (read this before re-tuning `config.py`)

Everything in this section was a reasonable-sounding assumption at scaffold
time that turned out to be wrong (or merely unconfirmed) against the actual
Pi + HAT + camera unit this project targets. Each is now a named config
field rather than a hardcoded constant, specifically so it doesn't need
rediscovering — but the **defaults themselves** encode what this hardware
needs, not a generic guess. Don't reset them to "obvious" values without
re-testing on the real unit.

### 5.1 Camera is mounted upside down
`CameraConfig.rotate_180` (`config.py`) — **default `True`**.

The Pan-Tilt HAT's bracket mounts the Pi Camera board upside down relative
to a normal handheld orientation. Applied via `libcamera.Transform(hflip,
vflip)` on Picamera2 (sensor/ISP-level, not a frame copy) and
`cv2.flip(frame, -1)` on the OpenCV webcam fallback. If you ever remount
the camera right-side up, set this `False` — and see 5.2, which is coupled
to this.

### 5.2 Servo direction is inverted relative to pixel error, on both axes
`TrackerConfig.invert_pan` / `invert_tilt` (`config.py`) — **default `True`**
for both.

Which way "positive pixel error" should drive the servo is a wiring/mount
fact specific to this HAT, not something derivable from the image. Verified
empirically on the real hardware: the camera panned and tilted *away* from
the face until both flags were inverted. If you swap the HAT unit or
re-wire it, re-run the empirical test in the README ("Tracking moves the
wrong way") before trusting these defaults — they are not a general
Pan-Tilt HAT fact, they are what *this* unit needed.

### 5.3 Package must be installed, not just importable via `PYTHONPATH`
`pyproject.toml` needs `[build-system]` (setuptools) and
`[tool.setuptools.packages.find] where = ["src"]` — without these,
`pip install -e .` fails outright, and `python -m pantilt_face.main` only
works by accident when launched from a shell where `src/` happens to be on
`sys.path`. `scripts/setup_pi.sh` runs `pip install -e .` after
requirements; if you ever run the app from a fresh venv (or a fresh clone)
without going through that script, run `pip install -e .` yourself first.
Also requires Python **≥3.11** for the editable install to resolve at all
(matches the Raspberry Pi OS Bookworm system Python).

### 5.4 The driver never confirmed the HAT's actual position — now it does
`ServoLimits.start_pan_deg` / `start_tilt_deg` (`config.py`) — default
`0.0` / `0.0`.

Originally `PanTiltDriver.__init__` just *assumed* internal state of
`(0, 0)` without ever commanding the hardware there — fine as long as the
HAT happened to already be at rest at zero, not fine after a power cycle or
a prior unclean shutdown. The driver now explicitly commands this position
on init and returns to it in `center()` (called on both startup and
shutdown), and it's a configurable "home" pose rather than a hardcoded
zero — set it if you want the camera looking at a specific spot when the
service comes up cold.

### 5.5 Raw P/D control chases detector noise on a motionless face
Two independent, currently-enabled debounces exist because a *static*
face's detected bbox still wobbles a few px frame-to-frame from detector
noise — a raw P/D loop chases that as if it were real movement, and the D
term actively amplifies it. Observed on hardware as constant low-amplitude
servo hunting with no one moving.

- `TrackerConfig.face_center_smoothing_alpha` (default `0.4`) — EMA
  smoothing on the detected face center before error is computed, reset
  after `smoothing_reset_after_missed_frames` (default `15`) consecutive
  missed frames so a long absence doesn't lag toward a stale position on
  reacquire.
- `HardwareConfig.min_command_delta_deg` (default `0.3`) — `PanTiltDriver`
  skips writing a servo axis when the slew-limited target is within this of
  the last *committed* position. Pending small deltas aren't dropped; they
  accumulate against that same baseline until real movement (or drift)
  clears the threshold.

**Tuning constraint:** for a real error to ever clear
`min_command_delta_deg`, `PIDGains.kp * PIDGains.deadzone_px` must stay
above it. Defaults (`0.045 * 8 = 0.36°` vs. a `0.3°` threshold) leave a thin
margin — if you loosen the pixel deadzone or lower `kp`, loosen
`min_command_delta_deg` to match, or you'll create a dead zone where real,
sustained tracking error never generates a large enough correction to
commit.

The HUD draws both the raw detection (hollow dot) and the smoothed tracking
target (filled dot + line) on the stream, specifically so this filtering is
visible/debuggable rather than invisible.

### 5.6 Onboard LED support is board/library-dependent, so it's probed, not assumed
`TrackerConfig.led_enabled` / `led_color_found` / `led_color_lost`
(`config.py`); driven through `PanTiltDriver.set_led()` / `.led_off()`
(`hardware/pantilt.py`), which calls `pantilthat.set_all(r,g,b)` +
`.show()` (the same SN3218 driver chip used for the servos).

This API was implemented from `pantilthat`'s documented interface, without
being able to confirm it lights a physical LED on the specific unit this
project targets — board revisions and library versions vary in whether
`set_all`/`show` are present at all. `PanTiltDriver` checks with
`hasattr()` before ever calling it; if unsupported, it logs **one** warning
and permanently no-ops (never retries a call that will never succeed) so
the tracking loop is unaffected either way. The HUD always draws a matching
status dot (top-right, green while tracking) independent of whether the
physical LED actually lit — treat that dot as the source of truth for
whether this feature is "working," and the physical LED as a bonus if your
board supports it.

---

## 6. Fallback Chains (why the app runs the same way on a Mac and on the Pi)

Every hardware dependency degrades gracefully rather than crashing, so the
full pipeline is developable and testable off-target:

| Layer | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| Camera | Picamera2 (CSI) | OpenCV `VideoCapture` (USB webcam / index 0) | Synthetic test-pattern frame generator |
| Face detector | YuNet ONNX (`cv2.FaceDetectorYN`) | Haar cascade (bundled with OpenCV) | — |
| Actuator | `pantilthat` (real I2C) | `MockPanTiltBackend` (logs commands, tracks position in software) | — |
| LED | `pantilthat.set_all`/`.show()` | HUD status dot only (see 5.6) | — |

Each fallback logs a `WARNING` explaining why it engaged — check the log
before assuming a config bug if something looks off; it may just mean a
dependency didn't load (e.g. running on a dev machine, or an unwired LED).

---

## 7. Testing

`tests/` is entirely hardware-free (forces `HardwareConfig(force_mock=True)`
and exercises `PID` math directly) — 20 tests as of this writing, covering:
clamping, slew-rate limiting, the min-command-delta debounce (including
that it's per-axis-independent and that a below-threshold request isn't
lost, just deferred), configurable start position (default, custom,
clamped, and that `center()` returns to the configured — not hardcoded —
position), LED reaching the mock backend and degrading cleanly on an
unsupported backend, and PID deadzone/anti-windup/derivative behavior.

Run with `pytest` from the repo root (requires `pip install -r
requirements-dev.txt`). None of this exercises the real HAT, camera
timing, or detector accuracy — those need the physical unit; treat a green
test run as "the control math and safety logic are correct," not as "this
will track well," which still needs on-hardware verification.

---

## 8. Deployment

- `scripts/setup_pi.sh` — apt deps (`picamera2`, `libcamera`, `pantilthat`
  if available), enables I2C, creates the `--system-site-packages` venv,
  installs requirements, `pip install -e .` (see 5.3), downloads the YuNet
  model.
- `scripts/download_models.sh` — fetches the YuNet ONNX model from
  `opencv_zoo` into `models/` (gitignored; Haar fallback works without it).
- `scripts/deploy.sh` — `rsync` to the Pi, optional `--restart` of the
  systemd service.
- `deploy/pantilt-face.service` — systemd unit for headless operation on
  boot; `sudo systemctl enable --now pantilt-face.service`.

See `README.md` for the full command sequences and the **Troubleshooting**
section (packaging errors, upside-down video, tracking direction, jittery
tracking) — each entry there maps directly to a subsection of §5 above.
