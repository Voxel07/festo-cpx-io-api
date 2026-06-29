"""Remanent Parameter persistence test.

Uses :class:`hal.HardwareInterface` — parameter IDs are configurable.

Two entry points are provided:

``run()``
    Write test values and verify *immediate* read-back (no power cycle).
    Sets ``needs_power_cycle: True`` in each result so the caller knows
    a subsequent power cycle + ``verify()`` call is required.

``run_with_power_cycle()``
    Full end-to-end test: writes values, cycles the bench power supply via
    :class:`~power_supply.PowerCycleSession`, reconnects the HAL, then reads
    back and verifies that the values survived the power cycle.

``verify()``
    Phase-2 verification: assumes the caller has already power-cycled the
    hardware externally and re-established the HAL connection.
"""
from __future__ import annotations

import time
from typing import Any

from hal import HardwareInterface
from ._base import LogFn, noop_log

TEST_DEFINITION = {
    "test_id": "remanent-params",
    "name": "Remanent Parameters",
    "version": "1.1.0",
    "description": (
        "Write test values to remanent parameters, power-cycle the bench, "
        "and verify persistence after restart"
    ),
    "required_capabilities": [
        "remanent_params"
    ],
    "supported_categories": [
        "input",
        "output",
        "inout",
        "valve",
        "bus"
    ],
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "param_id_1": 20118,
        "param_id_2": 20119,
        "power_supply_comport": None,
        "power_supply_channels": [1, 2, 4],
        "power_supply_voltage": 24.0,
        "reconnect_wait": 8.0,
    },
    "compatible_modules": [
        "*"
    ]
}


_TEST_VAL_1 = 0xAA55
_TEST_VAL_2 = 0x55AA


def run(
    hw: HardwareInterface,
    connections_path: str = "connections.jsonc",  # kept for API symmetry
    log: LogFn = noop_log,
    param_id_1: int = 20118,
    param_id_2: int = 20119,
    module_address: int | None = None,
) -> list[dict]:
    """Write test values and verify immediate read-back for all modules.

    Does **not** perform a power cycle.  Each result contains
    ``needs_power_cycle: True`` to signal that :func:`verify` should be
    called after an external power cycle to confirm persistence.
    """
    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]
    log("info", f"Remanent-params write phase on {len(topology)} module(s)")
    results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Module #{addr} {mod_info.name} …")
        result: dict[str, Any] = {
            "test": "remanent-params", "module": mod_info.name,
            "address": addr, "phase": "write",
        }

        try:
            hw.write_parameter(addr, param_id_1, _TEST_VAL_1)
            result["wrote_param_1"] = _TEST_VAL_1
            log("info", f"    Wrote {param_id_1} = 0x{_TEST_VAL_1:04X}")
        except Exception as exc:
            result["wrote_param_1"] = f"FAILED: {exc}"

        try:
            hw.write_parameter(addr, param_id_2, _TEST_VAL_2)
            result["wrote_param_2"] = _TEST_VAL_2
            log("info", f"    Wrote {param_id_2} = 0x{_TEST_VAL_2:04X}")
        except Exception as exc:
            result["wrote_param_2"] = f"FAILED: {exc}"

        ok_1 = ok_2 = False
        try:
            val1 = hw.read_parameter(addr, param_id_1)
            result["readback_param_1"] = val1
            ok_1 = val1 == _TEST_VAL_1
            result["write_ok_param_1"] = ok_1
        except Exception as exc:
            result["readback_param_1"] = f"FAILED: {exc}"
            result["write_ok_param_1"] = False

        try:
            val2 = hw.read_parameter(addr, param_id_2)
            result["readback_param_2"] = val2
            ok_2 = val2 == _TEST_VAL_2
            result["write_ok_param_2"] = ok_2
        except Exception as exc:
            result["readback_param_2"] = f"FAILED: {exc}"
            result["write_ok_param_2"] = False

        result["passed"] = ok_1 and ok_2
        result["needs_power_cycle"] = True
        result["note"] = (
            "Write phase complete. Power-cycle the CPX-AP system and run "
            "'remanent-params-verify' to confirm persistence."
        )

        if result["passed"]:
            log("info", f"  ✓ #{addr} {mod_info.name}: write phase PASS")
        else:
            log("error", f"  ✗ #{addr} {mod_info.name}: write phase FAIL")

        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results


