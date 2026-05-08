"""ChainLabeler — semantic labeling and self-learning from the event DAG.

The labeler walks the topologically-sorted local event chain, groups events
into logical **sessions** (contiguous runs sharing a common root), then
applies lightweight semantic labels to each session:

  - ``intent``   — what the agent was trying to do
  - ``outcome``  — success / partial / failure
  - ``quality``  — high / medium / low (derived from confidence scores where
                   available, otherwise from outcome)
  - ``domain``   — primary domain tag: reasoning, memory, tool, chain, atm, …
  - ``summary``  — one-line human-readable caption

Labelled sessions are:
  1. Persisted in ``ContentAddressedMemory`` under the ``chain_labels`` namespace.
  2. Fed back into ``AutoLearner`` so the system literally learns from its own
     chain history.
  3. Ready to be exported to a GitHub Gist via ``GistStore.push``.

Usage::

    labeler = ChainLabeler(memory, auto_learner)
    sessions = labeler.label(events)           # list[LabelledSession]
    labeler.flush_to_memory(sessions)          # persist + learn
    chains   = labeler.as_export_dicts(sessions)   # → GistStore.push(…, chains)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .dag import topological_sort
from .models import Event, EventType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain classifier — maps EventType prefixes to domain tags
# ---------------------------------------------------------------------------

_DOMAIN_MAP: List[Tuple[str, str]] = [
    ("atm_", "atm"),
    ("reasoning_", "reasoning"),
    ("chain_", "chain"),
    ("workflow_", "workflow"),
    ("tool_", "tool"),
    ("memory_", "memory"),
    ("user_profile", "memory"),
    ("auto_learn", "self_learning"),
    ("orc_", "orc"),
    ("agent_", "agent"),
    ("delegate_", "delegation"),
    ("session_", "session"),
]


def _classify_domain(event: Event) -> str:
    val = event.event_type.value
    for prefix, domain in _DOMAIN_MAP:
        if val.startswith(prefix):
            return domain
    return "general"


def _is_terminal(event: Event) -> bool:
    """True if this event type ends a logical unit of work."""
    val = event.event_type.value
    return any(val.endswith(s) for s in ("_complete", "_completed", "_failed", "_failed", "response"))


def _extract_confidence(event: Event) -> Optional[float]:
    """Pull a confidence value from event payload if present."""
    payload = event.payload if hasattr(event, "payload") else {}
    if isinstance(payload, dict):
        for key in ("confidence", "score", "quality"):
            v = payload.get(key)
            if isinstance(v, (int, float)):
                return float(v)
    return None


# ---------------------------------------------------------------------------
# LabelledSession dataclass (plain dict-serialisable)
# ---------------------------------------------------------------------------


class LabelledSession:
    """One labelled logical session extracted from the DAG."""

    __slots__ = (
        "session_id",
        "root_event_id",
        "event_ids",
        "intent",
        "outcome",
        "quality",
        "domain",
        "summary",
        "confidence",
        "labelled_at_ns",
    )

    def __init__(
        self,
        session_id: str,
        root_event_id: str,
        event_ids: List[str],
        intent: str,
        outcome: str,
        quality: str,
        domain: str,
        summary: str,
        confidence: float,
    ) -> None:
        self.session_id = session_id
        self.root_event_id = root_event_id
        self.event_ids = event_ids
        self.intent = intent
        self.outcome = outcome
        self.quality = quality
        self.domain = domain
        self.summary = summary
        self.confidence = confidence
        self.labelled_at_ns = time.time_ns()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "root_event_id": self.root_event_id,
            "event_ids": self.event_ids,
            "intent": self.intent,
            "outcome": self.outcome,
            "quality": self.quality,
            "domain": self.domain,
            "summary": self.summary,
            "confidence": self.confidence,
            "labelled_at_ns": self.labelled_at_ns,
        }


# ---------------------------------------------------------------------------
# ChainLabeler
# ---------------------------------------------------------------------------


class ChainLabeler:
    """Walk the local event DAG, label sessions, and feed them back to
    AutoLearner so the node learns from its own historical chains.

    Args:
        memory: ``ContentAddressedMemory`` instance for persistence.
        auto_learner: ``AutoLearner`` instance for self-learning feedback.
        min_events_per_session: Ignore sessions shorter than this (noise filter).
    """

    _NS = "chain_labels"

    def __init__(
        self,
        memory: Any,  # ContentAddressedMemory — avoid circular import
        auto_learner: Any,  # AutoLearner
        min_events_per_session: int = 2,
    ) -> None:
        self._memory = memory
        self._learner = auto_learner
        self._min_events = min_events_per_session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def label(self, events: List[Event]) -> List[LabelledSession]:
        """Topologically sort events, group into sessions, label each one.

        A *session* is a maximal connected sub-graph rooted at a REQUEST or
        ATM_STARTED event.  Events with no known parent start their own root.
        """
        if not events:
            return []

        sorted_events = topological_sort(events)
        sessions = self._group_sessions(sorted_events)
        labelled: List[LabelledSession] = []
        for root_id, session_events in sessions.items():
            if len(session_events) < self._min_events:
                continue
            ls = self._label_session(root_id, session_events)
            labelled.append(ls)

        logger.debug("ChainLabeler: labelled %d sessions from %d events", len(labelled), len(events))
        return labelled

    def flush_to_memory(self, sessions: List[LabelledSession]) -> None:
        """Persist labelled sessions to ContentAddressedMemory and feed
        high-quality ones back into AutoLearner."""
        for ls in sessions:
            # Persist
            try:
                key = f"session-{ls.session_id}"
                content_hash = self._memory.put(ls.to_dict())
                self._memory.bind(self._NS, key, content_hash)
            except Exception as exc:
                logger.debug("ChainLabeler: persist failed: %s", exc)

            # Self-learn from high / medium quality sessions
            if ls.quality in ("high", "medium") and self._learner is not None:
                prompt = f"[chain:{ls.domain}] {ls.intent}"
                output = ls.summary
                try:
                    self._learner.record(
                        prompt=prompt,
                        output=output,
                        confidence=ls.confidence,
                        source="chain_label",
                        model_used="chain_labeler",
                    )
                except Exception as exc:
                    logger.debug("ChainLabeler: auto_learn record failed: %s", exc)

    def as_export_dicts(self, sessions: List[LabelledSession]) -> List[Dict[str, Any]]:
        """Serialise sessions for GistStore export."""
        return [s.to_dict() for s in sessions]

    def load_from_memory(self) -> List[Dict[str, Any]]:
        """Load all previously persisted labelled sessions from memory."""
        out: List[Dict[str, Any]] = []
        try:
            for key in self._memory.list_keys(self._NS):
                raw = self._memory.get_by_key(self._NS, key)
                if raw is None:
                    continue
                try:
                    out.append(json.loads(raw) if isinstance(raw, (str, bytes)) else raw)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("ChainLabeler.load_from_memory: %s", exc)
        return out

    def ingest_remote_labels(self, remote_labels: List[Dict[str, Any]]) -> int:
        """Ingest labelled sessions pulled from a remote mirror's Gist.

        High-quality sessions from peers are fed into AutoLearner so the node
        learns from the broader mesh's collective experience.

        Returns the number of sessions ingested.
        """
        ingested = 0
        for raw in remote_labels:
            quality = raw.get("quality", "low")
            domain = raw.get("domain", "general")
            intent = raw.get("intent", "")
            summary = raw.get("summary", "")
            confidence = float(raw.get("confidence", 0.0))
            if quality in ("high", "medium") and self._learner is not None and intent and summary:
                try:
                    recorded = self._learner.record(
                        prompt=f"[remote:{domain}] {intent}",
                        output=summary,
                        confidence=confidence,
                        source="gist_mirror",
                        model_used="remote_chain_labeler",
                    )
                    if recorded:
                        ingested += 1
                except Exception:
                    pass
        logger.info("ChainLabeler: ingested %d remote labelled sessions", ingested)
        return ingested

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _group_sessions(self, sorted_events: List[Event]) -> Dict[str, List[Event]]:
        """Group events into sessions by tracing parent edges back to roots."""
        event_map = {e.id: e for e in sorted_events}
        # Map each event_id → session root id
        session_root: Dict[str, str] = {}

        def find_root(eid: str) -> str:
            if eid not in event_map:
                return eid
            e = event_map[eid]
            if not e.parent_ids:
                return eid
            # Pick first known parent as canonical ancestor
            for pid in e.parent_ids:
                if pid in event_map:
                    root = find_root(pid)
                    session_root[eid] = root
                    return root
            return eid

        sessions: Dict[str, List[Event]] = {}
        for event in sorted_events:
            root = find_root(event.id)
            session_root[event.id] = root
            sessions.setdefault(root, []).append(event)

        return sessions

    def _label_session(self, root_id: str, events: List[Event]) -> LabelledSession:
        """Derive labels for one session."""
        import uuid as _uuid

        # --- Domain: most frequent domain among events ---
        domain_counts: Dict[str, int] = {}
        for e in events:
            d = _classify_domain(e)
            domain_counts[d] = domain_counts.get(d, 0) + 1
        domain = max(domain_counts, key=lambda k: domain_counts[k])

        # --- Outcome: check for any failure event ---
        types = {e.event_type for e in events}
        failed_types = {
            EventType.ATM_FAILED,
            EventType.CHAIN_FAILED,
            EventType.WORKFLOW_FAILED,
            EventType.WORKFLOW_STEP_FAILED,
            EventType.DELEGATE_FAILED,
        }
        has_failure = bool(types & failed_types)
        completed_types = {
            EventType.ATM_COMPLETE,
            EventType.CHAIN_COMPLETED,
            EventType.WORKFLOW_COMPLETED,
            EventType.RESPONSE,
        }
        has_completion = bool(types & completed_types)

        if has_failure:
            outcome = "failure"
        elif has_completion:
            outcome = "success"
        else:
            outcome = "partial"

        # --- Confidence: mean of extractable confidence values ---
        confs = [c for c in (_extract_confidence(e) for e in events) if c is not None]
        confidence = sum(confs) / len(confs) if confs else (0.85 if outcome == "success" else 0.40)

        # --- Quality tier ---
        if confidence >= 0.80 and outcome == "success":
            quality = "high"
        elif confidence >= 0.55 or outcome == "partial":
            quality = "medium"
        else:
            quality = "low"

        # --- Intent: derive from root event type + domain ---
        root_event = next((e for e in events if e.id == root_id), events[0])
        intent = f"{root_event.event_type.value.replace('_', ' ')} [{domain}]"

        # --- Summary ---
        n = len(events)
        summary = (
            f"{outcome.capitalize()} {domain} chain: {n} event(s), "
            f"quality={quality}, conf={confidence:.2f}. "
            f"Root: {root_event.event_type.value}"
        )

        return LabelledSession(
            session_id=str(_uuid.uuid4()),
            root_event_id=root_id,
            event_ids=[e.id for e in events],
            intent=intent,
            outcome=outcome,
            quality=quality,
            domain=domain,
            summary=summary,
            confidence=confidence,
        )
