"""Power supply control for hardware-in-the-loop tests.

Wraps the HMP40x0 serial power supply driver sourced from the sibling
``testing-lib`` repo (``../testing-lib/ap_testing_lib/power_supply/hmp40x0.py``).

The import is done lazily via ``importlib`` so the full ``ap_testing_lib``
package (which requires proprietary ``engt`` dependencies) is never loaded.
Only ``pyserial`` must be installed in the current environment.

Typical usage::

    from power_supply import PowerCycleSession, is_available

    if not is_available():
        pytest.skip("Power supply library not available")

    with PowerCycleSession("COM3", channels=[1, 2, 4], voltage=24.0) as ps:
        ps.cycle(hw, ip_address="192.168.1.10")
"""
from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hal import HardwareInterface

# ── Lazy import of hmp40x0 from the sibling testing-lib ───────────────────────

_hmp40x0_class: type | None = None


def _try_load_hmp40x0() -> type | None:
    """Load ``hmp40x0`` from the sibling testing-lib without triggering the
    full ``ap_testing_lib`` package init (which requires proprietary deps)."""
    lib_root = Path(__file__).resolve().parent.parent / "testing-lib"
    ps_dir = lib_root / "ap_testing_lib" / "power_supply"
    if not ps_dir.exists():
        return None

    try:
        import serial  # noqa: F401 — pyserial must be installed
    except ImportError:
        return None

    try:
        # 1. Create stub package entries so relative imports inside the
        #    loaded files resolve correctly without loading the full package.
        for pkg_name in ("ap_testing_lib", "ap_testing_lib.power_supply"):
            if pkg_name not in sys.modules:
                stub = types.ModuleType(pkg_name)
                stub.__path__ = [str(ps_dir.parent if pkg_name == "ap_testing_lib" else ps_dir)]
                stub.__package__ = pkg_name
                sys.modules[pkg_name] = stub

        # 2. Load power_source (only exception classes, no external deps).
        ps_fqn = "ap_testing_lib.power_supply.power_source"
        if ps_fqn not in sys.modules:
            spec = importlib.util.spec_from_file_location(ps_fqn, ps_dir / "power_source.py")
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            mod.__package__ = "ap_testing_lib.power_supply"
            sys.modules[ps_fqn] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            # Attach to the stub package so "from . import power_source" works.
            sys.modules["ap_testing_lib.power_supply"].power_source = mod  # type: ignore[attr-defined]

        # 3. Load hmp40x0 with package context set.
        hmp_fqn = "ap_testing_lib.power_supply.hmp40x0"
        if hmp_fqn not in sys.modules:
            spec = importlib.util.spec_from_file_location(hmp_fqn, ps_dir / "hmp40x0.py")
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            mod.__package__ = "ap_testing_lib.power_supply"
            sys.modules[hmp_fqn] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]

        return sys.modules[hmp_fqn].hmp40x0  # type: ignore[attr-defined]
    except Exception:
        return None


_hmp40x0_class = _try_load_hmp40x0()


# ── Public API ─────────────────────────────────────────────────────────────────

#: How long to keep the power off during a cycle (seconds).
DEFAULT_OFF_TIME: float = 1.0

#: How long to wait after power-on for the CPX-AP system to restart (seconds).
DEFAULT_RECONNECT_WAIT: float = 8.0


class PowerSupplyNotAvailable(RuntimeError):
    """Raised when the HMP40x0 library cannot be imported."""


def is_available() -> bool:
    """Return ``True`` if the HMP40x0 driver was loaded successfully."""
    return _hmp40x0_class is not None


class PowerCycleSession:
    """Context manager for HMP40x0 power-cycle operations.

    Opens the serial connection on ``__enter__`` and closes it on ``__exit__``.

    Args:
        comport:         Serial port, e.g. ``"COM3"`` or ``"/dev/ttyUSB0"``.
        channels:        List of HMP output channels to switch (1-based).
        voltage:         Operating voltage to restore after power-off (V).
        off_time:        Seconds to keep the power off.
        reconnect_wait:  Seconds to wait after power-on before the test
                         continues (allows the CPX-AP to finish booting).

    Raises:
        PowerSupplyNotAvailable: If the HMP40x0 driver could not be imported.

    Example::

        with PowerCycleSession("COM3", channels=[1, 2, 4], voltage=24.0) as ps:
            ps.cycle(hw, ip_address="192.168.1.10")
    """

    def __init__(
        self,
        comport: str,
        channels: list[int],
        voltage: float = 24.0,
        off_time: float = DEFAULT_OFF_TIME,
        reconnect_wait: float = DEFAULT_RECONNECT_WAIT,
    ) -> None:
        if _hmp40x0_class is None:
            raise PowerSupplyNotAvailable(
                "HMP40x0 driver not available. "
                "Ensure pyserial is installed and the testing-lib repo exists at "
                "../testing-lib/ap_testing_lib/power_supply/hmp40x0.py"
            )
        self._comport = comport
        self._channels = channels
        self._voltage = voltage
        self._off_time = off_time
        self._reconnect_wait = reconnect_wait
        self._ps: object | None = None

    def __enter__(self) -> "PowerCycleSession":
        self._ps = _hmp40x0_class(self._comport)  # type: ignore[misc]
        self._ps.connect()  # type: ignore[union-attr]
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if self._ps is not None:
            try:
                self._ps.disconnect()  # type: ignore[union-attr]
            except Exception:
                pass
            self._ps = None
        return False

    def cycle(
        self,
        hw: "HardwareInterface",
        ip_address: str,
        timeout: float = 0,
    ) -> None:
        """Power off → wait → power on → wait → reconnect *hw*.

        After this call, *hw* is reconnected and ready for use.

        Args:
            hw:          The :class:`~hal.HardwareInterface` instance to
                         reconnect after the power cycle.
            ip_address:  IP address passed to ``hw.connect()``.
            timeout:     Optional connection timeout forwarded to ``hw.connect()``.
        """
        if self._ps is None:
            raise RuntimeError("PowerCycleSession must be used as a context manager")

        # Disconnect the HAL before cutting power so no pending Modbus frames
        # are left on the wire.
        try:
            hw.disconnect()
        except Exception:
            pass

        # Cut power
        self._ps.set_voltage_list(self._channels, 0)  # type: ignore[union-attr]
        time.sleep(self._off_time)

        # Restore power
        self._ps.set_voltage_list(self._channels, self._voltage)  # type: ignore[union-attr]
        time.sleep(self._reconnect_wait)

        # Reconnect the HAL
        hw.connect(ip_address, timeout)
