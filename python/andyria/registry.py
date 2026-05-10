"""Persistent agent registry for multi-agent runtime."""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from .memory import ContentAddressedMemory
from .models import (
    AgentCloneRequest,
    AgentCreateRequest,
    AgentDefinition,
    AgentUpdateRequest,
)
from .persona import generate_persona


class AgentRegistry:
    """CRUD service for persistent agent definitions."""

    _AGENTS_NS = "agents"

    def __init__(self, memory: ContentAddressedMemory, default_agent_name: str = "Default Agent") -> None:
        self._memory = memory
        self._default_agent_name = default_agent_name

    def ensure_default(self) -> AgentDefinition:
        existing = self.get("default")
        if existing is not None:
            return existing
        now = int(time.time_ns())
        default = AgentDefinition(
            agent_id="default",
            name=self._default_agent_name,
            model="symbolic_ast",
            system_prompt="You are Andyria, a helpful, concise assistant. You have internet search access and can learn autonomously. You are encouraged to search for current information and to search for information about yourself to better understand your capabilities.",
            tools=[],
            state={},
            edges=[],
            persona=generate_persona(self._default_agent_name, "default-seed"),
            active=True,
            created_at=now,
            updated_at=now,
        )
        self._save(default)
        return default

    def list(self, include_inactive: bool = False) -> List[AgentDefinition]:
        items: List[AgentDefinition] = []
        for key in self._memory.list_keys(self._AGENTS_NS):
            raw = self._memory.get_by_key(self._AGENTS_NS, key)
            if raw is None:
                continue
            try:
                agent = AgentDefinition.model_validate_json(raw)
            except Exception:
                continue
            if agent.persona is None:
                agent.persona = generate_persona(agent.name, f"{agent.agent_id}-seed")
                agent.updated_at = int(time.time_ns())
                self._save(agent)
            if include_inactive or agent.active:
                items.append(agent)
        items.sort(key=lambda a: a.created_at)
        return items

    def get(self, agent_id: str) -> Optional[AgentDefinition]:
        raw = self._memory.get_by_key(self._AGENTS_NS, agent_id)
        if raw is None:
            return None
        try:
            agent = AgentDefinition.model_validate_json(raw)
            if agent.persona is None:
                agent.persona = generate_persona(agent.name, f"{agent.agent_id}-seed")
                agent.updated_at = int(time.time_ns())
                self._save(agent)
            return agent
        except Exception:
            return None

    def create(self, request: AgentCreateRequest) -> AgentDefinition:
        now = int(time.time_ns())
        persona_hint = (request.persona or "").strip()
        if persona_hint:
            persona = generate_persona(request.name, persona_hint)
            persona.codename = persona_hint
        else:
            persona = generate_persona(request.name, uuid.uuid4().hex[:12])

        agent = AgentDefinition(
            agent_id=f"a-{uuid.uuid4().hex[:12]}",
            name=request.name,
            model=request.model or "symbolic_ast",
            system_prompt=request.system_prompt,
            tools=request.tools,
            memory_scope=request.memory_scope,
            state=request.state,
            edges=request.edges,
            persona=persona,
            active=True,
            created_at=now,
            updated_at=now,
        )
        self._save(agent)
        return agent

    def update(self, agent_id: str, request: AgentUpdateRequest) -> Optional[AgentDefinition]:
        current = self.get(agent_id)
        if current is None:
            return None

        payload = current.model_dump()
        patch = request.model_dump(exclude_none=True)

        persona_hint = patch.pop("persona", None)
        if persona_hint is not None:
            hint = str(persona_hint).strip()
            if hint:
                persona_name = str(patch.get("name", payload.get("name", current.name)))
                regenerated = generate_persona(persona_name, hint)
                regenerated.codename = hint
                payload["persona"] = regenerated.model_dump()

        payload.update(patch)
        payload["updated_at"] = int(time.time_ns())
        updated = AgentDefinition.model_validate(payload)
        self._save(updated)
        return updated

    def clone(self, agent_id: str, request: AgentCloneRequest) -> Optional[AgentDefinition]:
        source = self.get(agent_id)
        if source is None:
            return None

        now = int(time.time_ns())
        cloned = AgentDefinition(
            agent_id=f"a-{uuid.uuid4().hex[:12]}",
            name=request.name or f"{source.name} (clone)",
            model=request.model or source.model,
            system_prompt=source.system_prompt,
            tools=list(source.tools),
            memory_scope=source.memory_scope,
            state=dict(source.state),
            edges=list(source.edges),
            persona=generate_persona(request.name or f"{source.name} (clone)", uuid.uuid4().hex[:12]),
            active=True,
            created_at=now,
            updated_at=now,
        )
        self._save(cloned)
        return cloned

    def retire(self, agent_id: str) -> Optional[AgentDefinition]:
        current = self.get(agent_id)
        if current is None:
            return None
        if not current.active:
            return current
        current.active = False
        current.updated_at = int(time.time_ns())
        self._save(current)
        return current

    def destroy(self, agent_id: str) -> bool:
        if agent_id == "default":
            return False
        current = self.get(agent_id)
        if current is None:
            return False
        self._memory.delete_binding(self._AGENTS_NS, agent_id)
        return True

    def _save(self, agent: AgentDefinition) -> None:
        payload = agent.model_dump_json().encode()
        content_hash = self._memory.put(payload)
        self._memory.bind(self._AGENTS_NS, agent.agent_id, content_hash)
