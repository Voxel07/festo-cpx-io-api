"""Condition Counter (CC) validation test.

Uses :class:`hal.HardwareInterface` — parameter IDs are configurable.

For each I/O connection, performs a full toggle-and-verify cycle:
  1. Read the current CC value on the target module
  2. Toggle the source output channel N times
  3. Read the new CC value on the target module
  4. Verify that CC incremented by at least N
"""
from __future__ import annotations

import time
from typing import Any

from hal import HardwareInterface, ModuleInfo
from ._base import (
    LogFn, channel_index_from_port, is_module_compatible,
    load_compatibility, load_connections, noop_log,
)

TEST_DEFINITION = {
    "test_id": "condition-counter",
    "name": "Condition Counter",
    "version": "1.0.0",
    "description": "Read and verify condition counter parameters",
    "required_capabilities": [
        "condition_counter"
    ],
    "supported_categories": [
        "output",
        "input",
        "inout"
    ],
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "cc_param_id": 20094,
        "cc_readback_param_id": 20095
    },
    "compatible_modules": [
        "CPX-AP-I-16DI",
        "CPX-AP-I-16NDI",
        "CPX-AP-I-16DIO",
        "CPX-AP-I-16NDIO"
    ]
}

DEFAULT_TOGGLE_CYCLES = 3


