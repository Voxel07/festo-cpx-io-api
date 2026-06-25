"""Generate a topology configuration file for a CPX-AP system."""

import json
from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from cpx_io.cpx_system.cpx_ap.ap_module import ApModule


def _module_series(module: ApModule) -> str:
    """Determine AP bus series from the module order text."""
    name = getattr(module.apdd_information, "order_text", "") or ""
    if "CPX-AP-A" in name:
        return "CPX-AP-A"
    if "CPX-AP-I" in name:
        return "CPX-AP-I"
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
    with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
        entries = [module_to_topology_entry(m) for m in cpx_ap.modules]

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


if __name__ == "__main__":
    _topology = generate_topology(ip_address="192.168.1.11", timeout=0)
    save_topology(_topology)
    print(json.dumps(_topology, indent=4))