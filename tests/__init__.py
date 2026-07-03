"""Test registry for the CPX-AP validation suite.

Each entry maps a test ID (as used by the API and UI) to the module that
implements it and metadata used for display / execution.

Usage::

    from tests import REGISTRY

    entry = REGISTRY["validate-connections"]
    result = entry["run"](ip_address="192.168.0.11", connections_path="connections.jsonc")
"""
from __future__ import annotations

from . import (
    validate_connections,
    compare_topology,
    condition_counter,
    valve_condition_counter,
    remanent_params,
    factory_reset,
    open_load_diag,
    output_toggle,
    valve_toggle,
    dio_toggle,
)

# Canonical registry used by api.py to dispatch test runs.
# Keys match the ``tests`` list sent from the frontend.
REGISTRY: dict[str, dict] = {
    "validate-connections": {
        "label": "Connection Validation",
        "description": "Pulses each source output and reads the connected input.",
        "module": validate_connections,
        "needs_cpx_connection": False,   # opens its own CpxAp internally
    },
    "compare-topology": {
        "label": "Topology Comparison",
        "description": "Compares topology.jsonc against the live device.",
        "module": compare_topology,
        "needs_cpx_connection": False,
    },
    "condition-counter": {
        "label": "Condition Counter",
        "description": "Reads CC params (20094/20095) for all wired connections.  Optionally verifies persistence after power cycle.",
        "module": condition_counter,
        "needs_cpx_connection": True,    # caller provides a CpxAp instance
    },
    "valve-condition-counter": {
        "label": "Valve CC",
        "description": "Triggers CC maintenance warning on VABX valve terminals.",
        "module": valve_condition_counter,
        "needs_cpx_connection": True,
    },
    "remanent-params": {
        "label": "Remanent Parameters",
        "description": "Writes test values to remanent params and verifies persistence after power cycle.",
        "module": remanent_params,
        "needs_cpx_connection": True,
    },
    "factory-reset": {
        "label": "Factory Reset",
        "description": "Writes test values, performs normal/factory reset, verifies parameter persistence/clearance.",
        "module": factory_reset,
        "needs_cpx_connection": True,
    },
    "open-load-diag": {
        "label": "Open-Load Diagnostic",
        "description": "Activates outputs, enables open-load diagnostic, verifies diagnosis raised and clears.",
        "module": open_load_diag,
        "needs_cpx_connection": True,
    },
    "output-toggle": {
        "label": "Output Toggle",
        "description": "Toggle all digital output channels ON/OFF and verify state changes.",
        "module": output_toggle,
        "needs_cpx_connection": True,
    },
    "valve-toggle": {
        "label": "Valve Toggle",
        "description": "Toggle all valve channels ON/OFF and verify state changes.",
        "module": valve_toggle,
        "needs_cpx_connection": True,
    },
    "dio-toggle": {
        "label": "DIO Toggle",
        "description": "Configure DIO channels to output mode, toggle ON/OFF, and restore to input mode.",
        "module": dio_toggle,
        "needs_cpx_connection": True,
    },
}
