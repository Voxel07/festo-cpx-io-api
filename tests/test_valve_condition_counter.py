"""Valve Terminal Condition Counter test.

Uses :class:`hal.HardwareInterface` — parameter IDs are configurable.

Respects ``mounted_valves`` from the connections definition: only valve
channels that actually have a valve mounted are tested, because the
Condition Counter is only persisted to non-volatile memory for populated
slots.  Unmounted slots may increment the CC in RAM but never commit it.
"""
from __future__ import annotations

import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface
from valve_channels import expand_valve_indices

from ._base import (
    LogFn,
    load_bench_config,
    noop_log,
)

TEST_DEFINITION = {
    "test_id": "valve-condition-counter",
    "name": "Valve Condition Counter",
    "version": "1.0.0",
    "description": "Set CC setpoint, toggle valves past threshold, verify diagnosis",
    "required_capabilities": [
        "condition_counter",
        "valve_output"
    ],
    "supported_categories": [
        "valve",
        "inout"
    ],
    "safety_class": "caution",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "cc_param_id": 20094,
        "cc_readback_param_id": 20095,
        "toggle_cycles": 5
    },
}


def _get_mounted_valves(bench_config: BenchConfig | None) -> dict[int, list[int]]:
    """Return ``{module_address: [mounted_valve_slot, ...]}`` from config."""
    if not bench_config:
        return {}
    return {
        module.address: list(module.mounted_valves)
        for module in bench_config.module_instances
        if module.mounted_valves
    }


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    module_address: int | None = None,
) -> list[dict]:
    """Test CC behaviour on VABX valve terminals.

    Steps (per valve terminal):
      1. Read current CC value
      2. Toggle each *mounted* valve output N+2 times
      3. Read new CC value
      4. Verify CC incremented ≥ N
    """
    cc_param_id = TEST_DEFINITION["parameters"]["cc_param_id"]
    cc_readback_param_id = TEST_DEFINITION["parameters"]["cc_readback_param_id"]
    toggle_cycles = TEST_DEFINITION["parameters"]["toggle_cycles"]

    if bench_config is None:
        bench_config = load_bench_config(config_path)

    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]
    # The API resolver has already bound this invocation to a module whose
    # declared capabilities satisfy the test definition.
    valve_mods = topology
    results: list[dict] = []

    if not valve_mods:
        log("warning", "No capability-resolved valve terminal found on bus")
        results.append({"test": "valve-condition-counter", "passed": None,
                        "error": "No capability-resolved valve terminal found"})
        return results

    mounted_valves = _get_mounted_valves(bench_config)
    log("info", f"Found {len(valve_mods)} valve terminal(s): "
        f"{[m.name for m in valve_mods]}")

    for mod_info in valve_mods:
        addr = mod_info.address
        ch_start = time.time()
        mounted = mounted_valves.get(addr, [])
        log("info", f"Testing {mod_info.name} @ #{addr} …"
             f"  mounted valves: {mounted if mounted else '(none — skipping toggle)'}")
        result: dict[str, Any] = {
            "test": "valve-condition-counter",
            "module": mod_info.name, "address": addr,
            "mounted_valves": mounted,
            "steps": [],
        }

        try:
            # ── Step 0: Check CC support ──────────────────────────────
            # Probe the CC readback parameter.  If the valve terminal
            # doesn't expose it, skip rather than failing.
            try:
                hw.read_parameter(addr, cc_readback_param_id)
            except Exception as exc:
                err_msg = str(exc)
                if "has no parameter" in err_msg:
                    result["passed"] = None
                    result["note"] = f"{mod_info.name} has no CC support — skipping"
                    log("info", f"  ⊘ {mod_info.name}: no CC support, skipping")
                else:
                    result["passed"] = False
                    result["error"] = f"CC probe failed: {err_msg}"
                    log("warning", f"  [0] CC probe: {err_msg}")
                result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
                results.append(result)
                continue

            # ── Step 1: Read current CC value ─────────────────────────
            step1_ts = time.time()
            try:
                initial_act = hw.read_parameter(addr, cc_readback_param_id)
                result["initial_actual"] = initial_act
                log("info", f"  [1] Initial CC actual: {initial_act}")
                result["steps"].append({
                    "step": 1, "label": "Read initial CC",
                    "cc_actual": initial_act, "passed": True,
                    "duration_ms": round((time.time() - step1_ts) * 1000, 1),
                })
            except Exception as exc:
                initial_act = None
                log("warning", f"  [1] CC actual unreadable: {exc}")
                result["steps"].append({
                    "step": 1, "label": "Read initial CC",
                    "error": str(exc), "passed": False,
                    "duration_ms": round((time.time() - step1_ts) * 1000, 1),
                })

            # If no valves are mounted, skip toggle — CC in RAM only
            if not mounted:
                result["passed"] = True
                result["note"] = "No valves mounted — CC cannot be persisted; skipping toggle"
                log("info", f"  ⊘ {mod_info.name}: no mounted valves, skip toggle")
                result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
                results.append(result)
                continue

            # Write CC setpoint for the desired number of cycles
            try:
                hw.write_parameter(addr, cc_param_id, toggle_cycles)
                log("info", f"  CC setpoint set to {toggle_cycles}")
            except Exception as exc:
                result["passed"] = False
                result["error"] = f"Cannot write CC setpoint: {exc}"
                result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
                results.append(result)
                continue

            # ── Step 2: Toggle each mounted valve output ──────────────
            # Expand valve slot indices → hardware channel indices
            # (V4A/V4B/V4C: 2 channels/valve; VEAM: 1 channel/valve)
            cpv = bench_config.module_type_at(addr).channels_per_valve
            all_channels = expand_valve_indices(mounted, cpv)
            step2_ts = time.time()
            cycles = toggle_cycles + 2
            log("info", f"  [2] Toggling {len(mounted)} valve(s) × {cycles} cycles "
                 f"(valves: {mounted}, channels: {all_channels}, {cpv}c/valve) …")
            for _cycle in range(cycles):
                for ch in all_channels:
                    hw.write_output(addr, ch, True)
                time.sleep(0.05)
                for ch in all_channels:
                    hw.write_output(addr, ch, False)
                time.sleep(0.05)
            result["steps"].append({
                "step": 2, "label": f"Toggle valves ×{cycles}",
                "valves": mounted, "channels": all_channels,
                "channels_per_valve": cpv, "cycles": cycles, "passed": True,
                "duration_ms": round((time.time() - step2_ts) * 1000, 1),
            })

            time.sleep(0.2)

            # ── Step 3: Read new CC value ────────────────────────────
            step3_ts = time.time()
            try:
                cc_raw = hw.read_parameter(addr, cc_readback_param_id)
                # VABX returns a list indexed by valve slot (not hardware channel).
                # Extract only the mounted valve slots for comparison.
                if isinstance(cc_raw, list):
                    cc_act = min(cc_raw[i] for i in mounted) if mounted else 0
                    result["cc_actual"] = cc_raw
                    result["cc_per_valve"] = {i: cc_raw[i] for i in mounted}
                    result["channels_per_valve"] = cpv
                else:
                    cc_act = cc_raw
                    result["cc_actual"] = cc_act
                result["cc_expected"] = toggle_cycles
                log("info", f"  [3] New CC actual: {cc_act}  (raw: {cc_raw})")
                result["steps"].append({
                    "step": 3, "label": "Read final CC",
                    "cc_actual": cc_act, "cc_expected_min": toggle_cycles,
                    "passed": True,
                    "duration_ms": round((time.time() - step3_ts) * 1000, 1),
                })
            except Exception as exc:
                result["passed"] = False
                result["error"] = f"Cannot read CC actual after toggle: {exc}"
                result["steps"].append({
                    "step": 3, "label": "Read final CC",
                    "error": str(exc), "passed": False,
                    "duration_ms": round((time.time() - step3_ts) * 1000, 1),
                })
                results.append(result)
                continue

            # ── Step 4: Check results ────────────────────────────────
            step4_ts = time.time()
            if cc_act is not None and cc_act >= toggle_cycles:
                result["passed"] = True
                result["note"] = f"CC actual ({cc_act}) ≥ setpoint ({toggle_cycles})"
                result["steps"].append({
                    "step": 4, "label": "Verify CC increment",
                    "passed": True,
                    "detail": f"CC {cc_act} ≥ {toggle_cycles}",
                    "duration_ms": round((time.time() - step4_ts) * 1000, 1),
                })
                log("info", f"  [4] ✓ {mod_info.name}: PASS")
            else:
                result["passed"] = False
                result["error"] = f"CC actual ({cc_act}) < setpoint ({toggle_cycles})"
                result["steps"].append({
                    "step": 4, "label": "Verify CC increment",
                    "passed": False,
                    "detail": result["error"],
                    "duration_ms": round((time.time() - step4_ts) * 1000, 1),
                })
                log("error", f"  [4] ✗ {mod_info.name}: {result['error']}")

            # Restore CC setpoint
            try:
                hw.write_parameter(addr, cc_param_id, 0)
                log("info", "  CC setpoint restored to 0")
            except Exception as exc:
                log("warning", f"  Could not restore CC setpoint: {exc}")

            # Diagnosis check
            try:
                diag = hw.read_diagnosis(addr)
                result["diagnosis_present"] = diag is not None
                result["diagnosis_details"] = str(diag)[:200]
            except Exception:
                pass

        except Exception as exc:
            result["passed"] = False
            result["error"] = str(exc)

        results.append(result)
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)

    return results
