"""Connection wiring validation test.

Pulses each source output defined in *connections.jsonc* and reads the
connected target input to verify physical wiring is intact.

Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import time

from hal import HardwareInterface, SafeSession, CpxApHardware, ModuleInfo
from ._base import LogFn, load_connections, channel_index_from_port, noop_log

TEST_DEFINITION = {
    "test_id": "connection-validation",
    "name": "Connection Validation",
    "version": "1.0.0",
    "description": "Pulse source outputs and verify target inputs to validate wiring",
    "required_capabilities": [
        "digital_output"
    ],
    "required_wiring_type": "physical",
    "supported_categories": [
        "output",
        "input",
        "inout"
    ],
    "safety_class": "safe",
    "allowed_in_ci": True,
    "can_run_parallel": False,
    "singleton": False,
    "parameters": {
        "pulse_duration_s": 0.3
    },
    "compatible_modules": [
        "CPX-AP-A-*DO*",
        "CPX-AP-A-*HDO*",
        "CPX-AP-A-*DIO*",
        "CPX-AP-A-*DIDO*",
        "CPX-AP-A-*DI*DO*",
        "CPX-AP-I-*DO*",
        "CPX-AP-I-*DIO*",
        "CPX-AP-A-*IOL*",
        "CPX-AP-I-*IOL*",
        "VABX-A-*"
    ]
}


def validate_single(
    hw: HardwareInterface,
    conn: dict,
    src_mod: ModuleInfo,
    tgt_mod: ModuleInfo,
    pulse_duration_s: float = 0.3,
) -> dict:
    """Test one I/O connection by pulsing the source output and reading the target input."""
    src_addr = conn["source_module_addr"]
    tgt_addr = conn["target_module_addr"]
    src_ch = conn["source_channel"]
    tgt_ch = conn["target_channel"]

    cpp_src = 2 if "M12" in src_mod.name else 1
    cpp_tgt = 2 if "M12" in tgt_mod.name else 1

    port_num_src = int(src_ch.lstrip("X"))
    num_in_src = src_mod.num_inputs
    out_base = port_num_src * cpp_src - num_in_src

    port_num_tgt = int(tgt_ch.lstrip("X"))
    base_idx_tgt = port_num_tgt * cpp_tgt

    try:
        for i in range(cpp_src):
            hw.write_output(src_addr, out_base + i, False)
    except Exception:
        pass
    time.sleep(0.05)

    try:
        baseline_vals = [hw.read_input(tgt_addr, base_idx_tgt + i) for i in range(cpp_tgt)]
        baseline = all(baseline_vals)
    except Exception:
        baseline = False

    try:
        for i in range(cpp_src):
            hw.write_output(src_addr, out_base + i, True)
    except Exception as exc:
        return {
            "passed": False,
            "error": f"Cannot write to source: {exc}",
            "source_addr": src_addr, "target_addr": tgt_addr,
            "source_channel": src_ch, "target_channel": tgt_ch,
        }

    time.sleep(pulse_duration_s)
    try:
        actual_vals = [hw.read_input(tgt_addr, base_idx_tgt + i) for i in range(cpp_tgt)]
        actual = all(actual_vals)
    except Exception:
        actual = False

    try:
        for i in range(cpp_src):
            hw.write_output(src_addr, out_base + i, False)
    except Exception:
        pass

    return {
        "passed": bool(actual) and not baseline,
        "expected": True, "actual": actual, "baseline": baseline,
        "source_addr": src_addr, "target_addr": tgt_addr,
        "source_channel": src_ch, "target_channel": tgt_ch,
    }


def run(
    hw_or_ip: HardwareInterface | str,
    connections_path: str = "connections.jsonc",
    timeout: float = 0,
    pulse_duration_s: float = 0.3,
    log: LogFn = noop_log,
    connections: list[dict] | None = None,
) -> dict:
    """Validate all I/O connections listed in *connections_path*.

    Accepts either a pre-connected :class:`HardwareInterface` or an IP address
    string (creates a temporary :class:`SafeSession`).
    """
    if connections is None:
        connections = load_connections(connections_path)
    if not connections:
        log("warning", f"No connections found in '{connections_path}'")
        return {
            "ip_address": "", "total": 0, "passed": 0, "failed": 0,
            "all_passed": True, "results": [],
        }

    if isinstance(hw_or_ip, str):
        ip_address = hw_or_ip
        hw = CpxApHardware()
        session: SafeSession = SafeSession(hw, ip_address, timeout)
        own_session = True
    else:
        hw = hw_or_ip
        ip_address = ""
        own_session = False

    log("info", f"Validating {len(connections)} connection(s) …")
    results: list[dict] = []
    passed_count = 0
    t_start = time.time()

    try:
        if own_session:
            session.__enter__()

        topology = hw.read_topology()

        for i, conn in enumerate(connections, 1):
            src = f"#{conn['source_module_addr']}:{conn['source_channel']}"
            tgt = f"#{conn['target_module_addr']}:{conn['target_channel']}"
            label = f"{src} → {tgt}"
            log("info", f"  [{i}/{len(connections)}] Testing {label} …")

            src_addr = conn["source_module_addr"]
            tgt_addr = conn["target_module_addr"]
            src_mod = next((m for m in topology if m.address == src_addr), None)
            tgt_mod = next((m for m in topology if m.address == tgt_addr), None)

            if not src_mod:
                src_mod = ModuleInfo(name="", module_code=0, product_key="", address=src_addr)
            if not tgt_mod:
                tgt_mod = ModuleInfo(name="", module_code=0, product_key="", address=tgt_addr)

            try:
                ch_start = time.time()
                result = validate_single(hw, conn, src_mod, tgt_mod, pulse_duration_s)
                result["duration_ms"] = round((time.time() - ch_start) * 1000, 1)
            except Exception as exc:
                result = {
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "source_addr": conn.get("source_module_addr"),
                    "target_addr": conn.get("target_module_addr"),
                    "source_channel": conn.get("source_channel"),
                    "target_channel": conn.get("target_channel"),
                }

            results.append(result)
            if result.get("passed"):
                passed_count += 1
                log("info", f"  ✓ {label}: PASS")
            else:
                err_detail = result.get("error") or (
                    f"signal not received — baseline={result.get('baseline')}, "
                    f"actual={result.get('actual')}"
                )
                log("error", f"  ✗ {label}: FAIL — {err_detail}")
            time.sleep(0.05)

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log("error", f"Device connection failed: {err}")
        return {
            "ip_address": ip_address,
            "total": len(connections),
            "passed": passed_count,
            "failed": len(connections) - passed_count,
            "all_passed": False, "error": err, "results": results,
        }
    finally:
        if own_session:
            try:
                session.__exit__(None, None, None)
            except Exception:
                pass

    failed_count = len(results) - passed_count
    if failed_count == 0:
        log("info", f"✓ All {len(results)} connection(s) passed")
    else:
        log("warning", f"{failed_count}/{len(results)} connection(s) FAILED")

    return {
        "ip_address": ip_address, "total": len(results),
        "passed": passed_count, "failed": failed_count,
        "all_passed": passed_count == len(results), "results": results,
        "duration_ms": round((time.time() - t_start) * 1000, 1),
    }
