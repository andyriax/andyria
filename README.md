<div align="center">

<img src="https://raw.githubusercontent.com/andyriax/andyria/main/docs/logo.svg" alt="Andyria" width="72" height="72" />

# Andyria Foundation

**Edge-first autonomous AI agent platform — open source, self-improving, locally operated.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/andyriax/andyria?style=flat)](https://github.com/andyriax/andyria/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/andyriax/andyria)](https://github.com/andyriax/andyria/issues)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](python/)
[![Rust](https://img.shields.io/badge/rust-stable-orange.svg)](rust/)

[Website](https://andyriax.github.io/andyria) · [Architecture](docs/architecture.md) · [Roadmap](docs/roadmap.md) · [Contributing](#contributing)

</div>

---

## What is Andyria?

Andyria is a self-improving, locally-operated AI agent platform. It runs on your hardware — from a Raspberry Pi to a cloud VM — with zero cloud dependency, zero per-token bill, and full cryptographic auditability of every decision.

The platform fuses **chain-of-thought reasoning**, an **auto-learn loop**, a **cryptographic event DAG**, and a **peer-to-peer mesh** into a single runtime that gets smarter every session.

```bash
# Up in 60 seconds
git clone https://github.com/andyriax/andyria.git && cd andyria
docker compose up -d
# → http://localhost:7700
```

```bash
# Easiest install (auto mode + defaults + non-interactive)
curl -fsSL https://andyriax.github.io/andyria/install.sh | bash -s -- --easy
```

---

## Core Capabilities

| Capability | Description |
|---|---|
| 🧠 **ReasoningEngine** | Decompose → Analyze → Synthesize chain-of-thought, fully local |
| 📚 **Auto-Learn Loop** | Distils high-confidence responses into persistent memory, injected into future prompts |
| 🔄 **ATM** | Automated Thought Machine — iterative generate/critique/revise with reasoning escalation |
| 🤖 **Multi-Agent Orchestration** | Persona-driven agents with skill profiles, clone/retire, DAG execution chains |
| 🔗 **Agent Chains** | Sequential multi-agent pipelines via `ChainRegistry` |
| 🪄 **Delegation** | Parallel sub-agent spawning and result collection via `DelegationManager` |
| 🎭 **Persona Engine** | Procedural codename, archetype, style, mission + SVG avatar generation |
| 🧩 **Skill Registry** | Create, search, and load agent skills; per-agent skill profiles |
| 📅 **Cron Scheduler** | Background recurring tasks; per-agent auto-develop crons |
| ✅ **Todo Tracking** | Per-node task tracking with status lifecycle |
| 🌐 **Mesh Networking** | Gossip-based peer sync, no central coordinator, runs on any topology |
| 🔐 **Cryptographic DAG** | Ed25519-signed, BLAKE3-hashed append-only event ledger (Rust native) |
| ⚡ **Entropy Beacons** | Physical entropy anchors every event chain to real-world hardware state |
| 🎛️ **Multi-Model Router** | Local GGUF → Ollama → stub fallback. Cheapest path always first |
| 💾 **Persistent Memory** | MEMORY.md / USER.md / SOUL.md flat-file knowledge persistence |
| 📺 **TT Live Agent** | TikTok Live monetization runtime (Node.js) — personas, skills, revenue, DAG |
| 🔋 **Edge-First Runtime** | Runs on 2 GB RAM, first-class Raspberry Pi + Termux support |

---

## Architecture

```
HTTP/WebSocket
      │
  Coordinator
    ├── ReasoningEngine     (decompose → analyze → synthesize)
    ├── AutomatedThoughtMachine  (generate → critique → revise)
    ├── AutoLearner         (pattern distillation → MEMORY.md)
    ├── ModelRouter         (GGUF | Ollama | stub)
    ├── Planner + Verifier
    │
    ├── Agent Platform
    │   ├── AgentRegistry   (CRUD, clone, retire)
    │   ├── PersonaEngine   (codename, archetype, avatar SVG)
    │   ├── SkillRegistry   (create, search, load)
    │   ├── ChainRegistry   (sequential pipelines)
    │   ├── DelegationManager  (parallel sub-agents)
    │   ├── SessionStore    (multi-turn history)
    │   ├── TabProjectionStore  (UI viewports)
    │   ├── CronScheduler   (background tasks)
    │   └── TodoStore       (task tracking)
    │
    └── MeshManager         (gossip P2P)
          │
    EventDAG  ←──── Rust (BLAKE3 + Ed25519)
          │
    Memory Layer
    ├── ContentAddressedMemory  (BLAKE3 content hashing)
    ├── PersistentMemory        (MEMORY.md + USER.md)
    ├── SoulFile                (SOUL.md identity)
    └── SessionStore            (turn history)

TT Live Agent (Node.js)
    ├── Orchestrator        (multi-agent dispatch)
    ├── DAGStateMachine     (mirrors Python DAG)
    ├── PersonaEngine       (JSON-driven personas)
    ├── SkillLoader         (hot-reload skills)
    ├── OpenClaw            (self-explorer / gap discovery)
    └── Revenue tracking    (gift handling + stats)
```

See [docs/architecture.md](docs/architecture.md) for the full specification.

---

## Quick Start

### Docker (recommended)

```bash
docker compose up -d --build
```

| Service | URL |
|---|---|
| Andyria UI + API | http://localhost:7700 |
| Peer node | http://localhost:7701 |
| API docs (Swagger) | http://localhost:7700/docs |

### Local Python

```bash
pip install -e python/
python -m andyria serve --port 7700
```

### With Ollama (free local LLM)

```bash
ollama pull llama3
# Andyria auto-detects Ollama — no config needed
python -m andyria serve
```

### Raspberry Pi / Edge

```bash
python -m andyria serve --config deploy/raspberry-pi/config.yaml
```

### Dev mode (hot reload + browser IDE)

```bash
make dev
# → http://localhost:7700  (app)
# → http://localhost:8080  (code-server IDE)
```

---

## Roadmap

| Milestone | Status |
|---|---|
| v1 — Core DAG + Entropy + ModelRouter | ✅ Shipped |
| v1.5 — ReasoningEngine + ATM + AutoLearner | ✅ Shipped |
| **v2 — Full Agent Platform** (personas, skills, chains, delegation, sessions, cron, todos, dev workspaces) | ✅ Shipped |
| **v2.5 — TT Live Agent + Mesh P2P** | ✅ Shipped |
| v3 — Distributed swarm, mDNS peer discovery, Rust ARM runtime | 🔄 Active |
| v4 — Post-quantum cryptography (ML-DSA), WASM sandbox | 📋 Planned |
| v5 — Foundation governance + community grants | 📋 Planned |

Full roadmap: [docs/roadmap.md](docs/roadmap.md)

---

## Repository Structure

```
andyria/
├── python/andyria/         # Core Python runtime (FastAPI + all agents)
│   ├── coordinator.py      # Main intelligence loop
│   ├── reasoning.py        # Chain-of-thought ReasoningEngine
│   ├── auto_learn.py       # Self-recording AutoLearner
│   ├── atm.py              # Automated Thought Machine
│   ├── models.py           # Shared Pydantic models + EventType enum
│   ├── registry.py         # AgentRegistry (CRUD, clone, retire)
│   ├── persona.py          # PersonaEngine + avatar SVG generation
│   ├── chains.py           # ChainRegistry (sequential pipelines)
│   ├── delegation.py       # DelegationManager (parallel sub-agents)
│   ├── session_store.py    # Multi-turn conversation history
│   ├── projections.py      # TabProjectionStore (UI viewports)
│   ├── skills.py           # SkillRegistry
│   ├── cron.py             # CronScheduler
│   ├── todo.py             # TodoStore
│   ├── persistent_memory.py# MEMORY.md / USER.md
│   ├── soul.py             # SOUL.md identity file
│   ├── mesh.py             # P2P gossip networking
│   ├── demo.py             # DemoManager
│   └── static/             # Web UI
├── rust/crates/            # Cryptographic DAG ledger (Rust)
│   ├── ledger/             # Ed25519 signing + BLAKE3 event DAG
│   └── runtime/            # Hardware profiling + entropy
├── tt-live-agent/          # TikTok Live monetization agent (Node.js)
│   ├── core/               # Orchestrator, DAG, persona, LLM, skills, revenue
│   ├── agents/             # Per-agent config JSON
│   ├── personas/           # Persona definitions
│   └── skills/             # Revenue, hype, shoutout, sales, greet
├── deploy/                 # Docker + Raspberry Pi deployment configs
│   └── presets/agents.json # Agent preset templates
├── docs/                   # GitHub Pages site + architecture docs
└── schemas/                # JSON event schemas
```

---

## Contributing

Andyria is built in the open. All contributions are welcome.

1. **Fork** the repository
2. Create a branch: `git checkout -b feat/your-feature`
3. Make your changes and add tests
4. Open a **Pull Request** against `main`

See [CONTRIBUTING.md](CONTRIBUTING.md) for code style, commit conventions, and the review process.

**Good first issues:** look for the [`good first issue`](https://github.com/andyriax/andyria/issues?q=label%3A%22good+first+issue%22) label.

---

## Mission & Governance

> *Intelligence should not require a cloud account. Andyria exists to prove that autonomous AI can run on hardware you already own, improve itself without external intervention, and remain fully under your control.*

The **Andyria Foundation** is committed to:

- **Open source forever** — Apache 2.0, no open-core, no feature gating
- **Local-first architecture** — every capability works offline
- **Radical transparency** — every decision is a signed, auditable event
- **Community governance** — roadmap driven by contributors, not investors

Operational guidance for sovereign runtime controls:

- [Sovereign Governance Baseline](docs/sovereign-governance-baseline.md)

### 💛 CFF Commitment

**50% of all Andyria Foundation profits are donated to the [Cystic Fibrosis Foundation](https://www.cff.org).**  
This is a founding commitment. Every commercial license, every sponsored feature — half goes to fighting CF.

---

## License

Apache 2.0 — see [LICENSE](LICENSE)

---

<div align="center">

**[andyriax.github.io/andyria](https://andyriax.github.io/andyria)**

Founded by Michael J. Mahon · Built with ♥ on a NucBox · Runs on a Raspberry Pi

</div>
