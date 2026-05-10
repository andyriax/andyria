"""HTTP API for Andyria (FastAPI)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response

from .agent_features import (
    default_agent_environments,
    default_agent_modes,
    predominant_skills_for_agent,
)
from .coordinator import Coordinator
from .cron import CronScheduler
from .delegation import DelegationManager
from .demo import DemoManager
from .models import (
    AgentCloneRequest,
    AgentCreateRequest,
    AgentDefinition,
    AgentDevWorkspace,
    AgentUpdateRequest,
    AndyriaRequest,
    AndyriaResponse,
    ATMThinkRequest,
    ATMThoughtResponse,
    ChainCreateRequest,
    ChainDefinition,
    ChainRunRequest,
    ConnectorCreateRequest,
    ConnectorDefinition,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ConnectorUpdateRequest,
    CronJobCreate,
    CronJobInfo,
    DelegateRequest,
    DelegateResponse,
    DemoStatus,
    Event,
    EventType,
    MemoryOpRequest,
    MemoryOpResponse,
    NodeConfig,
    NodeConfigUpdate,
    NodeStatus,
    Promptbook,
    PromptbookCreateRequest,
    PromptbookMutateRequest,
    PromptbookRenderRequest,
    PromptbookRenderResponse,
    PromptbookUpdateRequest,
    PromptFlowInputRequest,
    PromptFlowResponse,
    PromptFlowStartRequest,
    SessionContext,
    SessionSearchRequest,
    SessionSearchResponse,
    SkillRequest,
    SkillResponse,
    TabCreateRequest,
    TabProjection,
    TabUpdateRequest,
    TodoRequest,
    TodoResponse,
    WorkflowCreateRequest,
    WorkflowDefinition,
    WorkflowRunRequest,
    WorkflowRunResult,
)
from .persistent_memory import PersistentMemory
from .persona import render_avatar_svg
from .session_store import SessionStore
from .skills import SkillRegistry
from .slash_commands import list_slash_commands
from .soul import SoulFile
from .todo import TodoStore

_coordinator: Optional[Coordinator] = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan manager — start/stop mesh on app startup/shutdown."""
    # Startup
    if _coordinator:
        await _coordinator.start_background_tasks()
    if _coordinator and _coordinator.mesh:
        await _coordinator.mesh.start()
    yield
    # Shutdown
    if _coordinator and _coordinator.mesh:
        await _coordinator.mesh.stop()
    if _coordinator:
        await _coordinator.stop_background_tasks()
    if _coordinator and hasattr(_coordinator, "close"):
        _coordinator.close()


