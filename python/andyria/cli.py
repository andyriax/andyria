"""CLI entry point for Andyria."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="andyria", help="Andyria — edge-first hybrid intelligence platform")
console = Console()


def _load_config(config_path: Optional[Path]) -> dict:
    import yaml

    if config_path and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


@app.command()
def serve(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Config YAML"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),  # noqa: S104
    port: int = typer.Option(7700, "--port", "-p", help="Bind port"),
    data_dir: Path = typer.Option(Path.home() / ".andyria", "--data-dir"),
    node_id: Optional[str] = typer.Option(None, "--node-id"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model"),
) -> None:
    """Start the Andyria HTTP API server."""
    from .api import create_app
    from .coordinator import Coordinator

    cfg = _load_config(config)
    resolved_node_id = node_id or cfg.get("node_id", "andyria-node-0")
    resolved_data_dir = Path(cfg.get("data_dir", str(data_dir)))
    model_path = Path(cfg["model_path"]) if cfg.get("model_path") else None

    coordinator = Coordinator(
        data_dir=resolved_data_dir,
        node_id=resolved_node_id,
        deployment_class=cfg.get("deployment_class", "edge"),
        entropy_sources=cfg.get("entropy_sources"),
        model_path=model_path,
        ollama_url=ollama_url or cfg.get("ollama_url"),
        ollama_model=ollama_model or cfg.get("ollama_model"),
    )

    fastapi_app = create_app(coordinator)
    console.print(f"[bold green]Andyria[/] node [cyan]{resolved_node_id}[/] → {host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port)


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="Input prompt"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    data_dir: Path = typer.Option(Path.home() / ".andyria", "--data-dir"),
    node_id: Optional[str] = typer.Option(None, "--node-id"),
) -> None:
    """Send a single request and print the response."""
    from .coordinator import Coordinator
    from .models import AndyriaRequest

    cfg = _load_config(config)
    resolved_node_id = node_id or cfg.get("node_id", "andyria-node-0")
    model_path = Path(cfg["model_path"]) if cfg.get("model_path") else None

    coordinator = Coordinator(
        data_dir=data_dir,
        node_id=resolved_node_id,
        deployment_class=cfg.get("deployment_class", "edge"),
        entropy_sources=cfg.get("entropy_sources"),
        model_path=model_path,
        ollama_url=cfg.get("ollama_url"),
    )

    request = AndyriaRequest(input=prompt)
    response = asyncio.run(coordinator.process(request))

    beacon_short = response.entropy_beacon_id[:16]
    console.print(f"\n[bold]Andyria[/] [dim](beacon {beacon_short}…)[/]\n")
    console.print(response.output)
    console.print(
        f"\n[dim]tasks={response.tasks_completed}  "
        f"verified={response.verified}  "
        f"events={len(response.event_ids)}[/]"
    )


@app.command()
def status(
    url: str = typer.Option("http://localhost:7700", "--url", "-u"),
) -> None:
    """Show status of a running Andyria node."""
    import httpx

    try:
        resp = httpx.get(f"{url}/v1/status", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()

        table = Table(title="Andyria Node Status", show_header=True)
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        for k, v in data.items():
            table.add_row(str(k), str(v))
        console.print(table)
    except Exception as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)