def verify(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    param_id_1: int = 20118,
    param_id_2: int = 20119,
    module_address: int | None = None,
) -> list[dict]:
    """Phase 2: verify test values survived a power cycle.

    Should be called after the hardware has been power-cycled and *hw*
    has been reconnected.
    """
    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]
    log("info", "Remanent-params verify phase")
    results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Module #{addr} {mod_info.name} …")
        result: dict[str, Any] = {
            "test": "remanent-params-verify",
            "module": mod_info.name, "address": addr, "phase": "verify",
        }
        try:
            val1 = hw.read_parameter(addr, param_id_1)
            val2 = hw.read_parameter(addr, param_id_2)
            ok_1 = val1 == _TEST_VAL_1
            ok_2 = val2 == _TEST_VAL_2
            result.update({
                "value_param_1": val1, "ok_param_1": ok_1,
                "value_param_2": val2, "ok_param_2": ok_2,
                "passed": ok_1 and ok_2,
            })
            if not result["passed"]:
                result["error"] = (
                    f"Mismatch — {param_id_1}=0x{val1:04X} (exp 0x{_TEST_VAL_1:04X}), "
                    f"{param_id_2}=0x{val2:04X} (exp 0x{_TEST_VAL_2:04X})"
                )
                log("error", f"  ✗ #{addr}: {result['error']}")
            else:
                log("info", f"  ✓ #{addr} {mod_info.name}: values persisted after power cycle")
        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)
            log("error", f"  ✗ #{addr} verify failed: {exc}")
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results


def run_with_power_cycle(
    hw: HardwareInterface,
    ip_address: str,
    power_supply_comport: str,
    power_supply_channels: list[int],
    log: LogFn = noop_log,
    param_id_1: int = 20118,
    param_id_2: int = 20119,
    module_address: int | None = None,
    power_supply_voltage: float = 24.0,
    reconnect_wait: float = 8.0,
    off_time: float = 1.0,
) -> list[dict]:
    """Full end-to-end remanent-params test with bench power cycle.

    Procedure (per module):
      1. Write test sentinel values to both parameters.
      2. Verify immediate read-back (sanity check).
      3. Power-cycle the bench via the HMP40x0 power supply.
      4. Reconnect the HAL after restart.
      5. Read back both parameters and verify they survived the cycle.

    Args:
        hw:                      Connected :class:`~hal.HardwareInterface`.
        ip_address:              IP address for HAL reconnect after power cycle.
        power_supply_comport:    Serial port of the HMP40x0 (e.g. ``"COM3"``).
        power_supply_channels:   HMP output channels to switch (1-based).
        log:                     Optional logging callback.
        param_id_1:              First remanent parameter ID (default 20118).
        param_id_2:              Second remanent parameter ID (default 20119).
        module_address:          Restrict to a single module address; ``None``
                                 tests all modules.
        power_supply_voltage:    Voltage to restore after power-off (V).
        reconnect_wait:          Seconds to wait after power-on before
                                 reconnecting (default 8 s).
        off_time:                Seconds to keep the power off (default 1 s).

    Returns:
        Combined list of write-phase and verify-phase result dicts.
    """
    from power_supply import PowerCycleSession, PowerSupplyNotAvailable

    # ── Phase 1: write ────────────────────────────────────────────────────────
    write_results = run(
        hw=hw,
        log=log,
        param_id_1=param_id_1,
        param_id_2=param_id_2,
        module_address=module_address,
    )

    write_failures = [r for r in write_results if r.get("passed") is False]
    if write_failures:
        log("warning", "Write phase had failures — skipping power cycle")
        return write_results

    # ── Phase 2: power cycle ──────────────────────────────────────────────────
    log("info", f"  Power-cycling bench via HMP40x0 on {power_supply_comport} …")
    try:
        with PowerCycleSession(
            comport=power_supply_comport,
            channels=power_supply_channels,
            voltage=power_supply_voltage,
            off_time=off_time,
            reconnect_wait=reconnect_wait,
        ) as ps:
            ps.cycle(hw, ip_address)
        log("info", "  Power cycle complete, HAL reconnected")
    except PowerSupplyNotAvailable as exc:
        log("error", f"  Power supply not available: {exc}")
        for r in write_results:
            r["power_cycle"] = "SKIPPED — power supply not available"
        return write_results
    except Exception as exc:
        log("error", f"  Power cycle failed: {exc}")
        for r in write_results:
            r["power_cycle"] = f"FAILED: {exc}"
        return write_results

    # ── Phase 3: verify ───────────────────────────────────────────────────────
    verify_results = verify(
        hw=hw,
        log=log,
        param_id_1=param_id_1,
        param_id_2=param_id_2,
        module_address=module_address,
    )

    return write_results + verify_results
