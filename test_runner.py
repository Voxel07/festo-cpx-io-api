"""Backward-compatible thin wrapper around the ``tests/`` package.

The individual test implementations now live in separate modules under
``tests/``.  This file re-exports the original function names so that any
existing scripts or tooling that imports from ``test_runner`` directly
continues to work without changes.

For new code, prefer importing from the individual test modules::

    from tests.condition_counter import run as test_condition_counter
    from tests.valve_condition_counter import run as test_valve_condition_counter
    from tests.remanent_params import run as test_remanent_params
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from generate_system_config import validate_connections, compare_topology

# Re-export individual test runners under their original names
from tests.condition_counter import run as test_condition_counter          # noqa: F401
from tests.valve_condition_counter import run as test_valve_condition_counter  # noqa: F401
from tests.remanent_params import run as test_remanent_params              # noqa: F401
from tests.remanent_params import verify as test_remanent_params_verify   # noqa: F401
from tests.validate_connections import run as run_validate_connections     # noqa: F401
from tests.compare_topology import run as run_compare_topology             # noqa: F401


# ─── PSU Power Cycle Mock ──────────────────────────────────────────────────────

def psu_power_cycle(delay_s: float = 10.0) -> None:
    """Mock function for PSU-controlled power cycling.

    Replace this with your actual lab PSU control code (VISA/SCPI, serial,
    REST API, …) to physically power-cycle the CPX-AP system.
    """
    print(f"[PSU MOCK] Powering OFF for {delay_s}s …")
    time.sleep(delay_s)
    print("[PSU MOCK] Power ON — system should now be back online")


# ─── Bulk CLI runner ───────────────────────────────────────────────────────────

def run_all_tests(
    ip_address: str,
    connections_path: str = "connections.jsonc",
    topology_path: str = "topology.jsonc",
    timeout: float = 0,
) -> dict:
    """Run the complete test suite against a CPX-AP system and return results."""
    from tests.validate_connections import run as _vc
    from tests.compare_topology import run as _ct
    from tests.condition_counter import run as _cc
    from tests.valve_condition_counter import run as _vcc
    from tests.remanent_params import run as _rem

    output: dict = {
        "ip_address": ip_address,
        "timestamp": time.time(),
        "tests": {},
    }

    def _agg(raw):
        if isinstance(raw, list):
            ok = all(r.get("passed", False) for r in raw if r.get("passed") is not None)
            return {"results": raw, "passed": ok}
        return raw

    output["tests"]["validate-connections"] = _agg(_vc(ip_address, connections_path, timeout=timeout))
    output["tests"]["compare-topology"] = _agg(_ct(topology_path, ip_address, timeout=timeout))

    with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
        output["tests"]["condition-counter"] = _agg(_cc(cpx_ap, connections_path))
        output["tests"]["valve-condition-counter"] = _agg(_vcc(cpx_ap))
        output["tests"]["remanent-params"] = _agg(_rem(cpx_ap, connections_path))

    return output


if __name__ == "__main__":
    import sys
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.11"
    print(json.dumps(run_all_tests(ip), indent=2, default=str))