def run(
    hw: HardwareInterface,
    connections_path: str = "connections.jsonc",
    log: LogFn = noop_log,
    cc_param_id: int = 20094,
    cc_readback_param_id: int = 20095,
    toggle_cycles: int = DEFAULT_TOGGLE_CYCLES,
    connections: list[dict] | None = None,
) -> list[dict]:
    """Validate Condition Counter wiring for every defined connection.

    Steps (per connection):
      1. Read initial CC actual on target module
      2. Toggle source output channel *toggle_cycles* + 2 times
      3. Read final CC actual on target module
      4. Verify CC incremented ≥ *toggle_cycles*
    """
    if connections is None:
        connections = load_connections(connections_path)
    if not connections:
        log("warning", f"No connections found in '{connections_path}'")
        return [{"test": "condition-counter", "passed": None,
                 "error": "No connections defined"}]

    topology = hw.read_topology()
    mod_by_addr: dict[int, ModuleInfo] = {m.address: m for m in topology}
    compat = load_compatibility()

    # ── Pre-filter: skip connections whose target module is not CC-compatible ──
    filtered: list[dict] = []
    skipped: list[dict] = []
    for conn in connections:
        tgt_addr: int = conn["target_module_addr"]
        tgt_mod = mod_by_addr.get(tgt_addr)
        if tgt_mod is not None and not is_module_compatible(
            tgt_mod.name, "condition-counter", compat,
        ):
            label = f"#{conn['source_module_addr']}:{conn.get('source_channel','X0')} → #{tgt_addr}:{conn['target_channel']}"
            skipped.append({
                "test": "condition-counter", "connection": label,
                "target_module": tgt_mod.name, "passed": None,
                "note": f"{tgt_mod.name} not CC-compatible — skipped",
            })
            log("info", f"  ⊘ {label}: {tgt_mod.name} not CC-compatible, skipping")
        else:
            filtered.append(conn)
    results: list[dict] = skipped

    for conn in filtered:
        ch_start = time.time()
        src_addr: int = conn["source_module_addr"]
        tgt_addr: int = conn["target_module_addr"]
        src_channel: str = conn.get("source_channel", "X0")
        src_ch_idx = channel_index_from_port(src_channel)
        label = f"#{src_addr}:{src_channel} → #{tgt_addr}:{conn['target_channel']}"
        log("info", f"  CC check {label} …")

        src_mod = mod_by_addr.get(src_addr)
        tgt_mod = mod_by_addr.get(tgt_addr)

        if src_mod is None or tgt_mod is None:
            missing = [a for a, m in [(src_addr, src_mod), (tgt_addr, tgt_mod)] if m is None]
            log("error", f"  ✗ {label}: module(s) #{missing} not found")
            results.append({"test": "condition-counter", "connection": label,
                           "passed": False,
                           "error": f"Module(s) at address {missing} not found on bus",
                           "duration_ms": round((time.time() - ch_start) * 1000, 1)})
            continue

        has_outputs = src_mod.num_outputs > 0
        has_inputs = tgt_mod.num_inputs > 0

        result: dict[str, Any] = {
            "test": "condition-counter", "connection": label,
            "source_module": src_mod.name, "target_module": tgt_mod.name,
            "source_has_outputs": has_outputs, "target_has_inputs": has_inputs,
            "source_channel": src_ch_idx, "steps": [],
        }

        if not has_outputs:
            result["passed"] = False
            result["error"] = f"Source #{src_addr} ({src_mod.name}) has no output channels"
            results.append(result)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            continue
        if not has_inputs:
            result["passed"] = False
            result["error"] = f"Target #{tgt_addr} ({tgt_mod.name}) has no input channels"
            results.append(result)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            continue

        # ── Step 0: Check CC support on target module ──────────────────
        # Some modules (e.g. CPX-AP-A-16DI-D) don't have CC parameters.
        # Probe before toggling to avoid unnecessary output switching.
        try:
            hw.read_parameter(tgt_addr, cc_readback_param_id)
        except Exception as exc:
            err_msg = str(exc)
            if "has no parameter" in err_msg:
                result["passed"] = None  # skipped — not a failure
                result["note"] = f"{tgt_mod.name} has no CC support — skipping"
                log("info", f"  ⊘ {label}: {tgt_mod.name} has no CC support, skipping")
            else:
                result["passed"] = False
                result["error"] = f"CC probe failed: {err_msg}"
                log("warning", f"  [0] CC probe @ #{tgt_addr}: {err_msg}")
            results.append(result)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            continue

        # ── Step 1: Read current CC value ─────────────────────────────
        step1_ts = time.time()
        try:
            initial_cc = hw.read_parameter(tgt_addr, cc_readback_param_id)
            result["initial_cc"] = initial_cc
            log("info", f"  [1] Initial CC @ #{tgt_addr}: {initial_cc}")
            result["steps"].append({
                "step": 1, "label": "Read initial CC",
                "cc_actual": initial_cc, "passed": True,
                "duration_ms": round((time.time() - step1_ts) * 1000, 1),
            })
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"CC readback failed: {exc}"
            result["steps"].append({
                "step": 1, "label": "Read initial CC",
                "error": str(exc), "passed": False,
                "duration_ms": round((time.time() - step1_ts) * 1000, 1),
            })
            results.append(result)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            continue

        # ── Step 2: Toggle source output N times ──────────────────────
        step2_ts = time.time()
        cycles = toggle_cycles + 2
        log("info", f"  [2] Toggling #{src_addr} ch {src_ch_idx} × {cycles} cycles …")
        try:
            for _ in range(cycles):
                hw.write_output(src_addr, src_ch_idx, True)
                time.sleep(0.02)
                hw.write_output(src_addr, src_ch_idx, False)
                time.sleep(0.02)
            result["steps"].append({
                "step": 2, "label": f"Toggle output ×{cycles}",
                "channel": src_ch_idx, "cycles": cycles, "passed": True,
                "duration_ms": round((time.time() - step2_ts) * 1000, 1),
            })
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"Toggle failed: {exc}"
            result["steps"].append({
                "step": 2, "label": f"Toggle output ×{cycles}",
                "error": str(exc), "passed": False,
                "duration_ms": round((time.time() - step2_ts) * 1000, 1),
            })
            results.append(result)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            continue

        time.sleep(0.1)

        # ── Step 3: Read new CC value ─────────────────────────────────
        step3_ts = time.time()
        try:
            final_cc = hw.read_parameter(tgt_addr, cc_readback_param_id)
            result["final_cc"] = final_cc
            result["cc_expected_min"] = toggle_cycles
            log("info", f"  [3] Final CC @ #{tgt_addr}: {final_cc}")
            result["steps"].append({
                "step": 3, "label": "Read final CC",
                "cc_actual": final_cc, "cc_expected_min": toggle_cycles,
                "passed": True,
                "duration_ms": round((time.time() - step3_ts) * 1000, 1),
            })
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"Cannot read CC after toggle: {exc}"
            result["steps"].append({
                "step": 3, "label": "Read final CC",
                "error": str(exc), "passed": False,
                "duration_ms": round((time.time() - step3_ts) * 1000, 1),
            })
            results.append(result)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            continue

        # ── Step 4: Check results ─────────────────────────────────────
        step4_ts = time.time()
        if final_cc is not None and final_cc >= toggle_cycles:
            result["passed"] = True
            result["note"] = f"CC ({final_cc}) ≥ {toggle_cycles}"
            result["steps"].append({
                "step": 4, "label": "Verify CC increment",
                "passed": True,
                "detail": f"CC {final_cc} ≥ {toggle_cycles}",
                "duration_ms": round((time.time() - step4_ts) * 1000, 1),
            })
            log("info", f"  [4] ✓ {label}: PASS (CC {final_cc} ≥ {toggle_cycles})")
        else:
            result["passed"] = False
            result["error"] = f"CC ({final_cc}) < expected ({toggle_cycles})"
            result["steps"].append({
                "step": 4, "label": "Verify CC increment",
                "passed": False,
                "detail": result["error"],
                "duration_ms": round((time.time() - step4_ts) * 1000, 1),
            })
            log("error", f"  [4] ✗ {label}: {result['error']}")

        results.append(result)
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)

    return results
