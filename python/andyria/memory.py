"""Content-addressed memory for Andyria.

All values are stored by their BLAKE3 / SHA3-256 content hash, making
every stored artifact self-verifying and deduplicated. Named bindings
(namespace → key → hash) provide human-readable lookup without breaking
the content-addressed guarantee.

Checkpoints produce signed ``Event`` records that can be committed to
the append-only ledger, anchoring the memory state to the event graph.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import Event, EventType, SessionContext, SessionTurn


def _hash(data: bytes) -> str:
    try:
        import blake3  # type: ignore
        return blake3.blake3(data).hexdigest()
    except ImportError:
        return hashlib.sha3_256(data).hexdigest()


def _canonical_event(event: Event) -> bytes:
    return json.dumps(
        {
            "id": event.id,
            "parent_ids": event.parent_ids,
            "event_type": event.event_type.value,
            "payload_hash": event.payload_hash,
            "entropy_beacon_id": event.entropy_beacon_id,
            "timestamp_ns": event.timestamp_ns,
            "node_id": event.node_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class ContentAddressedMemory:
    """Persistent content-addressed local memory store.

    Layout on disk::

        {data_dir}/memory/objects/{content_hash}   — raw value blobs
        {data_dir}/memory/index/{namespace}/{key}  — hash pointers
    """

    def __init__(
        self,
        data_dir: Path,
        node_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self._store_dir = data_dir / "memory" / "objects"
        self._index_dir = data_dir / "memory" / "index"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._node_id = node_id
        self._private_key = private_key

    # ------------------------------------------------------------------
    # Core content-addressed operations
    # ------------------------------------------------------------------

    def put(self, value: Any) -> str:
        """Serialize ``value``, persist it, and return its content hash."""
        if isinstance(value, (dict, list)):
            data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        elif isinstance(value, str):
            data = value.encode()
        elif isinstance(value, bytes):
            data = value
        else:
            data = json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode()

        content_hash = _hash(data)
        obj_path = self._store_dir / content_hash
        if not obj_path.exists():
            obj_path.write_bytes(data)
        return content_hash

    def get(self, content_hash: str) -> Optional[bytes]:
        """Retrieve raw bytes by content hash."""
        obj_path = self._store_dir / content_hash
        return obj_path.read_bytes() if obj_path.exists() else None

    def get_json(self, content_hash: str) -> Optional[Any]:
        raw = self.get(content_hash)
        return json.loads(raw) if raw is not None else None

    # ------------------------------------------------------------------
    # Named bindings
    # ------------------------------------------------------------------

    def bind(self, namespace: str, key: str, content_hash: str) -> None:
        """Associate ``{namespace}/{key}`` with a content hash."""
        ns_dir = self._index_dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        (ns_dir / key).write_text(content_hash)

    def resolve(self, namespace: str, key: str) -> Optional[str]:
        """Resolve a named binding to its content hash."""
        path = self._index_dir / namespace / key
        return path.read_text().strip() if path.exists() else None

    def get_by_key(self, namespace: str, key: str) -> Optional[bytes]:
        content_hash = self.resolve(namespace, key)
        return self.get(content_hash) if content_hash else None

    def list_keys(self, namespace: str) -> List[str]:
        """List all keys bound under a namespace."""
        ns_dir = self._index_dir / namespace
        if not ns_dir.exists():
            return []
        return sorted(p.name for p in ns_dir.iterdir() if p.is_file())

    def delete_binding(self, namespace: str, key: str) -> None:
        """Delete a named binding without removing underlying object blobs."""
        path = self._index_dir / namespace / key
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        label: str,
        entropy_beacon_id: str,
        parent_ids: List[str],
    ) -> Event:
        """Create and sign a checkpoint event anchoring current memory state.

        The checkpoint payload lists every stored content hash (the manifest),
        hashes it, and produces a signed ``Event`` for inclusion in the ledger.
        Peers can replay this event to verify memory consistency.
        """
        timestamp_ns = int(time.perf_counter_ns())
        manifest = sorted(p.name for p in self._store_dir.iterdir() if p.is_file())

        payload = {
            "label": label,
            "manifest": manifest,
            "timestamp_ns": timestamp_ns,
            "node_id": self._node_id,
        }
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = _hash(payload_bytes)

        sorted_parents = sorted(parent_ids)
        id_input = json.dumps(
            {
                "parent_ids": sorted_parents,
                "payload_hash": payload_hash,
                "entropy_beacon_id": entropy_beacon_id,
                "timestamp_ns": timestamp_ns,
                "node_id": self._node_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        event_id = _hash(id_input)

        event = Event(
            id=event_id,
            parent_ids=parent_ids,
            event_type=EventType.CHECKPOINT,
            payload_hash=payload_hash,
            entropy_beacon_id=entropy_beacon_id,
            timestamp_ns=timestamp_ns,
            node_id=self._node_id,
            signature="",
        )
        sig = self._private_key.sign(_canonical_event(event))
        event.signature = sig.hex()
        return event

    # ------------------------------------------------------------------
    # Session context helpers
    # ------------------------------------------------------------------

    _SESSION_NS = "sessions"
    _MAX_TURNS = 20  # rolling window — keep most recent N turns

    def get_session(self, session_id: str) -> Optional[SessionContext]:
        """Return the current SessionContext for ``session_id``, or None."""
        raw = self.get_by_key(self._SESSION_NS, session_id)
        if raw is None:
            return None
        try:
            return SessionContext.model_validate_json(raw)
        except Exception:
            return None

    def append_session_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_output: str,
        model_used: str = "stub",
        confidence: float = 0.0,
    ) -> SessionContext:
        """Append a user+assistant turn pair and persist the session."""
        now = int(time.perf_counter_ns())
        existing = self.get_session(session_id)
        if existing is None:
            existing = SessionContext(session_id=session_id, created_at=now)

        existing.turns.append(SessionTurn(role="user", content=user_input, timestamp_ns=now))
        existing.turns.append(
            SessionTurn(
                role="assistant",
                content=assistant_output,
                model_used=model_used,
                confidence=confidence,
                timestamp_ns=now,
            )
        )
        # Trim to rolling window (keep pairs, so trim to even number)
        max_entries = self._MAX_TURNS * 2
        if len(existing.turns) > max_entries:
            existing.turns = existing.turns[-max_entries:]

        existing.updated_at = now
        payload = existing.model_dump_json().encode()
        content_hash = self.put(payload)
        self.bind(self._SESSION_NS, session_id, content_hash)
        return existing

    def clear_session(self, session_id: str) -> None:
        """Remove all stored context for ``session_id``."""
        path = self._index_dir / self._SESSION_NS / session_id
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def object_count(self) -> int:
        return sum(1 for p in self._store_dir.iterdir() if p.is_file())
