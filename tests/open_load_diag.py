"""Open-Load Diagnostic test.

Ported from the smoketest ``diagtest.py``, adapted to use the
:class:`~hal.HardwareInterface` / ``festo-cpx-io`` abstraction.

Test flow
---------
For each compatible valve/output module on the bus:

1. **Activate all outputs** by writing ``OutputForceMask`` (20081) and
   ``OutputForceValue`` (20082) so every output drives its load.
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
OutputForceMask                20081   Bit-mask: 1 = force-enable
OutputForceValue               20082   Bit-mask: output value when forced
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

import fnmatch
import time
from typing import Any

from hal import HardwareInterface
from ._base import LogFn, load_compatibility, noop_log

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
        "digital_output",
        "valve_output",
    ],
    "supported_categories": [
        "valve",
        "output",
        "inout",
    ],
    "safety_class": "caution",
    "allowed_in_ci": False,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "force_mask_param_id": 20081,
        "force_value_param_id": 20082,
        "valve_defect_diag_enable_param_id": 20021,
        "openload_diag_enable_param_id": 20027,
        "diag_settle_time": 1.5,
        "diag_clear_time": 1.0,
    },
    "compatible_modules": [
        "VABX-A-S-BV-*",
        "VABX-A-P-EL-*",
        "VAEM-L1-S-*",
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
]

# Fallback for any other VABX pattern not listed above
_VABX_FALLBACK = ("VABX-*", 1, 20021, DIAG_VALVE_DEFECT)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_module_config(
    module_name: str,
) -> tuple[int, int, int] | None:
    """Return ``(num_output_bytes, diag_enable_param_id, expected_diag_id)``
    for *module_name*, or ``None`` if not in the table."""
    for pattern, num_bytes, enable_param, diag_id in _MODULE_CONFIGS:
        if fnmatch.fnmatch(module_name.upper(), pattern.upper()):
            return num_bytes, enable_param, diag_id
    # Fallback for any VABX
    if fnmatch.fnmatch(module_name.upper(), "VABX-*"):
        return _VABX_FALLBACK[1], _VABX_FALLBACK[2], _VABX_FALLBACK[3]
    return None


def _all_ones_mask(num_bytes: int) -> int:
    """Return an integer with all ``num_bytes * 8`` bits set."""
    return (1 << (num_bytes * 8)) - 1


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


def run(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    module_address: int | None = None,
    force_mask_param_id: int = 20081,
    force_value_param_id: int = 20082,
    valve_defect_diag_enable_param_id: int = 20021,
    openload_diag_enable_param_id: int = 20027,
    diag_settle_time: float = 1.5,
    diag_clear_time: float = 1.0,
) -> list[dict]:
    """Run the open-load diagnostic test on all compatible output/valve modules.

    Steps per module:

    1. Look up the module in the configuration table.
    2. Write ``OutputForceMask`` and ``OutputForceValue`` to activate all
       outputs simultaneously.
    3. Write the enable parameter to arm the open-load detection circuit.
    4. Wait ``diag_settle_time`` seconds.
    5. Read the module's current diagnosis via
       :meth:`~hal.HardwareInterface.read_diagnosis`.
    6. Verify the diagnosis ID matches the expected value.
    7. Deactivate outputs (write zeros) and verify the diagnosis clears
       within ``diag_clear_time`` seconds.

    Args:
        hw:                              Connected HAL instance.
        log:                             Optional logging callback.
        module_address:                  Restrict to one address; ``None``
                                         tests all compatible modules.
        force_mask_param_id:             OutputForceMask parameter ID (20081).
        force_value_param_id:            OutputForceValue parameter ID (20082).
        valve_defect_diag_enable_param_id: ValveDefectDiagEnable param ID (20021).
        openload_diag_enable_param_id:   OpenloadDiagEnable param ID (20027).
        diag_settle_time:                Seconds to wait after enabling diag.
        diag_clear_time:                 Seconds to wait after deactivating
                                         outputs for the diag to clear.

    Returns:
        List of result dicts, one per tested module.
    """
    topology = hw.read_topology()
    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    compat = load_compatibility()
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
                "module": name, "address": addr,
                "passed": None,
                "note": f"{name} not in open-load-diag compatibility table — skipped",
                "duration_ms": round((time.time() - ch_start) * 1000, 1),
            })
            continue

        num_bytes, enable_param, expected_diag_id = cfg
        full_mask = _all_ones_mask(num_bytes)
        log("info", f"  Testing #{addr} {name} (mask=0x{full_mask:X}, enable_param={enable_param}) …")

        result: dict[str, Any] = {
            "test": "open-load-diag",
            "module": name, "address": addr,
            "expected_diag_id": hex(expected_diag_id),
            "steps": [],
        }

        # ── Step 1: activate outputs ──────────────────────────────────────
        step_ts = time.time()
        try:
            hw.write_parameter(addr, force_mask_param_id, full_mask)
            hw.write_parameter(addr, force_value_param_id, full_mask)
            result["steps"].append({
                "step": 1, "label": "Activate all outputs",
                "passed": True,
                "detail": f"OutputForceMask=OutputForceValue=0x{full_mask:X}",
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            log("info", f"    [1] Outputs activated (mask=0x{full_mask:X})")
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"Failed to activate outputs: {exc}"
            result["steps"].append({
                "step": 1, "label": "Activate all outputs",
                "passed": False, "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            results.append(result)
            continue

        # ── Step 2: enable open-load diagnostic ───────────────────────────
        step_ts = time.time()
        try:
            hw.write_parameter(addr, enable_param, 1)
            result["steps"].append({
                "step": 2, "label": f"Enable diag (param {enable_param}=1)",
                "passed": True,
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            log("info", f"    [2] Diagnostic enabled (param {enable_param}=1)")
        except Exception as exc:
            result["passed"] = False
            result["error"] = f"Failed to enable diagnostic: {exc}"
            result["steps"].append({
                "step": 2, "label": f"Enable diag (param {enable_param}=1)",
                "passed": False, "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            # Clean up outputs
            _safe_deactivate(hw, addr, force_mask_param_id, force_value_param_id, log)
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            results.append(result)
            continue

        # ── Step 3: settle ────────────────────────────────────────────────
        log("info", f"    [3] Waiting {diag_settle_time} s for diagnostic to settle …")
        time.sleep(diag_settle_time)
        result["steps"].append({
            "step": 3, "label": f"Wait {diag_settle_time} s",
            "passed": True,
        })

        # ── Step 4: read and verify diagnosis ─────────────────────────────
        step_ts = time.time()
        try:
            diag = hw.read_diagnosis(addr)
            diag_id = _diagnosis_id_from_result(diag)
            diag_name = getattr(diag, "name", "unknown") if diag else "none"
            log("info", f"    [4] Diagnosis: id={diag_id:#010x if diag_id else None}, name={diag_name!r}")

            if diag_id == expected_diag_id:
                diag_ok = True
                detail = f"Diagnosis 0x{expected_diag_id:08X} ({diag_name}) active ✓"
                log("info", f"    [4] ✓ Expected diagnosis confirmed on #{addr}")
            elif diag_id is None:
                diag_ok = False
                detail = f"No diagnosis active (expected 0x{expected_diag_id:08X})"
                log("warning", f"    [4] ✗ No diagnosis on #{addr} — expected 0x{expected_diag_id:08X}")
            else:
                diag_ok = False
                detail = (
                    f"Wrong diagnosis: got 0x{diag_id:08X} ({diag_name}), "
                    f"expected 0x{expected_diag_id:08X}"
                )
                log("warning", f"    [4] ✗ Wrong diagnosis on #{addr}: {detail}")

            result["diag_id"] = hex(diag_id) if diag_id else None
            result["diag_name"] = diag_name
            result["steps"].append({
                "step": 4, "label": "Read & verify diagnosis",
                "passed": diag_ok, "detail": detail,
                "diag_id": hex(diag_id) if diag_id else None,
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
        except Exception as exc:
            diag_ok = False
            result["steps"].append({
                "step": 4, "label": "Read & verify diagnosis",
                "passed": False, "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            log("error", f"    [4] ✗ Failed to read diagnosis on #{addr}: {exc}")

        # ── Step 5: deactivate outputs and check clear ────────────────────
        _safe_deactivate(hw, addr, force_mask_param_id, force_value_param_id, log)
        time.sleep(diag_clear_time)

        step_ts = time.time()
        try:
            diag_after = hw.read_diagnosis(addr)
            diag_id_after = _diagnosis_id_from_result(diag_after)
            cleared = diag_id_after != expected_diag_id
            result["diag_id_after_deactivate"] = hex(diag_id_after) if diag_id_after else None
            result["steps"].append({
                "step": 5, "label": "Deactivate outputs & verify diag clears",
                "passed": cleared,
                "detail": (
                    "Diagnosis cleared ✓"
                    if cleared
                    else f"Diagnosis 0x{expected_diag_id:08X} still active after deactivation"
                ),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })
            if cleared:
                log("info", f"    [5] ✓ Diagnosis cleared after output deactivation on #{addr}")
            else:
                log("warning", f"    [5] ✗ Diagnosis NOT cleared on #{addr}")
        except Exception as exc:
            cleared = False
            result["steps"].append({
                "step": 5, "label": "Deactivate outputs & verify diag clears",
                "passed": False, "error": str(exc),
                "duration_ms": round((time.time() - step_ts) * 1000, 1),
            })

        result["passed"] = diag_ok and cleared
        if result["passed"]:
            log("info", f"  ✓ #{addr} {name}: PASS")
        else:
            log("error", f"  ✗ #{addr} {name}: FAIL")

        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results


def _safe_deactivate(
    hw: HardwareInterface,
    addr: int,
    force_mask_param_id: int,
    force_value_param_id: int,
    log: LogFn,
) -> None:
    """Write zeros to OutputForceMask and OutputForceValue.  Best-effort."""
    try:
        hw.write_parameter(addr, force_mask_param_id, 0)
        hw.write_parameter(addr, force_value_param_id, 0)
        log("info", f"    Outputs deactivated on #{addr}")
    except Exception as exc:
        log("warning", f"    Failed to deactivate outputs on #{addr}: {exc}")
