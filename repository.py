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

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


def resolve_pocketbase_url() -> str:
    """Return the server-side PocketBase URL used by every API component."""
    return (
        os.environ.get("PB_URL")
        or os.environ.get("POCKETBASE_URL")
        or "http://localhost:8090"
    ).rstrip("/")


def resolve_pocketbase_public_url() -> str:
    """Return the browser-reachable PocketBase URL used for realtime SSE."""
    return os.environ.get("POCKETBASE_PUBLIC_URL", resolve_pocketbase_url()).rstrip("/")

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
    resolved_plan_id: str = ""
    schema_version: str = ""
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
    resolved_instance_id: str = ""
    module_instance_id: str = ""
    module_code: int = 0
    product_key: str = ""
    channel_id: str | None = None
    channel_mode: str | None = None
    wiring_id: str | None = None
    verdict: str = "unknown"  # passed, failed, error, skipped
    start_time: str = ""
    end_time: str = ""
    duration_ms: float = 0
    failure_reason: str = ""
    exception_type: str = ""
    stack_trace: str = ""
    raw_log_ref: str = ""
    artifact_ref: str = ""


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
    def update_test_run(
        self,
        run_id: str,
        status: str,
        *,
        results: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> bool:
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


# ── Collection names — must match pocketbase_schema.json ────────────────────
COLL_TEST_RUNS = "festo_test_runs"
COLL_TEST_RESULTS = "festo_test_results"
COLL_SYSTEM_LOGS = "festo_system_logs"
COLL_MEASUREMENTS = "festo_measurements"
COLL_PLANS = "festo_resolved_plans"
COLL_MODULE_SNAPSHOTS = "festo_module_snapshots"
COLL_WIRING_SNAPSHOTS = "festo_wiring_snapshots"
COLL_ARTIFACTS = "festo_artifacts"


class PocketBaseRepository(ResultRepository):
    """PocketBase-backed repository.

    Collections used (must match ``pocketbase_schema.json``):
    - ``festo_test_runs``     — top-level run records
    - ``festo_test_results``  — per-test-instance results
    - ``festo_system_logs``   — structured log events
    - ``festo_measurements``  — structured measurement data
    """

    def __init__(
        self,
        url: str | None = None,
        username: str = "",
        password: str = "",
        auth_collection: str = "",
    ) -> None:
        self._url = (url or resolve_pocketbase_url()).rstrip("/")
        self._username = username or os.environ.get("PB_USERNAME", "")
        self._password = password or os.environ.get("PB_PASSWORD", "")
        self._auth_collection = (
            auth_collection or os.environ.get("PB_AUTH_COLLECTION", "users")
        ).strip()
        self._token: str | None = os.environ.get("POCKETBASE_TOKEN") or None
        self._token_expiry: float = 0
        self._auth_lock = threading.Lock()
        self._request_state = threading.local()
        self._session = requests.Session()
        self._timeout = (
            float(os.environ.get("PB_CONNECT_TIMEOUT_S", "0.75")),
            float(os.environ.get("PB_READ_TIMEOUT_S", "2.0")),
        )

    # ── Auth ──────────────────────────────────────────────────────────────

    def _authenticate(self) -> str:
        configured_token = os.environ.get("POCKETBASE_TOKEN", "")
        if configured_token:
            self._token = configured_token
            self._token_expiry = float("inf")
            self._request_state.auth_error = ""
            return configured_token
        with self._auth_lock:
            if self._token and time.time() < self._token_expiry - 60:
                self._request_state.auth_error = ""
                return self._token
            if not self._username or not self._password:
                self._token = None
                self._request_state.auth_error = ""
                return ""
            try:
                resp = self._session.post(
                    f"{self._url}/api/collections/{self._auth_collection}/auth-with-password",
                    json={"identity": self._username, "password": self._password},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                self._token = data.get("token", "")
                self._token_expiry = time.time() + 3600
                self._request_state.auth_error = ""
                return self._token
            except Exception as exc:
                self._token = None
                self._request_state.auth_error = self._format_exception(exc)
                return ""

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        token = self._authenticate()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _post(self, collection: str, data: dict[str, Any]) -> bool:
        try:
            resp = self._session.post(
                f"{self._url}/api/collections/{collection}/records",
                json=data,
                headers=self._headers(),
                timeout=self._timeout,
            )
            return self._record_response("POST", collection, resp, (200, 201))
        except Exception as exc:
            self._record_exception("POST", collection, exc)
            return False

    def _patch(self, collection: str, record_id: str, data: dict[str, Any]) -> bool:
        try:
            resp = self._session.patch(
                f"{self._url}/api/collections/{collection}/records/{record_id}",
                json=data,
                headers=self._headers(),
                timeout=self._timeout,
            )
            return self._record_response("PATCH", collection, resp, (200, 201))
        except Exception as exc:
            self._record_exception("PATCH", collection, exc)
            return False

    @staticmethod
    def _format_exception(exc: BaseException) -> str:
        response = getattr(exc, "response", None)
        if response is not None:
            body = (getattr(response, "text", "") or "").strip().replace("\n", " ")[:500]
            return f"HTTP {response.status_code}: {body or response.reason}"
        return f"{type(exc).__name__}: {exc}"

    def _record_response(
        self,
        method: str,
        collection: str,
        response: requests.Response,
        success_codes: tuple[int, ...],
    ) -> bool:
        if response.status_code in success_codes:
            self._request_state.last_error = ""
            self._request_state.auth_error = ""
            return True
        body = (response.text or "").strip().replace("\n", " ")[:500]
        auth_error = getattr(self._request_state, "auth_error", "")
        detail = f"{method} {collection}: HTTP {response.status_code}: {body or response.reason}"
        if auth_error:
            detail = f"{detail}; normal-user auth failed: {auth_error}"
        self._request_state.last_error = detail
        logger.warning("PocketBase request failed: %s", detail)
        return False

    def _record_exception(self, method: str, collection: str, exc: BaseException) -> None:
        detail = f"{method} {collection}: {self._format_exception(exc)}"
        auth_error = getattr(self._request_state, "auth_error", "")
        if auth_error:
            detail = f"{detail}; normal-user auth failed: {auth_error}"
        self._request_state.last_error = detail
        logger.warning("PocketBase request failed: %s", detail)

    @property
    def last_error(self) -> str:
        """Diagnostic for the last failed request in the current worker thread."""
        return getattr(self._request_state, "last_error", "")

    def _find_record(self, collection: str, filter_expr: str) -> dict[str, Any] | None:
        try:
            resp = self._session.get(
                f"{self._url}/api/collections/{collection}/records",
                params={"filter": filter_expr, "perPage": 1},
                headers=self._headers(),
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                self._record_response("GET", collection, resp, (200,))
                items = resp.json().get("items", [])
                return items[0] if items else None
            self._record_response("GET", collection, resp, (200,))
        except Exception as exc:
            self._record_exception("GET", collection, exc)
        return None

    # ── Public API ────────────────────────────────────────────────────────

    def create_test_run(self, record: TestRunRecord) -> bool:
        return self._post(
            COLL_TEST_RUNS,
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
                "resolved_plan_id": record.resolved_plan_id,
                "schema_version": record.schema_version,
                "tests": record.tests,
                "started_at": record.started_at or _utc_now(),
            },
        )

    def update_test_run(
        self,
        run_id: str,
        status: str,
        *,
        results: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> bool:
        existing = self._find_record(COLL_TEST_RUNS, f"(run_id='{run_id}')")
        if existing:
            return self._patch(
                COLL_TEST_RUNS,
                existing["id"],
                {
                    "status": status,
                    "completed_at": _utc_now(),
                    **({"results": results} if results is not None else {}),
                    **({"error": error} if error else {}),
                },
            )
        if not self.last_error:
            self._request_state.last_error = (
                f"GET {COLL_TEST_RUNS}: no record found for run_id={run_id}"
            )
        return False

    def add_test_result(self, record: TestResultRecord) -> bool:
        return self._post(
            COLL_TEST_RESULTS,
            {
                "run_id": record.run_id,
                "test_id": record.test_id,
                "resolved_instance_id": record.resolved_instance_id,
                "test_name": record.test_name,
                "test_version": record.test_version,
                "module_instance_id": record.module_instance_id,
                "module_code": record.module_code,
                "product_key": record.product_key,
                "channel_id": record.channel_id or "",
                "channel_mode": record.channel_mode or "",
                "wiring_id": record.wiring_id or "",
                "verdict": record.verdict,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "duration_ms": record.duration_ms,
                "failure_reason": record.failure_reason,
                "exception_type": record.exception_type,
                "stack_trace": record.stack_trace[:2000] if record.stack_trace else "",
                "raw_log_ref": record.raw_log_ref,
                "artifact_ref": record.artifact_ref,
                "timestamp": _utc_now(),
            },
        )

    def add_measurement(self, record: MeasurementRecord) -> bool:
        return self._post(
            COLL_MEASUREMENTS,
            {
                "run_id": record.run_id,
                "test_result_id": record.test_result_id,
                "name": record.name,
                "value": record.value,
                "unit": record.unit,
                "limit_lower": record.limit_lower,
                "limit_upper": record.limit_upper,
                "timestamp": record.timestamp or _utc_now(),
            },
        )

    def add_log_event(self, record: LogEventRecord) -> bool:
        return self._post(
            COLL_SYSTEM_LOGS,
            {
                "run_id": record.run_id,
                "level": record.level,
                "message": record.message,
                "event_type": record.event_type,
                "details": {"event_type": record.event_type, **record.details},
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
            resp = self._session.get(
                f"{self._url}/api/collections/{COLL_TEST_RUNS}/records",
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                return resp.json().get("items", [])
        except Exception:
            pass
        return []

    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        run = self._find_record(COLL_TEST_RUNS, f"(run_id='{run_id}')")
        if run is None:
            return None
        # Fetch related normalized test results.
        try:
            cp_resp = self._session.get(
                f"{self._url}/api/collections/{COLL_TEST_RESULTS}/records",
                params={
                    "filter": f"(run_id='{run_id}')",
                    "sort": "created",
                    "perPage": 500,
                },
                headers=self._headers(),
                timeout=self._timeout,
            )
            if cp_resp.status_code == 200:
                run["test_results"] = cp_resp.json().get("items", [])
                run["checkpoints"] = run["test_results"]
        except Exception:
            run["checkpoints"] = []
        return run

    def save_execution_context(self, run_id: str, plan: Any, config: Any) -> bool:
        """Persist the plan and immutable module/wiring snapshots for a run."""
        plan_payload = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
        ok = self._post(COLL_PLANS, {
            "plan_id": plan_payload.get("plan_id", ""),
            "run_id": run_id,
            "test_bench_id": plan_payload.get("test_bench_id", ""),
            "created_at": plan_payload.get("created_at", _utc_now()),
            "plan": plan_payload,
        })
        for module in config.module_instances:
            ok = self._post(COLL_MODULE_SNAPSHOTS, {
                "run_id": run_id,
                "instance_id": module.instance_id,
                "module_code": module.module_code,
                "product_key": module.product_key or "",
                "firmware_version": module.firmware_version or "",
                "serial_number": module.serial_number or "",
                "snapshot": module.model_dump(mode="json"),
            }) and ok
        for wire in config.wiring:
            ok = self._post(COLL_WIRING_SNAPSHOTS, {
                "run_id": run_id,
                "wiring_id": wire.id,
                "snapshot": wire.model_dump(mode="json"),
            }) and ok
        return ok

    def delete_run(self, run_id: str) -> bool:
        existing = self._find_record(COLL_TEST_RUNS, f"(run_id='{run_id}')")
        if not existing:
            return False
        try:
            response = self._session.delete(
                f"{self._url}/api/collections/{COLL_TEST_RUNS}/records/{existing['id']}",
                headers=self._headers(),
                timeout=self._timeout,
            )
            return response.status_code in (200, 204)
        except Exception:
            return False

    def clear_history(self) -> bool:
        runs = self.get_run_history(limit=500)
        return all(self.delete_run(run.get("run_id", "")) for run in runs)

    def recover_stale_runs(self, max_age_s: float = 3600.0) -> int:
        """Mark abandoned running records as interrupted after API restarts."""
        try:
            response = self._session.get(
                f"{self._url}/api/collections/{COLL_TEST_RUNS}/records",
                params={"filter": "(status='running')", "perPage": 200},
                headers=self._headers(),
                timeout=self._timeout,
            )
            if response.status_code != 200:
                return 0
            recovered = 0
            now = datetime.now(timezone.utc)
            for item in response.json().get("items", []):
                raw = item.get("started_at") or item.get("created")
                try:
                    started = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    started = now
                if (now - started).total_seconds() >= max_age_s:
                    if self._patch(COLL_TEST_RUNS, item["id"], {
                        "status": "interrupted",
                        "error": "Recovered as interrupted after API restart",
                        "completed_at": _utc_now(),
                    }):
                        recovered += 1
            return recovered
        except Exception:
            return 0


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _utc_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ResultStore:
    """Application-facing persistence service independent of PocketBase APIs."""

    def __init__(self, repository: ResultRepository | None = None) -> None:
        self.repository = repository or PocketBaseRepository()

    @property
    def last_error(self) -> str:
        """Return the repository diagnostic associated with the current worker."""
        return str(getattr(self.repository, "last_error", "") or "")

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
    ) -> bool:
        return self.repository.create_test_run(TestRunRecord(
            run_id=run_id,
            source=source,
            ip_address=ip_address,
            tests=tests,
            test_bench_id=test_bench_id,
            test_code_commit=test_code_commit,
            config_commit=config_commit,
            gitlab_pipeline_id=gitlab_pipeline_id,
            gitlab_job_id=gitlab_job_id,
            resolved_plan_id=resolved_plan_id,
            schema_version=schema_version,
            started_at=_utc_now(),
        ))

    def test_run_completed(
        self,
        run_id: str,
        results: list[dict[str, Any]],
        status: str = "completed",
        error: str | None = None,
    ) -> bool:
        return self.repository.update_test_run(run_id, status, results=results, error=error)

    def checkpoint(
        self, run_id: str, test: str, status: str, error: str | None = None,
    ) -> bool:
        return self.repository.add_log_event(LogEventRecord(
            run_id=run_id,
            level="error" if status == "failed" else "info",
            message=f"{test}: {status}",
            event_type="test_checkpoint",
            details={"test_id": test, "status": status, "error": error or ""},
        ))

    def save_execution_context(self, run_id: str, plan: Any, config: Any) -> bool:
        method = getattr(self.repository, "save_execution_context", None)
        return bool(method and method(run_id, plan, config))

    def add_test_result(self, record: TestResultRecord) -> bool:
        return self.repository.add_test_result(record)

    def log(
        self, run_id: str | None, level: str, message: str, details: dict | None = None,
    ) -> bool:
        return self.repository.add_log_event(LogEventRecord(
            run_id=run_id or "",
            level=level,
            message=message,
            event_type=(details or {}).get("event_type", "log"),
            details=details or {},
        ))

    def error(self, run_id: str | None, message: str, details: dict | None = None) -> bool:
        return self.log(run_id, "error", message, details)

    def info(self, run_id: str | None, message: str) -> bool:
        return self.log(run_id, "info", message)

    def get_run_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.get_run_history(limit=limit)

    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_run_detail(run_id)

    def delete_run(self, run_id: str) -> bool:
        method = getattr(self.repository, "delete_run", None)
        return bool(method and method(run_id))

    def clear_history(self) -> bool:
        method = getattr(self.repository, "clear_history", None)
        return bool(method and method())

    def recover_stale_runs(self, max_age_s: float = 3600.0) -> int:
        method = getattr(self.repository, "recover_stale_runs", None)
        return int(method(max_age_s) if method else 0)


result_store = ResultStore()


def pocketbase_api_context() -> tuple[str, dict[str, str]]:
    """Return the shared PocketBase endpoint and authenticated headers."""
    repository = result_store.repository
    if not isinstance(repository, PocketBaseRepository):
        raise RuntimeError("The configured repository is not PocketBase-backed")
    return repository._url, repository._headers()
