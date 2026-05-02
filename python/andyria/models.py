"""Shared data models for the Andyria intelligence platform."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    LANGUAGE = "language"
    SYMBOLIC = "symbolic"
    TOOL = "tool"
    COMPOSITE = "composite"


class EventType(str, Enum):
    REQUEST = "request"
    PLAN = "plan"
    TASK_RESULT = "task_result"
    RESPONSE = "response"
    ENTROPY_BEACON = "entropy_beacon"
    NODE_IDENTITY = "node_identity"
    CHECKPOINT = "checkpoint"
    AGENT_CREATED = "agent_created"
    AGENT_UPDATED = "agent_updated"
    AGENT_CLONED = "agent_cloned"
    AGENT_RETIRED = "agent_retired"
    TAB_OPENED = "tab_opened"
    TAB_UPDATED = "tab_updated"
    TAB_CLOSED = "tab_closed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CHAIN_STARTED = "chain_started"
    CHAIN_STEP = "chain_step"
    CHAIN_COMPLETED = "chain_completed"
    CHAIN_FAILED = "chain_failed"
    ATM_STARTED = "atm_started"
    ATM_STEP = "atm_step"
    ATM_COMPLETE = "atm_complete"
    ATM_FAILED = "atm_failed"
    REFLECTION_STARTED = "reflection_started"
    REFLECTION_COMPLETE = "reflection_complete"
    AGENT_PERSONA_ASSIGNED = "agent_persona_assigned"
    AGENT_DEV_WORKSPACE_PREPARED = "agent_dev_workspace_prepared"
    DEMO_STARTED = "demo_started"
    DEMO_STOPPED = "demo_stopped"


class EntropyBeacon(BaseModel):
    """A signed, auditable entropy beacon sampled from physical hardware sources.

    Design invariant: event content hashes include ``id`` (not raw bytes),
    so events remain deterministically verifiable by any peer while still
    being anchored to physical-world randomness at the originating node.
    """

    id: str
    timestamp_ns: int
    sources: List[str]
    raw_entropy_hash: str  # BLAKE3 / SHA3-256 hex of mixed raw bytes
    nonce: str             # 32-byte whitened entropy, hex; usable as salt/nonce
    node_id: str
    signature: str         # Ed25519 hex signature over canonical JSON form


class NodeIdentity(BaseModel):
    """Cryptographic identity for an Andyria node."""

    node_id: str
    public_key: str        # Ed25519 public key, raw bytes hex
    created_at: int        # Unix nanoseconds
    deployment_class: str  # "edge" | "server" | "cluster"
    capabilities: List[str]


class Event(BaseModel):
    """A signed, immutable entry in the Andyria append-only event log."""

    id: str
    parent_ids: List[str]
    event_type: EventType
    payload_hash: str      # BLAKE3 / SHA3-256 hex of canonical payload bytes
    entropy_beacon_id: str # References a signed EntropyBeacon
    timestamp_ns: int
    node_id: str
    signature: str         # Ed25519 hex signature


class PeerStatus(BaseModel):
    """Runtime status of a peer in the mesh."""
    url: str
    node_id: Optional[str] = None
    last_seen_ns: int = 0
    events_synced: int = 0
    reachable: bool = False


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    task_type: TaskType
    priority: int = Field(default=5, ge=1, le=10)
    context: Dict[str, Any] = Field(default_factory=dict)
    parent_request_id: Optional[str] = None


class TaskResult(BaseModel):
    task_id: str
    output: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_used: str
    verified: bool = False
    event_id: str = ""
    tokens_used: Optional[int] = None


class SessionTurn(BaseModel):
    """One turn in a conversation session."""
    role: str          # "user" or "assistant"
    content: str
    model_used: str = "stub"
    confidence: float = 0.0
    timestamp_ns: int = 0


class SessionContext(BaseModel):
    """Rolling conversation context for a session."""
    session_id: str
    turns: List[SessionTurn] = Field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0


class AgentMemoryScope(str, Enum):
    ISOLATED = "isolated"
    SHARED = "shared"
    GLOBAL = "global"


class ViewportMode(str, Enum):
    CHAT = "chat"
    GRAPH = "graph"
    DEBUG = "debug"


class AgentPersona(BaseModel):
    seed: str
    codename: str
    archetype: str
    style: str
    mission: str
    quirks: List[str] = Field(default_factory=list)
    image_prompt: str = ""


class AgentDefinition(BaseModel):
    agent_id: str
    name: str
    model: str = "stub"
    system_prompt: str = ""
    tools: List[str] = Field(default_factory=list)
    memory_scope: AgentMemoryScope = AgentMemoryScope.ISOLATED
    state: Dict[str, Any] = Field(default_factory=dict)
    edges: List[str] = Field(default_factory=list)
    persona: Optional[AgentPersona] = None
    active: bool = True
    created_at: int = 0
    updated_at: int = 0


class AgentCreateRequest(BaseModel):
    name: str
    model: str = "stub"
    system_prompt: str = ""
    tools: List[str] = Field(default_factory=list)
    memory_scope: AgentMemoryScope = AgentMemoryScope.ISOLATED
    state: Dict[str, Any] = Field(default_factory=dict)
    edges: List[str] = Field(default_factory=list)


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[List[str]] = None
    memory_scope: Optional[AgentMemoryScope] = None
    state: Optional[Dict[str, Any]] = None
    edges: Optional[List[str]] = None
    active: Optional[bool] = None


class AgentCloneRequest(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None


class AgentDevWorkspace(BaseModel):
    agent_id: str
    workspace_path: str
    ide_url: str


class DemoStatus(BaseModel):
    """Runtime status of demo mode."""

    active: bool
    started_at: Optional[int] = None
    stopped_at: Optional[int] = None
    agent_ids: List[str] = Field(default_factory=list)
    session_ids: List[str] = Field(default_factory=list)
    message: str = ""


class TabProjection(BaseModel):
    tab_id: str
    agent_id: str
    viewport_mode: ViewportMode = ViewportMode.CHAT
    created_at: int = 0


class TabCreateRequest(BaseModel):
    agent_id: Optional[str] = None
    viewport_mode: ViewportMode = ViewportMode.CHAT


class TabUpdateRequest(BaseModel):
    agent_id: Optional[str] = None
    viewport_mode: Optional[ViewportMode] = None


class AndyriaRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input: str
    agent_id: Optional[str] = None
    session_id: Optional[str] = None   # omit for stateless single-turn requests
    context: Dict[str, Any] = Field(default_factory=dict)


class AndyriaResponse(BaseModel):
    request_id: str
    output: str
    tasks_completed: int
    verified: bool = True
    confidence: float = 0.0
    entropy_beacon_id: str
    event_ids: List[str]
    model_used: str = "stub"
    plan_summary: Optional[List[str]] = None
    processing_ms: Optional[int] = None
    timestamp_ns: Optional[int] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    turn_number: int = 0
    reflection: Optional["ReflectionResult"] = None


class NodeStatus(BaseModel):
    node_id: str
    deployment_class: str
    uptime_s: float
    requests_processed: int
    events_stored: int = 0
    entropy_beacons_generated: int = 0
    model_loaded: bool = False
    memory_objects: int = 0
    entropy_sources: List[str] = Field(default_factory=list)
    peer_count: int = 0
    peers: List[PeerStatus] = Field(default_factory=list)
    ready: bool = True
    readiness_detail: Optional[str] = None


class NodeConfig(BaseModel):
    """Runtime-configurable settings for the node."""
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    model_path: Optional[str] = None  # local GGUF path


class NodeConfigUpdate(BaseModel):
    """Partial update — only provided fields are changed."""
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None


class ChainDefinition(BaseModel):
    """An ordered sequence of agent IDs forming a multi-agent pipeline."""

    chain_id: str
    name: str
    agent_ids: List[str]
    active: bool = True
    created_at: int = 0


class ChainCreateRequest(BaseModel):
    name: str
    agent_ids: List[str]


class ChainRunRequest(BaseModel):
    input: str
    session_id: Optional[str] = None


class ATMThinkRequest(BaseModel):
    """Request body for a direct ATM think invocation."""
    prompt: str
    max_iterations: int = 3
    context: Dict[str, Any] = Field(default_factory=dict)


class ATMThoughtStepOut(BaseModel):
    """Serialised view of one ATM thought cycle step."""
    step: int
    output: str
    critique: str
    confidence: float
    model_used: str
    elapsed_ms: int


class ATMThoughtResponse(BaseModel):
    """HTTP response for a completed ATM think or reflect invocation."""
    thought_id: str
    prompt: str
    steps: List[ATMThoughtStepOut]
    final_output: str
    final_confidence: float
    total_ms: int
    timestamp_ns: int


class ReflectionResult(BaseModel):
    """Embedded in AndyriaResponse when self-reflection was performed."""
    thought_id: str
    critique: str
    revised: bool
    final_confidence: float
    total_ms: int
