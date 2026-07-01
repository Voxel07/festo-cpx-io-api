"""Factory Reset and Normal Reset parameter persistence test.

Ported from the smoketest ``factory_reset_test.py``, adapted to use the
:class:`~hal.HardwareInterface` / ``festo-cpx-io`` abstraction instead of
the proprietary ``engt`` library.

Test flow
---------
For each module under test:

1. **Write phase** — Write distinctive test values to a configurable set of
   remanent parameters.
2. **Normal-reset phase** — Trigger a warm restart (``reset_options = 0``),
   reconnect, and verify that all written values *persisted*.
3. **Factory-reset phase** — Trigger a factory reset (``reset_options = 1``),
   reconnect, and verify that all written values are *cleared* to their
   factory defaults (0 / empty string).

Device reset mechanism
----------------------
The AP DeviceReset command is sent by writing parameter ID ``device_reset_param_id``
(default **20001**) with:

* ``0x5761`` — warm restart (parameters preserved)
* ``0x4B6C`` — factory reset (parameters cleared)

.. note::
   The exact parameter ID may differ between CPX-AP hardware revisions.
   If writes to ID 20001 have no effect on your device, consult the module
   APDD and update ``device_reset_param_id`` accordingly.

If the device reset parameter is not supported (``NotImplementedError`` or
similar), the test falls back to a **power-cycle** via the HMP40x0 power
supply (normal-reset behaviour only — factory reset cannot be emulated via
power cycle alone).

Parameter IDs used
------------------
All IDs are configurable.  The defaults mirror the Festo AP standard:

==================          ======      ================================================
Name                        ID          Notes
==================          ======      ================================================
ApplicationTag              20118       String, max 32 chars
LocationTag                 20207       String, max length device-specific
I&M 2 installation date     11295004    String/date, format device-specific
CC Setpoint (out)           20094       UINT32, output condition counter setpoint
CC Actual (out)             20095       UINT32, output condition counter actual value
CC Setpoint (in)            20294       UINT32, input condition counter setpoint
CC Actual (in)              20295       UINT32, input condition counter actual value
==================          ======      ================================================
"""
from __future__ import annotations

import time
from typing import Any

from hal import HardwareInterface
from ._base import LogFn, noop_log

# ── Test metadata ──────────────────────────────────────────────────────────────

TEST_DEFINITION = {
    "test_id": "factory-reset",
    "name": "Factory Reset",
    "version": "1.1.0",
    "description": (
        "Write test values, perform normal reset (verify persistence), then "
        "perform factory reset (verify clearance)"
    ),
    "required_capabilities": [
        "remanent_params"
    ],
    "supported_categories": [
        "input",
        "output",
        "inout",
        "valve",
        "bus"
    ],
    "safety_class": "caution",
    "allowed_in_ci": False,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "location_tag_param_id": 20207,
        "im2_installation_date_param_id": 11295004,
        "cc_setpoint_out_param_id": 20094,
        "cc_actual_out_param_id": 20095,
        "cc_setpoint_in_param_id": 20294,
        "cc_actual_in_param_id": 20295,
        "device_reset_param_id": 20001,
        "reset_reconnect_wait": 10.0,
        "power_supply_comport": None,
        "power_supply_channels": [1, 2, 4],
        "power_supply_voltage": 24.0,
        "reconnect_wait": 8.0,
    },
    "compatible_modules": [
        "*"
    ]
}


# ── Device family → reset parameter mapping ────────────────────────────────────

