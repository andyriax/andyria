"""Dev-mode application bootstrap.

Used as the uvicorn reload target:
    uvicorn andyria._dev:app --reload

Reads all configuration from environment variables so the module can be
imported fresh on every reload without needing CLI arguments.
"""

from __future__ import annotations

import os
from pathlib import Path

from .api import create_app
from .coordinator import Coordinator

_data_dir = Path(os.environ.get("ANDYRIA_DATA_DIR", Path.home() / ".andyria"))
_node_id = os.environ.get("ANDYRIA_NODE_ID", "andyria-dev-0")
_deployment_class = os.environ.get("ANDYRIA_DEPLOYMENT_CLASS", "server")
_entropy_sources_raw = os.environ.get("ANDYRIA_ENTROPY_SOURCES", "")
_entropy_sources = [s.strip() for s in _entropy_sources_raw.split(",") if s.strip()] or None
_ollama_url = os.environ.get("ANDYRIA_OLLAMA_URL", "http://host.docker.internal:11434")
_ollama_model = os.environ.get("ANDYRIA_OLLAMA_MODEL", "phi3")
_peers_raw = os.environ.get("ANDYRIA_PEERS", "")
_peer_urls = [p.strip() for p in _peers_raw.split(",") if p.strip()]

coordinator = Coordinator(
    data_dir=_data_dir,
    node_id=_node_id,
    deployment_class=_deployment_class,
    entropy_sources=_entropy_sources,
    ollama_url=_ollama_url,
    ollama_model=_ollama_model,
    peer_urls=_peer_urls,
)

app = create_app(coordinator)
