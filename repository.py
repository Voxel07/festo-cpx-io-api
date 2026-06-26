"""Database abstraction layer (Repository pattern).

Defines :class:`ResultRepository` (ABC) with PocketBase as the initial
implementation.  A future PostgreSQL implementation can be added without
changing business logic.

Usage::

    from repository import PocketBaseRepository
    repo = PocketBaseRepository("http://localhost:8090", "admin", "password")
    run_id = repo.create_test_run(bench_id="bench-03", source="ci")
    repo.add_test_result(TestResultRecord(run_id=run_id, ...))
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests


# ─── Domain records ───────────────────────────────────────────────────────────


@dataclass
class TestRunRecord:
    """A single test execution run."""

    run_id: str
    test_bench_id: str = ""
    source: str = "unknown"  # 'web', 'ci'
    ip_address: str = ""
    status: str = "running"  # running, completed, error, aborted
    test_code_commit: str = ""
    config_commit: str = ""
    gitlab_pipeline_id: str = ""
    gitlab_job_id: str = ""
    tests: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""


@dataclass
class TestResultRecord:
    """Result of a single test instance."""

    run_id: str
    test_id: str
    test_version: str = ""
    test_name: str = ""
    module_instance_id: str = ""
    module_code: int = 0
    product_key: str = ""
    channel_id: str | None = None
    verdict: str = "unknown"  # passed, failed, error, skipped
    start_time: str = ""
    end_time: str = ""
    duration_ms: float = 0
    failure_reason: str = ""
    exception_type: str = ""
    stack_trace: str = ""


@dataclass
class MeasurementRecord:
    """A single measurement taken during a test."""

    test_result_id: str = ""
    run_id: str = ""
    name: str = ""
    value: float = 0.0
    unit: str = ""
    limit_lower: float | None = None
    limit_upper: float | None = None
    timestamp: str = ""


@dataclass
class LogEventRecord:
    """Structured log event."""

    run_id: str = ""
    level: str = "info"  # debug, info, warning, error
    message: str = ""
    event_type: str = ""  # test_start, test_end, output_change, error, etc.
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


# ─── Abstract repository ─────────────────────────────────────────────────────


class ResultRepository(ABC):
    """Abstract interface for persisting test results."""

    @abstractmethod
    def create_test_run(self, record: TestRunRecord) -> bool:
        """Create a new test run record."""

    @abstractmethod
    def update_test_run(self, run_id: str, status: str) -> bool:
        """Update the status of an existing test run."""

    @abstractmethod
    def add_test_result(self, record: TestResultRecord) -> bool:
        """Record a single test result."""

    @abstractmethod
    def add_measurement(self, record: MeasurementRecord) -> bool:
        """Record a measurement."""

    @abstractmethod
    def add_log_event(self, record: LogEventRecord) -> bool:
        """Record a structured log event."""

    @abstractmethod
    def get_run_history(
        self, bench_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Retrieve recent test runs."""

    @abstractmethod
    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        """Get full detail for a specific run."""


# ─── PocketBase implementation ───────────────────────────────────────────────


