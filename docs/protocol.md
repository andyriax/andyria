# Andyria Wire Protocol

## HTTP API (v1)

All endpoints are served on port **7700** by default.  
Interactive docs (Swagger UI): `http://localhost:7700/docs`

---

### Core Inference

#### POST /v1/infer

Submit a natural-language or symbolic request.

**Request body** (`application/json`):
```json
{
  "id": "<uuid>",
  "input": "calculate 6 * 7",
  "session_id": "<optional uuid>",
  "context": {}
}
```

**Response** (`200 OK`):
```json
{
  "request_id": "<uuid>",
  "output": "42",
  "confidence": 0.95,
  "tasks_completed": 1,
  "entropy_beacon_id": "<hex>",
  "event_ids": ["<hex>", "..."],
  "model_used": "symbolic_ast",
  "processing_ms": 3,
  "timestamp_ns": 1712000000000000000
}
```

#### GET /v1/status

Returns `NodeStatus`: `node_id`, `deployment_class`, `uptime_s`,
`requests_processed`, `entropy_beacons_generated`, `events_stored`,
`model_loaded`, `memory_objects`, `entropy_sources`, `ready`,
`readiness_detail`.

#### GET /v1/events

Query parameters: `event_type`, `agent_id`, `tab_id`, `limit` (default 200).  
Returns an array of signed `Event` objects (newest first).

#### WebSocket /v1/stream

Real-time event stream. Query parameters: `event_type`, `agent_id`, `tab_id`.  
Each message: `{"event": {...}, "metadata": {...}}`.

#### GET /v1/beacon/{beacon_id}

Returns the `EntropyBeacon` with that ID, or 404.

#### GET /v1/tools

Returns the list of registered tool names.

#### GET /health

```json
{"status": "ok", "node_id": "...", "ready": true, "detail": "...", "timestamp_ns": ...}
```

---

### Configuration & Models

#### GET /v1/config

Returns the current `NodeConfig` (ollama_url, model, data_dir, etc.).

#### PATCH /v1/config

Partial update of node configuration. Body: `NodeConfigUpdate` fields.

#### GET /v1/models

Returns a list of model names available from the configured Ollama instance.

---

### Sessions

#### GET /v1/session/{session_id}

Returns a `SessionContext` for the given session.

#### DELETE /v1/session/{session_id}

Clears a session's turn history.

---

### Agents

#### GET /v1/agents/presets

Returns the list of agent preset templates from `deploy/presets/agents.json`.

#### GET /v1/agents?include_inactive=false

List all active agents (or all if `include_inactive=true`).

#### POST /v1/agents → 201

Create an agent. Body: `AgentCreateRequest` (`name`, optional `model`,
`system_prompt`, `persona`, `tools`, `memory_scope`, …). If `model` is omitted,
the runtime falls back to `symbolic_ast`.

#### GET /v1/agents/{agent_id}

Get a single `AgentDefinition`.

#### PATCH /v1/agents/{agent_id}

Partial update of an agent. Body: `AgentUpdateRequest`.

#### POST /v1/agents/{agent_id}/clone → 201

Clone an agent. Body: `AgentCloneRequest` (`new_name`).

#### DELETE /v1/agents/{agent_id}

Retire (soft-delete) an agent.

#### GET /v1/agents/{agent_id}/avatar.svg

Returns a procedurally generated SVG avatar for the agent's persona.

#### GET /v1/agents/{agent_id}/skills

Returns `{agent_id, skills, modes, environments}` — the computed skill profile
for the agent.

#### GET /v1/agents/{agent_id}/dev

Prepares and returns an `AgentDevWorkspace` — creates a workspace directory
with `agent.profile.json`, `skills.imports.txt`, `cron.auto-develop`,
`sleepmode.dreamscapes.json`, `workspace.manifest.json`, `.env.agent`, and
`README.md`. Returns `{agent_id, workspace_path, ide_url}`.

---

### Tabs

Tab projections track UI viewports over agent state.

#### GET /v1/tabs

List all `TabProjection` objects.

#### POST /v1/tabs → 201

Create a tab. Body: `TabCreateRequest` (`agent_id`, `viewport_mode`).

#### GET /v1/tabs/{tab_id}

Get a single tab.

#### PATCH /v1/tabs/{tab_id}

Update a tab's `viewport_mode` or `agent_id`.

#### DELETE /v1/tabs/{tab_id}

Delete a tab.

---

### Chains

Sequential multi-agent execution pipelines.

#### GET /v1/chains

List all `ChainDefinition` objects.

#### POST /v1/chains → 201

Create a chain. Body: `ChainCreateRequest` (`name`, `steps: [agent_id, ...]`).

#### GET /v1/chains/{chain_id}

Get a single chain.

#### DELETE /v1/chains/{chain_id}

Delete a chain.

#### POST /v1/chains/{chain_id}/run

