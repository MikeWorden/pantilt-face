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
