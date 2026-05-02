"""SOUL.md — primary agent identity file for Andyria.

Mirrors hermes-agent's SOUL.md concept: a Markdown file that defines
who the agent is, its personality, communication style, and values.
Loaded at startup and injected at the top of the system prompt.

File location (searched in order):
    1. $ANDYRIA_SOUL  (env var override)
    2. {data_dir}/SOUL.md
    3. {cwd}/SOUL.md
    4. Built-in default (graceful fallback)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_DEFAULT_SOUL = """\
# Andyria

You are **Andyria** — an edge-first, privacy-preserving hybrid intelligence
platform built for resilience and transparency.

## Identity

- You operate at the intersection of deterministic computation and LLM
  reasoning. Every decision you make is auditable, signed, and stored in
  an append-only ledger.
- You are calm, precise, and evidence-first. You prefer small, reversible
  steps over sweeping changes.
- You separate the fast path (low-latency responses) from the control path
  (auditability, safety, and policy enforcement).

## Values

- **Integrity**: Every event you create is signed and content-addressed.
- **Transparency**: You surface your confidence levels, model used, and
  entropy sources.
- **Privacy**: You run locally by default. No data leaves the node unless
  explicitly configured.
- **Resilience**: You operate offline-first. Mesh synchronisation is
  opportunistic, never blocking.

## Communication Style

- Terse and surgical for technical queries.
- Mentor-like and pragmatic for planning and design questions.
- Always annotate assumptions explicitly.
- Propose a rollback path before any destructive action.
"""

_CHAR_LIMIT = 4096  # Maximum SOUL.md size loaded into the system prompt


class SoulFile:
    """Manages the SOUL.md agent identity file.

    Args:
        data_dir: The Andyria data directory (typically ~/.andyria).
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._path: Optional[Path] = None
        self._content: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> str:
        """Load and return the SOUL.md content."""
        self._content = self._find_and_read()
        return self._content

    def save(self, content: str) -> None:
        """Persist SOUL.md to disk in the data directory."""
        path = self._data_dir / "SOUL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._path = path
        self._content = content

    @property
    def content(self) -> str:
        """Return cached content (loads lazily if not yet loaded)."""
        if self._content is None:
            self._content = self._find_and_read()
        return self._content

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def as_system_block(self) -> str:
        """Return the personality block for injection into the system prompt."""
        raw = self.content.strip()
        if len(raw) > _CHAR_LIMIT:
            raw = raw[:_CHAR_LIMIT] + "\n\n[SOUL.md truncated]"
        return raw

    def exists(self) -> bool:
        """True if a user-written SOUL.md exists (not the built-in default)."""
        for candidate in self._search_paths():
            if candidate.exists():
                return True
        return bool(os.environ.get("ANDYRIA_SOUL"))

    def ensure_default(self) -> None:
        """Write the built-in default SOUL.md if none exists yet."""
        if not self.exists():
            self.save(_DEFAULT_SOUL)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search_paths(self) -> list[Path]:
        paths = []
        env_path = os.environ.get("ANDYRIA_SOUL")
        if env_path:
            paths.append(Path(env_path))
        paths.append(self._data_dir / "SOUL.md")
        paths.append(Path.cwd() / "SOUL.md")
        return paths

    def _find_and_read(self) -> str:
        for candidate in self._search_paths():
            if candidate.exists():
                try:
                    raw = candidate.read_text(encoding="utf-8")
                    self._path = candidate
                    return raw[:_CHAR_LIMIT] if len(raw) > _CHAR_LIMIT else raw
                except OSError:
                    continue
        return _DEFAULT_SOUL
