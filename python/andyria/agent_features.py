"""Per-agent feature bootstrap profiles (skills, cron, environments, modes)."""

from __future__ import annotations

from typing import Dict, List

from .models import AgentDefinition

_BASE_SKILLS = [
    "atm.iterative_thinking",
    "atm.self_reflection",
    "planner.task_decomposition",
    "verifier.policy_and_quality",
    "memory.content_addressed_state",
    "ledger.signed_event_audit",
    "mesh.peer_sync_basics",
    "tools.registry_dispatch",
    "chains.multi_agent_execution",
    "reasoning.chain_of_thought",
    "auto_learn.self_improvement",
]

_ARCHETYPE_SKILL_BOOST = {
    "Entropy Analyst": ["entropy.health_monitoring", "beacon.integrity_checks"],
    "Mesh Sentinel": ["mesh.replication_hygiene", "mesh.causal_consistency"],
    "Protocol Alchemist": ["protocol.contract_design", "api.schema_evolution"],
    "Runtime Choreographer": ["runtime.optimization", "dev.hot_reload_workflow"],
    "Ledger Forensic": ["ledger.event_forensics", "signature.verification"],
    "Reasoning Agent": ["reasoning.deep_decomposition", "auto_learn.pattern_extraction"],
}


def predominant_skills_for_agent(agent: AgentDefinition) -> List[str]:
    """Return ordered predominant skills that should be imported for an agent."""
    skills = list(_BASE_SKILLS)
    if agent.persona is not None:
        skills.extend(_ARCHETYPE_SKILL_BOOST.get(agent.persona.archetype, []))

    # Include tool-linked skill tags for agent-specific specialization.
    for tool in agent.tools:
        skills.append(f"tool.{tool}.operations")

    # De-duplicate while preserving order.
    seen = set()
    unique: List[str] = []
    for skill in skills:
        if skill in seen:
            continue
        seen.add(skill)
        unique.append(skill)
    return unique


def default_agent_modes() -> Dict[str, bool]:
    return {
        "auto_self_actualize": True,
        "conical_laws_guarded": True,
        "dreamscapes_sleepmode": True,
        "auto_resume": True,
    }


def default_agent_environments() -> Dict[str, str]:
    return {
        "runtime": "containerized-dev",
        "llm_provider": "active-router-default",
        "policy": "strict-audit",
        "telemetry": "event-stream",
    }