# Keys refer to entries in the runtime parameter-id mapping built in run().
#
# Examples:
# 16DI:
#   cc_setpoint_in_param_id: 20294
#   cc_actual_in_param_id:   20295
#
# 16DIO:
#   cc_setpoint_in_param_id:  20294
#   cc_actual_in_param_id:    20295
#   cc_setpoint_out_param_id: 20094
#   cc_actual_out_param_id:   20095
#
# VABX-V4A:
#   cc_setpoint_out_param_id: 20094
#   cc_actual_out_param_id:   20095
DEVICE_FAMILY_RESET_PARAM_KEYS: dict[str, list[str]] = {
    "16DI-M": [
        "cc_setpoint_in_param_id",
        "cc_actual_in_param_id",
    ],
    "16DIO": [
        "cc_setpoint_in_param_id",
        "cc_actual_in_param_id",
        "cc_setpoint_out_param_id",
        "cc_actual_out_param_id",
    ],
    "VABX-V4": [
        "cc_setpoint_out_param_id",
        "cc_actual_out_param_id",
    ],
}

# ── Test sentinel values ───────────────────────────────────────────────────────

_APP_TAG_VALUE = "CPX-AP-FR-Test"
_LOCATION_TAG_VALUE = "CPX-AP-FR-Test"
_IM2_INSTALLATION_DATE_VALUE = "2026-07-01"
_CC_SETPOINT_VALUE = 1000
_CC_ACTUAL_VALUE = 500
_CC_IN_SETPOINT_VALUE = 2000
_CC_IN_ACTUAL_VALUE = 750

# Seconds to wait after a device reset command before reconnecting
_DEFAULT_RESET_WAIT = 10.0

# AP standard reset command values
_WARM_RESET_CMD = 0x5761
_FACTORY_RESET_CMD = 0x4B6C


# ── Internal helpers ───────────────────────────────────────────────────────────

def _module_family_key(module_name: str) -> str | None:
    """Return the configured device-family key matching ``module_name``.

    Matching is intentionally substring-based because topology names often
    include full order codes / variants around the family token.
    """
    normalized = module_name.upper()

    # Match longer / more specific keys first, e.g. 16DIO before 16DI.
    for family in sorted(DEVICE_FAMILY_RESET_PARAM_KEYS, key=len, reverse=True):
        if family.upper() in normalized:
            return family

    return None


def _get_reset_param_specs_for_module(
    module_name: str,
    location_tag_param_id: int,
    im2_installation_date_param_id: int,
    cc_setpoint_out_param_id: int,
    cc_actual_out_param_id: int,
    cc_setpoint_in_param_id: int,
    cc_actual_in_param_id: int,
    log: LogFn,
) -> list[tuple[str, int, Any, Any]]:
    """Build the parameter list for a module.

    Returns tuples:

    ``(name, param_id, test_value, factory_default)``

    LocationTag and I&M 2 installation date are tested for all modules.
    Condition-counter parameters are selected by device family.
    """
    param_ids: dict[str, int] = {
        "location_tag_param_id": location_tag_param_id,
        "im2_installation_date_param_id": im2_installation_date_param_id,
        "cc_setpoint_out_param_id": cc_setpoint_out_param_id,
        "cc_actual_out_param_id": cc_actual_out_param_id,
        "cc_setpoint_in_param_id": cc_setpoint_in_param_id,
        "cc_actual_in_param_id": cc_actual_in_param_id,
    }

    param_defs: dict[str, tuple[str, Any, Any]] = {
        "location_tag_param_id": ("location_tag", _LOCATION_TAG_VALUE, ""),
        "im2_installation_date_param_id": (
            "im2_installation_date",
            _IM2_INSTALLATION_DATE_VALUE,
            "",
        ),
        "cc_setpoint_out_param_id": ("cc_out_sp", _CC_SETPOINT_VALUE, 0),
        "cc_actual_out_param_id": ("cc_out_act", _CC_ACTUAL_VALUE, 0),
        "cc_setpoint_in_param_id": ("cc_in_sp", _CC_IN_SETPOINT_VALUE, 0),
        "cc_actual_in_param_id": ("cc_in_act", _CC_IN_ACTUAL_VALUE, 0),
    }

    family = _module_family_key(module_name)

    # These two are tested for all modules.
    selected_keys = [
        "location_tag_param_id",
        "im2_installation_date_param_id",
    ]

    if family is None:
        log(
            "warning",
            f"  No CC reset-parameter mapping found for module {module_name!r}; "
            "testing common parameters only",
        )
    else:
        selected_keys.extend(DEVICE_FAMILY_RESET_PARAM_KEYS[family])
        log(
            "info",
            f"  Using CC reset-parameter mapping for device family {family}: "
            f"{DEVICE_FAMILY_RESET_PARAM_KEYS[family]}",
        )

    specs: list[tuple[str, int, Any, Any]] = []

    for key in selected_keys:
        name, test_value, factory_default = param_defs[key]
        specs.append((name, param_ids[key], test_value, factory_default))

    return specs



