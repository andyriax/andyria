"""Promptbook system for Andyria — Cyphermorph-style prompt mutation & composition.

A Promptbook is a named, versioned collection of PromptTemplates. Templates
support ``{{variable}}`` placeholders that are rendered at invocation time.

Promptbooks can be forked into "mutations" (named variants) which override or
extend individual templates, enabling A/B style prompt evolution without
losing the parent lineage.

Usage::

    registry = PromptbookRegistry(memory)

    pb = registry.create(PromptbookCreateRequest(
        name="onboarding",
        templates=[
            PromptTemplate(
                name="system",
                role="system",
                template="You are {{agent_name}}, a {{archetype}} agent.",
                variables=["agent_name", "archetype"],
            ),
            PromptTemplate(
                name="opener",
                role="user",
                template="Hello! I need help with {{task}}.",
                variables=["task"],
            ),
        ],
        variables={"agent_name": "Agent display name", "task": "User task"},
    ))

    rendered = registry.render(pb.promptbook_id, {"agent_name": "Aria", "task": "code review"})
    # → [{role: "system", name: "system", content: "You are Aria, a ..."},
    #    {role: "user",   name: "opener", content: "Hello! I need help with code review."}]

    # Fork a mutation
    mutant = registry.mutate(
        pb.promptbook_id,
        PromptbookMutateRequest(
            name="onboarding-v2",
            overrides={"opener": "Hi! Please help me {{task}} in {{language}}."},
            extra_templates=[PromptTemplate(name="closer", role="user",
                                            template="Thanks for the {{language}} help!")],
        ),
    )
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

from .memory import ContentAddressedMemory
from .models import (
    Promptbook,
    PromptbookCreateRequest,
    PromptbookMutateRequest,
    PromptbookRenderResponse,
    PromptbookUpdateRequest,
    PromptTemplate,
)

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

_NS = "promptbooks"


def _render_template(template: str, variables: Dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``{{var}}`` placeholders; return (rendered, missing_vars)."""
    missing: list[str] = []
    seen: set[str] = set()

    def _sub(m: re.Match) -> str:  # type: ignore[type-arg]
        key = m.group(1)
        if key in variables:
            return variables[key]
        if key not in seen:
            missing.append(key)
            seen.add(key)
        return m.group(0)  # leave placeholder intact

    rendered = _VAR_RE.sub(_sub, template)
    return rendered, missing


