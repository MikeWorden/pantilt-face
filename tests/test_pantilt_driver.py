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


def test_default_start_position_is_zero_zero(driver: PanTiltDriver) -> None:
    assert driver.pan_deg == 0.0
    assert driver.tilt_deg == 0.0
    assert driver._backend.last_pan == 0.0  # noqa: SLF001
    assert driver._backend.last_tilt == 0.0  # noqa: SLF001


def test_custom_start_position_applied_on_init() -> None:
    limits = ServoLimits(start_pan_deg=20.0, start_tilt_deg=-10.0)
    d = PanTiltDriver(limits=limits, hw=HardwareConfig(force_mock=True))
    assert d.pan_deg == pytest.approx(20.0)
    assert d.tilt_deg == pytest.approx(-10.0)
    # Actually written to hardware, not just tracked internally.
    assert d._backend.last_pan == pytest.approx(20.0)  # noqa: SLF001
    assert d._backend.last_tilt == pytest.approx(-10.0)  # noqa: SLF001


def test_start_position_clamped_to_safe_envelope() -> None:
    limits = ServoLimits(start_pan_deg=999.0, start_tilt_deg=-999.0)
    d = PanTiltDriver(limits=limits, hw=HardwareConfig(force_mock=True))
    assert d.pan_deg == limits.pan_max_deg
    assert d.tilt_deg == limits.tilt_min_deg


def test_set_led_reaches_mock_backend(driver: PanTiltDriver) -> None:
    driver.set_led(0, 255, 0)
    assert driver._backend.last_led_rgb == (0, 255, 0)  # noqa: SLF001


def test_led_off_sets_black(driver: PanTiltDriver) -> None:
    driver.set_led(10, 20, 30)
    driver.led_off()
    assert driver._backend.last_led_rgb == (0, 0, 0)  # noqa: SLF001


def test_led_unsupported_backend_degrades_without_raising() -> None:
    class NoLedBackend:
        def __init__(self) -> None:
            self.last_pan = 0.0
            self.last_tilt = 0.0

        def pan(self, angle: int) -> None:
            self.last_pan = float(angle)

        def tilt(self, angle: int) -> None:
            self.last_tilt = float(angle)

    d = PanTiltDriver(hw=HardwareConfig(force_mock=True))
    d._backend = NoLedBackend()  # noqa: SLF001 - simulate an older/bare board
    d.set_led(255, 0, 0)  # must not raise
    assert d._led_unsupported  # noqa: SLF001


def test_center_returns_to_configured_start_not_hardcoded_zero() -> None:
    limits = ServoLimits(start_pan_deg=15.0, start_tilt_deg=5.0)
    d = PanTiltDriver(limits=limits, hw=HardwareConfig(force_mock=True))
    d.update(pan_target_deg=-50, tilt_target_deg=-30, dt=10.0)
    d.center()
    assert d.pan_deg == pytest.approx(15.0)
    assert d.tilt_deg == pytest.approx(5.0)
