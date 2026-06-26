"""Condition Counter (CC) validation test.

For each I/O connection defined in *connections.jsonc*, checks that the source
module has outputs, the target module has inputs, and attempts to read the CC
parameters (20094 / 20095) from both.
"""
from __future__ import annotations

from typing import Any

from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
from cpx_io.cpx_system.cpx_ap.ap_parameter import Parameter

from ._base import LogFn, load_connections, noop_log


def _make_param(parameter_id: int, data_type: str) -> "Parameter":
    """Construct a minimal Parameter with only the fields needed for read/write."""
    return Parameter(
        parameter_id=parameter_id,
        parameter_instances={},
        is_writable=True,
        array_size=1,
        data_type=data_type,
        default_value=0,
        description="",
        name="",
    )


def run(
    cpx_ap: CpxAp,
    connections_path: str = "connections.jsonc",
    log: LogFn = noop_log,
) -> list[dict]:
    """Validate Condition Counter wiring for every defined connection.

    :param cpx_ap: Active CpxAp instance (caller opens / closes it).
    :param connections_path: Path to the connections JSONC file.
    :param log: Optional logging callback ``(level, message) -> None``.
    :returns: List of per-connection result dicts.
    """
    connections = load_connections(connections_path)
    if not connections:
        log("warning", f"No connections found in '{connections_path}'")
        return [{"test": "condition-counter", "passed": None,
                 "error": "No connections defined"}]

    param_setpoint = _make_param(20094, "UINT16")
    param_actual = _make_param(20095, "UINT16")

    results: list[dict] = []

    for conn in connections:
        src_addr = conn["source_module_addr"]
        tgt_addr = conn["target_module_addr"]
        label = f"#{src_addr}:{conn['source_channel']} → #{tgt_addr}:{conn['target_channel']}"
        log("info", f"  CC check {label} …")

        src_mod = next((m for m in cpx_ap.modules if m.position == src_addr), None)
        tgt_mod = next((m for m in cpx_ap.modules if m.position == tgt_addr), None)

        if src_mod is None or tgt_mod is None:
            missing = [a for a, m in [(src_addr, src_mod), (tgt_addr, tgt_mod)] if m is None]
            log("error", f"  ✗ {label}: module(s) #{missing} not found")
            results.append({
                "test": "condition-counter",
                "connection": label,
                "passed": False,
                "error": f"Module(s) at address {missing} not found on bus",
            })
            continue

        has_outputs = bool(src_mod.channels.outputs)
        has_inputs = bool(tgt_mod.channels.inputs)

        result: dict[str, Any] = {
            "test": "condition-counter",
            "connection": label,
            "source_module": src_mod.name,
            "target_module": tgt_mod.name,
            "source_has_outputs": has_outputs,
            "target_has_inputs": has_inputs,
        }

        if not has_outputs:
            result["passed"] = False
            result["error"] = f"Source #{src_addr} ({src_mod.name}) has no output channels"
            log("error", f"  ✗ {label}: {result['error']}")
        elif not has_inputs:
            result["passed"] = False
            result["error"] = f"Target #{tgt_addr} ({tgt_mod.name}) has no input channels"
            log("error", f"  ✗ {label}: {result['error']}")
        else:
            try:
                try:
                    cc_sp = cpx_ap.read_parameter(src_addr, param_setpoint)
                    result["cc_setpoint_source"] = cc_sp
                    log("info", f"    CC setpoint #{src_addr}: {cc_sp}")
                except Exception as exc:
                    result["cc_setpoint_source"] = f"N/A ({exc})"
                    log("warning", f"    CC setpoint #{src_addr}: not supported ({exc})")

                try:
                    cc_act = cpx_ap.read_parameter(tgt_addr, param_actual)
                    result["cc_actual_target"] = cc_act
                    log("info", f"    CC actual   #{tgt_addr}: {cc_act}")
                except Exception as exc:
                    result["cc_actual_target"] = f"N/A ({exc})"
                    log("warning", f"    CC actual #{tgt_addr}: not supported ({exc})")

                result["passed"] = True
                result["note"] = "Modules connected; CC parameters readable"
                log("info", f"  ✓ {label}: PASS")
            except Exception as exc:
                result["passed"] = False
                result["error"] = str(exc)
                log("error", f"  ✗ {label}: {exc}")

        results.append(result)

    return results
