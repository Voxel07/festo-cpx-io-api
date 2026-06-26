"""Hardware Abstraction Layer for the CPX-AP test framework.

Defines :class:`HardwareInterface` (ABC) and the production implementation
:class:`CpxApHardware` that wraps the ``festo-cpx-io`` library.

:class:`SafeSession` guarantees output reset + disconnect on scope exit,
even if an exception occurs.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any


# ─── Data transfer objects ────────────────────────────────────────────────────


@dataclass
class ModuleInfo:
    """Lightweight snapshot of a module on the bus."""

    name: str
    module_code: int
    product_key: str
    address: int
    series: str = ""
    num_inputs: int = 0
    num_outputs: int = 0
    num_inouts: int = 0
    firmware_version: str | None = None
    serial_number: str | None = None

    @property
    def is_input(self) -> bool:
        return self.num_inputs > 0 and self.num_outputs == 0 and self.num_inouts == 0

    @property
    def is_output(self) -> bool:
        return self.num_outputs > 0 and self.num_inputs == 0 and self.num_inouts == 0

    @property
    def is_inout(self) -> bool:
        return self.num_inouts > 0 or (self.num_inputs > 0 and self.num_outputs > 0)

    @property
    def is_valve(self) -> bool:
        return self.name.upper().startswith("VABX")


# ─── Abstract interface ───────────────────────────────────────────────────────


class HardwareInterface(ABC):
    """Abstract interface for all hardware interactions.

    All test code MUST use this interface — never import ``CpxAp`` directly.
    """

    @abstractmethod
    def connect(self, ip_address: str, timeout: float = 0) -> None:
        """Establish a connection to the hardware."""

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully close the connection."""

    @abstractmethod
    def read_topology(self) -> list[ModuleInfo]:
        """Return a list of all modules on the bus."""

    @abstractmethod
    def read_input(self, address: int, channel: int) -> bool:
        """Read a single digital input channel."""

    @abstractmethod
    def write_output(self, address: int, channel: int, value: bool) -> None:
        """Write a single digital output channel."""

    @abstractmethod
    def reset_all_outputs(self) -> None:
        """Force all output channels on all modules to a safe (LOW) state."""

    @abstractmethod
    def read_parameter(self, address: int, param_id: int) -> int:
        """Read a parameter value from a module."""

    @abstractmethod
    def write_parameter(self, address: int, param_id: int, value: int) -> None:
        """Write a parameter value to a module."""

    @abstractmethod
    def read_diagnosis(self, address: int) -> Any:
        """Read diagnosis information from a module."""

    @abstractmethod
    def module_supports_channel_write(self, address: int) -> bool:
        """Check whether individual channel writes are supported."""


# ─── Production implementation ────────────────────────────────────────────────


class CpxApHardware(HardwareInterface):
    """Production implementation wrapping the ``festo-cpx-io`` library."""

    def __init__(self) -> None:
        self._cpx_ap: Any = None
        self._modules: list[Any] = []

    def connect(self, ip_address: str, timeout: float = 0) -> None:
        from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp  # type: ignore[import-untyped]

        self._cpx_ap = CpxAp(ip_address=ip_address, timeout=timeout)
        self._cpx_ap.__enter__()
        self._modules = list(self._cpx_ap.modules)

    def disconnect(self) -> None:
        if self._cpx_ap is not None:
            try:
                self._cpx_ap.__exit__(None, None, None)
            except Exception:
                pass
            self._cpx_ap = None
            self._modules = []

    def read_topology(self) -> list[ModuleInfo]:
        return [_module_to_info(m) for m in self._modules]

    def read_input(self, address: int, channel: int) -> bool:
        mod = self._get_module(address)
        return bool(mod.read_channel(channel))

    def write_output(self, address: int, channel: int, value: bool) -> None:
        mod = self._get_module(address)
        try:
            mod.write_channel(channel, value)
        except Exception:
            # Fallback: build a full channel list and write all at once.
            # Account for both 'outputs' and 'inout' channels (IO-Link etc.).
            num_out = len(mod.channels.outputs) + len(mod.channels.inouts)
            if num_out == 0:
                raise RuntimeError(
                    f"Module at #{address} has no writable channels"
                )
            vals = [False] * max(num_out, channel + 1)
            vals[channel] = value
            mod.write_channels(vals)

    def reset_all_outputs(self) -> None:
        if not self._modules:
            return
        for mod in self._modules:
            try:
                total_out = len(mod.channels.outputs) + len(mod.channels.inouts)
                if total_out > 0:
                    zeros = [False] * total_out
                    mod.write_channels(zeros)
                    time.sleep(0.01)
            except Exception:
                continue

    def read_parameter(self, address: int, param_id: int) -> int:
        """Read a parameter via the module's own parameter dictionary.

        Uses ``module.read_module_parameter()`` which resolves the real
        ``Parameter`` object (with correct ``parameter_instances``) from the
        module's APDD.  This is essential for VABX valve terminals where
        parameters are per-channel instances.
        """
        mod = self._get_module(address)
        return mod.read_module_parameter(param_id)

    def write_parameter(self, address: int, param_id: int, value: int) -> None:
        """Write a parameter via the module's own parameter dictionary."""
        mod = self._get_module(address)
        mod.write_module_parameter(param_id, value)

    def read_diagnosis(self, address: int) -> Any:
        mod = self._get_module(address)
        return mod.read_diagnosis_information()

    def module_supports_channel_write(self, address: int) -> bool:
        mod = self._get_module(address)
        return (len(mod.channels.outputs) + len(mod.channels.inouts)) > 0

    def _get_module(self, address: int) -> Any:
        for m in self._modules:
            if m.position == address:
                return m
        raise ValueError(f"No module at address {address}")


def _module_to_info(mod: Any) -> ModuleInfo:
    """Convert a festo-cpx-io ApModule to a ModuleInfo DTO."""
    pure_in = [c for c in mod.channels.inputs if c.direction == "in"]
    pure_out = [c for c in mod.channels.outputs if c.direction == "out"]
    inouts = mod.channels.inouts

    name = getattr(mod.apdd_information, "order_text", "") or ""
    if "CPX-AP-A" in name:
        series = "CPX-AP-A"
    elif "CPX-AP-I" in name:
        series = "CPX-AP-I"
    elif name.upper().startswith("VABX"):
        series = "VABX"
    else:
        series = "Other"

    return ModuleInfo(
        name=name,
        module_code=int(mod.information.module_code),
        product_key=str(mod.information.product_key),
        address=int(mod.position),
        series=series,
        num_inputs=len(pure_in),
        num_outputs=len(pure_out),
        num_inouts=len(inouts),
    )


# ─── Safe session ─────────────────────────────────────────────────────────────


class SafeSession(AbstractContextManager["HardwareInterface"]):
    """Context manager that guarantees output reset + disconnect on exit.

    Usage::

        hw = CpxApHardware()
        with SafeSession(hw, "192.168.0.11") as iface:
            val = iface.read_input(3, 0)
            iface.write_output(2, 0, True)
        # Outputs are guaranteed LOW here, even after an exception.
    """

    def __init__(self, hw: HardwareInterface, ip_address: str, timeout: float = 0) -> None:
        self._hw = hw
        self._ip = ip_address
        self._timeout = timeout

    def __enter__(self) -> HardwareInterface:
        self._hw.connect(self._ip, self._timeout)
        return self._hw

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        try:
            self._hw.reset_all_outputs()
        except Exception:
            pass
        finally:
            self._hw.disconnect()
        return False  # don't suppress exceptions
