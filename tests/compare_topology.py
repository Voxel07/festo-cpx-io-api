"""Topology comparison test.

Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from hal import HardwareInterface
from config_models import BenchConfig
from ._base import LogFn, noop_log

TEST_DEFINITION = {
    "test_id": "compare-topology",
    "name": "Topology Comparison",
    "version": "1.0.0",
    "description": "Compare stored topology against live hardware",
    "required_capabilities": [],
    "supported_categories": [
        "bus"
    ],
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": True,
    "parameters": {},
    "compatible_modules": [
        "CPX-AP-A-EP*",
        "CPX-AP-A-EC*",
        "CPX-AP-A-PN*",
        "CPX-AP-A-PB*",
        "CPX-AP-I-EP*",
        "CPX-AP-I-EC*",
        "CPX-AP-I-PN*",
        "CPX-AP-I-PB*"
    ]
}


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    module_address: int | None = None,
) -> dict:
    t_start = time.time()
    if not bench_config:
        err = "No bench_config provided for comparison"
        log("error", err)
        return {"passed": False, "has_diff": True, "error": err,
                "changes": [], "added": [], "removed": [],
                "duration_ms": round((time.time() - t_start) * 1000, 1)}

    stored_modules = []
    for inst in bench_config.module_instances:
        type_def = bench_config.module_types.get(inst.module_type_ref)
        num_in = inst.num_inputs
        num_out = inst.num_outputs
        num_io = inst.num_inouts

        cat = inst.category.value
        m_type = "Input"
        if cat == "valve":
            m_type = "Valve"
        elif cat == "inout":
            m_type = "In/Out"
        elif cat == "output":
            m_type = "Output"
        elif cat == "bus":
            m_type = "Bus"

        stored_modules.append({
            "Name": inst.display_name,
            "Modulecode": inst.module_code,
            "ProductKey": inst.product_key,
            "Adress": inst.address,
            "Type": m_type,
            "NumOfInputs": num_in,
            "NumOfOutputs": num_out,
            "NumOfInOuts": num_io,
        })

    log("info", f"Stored config has {len(stored_modules)} module(s)")

    log("info", "Reading live topology …")
    try:
        live_info = hw.read_topology()
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log("error", f"Device connection failed: {err}")
        return {"passed": False, "has_diff": True, "error": err,
                "changes": [], "added": [], "removed": [],
                "duration_ms": round((time.time() - t_start) * 1000, 1)}

    def _derive_type(m) -> str:
        """Derive module type string from channel counts (matches stored format)."""
        name_up = m.name.upper()
        if m.name.upper().startswith("VABX") or m.name.upper().startswith("VMPAL") or m.name.upper().startswith("VAEM"):
            return "Valve"
        if any(x in name_up for x in ("EP", "EC", "PN", "PB", "EPLI")):
            return "Bus"
        if m.num_inouts > 0 or (m.num_inputs > 0 and m.num_outputs > 0):
            return "In/Out"
        if m.num_outputs > 0:
            return "Output"
        if m.num_inputs > 0:
            return "Input"
        return "Input"

    live_modules = [
        {
            "Name": m.name, "Modulecode": m.module_code,
            "ProductKey": m.product_key, "Adress": m.address,
            "Type": _derive_type(m),
            "NumOfInputs": m.num_inputs, "NumOfOutputs": m.num_outputs,
            "NumOfInOuts": m.num_inouts,
        }
        for m in live_info
    ]
    log("info", f"Live topology has {len(live_modules)} module(s)")

    from difflib import SequenceMatcher

    # Sequence of tuples for matching: (Name, Modulecode)
    # Using these instead of Address allows us to track modules that were shifted.
    stored_seq = [(m.get("Name"), m.get("Modulecode")) for m in stored_modules]
    live_seq = [(m.get("Name"), m.get("Modulecode")) for m in live_modules]

    sm = SequenceMatcher(None, stored_seq, live_seq)
    changes, added, removed = [], [], []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for i, j in zip(range(i1, i2), range(j1, j2)):
                stored_entry = stored_modules[i]
                live_entry = live_modules[j]
                for field in set(stored_entry) | set(live_entry):
                    sv, lv = stored_entry.get(field), live_entry.get(field)
                    if sv != lv:
                        changes.append({
                            "address": live_entry["Adress"],
                            "field": field,
                            "stored_value": sv,
                            "live_value": lv,
                        })
        elif tag == 'replace':
            for i in range(i1, i2):
                removed.append(stored_modules[i])
            for j in range(j1, j2):
                added.append(live_modules[j])
        elif tag == 'delete':
            for i in range(i1, i2):
                removed.append(stored_modules[i])
        elif tag == 'insert':
            for j in range(j1, j2):
                added.append(live_modules[j])

    has_diff = bool(changes or added or removed)

    if not has_diff:
        log("info", f"✓ Topology matches — all {len(live_modules)} module(s) identical")
    else:
        for c in changes:
            log("warning",
                f"  Changed  #{c['address']}.{c['field']}: "
                f"{c['stored_value']!r} → {c['live_value']!r}")
        for m in added:
            log("warning", f"  Added    #{m['Adress']} ({m.get('Name', '?')})")
        for m in removed:
            log("warning", f"  Removed  #{m['Adress']} ({m.get('Name', '?')})")
        log("warning", f"  Total: {len(changes)} change(s), {len(added)} added, {len(removed)} removed")

    return {
        "passed": not has_diff,
        "has_diff": has_diff,
        "changes": changes,
        "added": added,
        "removed": removed,
        "stored": {"Topology": stored_modules},
        "live": {"Topology": live_modules},
        "duration_ms": round((time.time() - t_start) * 1000, 1),
    }
