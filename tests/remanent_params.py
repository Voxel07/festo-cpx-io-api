"""Remanent parameters test."""

import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, noop_log
from .factory_reset import _get_reset_param_specs_for_module, _verify_persisted, _write_test_values

TEST_DEFINITION = {
    "test_id": "remanent-params",
    "name": "Remanent Parameters",
    "version": "1.1.0",
    "description": "Write test values to remanent parameters and verify they survive a power cycle.",
    "required_capabilities": ["remanent_params"],
    "supported_categories": ["input", "output", "inout", "valve", "bus"],
    "safety_class": "caution",
    "allowed_in_ci": False,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "power_supply_comport": None,
        "power_supply_channels": [1, 2, 4],
        "power_supply_voltage": 24.0,
        "reconnect_wait": 8.0,
    },
    "compatible_modules": [
        "CPX-AP-A*",
        "CPX-AP-I*",
        "VABX*"
    ]
}

def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    module_address: int | None = None,
) -> list[dict]:
    """Phase 1: write test values to remanent parameters."""
    kwargs = TEST_DEFINITION["parameters"]
    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    log("info", f"Remanent-params write phase on {len(topology)} module(s)")
    results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Module #{addr} {mod_info.name} …")

        result: dict[str, Any] = {
            "test": "remanent-params",
            "module": mod_info.name,
            "address": addr,
            "phase": "write",
        }

        # Reuse parameter specification logic from factory-reset
        param_specs = _get_reset_param_specs_for_module(mod_info.name, kwargs, log)

        write_result = _write_test_values(hw, addr, param_specs, log)
        result.update(write_result)

        result["passed"] = write_result.get("write_ok", False)
        result["needs_power_cycle"] = True
        result["note"] = "Write phase complete. Run verify phase after power cycle."
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        
        # Save specs into result so verify phase knows what to check
        result["_param_specs"] = param_specs

        if result["passed"]:
            log("info", f"  ✓ #{addr} {mod_info.name}: write phase PASS")
        else:
            log("error", f"  ✗ #{addr} {mod_info.name}: write phase FAIL")
            if "write_errors" in write_result:
                result["error"] = "Write errors: " + ", ".join(write_result["write_errors"])
            else:
                result["error"] = "Write phase failed."

        results.append(result)

    return results


def verify(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    module_address: int | None = None,
    write_results: list[dict] | None = None,
) -> list[dict]:
    """Phase 2: verify test values survived a power cycle."""
    kwargs = TEST_DEFINITION["parameters"]
    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    log("info", "Remanent-params verify phase")
    results: list[dict] = []

    # Map address to param_specs from write phase if available
    specs_map = {}
    if write_results:
        for r in write_results:
            if "_param_specs" in r:
                specs_map[r["address"]] = r["_param_specs"]

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Module #{addr} {mod_info.name} …")

        result: dict[str, Any] = {
            "test": "remanent-params-verify",
            "module": mod_info.name,
            "address": addr,
            "phase": "verify",
        }

        # Retrieve specs from write phase, or regenerate them
        param_specs = specs_map.get(addr)
        if not param_specs:
            param_specs = _get_reset_param_specs_for_module(mod_info.name, kwargs, log)

        verify_res = _verify_persisted(hw, addr, param_specs, log)
        result.update(verify_res)

        result["passed"] = verify_res.get("persist_ok", False)
        if not result["passed"]:
            errs = verify_res.get("persist_errors", [])
            result["error"] = "Verify errors: " + ", ".join(errs) if errs else "Verify failed"
            log("error", f"  ✗ #{addr}: {result['error']}")
        else:
            log("info", f"  ✓ #{addr} {mod_info.name}: values persisted after power cycle")

        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results


def run_with_power_cycle(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    module_address: int | None = None,
) -> list[dict]:
    """Full end-to-end remanent-params test with bench power cycle."""
    from power_supply import PowerCycleSession, PowerSupplyNotAvailable

    if not bench_config or not bench_config.power_supply or not bench_config.power_supply.comport:
        msg = "Power supply is required for remanent-params but not configured in bench_config.json"
        log("error", f"  {msg}. Aborting test.")
        return [{"test": "remanent-params", "passed": False, "error": msg}]

    power_supply_comport = bench_config.power_supply.comport
    ch = []
    if bench_config.power_supply.pl_channel is not None:
        ch.append(bench_config.power_supply.pl_channel)
    if bench_config.power_supply.ps_channel is not None:
        ch.append(bench_config.power_supply.ps_channel)
    power_supply_channels = ch if ch else TEST_DEFINITION["parameters"]["power_supply_channels"]

    ip_address = bench_config.test_bench.ip_address
    power_supply_voltage = TEST_DEFINITION["parameters"]["power_supply_voltage"]
    reconnect_wait = TEST_DEFINITION["parameters"]["reconnect_wait"]
    off_time = 1.0

    # ── Test power supply connection first ──
    log("info", "  Testing power supply connection ...")
    try:
        with PowerCycleSession(
            comport=power_supply_comport,
            channels=power_supply_channels,
            voltage=power_supply_voltage,
            off_time=off_time,
            reconnect_wait=reconnect_wait,
        ) as ps:
            pass
        log("info", "  Power supply connection test successful ✓")
    except Exception as exc:
        log("error", f"  Power supply connection failed: {exc}. Aborting test.")
        return [{"test": "remanent-params", "passed": False, "error": f"Power supply connection failed: {exc}"}]

    # ── Phase 1: write ────────────────────────────────────────────────────────
    write_results = run(
        hw=hw,
        log=log,
        bench_config=bench_config,
        module_address=module_address,
    )

    write_failures = [r for r in write_results if r.get("passed") is False]
    if write_failures:
        log("warning", "Write phase had failures — skipping power cycle")
        return write_results

    # ── Phase 2: power cycle ──────────────────────────────────────────────────
    log("info", f"  Power-cycling bench via HMP40x0 on {power_supply_comport} …")
    try:
        with PowerCycleSession(
            comport=power_supply_comport,
            channels=power_supply_channels,
            voltage=power_supply_voltage,
            off_time=off_time,
            reconnect_wait=reconnect_wait,
        ) as ps:
            ps.cycle(hw, ip_address)
        log("info", "  Power cycle complete, HAL reconnected")
    except PowerSupplyNotAvailable as exc:
        log("error", f"  Power supply not available: {exc}")
        for r in write_results:
            r["power_cycle"] = "SKIPPED — power supply not available"
        return write_results
    except Exception as exc:
        log("error", f"  Power cycle failed: {exc}")
        for r in write_results:
            r["power_cycle"] = f"FAILED: {exc}"
        return write_results

    # ── Phase 3: verify ───────────────────────────────────────────────────────
    verify_results = verify(
        hw=hw,
        log=log,
        bench_config=bench_config,
        module_address=module_address,
        write_results=write_results,
    )

    # Clean up internal specs before returning
    for r in write_results:
        r.pop("_param_specs", None)

    return write_results + verify_results
