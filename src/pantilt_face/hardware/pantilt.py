"""
Pan-Tilt HAT driver wrapper.

Wraps Pimoroni's `pantilthat` (I2C bus 1, address 0x15) with:
  - auto-mocking when the real library or I2C bus isn't available (e.g.
    developing off-Pi), so the rest of the stack never has to special-case it
  - retry-with-backoff around I2C writes, which are the flakiest part of this
    stack on a Pi under load
  - hard clamping to the mechanical safe envelope
  - slew-rate limiting so the PID loop can never demand a faster sweep than
    the gears (and the Pi's power supply) can tolerate
  - best-effort control of the HAT's onboard RGB LED (SN3218-driven), which
    degrades to a no-op if the installed pantilthat/board doesn't expose it

Import `PanTiltDriver` and call `.update(pan_deg, tilt_deg, dt)` once per
control-loop tick; it internally rate-limits and clamps before touching
hardware. Call `.set_led(r, g, b)` / `.led_off()` for the status LED.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

from pantilt_face.config import CONFIG, HardwareConfig, ServoLimits

logger = logging.getLogger(__name__)


class _PanTiltBackend(Protocol):
    def pan(self, angle: int) -> None: ...
    def tilt(self, angle: int) -> None: ...
    # LED control (SN3218-driven onboard RGB LED). Not declared as required
    # here since it's probed with hasattr() at call time -- older pantilthat
    # versions or bare boards without the LED wired may not expose it.
    def set_all(self, r: int, g: int, b: int) -> None: ...
    def show(self) -> None: ...


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _load_real_backend(hw: HardwareConfig) -> Optional[_PanTiltBackend]:
    """Try to import and initialize the real pantilthat library.

    Returns None on any failure (missing module, no I2C bus, wrong
    permissions) so the caller can fall back to the mock transparently.
    """
    if hw.force_mock:
        return None
    try:
        import pantilthat  # type: ignore

        # Touching the bus here (rather than lazily) surfaces I2C failures
        # immediately at startup instead of on the first tracking frame.
        pantilthat.pan(0)
        pantilthat.tilt(0)
        logger.info(
            "pantilthat initialized on I2C bus %d @ 0x%02x",
            hw.i2c_bus,
            hw.i2c_address,
        )
        return pantilthat
    except Exception as exc:  # noqa: BLE001 - any import/hw failure -> mock
        logger.warning(
            "pantilthat unavailable (%s); falling back to MockPanTiltBackend. "
            "This is expected when developing off the Raspberry Pi.",
            exc,
        )
        return None


class MockPanTiltBackend:
    """Software stand-in for the HAT. Logs commands, tracks last angles.

    Used automatically whenever the real hardware/library can't be reached,
    and can be forced via HardwareConfig.force_mock for bench testing.
    """

    def __init__(self) -> None:
        self.last_pan: float = 0.0
        self.last_tilt: float = 0.0
        self.last_led_rgb: tuple[int, int, int] = (0, 0, 0)

    def pan(self, angle: int) -> None:
        self.last_pan = float(angle)
        logger.debug("[MOCK] pan -> %s deg", angle)

    def tilt(self, angle: int) -> None:
        self.last_tilt = float(angle)
        logger.debug("[MOCK] tilt -> %s deg", angle)

    def set_all(self, r: int, g: int, b: int) -> None:
        self.last_led_rgb = (r, g, b)
        logger.debug("[MOCK] LED set_all -> rgb(%d, %d, %d)", r, g, b)

    def show(self) -> None:
        logger.debug("[MOCK] LED show()")


class PanTiltDriver:
    """Safe, rate-limited, retrying interface to the Pan-Tilt HAT."""

    def __init__(
        self,
        limits: ServoLimits = CONFIG.servo_limits,
        hw: HardwareConfig = CONFIG.hardware,
    ) -> None:
        self.limits = limits
        self.hw = hw
        self._backend = _load_real_backend(hw) or MockPanTiltBackend()
        self.is_mock = isinstance(self._backend, MockPanTiltBackend)

        self._pan_deg = 0.0
        self._tilt_deg = 0.0
        self._led_unsupported = False
        self.center()  # snap to the configured start position immediately

    @property
    def pan_deg(self) -> float:
        return self._pan_deg

    @property
    def tilt_deg(self) -> float:
        return self._tilt_deg

    def center(self) -> None:
        """Move immediately to ServoLimits.start_pan_deg/start_tilt_deg
        (0,0 by default), bypassing slew and debounce. Called on startup
        and on shutdown, so the gimbal has a known, deliberate resting pose
        rather than whatever the hardware happened to be left at."""
        self._pan_deg = _clamp(self.limits.start_pan_deg, self.limits.pan_min_deg, self.limits.pan_max_deg)
        self._tilt_deg = _clamp(self.limits.start_tilt_deg, self.limits.tilt_min_deg, self.limits.tilt_max_deg)
        self._write(self._pan_deg, self._tilt_deg)
        logger.info("Pan-Tilt moved to home position: pan=%.1f tilt=%.1f", self._pan_deg, self._tilt_deg)

    def update(self, pan_target_deg: float, tilt_target_deg: float, dt: float) -> tuple[float, float]:
        """Move toward (pan_target_deg, tilt_target_deg), respecting slew and clamps.

        `dt` is the wall-clock seconds since the previous call. Returns the
        actual (pan, tilt) commanded after rate limiting and debouncing,
        which the caller should feed back into the PID loop as the true
        current position.

        Each axis is debounced independently against
        `HardwareConfig.min_command_delta_deg`: if the slew-limited target
        is within that of the last *committed* position, the write (and the
        position update) is skipped rather than sent. This isn't lost --
        small residual deltas keep accumulating against that same
        last-committed baseline next tick, so real movement still gets
        through once it adds up past the threshold; it just stops the servo
        re-commanding itself every tick for noise that nets out to nothing.
        """
        pan_target_deg = _clamp(pan_target_deg, self.limits.pan_min_deg, self.limits.pan_max_deg)
        tilt_target_deg = _clamp(tilt_target_deg, self.limits.tilt_min_deg, self.limits.tilt_max_deg)

        max_step = self.limits.max_slew_deg_s * max(dt, 0.0)
        candidate_pan = self._pan_deg + _clamp(pan_target_deg - self._pan_deg, -max_step, max_step)
        candidate_tilt = self._tilt_deg + _clamp(tilt_target_deg - self._tilt_deg, -max_step, max_step)

        min_delta = self.hw.min_command_delta_deg
        if abs(candidate_pan - self._pan_deg) >= min_delta:
            self._retrying_call(self._backend.pan, int(round(candidate_pan)))
            self._pan_deg = candidate_pan
        if abs(candidate_tilt - self._tilt_deg) >= min_delta:
            self._retrying_call(self._backend.tilt, int(round(candidate_tilt)))
            self._tilt_deg = candidate_tilt

        return self._pan_deg, self._tilt_deg

    def set_led(self, r: int, g: int, b: int) -> None:
        """Best-effort onboard RGB LED control.

        Not every pantilthat version/board revision exposes set_all()/
        show() (older library, or a bare board with the LED pads unused),
        so this is probed once with hasattr() rather than assumed -- an
        unsupported board logs a single warning and then no-ops silently
        instead of retrying a call that will never succeed. Real transient
        I2C failures still go through the normal retry-with-backoff path.
        """
        if self._led_unsupported:
            return
        if not (hasattr(self._backend, "set_all") and hasattr(self._backend, "show")):
            logger.warning(
                "This pantilthat install/board doesn't expose set_all()/show() "
                "for LED control; the face-found indicator will be HUD-only."
            )
            self._led_unsupported = True
            return
        self._retrying_call(self._backend.set_all, r, g, b)
        self._retrying_call(self._backend.show)

    def led_off(self) -> None:
        self.set_led(0, 0, 0)

    def _write(self, pan_deg: float, tilt_deg: float) -> None:
        pan_i = int(round(pan_deg))
        tilt_i = int(round(tilt_deg))
        self._retrying_call(self._backend.pan, pan_i)
        self._retrying_call(self._backend.tilt, tilt_i)

    def _retrying_call(self, fn, *args) -> None:
        attempts = max(1, self.hw.i2c_retries)
        for attempt in range(1, attempts + 1):
            try:
                fn(*args)
                return
            except Exception as exc:  # noqa: BLE001 - I2C bus errors are opaque
                if attempt == attempts:
                    logger.error(
                        "I2C write failed after %d attempts (%s); dropping this command",
                        attempts,
                        exc,
                    )
                    return
                backoff = self.hw.i2c_retry_backoff_s * attempt
                logger.warning(
                    "I2C write failed (attempt %d/%d): %s; retrying in %.3fs",
                    attempt,
                    attempts,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
