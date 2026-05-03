# Andyria Roadmap

## Phase 0 — Foundation ✅

- [x] Physical entropy subsystem (hwrng, jitter, thermal, stats)
- [x] NIST SP 800-90B health tests (RCT + APT)
- [x] Signed EntropyBeacon with BLAKE3-whitened nonce
- [x] Ed25519 identity per node
- [x] Append-only DAG event log (NDJSON + flat index)
- [x] Content-addressed memory store
- [x] Rule-based planner (SYMBOLIC / LANGUAGE / TOOL decomposition)
- [x] AST-safe symbolic math solver (no eval)
- [x] ModelRouter: AST → llama.cpp → Ollama → stub
- [x] Policy + quality verifier
- [x] FastAPI HTTP API (v1)
- [x] WebSocket real-time event stream (`/v1/stream`)
- [x] typer CLI (serve / ask / status)
- [x] Deployment classes: edge / server / cluster
- [x] Rust ledger crate (crypto, entropy, event, store)
- [x] Rust runtime crate (hardware detection, profile selection)
- [x] Docker Compose single-node + optional peer profile
- [x] Raspberry Pi deploy config

## Phase 1 — Agent Platform ✅

- [x] ReasoningEngine: multi-step chain-of-thought (decompose → analyze → synthesize)
- [x] AutomatedThoughtMachine (ATM): iterative generate → critique → revise loop
- [x] AutoLearner: distil high-confidence results into persistent `MEMORY.md`
- [x] AgentRegistry: full CRUD, clone, retire with persona generation
- [x] PersonaEngine: codename, archetype, avatar SVG procedural generation
- [x] SkillRegistry: create, search, view, and manage agent skills
- [x] ChainRegistry: sequential multi-agent pipelines
- [x] DelegationManager: parallel sub-agent spawning and result collection
- [x] SessionStore: multi-turn conversation history with search
- [x] TabProjectionStore: UI viewport projections over agent state
- [x] CronScheduler: background recurring task runner
- [x] TodoStore: per-node task tracking
- [x] PersistentMemory: flat-file MEMORY.md / USER.md with CRUD
- [x] SoulFile: SOUL.md agent identity and directive file
- [x] Agent dev workspace (`/v1/agents/{id}/dev`): IDE integration, auto-develop cron, dreamscapes
- [x] Demo mode: showcase agent seeding via `/v1/demo`
- [x] Agent presets: preset templates from `deploy/presets/agents.json`
- [x] Gossip-based P2P mesh (MeshManager, delta sync)
- [x] TT Live Agent (Node.js): TikTok Live monetization runtime with DAG, personas, skills, revenue tracking

## Phase 2 — Multi-node mesh (in progress)

- [x] MeshManager: gossip protocol for event exchange between peers
- [x] Peer discovery: manual peer registration via `/v1/peers`
- [ ] Conflict-free DAG merge (topological sort, causal consistency)
- [ ] Peer discovery via mDNS (LAN) and DNS-SD
- [ ] Verified peer identity exchange (signed NodeIdentity events)
- [ ] Lightweight replication protocol (delta sync by event ID set)

## Phase 3 — Post-quantum cryptography

- [ ] Add ML-DSA (CRYSTALS-Dilithium) signing via `pqcrypto` crate
- [ ] Dual-signature transition protocol (Ed25519 + ML-DSA window)
- [ ] Update NodeIdentity capabilities field
- [ ] Scheduled Ed25519 deprecation after full mesh upgrade

## Phase 4 — Cognitive depth

- [x] Persistent session context (multi-turn memory via SessionStore + ContentAddressedMemory)
- [x] Tool-use framework: agents invoke registered Python functions
- [x] Planner upgrade: LLM-guided task decomposition (chain-of-thought via ReasoningEngine)
- [ ] Confidence calibration using historical task results
- [ ] Model hot-swap: reload GGUF without node restart

## Phase 5 — Scalability & observability

- [ ] Prometheus metrics endpoint (`/metrics`)
- [ ] Structured JSONL audit log (all events with full payload)
- [ ] Horizontal cluster: shared event store (PostgreSQL or CockroachDB)
- [ ] Kubernetes Helm chart (autoscaling, anti-affinity for entropy diversity)
- [ ] Web UI: event graph explorer, beacon timeline, node health

## Phase 6 — Research extensions

- [ ] Quantum random number integration (IBMQ / IonQ via REST API)
- [ ] Neuromorphic model backend (Intel Loihi, if accessible)
- [ ] Formal verification of DAG convergence (TLA+ spec)
- [ ] Academic paper: "Physical-Entropy-Anchored Cognitive Event Logs"
