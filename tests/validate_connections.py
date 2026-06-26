"""Connection wiring validation test.

Pulses each source output defined in *connections.jsonc* and reads the
connected target input to verify physical wiring is intact.

Uses :class:`hal.HardwareInterface` — never imports ``CpxAp`` directly.
"""
from __future__ import annotations

import time

from hal import HardwareInterface, SafeSession, CpxApHardware
from ._base import LogFn, load_connections, channel_index_from_port, noop_log


def validate_single(
    hw: HardwareInterface,
    conn: dict,
    pulse_duration_s: float = 0.3,
) -> dict:
    """Test one I/O connection by pulsing the source output and reading the target input."""
    src_addr = conn["source_module_addr"]
    tgt_addr = conn["target_module_addr"]
    src_ch = conn["source_channel"]
    tgt_ch = conn["target_channel"]

    src_idx = channel_index_from_port(src_ch)
    tgt_idx = channel_index_from_port(tgt_ch)

    try:
        hw.write_output(src_addr, src_idx, False)
    except Exception:
        pass
    time.sleep(0.05)

    baseline = hw.read_input(tgt_addr, tgt_idx)

    try:
        hw.write_output(src_addr, src_idx, True)
    except Exception as exc:
        return {
            "passed": False,
            "error": f"Cannot write to source: {exc}",
            "source_addr": src_addr, "target_addr": tgt_addr,
            "source_channel": src_ch, "target_channel": tgt_ch,
        }

    time.sleep(pulse_duration_s)
    actual = hw.read_input(tgt_addr, tgt_idx)

    try:
        hw.write_output(src_addr, src_idx, False)
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
) -> dict:
    """Validate all I/O connections listed in *connections_path*.

    Accepts either a pre-connected :class:`HardwareInterface` or an IP address
    string (creates a temporary :class:`SafeSession`).
    """
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

        for i, conn in enumerate(connections, 1):
            src = f"#{conn['source_module_addr']}:{conn['source_channel']}"
            tgt = f"#{conn['target_module_addr']}:{conn['target_channel']}"
            label = f"{src} → {tgt}"
            log("info", f"  [{i}/{len(connections)}] Testing {label} …")

            try:
                ch_start = time.time()
                result = validate_single(hw, conn, pulse_duration_s)
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
