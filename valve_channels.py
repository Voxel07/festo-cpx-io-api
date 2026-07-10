"""
Valve channel mapping — defines how many hardware channels correspond to each
valve slot per product family.  All VABX valve bodies have the same physical
layout (8 channels for 4 valves = 2 channels per valve) except for VEAM which
uses monostable valves (1 channel per valve).

Shared by backend API endpoints and test modules.
"""
from __future__ import annotations

import fnmatch

# ── Valve channel config per product-family pattern ───────────────────────────

# Patterns are matched via fnmatch against the module *display_name* (e.g.
# "VABX-A-S-BV-V4A", "VABX-A-VE-S").  The first matching pattern wins.
_VALVE_CHANNEL_CONFIG: list[tuple[str, int]] = [
    # V4A / V4B / V4C / BV-S variants: 4 bistable valves, 2 coils each → 8 channels
    ("VABX-A-S-BV-V4*", 2),
    ("VABX-A-S-BV-*",   2),   # generic BV-S catch-all
    ("VABX-A-BV-S-*",    2),   # BV-S without adaptor prefix
    ("VABX-A-BV-*",      2),   # plain BV variants

    # VEAM (monostable): 1 channel per valve
    ("VABX-A-VE-S*",     1),
    ("VABX-A-VE-*",      1),

    # VP (proportional): 2 channels per valve
    ("VABX-A-VP-*",      2),

    # MPAL valves (configurable, assuming bistable default: 2 channels)
    ("VMPAL-*",          2),
]

# Default for any VABX body not matched above
_VALVE_DEFAULT_CPV = 2


def channels_per_valve(module_name: str) -> int:
    """Return the number of hardware channels per valve slot for *module_name*.

    >>> channels_per_valve("VABX-A-S-BV-V4A")
    2
    >>> channels_per_valve("VABX-A-VE-S")
    1
    """
    for pattern, cpv in _VALVE_CHANNEL_CONFIG:
        if fnmatch.fnmatch(module_name, pattern):
            return cpv
    return _VALVE_DEFAULT_CPV


def valve_slot_to_channels(valve_index: int, cpv: int | None = None, module_name: str = "") -> list[int]:
    """Return the hardware channel indices for a given valve slot (0-based).

    >>> valve_slot_to_channels(0, cpv=2)
    [0, 1]
    >>> valve_slot_to_channels(3, cpv=2)
    [6, 7]
    """
    if cpv is None:
        cpv = channels_per_valve(module_name)
    base = valve_index * cpv
    return list(range(base, base + cpv))


def expand_valve_indices(valve_indices: list[int], module_name: str) -> list[int]:
    """Flatten a list of valve slot indices into hardware channel indices.

    >>> expand_valve_indices([0, 3], "VABX-A-S-BV-V4A")
    [0, 1, 6, 7]
    """
    cpv = channels_per_valve(module_name)
    channels: list[int] = []
    for vi in valve_indices:
        channels.extend(valve_slot_to_channels(vi, cpv))
    return channels
