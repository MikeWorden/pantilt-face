"""
Discrete PID controller with integral anti-windup and an error deadzone.

Note on what "PID" means for a position servo: pan/tilt commands are
absolute angles, not torques, so this loop drives pixel error -> an angle
*delta* that gets added to the current commanded angle each tick. The
integral term exists to cancel steady-state offset (e.g. a slightly
miscalibrated center), not to fight gravity/load the way it would on a
torque-controlled joint -- which is why the anti-windup clamp is tight
relative to the proportional term.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from pantilt_face.config import PIDGains


@dataclass
class PIDState:
    integral: float = 0.0
    prev_error: float = 0.0
    prev_time: float | None = None


class PID:
    """One axis of PID. Call `step(error)` once per control tick."""

    def __init__(self, gains: PIDGains):
        self.gains = gains
        self.state = PIDState()

    def reset(self) -> None:
        self.state = PIDState()

    def step(self, error: float, now: float | None = None) -> float:
        """Returns the angle delta (degrees) to command this tick.

        `error` is in pixels (frame-center-relative). `now` defaults to
        time.monotonic() and is exposed as a param for deterministic tests.
        """
        g = self.gains
        now = time.monotonic() if now is None else now

        if abs(error) < g.deadzone_px:
            error = 0.0

        dt = 0.0 if self.state.prev_time is None else max(now - self.state.prev_time, 1e-6)

        self.state.integral += error * dt
        self.state.integral = max(-g.integral_limit, min(g.integral_limit, self.state.integral))

        derivative = 0.0 if dt == 0.0 else (error - self.state.prev_error) / dt

        output = g.kp * error + g.ki * self.state.integral + g.kd * derivative

        self.state.prev_error = error
        self.state.prev_time = now
        return output
