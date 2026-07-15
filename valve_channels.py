"""Valve slot/channel arithmetic.

Product-specific channel counts live in ``BenchConfig.module_types``. Keeping
this module data-free prevents the API and React client from maintaining a
second compatibility table keyed by display name.
"""

from __future__ import annotations


def valve_slot_to_channels(valve_index: int, cpv: int = 2) -> list[int]:
    """Return hardware channel indices for one zero-based valve slot."""
    if valve_index < 0:
        raise ValueError("valve_index must be non-negative")
    if cpv < 1:
        raise ValueError("channels_per_valve must be at least 1")
    base = valve_index * cpv
    return list(range(base, base + cpv))


def expand_valve_indices(valve_indices: list[int], cpv: int) -> list[int]:
    """Flatten valve slots into hardware channel indices."""
    channels: list[int] = []
    for valve_index in valve_indices:
        channels.extend(valve_slot_to_channels(valve_index, cpv))
    return channels
