# Andyria Wire Protocol

## HTTP API (v1)

All endpoints are served on port **7700** by default.

### POST /v1/infer

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

### GET /v1/status

Returns `NodeStatus`: node_id, deployment_class, uptime_s, requests_processed,
entropy_beacons_generated, events_stored, model_loaded, memory_objects,
entropy_sources.

### GET /v1/events?limit=100

Returns an array of signed `Event` objects (newest first).

### GET /v1/beacon/{beacon_id}

Returns the `EntropyBeacon` with that ID, or 404.

### GET /health

Returns `{"status":"ok","node_id":"...","timestamp_ns":...}`.

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