def _write_test_values(
    hw: HardwareInterface,
    addr: int,
    params_to_write: list[tuple[str, int, Any, Any]],
    log: LogFn,
) -> dict[str, Any]:
    """Write all configured test-sentinel values to one module.

    ``params_to_write`` contains tuples:

    ``(name, param_id, test_value, factory_default)``
    """
    result: dict[str, Any] = {}
    errors: list[str] = []

    for name, pid, value, _factory_default in params_to_write:
        try:
            hw.write_parameter(addr, pid, value)
            result[f"wrote_{name}"] = value
            log("info", f"    Wrote [{pid}] {name} = {value!r}")
        except Exception as exc:
            err_msg = f"write {name} [{pid}] failed: {exc}"
            result[f"wrote_{name}"] = f"FAILED: {exc}"
            errors.append(err_msg)
            log("warning", f"    {err_msg}")

    # Immediate read-back sanity check
    for name, pid, expected, _factory_default in params_to_write:
        if f"wrote_{name}" in result and "FAILED" in str(result.get(f"wrote_{name}", "")):
            continue

        try:
            actual = hw.read_parameter(addr, pid)
            ok = actual == expected
            result[f"readback_{name}"] = actual
            result[f"ok_{name}"] = ok

            if not ok:
                errors.append(
                    f"readback mismatch {name} [{pid}]: got {actual!r}, expected {expected!r}"
                )
        except Exception as exc:
            result[f"readback_{name}"] = f"FAILED: {exc}"
            result[f"ok_{name}"] = False
            errors.append(f"readback {name} [{pid}] failed: {exc}")

    result["write_ok"] = not errors

    if errors:
        result["write_errors"] = errors

    return result


def _verify_persisted(
    hw: HardwareInterface,
    addr: int,
    params_to_check: list[tuple[str, int, Any, Any]],
    log: LogFn,
) -> dict[str, Any]:
    """Verify all configured test-sentinel values are still present."""
    result: dict[str, Any] = {}
    errors: list[str] = []

    for name, pid, expected, _factory_default in params_to_check:
        try:
            actual = hw.read_parameter(addr, pid)
            ok = actual == expected
            result[f"persist_{name}"] = actual
            result[f"persist_ok_{name}"] = ok

            if not ok:
                errors.append(
                    f"{name} [{pid}] not persisted: got {actual!r}, expected {expected!r}"
                )
        except Exception as exc:
            result[f"persist_{name}"] = f"FAILED: {exc}"
            result[f"persist_ok_{name}"] = False
            errors.append(f"read {name} [{pid}] failed after normal reset: {exc}")

    result["persist_ok"] = not errors

    if errors:
        result["persist_errors"] = errors

    return result


