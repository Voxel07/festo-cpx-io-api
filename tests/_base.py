"""Shared helpers and types for all CPX-AP test modules."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

# Signature: (level: str, message: str) -> None
LogFn = Callable[[str, str], None]


def noop_log(level: str, msg: str) -> None:  # noqa: ARG001
    """Default no-op log — used when no callback is supplied."""


def load_connections(connections_path: str = "connections.jsonc") -> list[dict]:
    """Load I/O connection entries from a JSONC file."""
    path = Path(connections_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("connections", [])


def is_valve_terminal(mod) -> bool:
    """Return True if *mod* is a VABX valve terminal."""
    return mod.name.upper().startswith("VABX")
