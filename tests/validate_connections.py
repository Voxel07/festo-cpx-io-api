"""Connection wiring validation test.

Pulses each source output defined in *connections.jsonc* and reads the
connected target input to verify physical wiring is intact.
"""
from __future__ import annotations

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp

from generate_system_config import validate_single_connection
from ._base import LogFn, load_connections, noop_log


def run(
    ip_address: str,
    connections_path: str = "connections.jsonc",
    timeout: float = 0,
    pulse_duration_s: float = 0.3,
    log: LogFn = noop_log,
) -> dict:
    """Validate all I/O connections listed in *connections_path*.

    Returns a summary dict::

        {
            "ip_address": str,
            "total": int,
            "passed": int,
            "failed": int,
            "all_passed": bool,
            "results": [per-connection dicts],
            "error": str | None,   # set on device-level failure only
        }
    """
    connections = load_connections(connections_path)

    if not connections:
        log("warning", f"No connections found in '{connections_path}'")
        return {
            "ip_address": ip_address,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "all_passed": True,
            "results": [],
        }

    log("info", f"Connecting to {ip_address} to validate {len(connections)} connection(s) …")
    results: list[dict] = []
    passed_count = 0

    try:
        with CpxAp(ip_address=ip_address, timeout=timeout) as cpx_ap:
            for i, conn in enumerate(connections, 1):
                src = f"#{conn['source_module_addr']}:{conn['source_channel']}"
                tgt = f"#{conn['target_module_addr']}:{conn['target_channel']}"
                label = f"{src} → {tgt}"
                log("info", f"  [{i}/{len(connections)}] Testing {label} …")

                try:
                    result = validate_single_connection(cpx_ap, conn, pulse_duration_s)
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

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log("error", f"Device connection failed: {err}")
        return {
            "ip_address": ip_address,
            "total": len(connections),
            "passed": passed_count,
            "failed": len(connections) - passed_count,
            "all_passed": False,
            "error": err,
            "results": results,
        }

    failed_count = len(results) - passed_count
    if failed_count == 0:
        log("info", f"✓ All {len(results)} connection(s) passed")
    else:
        log("warning", f"{failed_count}/{len(results)} connection(s) FAILED")

    return {
        "ip_address": ip_address,
        "total": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "all_passed": passed_count == len(results),
        "results": results,
    }