class PromptbookRegistry:
    """Persists and retrieves Promptbooks in ContentAddressedMemory."""

    def __init__(self, memory: ContentAddressedMemory) -> None:
        self._memory = memory

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self, tag: Optional[str] = None) -> List[Promptbook]:
        books: List[Promptbook] = []
        for key in self._memory.list_keys(_NS):
            raw = self._memory.get_by_key(_NS, key)
            if raw is None:
                continue
            try:
                pb = Promptbook.model_validate_json(raw)
                if pb.active:
                    if tag is None or tag in pb.tags:
                        books.append(pb)
            except Exception:
                pass
        books.sort(key=lambda b: b.created_at)
        return books

    def get(self, promptbook_id: str) -> Optional[Promptbook]:
        raw = self._memory.get_by_key(_NS, promptbook_id)
        if raw is None:
            return None
        try:
            return Promptbook.model_validate_json(raw)
        except Exception:
            return None

    def create(self, request: PromptbookCreateRequest) -> Promptbook:
        now = int(time.time_ns())
        pb_id = f"pb-{now % (10 ** 12):012d}"
        pb = Promptbook(
            promptbook_id=pb_id,
            name=request.name,
            description=request.description,
            templates=list(request.templates),
            variables=dict(request.variables),
            tags=list(request.tags),
            version=request.version,
            created_at=now,
            updated_at=now,
        )
        self._save(pb)
        return pb

    def update(self, promptbook_id: str, request: PromptbookUpdateRequest) -> Optional[Promptbook]:
        pb = self.get(promptbook_id)
        if pb is None:
            return None
        updates: dict = {"updated_at": int(time.time_ns())}
        if request.name is not None:
            updates["name"] = request.name
        if request.description is not None:
            updates["description"] = request.description
        if request.templates is not None:
            updates["templates"] = request.templates
        if request.variables is not None:
            updates["variables"] = request.variables
        if request.tags is not None:
            updates["tags"] = request.tags
        if request.version is not None:
            updates["version"] = request.version
        pb = pb.model_copy(update=updates)
        self._save(pb)
        return pb

    def delete(self, promptbook_id: str) -> Optional[Promptbook]:
        pb = self.get(promptbook_id)
        if pb is None:
            return None
        pb = pb.model_copy(update={"active": False, "updated_at": int(time.time_ns())})
        self._save(pb)
        return pb

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(
        self,
        promptbook_id: str,
        variables: Dict[str, str],
        template_name: Optional[str] = None,
    ) -> Optional[PromptbookRenderResponse]:
        """Render one or all templates in a promptbook.

        Returns ``None`` if the promptbook does not exist.
        All ``{{variable}}`` placeholders are substituted; any remaining
        unresolved placeholders are listed in ``missing_variables``.
        """
        pb = self.get(promptbook_id)
        if pb is None:
            return None

        # Merge declared default-descriptions with caller values
        effective: Dict[str, str] = dict(variables)

        templates = pb.templates
        if template_name is not None:
            templates = [t for t in templates if t.name == template_name]

        rendered_blocks: List[Dict[str, str]] = []
        all_missing: List[str] = []

        for tmpl in templates:
            content, missing = _render_template(tmpl.template, effective)
            rendered_blocks.append({"name": tmpl.name, "role": tmpl.role, "content": content})
            for m in missing:
                if m not in all_missing:
                    all_missing.append(m)

        return PromptbookRenderResponse(
            promptbook_id=promptbook_id,
            rendered=rendered_blocks,
            missing_variables=all_missing,
        )

    # ------------------------------------------------------------------
    # Mutation (Cyphermorph fork)
    # ------------------------------------------------------------------

    def mutate(self, promptbook_id: str, request: PromptbookMutateRequest) -> Optional[Promptbook]:
        """Fork a promptbook as a named variant with selective overrides.

        The resulting mutation:
        - Copies all templates from the parent
        - Replaces template text for names listed in ``overrides``
        - Appends ``extra_templates``
        - Sets ``parent_id`` to the source promptbook_id
        """
        parent = self.get(promptbook_id)
        if parent is None:
            return None

        now = int(time.time_ns())
        pb_id = f"pb-{now % (10 ** 12):012d}"

        # Build new template list
        new_templates: List[PromptTemplate] = []
        for tmpl in parent.templates:
            if tmpl.name in request.overrides:
                new_text = request.overrides[tmpl.name]
                new_vars = _VAR_RE.findall(new_text)
                new_templates.append(
                    PromptTemplate(
                        name=tmpl.name,
                        role=tmpl.role,
                        template=new_text,
                        description=tmpl.description,
                        variables=new_vars,
                    )
                )
            else:
                new_templates.append(tmpl)
        new_templates.extend(request.extra_templates)

        mutation = Promptbook(
            promptbook_id=pb_id,
            name=request.name,
            description=request.description or f"Mutation of {parent.name}",
            templates=new_templates,
            variables=dict(parent.variables),
            tags=list(request.tags) or list(parent.tags),
            version=request.version,
            parent_id=promptbook_id,
            created_at=now,
            updated_at=now,
        )
        self._save(mutation)
        return mutation

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def as_system_block(self, promptbook_id: str, variables: Dict[str, str]) -> str:
        """Render a promptbook and return a formatted system-prompt block."""
        result = self.render(promptbook_id, variables)
        if result is None:
            return ""
        parts = [f"### Promptbook block [{r['name']}] ({r['role']})\n{r['content']}"
                 for r in result.rendered]
        return "\n\n".join(parts)

    def _save(self, pb: Promptbook) -> None:
        serialized = pb.model_dump_json().encode()
        content_hash = self._memory.put(serialized)
        self._memory.bind(_NS, pb.promptbook_id, content_hash)
