"""Workflow system for Andyria — composable DAG-based agentic pipelines.

A Workflow is a named directed acyclic graph (DAG) of WorkflowSteps. Each
step can invoke an agent, run a chain, call the ATM, render a promptbook, or
execute a tool. Steps declare dependencies via ``depends_on``; the runner
topologically sorts and executes them, threading outputs between steps.

Step types and their ``config`` keys
-------------------------------------
- ``agent``      — ``agent_id``, ``input_template`` (optional ``{{var}}`` string)
- ``chain``      — ``chain_id``, ``input_template``
- ``atm``        — ``prompt_template``, ``max_iterations`` (default 3)
- ``promptbook`` — ``promptbook_id``, ``template_name`` (optional), ``variables_map``
                   (maps step output keys to promptbook variables)
- ``tool``       — ``tool_name``, ``params`` (dict)

Variable interpolation
----------------------
All ``input_template`` / ``prompt_template`` strings support ``{{key}}``
placeholders that are resolved from:

1. The workflow's initial ``variables`` dict
2. The ``output_key`` of any previously completed step

Usage::

    runner = WorkflowRunner(coordinator)
    result = await runner.run(workflow_def, WorkflowRunRequest(
        input="Summarise the following document: ...",
        variables={"language": "English"},
    ))
"""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .memory import ContentAddressedMemory
from .models import (
    AndyriaRequest,
    ATMThinkRequest,
    WorkflowDefinition,
    WorkflowRunRequest,
    WorkflowRunResult,
    WorkflowStep,
    WorkflowStepResult,
    WorkflowStepType,
)

if TYPE_CHECKING:
    from .coordinator import Coordinator
    from .promptbook import PromptbookRegistry

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

_NS = "workflows"


def _resolve(template: str, ctx: Dict[str, str]) -> str:
    """Substitute ``{{key}}`` from context dict; leave unknown keys intact."""
    return _VAR_RE.sub(lambda m: ctx.get(m.group(1), m.group(0)), template)


