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

Import `PanTiltDriver` and call `.update(pan_deg, tilt_deg, dt)` once per
control-loop tick; it internally rate-limits and clamps before touching
hardware.
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

    def pan(self, angle: int) -> None:
        self.last_pan = float(angle)
        logger.debug("[MOCK] pan -> %s deg", angle)

    def tilt(self, angle: int) -> None:
        self.last_tilt = float(angle)
        logger.debug("[MOCK] tilt -> %s deg", angle)


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

    @property
    def pan_deg(self) -> float:
        return self._pan_deg

    @property
    def tilt_deg(self) -> float:
        return self._tilt_deg

    def center(self) -> None:
        self._pan_deg = 0.0
        self._tilt_deg = 0.0
        self._write(0.0, 0.0)

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

    def _write(self, pan_deg: float, tilt_deg: float) -> None:
        pan_i = int(round(pan_deg))
        tilt_i = int(round(tilt_deg))
        self._retrying_call(self._backend.pan, pan_i)
        self._retrying_call(self._backend.tilt, tilt_i)

    def _retrying_call(self, fn, value: int) -> None:
        attempts = max(1, self.hw.i2c_retries)
        for attempt in range(1, attempts + 1):
            try:
                fn(value)
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
