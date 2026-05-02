"""Agent persona and avatar generation helpers."""

from __future__ import annotations

import hashlib
import random
from typing import List

from .models import AgentPersona

_ARCHETYPES = [
    "Systems Cartographer",
    "Entropy Analyst",
    "Protocol Alchemist",
    "Mesh Sentinel",
    "Cognitive Blacksmith",
    "Signal Interpreter",
    "Ledger Forensic",
    "Runtime Choreographer",
]

_STYLES = [
    "terse and surgical",
    "calm and explanatory",
    "experimental and bold",
    "skeptical and evidence-first",
    "mentor-like and pragmatic",
    "architectural and systems-level",
]

_DOMAINS = [
    "distributed systems",
    "agentic workflows",
    "cryptographic integrity",
    "observability",
    "runtime optimization",
    "developer tooling",
    "safety guardrails",
]

_QUIRKS = [
    "always proposes a rollback path",
    "annotates assumptions explicitly",
    "prefers measurable outcomes",
    "optimizes for deterministic replay",
    "uses small iterative steps",
    "flags policy and safety boundaries",
    "separates fast path and control path",
]


def generate_persona(agent_name: str, seed: str) -> AgentPersona:
    """Generate a stable persona from a seed."""
    rng = random.Random(seed)
    archetype = rng.choice(_ARCHETYPES)
    domain = rng.choice(_DOMAINS)
    style = rng.choice(_STYLES)
    codename = f"{archetype.split()[0]}-{seed[:4].upper()}"
    mission = f"Specialized in {domain}; communicates in a {style} style."
    quirks = rng.sample(_QUIRKS, k=2)
    image_prompt = (
        f"Portrait icon for AI agent '{agent_name}', archetype '{archetype}', "
        f"minimal sci-fi glyph, high-contrast, clean vector style, unique motif."
    )
    return AgentPersona(
        seed=seed,
        codename=codename,
        archetype=archetype,
        style=style,
        mission=mission,
        quirks=quirks,
        image_prompt=image_prompt,
    )


def render_avatar_svg(seed: str, label: str) -> str:
    """Render a deterministic SVG avatar from seed + label."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    c1 = f"#{digest[0:6]}"
    c2 = f"#{digest[6:12]}"
    c3 = f"#{digest[12:18]}"

    initials = "".join(ch for ch in label if ch.isalnum()).upper()[:2] or "AI"

    circles: List[str] = []
    for i in range(0, 18, 6):
        x = 18 + (int(digest[i : i + 2], 16) % 92)
        y = 18 + (int(digest[i + 2 : i + 4], 16) % 92)
        r = 8 + (int(digest[i + 4 : i + 6], 16) % 14)
        circles.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c3}" fill-opacity="0.28" />'
        )

    circles_svg = "\n    ".join(circles)

    return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"128\" height=\"128\" viewBox=\"0 0 128 128\" role=\"img\" aria-label=\"{label}\">\n  <defs>\n    <linearGradient id=\"bg\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">\n      <stop offset=\"0%\" stop-color=\"{c1}\" />\n      <stop offset=\"100%\" stop-color=\"{c2}\" />\n    </linearGradient>\n  </defs>\n  <rect width=\"128\" height=\"128\" rx=\"22\" fill=\"url(#bg)\" />\n  {circles_svg}\n  <circle cx=\"64\" cy=\"64\" r=\"40\" fill=\"#0b1020\" fill-opacity=\"0.24\" />\n  <text x=\"64\" y=\"74\" text-anchor=\"middle\" font-family=\"Verdana, sans-serif\" font-size=\"36\" font-weight=\"700\" fill=\"white\">{initials}</text>\n</svg>"""
