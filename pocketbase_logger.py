"""
PocketBase logger for the CPX-AP test system.

Logs all test runs, system events, and errors to a PocketBase instance.
Requires environment variables ``PB_USERNAME`` and ``PB_PASSWORD``
for a **regular user** (not admin).  The user must have create/read
permissions on the ``festo_*`` collections.

Usage::

    from pocketbase_logger import pb_log

    pb_log.test_run_started(run_id="run-123", source="web", tests=["cc", "valve"])
    pb_log.checkpoint(run_id="run-123", test="cc", status="passed")
    pb_log.test_run_completed(run_id="run-123", results=[...])
    pb_log.error(run_id="run-123", message="Connection timeout")

Schema import
-------------
Import ``pocketbase_schema.json`` via the PocketBase Admin UI
(Settings → Import collections) to create all required collections at once.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

# ─── Configuration ────────────────────────────────────────────────────────────

PB_URL = os.environ.get("PB_URL") or os.environ.get("POCKETBASE_URL", "http://localhost:8090")
PB_USERNAME = os.environ.get("PB_USERNAME", "")
PB_PASSWORD = os.environ.get("PB_PASSWORD", "")

# ─── Collection names (prefixed for easy identification in PocketBase) ────────

COLL_TEST_RUNS   = "festo_test_runs"
COLL_CHECKPOINTS = "festo_checkpoints"
COLL_SYSTEM_LOGS = "festo_system_logs"
COLL_MEASUREMENTS = "festo_measurements"

# ─── Internal state ───────────────────────────────────────────────────────────

_token: str | None = None
_token_expiry: float = 0


def _authenticate() -> str:
    """Authenticate as a regular PocketBase user (not admin).

    Uses the users collection auth endpoint introduced in PocketBase 0.8+.
    """
    global _token, _token_expiry

    if _token and time.time() < _token_expiry - 60:
        return _token

    if not PB_USERNAME or not PB_PASSWORD:
        _token = None
        return ""

    try:
        resp = requests.post(
            f"{PB_URL}/api/collections/users/auth-with-password",
            json={"identity": PB_USERNAME, "password": PB_PASSWORD},
            timeout=(1.5, 3),
        )
        resp.raise_for_status()
        data = resp.json()
        _token = data.get("token", "")
        _token_expiry = time.time() + 3600
        return _token
    except Exception as exc:
        _token = None
        print(f"[PocketBase] Auth failed ({PB_URL}): {exc}", flush=True)
        return ""


def _headers() -> dict[str, str]:
    """Return request headers, optionally with auth token."""
    h = {"Content-Type": "application/json"}
    token = _authenticate()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _post(collection: str, data: dict) -> bool:
    """Create a record in *collection*.  Returns True on success.

    Uses short timeouts so an unreachable PocketBase never blocks test execution.
    Prints errors to stderr so failures are visible in the uvicorn console.
    """
    try:
        resp = requests.post(
            f"{PB_URL}/api/collections/{collection}/records",
            json=data,
            headers=_headers(),
            timeout=(1.0, 2.0),  # (connect, read) — fail in < 3s total
        )
        ok = resp.status_code in (200, 201)
        if not ok:
            print(f"[PocketBase] POST {collection} → HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
        return ok
    except Exception as exc:
        print(f"[PocketBase] POST {collection} → {exc}", flush=True)
        return False


def _ensure_collections() -> None:
    """Idempotently create required PocketBase collections if they don't exist.

    **Requires admin authentication.**  For regular users, import
    ``pocketbase_schema.json`` via the PocketBase Admin UI instead.

    Run once at startup if you have admin credentials set.
    """
    collections = {
        COLL_TEST_RUNS: [
            {"name": "run_id", "type": "text", "required": True},
            {"name": "source", "type": "text"},
            {"name": "ip_address", "type": "text"},
            {"name": "status", "type": "text"},
            {"name": "tests", "type": "json"},
            {"name": "results", "type": "json"},
            {"name": "started_at", "type": "text"},
            {"name": "completed_at", "type": "text"},
            {"name": "test_bench_id", "type": "text"},
            {"name": "test_code_commit", "type": "text"},
            {"name": "config_commit", "type": "text"},
            {"name": "gitlab_pipeline_id", "type": "text"},
            {"name": "gitlab_job_id", "type": "text"},
            {"name": "resolved_plan_id", "type": "text"},
            {"name": "schema_version", "type": "text"},
        ],
        COLL_CHECKPOINTS: [
            {"name": "run_id", "type": "text", "required": True},
            {"name": "test", "type": "text"},
            {"name": "status", "type": "text"},
            {"name": "error", "type": "text"},
            {"name": "timestamp", "type": "text"},
        ],
        COLL_SYSTEM_LOGS: [
            {"name": "run_id", "type": "text"},
            {"name": "level", "type": "text"},
            {"name": "message", "type": "text"},
            {"name": "details", "type": "json"},
            {"name": "timestamp", "type": "text"},
        ],
        COLL_MEASUREMENTS: [
            {"name": "run_id", "type": "text", "required": True},
            {"name": "test_result_id", "type": "text"},
            {"name": "name", "type": "text", "required": True},
            {"name": "value", "type": "number"},
            {"name": "unit", "type": "text"},
            {"name": "limit_lower", "type": "number"},
            {"name": "limit_upper", "type": "number"},
            {"name": "timestamp", "type": "text"},
        ],
    }

    token = _authenticate()
    if not token:
        return  # Can't create collections without auth

    for coll_name, fields in collections.items():
        try:
            resp = requests.get(
                f"{PB_URL}/api/collections/{coll_name}",
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp.status_code == 200:
                continue  # Already exists
        except Exception:
            pass

        try:
            requests.post(
                f"{PB_URL}/api/collections",
                json={"name": coll_name, "type": "base", "schema": fields},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
        except Exception:
            pass


# ─── Public API ────────────────────────────────────────────────────────────────


class PocketBaseLogger:
    """Stateless logger that writes to PocketBase collections."""

    def test_run_started(
        self,
        run_id: str,
        source: str,
        ip_address: str,
        tests: list[str],
        test_bench_id: str = "",
        test_code_commit: str = "",
        config_commit: str = "",
        gitlab_pipeline_id: str = "",
        gitlab_job_id: str = "",
        resolved_plan_id: str = "",
        schema_version: str = "",
    ) -> None:
        _post(COLL_TEST_RUNS, {
            "run_id": run_id,
            "source": source,
            "ip_address": ip_address,
            "status": "running",
            "tests": json.dumps(tests),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "test_bench_id": test_bench_id,
            "test_code_commit": test_code_commit,
            "config_commit": config_commit,
            "gitlab_pipeline_id": gitlab_pipeline_id,
            "gitlab_job_id": gitlab_job_id,
            "resolved_plan_id": resolved_plan_id,
            "schema_version": schema_version,
        })

    def test_run_completed(self, run_id: str, results: list[dict]) -> None:
        # Update the existing record — do NOT create a duplicate
        try:
            resp = requests.get(
                f"{PB_URL}/api/collections/{COLL_TEST_RUNS}/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 1},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    record_id = items[0]["id"]
                    requests.patch(
                        f"{PB_URL}/api/collections/{COLL_TEST_RUNS}/records/{record_id}",
                        json={
                            "status": "completed",
                            "results": json.dumps(results, default=str),
                            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                        headers=_headers(),
                        timeout=(1.0, 2.0),
                    )
                    return
        except Exception:
            pass
        # Fallback: create if no existing record found
        _post(COLL_TEST_RUNS, {
            "run_id": run_id,
            "status": "completed",
            "results": json.dumps(results, default=str),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def checkpoint(
        self, run_id: str, test: str, status: str, error: str | None = None,
    ) -> None:
        _post(COLL_CHECKPOINTS, {
            "run_id": run_id,
            "test": test,
            "status": status,
            "error": error or "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def measurement(
        self,
        run_id: str,
        name: str,
        value: float,
        unit: str = "",
        limit_lower: float | None = None,
        limit_upper: float | None = None,
        test_result_id: str = "",
    ) -> None:
        _post(COLL_MEASUREMENTS, {
            "run_id": run_id,
            "test_result_id": test_result_id,
            "name": name,
            "value": value,
            "unit": unit,
            "limit_lower": limit_lower if limit_lower is not None else "",
            "limit_upper": limit_upper if limit_upper is not None else "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def log(
        self, run_id: str | None, level: str, message: str, details: dict | None = None,
    ) -> None:
        _post(COLL_SYSTEM_LOGS, {
            "run_id": run_id or "",
            "level": level,
            "message": message,
            "details": json.dumps(details, default=str) if details else "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def error(self, run_id: str | None, message: str, details: dict | None = None) -> None:
        self.log(run_id, "error", message, details)

    def info(self, run_id: str | None, message: str) -> None:
        self.log(run_id, "info", message)

    def get_run_history(self, limit: int = 50) -> list[dict]:
        """Retrieve the most recent test runs."""
        try:
            resp = requests.get(
                f"{PB_URL}/api/collections/{COLL_TEST_RUNS}/records",
                params={"sort": "-created", "perPage": limit},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp.status_code == 200:
                return resp.json().get("items", [])
        except Exception:
            pass
        return []

    def get_run_detail(self, run_id: str) -> dict | None:
        """Get full details for a specific run including checkpoints."""
        try:
            resp = requests.get(
                f"{PB_URL}/api/collections/{COLL_TEST_RUNS}/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 1},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    run = items[0]
                    cp_resp = requests.get(
                        f"{PB_URL}/api/collections/{COLL_CHECKPOINTS}/records",
                        params={"filter": f"(run_id='{run_id}')", "sort": "created", "perPage": 500},
                        headers=_headers(),
                        timeout=(1.0, 2.0),
                    )
                    if cp_resp.status_code == 200:
                        run["checkpoints"] = cp_resp.json().get("items", [])
                    try:
                        log_resp = requests.get(
                            f"{PB_URL}/api/collections/{COLL_SYSTEM_LOGS}/records",
                            params={"filter": f"(run_id='{run_id}')", "sort": "created", "perPage": 1000},
                            headers=_headers(),
                            timeout=(1.0, 2.0),
                        )
                        if log_resp.status_code == 200:
                            run["logs"] = log_resp.json().get("items", [])
                    except Exception:
                        pass
                    return run
        except Exception:
            pass
        return None

    def delete_run(self, run_id: str) -> bool:
        """Delete a test run and its associated checkpoints/logs from PocketBase."""
        try:
            # 1. Delete test run record
            resp = requests.get(
                f"{PB_URL}/api/collections/{COLL_TEST_RUNS}/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 100},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp.status_code == 200:
                for item in resp.json().get("items", []):
                    requests.delete(
                        f"{PB_URL}/api/collections/{COLL_TEST_RUNS}/records/{item['id']}",
                        headers=_headers(),
                        timeout=(1.0, 2.0),
                    )

            # 2. Delete checkpoints
            resp_cp = requests.get(
                f"{PB_URL}/api/collections/{COLL_CHECKPOINTS}/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 500},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp_cp.status_code == 200:
                for item in resp_cp.json().get("items", []):
                    requests.delete(
                        f"{PB_URL}/api/collections/{COLL_CHECKPOINTS}/records/{item['id']}",
                        headers=_headers(),
                        timeout=(1.0, 2.0),
                    )

            # 3. Delete system logs
            resp_log = requests.get(
                f"{PB_URL}/api/collections/{COLL_SYSTEM_LOGS}/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 500},
                headers=_headers(),
                timeout=(1.0, 2.0),
            )
            if resp_log.status_code == 200:
                for item in resp_log.json().get("items", []):
                    requests.delete(
                        f"{PB_URL}/api/collections/{COLL_SYSTEM_LOGS}/records/{item['id']}",
                        headers=_headers(),
                        timeout=(1.0, 2.0),
                    )
            return True
        except Exception as exc:
            print(f"[PocketBase] Delete run failed: {exc}", flush=True)
            return False

    def clear_history(self) -> bool:
        """Delete all test runs, checkpoints, and logs from PocketBase."""
        try:
            for collection in (COLL_TEST_RUNS, COLL_CHECKPOINTS, COLL_SYSTEM_LOGS):
                while True:
                    resp = requests.get(
                        f"{PB_URL}/api/collections/{collection}/records",
                        params={"perPage": 100},
                        headers=_headers(),
                        timeout=(1.0, 2.0),
                    )
                    if resp.status_code != 200:
                        break
                    items = resp.json().get("items", [])
                    if not items:
                        break
                    for item in items:
                        requests.delete(
                            f"{PB_URL}/api/collections/{collection}/records/{item['id']}",
                            headers=_headers(),
                            timeout=(1.0, 2.0),
                        )
                    if len(items) < 100:
                        break
            return True
        except Exception as exc:
            print(f"[PocketBase] Clear history failed: {exc}", flush=True)
            return False


# Singleton (backward-compatible)
pb_log = PocketBaseLogger()


# ─── Bridge to repository pattern ─────────────────────────────────────────────

def get_repository() -> "PocketBaseRepository | None":
    """Return a repository instance or None if the new module is unavailable.

    Usage::

        repo = get_repository()
        if repo:
            repo.create_test_run(TestRunRecord(run_id="r1", ...))
    """
    try:
        from repository import PocketBaseRepository
        return PocketBaseRepository()
    except ImportError:
        return None
