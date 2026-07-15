"""Thin GitLab client for the FastAPI test service.

This module never imports hardware or test modules.  Validation, planning,
execution, persistence, and JUnit rendering remain API responsibilities.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _request(base_url: str, method: str, path: str, payload: dict | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.headers.get_content_type() == "application/json":
                return json.loads(body)
            return body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {method} {path} failed ({exc.code}): {detail}") from exc


def _wait_for_api(base_url: str, timeout_s: float = 30) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _request(base_url, "GET", "/hw/status")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"API did not become ready at {base_url}")


def _plan_payload(config_path: str) -> dict[str, Any]:
    return {
        "config_path": config_path,
        "test_filter": os.environ.get("TEST_FILTER") or None,
        "safety_class_filter": os.environ.get("SAFETY_CLASS_FILTER") or None,
    }


def plan(base_url: str, config_path: str, output: Path) -> dict[str, Any]:
    result = _request(base_url, "POST", "/test-run/plan", _plan_payload(config_path))
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def run(base_url: str, config_path: str, output: Path, junit: Path) -> int:
    execution_plan = plan(base_url, config_path, Path("plan.json"))
    test_ids = list(dict.fromkeys(
        instance["test_id"] for instance in execution_plan.get("instances", [])
    ))
    if not test_ids:
        raise RuntimeError("The API resolved an empty execution plan")
    start = _request(base_url, "POST", "/test-run/start", {
        "ip_address": os.environ.get("CPX_IP") or execution_plan.get("test_bench_ip"),
        "config_path": config_path,
        "tests": test_ids,
        "source": "ci",
        "allow_destructive": os.environ.get("ALLOW_DESTRUCTIVE", "false").lower() == "true",
        "allow_negative": os.environ.get("ALLOW_NEGATIVE", "false").lower() == "true",
        "per_test_timeout_s": float(os.environ.get("PER_TEST_TIMEOUT_S", "300")),
    })
    run_id = start["run_id"]
    while True:
        status = _request(base_url, "GET", "/test-run/status")
        if status.get("run_id") == run_id and status.get("status") not in {"running", "aborting"}:
            break
        time.sleep(2)
    output.write_text(json.dumps(status, indent=2), encoding="utf-8")
    junit.write_bytes(_request(base_url, "GET", f"/test-run/{run_id}/junit.xml"))
    return 0 if status.get("status") == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="GitLab client for the CPX API")
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument("--api-url", default=os.environ.get("TEST_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", default="plan.json")
    parser.add_argument("--junit", default="results.xml")
    args = parser.parse_args()
    _wait_for_api(args.api_url)
    if args.command == "plan":
        plan(args.api_url, args.config, Path(args.output))
        return 0
    return run(args.api_url, args.config, Path(args.output), Path(args.junit))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CI API client failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

