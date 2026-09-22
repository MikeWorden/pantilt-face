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

PID gains in `config.py` are untuned starting points — expect to tune
`kp`/`ki`/`kd` per-axis against the real HAT; behavior on the mock driver
won't tell you much about real-world settle time or overshoot.
