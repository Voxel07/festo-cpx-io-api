"""Open-Load Diagnostic test.

Ported from the smoketest ``diagtest.py``, adapted to use the
:class:`~hal.HardwareInterface` / ``festo-cpx-io`` abstraction.

Test flow
---------
For each compatible valve/output module on the bus:

1. **Activate all outputs** by writing ``True`` to all channels via ``write_channels``.
2. **Enable the open-load diagnostic** by writing the appropriate
   enable parameter (``ValveDefectDiagEnable`` = 20021 for VABX/VAEM, or
   ``OpenloadDiagEnable`` = 20027 for VABA variants).
3. **Wait** for the diagnostic to settle (``diag_settle_time`` seconds).
4. **Read diagnosis** via :meth:`~hal.HardwareInterface.read_diagnosis`.
5. **Verify** that the returned diagnosis matches the expected ID.
6. **Deactivate outputs** and verify the diagnosis clears.

Module compatibility
--------------------
The following module name patterns are tested by default (configurable):

* ``VABX-A-S-BV-*``  — uses ValveDefectDiagEnable (20021)
* ``VAEM-L1-S-*``    — uses ValveDefectDiagEnable (20021)
* ``VABA-S6-*``      — uses OpenloadDiagEnable (20027)

.. note::
   The ``festo-cpx-io`` library's :meth:`read_diagnosis_information` returns
   a module-level ``ModuleDiagnosis`` with the active diagnosis ID, name and
   description.  Per-channel diagnosis granularity is **not** available
   through this API — only whether *any* channel on the module has the
   expected diagnosis active is verified.  For per-channel verification use
   the proprietary ``engt`` library (see ``diagtest.py`` in the smoketest).

Parameter IDs
-------------
All IDs are configurable; the defaults are the Festo AP standard values:

============================  ======  =================================
Parameter                      ID      Notes
============================  ======  =================================
ValveDefectDiagEnable          20021   VABX / VAEM modules
OpenloadDiagEnable             20027   VABA modules
============================  ======  =================================

Diagnosis IDs (hex)
-------------------
============================  ============  ===================
Diagnosis                      Hex ID        Modules
============================  ============  ===================
AP_DIAG_VALVE_DEFECT           0x07060268    VABX, VAEM
AP_DIAG_SWITCH_OPEN_CHANNEL    0x07060125    VABA
============================  ============  ===================
"""
from __future__ import annotations

import contextlib
import fnmatch
import time
from typing import Any

import valve_channels
from config_models import BenchConfig
from hal import HardwareInterface

from ._base import LogFn, load_bench_config, load_compatibility, noop_log

# ── Test metadata ──────────────────────────────────────────────────────────────

TEST_DEFINITION = {
    "test_id": "open-load-diag",
    "name": "Open-Load Diagnostic",
    "version": "1.0.0",
    "description": (
        "Activate outputs, enable open-load diagnostic, verify diagnosis raised; "
        "deactivate and verify diagnosis clears"
    ),
    "required_capabilities": [
        "valve_output",
    ],
    "supported_categories": [
        "valve",
    ],
    "safety_class": "caution",
    "allowed_in_ci": False,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "valve_defect_diag_enable_param_id": 20021,
        "openload_diag_enable_param_id": 20027,
        "diag_settle_time": 1.0,
        "diag_clear_time": 1.0,
    },
    "compatible_modules": [
        "VABX-A-S-BV-*",
        "VABX-A-P-EL-*",
        "VAEM-L1-S-*",
        "VMPAL-*",
        "VABA-S6-*",
    ],
}

# ── Diagnosis ID constants ─────────────────────────────────────────────────────

DIAG_VALVE_DEFECT = 0x07060268        # VABX / VAEM
DIAG_SWITCH_OPEN_CHANNEL = 0x07060125  # VABA

# ── Module configuration table ─────────────────────────────────────────────────

