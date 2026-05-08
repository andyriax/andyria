"""Context file auto-discovery — mirrors hermes-agent's context files feature.

Automatically discovers and loads project-scoped context files so the
agent understands the current repository without being told explicitly.

Files searched (in priority order):
    1. AGENTS.md      — agent-specific instructions (GitHub Copilot / Hermes convention)
    2. .andyria.md    — Andyria-specific project notes
    3. CLAUDE.md      — Claude-specific context (cross-compatible)
    4. .cursorrules   — Cursor IDE rules (useful context)
    5. README.md      — Fallback project description (first 2000 chars only)

Search paths (in order):
    1. Current working directory
    2. Git repository root (walked up from cwd)
    3. Home directory (~/)

Usage::

    loader = ContextFileLoader()
    loader.discover()
    block = loader.as_system_block()    # → injected into system prompt

    # Explicit load from a specific path:
    loader.load_explicit(Path("/path/to/custom.md"))
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

_CONTEXT_FILENAMES = [
    "AGENTS.md",
    ".andyria.md",
    "CLAUDE.md",
    ".cursorrules",
    "COPILOT-INSTRUCTIONS.md",
]

_README_MAX_CHARS = 2000
_CONTEXT_FILE_MAX_CHARS = 8000


def _find_git_root(start: Path) -> Optional[Path]:
    """Walk up from start looking for a .git directory."""
    current = start.resolve()
    for _ in range(10):  # max 10 levels up
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


class ContextFileLoader:
    """Discovers and caches project-scoped context files.

    Args:
        extra_paths: Additional directories to search.
        cwd:         Override current working directory.
    """

    def __init__(
        self,
        extra_paths: Optional[List[Path]] = None,
        cwd: Optional[Path] = None,
    ) -> None:
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._extra_paths = extra_paths or []
        self._loaded: Dict[str, str] = {}  # filename → content
        self._sources: Dict[str, Path] = {}  # filename → path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> List[str]:
        """Scan all search paths and load found context files.

        Returns list of found filenames.
        """
        self._loaded.clear()
        self._sources.clear()

        search_paths: List[Path] = [self._cwd]
        git_root = _find_git_root(self._cwd)
        if git_root and git_root != self._cwd:
            search_paths.append(git_root)
        search_paths.append(Path.home())
        search_paths.extend(self._extra_paths)

        found = []
        for search_dir in search_paths:
            for filename in _CONTEXT_FILENAMES:
                if filename in self._loaded:
                    continue  # already found from a higher-priority path
                candidate = search_dir / filename
                if candidate.is_file():
                    try:
                        content = candidate.read_text(encoding="utf-8")
                        if len(content) > _CONTEXT_FILE_MAX_CHARS:
                            content = content[:_CONTEXT_FILE_MAX_CHARS] + "\n\n[truncated]"
                        self._loaded[filename] = content
                        self._sources[filename] = candidate
                        found.append(filename)
                    except OSError:
                        continue

            # Fallback: README.md (truncated)
            if "README.md" not in self._loaded:
                readme = search_dir / "README.md"
                if readme.is_file():
                    try:
                        content = readme.read_text(encoding="utf-8")[:_README_MAX_CHARS]
                        self._loaded["README.md"] = content
                        self._sources["README.md"] = readme
                        found.append("README.md")
                    except OSError:
                        pass

        return found

    def load_explicit(self, path: Path) -> bool:
        """Explicitly load a context file. Returns True on success."""
        if not path.is_file():
            return False
        try:
            content = path.read_text(encoding="utf-8")
            if len(content) > _CONTEXT_FILE_MAX_CHARS:
                content = content[:_CONTEXT_FILE_MAX_CHARS] + "\n\n[truncated]"
            self._loaded[path.name] = content
            self._sources[path.name] = path
            return True
        except OSError:
            return False

    def get(self, filename: str) -> Optional[str]:
        return self._loaded.get(filename)

    def list_loaded(self) -> List[dict]:
        return [
            {"filename": fn, "path": str(self._sources[fn]), "chars": len(content)}
            for fn, content in self._loaded.items()
        ]

    def as_system_block(self) -> str:
        """Return all loaded context files as a combined system prompt block."""
        if not self._loaded:
            return ""
        parts = []
        for filename, content in self._loaded.items():
            path_str = str(self._sources.get(filename, filename))
            parts.append(f"### {filename} ({path_str})\n\n{content.strip()}")
        return "\n\n---\n\n".join(parts)
