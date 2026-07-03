"""Connection wiring validation test.

Pulses each source output defined in *connections.jsonc* and reads the
connected target input to verify physical wiring is intact.

Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import time

from hal import HardwareInterface, SafeSession, CpxApHardware, ModuleInfo
from config_models import BenchConfig
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
    
    src_sub = conn.get("source_subchannel")
    tgt_sub = conn.get("target_subchannel")

    cpp_src = 2 if "M12" in src_mod.name else 1
    cpp_tgt = 2 if "M12" in tgt_mod.name else 1

    port_num_src = int(src_ch.lstrip("X"))
    num_in_src = src_mod.num_inputs
    out_base = port_num_src * cpp_src - num_in_src

    port_num_tgt = int(tgt_ch.lstrip("X"))
    base_idx_tgt = port_num_tgt * cpp_tgt

    # Determine which exact channels to test
    src_offsets = [src_sub] if src_sub is not None else range(cpp_src)
    tgt_offsets = [tgt_sub] if tgt_sub is not None else range(cpp_tgt)

    direction_param_id = 20145

    # Configure target direction if it has inouts (False = input)
    if tgt_mod.num_inouts > 0:
        for i in tgt_offsets:
            try:
                hw.write_parameter(tgt_addr, direction_param_id, False, instance=base_idx_tgt + i + 1)
            except Exception:
                pass

    # Configure source direction if it has inouts (True = output)
    if src_mod.num_inouts > 0:
        for i in src_offsets:
            try:
                hw.write_parameter(src_addr, direction_param_id, True, instance=out_base + i + 1)
            except Exception:
                pass
            
    time.sleep(0.05)

    try:
        for i in src_offsets:
            hw.write_output(src_addr, out_base + i, False)
    except Exception:
        pass
    time.sleep(0.05)

    try:
        baseline_vals = [hw.read_input(tgt_addr, base_idx_tgt + i) for i in tgt_offsets]
        baseline = all(baseline_vals)
    except Exception:
        baseline = False

    try:
        for i in src_offsets:
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
        actual_vals = [hw.read_input(tgt_addr, base_idx_tgt + i) for i in tgt_offsets]
        actual = all(actual_vals)
    except Exception:
        actual = False

    try:
        for i in src_offsets:
            hw.write_output(src_addr, out_base + i, False)
            if src_mod.num_inouts > 0:
                # Restore to input (default)
                try:
                    hw.write_parameter(src_addr, direction_param_id, False, instance=out_base + i + 1)
                except Exception:
                    pass
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
    log: LogFn = noop_log,
    bench_config: BenchConfig | None = None,
    module_address: int | None = None,
) -> dict:
    """Validate all I/O connections listed in *connections_path*.

    Accepts either a pre-connected :class:`HardwareInterface` or an IP address
    string (creates a temporary :class:`SafeSession`).
    """
    pulse_duration_s = TEST_DEFINITION["parameters"]["pulse_duration_s"]
    timeout = 0
    connections = []
    if bench_config:
        # Find module_instance_id for the given module_address
        target_instance_id = None
        if module_address is not None:
            for m in bench_config.module_instances:
                if m.address == module_address:
                    target_instance_id = m.instance_id
                    break

        for wire in bench_config.wiring:
            if target_instance_id is None or wire.target_instance_id == target_instance_id or wire.source_instance_id == target_instance_id:
                src_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.source_instance_id), None)
                tgt_mod = next((m for m in bench_config.module_instances if m.instance_id == wire.target_instance_id), None)
                if src_mod and tgt_mod:
                    connections.append({
                        "source_module_addr": src_mod.address,
                        "source_channel": wire.source_channel,
                        "target_module_addr": tgt_mod.address,
                        "target_channel": wire.target_channel,
                    })

    if not connections:
        log("warning", f"No connections found for module {module_address}")
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
