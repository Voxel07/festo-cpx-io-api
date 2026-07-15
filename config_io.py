"""Canonical JSONC loading and semantic validation for bench configurations.

Every API endpoint and runtime test module must use this module.  Keeping the
parser here prevents the previously divergent strict-JSON and regex-based
implementations from accepting different configuration files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config_models import BenchConfig


class ConfigLoadError(ValueError):
    """Raised when a bench configuration cannot be parsed or validated."""


def _strip_jsonc_comments(source: str) -> str:
    """Remove JSONC comments without touching comment markers inside strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            output.extend((" ", " "))
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            output.extend((" ", " "))
            index += 2
            closed = False
            while index < len(source):
                if source[index] == "*" and index + 1 < len(source) and source[index + 1] == "/":
                    output.extend((" ", " "))
                    index += 2
                    closed = True
                    break
                output.append("\n" if source[index] == "\n" else " ")
                index += 1
            if not closed:
                raise ConfigLoadError("Unterminated JSONC block comment")
            continue
        output.append(char)
        index += 1
    if in_string:
        raise ConfigLoadError("Unterminated JSON string")
    return "".join(output)


def _remove_trailing_commas(source: str) -> str:
    """Remove JSONC trailing commas before a closing object or array token."""
    chars = list(source)
    in_string = False
    escaped = False
    for index, char in enumerate(chars):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char not in "}]":
            continue
        previous = index - 1
        while previous >= 0 and chars[previous].isspace():
            previous -= 1
        if previous >= 0 and chars[previous] == ",":
            chars[previous] = " "
    return "".join(chars)


def parse_jsonc(source: str) -> dict[str, Any]:
    """Parse a JSONC document and return its root object."""
    try:
        value = json.loads(_remove_trailing_commas(_strip_jsonc_comments(source)))
    except (json.JSONDecodeError, ConfigLoadError) as exc:
        raise ConfigLoadError(f"Invalid JSONC: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigLoadError("Bench configuration root must be a JSON object")
    return value


def _validate_assets(config: BenchConfig, base_dir: Path) -> None:
    missing: list[str] = []
    for type_ref, module_type in config.module_types.items():
        if module_type.image_asset:
            asset = Path(module_type.image_asset)
            if not asset.is_absolute():
                asset = base_dir / asset
            if not asset.is_file():
                missing.append(f"module_types.{type_ref}.image_asset={module_type.image_asset!r}")
    for position in config.ui_metadata.module_positions:
        if position.image_path:
            asset = Path(position.image_path)
            if not asset.is_absolute():
                asset = base_dir / asset
            if not asset.is_file():
                missing.append(
                    f"ui_metadata.module_positions[{position.instance_id}].image_path={position.image_path!r}"
                )
    if missing:
        raise ConfigLoadError("Missing UI image asset(s): " + ", ".join(missing))


def load_bench_config(path: str | Path, *, validate_assets: bool = True) -> BenchConfig:
    """Load, parse, and validate a bench configuration from *path*."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ConfigLoadError(f"Configuration file not found: {resolved}")
    try:
        config = BenchConfig.model_validate(parse_jsonc(resolved.read_text(encoding="utf-8")))
    except ConfigLoadError:
        raise
    except Exception as exc:
        raise ConfigLoadError(f"Invalid bench configuration {resolved.name}: {exc}") from exc
    if validate_assets:
        _validate_assets(config, resolved.parent)
    return config

