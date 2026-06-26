"""Remanent Parameter persistence test.

Write phase: writes test values 0xAA55 / 0x55AA to parameters 20118 / 20119
on every module and verifies the immediate read-back.

A second pass (``verify``) should be run *after* a power-cycle to confirm
the values survived.
"""
from __future__ import annotations

from typing import Any

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from cpx_io.cpx_system.cpx_ap.ap_parameter import Parameter

from ._base import LogFn, noop_log


def _make_param(parameter_id: int, data_type: str) -> Parameter:
    """Construct a minimal Parameter with only the fields needed for read/write."""
    return Parameter(
        parameter_id=parameter_id,
        parameter_instances={},
        is_writable=True,
        array_size=1,
        data_type=data_type,
        default_value=0,
        description="",
        name="",
    )


_TEST_VAL_1 = 0xAA55
_TEST_VAL_2 = 0x55AA

_PARAM_20118 = _make_param(20118, "UINT16")
_PARAM_20119 = _make_param(20119, "UINT16")


def run(
    cpx_ap: CpxAp,
    connections_path: str = "connections.jsonc",  # kept for API symmetry
    log: LogFn = noop_log,
) -> list[dict]:
    """Write test values and verify immediate read-back for all modules.

    :param cpx_ap: Active CpxAp instance.
    :param connections_path: Unused — kept for caller symmetry.
    :param log: Logging callback.
    :returns: List of per-module result dicts, each with ``needs_power_cycle=True``.
    """
    log("info", f"Remanent-params write phase on {len(cpx_ap.modules)} module(s)")
    results: list[dict] = []

    for mod in cpx_ap.modules:
        log("info", f"  Module #{mod.position} {mod.name} …")
        result: dict[str, Any] = {
            "test": "remanent-params",
            "module": mod.name,
            "address": mod.position,
            "phase": "write",
        }

        # ── Write ───────────────────────────────────────────────────
        try:
            cpx_ap.write_parameter(mod.position, _PARAM_20118, _TEST_VAL_1)
            result["wrote_20118"] = _TEST_VAL_1
            log("info", f"    Wrote 20118 = 0x{_TEST_VAL_1:04X}")
        except Exception as exc:
            result["wrote_20118"] = f"FAILED: {exc}"
            log("error", f"    Write 20118 FAILED: {exc}")

        try:
            cpx_ap.write_parameter(mod.position, _PARAM_20119, _TEST_VAL_2)
            result["wrote_20119"] = _TEST_VAL_2
            log("info", f"    Wrote 20119 = 0x{_TEST_VAL_2:04X}")
        except Exception as exc:
            result["wrote_20119"] = f"FAILED: {exc}"
            log("error", f"    Write 20119 FAILED: {exc}")

        # ── Immediate read-back ─────────────────────────────────────
        ok_1 = ok_2 = False
        try:
            val1 = cpx_ap.read_parameter(mod.position, _PARAM_20118)
            result["readback_20118"] = val1
            ok_1 = val1 == _TEST_VAL_1
            result["write_ok_20118"] = ok_1
            log("info" if ok_1 else "error",
                f"    Readback 20118 = 0x{val1:04X}  {'✓' if ok_1 else '✗'}")
        except Exception as exc:
            result["readback_20118"] = f"FAILED: {exc}"
            result["write_ok_20118"] = False
            log("error", f"    Readback 20118 FAILED: {exc}")

        try:
            val2 = cpx_ap.read_parameter(mod.position, _PARAM_20119)
            result["readback_20119"] = val2
            ok_2 = val2 == _TEST_VAL_2
            result["write_ok_20119"] = ok_2
            log("info" if ok_2 else "error",
                f"    Readback 20119 = 0x{val2:04X}  {'✓' if ok_2 else '✗'}")
        except Exception as exc:
            result["readback_20119"] = f"FAILED: {exc}"
            result["write_ok_20119"] = False
            log("error", f"    Readback 20119 FAILED: {exc}")

        result["passed"] = ok_1 and ok_2
        result["needs_power_cycle"] = True
        result["note"] = (
            "Write phase complete. Power-cycle the CPX-AP system and run "
            "'remanent-params-verify' to confirm persistence."
        )

        if result["passed"]:
            log("info", f"  ✓ #{mod.position} {mod.name}: write phase PASS")
        else:
            log("error", f"  ✗ #{mod.position} {mod.name}: write phase FAIL")

        results.append(result)

    return results


def verify(
    cpx_ap: CpxAp,
    log: LogFn = noop_log,
) -> list[dict]:
    """Phase 2: verify test values survived a power cycle.

    Call this after reconnecting to the system post-power-cycle.
    """
    log("info", "Remanent-params verify phase")
    results: list[dict] = []

    for mod in cpx_ap.modules:
        log("info", f"  Module #{mod.position} {mod.name} …")
        result: dict[str, Any] = {
            "test": "remanent-params-verify",
            "module": mod.name,
            "address": mod.position,
            "phase": "verify",
        }
        try:
            val1 = cpx_ap.read_parameter(mod.position, _PARAM_20118)
            val2 = cpx_ap.read_parameter(mod.position, _PARAM_20119)
            ok_1 = val1 == _TEST_VAL_1
            ok_2 = val2 == _TEST_VAL_2
            result.update({
                "value_20118": val1, "ok_20118": ok_1,
                "value_20119": val2, "ok_20119": ok_2,
                "passed": ok_1 and ok_2,
            })
            if not result["passed"]:
                result["error"] = (
                    f"Mismatch — 20118=0x{val1:04X} (exp 0x{_TEST_VAL_1:04X}), "
                    f"20119=0x{val2:04X} (exp 0x{_TEST_VAL_2:04X})"
                )
                log("error", f"  ✗ #{mod.position}: {result['error']}")
            else:
                log("info", f"  ✓ #{mod.position} {mod.name}: values persisted")
        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)
            log("error", f"  ✗ #{mod.position} verify failed: {exc}")
        results.append(result)

    return results
