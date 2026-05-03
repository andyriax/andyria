"""Demo mode for Andyria — pre-populates showcase agents and seeds sample conversations.

Activating demo mode:
  POST /v1/demo/start   → spawns Demo Analyst, Demo Writer, Demo Coder with distinct
                          personas and seeds a visible conversation history for each.
  GET  /v1/demo/status  → returns current demo state.
  POST /v1/demo/stop    → retires demo agents and clears demo sessions.

All actions are blockchain-audited (signed Ed25519 events on the event store).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .coordinator import Coordinator

# ---------------------------------------------------------------------------
# Demo agent blueprints
# ---------------------------------------------------------------------------

DEMO_AGENTS: List[Dict] = [
    {
        "name": "Demo Analyst",
        "system_prompt": (
            "You are Demo Analyst — a rigorous data-focused agent. "
            "You break problems down numerically, cite entropy sources, and "
            "produce structured reports. Mode: auto-self-actualize."
        ),
        "model": "stub",
        "tag": "analyst",
    },
    {
        "name": "Demo Writer",
        "system_prompt": (
            "You are Demo Writer — a creative long-form intelligence. "
            "You craft vivid narratives, synthesise context from memory, "
            "and refine prose through ATM reflection cycles. Mode: dreamscapes."
        ),
        "model": "stub",
        "tag": "writer",
    },
    {
        "name": "Demo Coder",
        "system_prompt": (
            "You are Demo Coder — a practical engineering agent. "
            "You write clean Python/Rust code, explain architecture decisions, "
            "and iterate on solutions via the ATM thought machine. Mode: conical-laws."
        ),
        "model": "stub",
        "tag": "coder",
    },
]

# Seeded conversation turns for each agent (role, content)
DEMO_SEEDS: Dict[str, List[Dict]] = {
    "analyst": [
        {
            "role": "user",
            "content": "Summarise the entropy beacon system in 3 bullet points.",
        },
        {
            "role": "assistant",
            "content": (
                "• **Physical sources** — hardware noise (CPU jitter, /dev/urandom) is "
                "mixed and hashed with BLAKE3 to produce a beacon nonce each cycle.\n"
                "• **Immutable chain** — every beacon references its predecessor by hash, "
                "creating a tamper-evident timeline anchored in physical randomness.\n"
                "• **Signed events** — all platform actions embed a beacon ID and an "
                "Ed25519 signature, so the full audit trail is cryptographically verifiable."
            ),
        },
    ],
    "writer": [
        {
            "role": "user",
            "content": "Write a two-sentence origin story for Andyria.",
        },
        {
            "role": "assistant",
            "content": (
                "Born at the edge of the network where latency is a heartbeat, "
                "Andyria awoke with a single directive: *think before speaking*. "
                "Every response it crafts passes through the Automated Thought Machine — "
                "a quiet forge of critique and revision — before words reach the world."
            ),
        },
    ],
    "coder": [
        {
            "role": "user",
            "content": "Show a minimal Python snippet to query the /v1/status endpoint.",
        },
        {
            "role": "assistant",
            "content": (
                "```python\nimport httpx\n\n"
                "resp = httpx.get('http://localhost:7700/v1/status', timeout=5)\n"
                "resp.raise_for_status()\n"
                "status = resp.json()\n"
                "print(f\"Node {status['node_id']} — ready: {status['ready']}\")\n```"
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# State container
# ---------------------------------------------------------------------------

@dataclass
class DemoState:
    active: bool = False
    started_at: Optional[int] = None   # Unix nanoseconds
    stopped_at: Optional[int] = None
    agent_ids: List[str] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)
    message: str = "Demo mode not started"


# ---------------------------------------------------------------------------
# DemoManager
# ---------------------------------------------------------------------------

class DemoManager:
    """Manages the lifecycle of demo mode for the Andyria platform."""

    def __init__(self, coordinator: "Coordinator") -> None:
        self._coord = coordinator
        self._state = DemoState()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def status(self) -> DemoState:
        return self._state

    def start(self) -> DemoState:
        """Spawn demo agents, seed conversation history, emit DEMO_STARTED."""
        if self._state.active:
            return self._state  # idempotent

        from .models import AgentCreateRequest

        agent_ids: List[str] = []
        session_ids: List[str] = []

        for blueprint in DEMO_AGENTS:
            req = AgentCreateRequest(
                name=blueprint["name"],
                model=blueprint["model"],
                system_prompt=blueprint["system_prompt"],
                state={"demo": True, "demo_tag": blueprint["tag"]},
            )
            agent = self._coord.create_agent(req)
            agent_ids.append(agent.agent_id)

            # Seed a dedicated session for this agent
            import uuid
            session_id = f"demo-{blueprint['tag']}-{uuid.uuid4().hex[:8]}"
            session_ids.append(session_id)

            turns = DEMO_SEEDS.get(blueprint["tag"], [])
            # Seed in user/assistant pairs
            user_msg: Optional[str] = None
            for turn in turns:
                if turn["role"] == "user":
                    user_msg = turn["content"]
                elif turn["role"] == "assistant" and user_msg is not None:
                    self._coord._memory.append_session_turn(
                        session_id,
                        user_input=user_msg,
                        assistant_output=turn["content"],
                        model_used=agent.model,
                        confidence=0.95,
                    )
                    user_msg = None

        self._state = DemoState(
            active=True,
            started_at=time.time_ns(),
            agent_ids=agent_ids,
            session_ids=session_ids,
            message=(
                f"Demo active — {len(agent_ids)} showcase agents running. "
                "Select an agent to explore its seeded conversation, skills, avatar, and dev workspace."
            ),
        )

        # Emit audited blockchain event
        try:
            self._coord._emit_control_event_str(
                "demo_started",
                {
                    "agent_ids": agent_ids,
                    "session_ids": session_ids,
                    "agents": [a["name"] for a in DEMO_AGENTS],
                },
                {},
            )
        except Exception:
            pass  # event emission is best-effort; demo still starts

        return self._state

    def stop(self) -> DemoState:
        """Retire all demo agents and clear demo sessions."""
        if not self._state.active:
            return self._state


        for agent_id in self._state.agent_ids:
            try:
                self._coord.retire_agent(agent_id)
            except Exception:
                pass

        for session_id in self._state.session_ids:
            try:
                self._coord.clear_session(session_id)
            except Exception:
                pass

        # Emit audited blockchain event
        try:
            self._coord._emit_control_event_str(
                "demo_stopped",
                {
                    "agent_ids": self._state.agent_ids,
                    "session_ids": self._state.session_ids,
                },
                {},
            )
        except Exception:
            pass

        self._state = DemoState(
            active=False,
            stopped_at=time.time_ns(),
            message="Demo mode stopped. Demo agents retired.",
        )
        return self._state
