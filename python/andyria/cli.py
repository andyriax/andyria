"""CLI entry point for Andyria."""

from __future__ import annotations
import os
import sys
import uuid

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
    node_id: Optional[str] = typer.Option(None, "--node-id", envvar="ANDYRIA_NODE_ID"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", envvar="ANDYRIA_OLLAMA_URL"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", envvar="ANDYRIA_OLLAMA_MODEL"),
    peers: Optional[str] = typer.Option(None, "--peers", envvar="ANDYRIA_PEERS", help="Comma-separated peer URLs"),
) -> None:
    """Start the Andyria HTTP API server."""
    from .api import create_app
    from .coordinator import Coordinator

    cfg = _load_config(config)
    perf_cfg = cfg.get("performance", {})
    resolved_node_id = node_id or cfg.get("node_id", "andyria-node-0")
    resolved_data_dir = Path(cfg.get("data_dir", str(data_dir)))
    model_path = Path(cfg["model_path"]) if cfg.get("model_path") else None
    
    # Parse peers from env or config
    peer_list = []
    if peers:
        peer_list = [p.strip() for p in peers.split(",") if p.strip()]
    elif cfg.get("peers"):
        peer_list = cfg.get("peers", [])

    coordinator = Coordinator(
        data_dir=resolved_data_dir,
        node_id=resolved_node_id,
        deployment_class=cfg.get("deployment_class", "edge"),
        entropy_sources=cfg.get("entropy_sources"),
        entropy_sampler_interval_ms=int(
            os.environ.get(
                "ANDYRIA_ENTROPY_SAMPLER_INTERVAL_MS",
                perf_cfg.get("beacon_interval_ms", 0),
            )
        ),
        entropy_min_active_sources=int(os.environ.get("ANDYRIA_ENTROPY_MIN_ACTIVE_SOURCES", 1)),
        entropy_max_consecutive_degraded=int(
            os.environ.get("ANDYRIA_ENTROPY_MAX_CONSECUTIVE_DEGRADED", 3)
        ),
        entropy_fail_closed=os.environ.get("ANDYRIA_ENTROPY_FAIL_CLOSED", "0") == "1",
        model_path=model_path,
        ollama_url=ollama_url or cfg.get("ollama_url"),
        ollama_model=ollama_model or cfg.get("ollama_model"),
        peer_urls=peer_list,
    )

    fastapi_app = create_app(coordinator)
    startup_status = coordinator.status()
    if startup_status.ready:
        console.print("[green]Startup check:[/] model/backend readiness OK")
    else:
        console.print(f"[yellow]Startup check:[/] {startup_status.readiness_detail}")
    console.print(f"[bold green]Andyria[/] node [cyan]{resolved_node_id}[/] → {host}:{port}")
    if peer_list:
        console.print(f"[dim]Peers: {', '.join(peer_list)}[/]")
    uvicorn.run(fastapi_app, host=host, port=port)


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="Input prompt"),
    data_dir: Path = typer.Option(Path.home() / ".andyria", "--data-dir"),
    node_id: Optional[str] = typer.Option(None, "--node-id"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", envvar="ANDYRIA_OLLAMA_URL"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", envvar="ANDYRIA_OLLAMA_MODEL"),
) -> None:
    """Send a single request and print the response."""
    from .coordinator import Coordinator
    from .models import AndyriaRequest

    cfg = _load_config(config)
    perf_cfg = cfg.get("performance", {})
    resolved_node_id = node_id or cfg.get("node_id", "andyria-node-0")
    model_path = Path(cfg["model_path"]) if cfg.get("model_path") else None

    coordinator = Coordinator(
        data_dir=data_dir,
        node_id=resolved_node_id,
        deployment_class=cfg.get("deployment_class", "edge"),
        entropy_sources=cfg.get("entropy_sources"),
        entropy_sampler_interval_ms=int(
            os.environ.get(
                "ANDYRIA_ENTROPY_SAMPLER_INTERVAL_MS",
                perf_cfg.get("beacon_interval_ms", 0),
            )
        ),
        entropy_min_active_sources=int(os.environ.get("ANDYRIA_ENTROPY_MIN_ACTIVE_SOURCES", 1)),
        entropy_max_consecutive_degraded=int(
            os.environ.get("ANDYRIA_ENTROPY_MAX_CONSECUTIVE_DEGRADED", 3)
        ),
        entropy_fail_closed=os.environ.get("ANDYRIA_ENTROPY_FAIL_CLOSED", "0") == "1",
        model_path=model_path,
        ollama_url=ollama_url or cfg.get("ollama_url"),
        ollama_model=ollama_model or cfg.get("ollama_model"),
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


@app.command()
def chat(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Config YAML"),
    data_dir: Path = typer.Option(Path.home() / ".andyria", "--data-dir"),
    node_id: Optional[str] = typer.Option(None, "--node-id"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", envvar="ANDYRIA_OLLAMA_URL"),
    ollama_model: Optional[str] = typer.Option(None, "--ollama-model", envvar="ANDYRIA_OLLAMA_MODEL"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Resume session by ID"),
    no_soul: bool = typer.Option(False, "--no-soul", help="Skip SOUL.md injection"),
) -> None:
    """Interactive chat REPL with full slash-command support.

    Slash commands:
        /new            Start a new session
        /reset          Clear history (keep session)
        /model <name>   Switch LLM model
        /personality    Show or edit SOUL.md
        /skills         List available skills
        /skill <name>   Load and display a skill
        /memory         Show MEMORY.md and USER.md
        /todo           Show current TODO list
        /cron           Show scheduled jobs
        /compress       Manually trigger context compression
        /history        Show past sessions
        /resume <id>    Resume a past session
        /session        Show current session info
        /usage          Show token usage estimate
        /help           Show this help
    """
    from .coordinator import Coordinator
    from .models import AndyriaRequest
    from .soul import SoulFile
    from .persistent_memory import PersistentMemory
    from .skills import SkillRegistry
    from .session_store import SessionStore
    from .todo import TodoStore
    from .cron import CronScheduler
    from .context_files import ContextFileLoader
    from .prompt_builder import PromptBuilder
    from .context_compressor import ContextCompressor

    cfg = _load_config(config)
    perf_cfg = cfg.get("performance", {})
    resolved_node_id = node_id or cfg.get("node_id", "andyria-node-0")
    resolved_data_dir = Path(cfg.get("data_dir", str(data_dir)))
    model_path = Path(cfg["model_path"]) if cfg.get("model_path") else None

    # Initialise all subsystems
    soul = SoulFile(resolved_data_dir)
    soul.ensure_default()

    memory = PersistentMemory(resolved_data_dir)
    skills = SkillRegistry(resolved_data_dir)
    session_store = SessionStore(resolved_data_dir)
    todo = TodoStore(resolved_data_dir)
    cron = CronScheduler(resolved_data_dir)
    cron.start()

    ctx_files = ContextFileLoader()
    found_ctx = ctx_files.discover()

    compressor = ContextCompressor(max_tokens=8192)

    coordinator = Coordinator(
        data_dir=resolved_data_dir,
        node_id=resolved_node_id,
        deployment_class=cfg.get("deployment_class", "edge"),
        entropy_sources=cfg.get("entropy_sources"),
        entropy_sampler_interval_ms=int(
            os.environ.get(
                "ANDYRIA_ENTROPY_SAMPLER_INTERVAL_MS",
                perf_cfg.get("beacon_interval_ms", 0),
            )
        ),
        entropy_min_active_sources=int(os.environ.get("ANDYRIA_ENTROPY_MIN_ACTIVE_SOURCES", 1)),
        entropy_max_consecutive_degraded=int(
            os.environ.get("ANDYRIA_ENTROPY_MAX_CONSECUTIVE_DEGRADED", 3)
        ),
        entropy_fail_closed=os.environ.get("ANDYRIA_ENTROPY_FAIL_CLOSED", "0") == "1",
        model_path=model_path,
        ollama_url=ollama_url or cfg.get("ollama_url"),
        ollama_model=ollama_model or cfg.get("ollama_model"),
    )

    # Session management
    current_session_id = session or str(uuid.uuid4())[:10]
    if session:
        loaded = session_store.load(session)
        if loaded:
            summary, _ = loaded
            console.print(f"[green]Resumed session:[/] {summary.title} ({summary.turn_count} turns)")
        else:
            console.print(f"[yellow]Session '{session}' not found — starting fresh[/]")
            current_session_id = str(uuid.uuid4())[:10]
    else:
        session_store.create(current_session_id)

    # Prompt builder
    builder = PromptBuilder(
        soul=soul if not no_soul else None,
        memory=memory,
        skills=skills,
        todo=todo,
        context_files=ctx_files if found_ctx else None,
    )

    # Print banner
    console.print(f"\n[bold cyan]Andyria[/] — [dim]{resolved_node_id}[/]  session [dim]{current_session_id}[/]")
    if found_ctx:
        console.print(f"[dim]Context files loaded: {', '.join(found_ctx)}[/]")
    console.print("[dim]Type /help for commands, Ctrl-C or /exit to quit.[/]\n")

    messages: list = []
    active_model: list = [None]   # mutable reference for closure

    def _run_request(user_input: str) -> str:
        req = AndyriaRequest(
            input=user_input,
            system_context=builder.build() or None,
        )
        if active_model[0]:
            req = AndyriaRequest(
                input=user_input,
                model=active_model[0],
                system_context=builder.build() or None,
            )
        resp = asyncio.run(coordinator.process(req))
        return resp.output

    while True:
        try:
            user_input = input("you › ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/]")
            cron.stop()
            break

        if not user_input:
            continue

        # ---------- Slash commands ----------
        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit"):
                console.print("[dim]Goodbye.[/]")
                cron.stop()
                break

            elif cmd == "/help":
                console.print(
                    "\n[bold]Slash commands:[/]\n"
                    "  /new              Start a new session\n"
                    "  /reset            Clear message history\n"
                    "  /model <name>     Switch LLM model\n"
                    "  /personality      Show SOUL.md\n"
                    "  /skills           List available skills\n"
                    "  /skill <name>     Load a skill into prompt\n"
                    "  /memory           Show MEMORY.md + USER.md\n"
                    "  /todo             Show TODO list\n"
                    "  /cron             Show scheduled jobs\n"
                    "  /compress         Manually compress context\n"
                    "  /history          List past sessions\n"
                    "  /resume <id>      Resume a past session\n"
                    "  /session          Current session info\n"
                    "  /usage            Token usage estimate\n"
                    "  /exit             Quit\n"
                )

            elif cmd == "/new":
                current_session_id = str(uuid.uuid4())[:10]
                session_store.create(current_session_id)
                messages.clear()
                console.print(f"[green]New session:[/] {current_session_id}")

            elif cmd == "/reset":
                messages.clear()
                console.print("[yellow]History cleared.[/]")

            elif cmd == "/model":
                if arg:
                    active_model[0] = arg.strip()
                    console.print(f"[green]Model set to:[/] {active_model[0]}")
                else:
                    console.print(f"Active model: {active_model[0] or '[coordinator default]'}")

            elif cmd == "/personality":
                soul.load()
                console.print(soul.content)

            elif cmd == "/skills":
                skill_list = skills.skills_list(category=arg or None)
                if skill_list:
                    table = Table(title="Available Skills", show_header=True)
                    table.add_column("Name", style="cyan")
                    table.add_column("Description")
                    table.add_column("Tags")
                    for sk in skill_list:
                        table.add_row(sk["name"], sk["description"], ", ".join(sk["tags"]))
                    console.print(table)
                else:
                    console.print("[dim]No skills found. Create one with /skill-create.[/]")

            elif cmd == "/skill":
                if not arg:
                    console.print("[yellow]Usage: /skill <name>[/]")
                else:
                    content = skills.skill_view(arg.strip())
                    if content:
                        console.print(content)
                        builder.set_active_skills([arg.strip()])
                        console.print(f"[green]Skill '{arg.strip()}' loaded into prompt.[/]")
                    else:
                        console.print(f"[red]Skill '{arg.strip()}' not found.[/]")

            elif cmd == "/memory":
                console.print(memory.read("MEMORY") or "[dim](empty)[/]")
                console.print()
                console.print(memory.read("USER") or "[dim](empty)[/]")
                stats = memory.stats()
                console.print(
                    f"\n[dim]MEMORY: {stats['MEMORY']['chars']}/{stats['MEMORY']['cap']} chars  "
                    f"USER: {stats['USER']['chars']}/{stats['USER']['cap']} chars[/]"
                )

            elif cmd == "/todo":
                items = todo.list()
                if items:
                    table = Table(title="TODOs", show_header=True)
                    table.add_column("ID", style="cyan", width=10)
                    table.add_column("Status", width=12)
                    table.add_column("Text")
                    for it in items:
                        table.add_row(it["id"], it["status"], it["text"])
                    console.print(table)
                else:
                    console.print("[dim]No TODOs.[/]")

            elif cmd == "/cron":
                jobs = cron.list()
                if jobs:
                    table = Table(title="Cron Jobs", show_header=True)
                    table.add_column("ID", width=10)
                    table.add_column("Name")
                    table.add_column("Schedule")
                    table.add_column("Task")
                    for j in jobs:
                        table.add_row(j.id, j.name, j.expression, j.task[:50])
                    console.print(table)
                else:
                    console.print("[dim]No cron jobs.[/]")

            elif cmd == "/compress":
                usage = compressor.token_usage(messages)
                console.print(
                    f"[dim]Tokens ≈ {usage['estimated_tokens']} / {usage['max_tokens']} "
                    f"({usage['pct_used']}%)[/]"
                )
                if len(messages) < 4:
                    console.print("[dim]Not enough messages to compress.[/]")
                else:
                    def _sync_summarise(text: str) -> str:
                        return asyncio.run(coordinator.process(
                            AndyriaRequest(input=text)
                        )).output

                    messages[:] = compressor.compress_sync(messages, _sync_summarise)
                    console.print("[green]Context compressed.[/]")

            elif cmd == "/history":
                sessions = session_store.list_sessions()
                if sessions:
                    table = Table(title="Past Sessions", show_header=True)
                    table.add_column("ID", style="cyan")
                    table.add_column("Title")
                    table.add_column("Turns")
                    for s in sessions:
                        table.add_row(s.session_id, s.title[:60], str(s.turn_count))
                    console.print(table)
                else:
                    console.print("[dim]No sessions found.[/]")

            elif cmd == "/resume":
                target_id = arg.strip()
                if not target_id:
                    console.print("[yellow]Usage: /resume <session_id>[/]")
                else:
                    loaded = session_store.load(target_id)
                    if loaded:
                        summary, turns = loaded
                        current_session_id = target_id
                        messages.clear()
                        for t in turns:
                            messages.append({"role": t.role, "content": t.content})
                        console.print(
                            f"[green]Resumed:[/] {summary.title} ({summary.turn_count} turns)"
                        )
                    else:
                        console.print(f"[red]Session '{target_id}' not found.[/]")

            elif cmd == "/session":
                loaded = session_store.load(current_session_id)
                if loaded:
                    summary, _ = loaded
                    console.print(
                        f"[cyan]Session:[/] {summary.session_id}\n"
                        f"[cyan]Title:[/]   {summary.title}\n"
                        f"[cyan]Turns:[/]   {summary.turn_count}"
                    )
                else:
                    console.print(f"[dim]Session: {current_session_id}[/]")

            elif cmd == "/usage":
                usage = compressor.token_usage(messages)
                console.print(
                    f"Messages: {len(messages)}  "
                    f"Est. tokens: {usage['estimated_tokens']} / {usage['max_tokens']} "
                    f"({usage['pct_used']}%)"
                )

            else:
                console.print(f"[yellow]Unknown command: {cmd}. Type /help.[/]")

            continue

        # ---------- Normal conversation turn ----------
        # Auto-compress if needed
        if compressor.needs_compression(messages):
            def _sync_summarise(text: str) -> str:
                return asyncio.run(coordinator.process(
                    AndyriaRequest(input=text)
                )).output
            messages[:] = compressor.compress_sync(messages, _sync_summarise)
            console.print("[dim][context compressed][/]")

        messages.append({"role": "user", "content": user_input})
        session_store.append_turn(current_session_id, "user", user_input)

        try:
            output = _run_request(user_input)
        except Exception as exc:
            console.print(f"[red]Error:[/] {exc}")
            messages.pop()
            continue

        messages.append({"role": "assistant", "content": output})
        session_store.append_turn(current_session_id, "assistant", output)

        console.print(f"\n[bold cyan]andyria[/] › {output}\n")
