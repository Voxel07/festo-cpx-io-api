"""Valve Terminal Condition Counter test.

For every VABX valve terminal on the bus:
1. Read current CC setpoint (param 20094) and actual (param 20095).
2. Write a low setpoint, toggle all valve outputs past it.
3. Verify the CC actual reaches the setpoint (maintenance warning expected).
4. Restore the original CC setpoint.
"""
from __future__ import annotations

import time
from typing import Any

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from cpx_io.cpx_system.cpx_ap.ap_parameter import Parameter

from ._base import LogFn, is_valve_terminal, noop_log


def _make_param(parameter_id: int, data_type: str) -> "Parameter":
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


def run(
    cpx_ap: CpxAp,
    toggle_cycles: int = 5,
    log: LogFn = noop_log,
) -> list[dict]:
    """Test CC behaviour on VABX valve terminals.

    :param cpx_ap: Active CpxAp instance.
    :param toggle_cycles: Number of valve toggle cycles to perform.
    :param log: Logging callback.
    :returns: List of per-module result dicts.
    """
    valve_mods = [m for m in cpx_ap.modules if is_valve_terminal(m)]
    if not valve_mods:
        log("warning", "No VABX valve terminals found on bus")
        return [{"test": "valve-condition-counter", "passed": None,
                 "error": "No valve terminals found"}]

    log("info", f"Found {len(valve_mods)} valve terminal(s): "
        f"{[m.name for m in valve_mods]}")

    param_setpoint = _make_param(20094, "UINT16")
    param_actual = _make_param(20095, "UINT16")
    results: list[dict] = []

    for mod in valve_mods:
        log("info", f"Testing {mod.name} @ #{mod.position} …")
        result: dict[str, Any] = {
            "test": "valve-condition-counter",
            "module": mod.name,
            "address": mod.position,
        }

        try:
            # ── Read initial CC values ──────────────────────────────
            try:
                initial_sp = cpx_ap.read_parameter(mod.position, param_setpoint)
                result["initial_setpoint"] = initial_sp
                log("info", f"  Initial CC setpoint: {initial_sp}")
            except Exception as exc:
                initial_sp = None
                log("warning", f"  CC setpoint unreadable: {exc}")

            try:
                initial_act = cpx_ap.read_parameter(mod.position, param_actual)
                result["initial_actual"] = initial_act
                log("info", f"  Initial CC actual:   {initial_act}")
            except Exception:
                pass

            # ── Set CC setpoint ─────────────────────────────────────
            try:
                cpx_ap.write_parameter(mod.position, param_setpoint, toggle_cycles)
                log("info", f"  CC setpoint set to {toggle_cycles}")
            except Exception as exc:
                result["passed"] = False
                result["error"] = f"Cannot write CC setpoint: {exc}"
                log("error", f"  ✗ Cannot write CC setpoint: {exc}")
                results.append(result)
                continue

            # ── Toggle valve outputs ────────────────────────────────
            num_out = len(mod.channels.outputs)
            if num_out == 0:
                result["passed"] = False
                result["error"] = "Valve terminal has no output channels"
                log("error", "  ✗ No output channels on valve terminal")
                results.append(result)
                continue

            log("info", f"  Toggling {num_out} output(s) × {toggle_cycles + 2} cycles …")
            all_hi = [True] * num_out
            all_lo = [False] * num_out
            for _ in range(toggle_cycles + 2):
                mod.write_channels(all_hi)
                time.sleep(0.05)
                mod.write_channels(all_lo)
                time.sleep(0.05)

            time.sleep(0.2)  # Let diagnosis propagate

            # ── Read diagnosis ──────────────────────────────────────
            try:
                diag = mod.read_diagnosis_information()
                result["diagnosis_present"] = diag is not None
                result["diagnosis_details"] = str(diag)[:200]
                log("info", f"  Diagnosis present: {diag is not None}")
            except Exception as exc:
                result["diagnosis_error"] = str(exc)
                log("warning", f"  Diagnosis read failed: {exc}")

            # ── Read CC actual ──────────────────────────────────────
            try:
                cc_act = cpx_ap.read_parameter(mod.position, param_actual)
                result["cc_actual"] = cc_act
                result["cc_expected"] = toggle_cycles + 2
                log("info", f"  CC actual: {cc_act} (expected ≥ {toggle_cycles})")

                if cc_act >= toggle_cycles:
                    result["passed"] = True
                    result["note"] = f"CC actual ({cc_act}) ≥ setpoint ({toggle_cycles})"
                    log("info", f"  ✓ {mod.name}: PASS")
                else:
                    result["passed"] = False
                    result["error"] = f"CC actual ({cc_act}) < setpoint ({toggle_cycles})"
                    log("error", f"  ✗ {mod.name}: {result['error']}")
            except Exception as exc:
                result["passed"] = False
                result["error"] = f"Cannot read CC actual: {exc}"
                log("error", f"  ✗ Cannot read CC actual: {exc}")

            # ── Restore original CC setpoint ────────────────────────
            restore_val = initial_sp if initial_sp is not None else 0
            try:
                cpx_ap.write_parameter(mod.position, param_setpoint, restore_val)
                log("info", f"  CC setpoint restored to {restore_val}")
            except Exception as exc:
                log("warning", f"  Could not restore CC setpoint: {exc}")

        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)
            log("error", f"  ✗ Unexpected error on {mod.name}: {exc}")

        results.append(result)

    return results
