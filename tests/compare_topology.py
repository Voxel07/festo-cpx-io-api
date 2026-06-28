"""Topology comparison test.

Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from hal import HardwareInterface
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
    topology_path: str,
    hw: HardwareInterface,
    log: LogFn = noop_log,
) -> dict:
    t_start = time.time()
    from config_models import BenchConfig
    try:
        config = BenchConfig.model_validate_json(Path(topology_path).read_text(encoding="utf-8"))
    except Exception as exc:
        err = f"Could not load BenchConfig: {exc}"
        log("error", err)
        return {"passed": False, "has_diff": True, "error": err,
                "changes": [], "added": [], "removed": [],
                "duration_ms": round((time.time() - t_start) * 1000, 1)}

    stored_modules = []
    for inst in config.module_instances:
        type_def = config.module_types.get(inst.module_type_ref)
        num_in = type_def.num_inputs if type_def else 0
        num_out = type_def.num_outputs if type_def else 0
        num_io = type_def.num_configurable if type_def else 0
        series = type_def.product_family if type_def else ""

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
            "Series": series,
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
        if any(x in name_up for x in ("EP", "EC", "PN", "PB", "EPLI")):
            return "Bus"
        if m.name.upper().startswith("VABX"):
            return "Valve"
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
            "Series": m.series, "Type": _derive_type(m),
            "NumOfInputs": m.num_inputs, "NumOfOutputs": m.num_outputs,
            "NumOfInOuts": m.num_inouts,
        }
        for m in live_info
    ]
    log("info", f"Live topology has {len(live_modules)} module(s)")

    stored_by_addr = {e["Adress"]: e for e in stored_modules}
    live_by_addr = {e["Adress"]: e for e in live_modules}

    changes, added, removed = [], [], []

    for addr, live_entry in live_by_addr.items():
        if addr in stored_by_addr:
            stored_entry = stored_by_addr[addr]
            for field in set(stored_entry) | set(live_entry):
                sv, lv = stored_entry.get(field), live_entry.get(field)
                if sv != lv:
                    changes.append({
                        "address": addr, "field": field,
                        "stored_value": sv, "live_value": lv,
                    })
        else:
            added.append(live_entry)

    for addr, entry in stored_by_addr.items():
        if addr not in live_by_addr:
            removed.append(entry)

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
