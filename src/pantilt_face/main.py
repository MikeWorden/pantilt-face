"""
Entrypoint. Wires together:

  FrameGrabber (capture thread)
      -> FaceTracker.process() run in a dedicated tracking thread
      -> FrameBus (published each tick)
      -> aiohttp server (asyncio, main thread) serving /stream.mjpg

This matches the three-stage architecture in CLAUDE.md: capture, vision +
control, and the HTTP server are independent so a slow browser client can
never stall the tracking loop, and a slow detector frame never stalls
capture.

Run with:
    python -m pantilt_face.main
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time

from pantilt_face.capture.camera import FrameGrabber
from pantilt_face.config import CONFIG
from pantilt_face.control.tracker import FaceTracker
from pantilt_face.server.app import FrameBus, run_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("pantilt_face.main")


def _tracking_loop(grabber: FrameGrabber, tracker: FaceTracker, bus: FrameBus, stop: threading.Event) -> None:
    """Runs detect -> PID -> actuate -> HUD as fast as frames arrive.

    A minimum-interval guard keeps this from busy-spinning if the capture
    backend produces frames faster than the vision pipeline can consume
    them; it does not otherwise throttle -- the target-fps floor in
    CONFIG.tracker is a performance goal to benchmark against, not a cap
    enforced here.
    """
    last_processed_ts = 0.0
    while not stop.is_set():
        frame, ts = grabber.get_latest()
        if frame is None or ts == last_processed_ts:
            time.sleep(0.001)
            continue
        last_processed_ts = ts

        annotated = tracker.process(frame)
        bus.publish(annotated, tracker.metrics)

        if tracker.metrics.total_ms > CONFIG.tracker.max_capture_to_servo_ms:
            logger.debug(
                "tracking tick %.1fms exceeded %.0fms budget",
                tracker.metrics.total_ms,
                CONFIG.tracker.max_capture_to_servo_ms,
            )


async def _amain() -> None:
    grabber = FrameGrabber().start()
    tracker = FaceTracker()
    bus = FrameBus()
    stop = threading.Event()

    tracking_thread = threading.Thread(
        target=_tracking_loop, args=(grabber, tracker, bus, stop), daemon=True, name="TrackingLoop"
    )
    tracking_thread.start()

    server_task = asyncio.create_task(run_server(bus))

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass  # Windows dev fallback; Ctrl+C still raises KeyboardInterrupt

    await shutdown_event.wait()
    logger.info("Shutting down...")

    server_task.cancel()
    stop.set()
    tracking_thread.join(timeout=2.0)
    tracker.shutdown()
    grabber.stop()
    logger.info("Clean shutdown complete.")


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
