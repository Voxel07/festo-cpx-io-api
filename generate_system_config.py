"""Generate a topology configuration file for a CPX-AP system.

Also provides connection validation: reads the connections.jsonc file, sets output
channels on source modules, and reads input channels on target modules to verify
the wiring is intact.
"""

import json
from pathlib import Path
from typing import Any

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from cpx_io.cpx_system.cpx_ap.ap_module import ApModule


def _module_series(module: ApModule) -> str:
    """Determine AP bus series from the module order text."""
    name = getattr(module.apdd_information, "order_text", "") or ""
    if "CPX-AP-A" in name:
        return "CPX-AP-A"
    if "CPX-AP-I" in name:
        return "CPX-AP-I"
    if name.upper().startswith("VABX"):
        return "VABX"
    return "Other"


def _module_type(module: ApModule) -> str:
    """Derive topology type string from module channel configuration."""
    pure_inputs = [c for c in module.channels.inputs if c.direction == "in"]
    pure_outputs = [c for c in module.channels.outputs if c.direction == "out"]
    has_inouts = len(module.channels.inouts) > 0

    if has_inouts or (pure_inputs and pure_outputs):
        return "In/Out"
    if pure_inputs:
        return "Input"
    if pure_outputs:
        return "Output"
    return "Input"


def module_to_topology_entry(module: ApModule) -> dict:
    """Convert an ApModule instance to a topology entry dict."""
    pure_inputs = [c for c in module.channels.inputs if c.direction == "in"]
    pure_outputs = [c for c in module.channels.outputs if c.direction == "out"]
    inouts = module.channels.inouts

    return {
        "Name": module.apdd_information.order_text,
        "Modulecode": module.information.module_code,
        "ProductKey": module.information.product_key,
        "Series": _module_series(module),
        "Adress": module.position,
        "Type": _module_type(module),
        "NumOfInputs": len(pure_inputs),
        "NumOfOutputs": len(pure_outputs),
        "NumOfInOuts": len(inouts),
    }


def generate_topology(ip_address: str, timeout: float = 0) -> dict:
    """Connect to a CPX-AP system and return its topology as a dict."""
    from hal import CrossProcessLock
    lock = CrossProcessLock(ip_address)
    lock.acquire(timeout=30.0)
    try:
        with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
            entries = [module_to_topology_entry(m) for m in cpx_ap.modules]
    finally:
        lock.release()

    return {
        "Name": f"Topology {ip_address}",
        "Description": "Auto-generated topology from CPX-AP system",
        "Version": "1.0",
        "Topology": entries,
    }


