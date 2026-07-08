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
import json
import socket
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
    """Return ``True`` if the HMP40x0 driver was loaded successfully or IP address is used."""
    return _hmp40x0_class is not None


def _is_ip_address(val: str) -> bool:
    if not val:
        return False
    parts = val.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return True
    return False


class HMP40x0TCP:
    """SCPI communication wrapper for HMP40x0 power supply over TCP/IP socket."""

    def __init__(self, ip_address: str, port: int = 5025) -> None:
        self.ip_address = ip_address
        self.port = port
        self.sock: socket.socket | None = None

    def connect(self) -> None:
        try:
            self.sock = socket.create_connection((self.ip_address, self.port), timeout=5.0)
        except Exception as e:
            raise RuntimeError(f"Could not connect to power supply at {self.ip_address}:{self.port}: {e}")

    def disconnect(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def set_voltage_list(self, channels: list[int], voltage: float) -> None:
        if self.sock is None:
            raise RuntimeError("Not connected")
        for ch in channels:
            # SCPI protocol for Rohde & Schwarz HMP40x0 series:
            # Select channel, set voltage, and enable/disable that channel
            self.sock.sendall(f"INST:NSEL {ch}\n".encode("utf-8"))
            self.sock.sendall(f"VOLT {voltage}\n".encode("utf-8"))
            if voltage > 0:
                self.sock.sendall(b"OUTP:SEL ON\n")
            else:
                self.sock.sendall(b"OUTP:SEL OFF\n")
        # Global output control (Master Output)
        if voltage > 0:
            self.sock.sendall(b"OUTP:GEN ON\n")


class PowerCycleSession:
    """Context manager for HMP40x0 power-cycle operations.

    Opens the serial/IP connection on ``__enter__`` and closes it on ``__exit__``.

    Args:
        comport:         Serial port, e.g. ``"COM3"`` or ``"/dev/ttyUSB0"``.
        channels:        List of HMP output channels to switch (1-based).
        voltage:         Operating voltage to restore after power-off (V).
        off_time:        Seconds to keep the power off.
        reconnect_wait:  Seconds to wait after power-on before the test
                         continues (allows the CPX-AP to finish booting).
        ip_address:      IP address of the power supply.

    Raises:
        PowerSupplyNotAvailable: If the HMP40x0 driver could not be imported and serial connection is selected.
        ConnectionError: If the power supply connection fails.

    Example::

        with PowerCycleSession("COM3", channels=[1, 2, 4], voltage=24.0) as ps:
            ps.cycle(hw, ip_address="192.168.1.10")
    """

    def __init__(
        self,
        comport: str | None = None,
        channels: list[int] | None = None,
        voltage: float = 24.0,
        off_time: float = DEFAULT_OFF_TIME,
        reconnect_wait: float = DEFAULT_RECONNECT_WAIT,
        ip_address: str | None = None,
    ) -> None:
        # Try to load configuration from bench_config.json
        ps_config = {}
        for p in (Path("bench_config.json"), Path(__file__).resolve().parent / "bench_config.json"):
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        ps_config = data.get("power_supply", {})
                        if ps_config:
                            break
                except Exception:
                    pass

        resolved_comport = comport or ps_config.get("ComPort")
        resolved_ip = ip_address or ps_config.get("Ip addr")

        # Swap if comport was passed but contains IP
        if resolved_comport and _is_ip_address(resolved_comport):
            resolved_ip = resolved_comport
            resolved_comport = None

        if resolved_comport and resolved_ip:
            raise ValueError(
                "Invalid power supply configuration: both 'ComPort' and 'Ip addr' are configured. "
                "Only one should be selected."
            )
        if not resolved_comport and not resolved_ip:
            raise ValueError(
                "Invalid power supply configuration: neither 'ComPort' nor 'Ip addr' is configured."
            )

        self._comport = resolved_comport
        self._ip_address = resolved_ip

        # Resolve channels
        if channels is not None:
            self._channels = channels
        else:
            pl_ch = ps_config.get("pl_channel")
            ps_ch = ps_config.get("ps_channel")
            self._channels = []
            if pl_ch is not None:
                self._channels.append(int(pl_ch))
            if ps_ch is not None:
                self._channels.append(int(ps_ch))
            if not self._channels:
                self._channels = [1, 2, 4]

        self._voltage = voltage
        self._off_time = off_time
        self._reconnect_wait = reconnect_wait
        self._ps: object | None = None

    def __enter__(self) -> "PowerCycleSession":
        if self._comport:
            if _hmp40x0_class is None:
                raise PowerSupplyNotAvailable(
                    "HMP40x0 driver not available. "
                    "Ensure pyserial is installed and the testing-lib repo exists at "
                    "../testing-lib/ap_testing_lib/power_supply/hmp40x0.py"
                )
            self._ps = _hmp40x0_class(self._comport)  # type: ignore[misc]
        else:
            self._ps = HMP40x0TCP(self._ip_address)

        try:
            self._ps.connect()  # type: ignore[union-attr]
        except Exception as exc:
            raise ConnectionError(f"Failed to connect to power supply: {exc}")

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