def _verify_factory_defaults(
    hw: HardwareInterface,
    addr: int,
    params_to_check: list[tuple[str, int, Any, Any]],
    log: LogFn,
) -> dict[str, Any]:
    """Verify all configured test-sentinel values are cleared to factory defaults."""
    result: dict[str, Any] = {}
    errors: list[str] = []

    for name, pid, _test_value, expected_default in params_to_check:
        try:
            actual = hw.read_parameter(addr, pid)

            ok = actual == expected_default or (
                isinstance(actual, str)
                and isinstance(expected_default, str)
                and actual.strip("\x00") == expected_default
            )

            result[f"factory_{name}"] = actual
            result[f"factory_ok_{name}"] = ok

            if not ok:
                errors.append(
                    f"{name} [{pid}] not at factory default: "
                    f"got {actual!r}, expected {expected_default!r}"
                )
        except Exception as exc:
            result[f"factory_{name}"] = f"FAILED: {exc}"
            result[f"factory_ok_{name}"] = False
            errors.append(f"read {name} [{pid}] failed after factory reset: {exc}")

    result["factory_ok"] = not errors

    if errors:
        result["factory_errors"] = errors

    return result


def _trigger_reset(
    hw: HardwareInterface,
    addr: int,
    ip_address: str,
    factory_reset: bool,
    device_reset_param_id: int,
    reset_wait: float,
    log: LogFn,
) -> bool:
    """Trigger device reset and reconnect. Returns True on success."""
    reset_label = "factory reset" if factory_reset else "warm restart"
    log("info", f"  Triggering {reset_label} on module #{addr} via param {device_reset_param_id} …")

    try:
        hw.reset_device(
            address=addr,
            factory_reset=factory_reset,
            device_reset_param_id=device_reset_param_id,
        )
    except NotImplementedError as exc:
        log("warning", f"  reset_device not implemented: {exc}")
        return False
    except Exception as exc:
        # Connection drops after reset — this is expected
        log("info", f"  Connection dropped after reset (expected): {exc}")

    log("info", f"  Waiting {reset_wait} s for system to restart …")

    try:
        hw.disconnect()
    except Exception:
        pass

    time.sleep(reset_wait)

    try:
        hw.connect(ip_address)
        log("info", "  Reconnected to AP system")
        return True
    except Exception as exc:
        log("error", f"  Failed to reconnect after reset: {exc}")
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def run(
    hw: HardwareInterface,
    ip_address: str,
    log: LogFn = noop_log,
    module_address: int | None = None,
    location_tag_param_id: int = 20207,
    im2_installation_date_param_id: int = 11295004,
    cc_setpoint_out_param_id: int = 20094,
    cc_actual_out_param_id: int = 20095,
    cc_setpoint_in_param_id: int = 20294,
    cc_actual_in_param_id: int = 20295,
    device_reset_param_id: int = 20001,
    reset_reconnect_wait: float = _DEFAULT_RESET_WAIT,
    power_supply_comport: str | None = None,
    power_supply_channels: list[int] | None = None,
    power_supply_voltage: float = 24.0,
    reconnect_wait: float = 8.0,
) -> list[dict]:

    """Full factory-reset + normal-reset parameter persistence test."""
    topology = hw.read_topology()

    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    log("info", f"Factory-reset test on {len(topology)} module(s)")
    all_results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Module #{addr} {mod_info.name} …")

        result: dict[str, Any] = {
            "test": "factory-reset",
            "module": mod_info.name,
            "address": addr,
        }

        reset_param_specs = _get_reset_param_specs_for_module(
            module_name=mod_info.name,
            location_tag_param_id=location_tag_param_id,
            im2_installation_date_param_id=im2_installation_date_param_id,
            cc_setpoint_out_param_id=cc_setpoint_out_param_id,
            cc_actual_out_param_id=cc_actual_out_param_id,
            cc_setpoint_in_param_id=cc_setpoint_in_param_id,
            cc_actual_in_param_id=cc_actual_in_param_id,
            log=log,
        )

        result["reset_param_names"] = [name for name, _pid, _value, _default in reset_param_specs]
        result["reset_param_ids"] = {
            name: pid for name, pid, _value, _default in reset_param_specs
        }

        # ── Step 1: write test values ─────────────────────────────────────
        write_data = _write_test_values(
            hw=hw,
            addr=addr,
            params_to_write=reset_param_specs,
            log=log,
        )
        result.update(write_data)

        if not write_data.get("write_ok"):
            log("warning", f"  Write phase failed for #{addr} — skipping resets")
            result["passed"] = False
            result["normal_reset_ok"] = None
            result["factory_reset_ok"] = None
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            all_results.append(result)
            continue

        # ── Step 2: warm restart + verify persistence ─────────────────────
        reset_ok = _trigger_reset(
            hw=hw,
            addr=addr,
            ip_address=ip_address,
            factory_reset=False,
            device_reset_param_id=device_reset_param_id,
            reset_wait=reset_reconnect_wait,
            log=log,
        )

        if not reset_ok and power_supply_comport:
            log("info", f"  Falling back to power-cycle for warm-restart on #{addr}")
            reset_ok = _power_cycle_reconnect(
                hw=hw,
                ip_address=ip_address,
                comport=power_supply_comport,
                channels=power_supply_channels or [1, 2, 4],
                voltage=power_supply_voltage,
                off_time=1.0,
                reconnect_wait=reconnect_wait,
                log=log,
            )

            if not reset_ok:
                log("error", "  Power cycle failed. Aborting test.")
                result["normal_reset_ok"] = False
                result["factory_reset_ok"] = None
                result["passed"] = False
                result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
                all_results.append(result)
                return all_results

        if not reset_ok:
            log("error", f"  Could not perform warm restart for #{addr}")
            result["normal_reset_ok"] = False
            result["factory_reset_ok"] = None
            result["passed"] = False
            result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            all_results.append(result)
            continue

        persist_data = _verify_persisted(
            hw=hw,
            addr=addr,
            params_to_check=reset_param_specs,
            log=log,
        )
        result.update(persist_data)

        normal_ok = persist_data.get("persist_ok", False)
        result["normal_reset_ok"] = normal_ok

        if normal_ok:
            log("info", f"  ✓ #{addr}: values persisted after warm restart")
        else:
            log("error", f"  ✗ #{addr}: persistence check failed after warm restart")

        # ── Step 3: factory reset + verify clearance ──────────────────────
        factory_reset_ok = _trigger_reset(
            hw=hw,
            addr=addr,
            ip_address=ip_address,
            factory_reset=True,
            device_reset_param_id=device_reset_param_id,
            reset_wait=reset_reconnect_wait,
            log=log,
        )

        if not factory_reset_ok:
            log("warning", f"  Factory reset not available for #{addr} — skipping clearance check")
            result["factory_reset_ok"] = None
            result["note"] = (
                "Factory reset not supported via parameter write on this device. "
                "Trigger manually and call factory_reset_verify() to check clearance."
            )
        else:
            factory_data = _verify_factory_defaults(
                hw=hw,
                addr=addr,
                params_to_check=reset_param_specs,
                log=log,
            )
            result.update(factory_data)

            fr_ok = factory_data.get("factory_ok", False)
            result["factory_reset_ok"] = fr_ok

            if fr_ok:
                log("info", f"  ✓ #{addr}: parameters cleared after factory reset")
            else:
                log("error", "  ✗ #{addr}: factory reset clearance check failed")

        fr_result = result.get("factory_reset_ok")
        result["passed"] = (
            write_data.get("write_ok", False)
            and normal_ok
            and (fr_result is None or fr_result is True)
        )

        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        all_results.append(result)

    return all_results

