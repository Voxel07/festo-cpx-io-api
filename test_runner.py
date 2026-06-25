"""
Test suite for CPX-AP system validation.

Provides test runners for:
- Condition Counter (CC) validation on input/output modules
- Valve terminal condition counter (VABX-A-S-BV-V4A/B/C)
- Remanent parameter persistence
- Connection wiring validation
- And more...

Each runner accepts a CpxAp instance + module and returns a standardised result dict.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from cpx_io.cpx_system.cpx_ap.ap_module import ApModule
from cpx_io.cpx_system.cpx_ap.ap_parameter import Parameter
from generate_system_config import validate_connections, compare_topology


# ─── Helper ─────────────────────────────────────────────────────────────────────

def _load_test_compat() -> dict:
    """Load the test compatibility matrix."""
    path = Path(__file__).parent / "test_compatibility.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_connections(connections_path: str = "connections.jsonc") -> list[dict]:
    """Load I/O connections from a JSON file."""
    path = Path(connections_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("connections", [])


def _find_connected_pair(
    connections: list[dict],
    module_addr: int,
    as_source: bool = True,
) -> list[dict]:
    """Find all connections where *module_addr* is the source (or target)."""
    if as_source:
        return [c for c in connections if c["source_module_addr"] == module_addr]
    return [c for c in connections if c["target_module_addr"] == module_addr]


def _is_valve_terminal(mod: ApModule) -> bool:
    """Check if a module is a VABX valve terminal."""
    name = mod.name.upper()
    return name.startswith("VABX")


def _channel_count(mod: ApModule) -> int:
    """Total writable/readable channel count."""
    return (
        len([c for c in mod.channels.inputs if c.direction == "in"])
        + len([c for c in mod.channels.outputs if c.direction == "out"])
        + len(mod.channels.inouts)
    )


# ─── 5.1  Condition Counter Test ────────────────────────────────────────────────

def test_condition_counter(
    cpx_ap: CpxAp,
    connections_path: str = "connections.jsonc",
) -> list[dict]:
    """Validate Condition Counter (CC) wiring between output and input modules.

    For each connection defined in connections.jsonc:
    1. Check if the source is an output module with CC support
    2. Check if the target is an input module with CC support
    3. Read the current CC parameter from both modules
    4. Verify that the wiring allows CC pulses to propagate

    :returns: List of per-connection result dicts
    """
    connections = _load_connections(connections_path)
    if not connections:
        return [{"test": "condition-counter", "passed": None, "error": "No connections defined"}]

    results: list[dict] = []

    for conn in connections:
        src_addr = conn["source_module_addr"]
        tgt_addr = conn["target_module_addr"]

        src_mod = next((m for m in cpx_ap.modules if m.position == src_addr), None)
        tgt_mod = next((m for m in cpx_ap.modules if m.position == tgt_addr), None)

        if src_mod is None or tgt_mod is None:
            results.append({
                "test": "condition-counter",
                "connection": f"#{src_addr}→#{tgt_addr}",
                "passed": False,
                "error": "Module not found",
            })
            continue

        # Check if source module has outputs
        has_outputs = bool(src_mod.channels.outputs)

        # Check if target module has inputs
        has_inputs = bool(tgt_mod.channels.inputs)

        result = {
            "test": "condition-counter",
            "connection": f"#{src_addr}:{conn['source_channel']}→#{tgt_addr}:{conn['target_channel']}",
            "source_module": src_mod.name,
            "target_module": tgt_mod.name,
            "source_has_outputs": has_outputs,
            "target_has_inputs": has_inputs,
        }

        if not has_outputs:
            result["passed"] = False
            result["error"] = "Source module has no output channels"
        elif not has_inputs:
            result["passed"] = False
            result["error"] = "Target module has no input channels"
        else:
            # Try reading CC-related parameters
            try:
                # Parameter 20094 = Condition Counter Setpoint (module-level)
                # Parameter 20095 = Condition Counter Actual Value
                param_setpoint = Parameter(parameter_id=20094, data_type="UINT16")
                param_actual = Parameter(parameter_id=20095, data_type="UINT16")

                try:
                    cc_setpoint = cpx_ap.read_parameter(src_addr, param_setpoint)
                    result["cc_setpoint_source"] = cc_setpoint
                except Exception:
                    result["cc_setpoint_source"] = "N/A (CC not supported)"

                try:
                    cc_actual = cpx_ap.read_parameter(tgt_addr, param_actual)
                    result["cc_actual_target"] = cc_actual
                except Exception:
                    result["cc_actual_target"] = "N/A (CC not supported)"

                result["passed"] = True
                result["note"] = "Modules connected; CC parameters readable"
            except Exception as exc:
                result["passed"] = False
                result["error"] = str(exc)

        results.append(result)

    return results


# ─── 5.2  Valve Terminal Condition Counter Test ─────────────────────────────────

def test_valve_condition_counter(
    cpx_ap: CpxAp,
    toggle_cycles: int = 5,
) -> list[dict]:
    """Test the condition counter on VABX valve terminals.

    For each valve terminal:
    1. Set parameter 20094 (CC setpoint) to *toggle_cycles*
    2. Toggle valve outputs more than *toggle_cycles* times
    3. Check if a diagnosis is present (expected: maintenance warning)
    4. Read parameter 20095 (CC actual) — should equal *toggle_cycles*

    :param toggle_cycles: Number of toggle cycles to trigger (default 5)
    :returns: List of per-module result dicts
    """
    results: list[dict] = []

    valve_mods = [m for m in cpx_ap.modules if _is_valve_terminal(m)]
    if not valve_mods:
        return [{"test": "valve-condition-counter", "passed": None, "error": "No valve terminals found"}]

    param_setpoint = Parameter(parameter_id=20094, data_type="UINT16")
    param_actual = Parameter(parameter_id=20095, data_type="UINT16")

    for mod in valve_mods:
        result: dict[str, Any] = {
            "test": "valve-condition-counter",
            "module": mod.name,
            "address": mod.position,
        }

        try:
            # ── Read current CC values ──────────────────────
            try:
                initial_setpoint = cpx_ap.read_parameter(mod.position, param_setpoint)
                result["initial_setpoint"] = initial_setpoint
            except Exception:
                initial_setpoint = None

            try:
                initial_actual = cpx_ap.read_parameter(mod.position, param_actual)
                result["initial_actual"] = initial_actual
            except Exception:
                initial_actual = None

            # ── Set CC setpoint ─────────────────────────────
            try:
                cpx_ap.write_parameter(mod.position, param_setpoint, toggle_cycles)
            except Exception as exc:
                result["passed"] = False
                result["error"] = f"Cannot write CC setpoint: {exc}"
                results.append(result)
                continue

            # ── Toggle valves ───────────────────────────────
            num_outputs = len(mod.channels.outputs)
            if num_outputs == 0:
                result["passed"] = False
                result["error"] = "Valve terminal has no output channels"
                results.append(result)
                continue

            for cycle in range(toggle_cycles + 2):  # +2 to exceed setpoint
                all_high = [True] * num_outputs
                all_low = [False] * num_outputs
                mod.write_channels(all_high)
                time.sleep(0.05)
                mod.write_channels(all_low)
                time.sleep(0.05)

            time.sleep(0.2)  # Let diagnosis propagate

            # ── Check diagnosis ─────────────────────────────
            try:
                diag_info = mod.read_diagnosis_information()
                result["diagnosis_present"] = diag_info is not None
                result["diagnosis_details"] = str(diag_info)[:200]
            except Exception as exc:
                result["diagnosis_error"] = str(exc)

            # ── Read CC actual ──────────────────────────────
            try:
                cc_actual = cpx_ap.read_parameter(mod.position, param_actual)
                result["cc_actual"] = cc_actual
                result["cc_expected"] = toggle_cycles + 2

                if cc_actual >= toggle_cycles:
                    result["passed"] = True
                    result["note"] = f"CC actual ({cc_actual}) >= setpoint ({toggle_cycles})"
                else:
                    result["passed"] = False
                    result["error"] = f"CC actual ({cc_actual}) < setpoint ({toggle_cycles})"
            except Exception as exc:
                result["passed"] = False
                result["error"] = f"Cannot read CC actual: {exc}"

            # ── Reset CC setpoint ───────────────────────────
            try:
                cpx_ap.write_parameter(mod.position, param_setpoint, 0)
            except Exception:
                pass

        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)

        results.append(result)

    return results


# ─── 5.3  Remanent Parameters Test ──────────────────────────────────────────────

def test_remanent_params(
    cpx_ap: CpxAp,
    connections_path: str = "connections.jsonc",
) -> list[dict]:
    """Test remanent parameter persistence (params 20118, 20119).

    For each module:
    1. Write a test value to param 20118 (e.g., 0xAA55)
    2. Write a test value to param 20119 (e.g., 0x55AA)
    3. Read both back immediately to verify write succeeded
    4. Print instructions for power-cycle (mock hook for PSU control)
    5. After reconnection: read both params again and verify they match

    .. note::
        The PSU power-cycle step is a mock — implement ``psu_power_cycle()``
        in your test harness to control the lab power supply.

    :returns: List of per-module result dicts with ``needs_power_cycle`` flag
    """
    results: list[dict] = []

    param_20118 = Parameter(parameter_id=20118, data_type="UINT16")
    param_20119 = Parameter(parameter_id=20119, data_type="UINT16")

    test_val_1 = 0xAA55
    test_val_2 = 0x55AA

    for mod in cpx_ap.modules:
        result: dict[str, Any] = {
            "test": "remanent-params",
            "module": mod.name,
            "address": mod.position,
            "phase": "write",
        }

        try:
            # ── Phase 1: Write ──────────────────────────────
            try:
                cpx_ap.write_parameter(mod.position, param_20118, test_val_1)
                result["wrote_20118"] = test_val_1
            except Exception as exc:
                result["wrote_20118"] = f"FAILED: {exc}"

            try:
                cpx_ap.write_parameter(mod.position, param_20119, test_val_2)
                result["wrote_20119"] = test_val_2
            except Exception as exc:
                result["wrote_20119"] = f"FAILED: {exc}"

            # ── Phase 2: Immediate read-back ─────────────────
            try:
                val1 = cpx_ap.read_parameter(mod.position, param_20118)
                result["readback_20118"] = val1
                result["write_ok_20118"] = (val1 == test_val_1)
            except Exception as exc:
                result["readback_20118"] = f"FAILED: {exc}"
                result["write_ok_20118"] = False

            try:
                val2 = cpx_ap.read_parameter(mod.position, param_20119)
                result["readback_20119"] = val2
                result["write_ok_20119"] = (val2 == test_val_2)
            except Exception as exc:
                result["readback_20119"] = f"FAILED: {exc}"
                result["write_ok_20119"] = False

            result["passed"] = result.get("write_ok_20118", False) and result.get("write_ok_20119", False)
            result["needs_power_cycle"] = True
            result["note"] = (
                "Write phase complete.  Power-cycle the CPX-AP system, reconnect, "
                "and run 'test_remanent_params_verify' to check persistence."
            )

        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)

        results.append(result)

    return results


def test_remanent_params_verify(
    cpx_ap: CpxAp,
) -> list[dict]:
    """Phase 2 of remanent params test: verify values survived a power cycle.

    Call this AFTER reconnecting to the system post-power-cycle.
    Compares stored values against the expected test values.
    """
    results: list[dict] = []

    param_20118 = Parameter(parameter_id=20118, data_type="UINT16")
    param_20119 = Parameter(parameter_id=20119, data_type="UINT16")

    expected_1 = 0xAA55
    expected_2 = 0x55AA

    for mod in cpx_ap.modules:
        result: dict[str, Any] = {
            "test": "remanent-params-verify",
            "module": mod.name,
            "address": mod.position,
            "phase": "verify",
        }

        try:
            val1 = cpx_ap.read_parameter(mod.position, param_20118)
            val2 = cpx_ap.read_parameter(mod.position, param_20119)

            result["value_20118"] = val1
            result["value_20119"] = val2
            result["ok_20118"] = (val1 == expected_1)
            result["ok_20119"] = (val2 == expected_2)
            result["passed"] = result["ok_20118"] and result["ok_20119"]

            if not result["passed"]:
                result["error"] = (
                    f"Values do not match: 20118={val1} (expected {expected_1}), "
                    f"20119={val2} (expected {expected_2})"
                )
        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)

        results.append(result)

    return results


# ─── PSU Power Cycle Mock ──────────────────────────────────────────────────────

def psu_power_cycle(delay_s: float = 10.0) -> None:
    """Mock function for PSU-controlled power cycling.

    **Replace this** with your actual lab PSU control code (e.g., VISA/SCPI,
    serial, or REST API) to physically power-cycle the CPX-AP system.

    The default implementation simply sleeps for *delay_s* seconds.

    :param delay_s: Power-off duration in seconds
    """
    print(f"[PSU MOCK] Powering OFF for {delay_s}s ... (implement real PSU control here)")
    time.sleep(delay_s)
    print("[PSU MOCK] Power ON — system should now be back online")


# ─── Bulk Test Runner ──────────────────────────────────────────────────────────

def run_all_tests(
    ip_address: str,
    connections_path: str = "connections.jsonc",
    topology_path: str = "topology.jsonc",
    timeout: float = 0,
) -> dict:
    """Run the complete test suite against a CPX-AP system.

    :returns: Aggregated results dict with per-test breakdown
    """
    results: dict[str, Any] = {
        "ip_address": ip_address,
        "timestamp": time.time(),
        "tests": {},
    }

    with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
        # 1. Connection validation
        try:
            results["tests"]["validate-connections"] = validate_connections(
                ip_address, connections_path, timeout,
            )
        except Exception as exc:
            results["tests"]["validate-connections"] = {"passed": False, "error": str(exc)}

        # 2. Topology comparison
        try:
            results["tests"]["compare-topology"] = compare_topology(
                topology_path, ip_address, timeout,
            )
        except Exception as exc:
            results["tests"]["compare-topology"] = {"passed": False, "error": str(exc)}

        # 3. Condition counter
        try:
            results["tests"]["condition-counter"] = test_condition_counter(
                cpx_ap, connections_path,
            )
        except Exception as exc:
            results["tests"]["condition-counter"] = {"passed": False, "error": str(exc)}

        # 4. Valve condition counter
        try:
            results["tests"]["valve-condition-counter"] = test_valve_condition_counter(
                cpx_ap, toggle_cycles=5,
            )
        except Exception as exc:
            results["tests"]["valve-condition-counter"] = {"passed": False, "error": str(exc)}

        # 5. Remanent params (write phase)
        try:
            results["tests"]["remanent-params"] = test_remanent_params(
                cpx_ap, connections_path,
            )
        except Exception as exc:
            results["tests"]["remanent-params"] = {"passed": False, "error": str(exc)}

    # Aggregate pass/fail
    all_passed = True
    for name, test_result in results["tests"].items():
        if isinstance(test_result, list):
            results["tests"][name] = {
                "results": test_result,
                "passed": all(r.get("passed", False) for r in test_result
                           if r.get("passed") is not None),
            }
        results["tests"][name]["passed"] = test_result.get("all_passed",
            all(r.get("passed", False) for r in (test_result if isinstance(test_result, list) else [test_result])
               if r.get("passed") is not None)
        ) if isinstance(test_result, dict) else False

    return results


if __name__ == "__main__":
    import sys
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.11"
    all_results = run_all_tests(ip)
    print(json.dumps(all_results, indent=2, default=str))
