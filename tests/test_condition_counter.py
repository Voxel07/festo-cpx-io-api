"""Condition-counter validation for wired CPX-AP-I-16* channels.

The resolver owns module eligibility and wire orientation.  This module only
executes already-resolved routes through the hardware abstraction layer.
"""
from __future__ import annotations

import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface
from resolver import TestResolver

from ._base import LogFn, load_bench_config, noop_log

TEST_DEFINITION = {
    "test_id": "condition-counter",
    "name": "Condition Counter",
    "version": "2.0.0",
    "description": "Toggle a wired output and verify a CPX-AP-I-16* channel counter increment",
    "required_capabilities": ["condition_counter"],
    "supported_categories": ["input", "output", "inout"],
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "cc_readback_param_id": 20095,
        "toggle_cycles": 3,
        "power_supply_channels": [1, 2, 4],
        "power_supply_voltage": 24.0,
        "reconnect_wait": 8.0,
    },
    "compatible_modules": ["CPX-AP-I-16*"],
}


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    module_address: int | None = None,
) -> list[dict[str, Any]]:
    """Count rising edges on every resolved route for one CC module."""
    config = bench_config or load_bench_config(config_path)
    if config is None:
        return [{"test": "condition-counter", "passed": None, "error": "No bench configuration"}]

    routes = TestResolver.resolve_condition_counter_routes(config, module_address)
    if not routes:
        return [{
            "test": "condition-counter", "passed": None,
            "error": f"No wired output route for CPX-AP-I-16* module {module_address}",
        }]

    parameter_id = int(TEST_DEFINITION["parameters"]["cc_readback_param_id"])
    cycles = int(TEST_DEFINITION["parameters"]["toggle_cycles"])
    results: list[dict[str, Any]] = []

    for route in routes:
        started = time.time()
        label = (
            f"#{route.output_address}:{route.output_channel} -> "
            f"#{route.counter_address}:{route.counter_channel}"
        )
        result: dict[str, Any] = {
            "test": "condition-counter", "connection": label,
            "wiring_id": route.wiring_id,
            "source_address": route.output_address,
            "target_address": route.counter_address,
            "counter_instance": route.counter_instance,
            "toggle_cycles": cycles,
        }
        try:
            if route.output_is_configurable:
                hw.configure_port_direction(route.output_address, route.output_channel, True)
            if route.counter_is_configurable:
                hw.configure_port_direction(route.counter_address, route.counter_channel, False)

            hw.write_output(route.output_address, route.output_channel, False)
            initial = int(hw.read_parameter(
                route.counter_address, parameter_id, instance=route.counter_instance
            ))
            for _ in range(cycles):
                hw.write_output(route.output_address, route.output_channel, True)
                time.sleep(0.02)
                hw.write_output(route.output_address, route.output_channel, False)
                time.sleep(0.02)
            final = int(hw.read_parameter(
                route.counter_address, parameter_id, instance=route.counter_instance
            ))
            increment = final - initial
            result.update(
                initial_cc=initial, final_cc=final, cc_increment=increment,
                passed=increment == cycles,
            )
            if increment != cycles:
                result["error"] = f"Counter increment {increment}, expected {cycles}"
            log("info" if result["passed"] else "error", f"{label}: {increment}/{cycles} edges")
        except Exception as exc:
            result.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                hw.write_output(route.output_address, route.output_channel, False)
            except Exception:
                pass
            # Restoring all touched DIO ports after each route makes the test
            # safe even when it is called outside SafeSession.
            hw.restore_port_directions()
            result["duration_ms"] = round((time.time() - started) * 1000, 1)
        results.append(result)
    return results


def run_with_power_cycle(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    module_address: int | None = None,
) -> list[dict[str, Any]]:
    """Run CC edge validation, then verify successful counters persist."""
    config = bench_config or load_bench_config(config_path)
    results = run(hw, log, config, config_path, module_address)
    successful = [item for item in results if item.get("passed") is True]
    if not successful or config is None:
        return results
    if not config.power_supply or not config.power_supply.comport:
        return results

    from power_supply import PowerCycleSession

    channels = [
        channel for channel in (config.power_supply.pl_channel, config.power_supply.ps_channel)
        if channel is not None
    ] or list(TEST_DEFINITION["parameters"]["power_supply_channels"])
    try:
        with PowerCycleSession(
            comport=config.power_supply.comport,
            channels=channels,
            voltage=float(TEST_DEFINITION["parameters"]["power_supply_voltage"]),
            off_time=1.0,
            reconnect_wait=float(TEST_DEFINITION["parameters"]["reconnect_wait"]),
        ) as supply:
            supply.cycle(hw, config.test_bench.ip_address)
    except Exception as exc:
        results.append({"test": "condition-counter", "phase": "power-cycle", "passed": False,
                        "error": f"Power cycle failed: {exc}"})
        return results

    parameter_id = int(TEST_DEFINITION["parameters"]["cc_readback_param_id"])
    for prior in successful:
        persisted: dict[str, Any] = {
            "test": "condition-counter", "phase": "power-cycle",
            "connection": prior["connection"], "target_address": prior["target_address"],
        }
        try:
            actual = int(hw.read_parameter(
                prior["target_address"], parameter_id, instance=prior["counter_instance"]
            ))
            persisted.update(cc_after_power_cycle=actual, passed=actual >= prior["final_cc"])
            if not persisted["passed"]:
                persisted["error"] = f"Counter {actual} is below pre-cycle value {prior['final_cc']}"
        except Exception as exc:
            persisted.update(passed=False, error=str(exc))
        results.append(persisted)
    return results
