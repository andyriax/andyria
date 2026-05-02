"""Persistent tab projections for UI views over shared agent state."""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from .memory import ContentAddressedMemory
from .models import TabCreateRequest, TabProjection, TabUpdateRequest


class TabProjectionStore:
    """CRUD service for tab projections backed by content-addressed memory."""

    _TABS_NS = "tabs"

    def __init__(self, memory: ContentAddressedMemory) -> None:
        self._memory = memory

    def list(self) -> List[TabProjection]:
        items: List[TabProjection] = []
        for key in self._memory.list_keys(self._TABS_NS):
            raw = self._memory.get_by_key(self._TABS_NS, key)
            if raw is None:
                continue
            try:
                items.append(TabProjection.model_validate_json(raw))
            except Exception:
                continue
        items.sort(key=lambda t: t.created_at)
        return items

    def get(self, tab_id: str) -> Optional[TabProjection]:
        raw = self._memory.get_by_key(self._TABS_NS, tab_id)
        if raw is None:
            return None
        try:
            return TabProjection.model_validate_json(raw)
        except Exception:
            return None

    def create(self, request: TabCreateRequest, agent_id: str) -> TabProjection:
        tab = TabProjection(
            tab_id=f"tab-{uuid.uuid4().hex[:12]}",
            agent_id=request.agent_id or agent_id,
            viewport_mode=request.viewport_mode,
            created_at=int(time.time_ns()),
        )
        self._save(tab)
        return tab

    def update(self, tab_id: str, request: TabUpdateRequest) -> Optional[TabProjection]:
        current = self.get(tab_id)
        if current is None:
            return None

        patch = request.model_dump(exclude_none=True)
        payload = current.model_dump()
        payload.update(patch)
        updated = TabProjection.model_validate(payload)
        self._save(updated)
        return updated

    def delete(self, tab_id: str) -> Optional[TabProjection]:
        current = self.get(tab_id)
        if current is None:
            return None
        self._memory.delete_binding(self._TABS_NS, tab_id)
        return current

    def _save(self, tab: TabProjection) -> None:
        content_hash = self._memory.put(tab.model_dump_json().encode())
        self._memory.bind(self._TABS_NS, tab.tab_id, content_hash)
