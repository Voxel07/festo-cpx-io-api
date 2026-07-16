from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from repository import PocketBaseRepository, TestRunRecord, resolve_pocketbase_url


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _next(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs):
        return self._next("POST", url, **kwargs)

    def get(self, url: str, **kwargs):
        return self._next("GET", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self._next("PATCH", url, **kwargs)


def response(status: int, body: str = "{}") -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode()
    result.reason = "test response"
    return result


class PocketBaseRepositoryTests(unittest.TestCase):
    def test_resolved_plan_schema_allows_same_plan_across_runs(self) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "pocketbase_schema.json").read_text(
                encoding="utf-8"
            )
        )
        collection = next(
            item for item in schema if item["name"] == "festo_resolved_plans"
        )

        self.assertIn(
            "CREATE INDEX `idx_plan_id` ON `festo_resolved_plans` (`plan_id`)",
            collection["indexes"],
        )
        self.assertIn(
            "CREATE UNIQUE INDEX `idx_plans_run_id` ON `festo_resolved_plans` (`run_id`)",
            collection["indexes"],
        )
        self.assertNotIn(
            "CREATE UNIQUE INDEX `idx_plan_id` ON `festo_resolved_plans` (`plan_id`)",
            collection["indexes"],
        )

    def test_resolve_url_accepts_long_environment_name(self) -> None:
        with patch.dict(
            os.environ,
            {"POCKETBASE_URL": "https://pb.example.test/"},
            clear=True,
        ):
            self.assertEqual(resolve_pocketbase_url(), "https://pb.example.test")

    def test_authenticates_against_normal_user_collection(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            repository = PocketBaseRepository(
                "https://pb.example.test",
                "operator@example.test",
                "secret",
                auth_collection="operators",
            )
            repository._session = FakeSession([
                response(200, '{"token":"normal-user-token"}'),
                response(201),
            ])

            self.assertTrue(repository.create_test_run(TestRunRecord(run_id="run-1")))
            calls = repository._session.calls
            self.assertEqual(
                calls[0][1],
                "https://pb.example.test/api/collections/operators/auth-with-password",
            )
            self.assertEqual(calls[0][2]["json"], {
                "identity": "operator@example.test",
                "password": "secret",
            })
            self.assertEqual(
                calls[1][2]["headers"]["Authorization"],
                "Bearer normal-user-token",
            )

    def test_public_write_skips_authentication_without_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            repository = PocketBaseRepository("https://pb.example.test")
            repository._session = FakeSession([response(201)])

            self.assertTrue(
                repository.create_test_run(TestRunRecord(run_id="run-public"))
            )
            self.assertEqual(len(repository._session.calls), 1)
            self.assertNotIn(
                "Authorization",
                repository._session.calls[0][2]["headers"],
            )

    def test_failed_write_exposes_pocketbase_response(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            repository = PocketBaseRepository("https://pb.example.test")
            repository._session = FakeSession([
                response(400, '{"message":"validation failed"}'),
            ])

            self.assertFalse(
                repository.create_test_run(TestRunRecord(run_id="bad-run"))
            )
            self.assertIn("HTTP 400", repository.last_error)
            self.assertIn("validation failed", repository.last_error)

    def test_execution_context_stops_at_first_failure_and_preserves_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            repository = PocketBaseRepository("https://pb.example.test")
            repository._session = FakeSession([
                response(400, '{"message":"plan_id must be unique"}'),
                response(201),
            ])
            plan = {
                "plan_id": "stable-plan-id",
                "test_bench_id": "bench-1",
                "created_at": "2026-07-17T12:00:00Z",
            }
            config = SimpleNamespace(
                module_instances=[SimpleNamespace()],
                wiring=[],
            )

            self.assertFalse(repository.save_execution_context("run-2", plan, config))
            self.assertEqual(len(repository._session.calls), 1)
            self.assertIn("plan_id must be unique", repository.last_error)


if __name__ == "__main__":
    unittest.main()
