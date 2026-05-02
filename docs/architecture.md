# Andyria Architecture

## Overview

Andyria is a **hybrid cognitive runtime** — a node-local intelligence loop
anchored to physical reality through cryptographically signed entropy beacons,
persisted in a lightweight append-only event log, and scalable from a
Raspberry Pi Zero 2W to a multi-node cluster.

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  Andyria Node                                                       │
│                                                                     │
│  HTTP API / CLI                                                     │
│       │                                                             │
│       ▼                                                             │
│  Coordinator ──── EntropyBeaconFactory ◄── Physical Sources        │
│       │                (signed)              (hwrng, jitter,        │
│       │                                        thermal, stats)      │
│       ▼                                                             │
│  Planner (rule-based decomposition) ──── EntropyBeacon.id          │
│       │                                                             │
│       ▼                                                             │
│  ModelRouter                                                        │
│   ├── SymbolicSolver  (AST math, no eval())                        │
│   ├── LlamaLocal      (llama-cpp-python, GGUF)                     │
│   ├── OllamaHttp      (HTTP proxy to local Ollama)                 │
│   └── StubModel       (offline fallback)                           │
│       │                                                             │
│       ▼                                                             │
│  Verifier (quality + policy) ──── signed Event → EventLog          │
│       │                                                             │
│       ▼                                                             │
│  ContentAddressedMemory  (BLAKE3-keyed blobs + named bindings)     │
│       │                                                             │
│       ▼                                                             │
│  AndyriaResponse                                                    │
└─────────────────────────────────────────────────────────────────────┘
```

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
