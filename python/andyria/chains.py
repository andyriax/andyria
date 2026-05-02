"""Chain definitions and registry for Andyria multi-agent pipelines.

A Chain is an ordered sequence of agent IDs.  When run, each agent's output
is piped as the next agent's input, forming a sequential pipeline.
"""

from __future__ import annotations

import time
from typing import List, Optional

from .memory import ContentAddressedMemory
from .models import ChainCreateRequest, ChainDefinition


class ChainRegistry:
    """Persists and retrieves ChainDefinitions in ContentAddressedMemory."""

    _NS = "chains"

    def __init__(self, memory: ContentAddressedMemory) -> None:
        self._memory = memory

    def list(self) -> List[ChainDefinition]:
        chains: List[ChainDefinition] = []
        for key in self._memory.list_keys(self._NS):
            raw = self._memory.get_by_key(self._NS, key)
            if raw is None:
                continue
            try:
                chain = ChainDefinition.model_validate_json(raw)
                if chain.active:
                    chains.append(chain)
            except Exception:
                pass
        chains.sort(key=lambda c: c.created_at)
        return chains

    def get(self, chain_id: str) -> Optional[ChainDefinition]:
        raw = self._memory.get_by_key(self._NS, chain_id)
        if raw is None:
            return None
        try:
            return ChainDefinition.model_validate_json(raw)
        except Exception:
            return None

    def create(self, request: ChainCreateRequest) -> ChainDefinition:
        now = int(time.time_ns())
        chain_id = f"chain-{now % (10 ** 12):012d}"
        chain = ChainDefinition(
            chain_id=chain_id,
            name=request.name,
            agent_ids=list(request.agent_ids),
            created_at=now,
        )
        self._save(chain)
        return chain

    def delete(self, chain_id: str) -> Optional[ChainDefinition]:
        chain = self.get(chain_id)
        if chain is None:
            return None
        chain = chain.model_copy(update={"active": False})
        self._save(chain)
        return chain

    def _save(self, chain: ChainDefinition) -> None:
        serialized = chain.model_dump_json().encode()
        content_hash = self._memory.put(serialized)
        self._memory.bind(self._NS, chain.chain_id, content_hash)