# Each entry: (name_pattern, num_output_bytes, diag_enable_param_id, expected_diag_id)
_MODULE_CONFIGS: list[tuple[str, int, int, int]] = [
    # VABX V4x  — 8 outputs, 1-byte mask, ValveDefect
    ("VABX-A-S-BV-V4*",  1, 20021, DIAG_VALVE_DEFECT),
    # VABX P/EL families
    ("VABX-A-P-EL-E12-*", 2, 20021, DIAG_VALVE_DEFECT),
    ("VABX-A-P-EL-E34-*", 4, 20021, DIAG_VALVE_DEFECT),
    # VAEM 12-station — 24 outputs, 3-byte mask
    ("VAEM-L1-S-12-*",   3, 20021, DIAG_VALVE_DEFECT),
    # VAEM 24-station — 48 outputs, 6-byte mask
    ("VAEM-L1-S-24-*",   6, 20021, DIAG_VALVE_DEFECT),
    # VABA — 32 outputs, 4-byte mask, SwitchOpenChannel
    ("VABA-S6-*",        4, 20027, DIAG_SWITCH_OPEN_CHANNEL),
    # VMPAL — 32 outputs (up to 16 valves), 4-byte mask
    ("VMPAL-*",          4, 20021, DIAG_VALVE_DEFECT),
]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_module_config(
    module_name: str,
) -> tuple[int, int, int] | None:
    """Return ``(num_output_bytes, diag_enable_param_id, expected_diag_id)``
    for *module_name*, or ``None`` if not in the table."""
    for pattern, num_bytes, enable_param, diag_id in _MODULE_CONFIGS:
        if fnmatch.fnmatch(module_name.upper(), pattern.upper()):
            return num_bytes, enable_param, diag_id
    return None


def _diagnosis_id_from_result(diag_result: Any) -> int | None:
    """Extract the numeric diagnosis ID from a ``ModuleDiagnosis`` object.

    The ``festo-cpx-io`` library stores the ID as a hex string like
    ``"0x07060268"``.  Returns ``None`` if unavailable.
    """
    if diag_result is None:
        return None
    raw = getattr(diag_result, "diagnosis_id", None)
    if raw is None:
        return None
    try:
        return int(raw, 16) if isinstance(raw, str) else int(raw)
    except (ValueError, TypeError):
        return None


# ── Public API ─────────────────────────────────────────────────────────────────
def _format_diag_id(diag_id: int | None) -> str:
    """Format a diagnosis ID as 8-digit hex, or None."""
    return f"0x{diag_id:08X}" if diag_id is not None else "None"


def _expected_diag_name(expected_diag_id: int) -> str:
    """Return a readable expected diagnosis name."""
    if expected_diag_id == DIAG_VALVE_DEFECT:
        return "AP_DIAG_VALVE_DEFECT"
    if expected_diag_id == DIAG_SWITCH_OPEN_CHANNEL:
        return "AP_DIAG_SWITCH_OPEN_CHANNEL"
    return "unknown"


def _mounted_valves_from_bench_config(
    bench_config: Any | None,
    addr: int,
) -> tuple[list[int] | None, int | None]:
    """Return (mounted_valves, valve_slots) for a module address, if available."""

    if bench_config is None:
        print("Warning: bench_config is None; cannot determine mounted_valves")
        return None, None

    module_instances = getattr(bench_config, "module_instances", None)

    if module_instances is None:
        print("Warning: bench_config missing 'module_instances'; cannot determine mounted_valves")
        return None, None

    inst = next(
        (
            m for m in module_instances
            if getattr(m, "address", None) == addr
        ),
        None,
    )

    if inst is None:
        print(f"Warning: no module instance found for address {addr}")
        return None, None

    return getattr(inst, "mounted_valves", None), getattr(inst, "valve_slots", None)


def _expected_channels_from_mounted_valves(
    mounted_valves: list[int] | None,
    valve_slots: int | None,
    module_name: str,
) -> list[int]:
    """Derive expected open-load diagnostic channels from mounted_valves."""
    expected_channels: list[int] = []

    if mounted_valves is None or valve_slots is None:
        return expected_channels

    for slot in range(valve_slots):
        if slot not in mounted_valves:
            expected_channels.extend(valve_channels.valve_slot_to_channels(slot, module_name=module_name))

    return expected_channels


def _mounted_valves_to_text(mounted_valves: list[int] | None, valve_slots: int | None) -> str:
    """Create a compact readable representation of mounted valves."""
    if mounted_valves is None or valve_slots is None:
        return "unknown, no bench_config mounted_valves available"

    return f"{mounted_valves} (out of {valve_slots} slots)"


def _diag_locations_text(
    channels: list[int] | None,
    diag_id: int | None = None,
    diag_name: str | None = None,
) -> str:
    """Format diagnostic location information."""
    if not channels:
        return "none"

    diag_id_text = _format_diag_id(diag_id) if diag_id is not None else "unknown-id"
    diag_name_text = diag_name or "unknown-name"
    return f"channels={channels}, diag={diag_id_text} ({diag_name_text})"

