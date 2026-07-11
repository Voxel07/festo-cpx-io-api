"""Pytest entry point for resolved hardware test plans.

The resolver remains the single source of truth: CI executes exactly the
instances selected from the BenchConfig rather than maintaining a second list
of hard-coded test calls.
"""
from __future__ import annotations

import pytest


def test_resolved_plan(resolved_plan, bench_config_path, hw):
    if not resolved_plan.instances:
        pytest.skip("No compatible test instances were resolved for this bench")

    from api import _run_single_test_hw

    config_path = bench_config_path or "data/bench_config.json"
    failures: list[str] = []
    for instance in resolved_plan.instances:
        result = _run_single_test_hw(hw, instance, config_path, lambda _level, _message: None)
        if result.get("passed") is not True:
            failures.append(
                f"{instance.unique_id}: {result.get('error') or result.get('results') or result}"
            )

    if failures:
        pytest.fail("Resolved test failures:\n" + "\n".join(failures))
