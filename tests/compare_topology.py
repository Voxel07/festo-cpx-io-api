"""Topology comparison test.

Loads a stored *topology.jsonc* and compares it field-by-field against the
live CPX-AP device, reporting added/removed/changed modules.
"""
from __future__ import annotations

import json
from pathlib import Path

from generate_system_config import generate_topology
from ._base import LogFn, noop_log


def run(
    topology_path: str,
    ip_address: str,
    timeout: float = 0,
    log: LogFn = noop_log,
) -> dict:
    """Compare *topology_path* against the live device at *ip_address*.

    Returns a result dict::

        {
            "passed": bool,        # True when no differences found
            "has_diff": bool,
            "changes": [...],
            "added": [...],
            "removed": [...],
            "stored": {...},
            "live": {...},
            "error": str | None,
        }
    """
    log("info", f"Loading stored topology from '{topology_path}' …")
    path = Path(topology_path)
    if not path.exists():
        err = f"Topology file not found: {path.resolve()}"
        log("error", err)
        return {"passed": False, "has_diff": True, "error": err,
                "changes": [], "added": [], "removed": []}

    with open(path, encoding="utf-8") as f:
        stored = json.load(f)

    stored_modules = stored.get("Topology", [])
    log("info", f"Stored topology has {len(stored_modules)} module(s)")

    log("info", f"Reading live topology from {ip_address} …")
    try:
        live = generate_topology(ip_address, timeout)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log("error", f"Device connection failed: {err}")
        return {"passed": False, "has_diff": True, "error": err,
                "changes": [], "added": [], "removed": []}

    live_modules = live.get("Topology", [])
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
        "stored": stored,
        "live": live,
    }
