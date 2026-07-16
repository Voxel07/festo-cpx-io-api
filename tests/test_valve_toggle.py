"""Valve toggle test — turns on all outputs one by one for all valve modules.

Per-channel validation with timing and structured logging.
Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import contextlib
import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface
from valve_channels import expand_valve_indices

from ._base import LogFn, load_bench_config, noop_log

TEST_DEFINITION = {
    "test_id": "valve-toggle",
    "name": "Valve Toggle",
    "version": "1.0.0",
    "description": "Toggle all valve channels ON/OFF and verify state changes",
    "required_capabilities": [
        "valve_output"
    ],
    "supported_categories": [
        "valve",
        "interface"
    ],
    "safety_class": "caution",
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
    on_module: callable = None,  # (address: int) -> None — called before each module
    on_result: callable = None,  # (result: dict) -> None — called after each module (live push)
    module_address: int | None = None,
) -> list[dict]:
    if bench_config is None:
        bench_config = load_bench_config(config_path)
    """Toggle each output channel on every valve module.

    For each valve module:
    1. Turn each channel HIGH → wait → turn LOW (valves do not support read-back)
    2. Log per-channel progress
    3. Report duration and pass/fail

    :param hw: Pre-connected HardwareInterface
    :param log: Logging callback ``(level, message) -> None``
    :returns: List of per-module result dicts with per-channel details
    """
    pulse_duration_s = 0.2
    pause_between_modules_s = 0.3

    topology = hw.read_topology()
    if module_address is not None:
        # The resolver already verified the configured capability/category.
        # Do not apply a second, product-name based valve filter here.
        valve_mods = [m for m in topology if m.address == module_address]
    else:
        valve_addresses = {
            module.address
            for module in bench_config.module_instances
            if "valve_output" in bench_config.module_capabilities(module)
        }
        valve_mods = [m for m in topology if m.address in valve_addresses]

    if not valve_mods:
        error = "Resolved valve module was not found in the live topology"
        log("warning", error)
        result = {
            "test_id": "valve-toggle",
            "address": module_address,
            "passed": False,
            "error": error,
            "channels": [],
        }
        if on_result:
            with contextlib.suppress(Exception):
                on_result(result)
        return [result]

    log("info", f"Found {len(valve_mods)} valve module(s): "
        f"{[f'#{m.address} {m.name}' for m in valve_mods]}")

    results: list[dict] = []

    for mod in valve_mods:
        configured = bench_config.module_instance_at(mod.address)
        module_type = bench_config.module_type_at(mod.address)
        cpv = module_type.channels_per_valve
        if cpv < 1:
            # Old generated configs did not persist this value. Interfaces use
            # one channel per valve; conventional valve terminals use two.
            cpv = 1 if configured.category.value == "interface" else 2
        available_channels = mod.num_outputs + mod.num_inouts
        mounted_valves = sorted(set(configured.mounted_valves))
        output_channels = [
            channel
            for channel in expand_valve_indices(mounted_valves, cpv)
            if channel < available_channels
        ]
        total_channels = len(output_channels)
        extra_info = f"  ({len(mounted_valves)} mounted valve(s) × {cpv}c/valve)"
        if on_module:
            with contextlib.suppress(Exception):
                on_module(mod.address)
        log("info", f"  ── #{mod.address} {mod.name} ({total_channels} channel(s){extra_info}) ──")
        t_start = time.time()
        channels: list[dict[str, Any]] = []

        if not output_channels:
            result = {
                "test_id": "valve-toggle",
                "module": mod.name,
                "address": mod.address,
                "module_code": mod.module_code,
                "product_key": mod.product_key,
                "mounted_valves": mounted_valves,
                "total_channels": 0,
                "passed_channels": 0,
                "failed_channels": 0,
                "passed": True,
                "skipped": True,
                "note": "No valves are configured as mounted",
                "duration_ms": 0.0,
                "channels": [],
            }
            results.append(result)
            log("info", f"  - #{mod.address} {mod.name}: no mounted valves; skipped")
            if on_result:
                with contextlib.suppress(Exception):
                    on_result(result)
            continue

        for ch in output_channels:
            ch_start = time.time()

            try:
                # Set HIGH
                hw.write_output(mod.address, ch, True)
                time.sleep(pulse_duration_s)

                # Set LOW
                hw.write_output(mod.address, ch, False)

                ch_dur = round((time.time() - ch_start) * 1000, 1)
                # Valve terminals: pass if write succeeded (no read-back possible)
                passed = True

                channels.append({
                    "channel": ch,
                    "passed": passed,
                    "duration_ms": ch_dur,
                    "readback": None,
                })

                status = "✓"
                valve_note = ""
                if cpv > 0:
                    vi = ch // cpv
                    sub = ch % cpv
                    valve_note = f"  [V{vi + 1} {'A' if sub == 0 else 'B'}]" if cpv > 1 else f"  [V{vi + 1}]"
                log("info", f"    ch {ch}:{valve_note} {status}  ({ch_dur}ms)")

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
                with contextlib.suppress(Exception):
                    hw.write_output(mod.address, ch, False)

        t_total = round((time.time() - t_start) * 1000, 1)
        all_ok = all(c.get("passed", False) for c in channels)

        result = {
            "test_id": "valve-toggle",
            "module": mod.name,
            "address": mod.address,
            "module_code": mod.module_code,
            "product_key": mod.product_key,
            "mounted_valves": mounted_valves,
            "total_channels": total_channels,
            "passed_channels": sum(1 for c in channels if c.get("passed")),
            "failed_channels": sum(1 for c in channels if not c.get("passed")),
            "passed": all_ok,
            "duration_ms": t_total,
            "channels": channels,
        }

        results.append(result)

        # Live push: notify caller of this module's result immediately
        if on_result:
            with contextlib.suppress(Exception):
                on_result(result)

        status_icon = "✓" if all_ok else "✗"
        log("info",
            f"  {status_icon} #{mod.address} {mod.name}: "
            f"{result['passed_channels']}/{total_channels} passed  ({t_total}ms)")

        if mod != valve_mods[-1]:
            time.sleep(pause_between_modules_s)

    return results
