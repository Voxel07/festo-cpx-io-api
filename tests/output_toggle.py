"""Output toggle test — turns on all outputs one by one for all output modules.

Per-channel validation with timing and structured logging.
Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import contextlib
import time
from typing import Any

from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, noop_log

TEST_DEFINITIONS = [
    {
        "test_id": "output-toggle",
        "name": "Output Toggle",
        "version": "1.0.0",
        "description": "Toggle all digital output channels ON/OFF and verify state changes",
        "required_capabilities": [
            "digital_output"
        ],
        "supported_categories": [
            "output",
            "inout"
        ],
        "safety_class": "caution",
        "allowed_in_ci": True,
        "can_run_parallel": False,
        "singleton": False,
        "parameters": {},
        "compatible_modules": [
            "CPX-AP-A-*DO*",
            "CPX-AP-A-*HDO*",
            "CPX-AP-I-*DO*",
        ]
    }
]


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    on_module: callable = None,  # (address: int) -> None — called before each module
    on_result: callable = None,  # (result: dict) -> None — called after each module (live push)
    module_address: int | None = None,
) -> list[dict]:
    """Toggle each output channel on every output-capable module.

    For each module with outputs:
    1. Turn each channel HIGH → wait → read back → turn LOW
    2. Log per-channel progress
    3. Report duration and pass/fail

    :param hw: Pre-connected HardwareInterface
    :param connections_path: Unused — kept for API symmetry
    :param log: Logging callback ``(level, message) -> None``
    :param pulse_duration_s: How long to hold each output HIGH
    :param pause_between_modules_s: Pause between modules
    :returns: List of per-module result dicts with per-channel details
    """
    pulse_duration_s = 0.2
    pause_between_modules_s = 0.3

    topology = hw.read_topology()
    output_mods = [m for m in topology if m.num_outputs > 0 or m.num_inouts > 0]
    if module_address is not None:
        output_mods = [m for m in output_mods if m.address == module_address]

    if not output_mods:
        log("warning", "No output-capable modules found on bus")
        return [{
            "test": "output-toggle",
            "passed": None,
            "error": "No output modules found",
        }]

    log("info", f"Found {len(output_mods)} output-capable module(s): "
        f"{[f'#{m.address} {m.name}' for m in output_mods]}")

    results: list[dict] = []

    for mod in output_mods:
        total_channels = mod.num_outputs
        if on_module:
            with contextlib.suppress(Exception):
                on_module(mod.address)
        log("info", f"  ── #{mod.address} {mod.name} ({total_channels} channel(s)) ──")
        t_start = time.time()
        channels: list[dict[str, Any]] = []

        # For mixed DI/DO modules, read_channel indexes ALL channels
        # (inputs first, then outputs), while write_channel indexes
        # outputs only.  Offset readback by num_inputs so we read the
        # output channel we just wrote, not an input channel.
        read_offset = mod.num_inputs

        for ch in range(total_channels):
            ch_start = time.time()

            try:
                # Set HIGH
                hw.write_output(mod.address, ch, True)
                time.sleep(pulse_duration_s)

                # Read back 
                try:
                    actual = hw.read_input(mod.address, read_offset + ch)
                except Exception:
                    actual = None

                # Set LOW
                hw.write_output(mod.address, ch, False)

                ch_dur = round((time.time() - ch_start) * 1000, 1)
                passed = actual is None or bool(actual)

                channels.append({
                    "channel": ch,
                    "passed": passed,
                    "duration_ms": ch_dur,
                    "readback": actual,
                })

                status = "✓" if passed else "✗ (readback LOW)"
                log("info", f"    ch {ch} {status}  ({ch_dur}ms)")

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
            "test": "output-toggle",
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

        # Live push: notify caller of this module's result immediately
        if on_result:
            with contextlib.suppress(Exception):
                on_result(result)

        status_icon = "✓" if all_ok else "✗"
        log("info",
            f"  {status_icon} #{mod.address} {mod.name}: "
            f"{result['passed_channels']}/{total_channels} passed  ({t_total}ms)")

        if mod != output_mods[-1]:
            time.sleep(pause_between_modules_s)

    return results
