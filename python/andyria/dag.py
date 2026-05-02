"""DAG utilities for Andyria — topological sort on event parent chains."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Event


def topological_sort(events: list[Event]) -> list[Event]:
    """
    Sort events by causal order using topological sort (Kahn's algorithm).

    Events whose parents haven't been seen fall back to timestamp order.
    This ensures causally consistent ordering even for cross-node events
    that arrive out of order.
    """
    if not events:
        return []

    # Build adjacency map: event_id → list of children
    event_map = {e.id: e for e in events}
    in_degree: dict[str, int] = defaultdict(int)
    adjacency: dict[str, list[str]] = defaultdict(list)

    # Count in-degrees and build edges
    for event in events:
        if event.id not in in_degree:
            in_degree[event.id] = 0

        for parent_id in event.parent_ids:
            if parent_id in event_map:
                adjacency[parent_id].append(event.id)
                in_degree[event.id] += 1

    # Kahn's algorithm: process nodes with zero in-degree first
    queue = [e.id for e in events if in_degree[e.id] == 0]
    result: list[str] = []

    while queue:
        # Sort queue by timestamp for deterministic order when there are ties
        queue.sort(key=lambda eid: event_map[eid].timestamp_ns)
        node = queue.pop(0)
        result.append(node)

        for child in adjacency[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # If result is missing events, it means there are cycles or orphaned events.
    # Append missing events sorted by timestamp (pragmatic fallback).
    if len(result) < len(events):
        missing = [e for e in events if e.id not in result]
        missing.sort(key=lambda e: e.timestamp_ns)
        result.extend(e.id for e in missing)

    return [event_map[eid] for eid in result]