def _topo_sort_steps(steps: List[WorkflowStep]) -> List[WorkflowStep]:
    """Topological sort of workflow steps by their ``depends_on`` edges."""
    step_map = {s.step_id: s for s in steps}
    in_degree: Dict[str, int] = defaultdict(int)
    adjacency: Dict[str, List[str]] = defaultdict(list)

    for step in steps:
        if step.step_id not in in_degree:
            in_degree[step.step_id] = 0
        for dep in step.depends_on:
            if dep in step_map:
                adjacency[dep].append(step.step_id)
                in_degree[step.step_id] += 1

    queue = [s.step_id for s in steps if in_degree[s.step_id] == 0]
    result: List[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for child in adjacency[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Fallback: include any orphaned/cycled steps in declaration order
    visited = set(result)
    result.extend(s.step_id for s in steps if s.step_id not in visited)

    return [step_map[sid] for sid in result if sid in step_map]


class WorkflowRegistry:
    """Persists and retrieves WorkflowDefinitions in ContentAddressedMemory."""

    def __init__(self, memory: ContentAddressedMemory) -> None:
        self._memory = memory

    def list(self, tag: Optional[str] = None) -> List[WorkflowDefinition]:
        workflows: List[WorkflowDefinition] = []
        for key in self._memory.list_keys(_NS):
            raw = self._memory.get_by_key(_NS, key)
            if raw is None:
                continue
            try:
                wf = WorkflowDefinition.model_validate_json(raw)
                if wf.active:
                    if tag is None or tag in wf.tags:
                        workflows.append(wf)
            except Exception:
                pass
        workflows.sort(key=lambda w: w.created_at)
        return workflows

    def get(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        raw = self._memory.get_by_key(_NS, workflow_id)
        if raw is None:
            return None
        try:
            return WorkflowDefinition.model_validate_json(raw)
        except Exception:
            return None

    def create(
        self,
        name: str,
        description: str = "",
        steps: Optional[List[WorkflowStep]] = None,
        input_schema: Optional[Dict[str, str]] = None,
        output_step: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> WorkflowDefinition:
        now = int(time.time_ns())
        wf_id = f"wf-{now % (10**12):012d}"
        wf = WorkflowDefinition(
            workflow_id=wf_id,
            name=name,
            description=description,
            steps=steps or [],
            input_schema=input_schema or {},
            output_step=output_step,
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        self._save(wf)
        return wf

    def update(self, workflow_id: str, **kwargs: Any) -> Optional[WorkflowDefinition]:
        wf = self.get(workflow_id)
        if wf is None:
            return None
        kwargs["updated_at"] = int(time.time_ns())
        wf = wf.model_copy(update=kwargs)
        self._save(wf)
        return wf

    def delete(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        wf = self.get(workflow_id)
        if wf is None:
            return None
        wf = wf.model_copy(update={"active": False, "updated_at": int(time.time_ns())})
        self._save(wf)
        return wf

    def _save(self, wf: WorkflowDefinition) -> None:
        serialized = wf.model_dump_json().encode()
        content_hash = self._memory.put(serialized)
        self._memory.bind(_NS, wf.workflow_id, content_hash)


class WorkflowRunner:
    """Executes a WorkflowDefinition step-by-step, threading outputs as inputs.

    The runner relies on the Coordinator for agent / chain / ATM calls and
    optionally on PromptbookRegistry for promptbook steps.
    """

    def __init__(
        self,
        coordinator: "Coordinator",
        promptbook_registry: Optional["PromptbookRegistry"] = None,
    ) -> None:
        self._coordinator = coordinator
        self._promptbooks = promptbook_registry

    async def run(
        self,
        workflow: WorkflowDefinition,
        request: WorkflowRunRequest,
    ) -> WorkflowRunResult:
        run_id = str(uuid.uuid4())
        start_ns = int(time.time_ns())
        start_ms = time.monotonic()

        # Execution context: starts with caller variables + raw input
        ctx: Dict[str, str] = dict(request.variables)
        ctx.setdefault("input", request.input)

        ordered_steps = _topo_sort_steps(workflow.steps)
        step_results: List[WorkflowStepResult] = []
        status = "completed"

        for step in ordered_steps:
            step_start = time.monotonic()
            try:
                output = await self._execute_step(step, ctx, request.session_id)
                elapsed = int((time.monotonic() - step_start) * 1000)
                step_results.append(
                    WorkflowStepResult(
                        step_id=step.step_id,
                        name=step.name,
                        status="completed",
                        output=output,
                        elapsed_ms=elapsed,
                    )
                )
                # Publish output into context under output_key or step_id
                key = step.output_key or step.step_id
                ctx[key] = output
            except Exception as exc:
                elapsed = int((time.monotonic() - step_start) * 1000)
                step_results.append(
                    WorkflowStepResult(
                        step_id=step.step_id,
                        name=step.name,
                        status="failed",
                        error=str(exc),
                        elapsed_ms=elapsed,
                    )
                )
                status = "failed"
                # Downstream dependent steps will have no input — mark them skipped
                dep_ids = {s.step_id for s in ordered_steps if step.step_id in s.depends_on}
                for s in ordered_steps:
                    if s.step_id in dep_ids and not any(r.step_id == s.step_id for r in step_results):
                        step_results.append(
                            WorkflowStepResult(
                                step_id=s.step_id,
                                name=s.name,
                                status="skipped",
                            )
                        )
                break

        # Determine final output
        final_output = ""
        if workflow.output_step:
            for sr in step_results:
                if sr.step_id == workflow.output_step:
                    final_output = sr.output
                    break
        elif step_results and step_results[-1].status == "completed":
            final_output = step_results[-1].output

        total_ms = int((time.monotonic() - start_ms) * 1000)

        return WorkflowRunResult(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            status=status,
            step_results=step_results,
            final_output=final_output,
            total_ms=total_ms,
            timestamp_ns=start_ns,
        )

    # ------------------------------------------------------------------
    # Step execution dispatch
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: WorkflowStep,
        ctx: Dict[str, str],
        session_id: Optional[str],
    ) -> str:
        if step.type == WorkflowStepType.AGENT:
            return await self._run_agent_step(step, ctx, session_id)
        if step.type == WorkflowStepType.CHAIN:
            return await self._run_chain_step(step, ctx, session_id)
        if step.type == WorkflowStepType.ATM:
            return self._run_atm_step(step, ctx)
        if step.type == WorkflowStepType.PROMPTBOOK:
            return self._run_promptbook_step(step, ctx)
        if step.type == WorkflowStepType.TOOL:
            return await self._run_tool_step(step, ctx)
        raise ValueError(f"Unknown step type: {step.type}")

    async def _run_agent_step(self, step: WorkflowStep, ctx: Dict[str, str], session_id: Optional[str]) -> str:
        agent_id = step.config.get("agent_id")
        input_template = step.config.get("input_template", "{{input}}")
        prompt = _resolve(input_template, ctx)
        req = AndyriaRequest(
            input=prompt,
            agent_id=agent_id,
            session_id=session_id,
        )
        response = await self._coordinator.process(req)
        return response.output

    async def _run_chain_step(self, step: WorkflowStep, ctx: Dict[str, str], session_id: Optional[str]) -> str:
        chain_id = step.config.get("chain_id")
        if not chain_id:
            raise ValueError(f"Step '{step.step_id}': chain_id required for chain steps")
        input_template = step.config.get("input_template", "{{input}}")
        prompt = _resolve(input_template, ctx)
        response = await self._coordinator.run_chain(chain_id, prompt, session_id=session_id)
        return response.output

    def _run_atm_step(self, step: WorkflowStep, ctx: Dict[str, str]) -> str:
        prompt_template = step.config.get("prompt_template", "{{input}}")
        max_iter = int(step.config.get("max_iterations", 3))
        prompt = _resolve(prompt_template, ctx)
        req = ATMThinkRequest(prompt=prompt, max_iterations=max_iter)
        result = self._coordinator.atm_think(req)
        return result.final_output

    def _run_promptbook_step(self, step: WorkflowStep, ctx: Dict[str, str]) -> str:
        if self._promptbooks is None:
            raise RuntimeError("PromptbookRegistry not configured on WorkflowRunner")
        pb_id = step.config.get("promptbook_id")
        if not pb_id:
            raise ValueError(f"Step '{step.step_id}': promptbook_id required")
        tmpl_name: Optional[str] = step.config.get("template_name")
        # variables_map maps promptbook variable names to context keys
        variables_map: Dict[str, str] = step.config.get("variables_map", {})
        resolved_vars: Dict[str, str] = {}
        for pb_var, ctx_key in variables_map.items():
            resolved_vars[pb_var] = ctx.get(ctx_key, ctx_key)
        # Also pass whole context so templates can use any key directly
        merged: Dict[str, str] = {**ctx, **resolved_vars}

        render = self._promptbooks.render(pb_id, merged, template_name=tmpl_name)
        if render is None:
            raise ValueError(f"Promptbook '{pb_id}' not found")
        # Concatenate rendered blocks as text
        parts = [f"[{r['role']}] {r['content']}" for r in render.rendered]
        return "\n\n".join(parts)

    async def _run_tool_step(self, step: WorkflowStep, ctx: Dict[str, str]) -> str:
        tool_name = step.config.get("tool_name")
        if not tool_name:
            raise ValueError(f"Step '{step.step_id}': tool_name required for tool steps")
        raw_params: Dict[str, Any] = dict(step.config.get("params", {}))
        # Resolve any string param values that contain {{placeholders}}
        params: Dict[str, Any] = {k: _resolve(v, ctx) if isinstance(v, str) else v for k, v in raw_params.items()}
        tool_registry = getattr(self._coordinator, "_tools", None)
        if tool_registry is None:
            raise RuntimeError("ToolRegistry not available on coordinator")
        result = await tool_registry.call(tool_name, **params)
        return str(result)
