"""Prompt builder — assembles the agent system prompt from all registered sources.

Mirrors hermes-agent's prompt assembly pipeline:
    SOUL.md → MEMORY.md / USER.md → Active skills → TODOs → Context files

Each source is a "block" injected in priority order. Empty blocks are
skipped gracefully so the prompt stays clean when features aren't in use.

Usage::

    builder = PromptBuilder(
        soul=soul_file,
        memory=persistent_memory,
        skills=skill_registry,
        todo=todo_store,
        context_files=context_file_loader,
    )
    system_prompt = builder.build()
"""

from __future__ import annotations

from typing import List, Optional


class PromptBuilder:
    """Assembles the full system prompt for an Andyria agent session.

    All constructor arguments are optional — only the non-None sources
    with non-empty content contribute to the final prompt.

    Args:
        soul:             SoulFile instance (personality / identity).
        memory:           PersistentMemory instance (MEMORY.md + USER.md).
        skills:           SkillRegistry instance (active skill content).
        active_skills:    List of skill names to include in the prompt.
        todo:             TodoStore instance (pending tasks).
        context_files:    ContextFileLoader (AGENTS.md, .andyria.md, etc.).
        extra_blocks:     Additional raw strings to append.
    """

    def __init__(
        self,
        soul=None,
        memory=None,
        skills=None,
        active_skills: Optional[List[str]] = None,
        todo=None,
        context_files=None,
        extra_blocks: Optional[List[str]] = None,
    ) -> None:
        self._soul = soul
        self._memory = memory
        self._skills = skills
        self._active_skills = active_skills or []
        self._todo = todo
        self._context_files = context_files
        self._extra_blocks = extra_blocks or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> str:
        """Assemble and return the full system prompt string."""
        blocks: List[str] = []

        # 1. Agent identity / personality (SOUL.md)
        if self._soul:
            soul_block = self._soul.as_system_block()
            if soul_block.strip():
                blocks.append(soul_block.strip())

        # 2. Persistent memory (MEMORY.md + USER.md)
        if self._memory:
            mem_block = self._memory.as_system_block()
            if mem_block.strip():
                blocks.append(mem_block.strip())

        # 3. Active skills (loaded on demand)
        if self._skills and self._active_skills:
            skill_parts = []
            for skill_name in self._active_skills:
                content = self._skills.skill_view(skill_name)
                if content:
                    skill_parts.append(content.strip())
            if skill_parts:
                blocks.append("## Active Skills\n\n" + "\n\n---\n\n".join(skill_parts))

        # 4. Current TODOs
        if self._todo:
            todo_block = self._todo.as_system_block()
            if todo_block.strip():
                blocks.append(todo_block.strip())

        # 5. Project context files (AGENTS.md, .andyria.md, etc.)
        if self._context_files:
            ctx_block = self._context_files.as_system_block()
            if ctx_block.strip():
                blocks.append("## Project Context\n\n" + ctx_block.strip())

        # 6. Caller-supplied extra blocks
        for extra in self._extra_blocks:
            if extra.strip():
                blocks.append(extra.strip())

        return "\n\n---\n\n".join(blocks)

    def set_active_skills(self, skill_names: List[str]) -> None:
        """Replace the list of active skill names."""
        self._active_skills = list(skill_names)

    def add_extra_block(self, block: str) -> None:
        """Append an extra raw block to the prompt."""
        self._extra_blocks.append(block)

    def clear_extras(self) -> None:
        self._extra_blocks.clear()
