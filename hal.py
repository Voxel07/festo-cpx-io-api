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

    def reconnect(self, ip_address: str, timeout: float = 0) -> None:
        """Disconnect and reconnect.  Convenience wrapper around
        :meth:`disconnect` + :meth:`connect`."""
        self.disconnect()
        self.connect(ip_address, timeout)

    def reset_device(
        self,
        address: int,
        factory_reset: bool = False,
        device_reset_param_id: int | None = None,
    ) -> None:
        """Trigger a device reset via a parameter write.

        After the write the module restarts and the Modbus connection is
        broken.  Callers are responsible for calling
        :meth:`reconnect` / :meth:`connect` afterwards.

        The default implementation raises :exc:`NotImplementedError`;
        override in subclasses that support this operation.

        Args:
            address:              Bus address of the module to reset.
            factory_reset:        ``True`` → factory reset (clears user
                                  parameters); ``False`` → warm restart
                                  (parameters are preserved).
            device_reset_param_id: Parameter ID to write the reset command
                                  to.  ``None`` → use the subclass default.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement reset_device. "
            "Provide a concrete subclass or use a power-cycle instead."
        )


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

    def reset_device(
        self,
        address: int,
        factory_reset: bool = False,
        device_reset_param_id: int | None = None,
    ) -> None:
        """Trigger a device reset by writing the AP device-reset parameter.

        AP standard reset-command values:
          - ``0x5761`` — warm restart (user parameters are preserved)
          - ``0x4B6C`` — factory reset (all user parameters cleared)

        After writing, the module restarts and the Modbus connection is
        broken.  Call :meth:`reconnect` / :meth:`connect` after the
        appropriate startup delay.

        Args:
            address:              Bus address of the module to reset.
            factory_reset:        ``True`` for factory reset, ``False`` for
                                  warm restart.
            device_reset_param_id: Parameter ID to write the reset command.
                                  Defaults to ``20001`` (AP DeviceReset param).
        """
        param_id = device_reset_param_id if device_reset_param_id is not None else 20001
        # AP-standard reset command values
        value = 0x4B6C if factory_reset else 0x5761
        mod = self._get_module(address)
        try:
            mod.write_module_parameter(param_id, value)
        except Exception:
            # The device restarts immediately; the connection is expected to
            # break here — suppress the resulting Modbus error.
            pass

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


# ─── Cross-Process Lock ────────────────────────────────────────────────────────

class CrossProcessLock:
    """A cross-process file-based lock to prevent concurrent hardware access."""

    def __init__(self, ip_address: str) -> None:
        import tempfile
        from pathlib import Path
        safe_ip = ip_address.replace(".", "_").replace(":", "_")
        self.lock_file = Path(tempfile.gettempdir()) / f"festo_bench_{safe_ip}.lock"
        self.is_locked = False

    def acquire(self, timeout: float = 60.0, poll_interval: float = 0.5) -> None:
        import os
        import time
        start_time = time.time()
        pid = os.getpid()
        while True:
            try:
                # Attempt atomic lock file creation
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(f"{pid}\n{time.time()}\n")
                self.is_locked = True
                return
            except FileExistsError:
                # File exists, check if process is alive or lock is stale
                try:
                    with open(self.lock_file, "r") as f:
                        lines = f.readlines()
                    lock_pid = int(lines[0].strip())
                    lock_time = float(lines[1].strip())
                except (IndexError, ValueError, OSError):
                    # Corrupt or unreadable file — break lock
                    self._force_release()
                    continue

                # Check if process is alive (works on Unix & Windows python 3.2+)
                process_alive = True
                try:
                    os.kill(lock_pid, 0)
                except OSError:
                    process_alive = False

                # If the lock is held by our own process or our parent (uvicorn manager),
                # it's a stale lock from a previous reload/worker run.
                is_own_chain = (lock_pid == os.getpid() or (hasattr(os, "getppid") and lock_pid == os.getppid()))

                # Stale check: 30 minutes
                is_stale = (time.time() - lock_time) > 1800

                if not process_alive or is_stale or is_own_chain:
                    self._force_release()
                    continue

                if time.time() - start_time > timeout:
                    raise TimeoutError(
                        f"Timeout waiting for hardware lock on bench at '{self.lock_file.name}'. "
                        f"Locked by PID {lock_pid}."
                    )
                time.sleep(poll_interval)

    def release(self) -> None:
        if self.is_locked:
            self._force_release()
            self.is_locked = False

    def _force_release(self) -> None:
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except OSError:
            pass


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
        self._lock = CrossProcessLock(ip_address)

    def __enter__(self) -> HardwareInterface:
        self._lock.acquire(timeout=60.0)
        try:
            self._hw.connect(self._ip, self._timeout)
            # Guarantee safe start state: reset all outputs to LOW on connect.
            # A previous crash may have left outputs HIGH.
            try:
                self._hw.reset_all_outputs()
            except Exception:
                pass  # Best-effort — some modules may not have outputs
            return self._hw
        except Exception:
            self._lock.release()
            raise

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
            try:
                self._hw.disconnect()
            except Exception:
                pass
            self._lock.release()
        return False  # don't suppress exceptions
