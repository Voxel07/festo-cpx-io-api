"""Process-isolated execution used exclusively by the FastAPI test service."""

from __future__ import annotations

import multiprocessing
import queue
import time
import traceback
from dataclasses import asdict
from typing import Any, Callable

from config_models import SafetyClass
from resolver import ResolvedTestInstance


def _instance_from_dict(payload: dict[str, Any]) -> ResolvedTestInstance:
    payload = dict(payload)
    payload["safety_class"] = SafetyClass(payload.get("safety_class", SafetyClass.SAFE))
    return ResolvedTestInstance(**payload)


def _child_execute(
    result_queue: multiprocessing.Queue,
    instance_payload: dict[str, Any],
    config_path: str,
    ip_address: str,
) -> None:
    """Child-process target.  It owns both the connection and SafeSession."""
    logs: list[dict[str, str]] = []

    def log(level: str, message: str) -> None:
        logs.append({
            "level": level,
            "message": message,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    try:
        from api import _run_single_test_hw
        from hal import CpxApHardware, SafeSession

        instance = _instance_from_dict(instance_payload)
        with SafeSession(CpxApHardware(), ip_address) as hardware:
            result = _run_single_test_hw(hardware, instance, config_path, log)
        result_queue.put({"result": result, "logs": logs})
    except BaseException as exc:
        result_queue.put({
            "result": {
                "test_id": instance_payload.get("test_id", "unknown"),
                "passed": False,
                "error": str(exc),
                "exception_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            },
            "logs": logs,
        })


def _emergency_reset(ip_address: str) -> str | None:
    """Reconnect after a killed child and force the bench back to a safe state."""
    try:
        from hal import CpxApHardware, SafeSession

        with SafeSession(CpxApHardware(), ip_address):
            pass
        return None
    except Exception as exc:
        return str(exc)


def execute_resolved_instance(
    instance: ResolvedTestInstance,
    config_path: str,
    ip_address: str,
    timeout_s: float,
    should_abort: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Run one resolved instance in a killable child process.

    Timeout and abort both terminate the child, then reconnect once solely to
    reset outputs and restore configurable port directions.
    """
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    payload = asdict(instance)
    payload["safety_class"] = instance.safety_class.value
    process = context.Process(
        target=_child_execute,
        args=(result_queue, payload, config_path, ip_address),
        name=f"festo-test-{instance.unique_id[:50]}",
    )
    process.start()
    deadline = time.monotonic() + timeout_s
    termination_reason = ""
    while process.is_alive():
        process.join(timeout=0.25)
        if should_abort and should_abort():
            termination_reason = "aborted"
            break
        if time.monotonic() >= deadline:
            termination_reason = "timeout"
            break
    if termination_reason:
        process.terminate()
        process.join(timeout=5)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=2)
        reset_error = _emergency_reset(ip_address)
        result = {
            "test_id": instance.test_id,
            "passed": False,
            "error": (
                "Aborted by user" if termination_reason == "aborted"
                else f"Test exceeded the {timeout_s:g}s safety timeout"
            ),
            "exception_type": "TestAborted" if termination_reason == "aborted" else "TestTimeout",
            "timed_out": termination_reason == "timeout",
            "aborted": termination_reason == "aborted",
        }
        if reset_error:
            result["emergency_reset_error"] = reset_error
        return result, []
    try:
        message = result_queue.get(timeout=2)
    except queue.Empty:
        message = {
            "result": {
                "test_id": instance.test_id,
                "passed": False,
                "error": f"Test worker exited with code {process.exitcode} without returning a result",
                "exception_type": "WorkerProcessError",
            },
            "logs": [],
        }
    finally:
        result_queue.close()
    return message["result"], message.get("logs", [])