def save_topology(topology: dict, output_path: str = "topology.jsonc") -> None:
    """Serialize *topology* to a JSONC file at *output_path*."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=4)


def compare_topology(stored_path: str, ip_address: str, timeout: float = 0) -> dict:
    """Compare a stored topology file with the live system configuration.

    Returns a dict with:
    * ``stored``  – the topology loaded from *stored_path*
    * ``live``    – the topology read from the device
    * ``changes`` – list of change dicts (each has ``address``, ``field``,
                    ``stored_value``, ``live_value``)
    * ``added``   – modules present in live but not in stored (by address)
    * ``removed`` – modules present in stored but not in live (by address)
    * ``has_diff`` – bool, True when any difference was found
    """
    with open(stored_path, "r", encoding="utf-8") as f:
        stored = json.load(f)

    live = generate_topology(ip_address, timeout)

    stored_by_addr = {entry["Adress"]: entry for entry in stored.get("Topology", [])}
    live_by_addr = {entry["Adress"]: entry for entry in live.get("Topology", [])}

    changes = []
    for addr, live_entry in live_by_addr.items():
        if addr in stored_by_addr:
            stored_entry = stored_by_addr[addr]
            for field in set(stored_entry) | set(live_entry):
                sv = stored_entry.get(field)
                lv = live_entry.get(field)
                if sv != lv:
                    changes.append(
                        {
                            "address": addr,
                            "field": field,
                            "stored_value": sv,
                            "live_value": lv,
                        }
                    )

    added = [e for addr, e in live_by_addr.items() if addr not in stored_by_addr]
    removed = [e for addr, e in stored_by_addr.items() if addr not in live_by_addr]

    return {
        "stored": stored,
        "live": live,
        "changes": changes,
        "added": added,
        "removed": removed,
        "has_diff": bool(changes or added or removed),
    }


# ─── Connection Validation ─────────────────────────────────────────────────────

def _find_module_by_addr(cpx_ap: CpxAp, addr: int) -> ApModule:
    """Return the module at the given bus address (0-based position)."""
    for m in cpx_ap.modules:
        if m.position == addr:
            return m
    raise ValueError(f"No module found at address {addr}")


def _channel_index_from_port(port: str) -> int:
    """Convert a port label like 'X0', 'X3' to a 0-based channel index."""
    return int(port.lstrip("X"))


def validate_single_connection(
    cpx_ap: CpxAp,
    conn: dict,
    pulse_duration_s: float = 0.3,
) -> dict:
    """Test one I/O connection by pulsing the source output and reading the target input.

    :param cpx_ap: Connected CPX-AP system instance
    :param conn: Connection dict from connections.jsonc with keys:
                 ``source_module_addr``, ``source_channel``,
                 ``target_module_addr``, ``target_channel``
    :param pulse_duration_s: How long (seconds) to hold the output HIGH
    :return: Validation result dict with ``passed``, ``expected``, ``actual``,
             ``source_addr``, ``target_addr``, ``source_channel``, ``target_channel``
    """
    import time

    src_addr = conn["source_module_addr"]
    tgt_addr = conn["target_module_addr"]
    src_ch = conn["source_channel"]  # e.g. 'X0'
    tgt_ch = conn["target_channel"]  # e.g. 'X0'

    src_mod = _find_module_by_addr(cpx_ap, src_addr)
    tgt_mod = _find_module_by_addr(cpx_ap, tgt_addr)

    src_name = getattr(src_mod.apdd_information, "order_text", "") or ""
    tgt_name = getattr(tgt_mod.apdd_information, "order_text", "") or ""
    cpp_src = 2 if "M12" in src_name else 1
    cpp_tgt = 2 if "M12" in tgt_name else 1

    port_num_src = int(src_ch.lstrip("X"))
    num_in_src = len([c for c in src_mod.channels.inputs if c.direction == "in"])
    out_base = port_num_src * cpp_src - num_in_src

    port_num_tgt = int(tgt_ch.lstrip("X"))
    base_idx_tgt = port_num_tgt * cpp_tgt

    # Ensure LOW baseline
    try:
        for i in range(cpp_src):
            src_mod.write_channel(out_base + i, False)
    except Exception:
        pass  # some modules may not support individual channel writes
    time.sleep(0.05)

    # Read baseline input
    try:
        baseline_vals = [tgt_mod.read_channel(base_idx_tgt + i) for i in range(cpp_tgt)]
        baseline = all(baseline_vals)
    except Exception:
        baseline = False

    # Pulse HIGH
    try:
        for i in range(cpp_src):
            src_mod.write_channel(out_base + i, True)
    except Exception:
        try:
            # Fallback: try write_channels with a list
            all_vals = [False] * len(src_mod.channels.outputs)
            for i in range(cpp_src):
                all_vals[out_base + i] = True
            src_mod.write_channels(all_vals)
        except Exception as exc:
            return {
                "passed": False,
                "error": f"Cannot write to source module: {exc}",
                "source_addr": src_addr,
                "target_addr": tgt_addr,
                "source_channel": src_ch,
                "target_channel": tgt_ch,
            }

    time.sleep(pulse_duration_s)
    try:
        actual_vals = [tgt_mod.read_channel(base_idx_tgt + i) for i in range(cpp_tgt)]
        actual = all(actual_vals)
    except Exception:
        actual = False

    # Restore LOW
    try:
        for i in range(cpp_src):
            src_mod.write_channel(out_base + i, False)
    except Exception:
        pass

    passed = bool(actual) and not baseline

    return {
        "passed": passed,
        "expected": True,
        "actual": actual,
        "baseline": baseline,
        "source_addr": src_addr,
        "target_addr": tgt_addr,
        "source_channel": src_ch,
        "target_channel": tgt_ch,
    }


def validate_connections(
    ip_address: str,
    connections_path: str = "connections.jsonc",
    timeout: float = 0,
    pulse_duration_s: float = 0.3,
) -> dict:
    """Validate all I/O connections defined in *connections_path* against the live system.

    Opens a CPX-AP connection, iterates every connection entry, pulses the source
    output, reads the target input, and returns a detailed report.

    :returns: Dict with keys ``ip_address``, ``total``, ``passed``, ``failed``,
              ``error``, ``results`` (list of per-connection dicts),
              ``all_passed`` (bool)
    """
    import time

    path = Path(connections_path)
    if not path.exists():
        return {
            "ip_address": ip_address,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "error": f"Connections file not found: {path.resolve()}",
            "results": [],
            "all_passed": False,
        }

    with open(path, encoding="utf-8") as f:
        conn_data = json.load(f)

    connections = conn_data.get("connections", [])
    if not connections:
        return {
            "ip_address": ip_address,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "error": "No connections defined in the file.",
            "results": [],
            "all_passed": True,
        }

    results: list[dict] = []
    passed_count = 0

    from hal import CrossProcessLock
    lock = CrossProcessLock(ip_address)
    lock.acquire(timeout=30.0)
    try:
        with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
            for conn in connections:
                # Skip connections where source/target is a valve body (VABX) – those
                # have no writable M12 ports in the standard way.
                try:
                    result = validate_single_connection(cpx_ap, conn, pulse_duration_s)
                except Exception as exc:
                    result = {
                        "passed": False,
                        "error": str(exc),
                        "source_addr": conn.get("source_module_addr"),
                        "target_addr": conn.get("target_module_addr"),
                        "source_channel": conn.get("source_channel"),
                        "target_channel": conn.get("target_channel"),
                    }
                results.append(result)
                if result.get("passed"):
                    passed_count += 1
                time.sleep(0.05)
    finally:
        lock.release()

    failed_count = len(results) - passed_count

    return {
        "ip_address": ip_address,
        "total": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "results": results,
        "all_passed": passed_count == len(results),
    }


# ─── Topology persistence with valve mounting info ────────────────────────────

def save_topology_with_valves(
    topology: dict,
    output_path: str = "topology.jsonc",
) -> None:
    """Save *topology* to *output_path*, preserving ``MountedValves`` fields.

    Unlike :func:`save_topology`, this does NOT re-read from the device – it
    writes the in-memory topology as-is so that valve-mount edits are kept.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=4)


if __name__ == "__main__":
    _topology = generate_topology(ip_address="192.168.0.11", timeout=0)
    save_topology(_topology)
    print(json.dumps(_topology, indent=4))