def verify_persistence_after_reset(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    module_address: int | None = None,
    location_tag_param_id: int = 20207,
    im2_installation_date_param_id: int = 11295004,
    cc_setpoint_out_param_id: int = 20094,
    cc_actual_out_param_id: int = 20095,
    cc_setpoint_in_param_id: int = 20294,
    cc_actual_in_param_id: int = 20295,
) -> list[dict]:
    """Phase 2: verify parameters persisted after a manual reset."""
    topology = hw.read_topology()

    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Verifying persistence on #{addr} {mod_info.name} …")

        reset_param_specs = _get_reset_param_specs_for_module(
            module_name=mod_info.name,
            location_tag_param_id=location_tag_param_id,
            im2_installation_date_param_id=im2_installation_date_param_id,
            cc_setpoint_out_param_id=cc_setpoint_out_param_id,
            cc_actual_out_param_id=cc_actual_out_param_id,
            cc_setpoint_in_param_id=cc_setpoint_in_param_id,
            cc_actual_in_param_id=cc_actual_in_param_id,
            log=log,
        )

        persist_data = _verify_persisted(
            hw=hw,
            addr=addr,
            params_to_check=reset_param_specs,
            log=log,
        )

        result = {
            "test": "factory-reset",
            "phase": "verify-persistence",
            "module": mod_info.name,
            "address": addr,
            "reset_param_names": [name for name, _pid, _value, _default in reset_param_specs],
            "reset_param_ids": {
                name: pid for name, pid, _value, _default in reset_param_specs
            },
            "passed": persist_data.get("persist_ok", False),
        }

        result.update(persist_data)
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results