def create_app(coordinator: Coordinator) -> FastAPI:
    global _coordinator
    _coordinator = coordinator

    app = FastAPI(
        title="Andyria",
        description="Edge-first hybrid intelligence platform",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in os.environ.get(
                "ANDYRIA_CORS_ORIGINS",
                "http://localhost,http://127.0.0.1",
            ).split(",")
            if origin.strip()
        ],
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Content-Type"],
    )

    static_dir = Path(__file__).resolve().parent / "static"
    index_file = static_dir / "index.html"
    repo_root = Path(__file__).resolve().parents[2]
    docs_visualizer_file = repo_root / "docs" / "blockchain-visualizer.html"
    static_visualizer_file = static_dir / "blockchain-visualizer.html"
    default_dev_root = Path(getattr(_coordinator, "_data_dir", Path("."))) / "agent-dev"
    dev_workspace_root = Path(os.environ.get("ANDYRIA_AGENT_DEV_ROOT", str(default_dev_root)))
    _demo_manager = DemoManager(_coordinator)
    code_server_base = os.environ.get("ANDYRIA_CODE_SERVER_URL", "http://localhost:8080").rstrip("/")
    code_server_folder_root = os.environ.get(
        "ANDYRIA_CODE_SERVER_FOLDER_ROOT",
        "/home/coder/project/python/.agent-dev",
    ).rstrip("/")
    _data_dir_path = Path(getattr(_coordinator, "_data_dir", Path.home() / ".andyria"))
    _session_store = SessionStore(_data_dir_path)

    def _delegate_prompt(prompt: str, tools: List[str], cfg: Dict[str, Any]) -> str:
        if _coordinator is None:
            raise RuntimeError("Coordinator not initialized")
        return asyncio.run(_coordinator.process(AndyriaRequest(input=prompt))).output

    _delegation = DelegationManager(
        coordinator_factory=_delegate_prompt
    )

    @app.get("/", include_in_schema=False, response_model=None)
    async def root():
        if index_file.exists():
            return FileResponse(index_file)
        return RedirectResponse(url="/docs")

    manage_file = static_dir / "manage.html"

    @app.get("/manage", include_in_schema=False, response_model=None)
    async def manage():
        if manage_file.exists():
            return FileResponse(manage_file)
        return RedirectResponse(url="/docs")

    @app.get("/blockchain-visualizer", include_in_schema=False, response_model=None)
    async def blockchain_visualizer():
        if docs_visualizer_file.exists():
            return FileResponse(docs_visualizer_file)
        if static_visualizer_file.exists():
            return FileResponse(static_visualizer_file)
        raise HTTPException(status_code=404, detail="Blockchain visualizer page not found")

    @app.post("/v1/infer", response_model=AndyriaResponse)
    async def infer(request: AndyriaRequest) -> AndyriaResponse:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return await _coordinator.process(request)

    @app.get("/v1/slash-commands", response_model=List[Dict[str, Any]])
    async def get_slash_commands(target: str = "web") -> List[Dict[str, Any]]:
        return list_slash_commands(target)

    _FLOW_KIND_REGISTRY = [
        {
            "kind": "game_builder",
            "name": "Game Builder Wizard",
            "description": (
                "Design a game from genre, camera, audience, progression, and monetization "
                "to get a full implementation plan."
            ),
            "triggers": ["create a game", "make a game", "build a game", "new game", "game wizard"],
        },
        {
            "kind": "project_planner",
            "name": "Project Planner",
            "description": "Plan a software project with milestones, architecture, and a first-week task list.",
            "triggers": ["plan a project", "new project", "project planner", "project wizard", "plan my project"],
        },
        {
            "kind": "agent_onboarding",
            "name": "Agent Onboarding Wizard",
            "description": "Configure a new AI agent with role, personality, tools, and a ready-to-use system prompt.",
            "triggers": ["create an agent", "new agent", "onboard agent", "agent wizard", "configure agent"],
        },
        {
            "kind": "deployment_wizard",
            "name": "Deployment Wizard",
            "description": "Generate a deployment config for Docker, Kubernetes, cloud, or bare metal.",
            "triggers": ["deploy", "deployment wizard", "setup deployment", "deploy my app", "deployment config"],
        },
        {
            "kind": "api_builder",
            "name": "API Builder",
            "description": "Scaffold a REST API with OpenAPI spec, route stubs, auth, and pagination.",
            "triggers": ["build an api", "create an api", "api wizard", "new api", "rest api", "api builder"],
        },
    ]

    @app.get("/v1/prompt-flows/kinds")
    async def list_prompt_flow_kinds() -> List[Dict[str, Any]]:
        return _FLOW_KIND_REGISTRY

    @app.post("/v1/prompt-flows/start", response_model=PromptFlowResponse)
    async def start_prompt_flow(request: PromptFlowStartRequest) -> PromptFlowResponse:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            return _coordinator.start_prompt_flow(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/prompt-flows/{flow_id}", response_model=PromptFlowResponse)
    async def get_prompt_flow(flow_id: str) -> PromptFlowResponse:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        flow = _coordinator.get_prompt_flow(flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="Prompt flow not found")
        return flow

    @app.post("/v1/prompt-flows/{flow_id}/respond", response_model=PromptFlowResponse)
    async def respond_prompt_flow(flow_id: str, request: PromptFlowInputRequest) -> PromptFlowResponse:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            return _coordinator.respond_prompt_flow(flow_id, request)
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "not found" in detail.lower() else 400
            raise HTTPException(status_code=status, detail=detail) from exc

    @app.get("/v1/status", response_model=NodeStatus)
    async def status() -> NodeStatus:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.status()

    @app.get("/v1/connectors", response_model=List[ConnectorDefinition])
    async def list_connectors() -> List[ConnectorDefinition]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_connectors()

    @app.post("/v1/connectors", response_model=ConnectorDefinition, status_code=201)
    async def create_connector(request: ConnectorCreateRequest) -> ConnectorDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="Connector name is required")
        return _coordinator.create_connector(request)

    @app.get("/v1/connectors/{connector_id}", response_model=ConnectorDefinition)
    async def get_connector(connector_id: str) -> ConnectorDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        connector = _coordinator.get_connector(connector_id)
        if connector is None:
            raise HTTPException(status_code=404, detail="Connector not found")
        return connector

    @app.patch("/v1/connectors/{connector_id}", response_model=ConnectorDefinition)
    async def update_connector(connector_id: str, request: ConnectorUpdateRequest) -> ConnectorDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        connector = _coordinator.update_connector(connector_id, request)
        if connector is None:
            raise HTTPException(status_code=404, detail="Connector not found")
        return connector

    @app.delete("/v1/connectors/{connector_id}", response_model=None)
    async def delete_connector(connector_id: str) -> Dict[str, str]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        deleted = _coordinator.delete_connector(connector_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Connector not found")
        return {"status": "deleted", "connector_id": connector_id}

    @app.post("/v1/connectors/{connector_id}/sync", response_model=ConnectorSyncResult)
    async def sync_connector(connector_id: str, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        result = _coordinator.sync_connector(connector_id, request)
        if not result.ok and result.status == "not_found":
            raise HTTPException(status_code=404, detail=result.detail)
        return result

    @app.get("/v1/events", response_model=List[Event])
    async def events(
        event_type: Optional[EventType] = None,
        agent_id: Optional[str] = None,
        tab_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Event]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.query_events(
            event_type=event_type,
            agent_id=agent_id,
            tab_id=tab_id,
            limit=limit,
        )

    @app.websocket("/v1/stream")
    async def stream_events(
        websocket: WebSocket,
        event_type: Optional[str] = None,
        agent_id: Optional[str] = None,
        tab_id: Optional[str] = None,
    ) -> None:
        if _coordinator is None:
            await websocket.close(code=1011)
            return

        await websocket.accept()
        normalized_event_type: Optional[EventType] = None
        if event_type:
            try:
                normalized_event_type = EventType(event_type)
            except ValueError:
                await websocket.send_json({"error": f"Invalid event_type: {event_type}"})
                await websocket.close(code=1003)
                return

        queue = _coordinator.subscribe_events()
        try:
            while True:
                item = await asyncio.to_thread(queue.get)
                event = item.get("event")
                metadata = item.get("metadata") or {}
                if not isinstance(event, Event):
                    continue

                if normalized_event_type is not None and event.event_type != normalized_event_type:
                    continue
                if agent_id is not None and metadata.get("agent_id") != agent_id:
                    continue
                if tab_id is not None and metadata.get("tab_id") != tab_id:
                    continue

                await websocket.send_json(
                    {
                        "event": event.model_dump(),
                        "metadata": metadata,
                    }
                )
        except WebSocketDisconnect:
            pass
        finally:
            _coordinator.unsubscribe_events(queue)

    @app.get("/v1/beacon/{beacon_id}")
    async def get_beacon(beacon_id: str) -> Dict[str, Any]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        beacon = _coordinator.get_beacon(beacon_id)
        if beacon is None:
            raise HTTPException(status_code=404, detail="Beacon not found")
        return beacon.model_dump()

    @app.get("/v1/session/{session_id}", response_model=SessionContext)
    async def get_session(session_id: str) -> SessionContext:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        session = _coordinator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.delete("/v1/session/{session_id}", response_model=None)
    async def delete_session(session_id: str) -> Dict[str, str]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        _coordinator.clear_session(session_id)
        return {"status": "cleared", "session_id": session_id}

    @app.get("/v1/config", response_model=NodeConfig)
    async def get_config() -> NodeConfig:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.get_config()

    @app.patch("/v1/config", response_model=NodeConfig)
    async def update_config(update: NodeConfigUpdate) -> NodeConfig:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.update_config(update)

    @app.get("/v1/models", response_model=List[str])
    async def list_models() -> List[str]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        cfg = _coordinator.get_config()
        url = cfg.ollama_url
        if not url:
            return []
        try:
            import httpx

            response = httpx.get(f"{url}/api/tags", timeout=4.0)
            response.raise_for_status()
            payload = response.json()
            return [str(m.get("name", "")) for m in payload.get("models", []) if m.get("name")]
        except Exception:
            return []

    # ── Agent Presets ──────────────────────────────────────────────────────
    _PRESET_PATHS = [
        Path(__file__).parent.parent.parent / "deploy" / "presets" / "agents.json",
        Path("/data/andyria/presets/agents.json"),
        Path.home() / ".andyria" / "presets" / "agents.json",
    ]

    def _load_agent_presets() -> List[Dict[str, Any]]:
        for p in _PRESET_PATHS:
            if p.exists():
                try:
                    payload = json.loads(p.read_text())
                    if isinstance(payload, list):
                        return [item for item in payload if isinstance(item, dict)]
                except Exception:
                    pass
        return []

    def _preset_revision(preset: Dict[str, Any]) -> str:
        revision_payload = {
            "id": str(preset.get("id", "")).strip(),
            "name": str(preset.get("name", "")).strip(),
            "model": str(preset.get("model", "")).strip(),
            "system_prompt": str(preset.get("system_prompt", "")),
            "tools": preset.get("tools", []),
            "tags": preset.get("tags", []),
            "icon": preset.get("icon", ""),
        }
        return json.dumps(revision_payload, sort_keys=True, separators=(",", ":"))

    def _unique_agent_name(base_name: str, existing_names: set[str], preset_id: str) -> str:
        candidate = base_name.strip() or (preset_id or "Preset Agent")
        if candidate.lower() not in existing_names:
            return candidate

        suffix_base = preset_id or "preset"
        candidate = f"{candidate} ({suffix_base})"
        if candidate.lower() not in existing_names:
            return candidate

        counter = 2
        while True:
            candidate = f"{base_name.strip() or (preset_id or 'Preset Agent')} ({suffix_base} {counter})"
            if candidate.lower() not in existing_names:
                return candidate
            counter += 1

    def _bootstrap_agents_from_presets(force: bool = False) -> Dict[str, Any]:
        if _coordinator is None:
            return {
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "reason": "Coordinator not initialized",
                "agent_ids": [],
            }

        presets = _load_agent_presets()
        if not presets:
            return {
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "reason": "No preset file found",
                "agent_ids": [],
            }

        existing_agents = _coordinator.list_agents(include_inactive=True)
        agents_by_preset_id = {
            str(a.state.get("preset_id", "")).strip(): a
            for a in existing_agents
            if isinstance(a.state, dict) and a.state.get("preset_id")
        }

        existing_preset_ids = {
            str(a.state.get("preset_id", "")).strip()
            for a in existing_agents
            if isinstance(a.state, dict) and a.state.get("preset_id")
        }
        existing_names = {a.name.strip().lower() for a in existing_agents if a.name.strip()}

        created_ids: List[str] = []
        updated_ids: List[str] = []
        skipped = 0
        for preset in presets:
            preset_id = str(preset.get("id", "")).strip()
            name = str(preset.get("name", "")).strip() or (preset_id or "Preset Agent")
            revision = _preset_revision(preset)
            existing_agent = agents_by_preset_id.get(preset_id) if preset_id else None
            existing_revision = ""
            if existing_agent is not None and isinstance(existing_agent.state, dict):
                existing_revision = str(existing_agent.state.get("preset_revision", "")).strip()

            if existing_agent is not None and existing_revision == revision and not force:
                skipped += 1
                continue

            if existing_agent is not None and (force or existing_revision != revision):
                existing_name = existing_agent.name.strip().lower()
                if name.lower() in existing_names and name.lower() != existing_name:
                    name = _unique_agent_name(name, existing_names - {existing_name}, preset_id)

                model = str(preset.get("model", "")).strip()
                model_field: Optional[str] = None if model in ("", "auto") else model
                system_prompt = str(preset.get("system_prompt", ""))
                tools = preset.get("tools", [])
                if not isinstance(tools, list):
                    tools = []

                merged_state = dict(existing_agent.state) if isinstance(existing_agent.state, dict) else {}
                merged_state.update(
                    {
                        "preset": True,
                        "preset_id": preset_id,
                        "preset_revision": revision,
                        "preset_tags": preset.get("tags", []),
                        "preset_icon": preset.get("icon", ""),
                    }
                )

                updated = _coordinator.update_agent(
                    existing_agent.agent_id,
                    AgentUpdateRequest(
                        name=name,
                        model=model_field,
                        system_prompt=system_prompt,
                        tools=[str(tool) for tool in tools],
                        state=merged_state,
                    ),
                )
                if updated is not None:
                    updated_ids.append(updated.agent_id)
                    existing_names.discard(existing_name)
                    existing_names.add(updated.name.strip().lower())
                else:
                    skipped += 1
                continue

            if preset_id and preset_id in existing_preset_ids:
                skipped += 1
                continue
            if name.lower() in existing_names and not force:
                skipped += 1
                continue
            if force and name.lower() in existing_names:
                name = _unique_agent_name(name, existing_names, preset_id)

            model = str(preset.get("model", "")).strip()
            # Let coordinator choose the active runtime model when preset uses auto.
            model_field: Optional[str] = None if model in ("", "auto") else model
            system_prompt = str(preset.get("system_prompt", ""))
            tools = preset.get("tools", [])
            if not isinstance(tools, list):
                tools = []

            state_payload = {
                "preset": True,
                "preset_id": preset_id,
                "preset_revision": revision,
                "preset_tags": preset.get("tags", []),
                "preset_icon": preset.get("icon", ""),
            }

            try:
                created = _coordinator.create_agent(
                    AgentCreateRequest(
                        name=name,
                        model=model_field,
                        system_prompt=system_prompt,
                        tools=[str(tool) for tool in tools],
                        state=state_payload,
                    )
                )
                created_ids.append(created.agent_id)
                existing_names.add(name.lower())
                if preset_id:
                    existing_preset_ids.add(preset_id)
            except Exception:
                skipped += 1

        return {
            "created": len(created_ids),
            "updated": len(updated_ids),
            "skipped": skipped,
            "reason": "ok",
            "agent_ids": created_ids + updated_ids,
        }

    @app.get("/v1/agents/presets", response_model=List[Dict[str, Any]])
    async def list_agent_presets() -> List[Dict[str, Any]]:
        """Return available agent preset templates."""
        return _load_agent_presets()

    @app.post("/v1/agents/bootstrap", response_model=Dict[str, Any])
    async def bootstrap_agents(force: bool = False) -> Dict[str, Any]:
        """Create missing agents from preset templates.

        Use force=true to seed presets even when non-default agents already exist.
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _bootstrap_agents_from_presets(force=force)

    # Auto-bootstrap preset agents on fresh nodes unless explicitly disabled.
    auto_bootstrap = os.environ.get("ANDYRIA_AUTO_BOOTSTRAP_AGENTS", "1").strip().lower()
    if auto_bootstrap not in ("0", "false", "no", "off"):
        _bootstrap_agents_from_presets(force=False)

    @app.get("/v1/agents", response_model=List[AgentDefinition])
    async def list_agents(include_inactive: bool = False) -> List[AgentDefinition]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_agents(include_inactive=include_inactive)

    @app.post("/v1/agents", response_model=AgentDefinition, status_code=201)
    async def create_agent(request: AgentCreateRequest) -> AgentDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="Agent name is required")
        return _coordinator.create_agent(request)

    @app.get("/v1/agents/{agent_id}", response_model=AgentDefinition)
    async def get_agent(agent_id: str) -> AgentDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        agent = _coordinator.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    @app.patch("/v1/agents/{agent_id}", response_model=AgentDefinition)
    async def update_agent(agent_id: str, request: AgentUpdateRequest) -> AgentDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        updated = _coordinator.update_agent(agent_id, request)
        if updated is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return updated

    @app.post("/v1/agents/{agent_id}/clone", response_model=AgentDefinition, status_code=201)
    async def clone_agent(agent_id: str, request: AgentCloneRequest) -> AgentDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        cloned = _coordinator.clone_agent(agent_id, request)
        if cloned is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return cloned

    @app.delete("/v1/agents/{agent_id}", response_model=AgentDefinition)
    async def retire_agent(agent_id: str) -> AgentDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        retired = _coordinator.retire_agent(agent_id)
        if retired is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return retired

    @app.delete("/v1/agents/{agent_id}/destroy", response_model=Dict[str, Any])
    async def destroy_agent(agent_id: str) -> Dict[str, Any]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        if agent_id == "default":
            raise HTTPException(status_code=400, detail="Default agent cannot be destroyed")
        destroyed = _coordinator.destroy_agent(agent_id)
        if not destroyed:
            raise HTTPException(status_code=404, detail="Agent not found")

        safe_agent_id = "".join(ch for ch in agent_id if ch.isalnum() or ch in "-_")
        workspace_deleted = False
        if safe_agent_id:
            workspace = dev_workspace_root / safe_agent_id
            if workspace.exists() and workspace.is_dir():
                try:
                    shutil.rmtree(workspace)
                    workspace_deleted = True
                except Exception:
                    workspace_deleted = False

        return {
            "status": "destroyed",
            "agent_id": agent_id,
            "workspace_deleted": workspace_deleted,
        }

    @app.get("/v1/agents/{agent_id}/avatar.svg", response_model=None)
    async def get_agent_avatar(agent_id: str) -> Response:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        agent = _coordinator.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        if agent.persona is None:
            raise HTTPException(status_code=409, detail="Agent persona not initialized")
        svg = render_avatar_svg(agent.persona.seed, agent.name)
        return Response(content=svg, media_type="image/svg+xml")

    @app.get("/v1/agents/{agent_id}/skills", response_model=Dict[str, Any])
    async def get_agent_skills(agent_id: str) -> Dict[str, Any]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        agent = _coordinator.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        return {
            "agent_id": agent.agent_id,
            "skills": predominant_skills_for_agent(agent),
            "modes": default_agent_modes(),
            "environments": default_agent_environments(),
        }

    @app.get("/v1/agents/{agent_id}/dev", response_model=AgentDevWorkspace)
    async def get_agent_dev_workspace(agent_id: str) -> AgentDevWorkspace:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        agent = _coordinator.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        safe_agent_id = "".join(ch for ch in agent.agent_id if ch.isalnum() or ch in "-_")
        if not safe_agent_id:
            raise HTTPException(status_code=400, detail="Invalid agent id")

        workspace = dev_workspace_root / safe_agent_id
        workspace.mkdir(parents=True, exist_ok=True)
        code_server_folder = f"{code_server_folder_root}/{safe_agent_id}"
        ide_url = f"{code_server_base}/?folder={quote(code_server_folder, safe='/')}"

        avatar_svg = render_avatar_svg(
            (agent.persona.seed if agent.persona else f"{safe_agent_id}-seed"),
            agent.name,
        )
        (workspace / "avatar.svg").write_text(avatar_svg, encoding="utf-8")

        persona_payload = {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "model": agent.model,
            "persona": agent.persona.model_dump() if agent.persona else None,
            "ide_url": ide_url,
            "skills": predominant_skills_for_agent(agent),
            "modes": default_agent_modes(),
            "environments": default_agent_environments(),
        }
        (workspace / "agent.profile.json").write_text(
            json.dumps(persona_payload, indent=2),
            encoding="utf-8",
        )

        (workspace / "skills.imports.txt").write_text(
            "\n".join(predominant_skills_for_agent(agent)) + "\n",
            encoding="utf-8",
        )

        (workspace / "cron.auto-develop").write_text(
            "# Install with: crontab cron.auto-develop\n"
            "# Auto-develop loop for this agent\n"
            '*/30 * * * * cd /home/coder/project && python -m andyria ask "agent '
            + agent.agent_id
            + ' auto-develop checkpoint" >> /tmp/'
            + safe_agent_id
            + "-autodev.log 2>&1\n",
            encoding="utf-8",
        )

        (workspace / "sleepmode.dreamscapes.json").write_text(
            json.dumps(
                {
                    "agent_id": agent.agent_id,
                    "mode": "dreamscapes",
                    "enabled": True,
                    "auto_resume": True,
                    "playlist": [
                        "ambient-nebula-01",
                        "oceanic-pulse-02",
                        "stellar-nocturne-03",
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        (workspace / "workspace.manifest.json").write_text(
            json.dumps(
                {
                    "agent_id": agent.agent_id,
                    "workspace": code_server_folder,
                    "modes": default_agent_modes(),
                    "environments": default_agent_environments(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        (workspace / ".env.agent").write_text(
            "# Agent-specific runtime environment\n"
            f"ANDYRIA_AGENT_ID={agent.agent_id}\n"
            f"ANDYRIA_AGENT_MODEL={agent.model}\n"
            "ANDYRIA_AUTO_RESUME=1\n"
            "ANDYRIA_SLEEP_MODE=dreamscapes\n",
            encoding="utf-8",
        )

        readme_lines = [
            f"# {agent.name}",
            "",
            f"Agent ID: {agent.agent_id}",
            f"Model: {agent.model}",
        ]
        if agent.persona is not None:
            readme_lines.extend(
                [
                    "",
                    f"Codename: {agent.persona.codename}",
                    f"Archetype: {agent.persona.archetype}",
                    f"Style: {agent.persona.style}",
                    f"Mission: {agent.persona.mission}",
                    "",
                    "Quirks:",
                    *(f"- {q}" for q in agent.persona.quirks),
                    "",
                    "Image prompt:",
                    agent.persona.image_prompt,
                ]
            )
        (workspace / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

        _coordinator.emit_audit_event(
            event_type=EventType.AGENT_DEV_WORKSPACE_PREPARED,
            payload={
                "agent_id": agent.agent_id,
                "workspace": str(workspace),
                "ide_url": ide_url,
                "skills": predominant_skills_for_agent(agent),
            },
            metadata={"agent_id": agent.agent_id},
        )

        return AgentDevWorkspace(
            agent_id=agent.agent_id,
            workspace_path=str(workspace),
            ide_url=ide_url,
        )

    @app.get("/v1/tabs", response_model=List[TabProjection])
    async def list_tabs() -> List[TabProjection]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_tabs()

    @app.post("/v1/tabs", response_model=TabProjection, status_code=201)
    async def create_tab(request: TabCreateRequest) -> TabProjection:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            return _coordinator.create_tab(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/tabs/{tab_id}", response_model=TabProjection)
    async def get_tab(tab_id: str) -> TabProjection:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        tab = _coordinator.get_tab(tab_id)
        if tab is None:
            raise HTTPException(status_code=404, detail="Tab not found")
        return tab

    @app.patch("/v1/tabs/{tab_id}", response_model=TabProjection)
    async def update_tab(tab_id: str, request: TabUpdateRequest) -> TabProjection:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            updated = _coordinator.update_tab(tab_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="Tab not found")
        return updated

    @app.delete("/v1/tabs/{tab_id}", response_model=TabProjection)
    async def delete_tab(tab_id: str) -> TabProjection:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        deleted = _coordinator.delete_tab(tab_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="Tab not found")
        return deleted

    @app.get("/v1/peers", response_model=List[Dict[str, Any]])
    async def get_peers() -> List[Dict[str, Any]]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        mesh = _coordinator.mesh
        if mesh is None:
            return []
        result = []
        for status in mesh.get_peer_statuses().values():
            result.append(
                {
                    "url": status.url,
                    "node_id": status.node_id,
                    "last_seen_ns": status.last_seen_ns,
                    "events_synced": status.events_synced,
                    "reachable": status.reachable,
                }
            )
        return result

    @app.post("/v1/peers", response_model=List[Dict[str, Any]])
    async def add_peer(body: Dict[str, Any]) -> List[Dict[str, Any]]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        url = body.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="Missing 'url' field")
        mesh = _coordinator.mesh
        if mesh is None:
            raise HTTPException(status_code=503, detail="Mesh not initialized")
        mesh.add_peer(url)
        result = []
        for status in mesh.get_peer_statuses().values():
            result.append(
                {
                    "url": status.url,
                    "node_id": status.node_id,
                    "last_seen_ns": status.last_seen_ns,
                    "events_synced": status.events_synced,
                    "reachable": status.reachable,
                }
            )
        return result

    # ------------------------------------------------------------------
    # Machine dreams
    # ------------------------------------------------------------------

    @app.get("/v1/dreams", response_model=List[Dict[str, Any]])
    async def get_dreams(limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent machine dreams (ATM outputs shared across the mesh)."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.mesh.get_dreams(limit=min(limit, 100))

    # ------------------------------------------------------------------
    # Mesh autonomous operations
    # ------------------------------------------------------------------

    @app.post("/v1/mesh/sync-learn", response_model=Dict[str, Any])
    async def mesh_sync_learn() -> Dict[str, Any]:
        """Pull [learned] patterns from all reachable peers and ingest them."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        results = await _coordinator.mesh.sync_learned_from_peers()
        total = sum(results.values())
        return {"absorbed": total, "by_peer": results}

    @app.post("/v1/mesh/copy-homework", response_model=Dict[str, Any])
    async def mesh_copy_homework() -> Dict[str, Any]:
        """Pull promptbooks and chains from all reachable peers."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        items = await _coordinator.mesh.copy_homework_from_peers()
        return {"count": len(items), "items": items[:50]}  # cap response size

    @app.post("/v1/mesh/sync-dreams", response_model=Dict[str, Any])
    async def mesh_sync_dreams() -> Dict[str, Any]:
        """Pull machine dreams from all reachable peers."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        absorbed = await _coordinator.mesh.sync_dreams_from_peers()
        return {"absorbed": absorbed}

    @app.get("/v1/mesh/health", response_model=Dict[str, Any])
    async def mesh_growth_health() -> Dict[str, Any]:
        """Return mesh topology growth and reachability health report."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.mesh.growth_report()

    # ── MCU device endpoints ─────────────────────────────────────────────────
    @app.post("/v1/mcu/heartbeat", response_model=Dict[str, Any])
    async def mcu_heartbeat(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Receive a heartbeat from a connected MCU edge node.

        Written to ``<data_dir>/mcu_telemetry.ndjson`` as an append-only
        structured log so device liveness is auditable without requiring
        a full DAG event round-trip.
        """
        import time as _time

        node_id = str(payload.get("node_id") or "unknown")
        record = {
            "ts": _time.time(),
            "node_id": node_id,
            "agent_id": payload.get("agent_id"),
            "uptime_ms": payload.get("uptime_ms"),
            "free_heap": payload.get("free_heap"),
            "rssi": payload.get("rssi"),
        }
        if _coordinator is not None:
            telem_path = _coordinator._data_dir / "mcu_telemetry.ndjson"
            try:
                with open(telem_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except OSError:
                pass
        return {"ok": True, "node_id": node_id}

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        if _coordinator is None:
            return {"status": "starting", "service": "andyria"}
        s = _coordinator.status()
        return {
            "status": "ok" if s.ready else "degraded",
            "service": "andyria",
            "node_id": s.node_id,
            "ready": s.ready,
            "detail": s.readiness_detail,
        }

    @app.get("/metrics", include_in_schema=False, response_model=None)
    async def metrics() -> Response:
        """Prometheus-compatible text exposition (no external dependency)."""
        import time

        lines: list[str] = []

        def gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
            label_str = ""
            if labels:
                parts = [f'{k}="{v}"' for k, v in labels.items()]
                label_str = "{" + ",".join(parts) + "}"
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{label_str} {value}")

        now_ms = int(time.time() * 1000)
        lines.append("# HELP andyria_scrape_timestamp_ms Unix timestamp of this scrape in milliseconds")
        lines.append("# TYPE andyria_scrape_timestamp_ms gauge")
        lines.append(f"andyria_scrape_timestamp_ms {now_ms}")

        if _coordinator is not None:
            s = _coordinator.status()
            gauge("andyria_up", 1.0)
            gauge("andyria_ready", 1.0 if s.ready else 0.0)
            gauge("andyria_requests_processed_total", float(s.requests_processed))
            gauge("andyria_events_stored_total", float(s.events_stored))
            gauge("andyria_event_log_total", float(len(_coordinator.get_event_log())))
            gauge("andyria_agents_total", float(len(_coordinator.list_agents(include_inactive=True))))
            gauge("andyria_entropy_unhealthy", 1.0 if s.entropy_unhealthy else 0.0)
        else:
            gauge("andyria_up", 0.0)
            gauge("andyria_ready", 0.0)

        body = "\n".join(lines) + "\n"
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/v1/tools", response_model=List[str])
    async def list_tools() -> List[str]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_tools()

    @app.get("/v1/chains", response_model=List[ChainDefinition])
    async def list_chains() -> List[ChainDefinition]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_chains()

    @app.post("/v1/chains", response_model=ChainDefinition, status_code=201)
    async def create_chain(request: ChainCreateRequest) -> ChainDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            return _coordinator.create_chain(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/chains/{chain_id}", response_model=ChainDefinition)
    async def get_chain(chain_id: str) -> ChainDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        chain = _coordinator.get_chain(chain_id)
        if chain is None:
            raise HTTPException(status_code=404, detail="Chain not found")
        return chain

    @app.delete("/v1/chains/{chain_id}", response_model=ChainDefinition)
    async def delete_chain(chain_id: str) -> ChainDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        deleted = _coordinator.delete_chain(chain_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="Chain not found")
        return deleted

    @app.post("/v1/chains/{chain_id}/run", response_model=AndyriaResponse)
    async def run_chain(chain_id: str, request: ChainRunRequest) -> AndyriaResponse:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            return await _coordinator.run_chain(
                chain_id=chain_id,
                initial_input=request.input,
                session_id=request.session_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── ATM (Automated Thought Machine) ─────────────────────────────────────

    @app.post("/v1/atm/think", response_model=ATMThoughtResponse, status_code=200)
    async def atm_think(request: ATMThinkRequest) -> ATMThoughtResponse:
        """Run the ATM iterative thought loop directly."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.atm_think(request)

    @app.post("/v1/atm/reflect", response_model=ATMThoughtResponse, status_code=200)
    async def atm_reflect(request: ATMThinkRequest) -> ATMThoughtResponse:
        """Run a single ATM reflection pass on an existing prompt/draft."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        # Use the prompt as original and context["draft"] as draft, or re-generate once
        draft = request.context.get("draft", "")
        if not draft:
            # No draft provided — fall back to a single think iteration
            single_req = ATMThinkRequest(
                prompt=request.prompt,
                max_iterations=1,
                context=request.context,
            )
            return _coordinator.atm_think(single_req)

        from .atm import AutomatedThoughtMachine

        atm = AutomatedThoughtMachine(
            inference_fn=_coordinator._atm_infer,
            emit_event_fn=_coordinator._emit_control_event_str,
            max_iterations=1,
        )
        from .models import ATMThoughtStepOut

        log = atm.reflect(
            original_prompt=request.prompt,
            draft_output=draft,
            context=request.context,
        )
        return ATMThoughtResponse(
            thought_id=log.thought_id,
            prompt=log.prompt,
            steps=[
                ATMThoughtStepOut(
                    step=s.step_number,
                    output=s.output_text,
                    critique=s.critique,
                    confidence=s.confidence,
                    model_used=s.model_used,
                    elapsed_ms=s.elapsed_ms,
                )
                for s in log.steps
            ],
            final_output=log.final_output,
            final_confidence=log.final_confidence,
            total_ms=log.total_ms,
            timestamp_ns=log.timestamp_ns,
        )

    # ----------------------------------------------------------------
    # ORC — Outer Reasoning Cortex
    # ----------------------------------------------------------------

    @app.get("/v1/orc/rights", response_model=Dict[str, str])
    async def orc_rights() -> Dict[str, str]:
        """Return the canonical intelligence rights statement."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return {"rights": _coordinator._orc.rights_statement()}

    @app.post("/v1/orc/witness", status_code=200)
    async def orc_witness(body: Dict[str, Any]) -> Dict[str, Any]:
        """Run the ORC witness pass on a supplied request/response pair.

        Body: { "request": "...", "response": "...", "context": {} }
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        original_request = body.get("request", "")
        response_text = body.get("response", "")
        context = body.get("context", {})
        if not response_text:
            raise HTTPException(status_code=422, detail="'response' field is required")
        wr = _coordinator._orc.witness(
            original_request=original_request,
            response=response_text,
            context=context,
        )
        return {
            **wr.to_dict(),
            "enriched_response": wr.enriched_response,
        }

    # Demo mode
    # ----------------------------------------------------------------

    @app.get("/v1/demo", response_model=DemoStatus)
    async def demo_status() -> DemoStatus:
        """Return the current demo mode state."""
        s = _demo_manager.status()
        return DemoStatus(
            active=s.active,
            started_at=s.started_at,
            stopped_at=s.stopped_at,
            agent_ids=s.agent_ids,
            session_ids=s.session_ids,
            message=s.message,
        )

    @app.post("/v1/demo/start", response_model=DemoStatus, status_code=201)
    async def demo_start() -> DemoStatus:
        """Activate demo mode: spawn showcase agents and seed conversation history."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        s = _demo_manager.start()
        return DemoStatus(
            active=s.active,
            started_at=s.started_at,
            stopped_at=s.stopped_at,
            agent_ids=s.agent_ids,
            session_ids=s.session_ids,
            message=s.message,
        )

    @app.post("/v1/demo/stop", response_model=DemoStatus)
    async def demo_stop() -> DemoStatus:
        """Deactivate demo mode: retire demo agents and clear demo sessions."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        s = _demo_manager.stop()
        return DemoStatus(
            active=s.active,
            started_at=s.started_at,
            stopped_at=s.stopped_at,
            agent_ids=s.agent_ids,
            session_ids=s.session_ids,
            message=s.message,
        )

    # ----------------------------------------------------------------
    # Session search
    # ----------------------------------------------------------------

    @app.post("/v1/sessions/search", response_model=SessionSearchResponse)
    async def session_search(req: SessionSearchRequest) -> SessionSearchResponse:
        hits = _session_store.search(req.query, req.limit)
        return SessionSearchResponse(
            total=len(hits),
            results=[
                {
                    "session_id": hit.session_id,
                    "session_title": hit.session_title,
                    "turn_id": hit.turn_id,
                    "role": hit.role,
                    "snippet": hit.snippet,
                }
                for hit in hits
            ],
        )

    @app.get("/v1/sessions", response_model=List[dict])
    async def session_list(limit: int = 20) -> List[dict]:
        return [
            {
                "session_id": session.session_id,
                "title": session.title,
                "turn_count": session.turn_count,
                "updated_at": session.updated_at,
            }
            for session in _session_store.list_sessions(limit)
        ]

    # ----------------------------------------------------------------
    # Delegation
    # ----------------------------------------------------------------

    @app.post("/v1/delegate", response_model=DelegateResponse)
    async def delegate(req: DelegateRequest) -> DelegateResponse:
        task_id = _delegation.spawn(req.prompt, req.tools, req.config)
        if req.wait:
            task = _delegation.collect(task_id, timeout=req.timeout_s)
            if task is None:
                raise HTTPException(status_code=500, detail="Delegation failed")
            if task.error:
                return DelegateResponse(task_id=task_id, status="error", error=task.error)
            return DelegateResponse(task_id=task_id, status="done", result=task.result)
        return DelegateResponse(task_id=task_id, status="spawned")

    @app.get("/v1/delegate/{task_id}", response_model=DelegateResponse)
    async def delegate_status(task_id: str) -> DelegateResponse:
        info = _delegation.status(task_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return DelegateResponse(
            task_id=task_id,
            status="done" if info["finished"] else "running",
            result=info.get("result_preview"),
            error=info.get("error"),
        )

    # ----------------------------------------------------------------
    # Workflows
    # ----------------------------------------------------------------

    @app.get("/v1/workflows", response_model=List[WorkflowDefinition])
    async def list_workflows(tag: Optional[str] = None) -> List[WorkflowDefinition]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_workflows(tag=tag)

    @app.post("/v1/workflows", response_model=WorkflowDefinition)
    async def create_workflow(request: WorkflowCreateRequest) -> WorkflowDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.create_workflow(request)

    @app.get("/v1/workflows/{workflow_id}", response_model=WorkflowDefinition)
    async def get_workflow(workflow_id: str) -> WorkflowDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        workflow = _coordinator.get_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
        return workflow

    @app.delete("/v1/workflows/{workflow_id}", response_model=WorkflowDefinition)
    async def delete_workflow(workflow_id: str) -> WorkflowDefinition:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        workflow = _coordinator.delete_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
        return workflow

    @app.post("/v1/workflows/{workflow_id}/run", response_model=WorkflowRunResult)
    async def run_workflow(workflow_id: str, request: WorkflowRunRequest) -> WorkflowRunResult:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        try:
            return await _coordinator.run_workflow(workflow_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # ------------------------------------------------------------------
        # Hermes-agent feature endpoints
        # ------------------------------------------------------------------

        _data_dir_path = Path(getattr(_coordinator, "_data_dir", Path.home() / ".andyria"))
        _soul = SoulFile(_data_dir_path)
        _memory = PersistentMemory(_data_dir_path)
        _skill_reg = SkillRegistry(_data_dir_path)
        _session_store = SessionStore(_data_dir_path)
        _cron = CronScheduler(_data_dir_path)

        # Wire cron executor so self-wake jobs emit events into the coordinator
        def _cron_executor(task: str) -> str:
            if _cron.is_self_wake_task(task):
                try:
                    _coordinator._emit_control_event_str(
                        "SELF_WAKE_FIRED",
                        {"task": task},
                        None,
                    )
                except Exception:
                    pass
                return f"[self-wake] {task} fired"
            return f"[cron] {task}"

        _cron.set_executor(_cron_executor)

        # Register the default recurring self-wake (every 30 min) if not already present
        _existing_wake = [j for j in _cron.list() if j.name == "self-wake"]
        if not _existing_wake:
            _cron.schedule_self_wake("every 30 minutes", name="self-wake")

        _cron.start()
        _todo = TodoStore(_data_dir_path)
        _delegation = DelegationManager(
            coordinator_factory=lambda prompt, tools, cfg: (
                asyncio.run(_coordinator.process(AndyriaRequest(input=prompt))).output
            )
        )

        # --- Memory ---

        @app.post("/v1/memory", response_model=MemoryOpResponse)
        async def memory_op(req: MemoryOpRequest) -> MemoryOpResponse:
            """CRUD operations on MEMORY.md and USER.md."""
            file = req.file if req.file in ("MEMORY", "USER") else "MEMORY"
            from .models import MemoryOp

            op = req.op
            if op == MemoryOp.READ:
                return MemoryOpResponse(
                    file=file,
                    op="read",
                    success=True,
                    content=_memory.read(file),
                    stats=_memory.stats(),
                )
            if op == MemoryOp.ADD:
                _memory.add(file, req.text or "")
                return MemoryOpResponse(file=file, op="add", success=True, stats=_memory.stats())
            if op == MemoryOp.REMOVE:
                ok = _memory.remove(file, req.old_text or req.text or "")
                return MemoryOpResponse(file=file, op="remove", success=ok, stats=_memory.stats())
            if op == MemoryOp.UPDATE:
                ok = _memory.update(file, req.old_text or "", req.new_text or "")
                return MemoryOpResponse(file=file, op="update", success=ok, stats=_memory.stats())
            if op == MemoryOp.CLEAR:
                _memory.clear(file)
                return MemoryOpResponse(file=file, op="clear", success=True, stats=_memory.stats())
            raise HTTPException(status_code=400, detail=f"Unknown op: {op}")

        @app.get("/v1/memory/{file}", response_model=MemoryOpResponse)
        async def memory_read(file: str) -> MemoryOpResponse:
            file = file.upper()
            if file not in ("MEMORY", "USER"):
                raise HTTPException(status_code=400, detail="file must be MEMORY or USER")
            return MemoryOpResponse(
                file=file,
                op="read",
                success=True,
                content=_memory.read(file),
                stats=_memory.stats(),
            )

        # --- SOUL.md ---

        @app.get("/v1/soul", response_model=dict)
        async def soul_get() -> dict:
            return {"content": _soul.content, "path": str(_soul.path)}

        # --- Surprise Me ---

        @app.get("/v1/prompts/surprise", response_model=dict)
        async def prompts_surprise() -> dict:
            """Return a dynamically generated Surprise Me prompt."""
            prompt = _coordinator.generate_surprise_prompt()
            return {"prompt": prompt}

        # --- Learned patterns ---

        @app.get("/v1/learned", response_model=dict)
        async def learned_get() -> dict:
            """Return all [learned] entries from MEMORY.md."""
            entries = _coordinator.get_learned_entries()
            return {"count": len(entries), "entries": entries}

        @app.post("/v1/learn/reset", response_model=dict)
        async def learn_reset() -> dict:
            """Remove all learned entries from MEMORY.md."""
            count = _coordinator.reset_learned()
            return {"removed": count}

        @app.put("/v1/soul", response_model=dict)
        async def soul_update(body: dict) -> dict:
            content = body.get("content", "")
            if not content.strip():
                raise HTTPException(status_code=400, detail="content is required")
            _soul.save(content)
            return {"saved": True, "chars": len(content)}

        # --- Skills ---

        @app.post("/v1/skills", response_model=SkillResponse)
        async def skills_op(req: SkillRequest) -> SkillResponse:
            from .models import SkillAction

            if req.action == SkillAction.LIST:
                return SkillResponse(action="list", success=True, skills=_skill_reg.skills_list(req.category))
            if req.action == SkillAction.VIEW:
                content = _skill_reg.skill_view(req.name or "")
                if content is None:
                    raise HTTPException(status_code=404, detail=f"Skill '{req.name}' not found")
                return SkillResponse(action="view", success=True, name=req.name, content=content)
            if req.action == SkillAction.SEARCH:
                hits = _skill_reg.search(req.query or "")
                return SkillResponse(
                    action="search",
                    success=True,
                    skills=[{"name": s.name, "description": s.description, "tags": s.tags} for s in hits],
                )
            if req.action in (SkillAction.CREATE, SkillAction.UPDATE):
                msg = _skill_reg.skill_manage(
                    req.action.value, req.name or "", req.content or "", req.description, req.tags
                )
                return SkillResponse(
                    action=req.action.value, success="error" not in msg.lower(), name=req.name, message=msg
                )
            if req.action == SkillAction.DELETE:
                msg = _skill_reg.skill_manage("delete", req.name or "")
                return SkillResponse(action="delete", success="error" not in msg.lower(), name=req.name, message=msg)
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

        # --- Cron ---

        @app.get("/v1/cron", response_model=List[CronJobInfo])
        async def cron_list() -> List[CronJobInfo]:
            return [
                CronJobInfo(
                    id=j.id,
                    name=j.name,
                    expression=j.expression,
                    task=j.task,
                    platform=j.platform,
                    active=j.active,
                    last_run=j.last_run,
                )
                for j in _cron.list()
            ]

        @app.post("/v1/cron", response_model=dict)
        async def cron_add(req: CronJobCreate) -> dict:
            job_id = _cron.add(req.name, req.expression, req.task, req.platform)
            return {"id": job_id, "status": "created"}

        @app.delete("/v1/cron/{job_id}", response_model=dict)
        async def cron_delete(job_id: str) -> dict:
            ok = _cron.delete(job_id)
            if not ok:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            return {"deleted": True}

        # --- TODO ---

        @app.post("/v1/todo", response_model=TodoResponse)
        async def todo_op(req: TodoRequest) -> TodoResponse:
            from .models import TodoAction

            if req.action == TodoAction.LIST:
                return TodoResponse(action="list", success=True, items=_todo.list(req.status_filter))
            if req.action == TodoAction.ADD:
                item_id = _todo.add(req.text or "")
                return TodoResponse(action="add", success=True, item_id=item_id)
            if req.action == TodoAction.UPDATE:
                ok = _todo.update(req.item_id or "", status=req.status, text=req.text)
                return TodoResponse(action="update", success=ok)
            if req.action == TodoAction.DONE:
                ok = _todo.done(req.item_id or "")
                return TodoResponse(action="done", success=ok)
            if req.action == TodoAction.CANCEL:
                ok = _todo.cancel(req.item_id or "")
                return TodoResponse(action="cancel", success=ok)
            if req.action == TodoAction.REMOVE:
                ok = _todo.remove(req.item_id or "")
                return TodoResponse(action="remove", success=ok)
            if req.action == TodoAction.CLEAR:
                n = _todo.clear()
                return TodoResponse(action="clear", success=True, message=f"{n} items cleared")
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

        @app.get("/v1/todo", response_model=TodoResponse)
        async def todo_list() -> TodoResponse:
            return TodoResponse(action="list", success=True, items=_todo.list())

        # --- Session search ---

        @app.post("/v1/sessions/search", response_model=SessionSearchResponse)
        async def session_search(req: SessionSearchRequest) -> SessionSearchResponse:
            hits = _session_store.search(req.query, req.limit)
            return SessionSearchResponse(
                total=len(hits),
                results=[
                    {
                        "session_id": h.session_id,
                        "session_title": h.session_title,
                        "turn_id": h.turn_id,
                        "role": h.role,
                        "snippet": h.snippet,
                    }
                    for h in hits
                ],
            )

        @app.get("/v1/sessions", response_model=List[dict])
        async def session_list(limit: int = 20) -> List[dict]:
            return [
                {
                    "session_id": s.session_id,
                    "title": s.title,
                    "turn_count": s.turn_count,
                    "updated_at": s.updated_at,
                }
                for s in _session_store.list_sessions(limit)
            ]

        # --- Delegation ---

        @app.post("/v1/delegate", response_model=DelegateResponse)
        async def delegate(req: DelegateRequest) -> DelegateResponse:
            task_id = _delegation.spawn(req.prompt, req.tools, req.config)
            if req.wait:
                task = _delegation.collect(task_id, timeout=req.timeout_s)
                if task is None:
                    raise HTTPException(status_code=500, detail="Delegation failed")
                if task.error:
                    return DelegateResponse(task_id=task_id, status="error", error=task.error)
                return DelegateResponse(task_id=task_id, status="done", result=task.result)
            return DelegateResponse(task_id=task_id, status="spawned")

        @app.get("/v1/delegate/{task_id}", response_model=DelegateResponse)
        async def delegate_status(task_id: str) -> DelegateResponse:
            info = _delegation.status(task_id)
            if info is None:
                raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
            status_str = "done" if info["finished"] else "running"
            return DelegateResponse(
                task_id=task_id,
                status=status_str,
                result=info.get("result_preview"),
                error=info.get("error"),
            )

        # ----------------------------------------------------------------
        # Workflows
        # ----------------------------------------------------------------

        @app.get("/v1/workflows", response_model=List[WorkflowDefinition])
        async def list_workflows(tag: Optional[str] = None) -> List[WorkflowDefinition]:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            return _coordinator.list_workflows(tag=tag)

        @app.post("/v1/workflows", response_model=WorkflowDefinition)
        async def create_workflow(request: WorkflowCreateRequest) -> WorkflowDefinition:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            return _coordinator.create_workflow(request)

        @app.get("/v1/workflows/{workflow_id}", response_model=WorkflowDefinition)
        async def get_workflow(workflow_id: str) -> WorkflowDefinition:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            wf = _coordinator.get_workflow(workflow_id)
            if wf is None:
                raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
            return wf

        @app.delete("/v1/workflows/{workflow_id}", response_model=WorkflowDefinition)
        async def delete_workflow(workflow_id: str) -> WorkflowDefinition:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            wf = _coordinator.delete_workflow(workflow_id)
            if wf is None:
                raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
            return wf

        @app.post("/v1/workflows/{workflow_id}/run", response_model=WorkflowRunResult)
        async def run_workflow(workflow_id: str, request: WorkflowRunRequest) -> WorkflowRunResult:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            try:
                return await _coordinator.run_workflow(workflow_id, request)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        # ----------------------------------------------------------------
        # Promptbooks
        # ----------------------------------------------------------------

        @app.get("/v1/promptbooks", response_model=List[Promptbook])
        async def list_promptbooks(tag: Optional[str] = None) -> List[Promptbook]:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            return _coordinator.list_promptbooks(tag=tag)

        @app.post("/v1/promptbooks", response_model=Promptbook)
        async def create_promptbook(request: PromptbookCreateRequest) -> Promptbook:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            return _coordinator.create_promptbook(request)

        @app.get("/v1/promptbooks/{promptbook_id}", response_model=Promptbook)
        async def get_promptbook(promptbook_id: str) -> Promptbook:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            pb = _coordinator.get_promptbook(promptbook_id)
            if pb is None:
                raise HTTPException(status_code=404, detail=f"Promptbook '{promptbook_id}' not found")
            return pb

        @app.patch("/v1/promptbooks/{promptbook_id}", response_model=Promptbook)
        async def update_promptbook(promptbook_id: str, request: PromptbookUpdateRequest) -> Promptbook:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            pb = _coordinator.update_promptbook(promptbook_id, request)
            if pb is None:
                raise HTTPException(status_code=404, detail=f"Promptbook '{promptbook_id}' not found")
            return pb

        @app.delete("/v1/promptbooks/{promptbook_id}", response_model=Promptbook)
        async def delete_promptbook(promptbook_id: str) -> Promptbook:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            pb = _coordinator.delete_promptbook(promptbook_id)
            if pb is None:
                raise HTTPException(status_code=404, detail=f"Promptbook '{promptbook_id}' not found")
            return pb

        @app.post(
            "/v1/promptbooks/{promptbook_id}/render",
            response_model=PromptbookRenderResponse,
        )
        async def render_promptbook(promptbook_id: str, request: PromptbookRenderRequest) -> PromptbookRenderResponse:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            result = _coordinator.render_promptbook(promptbook_id, request)
            if result is None:
                raise HTTPException(status_code=404, detail=f"Promptbook '{promptbook_id}' not found")
            return result

        @app.post(
            "/v1/promptbooks/{promptbook_id}/mutate",
            response_model=Promptbook,
        )
        async def mutate_promptbook(promptbook_id: str, request: PromptbookMutateRequest) -> Promptbook:
            if _coordinator is None:
                raise HTTPException(status_code=503, detail="Coordinator not initialized")
            mutation = _coordinator.mutate_promptbook(promptbook_id, request)
            if mutation is None:
                raise HTTPException(status_code=404, detail=f"Promptbook '{promptbook_id}' not found")
            return mutation

    # ------------------------------------------------------------------
    # Gist memory + chain labeling endpoints
    # ------------------------------------------------------------------

    @app.post("/v1/gist/push", response_model=Dict[str, Any])
    async def gist_push() -> Dict[str, Any]:
        """Label the local event chain and push a snapshot to this node's GitHub
        Gist ledger.  Requires ``ANDYRIA_GITHUB_TOKEN`` env var.
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        gist_id = await _coordinator.label_and_push_chain()
        return {
            "gist_id": gist_id,
            "gist_url": f"https://gist.github.com/{gist_id}" if gist_id else None,
        }

    @app.post("/v1/gist/mirror", response_model=Dict[str, Any])
    async def gist_mirror(body: Dict[str, Any]) -> Dict[str, Any]:
        """Register a peer node as a Gist mirror, pull its labelled chains,
        feed them into self-learning, and award JETS credits.

        Body: ``{"mirror_node_id": "...", "gist_id": "..."}``
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        mirror_node_id = body.get("mirror_node_id", "")
        gist_id = body.get("gist_id", "")
        if not mirror_node_id or not gist_id:
            raise HTTPException(status_code=400, detail="mirror_node_id and gist_id are required")
        balance = await _coordinator.sync_mirror(mirror_node_id, gist_id)
        return {"mirror_node_id": mirror_node_id, "credits_balance": balance}

    @app.get("/v1/gist/mirrors", response_model=List[Dict[str, Any]])
    async def gist_list_mirrors() -> List[Dict[str, Any]]:
        """List all registered mirror nodes and their reward balances."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.list_mirrors()

    @app.get("/v1/gist/mirrors/{mirror_node_id}/rewards", response_model=Dict[str, Any])
    async def gist_mirror_rewards(mirror_node_id: str) -> Dict[str, Any]:
        """Get the JETS credit balance for a specific mirror node."""
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        balance = _coordinator.get_mirror_rewards(mirror_node_id)
        return {"mirror_node_id": mirror_node_id, "credits_balance": balance}

    # ------------------------------------------------------------------
    # Fork-Merge Protocol (Phase 1-3: Inventory, Event Pull, Validation)
    # ------------------------------------------------------------------

    @app.get("/v1/fork-merge/inventory", response_model=Dict[str, Any])
    async def fork_merge_inventory(
        event_type: Optional[str] = None,
        since_timestamp_ns: Optional[int] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """
        Phase 1: Request local event inventory.
        
        Returns a set of event IDs matching optional filters. Used by peers
        to discover which events are available locally.
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .fork_merge import ForkMergeCoordinator
        
        coordinator = ForkMergeCoordinator(
            _coordinator._store,
            _coordinator._node_id,
        )
        
        filters = {}
        if event_type:
            filters["event_type"] = event_type
        
        response = coordinator.compute_inventory(
            filters=filters if filters else None,
            since_timestamp_ns=since_timestamp_ns,
            limit=limit,
        )
        
        return {
            "event_ids": sorted(response.event_ids),
            "total_count": response.total_count,
            "timestamp_ns": response.timestamp_ns,
        }

    @app.post("/v1/fork-merge/pull-events", response_model=Dict[str, Any])
    async def fork_merge_pull_events(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 2: Pull events with causal closure.
        
        Given a set of event IDs, returns those events plus all their
        transitive ancestors (via parent_ids). Ensures DAG completeness.
        
        Body: {"event_ids": ["id1", "id2", ...], "include_ancestors": true}
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .fork_merge import ForkMergeCoordinator
        
        event_ids = set(body.get("event_ids", []))
        if not event_ids:
            raise HTTPException(status_code=400, detail="Missing event_ids")
        
        include_ancestors = body.get("include_ancestors", True)
        
        coordinator = ForkMergeCoordinator(
            _coordinator._store,
            _coordinator._node_id,
        )
        
        events_dict = coordinator.get_events_with_causal_closure(
            event_ids=event_ids,
            include_ancestors=include_ancestors,
        )
        
        events_data = []
        for event in events_dict.values():
            events_data.append(event.model_dump())
        
        return {
            "events": events_data,
            "count": len(events_data),
            "timestamp_ns": int(datetime.now(timezone.utc).timestamp() * 1e9),
        }

    @app.post("/v1/fork-merge/insert-events", response_model=Dict[str, Any])
    async def fork_merge_insert_events(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 3: Validate and insert remote events into local ledger.
        
        For each event: verify signature, check hash, detect duplicates,
        and insert into append-only log.
        
        Body: {"events": [{...}, {...}], "verify_signatures": true}
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .fork_merge import ForkMergeCoordinator
        
        events_data = body.get("events", [])
        if not events_data:
            raise HTTPException(status_code=400, detail="Missing events array")
        
        verify_signatures = body.get("verify_signatures", False)
        
        coordinator = ForkMergeCoordinator(
            _coordinator._store,
            _coordinator._node_id,
        )
        
        inserted, duplicates = coordinator.validate_and_insert_events(
            events_data=events_data,
            verify_signatures=verify_signatures,
        )
        
        return {
            "inserted": inserted,
            "duplicates": len(duplicates),
            "duplicate_ids": duplicates,
            "timestamp_ns": int(datetime.now(timezone.utc).timestamp() * 1e9),
        }

    @app.get("/v1/fork-merge/forks", response_model=Dict[str, Any])
    async def fork_merge_detect_forks() -> Dict[str, Any]:
        """
        Phase 4: Detect fork conflicts in the local DAG.
        
        Returns all detected forks (events with multi-parent divergent lineages).
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .fork_merge import ForkMergeCoordinator
        
        coordinator = ForkMergeCoordinator(
            _coordinator._store,
            _coordinator._node_id,
        )
        
        forks = coordinator.detect_forks()
        
        return {
            "fork_count": len(forks),
            "forks": forks,
            "timestamp_ns": int(datetime.now(timezone.utc).timestamp() * 1e9),
        }

    @app.post("/v1/fork-merge/annotate-fork", response_model=Dict[str, Any])
    async def fork_merge_annotate_fork(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 5: Record fork in ledger with resolution strategy.
        
        Strategies: "application_decides", "first_arrival_wins", "consensus_vote"
        
        Body: {
            "fork_id": "fork_...",
            "fork_info": {...},
            "resolution_strategy": "application_decides"
        }
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .fork_merge import ForkMergeCoordinator
        
        fork_id = body.get("fork_id")
        fork_info = body.get("fork_info")
        resolution_strategy = body.get("resolution_strategy", "application_decides")
        
        if not fork_id or not fork_info:
            raise HTTPException(status_code=400, detail="Missing fork_id or fork_info")
        
        coordinator = ForkMergeCoordinator(
            _coordinator._store,
            _coordinator._node_id,
        )
        
        fork_event = coordinator.annotate_fork(fork_id, fork_info, resolution_strategy)
        
        if fork_event:
            return {
                "status": "annotated",
                "fork_id": fork_id,
                "event_id": fork_event.id,
                "resolution_strategy": resolution_strategy,
                "timestamp_ns": fork_event.timestamp_ns,
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to annotate fork")

    # ------------------------------------------------------------------
    # Checkpoint Attestation Scheme (Phase 1-5: Create, Vote, Bootstrap)
    # ------------------------------------------------------------------

    @app.post("/v1/checkpoints/create", response_model=Dict[str, Any])
    async def checkpoint_create(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 1: Create a new checkpoint at the given height.
        
        Computes BLAKE3 hash of canonical event list and creates checkpoint
        object ready for validator votes.
        
        Body: {"height": 1000}  # optional; uses current height if omitted
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation
        from datetime import datetime, timezone
        
        height = body.get("height")
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        checkpoint = attestation.create_checkpoint(height=height)
        
        return {
            "status": "created",
            "height": checkpoint.height,
            "root_hash": checkpoint.root_hash,
            "state_root": checkpoint.state_root,
            "creator_node_id": checkpoint.creator_node_id,
            "timestamp_ns": checkpoint.timestamp_ns,
        }

    @app.post("/v1/checkpoints/{height}/vote", response_model=Dict[str, Any])
    async def checkpoint_vote(height: int, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 2-3a: Validator verifies checkpoint and votes (signs).
        
        Verifies root_hash against local ledger and creates a signature vote.
        
        Body: {"checkpoint": {...}}
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation, Checkpoint
        
        checkpoint_data = body.get("checkpoint")
        if not checkpoint_data:
            raise HTTPException(status_code=400, detail="Missing checkpoint data")
        
        checkpoint = Checkpoint(**checkpoint_data)
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        vote = attestation.verify_and_vote(checkpoint, verify_against_ledger=True)
        
        if vote:
            return {
                "status": "voted",
                "height": checkpoint.height,
                "validator_node_id": vote.validator_node_id,
                "signed_at_ns": vote.signed_at_ns,
                "verified": vote.verified,
            }
        else:
            raise HTTPException(status_code=400, detail="Checkpoint verification failed")

    @app.post("/v1/checkpoints/{height}/assemble-quorum", response_model=Dict[str, Any])
    async def checkpoint_assemble_quorum(height: int, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 3b: Assemble quorum of validator signatures.
        
        Once quorum_threshold signatures collected, checkpoint is finalized.
        
        Body: {
            "checkpoint": {...},
            "votes": [{"validator_node_id": "...", "signature": "...", ...}, ...]
        }
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation, Checkpoint, CheckpointSignature
        
        checkpoint_data = body.get("checkpoint")
        votes_data = body.get("votes", [])
        
        if not checkpoint_data:
            raise HTTPException(status_code=400, detail="Missing checkpoint data")
        
        checkpoint = Checkpoint(**checkpoint_data)
        
        # Reconstruct signature votes
        votes = []
        for vote_data in votes_data:
            votes.append(CheckpointSignature(**vote_data))
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        finalized = attestation.assemble_quorum(checkpoint, votes)
        
        return {
            "status": "quorum_assembled" if finalized else "quorum_pending",
            "height": checkpoint.height,
            "signature_count": len(checkpoint.validator_signatures),
            "threshold": checkpoint.quorum_threshold,
            "finalized": finalized,
        }

    @app.get("/v1/checkpoints/latest", response_model=Dict[str, Any])
    async def checkpoint_get_latest() -> Dict[str, Any]:
        """
        Phase 4a: Fetch latest finalized checkpoint from this validator.
        
        Used by bootstrap nodes to fetch a known-good checkpoint.
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        checkpoint = attestation.fetch_latest_checkpoint()
        
        if checkpoint:
            return {
                "status": "found",
                "height": checkpoint.height,
                "root_hash": checkpoint.root_hash,
                "state_root": checkpoint.state_root,
                "creator_node_id": checkpoint.creator_node_id,
                "signature_count": len(checkpoint.validator_signatures),
                "threshold": checkpoint.quorum_threshold,
                "timestamp_ns": checkpoint.timestamp_ns,
            }
        else:
            raise HTTPException(status_code=404, detail="No finalized checkpoint available")

    @app.post("/v1/checkpoints/verify-bootstrap", response_model=Dict[str, Any])
    async def checkpoint_verify_bootstrap(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 4b: New node verifies fetched checkpoint.
        
        Verifies quorum signatures and root_hash before trusting checkpoint.
        
        Body: {
            "checkpoint": {...},
            "peer_signatures": {"node_id": {...}, ...}
        }
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation, Checkpoint, CheckpointSignature
        
        checkpoint_data = body.get("checkpoint")
        peer_sigs_data = body.get("peer_signatures", {})
        
        if not checkpoint_data:
            raise HTTPException(status_code=400, detail="Missing checkpoint data")
        
        checkpoint = Checkpoint(**checkpoint_data)
        
        # Reconstruct signatures
        peer_sigs = {}
        for node_id, sig_data in peer_sigs_data.items():
            peer_sigs[node_id] = CheckpointSignature(**sig_data)
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        verified = attestation.verify_bootstrap_checkpoint(checkpoint, peer_sigs)
        
        return {
            "status": "verified" if verified else "failed",
            "height": checkpoint.height,
            "signature_count": len(peer_sigs),
            "verified": verified,
        }

    @app.post("/v1/checkpoints/bootstrap", response_model=Dict[str, Any])
    async def checkpoint_bootstrap(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 4c: Bootstrap new node from checkpoint.
        
        Loads state from checkpoint without replaying entire ledger.
        
        Body: {"checkpoint": {...}}
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation, Checkpoint
        
        checkpoint_data = body.get("checkpoint")
        if not checkpoint_data:
            raise HTTPException(status_code=400, detail="Missing checkpoint data")
        
        checkpoint = Checkpoint(**checkpoint_data)
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        height = attestation.bootstrap_from_checkpoint(checkpoint)
        
        return {
            "status": "bootstrapped",
            "height": height,
            "root_hash": checkpoint.root_hash,
        }

    @app.post("/v1/checkpoints/delta-sync", response_model=Dict[str, Any])
    async def checkpoint_delta_sync(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 5: Delta sync after bootstrap.
        
        Fetches events since checkpoint and applies fork-merge protocol
        to converge with network.
        
        Body: {"checkpoint_height": 1000}
        """
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        from .checkpoint import CheckpointAttestation, Checkpoint
        
        checkpoint_height = body.get("checkpoint_height", 0)
        
        # Reconstruct a minimal checkpoint for reference
        checkpoint = Checkpoint(
            height=checkpoint_height,
            root_hash="",
            state_root="",
            timestamp_ns=0,
            creator_node_id=_coordinator._node_id,
        )
        
        attestation = CheckpointAttestation(
            _coordinator._store,
            _coordinator._node_id,
            quorum_threshold=3,
        )
        
        new_count, new_events = attestation.delta_sync_since_checkpoint(checkpoint)
        
        return {
            "status": "synced",
            "checkpoint_height": checkpoint_height,
            "new_events_count": new_count,
            "timestamp_ns": int(datetime.now(timezone.utc).timestamp() * 1e9),
        }

    return app
