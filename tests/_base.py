"""Shared helpers and types for all CPX-AP test modules."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Callable

from cpx_io.cpx_system.cpx_ap.ap_parameter import Parameter  # type: ignore[import-untyped]

# ── Types ─────────────────────────────────────────────────────────────────────

# Signature: (level: str, message: str) -> None
LogFn = Callable[[str, str], None]


def noop_log(level: str, msg: str) -> None:  # noqa: ARG001
    """Default no-op log — used when no callback is supplied."""


# ── Compatibility helpers ─────────────────────────────────────────────────────


def load_compatibility(compat_path: str = "test_compatibility.json") -> dict:
    """Load the module-test compatibility matrix."""
    path = Path(compat_path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _matches_any_pattern(name: str, patterns: list[str]) -> bool:
    """Return True if *name* matches any fnmatch pattern."""
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def get_compatible_tests(module_name: str, compat: dict | None = None) -> set[str]:
    """Return the set of test IDs that *module_name* is compatible with."""
    try:
        from resolver import load_all_test_definitions
        raw_defs = load_all_test_definitions()
        tests = set()
        for d in raw_defs:
            patterns = d.get("compatible_modules", [])
            test_id = d.get("test_id")
            if any(fnmatch.fnmatch(module_name, p) for p in patterns):
                tests.add(test_id)
        if tests:
            return tests
    except Exception:
        pass

    if compat is None:
        compat = load_compatibility()
    tests: set[str] = set()
    for category in compat.get("compatibility", {}).values():
        if _matches_any_pattern(module_name, category.get("module_patterns", [])):
            tests.update(category.get("tests", []))
    return tests


def is_module_compatible(
    module_name: str, test_id: str, compat: dict | None = None,
) -> bool:
    """Return True if *module_name* matches a pattern in a category that lists *test_id*."""
    return test_id in get_compatible_tests(module_name, compat)


# ── Parameter helpers ─────────────────────────────────────────────────────────

# Parameter cache: (param_id, data_type) → Parameter instance
_param_cache: dict[tuple[int, str], Parameter] = {}


def make_param(parameter_id: int, data_type: str = "UINT16") -> Parameter:
    """Construct (or reuse) a minimal Parameter object.

    Cached per (parameter_id, data_type) to avoid repeated allocations.
    """
    key = (parameter_id, data_type)
    if key not in _param_cache:
        _param_cache[key] = Parameter(
            parameter_id=parameter_id,
            parameter_instances={},
            is_writable=True,
            array_size=1,
            data_type=data_type,
            default_value=0,
            description="",
            name="",
        )
    return _param_cache[key]


# ── Connection helpers ────────────────────────────────────────────────────────


def load_connections(connections_path: str = "connections.jsonc") -> list[dict]:
    """Load I/O connection entries from a JSONC file."""
    path = Path(connections_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("connections", [])


def is_valve_terminal(mod: Any) -> bool:
    """Return True if *mod* is a VABX valve terminal.

    Works with both festo-cpx-io ApModule objects and hal.ModuleInfo.
    """
    name = getattr(mod, "name", "") or getattr(mod, "Name", "")
    return name.upper().startswith("VABX")


def channel_index_from_port(port: str) -> int:
    """Convert a port label like 'X0', 'X3' to a 0-based channel index."""
    return int(port.lstrip("X"))
