"""HTTP API for Andyria (FastAPI)."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response

from .coordinator import Coordinator
from .agent_features import (
    default_agent_environments,
    default_agent_modes,
    predominant_skills_for_agent,
)
from .models import (
    AgentCloneRequest,
    AgentCreateRequest,
    AgentDevWorkspace,
    AgentDefinition,
    AgentUpdateRequest,
    AndyriaRequest,
    AndyriaResponse,
    ATMThinkRequest,
    ATMThoughtResponse,
    ChainCreateRequest,
    ChainDefinition,
    ChainRunRequest,
    DemoStatus,
    Event,
    EventType,
    NodeConfig,
    NodeConfigUpdate,
    NodeStatus,
    SessionContext,
    TabCreateRequest,
    TabProjection,
    TabUpdateRequest,
)
from .persona import render_avatar_svg
from .demo import DemoManager

_coordinator: Optional[Coordinator] = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan manager — start/stop mesh on app startup/shutdown."""
    # Startup
    if _coordinator and _coordinator.mesh:
        await _coordinator.mesh.start()
    yield
    # Shutdown
    if _coordinator and _coordinator.mesh:
        await _coordinator.mesh.stop()


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
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Content-Type"],
    )

    static_dir = Path(__file__).resolve().parent / "static"
    index_file = static_dir / "index.html"
    default_dev_root = Path(getattr(_coordinator, "_data_dir", Path("."))) / "agent-dev"
    dev_workspace_root = Path(os.environ.get("ANDYRIA_AGENT_DEV_ROOT", str(default_dev_root)))
    _demo_manager = DemoManager(_coordinator)
    code_server_base = os.environ.get("ANDYRIA_CODE_SERVER_URL", "http://localhost:8080").rstrip("/")
    code_server_folder_root = os.environ.get(
        "ANDYRIA_CODE_SERVER_FOLDER_ROOT",
        "/home/coder/project/python/.agent-dev",
    ).rstrip("/")

    @app.get("/", include_in_schema=False, response_model=None)
    async def root():
        if index_file.exists():
            return FileResponse(index_file)
        return RedirectResponse(url="/docs")

    @app.post("/v1/infer", response_model=AndyriaResponse)
    async def infer(request: AndyriaRequest) -> AndyriaResponse:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return await _coordinator.process(request)

    @app.get("/v1/status", response_model=NodeStatus)
    async def status() -> NodeStatus:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.status()

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
            "*/30 * * * * cd /home/coder/project && python -m andyria ask \"agent "
            + agent.agent_id
            + " auto-develop checkpoint\" >> /tmp/"
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
            result.append({
                "url": status.url,
                "node_id": status.node_id,
                "last_seen_ns": status.last_seen_ns,
                "events_synced": status.events_synced,
                "reachable": status.reachable,
            })
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
            result.append({
                "url": status.url,
                "node_id": status.node_id,
                "last_seen_ns": status.last_seen_ns,
                "events_synced": status.events_synced,
                "reachable": status.reachable,
            })
        return result

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

    return app
