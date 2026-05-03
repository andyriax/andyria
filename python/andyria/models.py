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


class AndyriaRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input: str
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
    session_id: Optional[str] = None
    turn_number: int = 0


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
