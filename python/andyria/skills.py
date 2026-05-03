"""Skills system for Andyria — mirrors hermes-agent's Skills Hub.

Skills are Markdown files (SKILL.md format) stored in ``{data_dir}/skills/``.
Each file describes a reusable capability the agent can load on demand
(progressive disclosure) rather than keeping every skill in every prompt.

Skill file format (compatible with agentskills.io)::

    ---
    name: skill-name
    description: One-line description
    tags: [tag1, tag2]
    author: optional
    version: "1.0"
    ---

    ## Skill content here (instructions, examples, templates)

The agent can:
  * List available skills (``skills_list``)
  * View a skill (``skill_view``)
  * Create / update / delete its own skills (``skill_manage``)

Usage::

    store = SkillRegistry(data_dir=Path("~/.andyria"))
    store.create("my-skill", content="## My Skill\n\nDo X, then Y.")
    skills = store.list()
    content = store.get("my-skill")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    content: str            # full file text including front-matter
    tags: List[str] = field(default_factory=list)
    author: str = ""
    version: str = "1.0"
    path: Optional[Path] = None
    updated_at: int = 0


def _parse_front_matter(text: str) -> dict:
    """Extract YAML-ish front matter without a YAML dependency."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k == "tags":
            # [tag1, tag2] inline list
            v = re.sub(r"[\[\]]", "", v)
            result[k] = [t.strip() for t in v.split(",") if t.strip()]
        else:
            result[k] = v
    return result


def _build_front_matter(name: str, description: str, tags: List[str], author: str, version: str) -> str:
    tag_str = "[" + ", ".join(tags) + "]" if tags else "[]"
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"tags: {tag_str}",
        f"author: {author}",
        f'version: "{version}"',
        "---",
        "",
    ]
    return "\n".join(lines)


class SkillRegistry:
    """Loads and manages SKILL.md files from ``{data_dir}/skills/``.

    Agent tools exposed:
        * ``skills_list(category: str | None) -> List[dict]``
        * ``skill_view(name: str) -> str``
        * ``skill_manage(action, name, content, description, tags) -> str``
    """

    def __init__(self, data_dir: Path) -> None:
        self._skills_dir = Path(data_dir) / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Skill] = {}

    # ------------------------------------------------------------------
    # Public agent tools
    # ------------------------------------------------------------------

    def skills_list(self, category: Optional[str] = None) -> List[dict]:
        """Return summary list (name, description, tags) of all skills.

        Optionally filter by tag/category.
        """
        self._refresh_cache()
        result = []
        for skill in sorted(self._cache.values(), key=lambda s: s.name):
            if category and category.lower() not in [t.lower() for t in skill.tags]:
                continue
            result.append({
                "name": skill.name,
                "description": skill.description,
                "tags": skill.tags,
                "version": skill.version,
            })
        return result

    def skill_view(self, name: str) -> Optional[str]:
        """Return the full content of a named skill."""
        self._refresh_cache()
        skill = self._cache.get(self._normalize(name))
        return skill.content if skill else None

    def skill_manage(
        self,
        action: str,
        name: str,
        content: str = "",
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        """Create, update, or delete a skill.

        Args:
            action:  "create" | "update" | "delete"
            name:    Skill name (slug)
            content: Markdown body (for create/update)
            description: One-line description
            tags:    List of tag strings

        Returns a human-readable result string.
        """
        action = action.lower().strip()
        name_slug = self._normalize(name)
        if not name_slug:
            return "Error: skill name required"

        if action == "delete":
            path = self._skills_dir / f"{name_slug}.md"
            if not path.exists():
                return f"Skill '{name_slug}' not found"
            path.unlink()
            self._cache.pop(name_slug, None)
            return f"Skill '{name_slug}' deleted"

        if action in ("create", "update"):
            fm = _build_front_matter(
                name=name_slug,
                description=description or name_slug,
                tags=tags or [],
                author="andyria",
                version="1.0",
            )
            full = fm + (content.strip() or f"## {name}\n\n*(empty skill — fill in content)*")
            path = self._skills_dir / f"{name_slug}.md"
            path.write_text(full, encoding="utf-8")
            self._cache.pop(name_slug, None)  # invalidate cache entry
            verb = "created" if action == "create" else "updated"
            return f"Skill '{name_slug}' {verb}"

        return f"Unknown action '{action}' — use create, update, or delete"

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[Skill]:
        """Return a Skill object by name."""
        self._refresh_cache()
        return self._cache.get(self._normalize(name))

    def search(self, query: str) -> List[Skill]:
        """Naive substring search across name, description, and content."""
        self._refresh_cache()
        q = query.lower()
        return [
            s for s in self._cache.values()
            if q in s.name.lower()
            or q in s.description.lower()
            or q in s.content.lower()
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"[^a-z0-9_-]", "-", name.strip().lower()).strip("-")

    def _refresh_cache(self) -> None:
        """Scan skills directory and update cache for new/changed files."""
        for path in self._skills_dir.glob("*.md"):
            slug = path.stem
            cached = self._cache.get(slug)
            mtime_ns = int(path.stat().st_mtime_ns)
            if cached and cached.updated_at >= mtime_ns:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = _parse_front_matter(raw)
            self._cache[slug] = Skill(
                name=meta.get("name", slug),
                description=meta.get("description", ""),
                content=raw,
                tags=meta.get("tags") or [],
                author=meta.get("author", ""),
                version=meta.get("version", "1.0"),
                path=path,
                updated_at=mtime_ns,
            )
        # Remove cache entries whose files were deleted
        on_disk = {p.stem for p in self._skills_dir.glob("*.md")}
        for slug in list(self._cache):
            if slug not in on_disk:
                del self._cache[slug]
