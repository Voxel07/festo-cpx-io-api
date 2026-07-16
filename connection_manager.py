"""Shared hardware connection manager for the CPX-AP API.

Provides a module-level singleton that all API endpoints share so the
Modbus connection is established once by the user (via the UI) and
reused by all operations.

Usage::

    from connection_manager import ConnectionManager
    mgr = ConnectionManager()

    # Called by /hw/connect endpoint
    mgr.connect("192.168.0.11", timeout=5.0)

    # Called by /hw/disconnect endpoint
    mgr.disconnect()

    # Used by all other endpoints
    hw = mgr.get_hw()  # raises if not connected
"""

from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from hal import CpxApHardware, HardwareInterface


@dataclass(frozen=True)
class ConnectionSettings:
    """Immutable settings needed to restore an interactive connection."""

    ip_address: str
    timeout: float


class ConnectionManager:
    """Thread-safe singleton that manages a single :class:`CpxApHardware` instance.

    All endpoints share this connection — only one Modbus session is active
    at a time per process.
    """

    def __init__(self) -> None:
        self._hw: CpxApHardware | None = None
        self._lock = threading.Lock()
        self._ip: str = ""
        self._timeout: float = 0.0

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._hw is not None

    @property
    def ip_address(self) -> str:
        with self._lock:
            return self._ip

    @property
    def timeout(self) -> float:
        with self._lock:
            return self._timeout

    def connection_settings(self) -> ConnectionSettings | None:
        """Atomically snapshot the active interactive connection, if any."""
        with self._lock:
            if self._hw is None:
                return None
            return ConnectionSettings(self._ip, self._timeout)

    def connect(self, ip_address: str, timeout: float = 0) -> None:
        """Establish (or replace) the shared hardware connection."""
        with self._lock:
            # Disconnect existing session first
            if self._hw is not None:
                self._disconnect_unsafe()
            hw = CpxApHardware()
            hw.connect(ip_address, timeout)
            # Reset all outputs to safe state on connect
            with suppress(Exception):
                hw.reset_all_outputs()
            self._hw = hw
            self._ip = ip_address
            self._timeout = timeout

    def disconnect(self) -> None:
        """Gracefully close the shared connection."""
        with self._lock:
            self._disconnect_unsafe()

    def get_hw(self) -> HardwareInterface:
        """Return the shared hardware interface.

        Raises:
            RuntimeError: If no connection is active.
        """
        with self._lock:
            if self._hw is None:
                raise RuntimeError(
                    "Not connected to hardware. Call /hw/connect first."
                )
            return self._hw

    def get_module(self, address: int) -> Any:
        """Return the raw module object at *address*.

        Convenience for endpoints that need direct module access
        (channel enumeration, parameter metadata, etc.).
        """
        hw = self.get_hw()
        if not isinstance(hw, CpxApHardware):
            raise RuntimeError("Shared connection is not a CpxApHardware instance")
        return hw._get_module(address)

    # ── Internal ────────────────────────────────────────────────────────────

    def _disconnect_unsafe(self) -> None:
        """Disconnect without acquiring the lock (caller must hold it)."""
        if self._hw is not None:
            with suppress(Exception):
                self._hw.reset_all_outputs()
            with suppress(Exception):
                self._hw.disconnect()
            self._hw = None
            self._ip = ""
            self._timeout = 0.0


# Module-level singleton — every import shares the same instance.
_connection_manager = ConnectionManager()


def get_connection_manager() -> ConnectionManager:
    """Return the module-level :class:`ConnectionManager` singleton."""
    return _connection_manager
