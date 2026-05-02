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

---

## Core Capabilities

| Capability | Description |
|---|---|
| 🧠 **ReasoningEngine** | Decompose → Analyze → Synthesize chain-of-thought, fully local |
| 📚 **Auto-Learn Loop** | Distils high-confidence responses into persistent memory, injected into future prompts |
| 🔄 **ATM** | Automated Thought Machine — iterative generate/critique/revise with reasoning escalation |
| 🌐 **Mesh Networking** | Gossip-based peer sync, no central coordinator, runs on any topology |
| 🔐 **Cryptographic DAG** | Ed25519-signed, BLAKE3-hashed append-only event ledger (Rust native) |
| ⚡ **Entropy Beacons** | Physical entropy anchors every event chain to real-world hardware state |
| 🎛️ **Multi-Model Router** | Local GGUF → Ollama → stub fallback. Cheapest path always first |
| 🤖 **Multi-Agent Orchestration** | Persona-driven agents with skill profiles, DAG execution chains |
| 🔋 **Edge-First Runtime** | Runs on 2GB RAM, first-class Raspberry Pi + Termux support |

---

## Architecture

```
HTTP/WebSocket
      │
  Coordinator
    ├── ReasoningEngine   (decompose → analyze → synthesize)
    ├── AutomatedThoughtMachine  (generate → critique → revise)
    ├── AutoLearner       (pattern distillation → MEMORY.md)
    ├── ModelRouter       (GGUF | Ollama | stub)
    ├── Planner + Verifier
    └── MeshManager       (gossip P2P)
          │
    EventDAG  ←──── Rust (BLAKE3 + Ed25519)
          │
    Memory Layer
    ├── ContentAddressedMemory  (BLAKE3 content hashing)
    ├── PersistentMemory        (MEMORY.md + USER.md)
    └── SessionStore            (turn history)
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
| v1 — Core DAG + Mesh + ATM | ✅ Shipped |
| v1.5 — Agent Platform (personas, skills, sessions) | ✅ Shipped |
| **v2 — ReasoningEngine + Auto-Learn** | 🔄 Active |
| v2.5 — TT Live Agent + JETS token rewards | ⏳ Next |
| v3 — Distributed swarm, Rust ARM runtime, WASM sandbox | 📋 Planned |
| v4 — Foundation governance + community grants | 📋 Planned |

Full roadmap: [docs/roadmap.md](docs/roadmap.md)

---

## Repository Structure

```
andyria/
├── python/andyria/      # Core Python runtime (FastAPI + all agents)
│   ├── coordinator.py   # Main intelligence loop
│   ├── reasoning.py     # Chain-of-thought ReasoningEngine
│   ├── auto_learn.py    # Self-recording AutoLearner
│   ├── atm.py           # Automated Thought Machine
│   ├── models.py        # Shared Pydantic models + EventType enum
│   ├── mesh.py          # P2P gossip networking
│   └── static/          # Web UI
├── rust/crates/         # Cryptographic DAG ledger (Rust)
│   ├── ledger/          # Ed25519 signing + BLAKE3 event DAG
│   └── runtime/         # Hardware profiling + entropy
├── tt-live-agent/       # TikTok Live monetization agent (Node.js)
├── deploy/              # Docker + Raspberry Pi deployment configs
├── docs/                # GitHub Pages site + architecture docs
└── schemas/             # JSON event schemas
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
