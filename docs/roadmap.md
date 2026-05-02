# Andyria Roadmap

## Phase 0 — Foundation (current)

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
- [x] typer CLI (serve / ask / status)
- [x] Deployment classes: edge / server / cluster
- [x] Rust ledger crate (crypto, entropy, event, store)
- [x] Rust runtime crate (hardware detection, profile selection)
- [x] Docker Compose single-node + optional peer profile
- [x] Raspberry Pi deploy config

## Phase 1 — Multi-node mesh

- [ ] Gossip protocol for beacon and event exchange between peers
- [ ] Conflict-free DAG merge (topological sort, causal consistency)
- [ ] Peer discovery via mDNS (LAN) and DNS-SD
- [ ] Verified peer identity exchange (signed NodeIdentity events)
- [ ] Lightweight replication protocol (delta sync by event ID set)

## Phase 2 — Post-quantum cryptography

- [ ] Add ML-DSA (CRYSTALS-Dilithium) signing via `pqcrypto` crate
- [ ] Dual-signature transition protocol (Ed25519 + ML-DSA window)
- [ ] Update NodeIdentity capabilities field
- [ ] Scheduled Ed25519 deprecation after full mesh upgrade

## Phase 3 — Cognitive depth

- [ ] Persistent session context (multi-turn memory via ContentAddressedMemory)
- [ ] Tool-use framework: agents can invoke registered Python functions
- [ ] Planner upgrade: LLM-guided task decomposition (chain-of-thought)
- [ ] Confidence calibration using historical task results
- [ ] Model hot-swap: reload GGUF without node restart

## Phase 4 — Scalability & observability

- [ ] Prometheus metrics endpoint (`/metrics`)
- [ ] Structured JSONL audit log (all events with full payload)
- [ ] Horizontal cluster: shared event store (PostgreSQL or CockroachDB)
- [ ] Kubernetes Helm chart (autoscaling, anti-affinity for entropy diversity)
- [ ] Web UI: event graph explorer, beacon timeline, node health

## Phase 5 — Research extensions

- [ ] Quantum random number integration (IBMQ / IonQ via REST API)
- [ ] Neuromorphic model backend (Intel Loihi, if accessible)
- [ ] Formal verification of DAG convergence (TLA+ spec)
- [ ] Academic paper: "Physical-Entropy-Anchored Cognitive Event Logs"
