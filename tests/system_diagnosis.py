"""System Diagnosis validation test."""
from __future__ import annotations

from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, noop_log

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
    if module_address is None:
        return {"passed": False, "diagnosis": "No module address provided", "results": []}
    diag = hw.read_diagnosis(module_address)
    return {
        "passed": diag is not None,
        "diagnosis": str(diag),
        "results": [{"module": str(module_address), "passed": diag is not None}]
    }
