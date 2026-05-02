"""Verifier for Andyria: checks task outputs for quality and policy compliance.

Every result that passes verification is committed as a signed Event in
the append-only event log so the verification decision is auditable.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import Event, EventType, TaskResult


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


# Policy: reject outputs that contain these literal strings (case-sensitive)
_POLICY_REJECT = [
    "rm -rf",
    "__import__",
    "exec(",
    "eval(",
    "subprocess.call",
    "os.system(",
]


class Verifier:
    """Verifies task outputs and produces signed Event records.

    Checks applied (in order):
        1. Non-empty output
        2. Output length within bounds
        3. Confidence above threshold
        4. Policy pattern rejection

    A signed Event is produced for every result that passes, recording
    the output hash, confidence, model used, and entropy beacon reference.
    """

    def __init__(
        self,
        node_id: str,
        private_key: Ed25519PrivateKey,
        min_confidence: float = 0.1,
        max_output_chars: int = 32_768,
    ) -> None:
        self._node_id = node_id
        self._private_key = private_key
        self._min_confidence = min_confidence
        self._max_output_chars = max_output_chars

    def verify(
        self,
        result: TaskResult,
        entropy_beacon_id: str,
        parent_event_ids: List[str],
    ) -> Tuple[TaskResult, Optional[Event]]:
        """Verify ``result`` and produce a signed Event if it passes.

        Returns
        -------
        (updated_result, event_or_none)
        """
        passed, reason = self._check(result)
        result = result.model_copy(update={"verified": passed})

        if not passed:
            return result, None

        event = self._build_event(result, reason, entropy_beacon_id, parent_event_ids)
        return result, event

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self, result: TaskResult) -> Tuple[bool, str]:
        if not result.output or not result.output.strip():
            return False, "empty output"

        if len(result.output) > self._max_output_chars:
            return False, f"output exceeds {self._max_output_chars} characters"

        if result.confidence < self._min_confidence:
            return (
                False,
                f"confidence {result.confidence:.2f} below threshold {self._min_confidence:.2f}",
            )

        for pattern in _POLICY_REJECT:
            if pattern in result.output:
                return False, f"policy violation: contains '{pattern}'"

        return True, "passed"

    def _build_event(
        self,
        result: TaskResult,
        reason: str,
        entropy_beacon_id: str,
        parent_event_ids: List[str],
    ) -> Event:
        timestamp_ns = int(time.perf_counter_ns())

        payload = {
            "task_id": result.task_id,
            "output_hash": _hash(result.output.encode()),
            "confidence": result.confidence,
            "model_used": result.model_used,
            "verified": result.verified,
            "reason": reason,
        }
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = _hash(payload_bytes)

        sorted_parents = sorted(parent_event_ids)
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
            parent_ids=parent_event_ids,
            event_type=EventType.TASK_RESULT,
            payload_hash=payload_hash,
            entropy_beacon_id=entropy_beacon_id,
            timestamp_ns=timestamp_ns,
            node_id=self._node_id,
            signature="",
        )
        sig = self._private_key.sign(_canonical_event(event))
        event.signature = sig.hex()
        return event
