"""Template for new CPX-AP test modules.

Copy this file, rename it, update TEST_DEFINITION and implement run().
Do NOT modify this file — it is the canonical empty template.
"""
from __future__ import annotations

import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, load_bench_config, noop_log

TEST_DEFINITION = {
    # Unique kebab-case identifier referenced in api.py dispatch
    "test_id": "test-api",
    "name": "Test API",
    "version": "1.0.0",
    "description": "Template test — replace with a real description",
    # Capabilities required on a module to qualify (see resolver.py for known values)
    "required_capabilities": [
        "digital_output"
    ],
    # 'physical', 'simulated' or 'virtual'
    # "required_wiring_type": "physical",
    # Module categories this test targets: 'output', 'input', 'inout', 'bus', …
    "supported_categories": [
        "output",
        "input",
        "inout",
    ],
    # 'safe' | 'caution' | 'destructive'
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    # Static parameters available to run()
    "parameters": {},
}


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    module_address: int | None = None,
) -> dict:
    """Execute the test against a single module.

    :param hw: Pre-connected HardwareInterface (do NOT call hw.connect here).
    :param log: Logging callback ``(level: str, message: str) -> None``.
    :param bench_config: Full bench configuration (connections, parameters, …).
    :param config_path: Path to bench_config.json.
    :param module_address: Bus address of the module under test.
    :returns: Result dict with at minimum ``{'passed': bool, 'results': list}``.
    """
    if bench_config is None:
        bench_config = load_bench_config(config_path)
    t0 = time.monotonic()

    log("info", f"[test-api] Running on module {module_address}")

    log("warning", "[test-api] This is a template test — replace with a real implementation")

    # ── Implement test logic here ────────────────────────────────────────────
    passed = True
    details: list[dict[str, Any]] = []
    # ────────────────────────────────────────────────────────────────────────

    duration_ms = round((time.monotonic() - t0) * 1000, 1)
    return {
        "test_id": TEST_DEFINITION["test_id"],
        "passed": passed,
        "duration_ms": duration_ms,
        "results": [
            {
                "module": str(module_address),
                "address": module_address,
                "passed": passed,
                "details": details,
                "duration_ms": duration_ms,
            }
        ],
    }
