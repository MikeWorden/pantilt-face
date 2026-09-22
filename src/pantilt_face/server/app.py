"""
aiohttp server exposing:
  GET /            -- index.html UI (video + live metrics)
  GET /stream.mjpg -- multipart/x-mixed-replace MJPEG stream
  GET /metrics     -- JSON snapshot of the latest TrackerMetrics
  GET /healthz     -- liveness check

Reads frames from a `FrameBus`, an atomically-swapped single-slot buffer
that the tracking loop publishes into every tick. The server never blocks
the tracking loop and never queues frames -- a slow browser client just
gets the next `get()` a bit later, per the "atomic frame lock" design in
CLAUDE.md.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from aiohttp import web

from pantilt_face.config import CONFIG, ServerConfig
from pantilt_face.control.tracker import TrackerMetrics

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


class FrameBus:
    """Thread-safe single-slot latest-frame buffer, shared between the
    tracking thread (producer) and the aiohttp server (consumer)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame_rgb: Optional[np.ndarray] = None
        self._metrics: TrackerMetrics = TrackerMetrics()

    def publish(self, frame_rgb: np.ndarray, metrics: TrackerMetrics) -> None:
        with self._lock:
            self._frame_rgb = frame_rgb
            self._metrics = metrics

    def get(self) -> tuple[Optional[np.ndarray], TrackerMetrics]:
        with self._lock:
            return self._frame_rgb, self._metrics


def build_app(bus: FrameBus, cfg: ServerConfig = CONFIG.server) -> web.Application:
    app = web.Application()
    app["bus"] = bus
    app["cfg"] = cfg
    app.router.add_get("/", handle_index)
    app.router.add_get("/stream.mjpg", handle_stream)
    app.router.add_get("/metrics", handle_metrics)
    app.router.add_get("/healthz", handle_health)
    return app


async def handle_index(request: web.Request) -> web.Response:
    html_path = TEMPLATES_DIR / "index.html"
    return web.Response(text=html_path.read_text(), content_type="text/html")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_metrics(request: web.Request) -> web.Response:
    bus: FrameBus = request.app["bus"]
    _, metrics = bus.get()
    return web.json_response(
        {
            "face_found": metrics.face_found,
            "error_x_px": metrics.error_x_px,
            "error_y_px": metrics.error_y_px,
            "pan_deg": metrics.pan_deg,
            "tilt_deg": metrics.tilt_deg,
            "detect_ms": metrics.detect_ms,
            "total_ms": metrics.total_ms,
            "fps": metrics.fps,
            "backend": metrics.backend,
            "age_s": time.monotonic() - metrics.last_updated,
        }
    )


async def handle_stream(request: web.Request) -> web.StreamResponse:
    cfg: ServerConfig = request.app["cfg"]
    bus: FrameBus = request.app["bus"]

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={cfg.boundary}",
            "Cache-Control": "no-cache, private",
            "Pragma": "no-cache",
        },
    )
    await response.prepare(request)

    last_sent_ts = 0.0
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, cfg.jpeg_quality]

    try:
        while True:
            frame_rgb, metrics = bus.get()
            if frame_rgb is not None and metrics.last_updated != last_sent_ts:
                last_sent_ts = metrics.last_updated
                frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                ok, jpg = cv2.imencode(".jpg", frame_bgr, encode_params)
                if ok:
                    payload = jpg.tobytes()
                    await response.write(
                        f"--{cfg.boundary}\r\n"
                        f"Content-Type: image/jpeg\r\n"
                        f"Content-Length: {len(payload)}\r\n\r\n".encode()
                        + payload
                        + b"\r\n"
                    )
            await asyncio.sleep(1 / 60)  # poll rate; independent of tracking-loop fps
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        return response


async def run_server(bus: FrameBus, cfg: ServerConfig = CONFIG.server) -> None:
    app = build_app(bus, cfg)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    logger.info("MJPEG server listening on http://%s:%d", cfg.host, cfg.port)
    while True:
        await asyncio.sleep(3600)
