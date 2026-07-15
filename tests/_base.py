"""Shared helpers and types for all CPX-AP test modules."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TYPE_CHECKING

from cpx_io.cpx_system.cpx_ap.ap_parameter import Parameter  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from config_models import BenchConfig

# ── Types ─────────────────────────────────────────────────────────────────────

# Signature: (level: str, message: str) -> None
LogFn = Callable[[str, str], None]


def noop_log(level: str, msg: str) -> None:  # noqa: ARG001
    """Default no-op log — used when no callback is supplied."""


# ── Config loading ────────────────────────────────────────────────────────────

# Default path to the bench configuration file — each test knows this path.
DEFAULT_BENCH_CONFIG_PATH = "data/bench_config.json"


def load_bench_config(config_path: str | None = None) -> "BenchConfig | None":
    """Load the :class:`BenchConfig` from *config_path*.

    Args:
        config_path: Path to bench_config.json.  Defaults to
            ``data/bench_config.json``.

    Returns:
        The parsed BenchConfig, or ``None`` if the file does not exist
        or cannot be parsed.
    """
    from config_io import load_bench_config as load_validated_config

    path = Path(config_path or DEFAULT_BENCH_CONFIG_PATH)
    if not path.exists():
        return None
    try:
        return load_validated_config(path)
    except Exception:
        return None


# ── Compatibility helpers ─────────────────────────────────────────────────────


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
    from config_io import parse_jsonc

    return parse_jsonc(path.read_text(encoding="utf-8")).get("connections", [])


def channel_index_from_port(port: str) -> int:
    """Convert a port label like 'X0', 'X3' to a 0-based channel index."""
    return int(port.lstrip("X"))
