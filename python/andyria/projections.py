"""Persistent tab projections for UI views over shared agent state."""

from __future__ import annotations

import re
import time
import uuid
from typing import Dict, List, Optional

from .memory import ContentAddressedMemory
from .models import (
    PromptChoiceOption,
    PromptFlowInputRequest,
    PromptFlowProjection,
    PromptFlowResponse,
    PromptFlowStartRequest,
    PromptFlowStep,
    TabCreateRequest,
    TabProjection,
    TabUpdateRequest,
)


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


class PromptFlowStore:
    """State machine store for interactive prompt flows in chat."""

    _FLOWS_NS = "prompt_flows"

    def __init__(self, memory: ContentAddressedMemory) -> None:
        self._memory = memory

    def start(self, request: PromptFlowStartRequest) -> PromptFlowResponse:
        kind = (request.kind or "game_builder").strip().lower()
        steps = self._build_steps(kind)
        flow = PromptFlowProjection(
            flow_id=f"flow-{uuid.uuid4().hex[:12]}",
            kind=kind,
            session_id=request.session_id,
            agent_id=request.agent_id,
            step=0,
            steps=steps,
            answers={},
            created_at=int(time.time_ns()),
            updated_at=int(time.time_ns()),
        )
        self._save(flow)

        first = steps[0]
        return PromptFlowResponse(
            flow_id=flow.flow_id,
            kind=flow.kind,
            completed=False,
            step=1,
            total_steps=len(steps),
            message=self._intro_message(kind),
            prompt=first.prompt,
            options=first.options,
            answers=flow.answers,
        )

    def get(self, flow_id: str) -> Optional[PromptFlowResponse]:
        flow = self._get_projection(flow_id)
        if flow is None:
            return None
        return self._state_response(flow)

    def respond(self, flow_id: str, request: PromptFlowInputRequest) -> PromptFlowResponse:
        flow = self._get_projection(flow_id)
        if flow is None:
            raise ValueError("Prompt flow not found")

        user_input = str(request.input or "").strip()
        if not user_input:
            return self._state_response(flow, message="Please enter a value, or type /cancel to stop.")

        if user_input.lower() == "/cancel":
            self._memory.delete_binding(self._FLOWS_NS, flow.flow_id)
            return PromptFlowResponse(
                flow_id=flow.flow_id,
                kind=flow.kind,
                completed=True,
                step=min(flow.step + 1, len(flow.steps)),
                total_steps=len(flow.steps),
                message=f"{self._flow_name(flow.kind)} cancelled.",
                answers=flow.answers,
            )

        if flow.step >= len(flow.steps):
            return self._finalize(flow)

        current = flow.steps[flow.step]
        value = user_input

        if current.type == "choice":
            parsed = self._resolve_choice(user_input, current.options, allow_multiple=current.allow_multiple)
            if parsed is None:
                return self._state_response(flow, message="Please choose one of the available options.")
            value = parsed.value

        flow.answers[current.key] = value
        flow.step += 1
        flow.updated_at = int(time.time_ns())

        if flow.step >= len(flow.steps):
            self._memory.delete_binding(self._FLOWS_NS, flow.flow_id)
            return self._finalize(flow)

        self._save(flow)
        return self._state_response(flow)

    def _state_response(self, flow: PromptFlowProjection, message: Optional[str] = None) -> PromptFlowResponse:
        if flow.step >= len(flow.steps):
            return self._finalize(flow)

        current = flow.steps[flow.step]
        text = message or f"Step {flow.step + 1}/{len(flow.steps)}"
        return PromptFlowResponse(
            flow_id=flow.flow_id,
            kind=flow.kind,
            completed=False,
            step=flow.step + 1,
            total_steps=len(flow.steps),
            message=text,
            prompt=current.prompt,
            options=current.options,
            answers=flow.answers,
        )

    def _finalize(self, flow: PromptFlowProjection) -> PromptFlowResponse:
        summary = self._build_summary(flow)
        backend_prompt = self._build_backend_prompt(flow)
        return PromptFlowResponse(
            flow_id=flow.flow_id,
            kind=flow.kind,
            completed=True,
            step=len(flow.steps),
            total_steps=len(flow.steps),
            message="Flow complete.",
            answers=flow.answers,
            summary=summary,
            backend_prompt=backend_prompt,
        )

    def _build_steps(self, kind: str) -> List[PromptFlowStep]:
        if kind == "game_builder":
            return [
                PromptFlowStep(
                    key="type",
                    type="choice",
                    prompt="What type of game do you want to create?",
                    options=[
                        PromptChoiceOption(value="Platformer", label="Platformer"),
                        PromptChoiceOption(value="Roguelike", label="Roguelike"),
                        PromptChoiceOption(value="Puzzle", label="Puzzle"),
                        PromptChoiceOption(value="Tower Defense", label="Tower Defense"),
                        PromptChoiceOption(value="Visual Novel", label="Visual Novel"),
                        PromptChoiceOption(value="Card Game", label="Card Game"),
                    ],
                ),
                PromptFlowStep(
                    key="platform",
                    type="choice",
                    prompt="Which platform is primary?",
                    options=[
                        PromptChoiceOption(value="PC", label="PC"),
                        PromptChoiceOption(value="Mobile", label="Mobile"),
                        PromptChoiceOption(value="Web", label="Web"),
                        PromptChoiceOption(value="Console", label="Console"),
                        PromptChoiceOption(value="Cross-platform", label="Cross-platform"),
                    ],
                ),
                PromptFlowStep(
                    key="style",
                    type="choice",
                    prompt="What play style are you targeting?",
                    options=[
                        PromptChoiceOption(value="Single-player", label="Single-player"),
                        PromptChoiceOption(value="Multiplayer", label="Multiplayer"),
                        PromptChoiceOption(value="Co-op", label="Co-op"),
                    ],
                ),
                PromptFlowStep(
                    key="coreLoop",
                    type="text",
                    prompt="Describe the core gameplay loop in one or two lines.",
                ),
                PromptFlowStep(
                    key="scope",
                    type="choice",
                    prompt="What is your target scope?",
                    options=[
                        PromptChoiceOption(value="Weekend prototype", label="Weekend prototype"),
                        PromptChoiceOption(value="2-week MVP", label="2-week MVP"),
                        PromptChoiceOption(value="1-month vertical slice", label="1-month vertical slice"),
                        PromptChoiceOption(value="3-month production alpha", label="3-month production alpha"),
                    ],
                ),
                PromptFlowStep(
                    key="art",
                    type="choice",
                    prompt="What art style do you want?",
                    options=[
                        PromptChoiceOption(value="Pixel art", label="Pixel art"),
                        PromptChoiceOption(value="Low-poly 3D", label="Low-poly 3D"),
                        PromptChoiceOption(value="Hand-drawn", label="Hand-drawn"),
                        PromptChoiceOption(value="Minimalist", label="Minimalist"),
                        PromptChoiceOption(value="Stylized 3D", label="Stylized 3D"),
                    ],
                ),
            ]
        if kind == "project_planner":
            return [
                PromptFlowStep(
                    key="name",
                    type="text",
                    prompt="What is the name of your project?",
                ),
                PromptFlowStep(
                    key="type",
                    type="choice",
                    prompt="What type of project is this?",
                    options=[
                        PromptChoiceOption(value="Web App", label="Web App"),
                        PromptChoiceOption(value="CLI Tool", label="CLI Tool"),
                        PromptChoiceOption(value="API Service", label="API Service"),
                        PromptChoiceOption(value="Mobile App", label="Mobile App"),
                        PromptChoiceOption(value="Data Pipeline", label="Data Pipeline"),
                        PromptChoiceOption(value="ML Model", label="ML Model"),
                        PromptChoiceOption(value="Library/SDK", label="Library / SDK"),
                    ],
                ),
                PromptFlowStep(
                    key="stack",
                    type="choice",
                    prompt="What is your primary tech stack?",
                    options=[
                        PromptChoiceOption(value="Python", label="Python"),
                        PromptChoiceOption(value="TypeScript", label="TypeScript"),
                        PromptChoiceOption(value="Rust", label="Rust"),
                        PromptChoiceOption(value="Go", label="Go"),
                        PromptChoiceOption(value="Full-stack JS", label="Full-stack JS"),
                        PromptChoiceOption(value="Mixed", label="Mixed / Polyglot"),
                    ],
                ),
                PromptFlowStep(
                    key="timeline",
                    type="choice",
                    prompt="What is your delivery timeline?",
                    options=[
                        PromptChoiceOption(value="1 week", label="1 week"),
                        PromptChoiceOption(value="2 weeks", label="2 weeks"),
                        PromptChoiceOption(value="1 month", label="1 month"),
                        PromptChoiceOption(value="3 months", label="3 months"),
                        PromptChoiceOption(value="6+ months", label="6+ months"),
                    ],
                ),
                PromptFlowStep(
                    key="team",
                    type="choice",
                    prompt="What is your team size?",
                    options=[
                        PromptChoiceOption(value="Solo", label="Solo"),
                        PromptChoiceOption(value="2-3 people", label="2-3 people"),
                        PromptChoiceOption(value="4-8 people", label="4-8 people"),
                        PromptChoiceOption(value="8+ people", label="8+ people"),
                    ],
                ),
                PromptFlowStep(
                    key="description",
                    type="text",
                    prompt="Briefly describe what the project does and its main goal.",
                ),
            ]
        if kind == "agent_onboarding":
            return [
                PromptFlowStep(
                    key="name",
                    type="text",
                    prompt="What is the name for this agent?",
                ),
                PromptFlowStep(
                    key="role",
                    type="choice",
                    prompt="What primary role will this agent play?",
                    options=[
                        PromptChoiceOption(value="Assistant", label="Assistant"),
                        PromptChoiceOption(value="Analyst", label="Analyst"),
                        PromptChoiceOption(value="Researcher", label="Researcher"),
                        PromptChoiceOption(value="Coder", label="Coder"),
                        PromptChoiceOption(value="Planner", label="Planner"),
                        PromptChoiceOption(value="Manager", label="Manager"),
                    ],
                ),
                PromptFlowStep(
                    key="personality",
                    type="choice",
                    prompt="What communication personality should this agent have?",
                    options=[
                        PromptChoiceOption(value="Professional", label="Professional"),
                        PromptChoiceOption(value="Friendly", label="Friendly"),
                        PromptChoiceOption(value="Concise", label="Concise"),
                        PromptChoiceOption(value="Verbose", label="Verbose"),
                        PromptChoiceOption(value="Socratic", label="Socratic"),
                    ],
                ),
                PromptFlowStep(
                    key="tools",
                    type="choice",
                    allow_multiple=True,
                    prompt="Which tools should this agent have access to? (select all that apply)",
                    options=[
                        PromptChoiceOption(value="Web search", label="Web search"),
                        PromptChoiceOption(value="Code execution", label="Code execution"),
                        PromptChoiceOption(value="File access", label="File access"),
                        PromptChoiceOption(value="API calls", label="API calls"),
                        PromptChoiceOption(value="Memory", label="Memory"),
                        PromptChoiceOption(value="Delegation", label="Delegation"),
                    ],
                ),
                PromptFlowStep(
                    key="memory_scope",
                    type="choice",
                    prompt="What memory scope should this agent use?",
                    options=[
                        PromptChoiceOption(value="Session only", label="Session only"),
                        PromptChoiceOption(value="Persistent", label="Persistent"),
                        PromptChoiceOption(value="Shared across agents", label="Shared across agents"),
                    ],
                ),
                PromptFlowStep(
                    key="goal",
                    type="text",
                    prompt="Describe this agent's primary goal or mission in one or two sentences.",
                ),
            ]
        if kind == "deployment_wizard":
            return [
                PromptFlowStep(
                    key="target",
                    type="choice",
                    prompt="Where are you deploying?",
                    options=[
                        PromptChoiceOption(value="Docker", label="Docker"),
                        PromptChoiceOption(value="Kubernetes", label="Kubernetes"),
                        PromptChoiceOption(value="AWS", label="AWS"),
                        PromptChoiceOption(value="GCP", label="GCP"),
                        PromptChoiceOption(value="Azure", label="Azure"),
                        PromptChoiceOption(value="Raspberry Pi", label="Raspberry Pi"),
                        PromptChoiceOption(value="VPS / Bare metal", label="VPS / Bare metal"),
                    ],
                ),
                PromptFlowStep(
                    key="scale",
                    type="choice",
                    prompt="What scale are you targeting?",
                    options=[
                        PromptChoiceOption(value="Single instance", label="Single instance"),
                        PromptChoiceOption(value="Small cluster (2-5)", label="Small cluster (2-5)"),
                        PromptChoiceOption(value="Medium cluster (5-20)", label="Medium cluster (5-20)"),
                        PromptChoiceOption(value="Auto-scale", label="Auto-scale"),
                    ],
                ),
                PromptFlowStep(
                    key="auth",
                    type="choice",
                    prompt="What authentication method will you use?",
                    options=[
                        PromptChoiceOption(value="None", label="None"),
                        PromptChoiceOption(value="API key", label="API key"),
                        PromptChoiceOption(value="JWT", label="JWT"),
                        PromptChoiceOption(value="OAuth2", label="OAuth2"),
                    ],
                ),
                PromptFlowStep(
                    key="monitoring",
                    type="choice",
                    prompt="What observability level do you need?",
                    options=[
                        PromptChoiceOption(value="None", label="None"),
                        PromptChoiceOption(value="Logs only", label="Logs only"),
                        PromptChoiceOption(value="Metrics", label="Metrics + Logs"),
                        PromptChoiceOption(value="Full observability", label="Full observability"),
                    ],
                ),
                PromptFlowStep(
                    key="env",
                    type="choice",
                    prompt="Which environment is this deployment for?",
                    options=[
                        PromptChoiceOption(value="Development", label="Development"),
                        PromptChoiceOption(value="Staging", label="Staging"),
                        PromptChoiceOption(value="Production", label="Production"),
                    ],
                ),
                PromptFlowStep(
                    key="service",
                    type="text",
                    prompt="What service or application are you deploying? Describe it briefly.",
                ),
            ]
        if kind == "api_builder":
            return [
                PromptFlowStep(
                    key="resource",
                    type="text",
                    prompt='What is the primary resource name? (e.g. "user", "order", "product")',
                ),
                PromptFlowStep(
                    key="methods",
                    type="choice",
                    allow_multiple=True,
                    prompt="Which HTTP methods/operations do you need? (select all that apply)",
                    options=[
                        PromptChoiceOption(value="GET list", label="GET list"),
                        PromptChoiceOption(value="GET detail", label="GET detail"),
                        PromptChoiceOption(value="POST create", label="POST create"),
                        PromptChoiceOption(value="PUT update", label="PUT update (full)"),
                        PromptChoiceOption(value="PATCH partial", label="PATCH update (partial)"),
                        PromptChoiceOption(value="DELETE", label="DELETE"),
                    ],
                ),
                PromptFlowStep(
                    key="auth",
                    type="choice",
                    prompt="What authentication will protect this API?",
                    options=[
                        PromptChoiceOption(value="None", label="None (public)"),
                        PromptChoiceOption(value="API key", label="API key"),
                        PromptChoiceOption(value="Bearer token", label="Bearer / JWT"),
                        PromptChoiceOption(value="OAuth2", label="OAuth2"),
                    ],
                ),
                PromptFlowStep(
                    key="data_model",
                    type="text",
                    prompt="Describe the key fields for this resource (e.g. id, name, email, created_at).",
                ),
                PromptFlowStep(
                    key="pagination",
                    type="choice",
                    prompt="What pagination strategy for list endpoints?",
                    options=[
                        PromptChoiceOption(value="None", label="None"),
                        PromptChoiceOption(value="Cursor-based", label="Cursor-based"),
                        PromptChoiceOption(value="Offset-limit", label="Offset-limit"),
                        PromptChoiceOption(value="Page-based", label="Page-based"),
                    ],
                ),
            ]
        raise ValueError(f"Unsupported prompt flow kind: {kind}")

    def _intro_message(self, kind: str) -> str:
        messages = {
            "game_builder": (
                "Game Builder Wizard started. I will ask a few quick questions, "
                "then generate a complete game plan and implementation prompt."
            ),
            "project_planner": (
                "Project Planner started. Answer a few questions and I will produce "
                "a structured project plan, milestone breakdown, and scaffold prompt."
            ),
            "agent_onboarding": (
                "Agent Onboarding Wizard started. Tell me about your new agent and I will "
                "generate a full agent definition with system prompt and configuration."
            ),
            "deployment_wizard": (
                "Deployment Wizard started. Answer a few questions and I will produce "
                "a ready-to-use deployment configuration for your target platform."
            ),
            "api_builder": (
                "API Builder started. Tell me about your resource and I will generate "
                "a complete REST API definition with routes, schemas, and auth wiring."
            ),
        }
        return messages.get(kind, f"{self._flow_name(kind)} started.")

    def _flow_name(self, kind: str) -> str:
        names = {
            "game_builder": "Game Builder Wizard",
            "project_planner": "Project Planner",
            "agent_onboarding": "Agent Onboarding Wizard",
            "deployment_wizard": "Deployment Wizard",
            "api_builder": "API Builder",
        }
        return names.get(kind, "Prompt Flow")

    def _build_summary(self, flow: PromptFlowProjection) -> str:
        a = flow.answers
        if flow.kind == "game_builder":
            return (
                f"Type: {a.get('type', '')}; Platform: {a.get('platform', '')}; "
                f"Style: {a.get('style', '')}; Core loop: {a.get('coreLoop', '')}; "
                f"Scope: {a.get('scope', '')}; Art: {a.get('art', '')}"
            )
        return "; ".join(f"{k}: {v}" for k, v in a.items())

    def _build_backend_prompt(self, flow: PromptFlowProjection) -> str:  # noqa: PLR0911
        a = flow.answers
        if flow.kind == "game_builder":
            return "\n".join(
                [
                    "You are a senior game developer and technical design lead.",
                    "Create a practical implementation plan for the game below.",
                    "",
                    "Game specification:",
                    f"- Type: {a.get('type', '')}",
                    f"- Platform: {a.get('platform', '')}",
                    f"- Play style: {a.get('style', '')}",
                    f"- Core loop: {a.get('coreLoop', '')}",
                    f"- Scope/timeline: {a.get('scope', '')}",
                    f"- Art direction: {a.get('art', '')}",
                    "",
                    "Output requirements:",
                    "1) One-sentence game pitch",
                    "2) Core mechanics and controls",
                    "3) MVP feature list",
                    "4) Suggested engine/framework and why",
                    "5) Data model and system architecture",
                    "6) File/folder scaffold",
                    "7) 7-day execution plan",
                    "8) First coding tasks in priority order",
                    "9) Risks and scope-cut options",
                ]
            )
        if flow.kind == "project_planner":
            return "\n".join(
                [
                    "You are an expert software architect and project manager.",
                    "Produce a complete project plan for the project described below.",
                    "",
                    "Project specification:",
                    f"- Name: {a.get('name', '')}",
                    f"- Type: {a.get('type', '')}",
                    f"- Stack: {a.get('stack', '')}",
                    f"- Timeline: {a.get('timeline', '')}",
                    f"- Team size: {a.get('team', '')}",
                    f"- Description: {a.get('description', '')}",
                    "",
                    "Output requirements:",
                    "1) Elevator pitch (2 sentences)",
                    "2) Goals and non-goals",
                    "3) Phase/milestone breakdown with dates",
                    "4) Recommended architecture and folder structure",
                    "5) Key dependencies and tooling decisions",
                    "6) Risk register with mitigations",
                    "7) Definition of Done for MVP",
                    "8) First week task list",
                ]
            )
        if flow.kind == "agent_onboarding":
            return "\n".join(
                [
                    "You are an AI agent architect specializing in autonomous agent design.",
                    "Create a complete agent definition for the agent described below.",
                    "",
                    "Agent specification:",
                    f"- Name: {a.get('name', '')}",
                    f"- Role: {a.get('role', '')}",
                    f"- Personality: {a.get('personality', '')}",
                    f"- Tools: {a.get('tools', '')}",
                    f"- Memory scope: {a.get('memory_scope', '')}",
                    f"- Goal: {a.get('goal', '')}",
                    "",
                    "Output requirements:",
                    "1) Agent system prompt (ready to use)",
                    "2) Capabilities and tool configuration",
                    "3) Memory and context management strategy",
                    "4) Suggested delegation patterns",
                    "5) Example interaction showing correct behavior",
                    "6) Guardrails and failure modes to handle",
                    "7) Evaluation criteria for this agent",
                ]
            )
        if flow.kind == "deployment_wizard":
            return "\n".join(
                [
                    "You are a senior DevOps engineer and platform architect.",
                    "Generate a complete deployment configuration for the service below.",
                    "",
                    "Deployment specification:",
                    f"- Target platform: {a.get('target', '')}",
                    f"- Scale: {a.get('scale', '')}",
                    f"- Authentication: {a.get('auth', '')}",
                    f"- Monitoring: {a.get('monitoring', '')}",
                    f"- Environment: {a.get('env', '')}",
                    f"- Service description: {a.get('service', '')}",
                    "",
                    "Output requirements:",
                    "1) Deployment architecture diagram (ASCII)",
                    "2) Dockerfile or deployment manifest",
                    "3) Environment variables and secrets management",
                    "4) Health check and readiness probe configuration",
                    "5) Monitoring and alerting setup",
                    "6) Rollback and zero-downtime deployment strategy",
                    "7) CI/CD pipeline steps",
                    "8) Security hardening checklist",
                ]
            )
        if flow.kind == "api_builder":
            return "\n".join(
                [
                    "You are a senior API designer and backend engineer.",
                    "Generate a complete REST API definition for the resource below.",
                    "",
                    "API specification:",
                    f"- Resource: {a.get('resource', '')}",
                    f"- HTTP methods: {a.get('methods', '')}",
                    f"- Authentication: {a.get('auth', '')}",
                    f"- Data model fields: {a.get('data_model', '')}",
                    f"- Pagination: {a.get('pagination', '')}",
                    "",
                    "Output requirements:",
                    "1) OpenAPI 3.1 YAML snippet for all routes",
                    "2) Pydantic/JSON schema for request and response bodies",
                    "3) Route handler stubs (Python/FastAPI preferred)",
                    "4) Authentication middleware wiring",
                    "5) Pagination implementation pattern",
                    "6) Error response shapes (400, 401, 404, 422, 500)",
                    "7) Example curl commands for each endpoint",
                ]
            )
        return ""

    def _resolve_choice(
        self,
        raw: str,
        options: List[PromptChoiceOption],
        allow_multiple: bool = False,
    ) -> Optional[PromptChoiceOption]:
        if not options:
            return None

        by_index: Dict[str, PromptChoiceOption] = {str(idx + 1): option for idx, option in enumerate(options)}
        by_label: Dict[str, PromptChoiceOption] = {}
        for option in options:
            by_label[option.label.lower()] = option
            by_label[option.value.lower()] = option
            for alias in option.aliases:
                by_label[alias.lower()] = option

        parts = [raw.strip()]
        if allow_multiple:
            parts = [p.strip() for p in re.split(r"[;,]+", raw) if p.strip()]

        picked: List[PromptChoiceOption] = []
        for token in parts:
            cleaned = re.sub(r"^([0-9]+)[\).\-\s]+", r"\1", token.lower())
            selected: Optional[PromptChoiceOption] = by_index.get(cleaned) or by_label.get(cleaned)
            if selected is None:
                return None
            if selected not in picked:
                picked.append(selected)

        if not picked:
            return None

        if not allow_multiple:
            return picked[0]

        return PromptChoiceOption(
            value=", ".join(o.value for o in picked),
            label=", ".join(o.label for o in picked),
            aliases=[],
        )

    def _get_projection(self, flow_id: str) -> Optional[PromptFlowProjection]:
        raw = self._memory.get_by_key(self._FLOWS_NS, flow_id)
        if raw is None:
            return None
        try:
            return PromptFlowProjection.model_validate_json(raw)
        except Exception:
            return None

    def _save(self, flow: PromptFlowProjection) -> None:
        content_hash = self._memory.put(flow.model_dump_json().encode())
        self._memory.bind(self._FLOWS_NS, flow.flow_id, content_hash)
