"""Planner for Andyria: decomposes requests into executable tasks.

v1 implements a rule-based planner. The entropy_beacon_id is stored in
each task's context so the planner's randomized decisions are auditable
and replayable.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import Task, TaskType


# (pattern, task_type, description template)
_RULES: list[tuple[re.Pattern, TaskType, str]] = [
    (
        re.compile(r"\b(calculate|compute|solve|math|equation|formula|integral|derivative)\b", re.I),
        TaskType.SYMBOLIC,
        "Compute: {input}",
    ),
    (
        re.compile(r"\b(search|find|lookup|retrieve|what is|who is|where is|define)\b", re.I),
        TaskType.LANGUAGE,
        "Retrieve: {input}",
    ),
    (
        re.compile(r"\b(write|generate|create|compose|summarize|explain|describe|translate)\b", re.I),
        TaskType.LANGUAGE,
        "Generate: {input}",
    ),
    (
        re.compile(r"\b(run|execute|call|invoke|trigger|deploy|start|stop)\b", re.I),
        TaskType.TOOL,
        "Execute: {input}",
    ),
]


class Planner:
    """Decomposes a user request into an ordered list of Tasks.

    Rule-based in v1. Each task carries ``entropy_beacon_id`` in its
    context so any randomized routing decisions are auditable.
    """

    def plan(
        self,
        request_id: str,
        user_input: str,
        context: Dict[str, Any],
        entropy_beacon_id: str,
    ) -> List[Task]:
        """Return an ordered task list for ``user_input``."""
        tasks = self._rule_based(request_id, user_input, context, entropy_beacon_id)
        if not tasks:
            # Fall back to a single generic language task
            tasks = [
                Task(
                    description=user_input,
                    task_type=TaskType.LANGUAGE,
                    priority=5,
                    context={**context, "entropy_beacon_id": entropy_beacon_id},
                    parent_request_id=request_id,
                )
            ]
        return tasks

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rule_based(
        self,
        request_id: str,
        user_input: str,
        context: Dict[str, Any],
        entropy_beacon_id: str,
    ) -> List[Task]:
        tasks: List[Task] = []
        seen_types: set[TaskType] = set()

        for pattern, task_type, desc_template in _RULES:
            if pattern.search(user_input) and task_type not in seen_types:
                tasks.append(
                    Task(
                        description=desc_template.format(input=user_input),
                        task_type=task_type,
                        priority=5,
                        context={**context, "entropy_beacon_id": entropy_beacon_id},
                        parent_request_id=request_id,
                    )
                )
                seen_types.add(task_type)

        return tasks
