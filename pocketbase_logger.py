"""
PocketBase logger for the CPX-AP test system.

Logs all test runs, system events, and errors to a PocketBase instance running
on localhost.  Requires environment variables ``PB_USERNAME`` and ``PB_PASSWORD``
(or set them directly in code).

Usage::

    from pocketbase_logger import pb_log

    pb_log.test_run_started(run_id="run-123", source="web", tests=["cc", "valve"])
    pb_log.checkpoint(run_id="run-123", test="cc", status="passed")
    pb_log.test_run_completed(run_id="run-123", results=[...])
    pb_log.error(run_id="run-123", message="Connection timeout")
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

# ─── Configuration ────────────────────────────────────────────────────────────

PB_URL = os.environ.get("PB_URL", "http://localhost:8090")
PB_USERNAME = os.environ.get("PB_USERNAME", "")
PB_PASSWORD = os.environ.get("PB_PASSWORD", "")

# ─── Internal state ───────────────────────────────────────────────────────────

_token: str | None = None
_token_expiry: float = 0


def _authenticate() -> str:
    """Authenticate with PocketBase and return a bearer token."""
    global _token, _token_expiry

    if _token and time.time() < _token_expiry - 60:
        return _token

    if not PB_USERNAME or not PB_PASSWORD:
        # No credentials → skip authentication, operate without token
        _token = None
        return ""

    try:
        resp = requests.post(
            f"{PB_URL}/api/admins/auth-with-password",
            json={"identity": PB_USERNAME, "password": PB_PASSWORD},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        _token = data.get("token", "")
        _token_expiry = time.time() + 3600  # assume 1h validity
        return _token
    except Exception:
        _token = None
        return ""


def _headers() -> dict[str, str]:
    """Return request headers, optionally with auth token."""
    h = {"Content-Type": "application/json"}
    token = _authenticate()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _post(collection: str, data: dict) -> bool:
    """Create a record in *collection*.  Returns True on success."""
    try:
        resp = requests.post(
            f"{PB_URL}/api/collections/{collection}/records",
            json=data,
            headers=_headers(),
            timeout=5,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def _ensure_collections() -> None:
    """Idempotently create required PocketBase collections if they don't exist.

    Run this once at startup.  Requires admin authentication.
    """
    collections = {
        "test_runs": [
            {"name": "run_id", "type": "text", "required": True},
            {"name": "source", "type": "text"},
            {"name": "ip_address", "type": "text"},
            {"name": "status", "type": "text"},
            {"name": "tests", "type": "json"},
            {"name": "results", "type": "json"},
            {"name": "started_at", "type": "text"},
            {"name": "completed_at", "type": "text"},
        ],
        "checkpoints": [
            {"name": "run_id", "type": "text", "required": True},
            {"name": "test", "type": "text"},
            {"name": "status", "type": "text"},
            {"name": "error", "type": "text"},
            {"name": "timestamp", "type": "text"},
        ],
        "system_logs": [
            {"name": "run_id", "type": "text"},
            {"name": "level", "type": "text"},
            {"name": "message", "type": "text"},
            {"name": "details", "type": "json"},
            {"name": "timestamp", "type": "text"},
        ],
    }

    token = _authenticate()
    if not token:
        return  # Can't create collections without admin auth

    for coll_name, fields in collections.items():
        try:
            # Check if collection exists
            resp = requests.get(
                f"{PB_URL}/api/collections/{coll_name}",
                headers=_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                continue  # Already exists
        except Exception:
            pass

        # Create collection
        try:
            requests.post(
                f"{PB_URL}/api/collections",
                json={"name": coll_name, "type": "base", "schema": fields},
                headers=_headers(),
                timeout=5,
            )
        except Exception:
            pass


# ─── Public API ────────────────────────────────────────────────────────────────


class PocketBaseLogger:
    """Stateless logger that writes to PocketBase collections."""

    def test_run_started(
        self, run_id: str, source: str, ip_address: str, tests: list[str],
    ) -> None:
        _post("test_runs", {
            "run_id": run_id,
            "source": source,
            "ip_address": ip_address,
            "status": "running",
            "tests": json.dumps(tests),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def test_run_completed(self, run_id: str, results: list[dict]) -> None:
        _post("test_runs", {
            "run_id": run_id,
            "status": "completed",
            "results": json.dumps(results, default=str),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        # Update existing record
        try:
            # Find the record by run_id
            resp = requests.get(
                f"{PB_URL}/api/collections/test_runs/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 1},
                headers=_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    record_id = items[0]["id"]
                    requests.patch(
                        f"{PB_URL}/api/collections/test_runs/records/{record_id}",
                        json={
                            "status": "completed",
                            "results": json.dumps(results, default=str),
                            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                        headers=_headers(),
                        timeout=5,
                    )
        except Exception:
            pass

    def checkpoint(
        self, run_id: str, test: str, status: str, error: str | None = None,
    ) -> None:
        _post("checkpoints", {
            "run_id": run_id,
            "test": test,
            "status": status,
            "error": error or "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def log(
        self, run_id: str | None, level: str, message: str, details: dict | None = None,
    ) -> None:
        _post("system_logs", {
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
                f"{PB_URL}/api/collections/test_runs/records",
                params={"sort": "-created", "perPage": limit},
                headers=_headers(),
                timeout=5,
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
                f"{PB_URL}/api/collections/test_runs/records",
                params={"filter": f"(run_id='{run_id}')", "perPage": 1},
                headers=_headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    run = items[0]
                    # Also fetch checkpoints
                    cp_resp = requests.get(
                        f"{PB_URL}/api/collections/checkpoints/records",
                        params={"filter": f"(run_id='{run_id}')", "sort": "created", "perPage": 500},
                        headers=_headers(),
                        timeout=5,
                    )
                    if cp_resp.status_code == 200:
                        run["checkpoints"] = cp_resp.json().get("items", [])
                    return run
        except Exception:
            pass
        return None


# Singleton
pb_log = PocketBaseLogger()