class PocketBaseRepository(ResultRepository):
    """PocketBase-backed repository.

    Collections used:
    - ``test_runs``     — top-level run records
    - ``checkpoints``   — per-test-instance results
    - ``system_logs``   — structured log events
    """

    def __init__(
        self,
        url: str | None = None,
        username: str = "",
        password: str = "",
    ) -> None:
        self._url = (url or os.environ.get("PB_URL", "http://localhost:8090")).rstrip("/")
        self._username = username or os.environ.get("PB_USERNAME", "")
        self._password = password or os.environ.get("PB_PASSWORD", "")
        self._token: str | None = None
        self._token_expiry: float = 0

    # ── Auth ──────────────────────────────────────────────────────────────

    def _authenticate(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        if not self._username or not self._password:
            self._token = None
            return ""
        try:
            resp = requests.post(
                f"{self._url}/api/admins/auth-with-password",
                json={"identity": self._username, "password": self._password},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("token", "")
            self._token_expiry = time.time() + 3600
            return self._token
        except Exception:
            self._token = None
            return ""

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        token = self._authenticate()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _post(self, collection: str, data: dict[str, Any]) -> bool:
        try:
            resp = requests.post(
                f"{self._url}/api/collections/{collection}/records",
                json=data,
                headers=self._headers(),
                timeout=5,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def _patch(self, collection: str, record_id: str, data: dict[str, Any]) -> bool:
        try:
            resp = requests.patch(
                f"{self._url}/api/collections/{collection}/records/{record_id}",
                json=data,
                headers=self._headers(),
                timeout=5,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def _find_record(self, collection: str, filter_expr: str) -> dict[str, Any] | None:
        try:
            resp = requests.get(
                f"{self._url}/api/collections/{collection}/records",
                params={"filter": filter_expr, "perPage": 1},
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                return items[0] if items else None
        except Exception:
            pass
        return None

    # ── Public API ────────────────────────────────────────────────────────

    def create_test_run(self, record: TestRunRecord) -> bool:
        return self._post(
            "test_runs",
            {
                "run_id": record.run_id,
                "test_bench_id": record.test_bench_id,
                "source": record.source,
                "ip_address": record.ip_address,
                "status": record.status,
                "test_code_commit": record.test_code_commit,
                "config_commit": record.config_commit,
                "gitlab_pipeline_id": record.gitlab_pipeline_id,
                "gitlab_job_id": record.gitlab_job_id,
                "tests": json.dumps(record.tests),
                "started_at": record.started_at or _utc_now(),
            },
        )

    def update_test_run(self, run_id: str, status: str) -> bool:
        existing = self._find_record("test_runs", f"(run_id='{run_id}')")
        if existing:
            return self._patch(
                "test_runs",
                existing["id"],
                {"status": status, "completed_at": _utc_now()},
            )
        return False

    def add_test_result(self, record: TestResultRecord) -> bool:
        return self._post(
            "checkpoints",
            {
                "run_id": record.run_id,
                "test": record.test_id,
                "test_version": record.test_version,
                "module_instance_id": record.module_instance_id,
                "module_code": record.module_code,
                "product_key": record.product_key,
                "channel": record.channel_id or "",
                "verdict": record.verdict,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "duration_ms": record.duration_ms,
                "failure_reason": record.failure_reason,
                "exception_type": record.exception_type,
                "stack_trace": record.stack_trace[:2000] if record.stack_trace else "",
                "timestamp": _utc_now(),
            },
        )

    def add_measurement(self, record: MeasurementRecord) -> bool:
        # Fallback: store in system_logs until a dedicated measurements collection exists
        return self._post(
            "system_logs",
            {
                "run_id": record.run_id,
                "level": "measurement",
                "message": record.name,
                "details": json.dumps(
                    {
                        "value": record.value,
                        "unit": record.unit,
                        "limit_lower": record.limit_lower,
                        "limit_upper": record.limit_upper,
                    }
                ),
                "timestamp": record.timestamp or _utc_now(),
            },
        )

    def add_log_event(self, record: LogEventRecord) -> bool:
        return self._post(
            "system_logs",
            {
                "run_id": record.run_id,
                "level": record.level,
                "message": record.message,
                "details": json.dumps(
                    {"event_type": record.event_type, **record.details}, default=str
                ),
                "timestamp": record.timestamp or _utc_now(),
            },
        )

    def get_run_history(
        self, bench_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        try:
            params: dict[str, Any] = {"sort": "-created", "perPage": limit}
            if bench_id:
                params["filter"] = f"(test_bench_id='{bench_id}')"
            resp = requests.get(
                f"{self._url}/api/collections/test_runs/records",
                params=params,
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json().get("items", [])
        except Exception:
            pass
        return []

    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        run = self._find_record("test_runs", f"(run_id='{run_id}')")
        if run is None:
            return None
        # Fetch related checkpoints
        try:
            cp_resp = requests.get(
                f"{self._url}/api/collections/checkpoints/records",
                params={
                    "filter": f"(run_id='{run_id}')",
                    "sort": "created",
                    "perPage": 500,
                },
                headers=self._headers(),
                timeout=5,
            )
            if cp_resp.status_code == 200:
                run["checkpoints"] = cp_resp.json().get("items", [])
        except Exception:
            run["checkpoints"] = []
        return run


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _utc_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
