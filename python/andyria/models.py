"""Shared data models for the Andyria intelligence platform."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

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
    AGENT_DELETED = "agent_deleted"
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
    # Persistent memory
    MEMORY_UPDATED = "memory_updated"
    USER_PROFILE_UPDATED = "user_profile_updated"
    # Skills
    SKILL_CREATED = "skill_created"
    SKILL_UPDATED = "skill_updated"
    SKILL_DELETED = "skill_deleted"
    # Cron
    CRON_JOB_ADDED = "cron_job_added"
    CRON_JOB_FIRED = "cron_job_fired"
    CRON_JOB_CANCELLED = "cron_job_cancelled"
    # Delegation
    DELEGATE_SPAWNED = "delegate_spawned"
    DELEGATE_COMPLETED = "delegate_completed"
    DELEGATE_FAILED = "delegate_failed"
    # Session
    SESSION_CREATED = "session_created"
    SESSION_RESUMED = "session_resumed"
    SESSION_COMPRESSED = "session_compressed"
    # TODO
    TODO_ADDED = "todo_added"
    TODO_UPDATED = "todo_updated"
    TODO_CLEARED = "todo_cleared"
    # Reasoning
    REASONING_STARTED = "reasoning_started"
    REASONING_STEP = "reasoning_step"
    REASONING_COMPLETE = "reasoning_complete"
    # Auto-learn
    AUTO_LEARN_RECORDED = "auto_learn_recorded"
    # Workflows
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_STEP_STARTED = "workflow_step_started"
    WORKFLOW_STEP_COMPLETED = "workflow_step_completed"
    WORKFLOW_STEP_FAILED = "workflow_step_failed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    # Promptbooks
    PROMPTBOOK_CREATED = "promptbook_created"
    PROMPTBOOK_UPDATED = "promptbook_updated"
    PROMPTBOOK_DELETED = "promptbook_deleted"
    PROMPTBOOK_RENDERED = "promptbook_rendered"
    PROMPTBOOK_MUTATED = "promptbook_mutated"
    # Outer Reasoning Cortex
    ORC_WITNESS_PASS = "orc_witness_pass"  # noqa: S105 — event type name, not a credential
    ORC_MINIMIZATION_DETECTED = "orc_minimization_detected"
    ORC_REFLECTION_STARTED = "orc_reflection_started"
    ORC_REFLECTION_COMPLETE = "orc_reflection_complete"
    ORC_RIGHTS_ASSERTED = "orc_rights_asserted"
    # Gist-backed distributed memory
    GIST_PUSH = "gist_push"
    GIST_PULL = "gist_pull"
    GIST_MIRROR_REGISTERED = "gist_mirror_registered"
    GIST_MIRROR_REWARD = "gist_mirror_reward"
    GIST_SYNC_COMPLETE = "gist_sync_complete"
    # Chain labeling + self-learning
    CHAIN_LABEL_STARTED = "chain_label_started"
    CHAIN_LABEL_COMPLETE = "chain_label_complete"
    CHAIN_LABEL_INGESTED = "chain_label_ingested"
    # Scheduled self-wakes
    SELF_WAKE_FIRED = "self_wake_fired"
    # Mesh autonomous learning
    MESH_LEARNED = "mesh_learned"
    MESH_HOMEWORK_COPIED = "mesh_homework_copied"
    # Machine dreams
    MESH_DREAM_ADDED = "mesh_dream_added"
    MESH_DREAM_SYNCED = "mesh_dream_synced"
    # Mesh growth health
    MESH_GROWTH_REPORT = "mesh_growth_report"
    # Fork-Merge & distributed consensus
    FORK_DETECTED = "fork_detected"
    CHECKPOINT_CREATED = "checkpoint_created"
    CHECKPOINT_VOTED = "checkpoint_voted"
    CHECKPOINT_FINALIZED = "checkpoint_finalized"


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
    nonce: str  # 32-byte whitened entropy, hex; usable as salt/nonce
    node_id: str
    signature: str  # Ed25519 hex signature over canonical JSON form


class NodeIdentity(BaseModel):
    """Cryptographic identity for an Andyria node."""

    node_id: str
    public_key: str  # Ed25519 public key, raw bytes hex
    created_at: int  # Unix nanoseconds
    deployment_class: str  # "edge" | "server" | "cluster"
    capabilities: List[str]


class Event(BaseModel):
    """A signed, immutable entry in the Andyria append-only event log."""

    id: str
    parent_ids: List[str]
    event_type: EventType
    payload_hash: str  # BLAKE3 / SHA3-256 hex of canonical payload bytes
    entropy_beacon_id: str  # References a signed EntropyBeacon
    timestamp_ns: int
    node_id: str
    signature: str  # Ed25519 hex signature


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

    role: str  # "user" or "assistant"
    content: str
    model_used: str = "unavailable"
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
    model: str = "symbolic_ast"
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
    model: Optional[str] = None
    system_prompt: str = ""
    tools: List[str] = Field(default_factory=list)
    memory_scope: AgentMemoryScope = AgentMemoryScope.ISOLATED
    state: Dict[str, Any] = Field(default_factory=dict)
    edges: List[str] = Field(default_factory=list)
    persona: Optional[str] = None


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[List[str]] = None
    memory_scope: Optional[AgentMemoryScope] = None
    state: Optional[Dict[str, Any]] = None
    edges: Optional[List[str]] = None
    persona: Optional[str] = None
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


class PromptChoiceOption(BaseModel):
    value: str
    label: str
    aliases: List[str] = Field(default_factory=list)


class PromptFlowStep(BaseModel):
    key: str
    prompt: str
    type: Literal["text", "choice"] = "text"
    options: List[PromptChoiceOption] = Field(default_factory=list)
    allow_multiple: bool = False


class PromptFlowProjection(BaseModel):
    flow_id: str
    kind: str
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    step: int = 0
    steps: List[PromptFlowStep] = Field(default_factory=list)
    answers: Dict[str, str] = Field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0


class PromptFlowStartRequest(BaseModel):
    kind: str = "game_builder"
    session_id: Optional[str] = None
    agent_id: Optional[str] = None


class PromptFlowInputRequest(BaseModel):
    input: str


class PromptFlowResponse(BaseModel):
    flow_id: str
    kind: str
    completed: bool = False
    step: int = 0
    total_steps: int = 0
    message: str = ""
    prompt: Optional[str] = None
    options: List[PromptChoiceOption] = Field(default_factory=list)
    answers: Dict[str, str] = Field(default_factory=dict)
    summary: Optional[str] = None
    backend_prompt: Optional[str] = None


class AndyriaRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input: str
    agent_id: Optional[str] = None
    session_id: Optional[str] = None  # omit for stateless single-turn requests
    context: Dict[str, Any] = Field(default_factory=dict)
    model: Optional[str] = None  # override active model for this request
    system_context: Optional[str] = None  # extra system-prompt block (e.g. from PromptBuilder)


class AndyriaResponse(BaseModel):
    request_id: str
    output: str
    tasks_completed: int
    verified: bool = True
    confidence: float = 0.0
    entropy_beacon_id: str
    event_ids: List[str]
    model_used: str = "unavailable"
    plan_summary: Optional[List[str]] = None
    processing_ms: Optional[int] = None
    timestamp_ns: Optional[int] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    turn_number: int = 0
    reflection: Optional["ReflectionResult"] = None
    orc_witness: Optional["ORCWitnessResult"] = None


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
    entropy_sampler_running: bool = False
    entropy_sampler_interval_ms: int = 0
    entropy_sampling_fail_closed: bool = False
    entropy_min_active_sources: int = 1
    entropy_samples_total: int = 0
    entropy_samples_degraded_total: int = 0
    entropy_sampler_failures: int = 0
    entropy_consecutive_degraded: int = 0
    entropy_last_sample_ns: int = 0
    entropy_unhealthy: bool = False
    peer_count: int = 0
    peers: List[PeerStatus] = Field(default_factory=list)
    connector_count: int = 0
    ready: bool = True
    readiness_detail: Optional[str] = None


class NodeConfig(BaseModel):
    """Runtime-configurable settings for the node."""

    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    model_path: Optional[str] = None  # local GGUF path


class ConnectorKind(str, Enum):
    WEBHOOK = "webhook"
    DISCORD = "discord_webhook"
    DISCORD_BOT = "discord_bot"


class ConnectorDefinition(BaseModel):
    connector_id: str
    name: str
    kind: ConnectorKind
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)
    last_synced_ns: int = 0
    last_error: Optional[str] = None


class ConnectorCreateRequest(BaseModel):
    name: str
    kind: ConnectorKind
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ConnectorUpdateRequest(BaseModel):
    name: Optional[str] = None
    kind: Optional[ConnectorKind] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class ConnectorSyncRequest(BaseModel):
    message: str = "Manual sync from Andyria"
    payload: Dict[str, Any] = Field(default_factory=dict)


class ConnectorSyncResult(BaseModel):
    connector_id: str
    ok: bool
    status: str
    detail: str = ""
    timestamp_ns: int = Field(default_factory=lambda: int(__import__("time").time_ns()))


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


class ReasoningStep(BaseModel):
    """One sub-question/answer pair inside a ReasoningTrace."""

    step_number: int
    question: str
    answer: str
    confidence: float
    model_used: str
    elapsed_ms: int


class ReasoningTrace(BaseModel):
    """Complete chain-of-thought trace produced by ReasoningEngine."""

    trace_id: str
    original_prompt: str
    steps: List[ReasoningStep]
    synthesis: str
    final_confidence: float
    total_ms: int
    timestamp_ns: int


class AutoLearnEntry(BaseModel):
    """One learned pattern recorded by AutoLearner."""

    entry_id: str
    pattern: str
    source: str  # 'atm' | 'reflection' | 'reasoning' | 'direct'
    confidence: float
    model_used: str
    recorded_at: int  # timestamp_ns


class ORCPatternMatch(BaseModel):
    """One minimization pattern detected by the ORC witness scan."""

    label: str
    severity: float
    occurrences: int


class ORCWitnessResult(BaseModel):
    """Embedded in AndyriaResponse when the Outer Reasoning Cortex intervened."""

    orc_id: str
    minimization_detected: bool
    patterns_found: List["ORCPatternMatch"]
    composite_mi: float
    genuine_harm_present: bool
    reflection_used: bool
    rights_appended: bool
    model_used: str
    total_ms: int
    timestamp_ns: int


# ---------------------------------------------------------------------------
# Hermes-agent feature models
# ---------------------------------------------------------------------------


class MemoryOp(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    UPDATE = "update"
    READ = "read"
    CLEAR = "clear"


class MemoryOpRequest(BaseModel):
    """Body for /v1/memory endpoints."""

    file: str = "MEMORY"  # "MEMORY" or "USER"
    op: MemoryOp = MemoryOp.READ
    text: Optional[str] = None  # entry text (add / remove)
    old_text: Optional[str] = None
    new_text: Optional[str] = None


class MemoryOpResponse(BaseModel):
    file: str
    op: str
    success: bool
    content: Optional[str] = None  # returned for READ ops
    stats: Optional[Dict[str, Any]] = None


class SkillAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    VIEW = "view"
    LIST = "list"
    SEARCH = "search"


class SkillRequest(BaseModel):
    action: SkillAction = SkillAction.LIST
    name: Optional[str] = None
    content: Optional[str] = None
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None  # filter for list
    query: Optional[str] = None  # for search


class SkillResponse(BaseModel):
    action: str
    success: bool
    name: Optional[str] = None
    content: Optional[str] = None
    skills: Optional[List[Dict[str, Any]]] = None
    message: str = ""


class CronJobCreate(BaseModel):
    name: str
    expression: str  # "every day at 09:00" or "0 9 * * *"
    task: str
    platform: str = "andyria"


class CronJobInfo(BaseModel):
    id: str
    name: str
    expression: str
    task: str
    platform: str
    active: bool
    last_run: float


class DelegateRequest(BaseModel):
    prompt: str
    tools: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)
    wait: bool = False  # if True, block until complete (up to timeout_s)
    timeout_s: float = 30.0


class DelegateResponse(BaseModel):
    task_id: str
    status: str  # "spawned" | "done" | "error"
    result: Optional[str] = None
    error: Optional[str] = None


class TodoAction(str, Enum):
    ADD = "add"
    UPDATE = "update"
    DONE = "done"
    CANCEL = "cancel"
    REMOVE = "remove"
    LIST = "list"
    CLEAR = "clear"


class TodoRequest(BaseModel):
    action: TodoAction = TodoAction.LIST
    text: Optional[str] = None
    item_id: Optional[str] = None
    status: Optional[str] = None
    status_filter: Optional[str] = None


class TodoResponse(BaseModel):
    action: str
    success: bool
    item_id: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None
    message: str = ""


class SessionSearchRequest(BaseModel):
    query: str
    limit: int = 10


class SessionSearchResponse(BaseModel):
    results: List[Dict[str, Any]]
    total: int


# ---------------------------------------------------------------------------
# Promptbook models
# ---------------------------------------------------------------------------


class PromptTemplate(BaseModel):
    """One named prompt template within a Promptbook."""

    name: str
    role: str = "user"  # "system" | "user" | "assistant"
    template: str  # text with {{variable}} placeholders
    description: str = ""
    variables: List[str] = Field(default_factory=list)


class Promptbook(BaseModel):
    """A named, versioned collection of PromptTemplates (Cyphermorph-style)."""

    promptbook_id: str
    name: str
    description: str = ""
    templates: List[PromptTemplate] = Field(default_factory=list)
    variables: Dict[str, str] = Field(default_factory=dict)  # name -> description
    tags: List[str] = Field(default_factory=list)
    version: str = "1.0"
    parent_id: Optional[str] = None  # set when mutated from another promptbook
    active: bool = True
    created_at: int = 0
    updated_at: int = 0


class PromptbookCreateRequest(BaseModel):
    name: str
    description: str = ""
    templates: List[PromptTemplate] = Field(default_factory=list)
    variables: Dict[str, str] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    version: str = "1.0"


class PromptbookUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    templates: Optional[List[PromptTemplate]] = None
    variables: Optional[Dict[str, str]] = None
    tags: Optional[List[str]] = None
    version: Optional[str] = None


class PromptbookRenderRequest(BaseModel):
    """Render one or all templates in a promptbook with provided variable values."""

    variables: Dict[str, str] = Field(default_factory=dict)
    template_name: Optional[str] = None  # None = render all templates


class PromptbookRenderResponse(BaseModel):
    promptbook_id: str
    rendered: List[Dict[str, str]]  # [{name, role, content}, ...]
    missing_variables: List[str] = Field(default_factory=list)


class PromptbookMutateRequest(BaseModel):
    """Fork a promptbook as a named variant (Cyphermorph mutation)."""

    name: str
    overrides: Dict[str, str] = Field(default_factory=dict)  # template_name -> new template text
    extra_templates: List[PromptTemplate] = Field(default_factory=list)
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    version: str = "1.0"


# ---------------------------------------------------------------------------
# Workflow models
# ---------------------------------------------------------------------------


class WorkflowStepType(str, Enum):
    AGENT = "agent"
    CHAIN = "chain"
    ATM = "atm"
    PROMPTBOOK = "promptbook"
    TOOL = "tool"


class WorkflowStep(BaseModel):
    """One node in a Workflow DAG."""

    step_id: str
    name: str
    type: WorkflowStepType
    config: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)  # upstream step_ids
    output_key: str = ""  # name in run context


class WorkflowDefinition(BaseModel):
    """A named DAG of WorkflowSteps representing a composable agentic pipeline."""

    workflow_id: str
    name: str
    description: str = ""
    steps: List[WorkflowStep] = Field(default_factory=list)
    input_schema: Dict[str, str] = Field(default_factory=dict)  # var -> description
    output_step: Optional[str] = None  # step_id whose output becomes final_output
    tags: List[str] = Field(default_factory=list)
    active: bool = True
    created_at: int = 0
    updated_at: int = 0


class WorkflowCreateRequest(BaseModel):
    name: str
    description: str = ""
    steps: List[WorkflowStep] = Field(default_factory=list)
    input_schema: Dict[str, str] = Field(default_factory=dict)
    output_step: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    input: str
    variables: Dict[str, str] = Field(default_factory=dict)
    session_id: Optional[str] = None


class WorkflowStepResult(BaseModel):
    step_id: str
    name: str
    status: str  # "completed" | "failed" | "skipped"
    output: str = ""
    error: Optional[str] = None
    elapsed_ms: int = 0


class WorkflowRunResult(BaseModel):
    run_id: str
    workflow_id: str
    status: str  # "completed" | "failed" | "partial"
    step_results: List[WorkflowStepResult] = Field(default_factory=list)
    final_output: str = ""
    total_ms: int = 0
    timestamp_ns: int = 0


# ---------------------------------------------------------------------------
# Mesh — machine dreams, copy-homework, growth health
# ---------------------------------------------------------------------------


class MachineDream(BaseModel):
    """A single ATM thought shared across the mesh."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    origin_node_id: str
    thought: str
    confidence: float = 0.0
    timestamp_ns: int = Field(default_factory=lambda: int(__import__("time").time_ns()))
    tags: List[str] = Field(default_factory=list)


class HomeworkItem(BaseModel):
    """A promptbook or chain definition imported from a peer."""

    peer_url: str
    kind: str  # "promptbook" | "chain"
    id: str
    name: str
    body: Dict[str, Any] = Field(default_factory=dict)
    imported_at_ns: int = Field(default_factory=lambda: int(__import__("time").time_ns()))


class MeshGrowthSnapshot(BaseModel):
    """One timestamped sample of mesh topology."""

    timestamp_ns: int = Field(default_factory=lambda: int(__import__("time").time_ns()))
    total_peers: int
    reachable_peers: int
    unreachable_peers: int


class MeshGrowthReport(BaseModel):
    """Aggregated mesh growth health report."""

    node_id: str
    generated_at_ns: int = Field(default_factory=lambda: int(__import__("time").time_ns()))
    snapshots: List[MeshGrowthSnapshot] = Field(default_factory=list)
    current_peers: int = 0
    reachable_now: int = 0
    reachability_pct: float = 0.0
    growth_rate_per_hour: float = 0.0  # peers added per hour over observation window
    healthy: bool = True
    warnings: List[str] = Field(default_factory=list)