def verify_factory_defaults(
    hw: HardwareInterface,
    log: LogFn = noop_log,
    module_address: int | None = None,
    location_tag_param_id: int = 20207,
    im2_installation_date_param_id: int = 11295004,
    cc_setpoint_out_param_id: int = 20094,
    cc_actual_out_param_id: int = 20095,
    cc_setpoint_in_param_id: int = 20294,
    cc_actual_in_param_id: int = 20295,
) -> list[dict]:
    """Phase 3: verify parameters cleared after a manual factory reset."""
    topology = hw.read_topology()

    if module_address is not None:
        topology = [m for m in topology if m.address == module_address]

    results: list[dict] = []

    for mod_info in topology:
        addr = mod_info.address
        ch_start = time.time()
        log("info", f"  Verifying factory defaults on #{addr} {mod_info.name} …")

        reset_param_specs = _get_reset_param_specs_for_module(
            module_name=mod_info.name,
            location_tag_param_id=location_tag_param_id,
            im2_installation_date_param_id=im2_installation_date_param_id,
            cc_setpoint_out_param_id=cc_setpoint_out_param_id,
            cc_actual_out_param_id=cc_actual_out_param_id,
            cc_setpoint_in_param_id=cc_setpoint_in_param_id,
            cc_actual_in_param_id=cc_actual_in_param_id,
            log=log,
        )

        factory_data = _verify_factory_defaults(
            hw=hw,
            addr=addr,
            params_to_check=reset_param_specs,
            log=log,
        )

        result = {
            "test": "factory-reset",
            "phase": "verify-factory-defaults",
            "module": mod_info.name,
            "address": addr,
            "reset_param_names": [name for name, _pid, _value, _default in reset_param_specs],
            "reset_param_ids": {
                name: pid for name, pid, _value, _default in reset_param_specs
            },
            "passed": factory_data.get("factory_ok", False),
        }

        result.update(factory_data)
        result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
        results.append(result)

    return results


# ── Private helper ─────────────────────────────────────────────────────────────


def _power_cycle_reconnect(
    hw: HardwareInterface,
    ip_address: str,
    comport: str,
    channels: list[int],
    voltage: float,
    off_time: float,
    reconnect_wait: float,
    log: LogFn,
) -> bool:
    """Power-cycle via HMP40x0, reconnect hw.  Returns True on success."""
    from power_supply import PowerCycleSession, PowerSupplyNotAvailable
    try:
        with PowerCycleSession(
            comport=comport,
            channels=channels,
            voltage=voltage,
            off_time=off_time,
            reconnect_wait=reconnect_wait,
        ) as ps:
            ps.cycle(hw, ip_address)
        return True
    except PowerSupplyNotAvailable as exc:
        log("error", f"  Power supply unavailable: {exc}")
        return False
    except Exception as exc:
        log("error", f"  Power cycle failed: {exc}")
        return False