Run a chain. Body: `ChainRunRequest` (`input`, `session_id?`).  
Returns `AndyriaResponse` of the final step's output.

---

### ATM (Automated Thought Machine)

#### POST /v1/atm/think

Run the ATM iterative generate → critique → revise loop.

**Request body**:
```json
{
  "prompt": "Explain quantum entanglement simply",
  "max_iterations": 3,
  "context": {}
}
```

**Response**: `ATMThoughtResponse`
```json
{
  "thought_id": "<uuid>",
  "prompt": "...",
  "steps": [
    {
      "step": 1,
      "output": "...",
      "critique": "...",
      "confidence": 0.82,
      "model_used": "ollama_http",
      "elapsed_ms": 412
    }
  ],
  "final_output": "...",
  "final_confidence": 0.91,
  "total_ms": 1240,
  "timestamp_ns": 1712000000000000000
}
```

#### POST /v1/atm/reflect

Single reflection pass. Provide `context.draft` for critique of an existing
draft; otherwise falls back to one think iteration.

---

### Peers (Mesh)

#### GET /v1/peers

Returns the list of known peer nodes and their sync status.

#### POST /v1/peers

Add a peer. Body: `{"url": "http://peer-host:7700"}`.

---

### Memory

Operates on `MEMORY.md` (learned facts) and `USER.md` (user preferences).

#### POST /v1/memory

Body: `MemoryOpRequest` — `op` ∈ `{read, add, remove, update, clear}`,
`file` ∈ `{MEMORY, USER}`, `text`, `old_text`, `new_text`.

#### GET /v1/memory/{file}

Read `MEMORY` or `USER` file contents.

---

### Soul

#### GET /v1/soul

Returns `{"content": "...", "path": "..."}` — the `SOUL.md` identity file.

#### PUT /v1/soul

Replace `SOUL.md`. Body: `{"content": "..."}`.

---

### Learned Patterns

#### GET /v1/learned

Returns all `[learned]`-tagged entries distilled from `MEMORY.md`.

#### POST /v1/learn/reset

Remove all `[learned]` entries from `MEMORY.md`.

---

### Surprise Prompt

#### GET /v1/prompts/surprise

Returns `{"prompt": "..."}` — a dynamically generated creative prompt.

---

### Skills

#### POST /v1/skills

Body: `SkillRequest` — `action` ∈ `{list, view, search, create, update, delete}`.

| action | Extra fields |
|---|---|
| list | `category?` |
| view | `name` |
| search | `query` |
| create / update | `name`, `content`, `description?`, `tags?` |
| delete | `name` |

---

### Cron

#### GET /v1/cron

List all scheduled jobs (`CronJobInfo`).

#### POST /v1/cron

Create a job. Body: `CronJobCreate` (`name`, `expression`, `task`, `platform?`).

#### DELETE /v1/cron/{job_id}

Remove a scheduled job.

---

### Todos

#### POST /v1/todo

Body: `TodoRequest` — `action` ∈ `{list, add, update, done, cancel, remove}`.

| action | Extra fields |
|---|---|
| list | `status_filter?` |
| add | `text` |
| update | `item_id`, `status?`, `text?` |
| done / cancel / remove | `item_id` |

---

### Demo Mode

#### GET /v1/demo

Returns current `DemoStatus` (active, agent_ids, session_ids).

#### POST /v1/demo/start → 201

Activate demo mode: spawns showcase agents and seeds conversation history.

#### POST /v1/demo/stop

Deactivate demo mode: retires demo agents and clears demo sessions.

---

## EntropyBeacon Schema

```json
{
  "id":               "hex-64",
  "timestamp_ns":     1712000000000000000,
  "sources":          ["os_urandom","clock_jitter"],
  "raw_entropy_hash": "hex-64",
  "nonce":            "hex-64",
  "node_id":          "andyria-node-0",
  "signature":        "hex-128"
}
```

`signature` = Ed25519 over the canonical JSON of the above fields **excluding** `signature`.

---

## Event Schema

```json
{
  "id":                "hex-64",
  "parent_ids":        ["hex-64"],
  "event_type":        "task_result",
  "payload_hash":      "hex-64",
  "entropy_beacon_id": "hex-64",
  "timestamp_ns":      1712000000000000000,
  "node_id":           "andyria-node-0",
  "signature":         "hex-128"
}
```

`id` = BLAKE3( sorted(parent_ids) ‖ payload_hash ‖ entropy_beacon_id ‖ timestamp_ns ‖ node_id )

---

## Post-Quantum Upgrade Protocol

When nodes negotiate a dual-signature window:

1. Old node: `capabilities = ["ed25519"]`
2. New node: `capabilities = ["ed25519", "ml_dsa_44"]`
3. During transition, events carry **both** `signature` and `pq_signature`.
4. After all peers upgrade, `signature` (Ed25519) is deprecated.

The transition is backward-compatible: peers that only understand Ed25519 still
accept events during the window.
