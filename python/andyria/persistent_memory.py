"""Persistent bounded memory for Andyria — MEMORY.md and USER.md.

Mirrors hermes-agent's closed learning loop:
  - MEMORY.md  — facts, preferences, project notes (~2 200 char cap)
  - USER.md    — user profile, communication preferences (~1 375 char cap)

Both files are human-readable Markdown. The agent curates them autonomously;
the coordinator injects them into every system prompt.

Usage::

    mem = PersistentMemory(data_dir=Path("~/.andyria"))
    mem.add("MEMORY", "Prefers Python 3.12, uses uv for package management")
    mem.update("MEMORY", old="Python 3.12", new="Python 3.13")
    mem.remove("MEMORY", "old entry")
    block = mem.as_system_block()   # → injected into system prompt
"""

from __future__ import annotations

import re
import textwrap
import time
from pathlib import Path
from typing import Literal, Optional

MemFile = Literal["MEMORY", "USER"]

# Character caps matching hermes-agent defaults
_CAPS: dict[MemFile, int] = {
    "MEMORY": 2200,
    "USER":   1375,
}

_HEADERS: dict[MemFile, str] = {
    "MEMORY": "## Memory",
    "USER":   "## User Profile",
}

_DEFAULTS: dict[MemFile, str] = {
    "MEMORY": "## Memory\n\n*(no facts recorded yet)*\n",
    "USER":   "## User Profile\n\n*(no profile recorded yet)*\n",
}

_BULLET = "- "


class PersistentMemory:
    """Bounded Markdown memory that persists across sessions.

    Files on disk::

        {data_dir}/MEMORY.md   — general facts & preferences
        {data_dir}/USER.md     — user profile & communication style

    Each file has a character cap. When a write would breach the cap the
    oldest entry (top bullet after the header) is evicted first so the
    most recent information is always retained.
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, file: MemFile, entry: str) -> None:
        """Append a bullet-point entry, evicting the oldest if over cap."""
        entry = entry.strip()
        if not entry:
            return
        content = self._read(file)
        new_bullet = f"{_BULLET}{entry}"
        body = self._body(content, file)
        if body.strip() in ("", "*(no facts recorded yet)*", "*(no profile recorded yet)*"):
            body = new_bullet
        else:
            body = body.rstrip() + "\n" + new_bullet

        content = self._rebuild(file, body)
        # Evict oldest entries while over cap
        content = self._enforce_cap(file, content)
        self._write(file, content)

    def remove(self, file: MemFile, old_text: str) -> bool:
        """Remove a bullet that contains *old_text*. Returns True if found."""
        old_text = old_text.strip()
        content = self._read(file)
        lines = content.splitlines(keepends=True)
        new_lines = [
            ln for ln in lines
            if not (ln.strip().startswith(_BULLET) and old_text in ln)
        ]
        if len(new_lines) == len(lines):
            return False
        self._write(file, "".join(new_lines))
        return True

    def update(self, file: MemFile, old_text: str, new_text: str) -> bool:
        """Replace old_text with new_text in a bullet. Returns True if found."""
        old_text = old_text.strip()
        new_text = new_text.strip()
        content = self._read(file)
        if old_text not in content:
            return False
        updated = content.replace(old_text, new_text, 1)
        self._write(file, updated)
        return True

    def read(self, file: MemFile) -> str:
        """Return the full raw content of the memory file."""
        return self._read(file)

    def clear(self, file: MemFile) -> None:
        """Reset a memory file to its empty default."""
        self._write(file, _DEFAULTS[file])

    # ------------------------------------------------------------------
    # System prompt injection
    # ------------------------------------------------------------------

    def as_system_block(self) -> str:
        """Return combined memory block for injection into the system prompt."""
        mem = self._read("MEMORY").strip()
        user = self._read("USER").strip()
        parts = []
        if mem and "(no facts" not in mem:
            parts.append(mem)
        if user and "(no profile" not in user:
            parts.append(user)
        if not parts:
            return ""
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "MEMORY": {
                "chars": len(self._read("MEMORY")),
                "cap"  : _CAPS["MEMORY"],
                "pct"  : round(len(self._read("MEMORY")) / _CAPS["MEMORY"] * 100, 1),
            },
            "USER": {
                "chars": len(self._read("USER")),
                "cap"  : _CAPS["USER"],
                "pct"  : round(len(self._read("USER")) / _CAPS["USER"] * 100, 1),
            },
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path(self, file: MemFile) -> Path:
        return self._dir / f"{file}.md"

    def _read(self, file: MemFile) -> str:
        p = self._path(file)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return _DEFAULTS[file]

    def _write(self, file: MemFile, content: str) -> None:
        self._path(file).write_text(content, encoding="utf-8")

    def _body(self, content: str, file: MemFile) -> str:
        """Extract the bullet-list body after the header line."""
        header = _HEADERS[file]
        idx = content.find(header)
        if idx == -1:
            return content
        rest = content[idx + len(header):].lstrip("\n")
        return rest

    def _rebuild(self, file: MemFile, body: str) -> str:
        return f"{_HEADERS[file]}\n\n{body.strip()}\n"

    def _enforce_cap(self, file: MemFile, content: str) -> str:
        """Evict oldest bullets until content fits within the character cap."""
        cap = _CAPS[file]
        while len(content) > cap:
            lines = content.splitlines(keepends=True)
            # Find first bullet after the header and remove it
            removed = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith(_BULLET):
                    lines.pop(i)
                    removed = True
                    break
            if not removed:
                # Nothing left to evict; hard-truncate
                content = content[:cap]
                break
            content = "".join(lines)
        return content
