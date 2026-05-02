"""Append-only NDJSON event store for Andyria ledger.

Events are stored as one JSON line per file (events.ndjson).
Event IDs are also written as sentinel files in an index/ directory for O(1) existence checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Event


class EventStore:
    """Append-only, content-addressed event ledger backed by NDJSON."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize store at {data_dir}/ledger/."""
        self.ledger_dir = Path(data_dir) / "ledger"
        self.log_path = self.ledger_dir / "events.ndjson"
        self.index_dir = self.ledger_dir / "index"

        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> bool:
        """Append event to log. Returns False if already present (idempotent)."""
        if self.contains(event.id):
            return False

        # Write event as JSON line
        line = event.model_dump_json()
        with open(self.log_path, "a") as f:
            f.write(line + "\n")

        # Write index sentinel
        sentinel = self.index_dir / event.id
        sentinel.write_text(event.id)

        return True

    def contains(self, event_id: str) -> bool:
        """Check if event with this ID is in the log (O(1) via index sentinel)."""
        return (self.index_dir / event_id).exists()

    def load_all(self) -> list[Event]:
        """Load all events from the log in append order."""
        from .models import Event

        if not self.log_path.exists():
            return []

        events = []
        with open(self.log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        event_data = json.loads(line)
                        events.append(Event(**event_data))
                    except (json.JSONDecodeError, TypeError):
                        # Skip malformed lines
                        pass
        return events

    def count(self) -> int:
        """Count of events in the log."""
        if not self.log_path.exists():
            return 0
        return sum(1 for line in open(self.log_path) if line.strip())
