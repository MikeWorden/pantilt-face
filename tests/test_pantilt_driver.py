"""Hardware-free tests: force the mock backend and verify clamping/slew."""
import pytest

from pantilt_face.config import HardwareConfig, ServoLimits
from pantilt_face.hardware.pantilt import MockPanTiltBackend, PanTiltDriver


@pytest.fixture
def driver() -> PanTiltDriver:
    return PanTiltDriver(hw=HardwareConfig(force_mock=True))


def test_uses_mock_backend_when_forced(driver: PanTiltDriver) -> None:
    assert driver.is_mock
    assert isinstance(driver._backend, MockPanTiltBackend)  # noqa: SLF001


def test_pan_clamped_to_safe_envelope(driver: PanTiltDriver) -> None:
    # Huge dt so slew limiting doesn't mask the clamp.
    pan, _ = driver.update(pan_target_deg=999, tilt_target_deg=0, dt=10.0)
    assert pan == ServoLimits().pan_max_deg

    pan, _ = driver.update(pan_target_deg=-999, tilt_target_deg=0, dt=10.0)
    assert pan == ServoLimits().pan_min_deg


def test_tilt_clamped_to_asymmetric_envelope(driver: PanTiltDriver) -> None:
    _, tilt = driver.update(pan_target_deg=0, tilt_target_deg=999, dt=10.0)
    assert tilt == ServoLimits().tilt_max_deg

    _, tilt = driver.update(pan_target_deg=0, tilt_target_deg=-999, dt=10.0)
    assert tilt == ServoLimits().tilt_min_deg


def test_slew_rate_limits_large_step(driver: PanTiltDriver) -> None:
    limits = ServoLimits()
    dt = 0.1  # 100ms tick
    pan, _ = driver.update(pan_target_deg=limits.pan_max_deg, tilt_target_deg=0, dt=dt)
    max_expected_step = limits.max_slew_deg_s * dt
    assert pan == pytest.approx(max_expected_step)
    assert pan < limits.pan_max_deg


def test_center_resets_position(driver: PanTiltDriver) -> None:
    driver.update(pan_target_deg=50, tilt_target_deg=30, dt=10.0)
    driver.center()
    assert driver.pan_deg == 0.0
    assert driver.tilt_deg == 0.0


def test_small_target_delta_is_debounced(driver: PanTiltDriver) -> None:
    # Default min_command_delta_deg is 0.3; a 0.2deg request should be
    # swallowed rather than sent to the servo.
    pan, _ = driver.update(pan_target_deg=0.2, tilt_target_deg=0.0, dt=10.0)
    assert pan == 0.0
    assert driver._backend.last_pan == 0.0  # noqa: SLF001 - mock introspection


def test_target_delta_crossing_threshold_commits(driver: PanTiltDriver) -> None:
    # A below-threshold request first (debounced, position unchanged), then
    # a request large enough to clear the threshold against that same
    # unmoved baseline -- it should commit for the full amount, not just
    # the increment since the last request.
    driver.update(pan_target_deg=0.2, tilt_target_deg=0.0, dt=10.0)
    pan, _ = driver.update(pan_target_deg=1.0, tilt_target_deg=0.0, dt=10.0)
    assert pan == pytest.approx(1.0)
    assert driver._backend.last_pan == pytest.approx(1.0)  # noqa: SLF001


def test_axes_are_debounced_independently(driver: PanTiltDriver) -> None:
    pan, tilt = driver.update(pan_target_deg=10.0, tilt_target_deg=0.1, dt=10.0)
    assert pan == pytest.approx(10.0)  # well above threshold -> commits
    assert tilt == 0.0  # below threshold -> stays debounced
