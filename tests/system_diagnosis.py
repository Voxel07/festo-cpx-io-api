"""System Diagnosis validation test."""
from __future__ import annotations
from hal import HardwareInterface

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


def run(hw: HardwareInterface, module_address: int) -> dict:
    diag = hw.read_diagnosis(module_address)
    return {
        "passed": diag is not None,
        "diagnosis": str(diag),
        "results": [{"module": str(module_address), "passed": diag is not None}]
    }
