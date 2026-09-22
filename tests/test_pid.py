import pytest

from pantilt_face.config import PIDGains
from pantilt_face.control.pid import PID


def test_deadzone_suppresses_small_error() -> None:
    pid = PID(PIDGains(kp=1.0, ki=0.0, kd=0.0, deadzone_px=10.0))
    out = pid.step(error=5.0, now=0.0)
    assert out == 0.0


def test_proportional_response_outside_deadzone() -> None:
    pid = PID(PIDGains(kp=0.5, ki=0.0, kd=0.0, deadzone_px=1.0))
    out = pid.step(error=20.0, now=0.0)
    assert out == 10.0


def test_integral_anti_windup_clamps() -> None:
    gains = PIDGains(kp=0.0, ki=1.0, kd=0.0, deadzone_px=0.0, integral_limit=5.0)
    pid = PID(gains)
    pid.step(error=100.0, now=0.0)
    out = pid.step(error=100.0, now=1.0)  # dt=1s -> integral would be 200 without clamp
    assert out == pytest.approx(5.0)


def test_derivative_reacts_to_error_change() -> None:
    gains = PIDGains(kp=0.0, ki=0.0, kd=1.0, deadzone_px=0.0)
    pid = PID(gains)
    pid.step(error=0.0, now=0.0)
    out = pid.step(error=10.0, now=1.0)  # d(error)/dt = 10
    assert out == pytest.approx(10.0)


def test_first_call_has_zero_derivative_and_integral_contribution() -> None:
    gains = PIDGains(kp=1.0, ki=1.0, kd=1.0, deadzone_px=0.0)
    pid = PID(gains)
    out = pid.step(error=10.0, now=0.0)
    # dt undefined on first call -> only proportional term contributes
    assert out == pytest.approx(10.0)
