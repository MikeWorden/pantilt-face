# Face-Tracking Pan-Tilt Camera

Real-time face detection and closed-loop visual servoing on a Raspberry Pi 4
with a Pimoroni Pan-Tilt HAT. See `CLAUDE.md` for the full project spec
(hardware constraints, performance targets, architecture).

## Layout

```
src/pantilt_face/
  config.py            # single source of truth for clamps, gains, resolution, ports
  hardware/pantilt.py  # Pan-Tilt HAT driver: auto-mock, I2C retry, clamp, slew-limit
  capture/camera.py    # Picamera2 capture thread, OpenCV webcam fallback for dev
  vision/detector.py   # YuNet (cv2.FaceDetectorYN) primary, Haar cascade fallback
  control/pid.py       # discrete PID, anti-windup, deadzone
  control/tracker.py   # detect -> PID -> actuate -> HUD, one step per frame
  server/app.py        # aiohttp MJPEG stream + JSON metrics
  main.py              # wires capture/tracking/server threads together
scripts/
  setup_pi.sh          # apt deps, enables I2C, creates --system-site-packages venv
  download_models.sh   # fetches the YuNet ONNX model from opencv_zoo
  deploy.sh            # rsync to the Pi (+ optional systemd restart)
deploy/pantilt-face.service   # systemd unit for running headless on boot
tests/                 # hardware-free unit tests (PID math, mock driver clamps)
```

## Developing on a Mac / non-Pi machine

Every hardware dependency auto-falls-back:

- No `pantilthat` / I2C bus → `PanTiltDriver` logs commands via
  `MockPanTiltBackend` instead of talking to hardware.
- No `picamera2` → `FrameGrabber` opens a USB webcam (or index 0) through
  OpenCV.
- No YuNet model downloaded yet → `FaceDetector` falls back to a bundled
  Haar cascade.

So the full pipeline — capture, detect, PID, "actuate" (into the mock), HUD,
and the MJPEG stream — runs end-to-end on a laptop for iteration, without
touching the Pi.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .                 # installs pantilt_face itself so -m works
./scripts/download_models.sh     # optional; Haar fallback works without it
python -m pantilt_face.main
# open http://localhost:8080
```

Run tests:

```bash
pytest
```

## Deploying to the Raspberry Pi

One-time setup on the Pi (installs `picamera2`/`libcamera`/`pantilthat` via
apt, enables I2C, creates the venv with `--system-site-packages` so those
apt-installed packages are visible inside it):

```bash
ssh pi@<pi-host>
git clone <this-repo> pantilt-face && cd pantilt-face
./scripts/setup_pi.sh
```

From your dev machine, to sync changes over without re-running setup:

```bash
PI_HOST=<pi-host-or-ip> PI_USER=pi ./scripts/deploy.sh
```

Run it directly:

```bash
ssh pi@<pi-host>
cd pantilt-face && source .venv/bin/activate
python -m pantilt_face.main
```

Or install as a systemd service so it survives reboots:

```bash
sudo cp deploy/pantilt-face.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pantilt-face.service
sudo journalctl -u pantilt-face.service -f
```

Then browse to `http://<pi-ip>:8080`.

## Configuration

All tunables (servo clamps, slew cap, PID gains, resolution, deadzone,
server port) live in `src/pantilt_face/config.py`. The mechanical safe
envelope (pan ±75°, tilt −40°/+50°, 120°/s slew cap) matches CLAUDE.md and
should not be loosened without re-checking the HAT's mechanical limits and
the CSI ribbon cable's range of motion.

`CameraConfig.rotate_180` defaults to `True` because the Pan-Tilt HAT's
bracket mounts the camera board upside down relative to a normal handheld
orientation. Set it to `False` if you've mounted the camera right-side up.

`ServoLimits.start_pan_deg` / `start_tilt_deg` (default `0.0`/`0.0`) set the
angle the gimbal moves to immediately on startup and returns to on shutdown.
There's no position feedback from the HAT, so the driver can't assume the
hardware is actually sitting at (0, 0) after a cold boot or power cycle —
it always explicitly commands this position rather than assuming it.
Out-of-range values are clamped to the pan/tilt min/max above.

PID gains in `config.py` are untuned starting points — expect to tune
`kp`/`ki`/`kd` per-axis against the real HAT; behavior on the mock driver
won't tell you much about real-world settle time or overshoot.

Two things debounce the tracking loop against detector jitter (a static
face's bbox still wobbles a few px frame-to-frame, which a raw P/D loop
otherwise chases as if it were real movement):

- `TrackerConfig.face_center_smoothing_alpha` — EMA smoothing on the
  detected face center before error is computed. Lower = smoother but
  slower to react to real movement; `1.0` disables it.
- `HardwareConfig.min_command_delta_deg` — the driver skips writing to a
  servo when the slew-limited target is closer than this to the last
  *committed* position, so residual sub-degree noise doesn't re-command the
  servo every tick.

These interact with `PIDGains.deadzone_px` and `kp`: for a real error to
ever clear `min_command_delta_deg`, `kp * deadzone_px` should stay above
it (the defaults — `0.045 * 8 = 0.36°` vs. a `0.3°` threshold — leave some
margin). Loosen the deadzone or the debounce threshold together if you
retune one.

## Troubleshooting

**Tracking moves the wrong way (pans/tilts away from the face instead of
toward it).** The HUD box tracks correctly but the servo direction is
backwards. Which pixel-error sign maps to which servo direction depends on
how your specific HAT is wired and mounted — it isn't derivable from the
image. Fix it with `TrackerConfig.invert_pan` / `invert_tilt` in
`config.py` (both default to `True` to match the reference hardware this
was built against):

1. Cover one eye's worth of test: stand off-center and watch which way the
   camera moves.
2. If pan moves away from you, flip `invert_pan`. If tilt moves away,
   flip `invert_tilt`. They're independent — fix one axis at a time.
3. Restart the app (or `sudo systemctl restart pantilt-face.service`) after
   each change.

**Video is upside down.** See `CameraConfig.rotate_180` above.
