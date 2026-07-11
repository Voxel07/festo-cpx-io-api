"""Backward-compatible test runner — now uses Hardware Abstraction Layer.

Re-exports individual test runners and provides a ``run_all_tests`` entry
point that uses :class:`hal.SafeSession` for guaranteed output reset.
"""
from __future__ import annotations

import json
import sys
import time

from hal import CpxApHardware, SafeSession
from tests.test_compare_topology import run as run_compare_topology  # noqa: F401

# Re-export individual test runners under their original names
from tests.test_condition_counter import run as test_condition_counter  # noqa: F401
from tests.test_remanent_params import run as test_remanent_params  # noqa: F401
from tests.test_remanent_params import verify as test_remanent_params_verify  # noqa: F401
from tests.test_validate_connections import run as run_validate_connections  # noqa: F401
from tests.test_valve_condition_counter import run as test_valve_condition_counter  # noqa: F401


def psu_power_cycle(delay_s: float = 10.0) -> None:
    """Mock function for PSU-controlled power cycling."""
    print(f"[PSU MOCK] Powering OFF for {delay_s}s …")
    time.sleep(delay_s)
    print("[PSU MOCK] Power ON — system should now be back online")


def run_all_tests(
    ip_address: str,
    connections_path: str = "connections.jsonc",
    topology_path: str = "topology.jsonc",
    timeout: float = 0,
    bench_config_path: str = "data/bench_config.json",
) -> dict:
    """Run the complete test suite against a CPX-AP system.

    Uses :class:`SafeSession` which guarantees all outputs are reset to LOW
    and the connection is closed, even on exception.
    """
    output: dict = {
        "ip_address": ip_address,
        "timestamp": time.time(),
        "tests": {},
    }

    def _agg(raw):
        if isinstance(raw, list):
            ok = all(r.get("passed", False) for r in raw if isinstance(r, dict) and r.get("passed") is not None)
            return {"results": raw, "passed": ok}
        return raw

    hw = CpxApHardware()
    with SafeSession(hw, ip_address, timeout) as iface:
        output["tests"]["connection-validation"] = _agg(
            run_validate_connections(
                hw_or_ip=iface,
                config_path=bench_config_path,
            )
        )
        output["tests"]["compare-topology"] = _agg(
            run_compare_topology(hw=iface, config_path=bench_config_path)
        )
        output["tests"]["condition-counter"] = _agg(
            test_condition_counter(hw=iface, config_path=bench_config_path)
        )
        output["tests"]["valve-condition-counter"] = _agg(
            test_valve_condition_counter(hw=iface, config_path=bench_config_path)
        )
        output["tests"]["remanent-params"] = _agg(
            test_remanent_params(hw=iface, config_path=bench_config_path)
        )

    return output


if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.11"
    print(json.dumps(run_all_tests(ip), indent=2, default=str))

