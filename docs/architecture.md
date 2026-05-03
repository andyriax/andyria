# Andyria Architecture

## Overview

Andyria is a **hybrid cognitive runtime** — a node-local intelligence loop
anchored to physical reality through cryptographically signed entropy beacons,
persisted in a lightweight append-only event log, and scalable from a
Raspberry Pi Zero 2W to a multi-node cluster.

The runtime is organized into three layers:

- **Core inference loop** — Coordinator, ReasoningEngine, ATM, ModelRouter, Planner, Verifier
- **Agent platform** — AgentRegistry, PersonaEngine, SkillRegistry, ChainRegistry, DelegationManager, SessionStore, TabProjectionStore
- **Persistence & crypto** — ContentAddressedMemory, PersistentMemory, EventStore (DAG), EntropyBeaconFactory

A companion **TT Live Agent** (`tt-live-agent/`) provides a standalone Node.js
runtime for TikTok Live monetization, driven by the same DAG and persona
concepts.

---

## Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Andyria Node                                                            │
│                                                                          │
│  HTTP API (FastAPI) / WebSocket / CLI (typer)                            │
│       │                                                                  │
│       ▼                                                                  │
│  Coordinator ──── EntropyBeaconFactory ◄── Physical Sources             │
│       │                 (signed)             (hwrng, jitter,             │
│       │                                        thermal, stats)           │
│       ├── ReasoningEngine  (decompose → analyze → synthesize)           │
│       ├── AutomatedThoughtMachine  (generate → critique → revise loop)  │
│       ├── AutoLearner  (distil high-confidence results → MEMORY.md)     │
│       │                                                                  │
│       ▼                                                                  │
│  Planner (rule-based decomposition) ──── EntropyBeacon.id               │
│       │                                                                  │
│       ▼                                                                  │
│  ModelRouter                                                             │
│   ├── SymbolicSolver  (AST math, no eval())                             │
│   ├── LlamaLocal      (llama-cpp-python, GGUF)                          │
│   ├── OllamaHttp      (HTTP proxy to local Ollama)                      │
│   └── StubModel       (offline fallback)                                │
│       │                                                                  │
│       ▼                                                                  │
│  Verifier (quality + policy) ──── signed Event → EventStore (DAG)      │
│       │                                                                  │
│       ▼                                                                  │
│  Agent Platform Layer                                                    │
│   ├── AgentRegistry   (CRUD, clone, retire)                             │
│   ├── PersonaEngine   (codename, archetype, avatar SVG)                 │
│   ├── SkillRegistry   (create/search/view skills)                       │
│   ├── ChainRegistry   (sequential agent chains)                         │
│   ├── DelegationManager  (parallel sub-agent spawning)                  │
│   ├── SessionStore    (multi-turn conversation history)                  │
│   ├── TabProjectionStore  (UI viewport projections)                     │
│   ├── CronScheduler   (background recurring tasks)                      │
│   └── TodoStore       (task tracking)                                   │
│       │                                                                  │
│       ▼                                                                  │
│  Memory Layer                                                            │
│   ├── ContentAddressedMemory  (BLAKE3-keyed blobs + named bindings)     │
│   ├── PersistentMemory        (MEMORY.md + USER.md flat files)          │
│   └── SoulFile                (SOUL.md agent identity file)             │
│       │                                                                  │
│       ▼                                                                  │
│  MeshManager  (gossip P2P, event delta-sync)                            │
│       │                                                                  │
│       ▼                                                                  │
│  AndyriaResponse                                                         │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Module Reference

| Module | Purpose |
|---|---|
| `coordinator.py` | Main intelligence loop; orchestrates all components |
| `reasoning.py` | Multi-step chain-of-thought (decompose → analyze → synthesize) |
| `atm.py` | Automated Thought Machine — iterative generate/critique/revise |
| `auto_learn.py` | Distils high-confidence responses into `MEMORY.md` |
| `planner.py` | Rule-based task decomposition (SYMBOLIC / LANGUAGE / TOOL) |
| `models.py` | Shared Pydantic models + `EventType` enum |
| `api.py` | FastAPI HTTP + WebSocket server |
| `cli.py` | typer CLI (`serve` / `ask` / `status`) |
| `registry.py` | AgentRegistry — CRUD, clone, retire |
| `persona.py` | PersonaEngine — codename, archetype, avatar SVG generation |
| `chains.py` | ChainRegistry — sequential multi-agent pipelines |
| `delegation.py` | DelegationManager — parallel sub-agent spawning |
| `session_store.py` | SessionStore — multi-turn history + search |
| `projections.py` | TabProjectionStore — UI viewport projections over agents |
| `skills.py` | SkillRegistry — create, search, and load agent skills |
| `cron.py` | CronScheduler — background recurring task runner |
| `todo.py` | TodoStore — per-node task tracking |
| `memory.py` | ContentAddressedMemory — BLAKE3-keyed blob store |
| `persistent_memory.py` | PersistentMemory — flat-file MEMORY.md / USER.md |
| `soul.py` | SoulFile — SOUL.md identity and directive file |
| `agent_features.py` | Agent modes, environments, skill-profile helpers |
| `mesh.py` | MeshManager — gossip-based P2P event sync |
| `store.py` | EventStore — append-only NDJSON + flat index |
| `dag.py` | DAG topology utilities (topological sort) |
| `verifier.py` | Quality + policy verifier |
| `node.py` | NodeIdentityManager — Ed25519 key management |
| `context_compressor.py` | Token-budget context compressor |
| `context_files.py` | Context file loader for agent dev workspaces |
| `prompt_builder.py` | Dynamic system prompt assembly |
| `demo.py` | DemoManager — showcase agent seeding |
| `entropy/` | Physical entropy subsystem (beacon, collectors, health) |