def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    config_path: str = "data/bench_config.json",
    module_address: int | None = None,
) -> list[dict]:
    """Run the open-load diagnostic test on all compatible output/valve modules."""
    if bench_config is None:
        bench_config = load_bench_config(config_path)
    valve_defect_diag_enable_param_id = TEST_DEFINITION["parameters"]["valve_defect_diag_enable_param_id"]
    openload_diag_enable_param_id = TEST_DEFINITION["parameters"]["openload_diag_enable_param_id"]
    diag_settle_time = TEST_DEFINITION["parameters"]["diag_settle_time"]
    diag_clear_time = TEST_DEFINITION["parameters"]["diag_clear_time"]
    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    load_compatibility()
    results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        name = mod_info.name
        ch_start = time.time()

        cfg = _get_module_config(name)
        if cfg is None:
            log("info", f"  ⊘ #{addr} {name}: not in open-load-diag module table — skipped")
            results.append({
                "test": "open-load-diag",
                "module": name,
                "address": addr,
                "passed": None,
                "note": f"{name} not in open-load-diag compatibility table — skipped",
                "duration_ms": round((time.time() - ch_start) * 1000, 1),
            })
            continue

        num_bytes, enable_param, expected_diag_id = cfg

        if expected_diag_id == DIAG_VALVE_DEFECT:
            enable_param = valve_defect_diag_enable_param_id
        elif expected_diag_id == DIAG_SWITCH_OPEN_CHANNEL:
            enable_param = openload_diag_enable_param_id

        num_channels = num_bytes * 8
        expected_diag_name = _expected_diag_name(expected_diag_id)

        # ── Determine mounted valves and expected diagnostic channels ──────
        mounted_valves, valve_slots = _mounted_valves_from_bench_config(bench_config, addr)
        expected_channels = _expected_channels_from_mounted_valves(mounted_valves, valve_slots, name)

        expected_diag_locations = [
            {
                "channel": ch,
                "diag_id": _format_diag_id(expected_diag_id),
                "diag_name": expected_diag_name,
            }
            for ch in expected_channels
        ]

        # ── Pre-test info log ──────────────────────────────────────────────
        log("info", f"  ── Open-load diagnostic test plan for #{addr} {name} ──")
        log("info", f"Module {addr}: Valves mounted: {_mounted_valves_to_text(mounted_valves, valve_slots)}")
        log("info", f"     Output channels: {num_channels}")
        log("info", f"     Enable parameter: {enable_param}")

        # Structured log markers for UI presentation
        log("info", f"OPEN_LOAD_INIT|{addr}|{name}|{num_channels}")
        for ch in expected_channels:
            log("info", f"OPEN_LOAD_EXPECTED|{addr}|{ch}")

        result: dict[str, Any] = {
            "test": "open-load-diag",
            "module": name,
            "address": addr,
            "mounted_valves": mounted_valves,
            "expected_diag_id": _format_diag_id(expected_diag_id),
            "expected_diag_name": expected_diag_name,
            "expected_channels": expected_channels,
            "expected_diag_locations": expected_diag_locations,
            "actual_diag_locations": [],
            "num_channels": num_channels,
            "steps": [],
        }

        log("info", f"  Testing #{addr} {name} (channels={num_channels}, enable_param={enable_param}) …")

        # ── Step 0: Ensure diagnosis is clear ──────────────────────────────
        step0_ts = time.time()
        try:
            hw.write_parameter(addr, enable_param, 0)
            time.sleep(0.5)
            diag_start = hw.read_diagnosis(addr)
            diag_id_start = _diagnosis_id_from_result(diag_start)

            if diag_id_start is not None:
                log("error", f"    [0] ✗ Diagnosis 0x{diag_id_start:08X} active at start of test!")
                result["passed"] = False
                result["error"] = "Diagnosis active at start"
                result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
                results.append(result)
                continue

            log("info", "    [0] ✓ Diagnosis clear at start")
            result["steps"].append({
                "step": 0,
                "label": "Check diag clear at start",
                "passed": True,
                "duration_ms": round((time.time() - step0_ts) * 1000, 1),
            })
        except Exception as exc:
            log("error", f"    [0] ✗ Failed to clear/check diagnosis at start: {exc}")
            result["passed"] = False
            result["error"] = f"Failed at start: {exc}"
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            results.append(result)
            continue

        # ── Step 1: enable open-load diagnostic ───────────────────────────
        step_ts = time.time()
        try:
            hw.write_parameter(addr, enable_param, 1)
            result["steps"].append({
                "step": 1,
                "label": f"Enable diag (param {enable_param}=1)",
                "passed": True,
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            log("info", f"    [1] Diagnostic enabled (param {enable_param}=1)")
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"Failed to enable diagnostic: {exc}"
            result["steps"].append({
                "step": 1,
                "label": f"Enable diag (param {enable_param}=1)",
                "passed": False,
                "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            _safe_deactivate(hw, addr, num_channels, log)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            results.append(result)
            continue

        # ── Step 2: activate outputs ──────────────────────────────────────
        step_ts = time.time()
        try:
            hw.write_channels(addr, [True] * num_channels)
            result["steps"].append({
                "step": 2,
                "label": "Activate all outputs",
                "passed": True,
                "detail": f"Channels activated via write_channels({num_channels})",
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            log("info", f"    [2] Outputs activated ({num_channels} channels)")
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"Failed to activate outputs: {exc}"
            result["steps"].append({
                "step": 2,
                "label": "Activate all outputs",
                "passed": False,
                "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            _safe_deactivate(hw, addr, num_channels, log)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            results.append(result)
            continue

        # ── Step 3: settle ────────────────────────────────────────────────
        log("info", f"    [3] Waiting {diag_settle_time} s for diagnostic to settle …")
        time.sleep(diag_settle_time)
        result["steps"].append({
            "step": 3,
            "label": f"Wait {diag_settle_time} s",
            "passed": True,
        })

        # ── Step 4: read and verify diagnosis ─────────────────────────────
        step_ts = time.time()
        try:
            diag = hw.read_diagnosis(addr)
            diag_id = _diagnosis_id_from_result(diag)
            diag_name = getattr(diag, "name", "unknown") if diag else "none"
            diag_id_str = _format_diag_id(diag_id)

            log("info", f"    [4] Diagnosis: id={diag_id_str}, name={diag_name!r}")

            expected_base = expected_diag_id & 0xFFFF00FF

            if diag_id is not None:
                actual_base = diag_id & 0xFFFF00FF
                actual_channel = (diag_id >> 8) & 0xFF
                actual_diag_locations = [{
                    "channel": actual_channel,
                    "diag_id": _format_diag_id(diag_id),
                    "diag_name": diag_name,
                }]
            else:
                actual_base = None
                actual_channel = None
                actual_diag_locations = []

            result["actual_channel"] = actual_channel
            result["actual_diag_locations"] = actual_diag_locations

            if not expected_channels:
                if diag_id is None:
                    diag_ok = True
                    detail = "No diagnosis active; none expected ✓"
                    log("info", f"    [4] ✓ {detail}")
                else:
                    diag_ok = False
                    detail = f"Unexpected diagnosis {_format_diag_id(diag_id)} on ch {actual_channel}"
                    log("warning", f"    [4] ✗ {detail}")
                    result["error"] = detail
            else:
                if diag_id is None:
                    diag_ok = False
                    detail = f"No diagnosis active; expected on channels {expected_channels}"
                    log("warning", f"    [4] ✗ {detail}")
                    result["error"] = detail
                elif actual_base == expected_base and actual_channel in expected_channels:
                    diag_ok = True
                    detail = (
                        f"Expected diagnosis {_format_diag_id(diag_id)} "
                        f"({diag_name}) on ch {actual_channel} ✓"
                    )
                    log("info", f"    [4] ✓ {detail}")
                else:
                    diag_ok = False
                    detail = (
                        f"Wrong diagnosis {_format_diag_id(diag_id)} on ch {actual_channel}; "
                        f"expected {_format_diag_id(expected_diag_id)} "
                        f"({expected_diag_name}) on channels {expected_channels}"
                    )
                    log("warning", f"    [4] ✗ {detail}")
                    result["error"] = detail

            log("info", f"OPEN_LOAD_ACTUAL|{addr}|{actual_channel if actual_channel is not None else 'none'}")
            
            # Print list for live log
            log("info", "    [4] Expected vs actual:")
            if expected_channels:
                log("info", "        Expected diag")
                for ch in expected_channels:
                    ch_status = "got diag" if ch == actual_channel else "did not get"
                    log("info", f"        - Channel {ch} -> {ch_status}")
            
            unexpected_ch = [ch for ch in range(num_channels) if ch not in expected_channels]
            if unexpected_ch:
                log("info", "        No diag expected")
                for ch in unexpected_ch:
                    ch_status = "failed got diag" if ch == actual_channel else "ok"
                    log("info", f"        - Channel {ch} -> {ch_status}")

            result["diag_id"] = _format_diag_id(diag_id) if diag_id is not None else None
            result["diag_name"] = diag_name
            result["steps"].append({
                "step": 4,
                "label": "Read & verify diagnosis",
                "passed": diag_ok,
                "detail": detail,
                "expected_diag_locations": expected_diag_locations,
                "actual_diag_locations": actual_diag_locations,
                "diag_id": _format_diag_id(diag_id) if diag_id is not None else None,
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
        except Exception as exc:
            diag_ok = False
            result["actual_diag_locations"] = []
            result["steps"].append({
                "step": 4,
                "label": "Read & verify diagnosis",
                "passed": False,
                "error": str(exc),
                "expected_diag_locations": expected_diag_locations,
                "actual_diag_locations": [],
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            log("error", f"    [4] ✗ Failed to read diagnosis on #{addr}: {exc}")

        # ── Step 5: deactivate outputs and check clear ────────────────────
        _safe_deactivate(hw, addr, num_channels, log)
        with contextlib.suppress(Exception):
            hw.write_parameter(addr, enable_param, 0)

        time.sleep(diag_clear_time)

        step_ts = time.time()
        try:
            diag_after = hw.read_diagnosis(addr)
            diag_id_after = _diagnosis_id_from_result(diag_after)
            expected_base = expected_diag_id & 0xFFFF00FF
            cleared = diag_id_after is None or (diag_id_after & 0xFFFF00FF) != expected_base

            result["diag_id_after_deactivate"] = (
                _format_diag_id(diag_id_after)
                if diag_id_after is not None
                else None
            )

            result["steps"].append({
                "step": 5,
                "label": "Deactivate outputs & verify diag clears",
                "passed": cleared,
                "detail": (
                    "Diagnosis cleared ✓"
                    if cleared
                    else f"Diagnosis {_format_diag_id(diag_id_after)} still active after deactivation"
                ),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })

            if cleared:
                log("info", f"    [5] ✓ Diagnosis cleared after output deactivation on #{addr}")
            else:
                log("warning", f"    [5] ✗ Diagnosis NOT cleared on #{addr}")
                if "error" not in result:
                    result["error"] = f"Diagnosis NOT cleared on #{addr}"
        except Exception as exc:
            cleared = False
            result["steps"].append({
                "step": 5,
                "label": "Deactivate outputs & verify diag clears",
                "passed": False,
                "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })

        result["passed"] = diag_ok and cleared

        if result["passed"]:
            log("info", f"  ✓ #{addr} {name}: PASS")
        else:
            log("error", f"  ✗ #{addr} {name}: FAIL")

        [
            loc["channel"]
            for loc in result.get("actual_diag_locations", [])
        ]

        if result.get("diag_id"):
            int(result["diag_id"], 16)

        log("info", f"  Result #{addr} {name}: {'PASS' if result['passed'] else 'FAIL'}")

        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    # ── Final summary ─────────────────────────────────────────────────────
    log("info", "── Open-load diagnostic summary ──")

    for result in results:
        if result.get("passed") is None:
            log(
                "info",
                f"  #{result.get('address')} {result.get('module')}: SKIPPED — "
                f"{result.get('note', '')}"
            )
            continue

        expected_channels = result.get("expected_channels", [])
        actual_channel = result.get("actual_channel")
        num_channels = result.get("num_channels", 0)

        status = "PASS" if result.get("passed") else "FAIL"

        log("info", f"  #{result.get('address')} {result.get('module')}: {status}")
        
        if expected_channels:
            log("info", "    Expected diag")
            for ch in expected_channels:
                ch_status = "got diag" if ch == actual_channel else "did not get"
                log("info", f"    - Channel {ch} -> {ch_status}")
        
        unexpected_ch = [ch for ch in range(num_channels) if ch not in expected_channels]
        if unexpected_ch:
            log("info", "    No diag expected")
            for ch in unexpected_ch:
                ch_status = "failed got diag" if ch == actual_channel else "ok"
                log("info", f"    - Channel {ch} -> {ch_status}")

    return results


def _safe_deactivate(
    hw: HardwareInterface,
    addr: int,
    num_channels: int,
    log: LogFn,
) -> None:
    """Write zeros to all channels. Best-effort."""
    try:
        hw.write_channels(addr, [False] * num_channels)
        log("info", f"    Outputs deactivated on #{addr}")
    except Exception as exc:
        log("warning", f"    Failed to deactivate outputs on #{addr}: {exc}")
