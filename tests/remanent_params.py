"""Remanent Parameter persistence test.

Uses :class:`hal.HardwareInterface` — parameter IDs are configurable.
"""
from __future__ import annotations

import time
from typing import Any

from hal import HardwareInterface
from ._base import LogFn, noop_log


_TEST_VAL_1 = 0xAA55
_TEST_VAL_2 = 0x55AA


def run(
    hw: HardwareInterface,
    connections_path: str = "connections.jsonc",  # kept for API symmetry
    log: LogFn = noop_log,
    param_id_1: int = 20118,
    param_id_2: int = 20119,
) -> list[dict]:
    """Write test values and verify immediate read-back for all modules."""
    topology = hw.read_topology()
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
            result["wrote_20118"] = _TEST_VAL_1
            log("info", f"    Wrote {param_id_1} = 0x{_TEST_VAL_1:04X}")
        except Exception as exc:
            result["wrote_20118"] = f"FAILED: {exc}"

        try:
            hw.write_parameter(addr, param_id_2, _TEST_VAL_2)
            result["wrote_20119"] = _TEST_VAL_2
            log("info", f"    Wrote {param_id_2} = 0x{_TEST_VAL_2:04X}")
        except Exception as exc:
            result["wrote_20119"] = f"FAILED: {exc}"

        ok_1 = ok_2 = False
        try:
            val1 = hw.read_parameter(addr, param_id_1)
            result["readback_20118"] = val1
            ok_1 = val1 == _TEST_VAL_1
            result["write_ok_20118"] = ok_1
        except Exception as exc:
            result["readback_20118"] = f"FAILED: {exc}"
            result["write_ok_20118"] = False

        try:
            val2 = hw.read_parameter(addr, param_id_2)
            result["readback_20119"] = val2
            ok_2 = val2 == _TEST_VAL_2
            result["write_ok_20119"] = ok_2
        except Exception as exc:
            result["readback_20119"] = f"FAILED: {exc}"
            result["write_ok_20119"] = False

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
) -> list[dict]:
    """Phase 2: verify test values survived a power cycle."""
    topology = hw.read_topology()
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
                "value_20118": val1, "ok_20118": ok_1,
                "value_20119": val2, "ok_20119": ok_2,
                "passed": ok_1 and ok_2,
            })
            if not result["passed"]:
                result["error"] = (
                    f"Mismatch — {param_id_1}=0x{val1:04X} (exp 0x{_TEST_VAL_1:04X}), "
                    f"{param_id_2}=0x{val2:04X} (exp 0x{_TEST_VAL_2:04X})"
                )
                log("error", f"  ✗ #{addr}: {result['error']}")
            else:
                log("info", f"  ✓ #{addr} {mod_info.name}: values persisted")
        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)
            log("error", f"  ✗ #{addr} verify failed: {exc}")
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results
