"""HTTP API for Andyria (FastAPI)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .coordinator import Coordinator
from .models import AndyriaRequest, AndyriaResponse, Event, NodeStatus

_coordinator: Optional[Coordinator] = None


def create_app(coordinator: Coordinator) -> FastAPI:
    global _coordinator
    _coordinator = coordinator

    app = FastAPI(
        title="Andyria",
        description="Edge-first hybrid intelligence platform",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
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
    async def events() -> List[Event]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        return _coordinator.get_event_log()

    @app.get("/v1/beacon/{beacon_id}")
    async def get_beacon(beacon_id: str) -> Dict[str, Any]:
        if _coordinator is None:
            raise HTTPException(status_code=503, detail="Coordinator not initialized")
        beacon = _coordinator.get_beacon(beacon_id)
        if beacon is None:
            raise HTTPException(status_code=404, detail="Beacon not found")
        return beacon.model_dump()

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": "andyria"}

    return app
