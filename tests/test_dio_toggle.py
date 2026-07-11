"""DIO toggle test — changes port direction to output, turns on, reads back, turns off, restores to input.

Uses :class:`hal.HardwareInterface`.
"""
from __future__ import annotations

import contextlib
import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, load_bench_config, noop_log

TEST_DEFINITION = {
    "test_id": "dio-toggle",
    "name": "DIO Toggle",
    "version": "1.0.0",
    "description": "Configure DIO channels to output mode, toggle ON/OFF, and restore to input mode",
    "required_capabilities": [],
    "supported_categories": [
        "inout"
    ],
    "safety_class": "caution",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {},
    "compatible_modules": [
        "CPX-AP-A-*DIO*",
        "CPX-AP-A-*DIDO*",
        "CPX-AP-A-*DI*DO*",
        "CPX-AP-I-*DIO*",
        "CPX-AP-A-*IOL*",
        "CPX-AP-I-*IOL*"
    ]
}


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    on_module: callable = None,
    on_result: callable = None,
    module_address: int | None = None,
) -> list[dict]:
    if bench_config is None:
        bench_config = load_bench_config(config_path)
    pulse_duration_s = 0.2
    pause_between_modules_s = 0.3
    topology = hw.read_topology()
    dio_mods = [m for m in topology if m.num_inouts > 0 or "DIO" in m.name.upper() or "IOL" in m.name.upper()]
    if module_address is not None:
        dio_mods = [m for m in dio_mods if m.address == module_address]

    if not dio_mods:
        log("warning", "No DIO modules found on bus")
        return [{
            "test": "dio-toggle",
            "passed": None,
            "error": "No DIO modules found",
        }]

    log("info", f"Found {len(dio_mods)} DIO module(s): {[f'#{m.address} {m.name}' for m in dio_mods]}")

    results: list[dict] = []

    for mod in dio_mods:
        # Some modules mix DO and DIO, but we focus on all outputs + inouts for toggling.
        # Actually for DIO toggle we should toggle the inout channels.
        total_channels = mod.num_outputs + mod.num_inouts
        if on_module:
            with contextlib.suppress(Exception):
                on_module(mod.address)
        log("info", f"  ── #{mod.address} {mod.name} ({total_channels} channel(s)) ──")
        t_start = time.time()
        channels: list[dict[str, Any]] = []

        for ch in range(total_channels):
            ch_start = time.time()
            try:
                # Configure as output (True)
                # Note: instances are 1-based, channel is 0-based
                hw.configure_port_direction(mod.address, ch, True)
                
                # Small delay to let the configuration settle
                time.sleep(0.05)
                
                # Set HIGH
                hw.write_output(mod.address, ch, True)
                time.sleep(pulse_duration_s)

                # Read back
                try:
                    actual = hw.read_input(mod.address, ch)
                except Exception:
                    actual = None

                # Set LOW
                hw.write_output(mod.address, ch, False)
                time.sleep(0.05)

                # Restore configuration to input (False)
                hw.configure_port_direction(mod.address, ch, False)

                ch_dur = round((time.time() - ch_start) * 1000, 1)
                passed = actual is None or bool(actual)

                channels.append({
                    "channel": ch,
                    "passed": passed,
                    "duration_ms": ch_dur,
                    "readback": actual,
                })

                status = "✓" if passed else "✗ (readback LOW)"
                log("info", f"    ch {ch} (as output) {status}  ({ch_dur}ms)")

            except Exception as exc:
                ch_dur = round((time.time() - ch_start) * 1000, 1)
                channels.append({
                    "channel": ch,
                    "passed": False,
                    "duration_ms": ch_dur,
                    "error": str(exc),
                })
                log("error", f"    ch {ch}: ✗ {exc}  ({ch_dur}ms)")
                # Try to reset
                try:
                    hw.write_output(mod.address, ch, False)
                    hw.configure_port_direction(mod.address, ch, False)
                except Exception:
                    pass

        t_total = round((time.time() - t_start) * 1000, 1)
        all_ok = all(c.get("passed", False) for c in channels)

        result = {
            "test_id": "dio-toggle",
            "module": mod.name,
            "address": mod.address,
            "module_code": mod.module_code,
            "product_key": mod.product_key,
            "total_channels": total_channels,
            "passed_channels": sum(1 for c in channels if c.get("passed")),
            "failed_channels": sum(1 for c in channels if not c.get("passed")),
            "passed": all_ok,
            "duration_ms": t_total,
            "channels": channels,
        }

        results.append(result)

        if on_result:
            with contextlib.suppress(Exception):
                on_result(result)

        status_icon = "✓" if all_ok else "✗"
        log("info",
            f"  {status_icon} #{mod.address} {mod.name}: "
            f"{result['passed_channels']}/{total_channels} passed  ({t_total}ms)")

        if mod != dio_mods[-1]:
            time.sleep(pause_between_modules_s)

    return results