---

## TT Live Agent

`tt-live-agent/` is a standalone **Node.js** runtime for TikTok Live sessions.

```
tt-live-agent/
├── agent.js          # Main entrypoint (CLI flags: --persona, --dry-run, --dag snapshot, etc.)
├── core/
│   ├── orchestrator.js  # Multi-agent dispatch
│   ├── dag.js           # DAGStateMachine — mirrors Python DAG concepts
│   ├── persona.js       # Persona loading + response formatting
│   ├── llm.js           # LLM bridge (local / stub)
│   ├── router.js        # Skill routing
│   ├── skillLoader.js   # Hot-reload skill modules
│   ├── db.js            # NDJSON event log + in-memory key-value store
│   ├── identity.js      # Capsule identity (UUID + sealed JSON)
│   ├── voice.js         # TTS voice output
│   ├── openclaw.js      # Self-explorer / gap-discovery engine
│   └── sleeper.js       # Sleep-mode dreamscape transitions
├── agents/           # Per-agent config (greeter, hype-bot, sales-sniper, chat-commander)
├── personas/         # Persona JSON files
└── skills/           # Revenue, hype, shoutout, sales, greet skill modules
```

Key CLI flags:

| Flag | Effect |
|---|---|
| `--persona <name>` | Load a specific persona on startup |
| `--dry-run` | No TikTok connection; accept stdin commands |
| `--dag snapshot` | Print DAG state for all agents and exit |
| `--revenue stats` | Print revenue totals and exit |
| `--self-explorer` | Start OpenClaw background self-explorer |
| `--explore` | One-shot gap discovery (dry-run safe) |
| `--promote-capsule` | Print sealed capsule identity JSON |

---

## Entropy Design Invariant

**Physical entropy does NOT mutate content hashes.**

Instead:

1. Raw bytes are collected from hardware sources.
2. XOR-mixed and BLAKE3-whitened.
3. A domain-separated nonce is derived.
4. An `EntropyBeacon` is produced and Ed25519-signed.
5. The beacon's **ID** (a deterministic hash of the beacon's fields) is embedded in `Event.entropy_beacon_id`.

This allows peers to:
- Verify event hashes independently (no secret bytes in the hash).
- Audit beacon chains to confirm physical-world anchoring.
- Detect beacon forging (signature check).

---

## Event Topology

Events form a directed acyclic graph (DAG). Each event commits to:
```
event.id = BLAKE3(sorted(parent_ids) | payload_hash | entropy_beacon_id | timestamp_ns | node_id)
```

This binds:
- **Causal order** (parent_ids)
- **Content integrity** (payload_hash)
- **Physical timestamp anchor** (entropy_beacon_id → physical entropy)
- **Node attribution** (node_id)

---

## Deployment Classes

| Class   | RAM    | Cores | Model tier | Context |
|---------|--------|-------|------------|---------|
| edge    | ≤ 4 GB | ≤ 4   | tiny < 1B  | 512 tok |
| server  | ≤ 32 GB| ≤ 16  | small 1-3B | 2 k tok |
| cluster | > 32 GB| any   | medium 3B+ | 8 k tok |

---

## Security Properties

| Property | Mechanism |
|---|---|
| Node authenticity | Ed25519 on every event and beacon |
| PQ upgrade path | `NodeKeyPair` sign/verify swappable → ML-DSA |
| Entropy health | NIST SP 800-90B RCT + APT tests |
| Safe symbolic eval | AST-only evaluator; no `eval()` or `exec()` |
| Key storage | Identity PEM, mode 0o600 |
| CORS restriction | localhost/127.0.0.1 only |
| Policy filter | Blocks shell injection patterns in outputs |

---

## Data Layout

```
$ANDYRIA_DATA_DIR/
  identity.pem                   # Ed25519 private key (mode 0o600)
  identity.json                  # NodeIdentity (public fields)
  ledger/
    events.ndjson                # Append-only NDJSON event log
    index/<event_id>             # O(1) existence check
  memory/
    objects/<hash>               # Content-addressed blob store
    index/<namespace>/<key>      # Named binding → hash
```
