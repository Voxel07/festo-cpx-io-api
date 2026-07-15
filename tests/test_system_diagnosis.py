"""System Diagnosis validation test."""
from __future__ import annotations

from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, load_bench_config, noop_log

TEST_DEFINITION = {
    "test_id": "system-diagnosis",
    "name": "System Diagnosis",
    "version": "1.0.0",
    "description": "Read global system diagnosis registers",
    "required_capabilities": [
        "system_diagnosis"
    ],
    "supported_categories": [
        "bus"
    ],
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {},
}


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    module_address: int | None = None,
) -> dict:
    if bench_config is None:
        bench_config = load_bench_config(config_path)
    if module_address is None:
        return {"passed": False, "diagnosis": "No module address provided", "results": []}
    diag = hw.read_diagnosis(module_address)
    return {
        "passed": diag is not None,
        "diagnosis": str(diag),
        "results": [{"module": str(module_address), "passed": diag is not None}]
    }
