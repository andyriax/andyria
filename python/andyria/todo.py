"""Agent-local TODO tool — mirrors hermes-agent's ephemeral task list.

A lightweight in-session task tracker that the agent can use to keep
tabs on multi-step work. Items persist to ``{data_dir}/todo.json`` so
they survive across short restarts, but are scoped to the current agent
and cleared when the agent calls ``todo clear``.

Usage (tool calls from within the agent loop)::

    todo = TodoStore(data_dir=Path("~/.andyria"))
    item_id = todo.add("Implement context compression")
    todo.update(item_id, status="in_progress")
    todo.update(item_id, text="Implement and test context compression")
    todo.done(item_id)
    todo.list()   # [{"id": ..., "text": ..., "status": "done", ...}]
    todo.clear()
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Literal, Optional

Status = Literal["todo", "in_progress", "done", "cancelled"]


@dataclass
class TodoItem:
    id: str
    text: str
    status: Status = "todo"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class TodoStore:
    """Persistent per-agent TODO list.

    Agent tool actions:
        * ``add(text) → item_id``
        * ``update(id, status=None, text=None) → bool``
        * ``done(id) → bool``
        * ``cancel(id) → bool``
        * ``remove(id) → bool``
        * ``list(status_filter=None) → List[dict]``
        * ``clear() → int``   — removes all done/cancelled items
        * ``as_system_block() → str``  — current todo list for system prompt
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "todo.json"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._items: List[TodoItem] = self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, text: str) -> str:
        """Add a new todo item. Returns the new item ID."""
        item = TodoItem(id=str(uuid.uuid4())[:8], text=text.strip())
        self._items.append(item)
        self._save()
        return item.id

    def update(
        self,
        item_id: str,
        *,
        status: Optional[Status] = None,
        text: Optional[str] = None,
    ) -> bool:
        """Update text and/or status of an item."""
        item = self._find(item_id)
        if item is None:
            return False
        if status is not None:
            item.status = status
        if text is not None:
            item.text = text.strip()
        item.updated_at = time.time()
        self._save()
        return True

    def done(self, item_id: str) -> bool:
        """Mark an item as done."""
        return self.update(item_id, status="done")

    def cancel(self, item_id: str) -> bool:
        """Mark an item as cancelled."""
        return self.update(item_id, status="cancelled")

    def remove(self, item_id: str) -> bool:
        """Permanently remove an item."""
        before = len(self._items)
        self._items = [i for i in self._items if i.id != item_id]
        if len(self._items) < before:
            self._save()
            return True
        return False

    def list(self, status_filter: Optional[Status] = None) -> List[dict]:
        """Return all items, optionally filtered by status."""
        items = self._items
        if status_filter:
            items = [i for i in items if i.status == status_filter]
        return [asdict(i) for i in items]

    def clear(self) -> int:
        """Remove all done and cancelled items. Returns count removed."""
        before = len(self._items)
        self._items = [i for i in self._items if i.status not in ("done", "cancelled")]
        removed = before - len(self._items)
        if removed:
            self._save()
        return removed

    def clear_all(self) -> int:
        """Remove every item."""
        count = len(self._items)
        self._items = []
        self._save()
        return count

    # ------------------------------------------------------------------
    # System prompt injection
    # ------------------------------------------------------------------

    def as_system_block(self) -> str:
        """Return pending todos as a compact block for the system prompt."""
        pending = [i for i in self._items if i.status not in ("done", "cancelled")]
        if not pending:
            return ""
        lines = ["## Current TODOs\n"]
        for item in pending:
            badge = {"todo": "☐", "in_progress": "⟳"}.get(item.status, "☐")
            lines.append(f"{badge} [{item.id}] {item.text}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find(self, item_id: str) -> Optional[TodoItem]:
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def _load(self) -> List[TodoItem]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return [TodoItem(**d) for d in raw]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    def _save(self) -> None:
        self._path.write_text(
            json.dumps([asdict(i) for i in self._items], indent=2, default=str),
            encoding="utf-8",
        )
