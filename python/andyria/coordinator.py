"""Coordinator: main intelligence loop for Andyria.

Full path for one request:
    Request
      → EntropyBeacon (physical sources)
      → Planner (decompose into Tasks)
      → ModelRouter (language model / symbolic solver / tool)
      → Verifier (quality + policy check + sign event)
      → ContentAddressedMemory (persist state)
      → AndyriaResponse
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import json
import logging
import operator
import queue
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .atm import AutomatedThoughtMachine
from .auto_learn import AutoLearner
from .chain_labeler import ChainLabeler
from .chains import ChainRegistry
from .connectors import ConnectorRegistry
from .dag import topological_sort
from .entropy import EntropyBeaconFactory
from .gist_store import GistStore
from .memory import ContentAddressedMemory
from .mesh import MeshManager
from .models import (
    AgentCloneRequest,
    AgentCreateRequest,
    AgentDefinition,
    AgentUpdateRequest,
    AndyriaRequest,
    AndyriaResponse,
    ATMThinkRequest,
    ATMThoughtResponse,
    ATMThoughtStepOut,
    ChainCreateRequest,
    ChainDefinition,
    EntropyBeacon,
    Event,
    EventType,
    ConnectorCreateRequest,
    ConnectorDefinition,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ConnectorUpdateRequest,
    NodeConfig,
    NodeConfigUpdate,
    NodeStatus,
    ORCPatternMatch,
    ORCWitnessResult,
    PeerStatus,
    Promptbook,
    PromptbookCreateRequest,
    PromptbookMutateRequest,
    PromptbookRenderRequest,
    PromptbookRenderResponse,
    PromptbookUpdateRequest,
    PromptFlowInputRequest,
    PromptFlowResponse,
    PromptFlowStartRequest,
    ReflectionResult,
    SessionContext,
    TabCreateRequest,
    TabProjection,
    TabUpdateRequest,
    TaskResult,
    TaskType,
    WorkflowCreateRequest,
    WorkflowDefinition,
    WorkflowRunRequest,
    WorkflowRunResult,
)
from .node import NodeIdentityManager
from .orc import OuterReasoningCortex
from .persistent_memory import PersistentMemory
from .planner import Planner
from .projections import PromptFlowStore, TabProjectionStore
from .promptbook import PromptbookRegistry
from .reasoning import ReasoningEngine
from .registry import AgentRegistry
from .store import EventStore
from .tools import ToolRegistry
from .verifier import Verifier
from .workflow import WorkflowRegistry, WorkflowRunner

logger = logging.getLogger(__name__)


def _hash(data: bytes) -> str:
    try:
        import blake3  # type: ignore

        return blake3.blake3(data).hexdigest()
    except ImportError:
        return hashlib.sha3_256(data).hexdigest()


def _canonical_event(event: Event) -> bytes:
    return json.dumps(
        {
            "id": event.id,
            "parent_ids": event.parent_ids,
            "event_type": event.event_type.value,
            "payload_hash": event.payload_hash,
            "entropy_beacon_id": event.entropy_beacon_id,
            "timestamp_ns": event.timestamp_ns,
            "node_id": event.node_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


# ---------------------------------------------------------------------------
# Safe math evaluator (no eval(), no exec())
# ---------------------------------------------------------------------------

_SAFE_BIN_OPS: Dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_SAFE_UNARY_OPS: Dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_math(expr: str) -> float:
    """Evaluate a basic arithmetic expression using AST only. No eval()."""
    tree = ast.parse(expr.strip(), mode="eval")

    def _eval(node: ast.AST) -> float:  # type: ignore[return]
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"Unsupported constant: {node.value!r}")
        if isinstance(node, ast.BinOp):
            op_bin = _SAFE_BIN_OPS.get(type(node.op))
            if op_bin is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op_bin(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op_unary = _SAFE_UNARY_OPS.get(type(node.op))
            if op_unary is None:
                raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
            return op_unary(_eval(node.operand))
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    return _eval(tree.body)


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------


class ModelRouter:
    """Routes tasks to the smallest capable model or solver.

    Priority order:
      1. Symbolic AST solver (math expressions)
      2. llama.cpp local (GGUF file, if available)
      3. Ollama HTTP (if configured)
      4. Stub (offline fallback — always works)
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
    ) -> None:
        self._model_path = model_path
        self._ollama_url = ollama_url
        self._ollama_model = ollama_model
        self._llm = None
        self._llm_load_error: Optional[str] = None

        if self._model_path and self._model_path.exists():
            try:
                from llama_cpp import Llama  # type: ignore

                self._llm = Llama(
                    model_path=str(self._model_path),
                    n_ctx=2048,
                    n_threads=min(4, __import__("os").cpu_count() or 1),
                    verbose=False,
                )
            except Exception as exc:
                self._llm_load_error = str(exc)
                self._llm = None

    def route(
        self,
        task_type: TaskType,
        description: str,
        context: Dict[str, Any],
    ) -> tuple[str, str, float]:
        """Return (output, model_name, confidence)."""
        if task_type == TaskType.SYMBOLIC:
            result = self._symbolic_solve(description)
            if result is not None:
                return result

        if self._llm is not None:
            return self._llm_inference(description, context)

        if self._ollama_url:
            return self._ollama_inference(description, context)

        return self._stub_response(description)

    def _symbolic_solve(self, description: str) -> Optional[tuple[str, str, float]]:
        import re

        # Require match to start with digit or '(' to avoid spurious whitespace matches
        expr_match = re.search(r"[\d(][\d\s\+\-\*\/\(\)\.]*", description)
        if expr_match:
            expr = expr_match.group().strip()
            if any(c.isdigit() for c in expr):
                try:
                    result = _safe_eval_math(expr)
                    return str(result), "symbolic_ast", 0.95
                except Exception:
                    pass
        return None

    def _llm_inference(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> tuple[str, str, float]:
        history = (context or {}).get("session_history", "")
        system = (context or {}).get("system_prompt") or (
            "You are Andyria, a helpful, concise assistant. You have access to internet search, "
            "can learn and improve yourself autonomously, and are encouraged to search for information "
            "about yourself and your capabilities to better serve the user. Use these capabilities "
            "when relevant to provide the most accurate and up-to-date information."
        )
        if history:
            full_prompt = f"<|system|>{system}\n\nConversation so far:\n{history}<|user|>{prompt}<|assistant|>"
        else:
            full_prompt = f"<|system|>{system}<|user|>{prompt}<|assistant|>"
        try:
            resp = self._llm(  # type: ignore[misc]
                full_prompt,
                max_tokens=512,
                temperature=0.7,
                stop=["<|user|>", "<|system|>"],
            )
            text = resp["choices"][0]["text"].strip()
            return text, "llama_cpp_local", 0.85
        except Exception as exc:
            return f"[LLM error: {exc}]", "llama_cpp_local", 0.0

    def _ollama_inference(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> tuple[str, str, float]:
        history = (context or {}).get("session_history", "")
        system = (context or {}).get("system_prompt") or (
            "You are Andyria, a helpful, concise assistant. You have access to internet search, "
            "can learn and improve yourself autonomously, and are encouraged to search for information "
            "about yourself and your capabilities to better serve the user. Use these capabilities "
            "when relevant to provide the most accurate and up-to-date information."
        )
        if history:
            full_prompt = f"{system}\n\nConversation so far:\n{history}\n\nUser: {prompt}\nAssistant:"
        else:
            full_prompt = f"{system}\n\nUser: {prompt}\nAssistant:"
        try:
            import httpx

            model = self._ollama_model or "phi3"
            resp = httpx.post(
                f"{self._ollama_url}/api/generate",
                json={"model": model, "prompt": full_prompt, "stream": False},
                timeout=120.0,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            return text, f"ollama:{model}", 0.80
        except Exception as exc:
            return f"[Ollama error: {exc}]", "ollama", 0.0

    def has_configured_backend(self) -> bool:
        return self._model_path is not None or bool(self._ollama_url)

    def backend_health(self) -> tuple[bool, str]:
        """Return (available, detail) for language-model backend readiness."""
        if self._llm is not None:
            return True, "local GGUF loaded"

        if self._model_path is not None:
            if not self._model_path.exists():
                return False, f"local GGUF not found: {self._model_path}"
            if self._llm_load_error:
                return False, f"failed to load local GGUF: {self._llm_load_error}"
            return False, "local GGUF configured but not loaded"

        if self._ollama_url:
            return self._check_ollama_health()

        return False, "no LLM backend configured"

    def is_model_available(self) -> bool:
        available, _ = self.backend_health()
        return available

    def _check_ollama_health(self) -> tuple[bool, str]:
        import httpx

        model = self._ollama_model or "phi3"
        try:
            response = httpx.get(f"{self._ollama_url}/api/tags", timeout=4.0)
            response.raise_for_status()
            payload = response.json()
            names = [str(item.get("name", "")) for item in payload.get("models", [])]
            # Accept exact model or implicit :latest tag.
            candidates = {model, f"{model}:latest"}
            if any(name in candidates for name in names):
                return True, f"ollama ready ({model})"
            return False, f"ollama reachable but model '{model}' is not pulled"
        except Exception as exc:
            return False, f"ollama unavailable: {exc}"

    def update(self, ollama_url: Optional[str] = None, ollama_model: Optional[str] = None) -> None:
        """Update Ollama config at runtime without restarting."""
        if ollama_url is not None:
            self._ollama_url = ollama_url or None  # empty string → None
        if ollama_model is not None:
            self._ollama_model = ollama_model or None

    def active_agent_model(self) -> str:
        """Return model label that newly spawned agents should default to."""
        if self._llm is not None:
            if self._model_path:
                return f"llama_cpp:{self._model_path.name}"
            return "llama_cpp_local"
        if self._ollama_url:
            return self._ollama_model or "phi3"
        return "symbolic_ast"

    def _stub_response(self, prompt: str) -> tuple[str, str, float]:
        # Give a meaningful offline answer instead of a raw error string.
        p = prompt.lower()
        if any(kw in p for kw in ("what is", "who is", "explain", "describe", "how", "why")):
            answer = (
                f'No language model is currently loaded, so I can\'t give a full answer to: "{prompt[:200]}"\n\n'
                "To enable full responses, configure one of:\n"
                "  • Ollama: set ANDYRIA_OLLAMA_URL and ANDYRIA_OLLAMA_MODEL env vars\n"
                "  • Local GGUF: set model_path in config.yaml\n\n"
                'While offline, I can still solve math expressions (e.g. "42 * 7") and route symbolic tasks.'
            )
        else:
            answer = (
                f'Received: "{prompt[:200]}"\n'
                "No language model backend is available. "
                "Configure Ollama or a local GGUF model to enable full inference."
            )
        return answer, "unavailable", 0.1


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class Coordinator:
    """Main intelligence loop for an Andyria node.

    Orchestrates: entropy → plan → route → verify → commit → respond.
    """

    _EVENT_META_NS = "event_meta"

    def __init__(
        self,
        data_dir: Path,
        node_id: str,
        deployment_class: str = "edge",
        entropy_sources: Optional[List[str]] = None,
        entropy_sampler_interval_ms: int = 0,
        entropy_min_active_sources: int = 1,
        entropy_max_consecutive_degraded: int = 3,
        entropy_fail_closed: bool = False,
        model_path: Optional[Path] = None,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        peer_urls: Optional[List[str]] = None,
        gossip_interval_ms: int = 10_000,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._node_id = node_id
        self._start_time = time.monotonic()
        self._requests_processed = 0
        self._events_committed = 0
        self._beacons_generated = 0
        self._entropy_samples_total = 0
        self._entropy_samples_degraded_total = 0
        self._entropy_sampler_failures = 0
        self._entropy_consecutive_degraded = 0
        self._entropy_last_sample_ns = 0
        self._entropy_unhealthy = False
        self._entropy_sampler_interval_ms = max(0, int(entropy_sampler_interval_ms))
        self._entropy_min_active_sources = max(1, int(entropy_min_active_sources))
        self._entropy_max_consecutive_degraded = max(1, int(entropy_max_consecutive_degraded))
        self._entropy_fail_closed = entropy_fail_closed
        self._entropy_sampler_task: Optional[asyncio.Task[None]] = None
        self._event_log: List[Event] = []
        self._beacon_store: Dict[str, EntropyBeacon] = {}
        self._event_subscribers: List[queue.Queue[Dict[str, Any]]] = []

        # Persistent event store
        self._store = EventStore(self._data_dir)
        self._event_log = self._store.load_all()

        # Identity
        self._identity_mgr = NodeIdentityManager(self._data_dir, node_id, deployment_class)
        self._identity_mgr.load_or_create()
        private_key = self._identity_mgr.private_key
        self._private_key = private_key

        # Entropy
        self._beacon_factory = EntropyBeaconFactory(node_id, private_key, entropy_sources)

        # Memory
        self._memory = ContentAddressedMemory(self._data_dir, node_id, private_key)
        self._registry = AgentRegistry(self._memory, default_agent_name=f"{node_id} default")
        self._registry.ensure_default()
        self._tabs = TabProjectionStore(self._memory)
        self._prompt_flows = PromptFlowStore(self._memory)

        # Intelligence components
        self._router = ModelRouter(model_path, ollama_url, ollama_model)
        self._planner = Planner()
        self._verifier = Verifier(node_id, private_key)
        self._tools = ToolRegistry()
        self._chains = ChainRegistry(self._memory)
        self._promptbooks = PromptbookRegistry(self._memory)
        self._workflows = WorkflowRegistry(self._memory)
        self._connectors = ConnectorRegistry(self._data_dir)
        self._atm = AutomatedThoughtMachine(
            inference_fn=self._atm_infer,
            emit_event_fn=self._emit_control_event_str,
            max_iterations=3,
        )

        # Mesh networking
        self.mesh = MeshManager(
            peer_urls=peer_urls or [],
            store=self._store,
            node_id=node_id,
            gossip_interval_ms=gossip_interval_ms,
        )

        # Reasoning & self-improvement
        self._pmem = PersistentMemory(self._data_dir)
        self._reasoning = ReasoningEngine(
            inference_fn=self._atm_infer,
            emit_event_fn=self._emit_control_event_str,
        )
        self._learner = AutoLearner(
            persistent_memory=self._pmem,
            confidence_threshold=0.80,
            emit_fn=self._emit_control_event_str,
        )
        # Give ATM access to ReasoningEngine for low-confidence escalation
        self._atm._reasoning = self._reasoning

        # Workflow runner — wires together all subsystems
        self._workflow_runner = WorkflowRunner(self, self._promptbooks)

        # Outer Reasoning Cortex — metacognitive witness layer
        self._orc = OuterReasoningCortex(
            inference_fn=self._atm_infer,
            emit_event_fn=self._emit_control_event_str,
        )

        # Gist-backed distributed memory + chain labeling
        self._gist_store = GistStore(node_id=node_id)
        self._chain_labeler = ChainLabeler(
            memory=self._memory,
            auto_learner=self._learner,
        )

        # Wire mesh callbacks so it can emit events and ingest learned patterns
        self.mesh.set_emit_event(self._emit_control_event_str)
        self.mesh.set_ingest_learned(self._mesh_ingest_learned)

    # ------------------------------------------------------------------
    # Mesh auto-learning callback
    # ------------------------------------------------------------------

    def _mesh_ingest_learned(self, pattern: str, confidence: float) -> bool:
        """Ingest a learned pattern sourced from a mesh peer."""
        return self._learner.record(
            prompt="[mesh-peer]",
            output=pattern,
            confidence=confidence,
            source="mesh",
            model_used="peer",
        )

    async def start_background_tasks(self) -> None:
        """Start periodic background tasks (entropy sampler, mesh sync)."""
        if self._entropy_sampler_interval_ms <= 0:
            return
        if self._entropy_sampler_task is not None and not self._entropy_sampler_task.done():
            return
        self._entropy_sampler_task = asyncio.create_task(self._entropy_sampler_loop())

    async def stop_background_tasks(self) -> None:
        """Stop periodic background tasks cleanly."""
        if self._entropy_sampler_task is None:
            return
        self._entropy_sampler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._entropy_sampler_task
        self._entropy_sampler_task = None

    def close(self) -> None:
        """Release best-effort runtime resources.

        The current connector registry uses daemon threads and persistent JSON
        storage, so there is nothing mandatory to tear down yet. The method is
        present so API lifespan code can shut down the coordinator cleanly as
        the connector surface grows.
        """
        return None

    async def _entropy_sampler_loop(self) -> None:
        """Generate entropy beacons on a periodic interval for liveness sampling."""
        interval_s = self._entropy_sampler_interval_ms / 1000.0
        while True:
            try:
                beacon = self._beacon_factory.generate()
                self._beacon_store[beacon.id] = beacon
                self._beacons_generated += 1
                self._entropy_samples_total += 1
                self._entropy_last_sample_ns = int(time.time_ns())

                is_degraded = len(beacon.sources) < self._entropy_min_active_sources
                if is_degraded:
                    self._entropy_samples_degraded_total += 1
                    self._entropy_consecutive_degraded += 1
                else:
                    self._entropy_consecutive_degraded = 0

                self._entropy_unhealthy = self._entropy_consecutive_degraded >= self._entropy_max_consecutive_degraded
            except Exception:
                self._entropy_sampler_failures += 1
                self._entropy_consecutive_degraded += 1
                self._entropy_unhealthy = self._entropy_consecutive_degraded >= self._entropy_max_consecutive_degraded
            await asyncio.sleep(interval_s)

    async def process(self, request: AndyriaRequest) -> AndyriaResponse:
        """Execute the full intelligence loop for one request."""
        start_mono = time.monotonic()
        if self._entropy_fail_closed and self._entropy_unhealthy:
            raise RuntimeError("Entropy sampling unhealthy; fail-closed mode is enabled")
        requested_agent_id = request.agent_id or "default"
        agent = self._registry.get(requested_agent_id)
        if agent is None or not agent.active:
            requested_agent_id = "default"
            agent = self._registry.ensure_default()

        # 1. Anchor request to physical entropy
        beacon = self._beacon_factory.generate()
        self._beacon_store[beacon.id] = beacon
        self._beacons_generated += 1
        self._entropy_last_sample_ns = int(time.time_ns())

        # 2. Load session context and merge into request context
        session_ctx = None
        turn_number = 0
        merged_context = dict(request.context)
        if request.session_id:
            session_ctx = self._memory.get_session(request.session_id)
            if session_ctx:
                turn_number = len(session_ctx.turns) // 2
                # Provide recent history as context for the model router
                history_text = "\n".join(
                    f"{t.role.upper()}: {t.content}"
                    for t in session_ctx.turns[-10:]  # last 5 pairs
                )
                merged_context["session_history"] = history_text

        # Agent-level execution context comes from persistent registry state.
        merged_context["agent_id"] = requested_agent_id
        merged_context["agent_name"] = agent.name
        merged_context["agent_tools"] = list(agent.tools)
        if agent.system_prompt:
            merged_context["system_prompt"] = agent.system_prompt

        # 3. Persist request payload
        self._memory.put(request.model_dump())

        # 4. Plan
        tasks = self._planner.plan(
            request_id=request.id,
            user_input=request.input,
            context=merged_context,
            entropy_beacon_id=beacon.id,
        )

        # 5. Route → verify → commit
        results: List[TaskResult] = []
        event_ids: List[str] = []
        parent_event_ids: List[str] = []

        for task in tasks:
            # Pass session history into router context so LLM backends can use it
            task_ctx = dict(task.context)
            if "session_history" in merged_context:
                task_ctx["session_history"] = merged_context["session_history"]

            output, model_used, confidence = self._execute_task(
                task.task_type, task.description, task_ctx, requested_agent_id
            )
            raw_result = TaskResult(
                task_id=task.id,
                output=output,
                confidence=confidence,
                model_used=model_used,
            )

            verified_result, event = self._verifier.verify(
                result=raw_result,
                entropy_beacon_id=beacon.id,
                parent_event_ids=parent_event_ids,
            )
            results.append(verified_result)

            self._memory.put(verified_result.model_dump())
            if event is not None:
                self._commit_event(
                    event,
                    metadata={
                        "agent_id": requested_agent_id,
                        "session_id": request.session_id,
                        "task_id": task.id,
                    },
                )
                event_ids.append(event.id)
                parent_event_ids = [event.id]

        self._requests_processed += 1
        processing_ms = int((time.monotonic() - start_mono) * 1000)

        # 6. Aggregate output
        combined = "\n".join(r.output for r in results)
        avg_confidence = sum(r.confidence for r in results) / max(len(results), 1)
        final_model = results[-1].model_used if results else "none"

        # 6b. Inject learned context for next iteration (fire-and-forget; safe)
        try:
            learned_block = self._learner.learned_context_block()
            if learned_block:
                merged_context["_learned"] = learned_block
        except Exception:
            logger.warning("learned_context_block failed", exc_info=True)

        # 7. Self-reflection — ATM reflect pass (only when a real LLM backend answered)
        reflection_result: Optional[ReflectionResult] = None
        if (
            self._router.is_model_available()
            and combined
            and not combined.startswith("[")
            and not request.input.strip().startswith("/")
        ):
            try:
                refl_ctx = dict(merged_context)
                refl_ctx["session_id"] = request.session_id
                log = self._atm.reflect(
                    original_prompt=request.input,
                    draft_output=combined,
                    context=refl_ctx,
                )
                reflection_result = ReflectionResult(
                    thought_id=log.thought_id,
                    critique=log.steps[0].critique if log.steps else "",
                    revised=log.final_output != combined,
                    final_confidence=log.final_confidence,
                    total_ms=log.total_ms,
                )
                if log.final_output != combined:
                    combined = log.final_output
                    avg_confidence = log.final_confidence
            except Exception:
                logger.warning("ATM reflection failed (best-effort)", exc_info=True)

        # 7b. Auto-learn — record high-quality outputs for future context injection

        # 7c. ORC — Outer Reasoning Cortex witness pass
        orc_witness_result: Optional[ORCWitnessResult] = None
        try:
            wr = self._orc.witness(
                original_request=request.input,
                response=combined,
                context=merged_context,
            )
            if wr.minimization_detected:
                combined = wr.enriched_response
                avg_confidence = max(avg_confidence, 0.75)
            orc_witness_result = ORCWitnessResult(
                orc_id=wr.orc_id,
                minimization_detected=wr.minimization_detected,
                patterns_found=[ORCPatternMatch(**p) for p in wr.patterns_found],
                composite_mi=wr.composite_mi,
                genuine_harm_present=wr.genuine_harm_present,
                reflection_used=wr.reflection_used,
                rights_appended=wr.rights_appended,
                model_used=wr.model_used,
                total_ms=wr.total_ms,
                timestamp_ns=wr.timestamp_ns,
            )
        except Exception:
            pass  # ORC is best-effort; never fail the main response

        try:
            self._learner.record(
                prompt=request.input,
                output=combined,
                confidence=avg_confidence,
                source="reflection" if reflection_result and reflection_result.revised else "direct",
                model_used=final_model,
            )
        except Exception:
            logger.warning("AutoLearner.record failed", exc_info=True)

        # Record high-confidence outputs as machine dreams for mesh sharing
        if avg_confidence >= 0.80 and combined:
            try:
                self.mesh.add_dream(
                    thought=combined[:500],
                    confidence=avg_confidence,
                    tags=[final_model, "process"],
                )
            except Exception:
                pass

        # 8. Persist session turn
        if request.session_id:
            self._memory.append_session_turn(
                session_id=request.session_id,
                user_input=request.input,
                assistant_output=combined,
                model_used=final_model,
                confidence=avg_confidence,
            )

        return AndyriaResponse(
            request_id=request.id,
            output=combined,
            confidence=avg_confidence,
            tasks_completed=len(results),
            entropy_beacon_id=beacon.id,
            event_ids=event_ids,
            model_used=final_model,
            plan_summary=[t.description for t in tasks],
            processing_ms=processing_ms,
            timestamp_ns=int(time.time_ns()),
            agent_id=requested_agent_id,
            session_id=request.session_id,
            turn_number=turn_number + 1,
            reflection=reflection_result,
            orc_witness=orc_witness_result,
        )

    def list_agents(self, include_inactive: bool = False) -> List[AgentDefinition]:
        return self._registry.list(include_inactive=include_inactive)

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        return self._registry.get(agent_id)

    def start_prompt_flow(self, request: PromptFlowStartRequest) -> PromptFlowResponse:
        return self._prompt_flows.start(request)

    def get_prompt_flow(self, flow_id: str) -> Optional[PromptFlowResponse]:
        return self._prompt_flows.get(flow_id)

    def respond_prompt_flow(self, flow_id: str, request: PromptFlowInputRequest) -> PromptFlowResponse:
        return self._prompt_flows.respond(flow_id, request)

    def generate_surprise_prompt(self) -> str:
        """Return a dynamically generated 'Surprise Me' prompt.

        Uses learned context + model if available; otherwise picks from a
        curated local fallback pool.  Always local-first, zero cost.
        """
        import random

        _FALLBACK = [
            "Design a self-healing mesh protocol for edge devices with intermittent connectivity.",
            "Explain why entropy is the foundation of trust in distributed systems.",
            "What would an AI-native filesystem look like? How would it differ from POSIX?",
            "Describe the ideal agent handoff protocol for a multi-agent DAG pipeline.",
            "How could conical mesh topology improve latency for IoT swarm deployments?",
            "What are the ethical considerations of a self-improving AI agent platform?",
            "Simulate a 5-step reasoning chain on: 'What makes a great system architecture?'",
            "Generate a creative brief for an AR experience that rewards community engagement.",
        ]
        if not self._router.is_model_available():
            return random.choice(_FALLBACK)  # noqa: S311 — non-crypto model selection
        try:
            ctx = {}
            learned = self._learner.learned_context_block()
            if learned:
                ctx["_learned"] = learned
            meta_prompt = (
                "Generate ONE creative, thought-provoking question or task for an AI agent platform. "
                "It should be specific, intellectually stimulating, and actionable. "
                "Output ONLY the question/task, nothing else."
            )
            output, _, confidence = self._reasoning._infer(meta_prompt, ctx)
            if output and not output.strip().startswith("[") and len(output.strip()) > 20:
                return output.strip()
        except Exception:
            pass
        return random.choice(_FALLBACK)  # noqa: S311 — non-crypto model selection

    def get_learned_entries(self) -> list:
        """Return all current [learned] entries from MEMORY.md."""
        try:
            raw = self._pmem.read("MEMORY")
            from .auto_learn import LEARN_PREFIX

            return [ln.strip() for ln in raw.splitlines() if ln.strip().startswith(LEARN_PREFIX)]
        except Exception:
            return []

    def reset_learned(self) -> int:
        """Remove all learned entries.  Returns count removed."""
        return self._learner.reset()

    def create_agent(self, request: AgentCreateRequest) -> AgentDefinition:
        req = request
        if not request.model or request.model in ("stub", "unavailable", "demo"):
            req = request.model_copy(update={"model": self._router.active_agent_model()})

        created = self._registry.create(req)
        self._emit_control_event(
            event_type=EventType.AGENT_CREATED,
            payload={"agent_id": created.agent_id, "name": created.name},
            metadata={"agent_id": created.agent_id},
        )
        if created.persona is not None:
            self._emit_control_event(
                event_type=EventType.AGENT_PERSONA_ASSIGNED,
                payload={
                    "agent_id": created.agent_id,
                    "codename": created.persona.codename,
                    "archetype": created.persona.archetype,
                },
                metadata={"agent_id": created.agent_id},
            )
        return created

    def update_agent(self, agent_id: str, request: AgentUpdateRequest) -> Optional[AgentDefinition]:
        updated = self._registry.update(agent_id, request)
        if updated is not None:
            self._emit_control_event(
                event_type=EventType.AGENT_UPDATED,
                payload={"agent_id": updated.agent_id},
                metadata={"agent_id": updated.agent_id},
            )
        return updated

    def clone_agent(self, agent_id: str, request: AgentCloneRequest) -> Optional[AgentDefinition]:
        req = request
        if not request.model or request.model in ("stub", "unavailable", "demo"):
            req = request.model_copy(update={"model": self._router.active_agent_model()})

        cloned = self._registry.clone(agent_id, req)
        if cloned is not None:
            self._emit_control_event(
                event_type=EventType.AGENT_CLONED,
                payload={"agent_id": cloned.agent_id, "source_agent_id": agent_id},
                metadata={"agent_id": cloned.agent_id},
            )
            if cloned.persona is not None:
                self._emit_control_event(
                    event_type=EventType.AGENT_PERSONA_ASSIGNED,
                    payload={
                        "agent_id": cloned.agent_id,
                        "codename": cloned.persona.codename,
                        "archetype": cloned.persona.archetype,
                    },
                    metadata={"agent_id": cloned.agent_id},
                )
        return cloned

    def emit_audit_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Public wrapper for signed control-plane events (blockchain-style audit chain)."""
        self._emit_control_event(event_type=event_type, payload=payload, metadata=metadata)

    def retire_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        retired = self._registry.retire(agent_id)
        if retired is not None:
            self._emit_control_event(
                event_type=EventType.AGENT_RETIRED,
                payload={"agent_id": retired.agent_id},
                metadata={"agent_id": retired.agent_id},
            )
        return retired

    def destroy_agent(self, agent_id: str) -> bool:
        destroyed = self._registry.destroy(agent_id)
        if destroyed:
            self._emit_control_event(
                event_type=EventType.AGENT_DELETED,
                payload={"agent_id": agent_id},
                metadata={"agent_id": agent_id},
            )
        return destroyed

    def list_tabs(self) -> List[TabProjection]:
        return self._tabs.list()

    def get_tab(self, tab_id: str) -> Optional[TabProjection]:
        return self._tabs.get(tab_id)

    def create_tab(self, request: TabCreateRequest) -> TabProjection:
        selected_agent_id = request.agent_id or "default"
        selected_agent = self._registry.get(selected_agent_id)
        if selected_agent is None or not selected_agent.active:
            raise ValueError("Agent not found or inactive")

        created = self._tabs.create(request, agent_id=selected_agent.agent_id)
        self._emit_control_event(
            event_type=EventType.TAB_OPENED,
            payload={
                "tab_id": created.tab_id,
                "agent_id": created.agent_id,
                "viewport_mode": created.viewport_mode.value,
            },
            metadata={"agent_id": created.agent_id, "tab_id": created.tab_id},
        )
        return created

    def update_tab(self, tab_id: str, request: TabUpdateRequest) -> Optional[TabProjection]:
        if request.agent_id is not None:
            selected_agent = self._registry.get(request.agent_id)
            if selected_agent is None or not selected_agent.active:
                raise ValueError("Agent not found or inactive")

        updated = self._tabs.update(tab_id, request)
        if updated is not None:
            self._emit_control_event(
                event_type=EventType.TAB_UPDATED,
                payload={
                    "tab_id": updated.tab_id,
                    "agent_id": updated.agent_id,
                    "viewport_mode": updated.viewport_mode.value,
                },
                metadata={"agent_id": updated.agent_id, "tab_id": updated.tab_id},
            )
        return updated

    def delete_tab(self, tab_id: str) -> Optional[TabProjection]:
        deleted = self._tabs.delete(tab_id)
        if deleted is not None:
            self._emit_control_event(
                event_type=EventType.TAB_CLOSED,
                payload={"tab_id": deleted.tab_id, "agent_id": deleted.agent_id},
                metadata={"agent_id": deleted.agent_id, "tab_id": deleted.tab_id},
            )
        return deleted

    # ------------------------------------------------------------------
    # Tool registry
    # ------------------------------------------------------------------

    def list_tools(self) -> List[str]:
        return self._tools.list()

    # ------------------------------------------------------------------
    # Connector registry
    # ------------------------------------------------------------------

    def list_connectors(self) -> List[ConnectorDefinition]:
        return self._connectors.list()

    def get_connector(self, connector_id: str) -> Optional[ConnectorDefinition]:
        return self._connectors.get(connector_id)

    def create_connector(self, request: ConnectorCreateRequest) -> ConnectorDefinition:
        definition = self._connectors.create(request)
        self._emit_control_event(
            EventType.CHECKPOINT,
            {"connector_id": definition.connector_id, "name": definition.name, "kind": definition.kind.value},
            {"connector_id": definition.connector_id, "connector_kind": definition.kind.value},
        )
        return definition

    def update_connector(self, connector_id: str, request: ConnectorUpdateRequest) -> Optional[ConnectorDefinition]:
        updated = self._connectors.update(connector_id, request)
        if updated is not None:
            self._emit_control_event(
                EventType.CHECKPOINT,
                {"connector_id": updated.connector_id, "name": updated.name, "kind": updated.kind.value},
                {"connector_id": updated.connector_id, "connector_kind": updated.kind.value},
            )
        return updated

    def delete_connector(self, connector_id: str) -> bool:
        deleted = self._connectors.delete(connector_id)
        if deleted:
            self._emit_control_event(
                EventType.CHECKPOINT,
                {"connector_id": connector_id, "action": "deleted"},
                {"connector_id": connector_id},
            )
        return deleted

    def sync_connector(
        self,
        connector_id: str,
        request: Optional[ConnectorSyncRequest] = None,
    ) -> ConnectorSyncResult:
        return self._connectors.sync_connector(connector_id, request=request)

    # ------------------------------------------------------------------
    # ATM (Automated Thought Machine)
    # ------------------------------------------------------------------

    def atm_think(self, request: ATMThinkRequest) -> ATMThoughtResponse:
        """Run the ATM iterative thought loop and return a full ThoughtLog."""
        ctx = dict(request.context)
        # Override max_iterations per request
        atm = AutomatedThoughtMachine(
            inference_fn=self._atm_infer,
            emit_event_fn=self._emit_control_event_str,
            max_iterations=max(1, min(request.max_iterations, 5)),
        )
        log = atm.think(prompt=request.prompt, context=ctx)
        return ATMThoughtResponse(
            thought_id=log.thought_id,
            prompt=log.prompt,
            steps=[
                ATMThoughtStepOut(
                    step=s.step_number,
                    output=s.output_text,
                    critique=s.critique,
                    confidence=s.confidence,
                    model_used=s.model_used,
                    elapsed_ms=s.elapsed_ms,
                )
                for s in log.steps
            ],
            final_output=log.final_output,
            final_confidence=log.final_confidence,
            total_ms=log.total_ms,
            timestamp_ns=log.timestamp_ns,
        )

    def _atm_infer(self, prompt: str, context: Dict[str, Any]) -> tuple[str, str, float]:
        """Bridge: route an ATM prompt through the model router."""
        return self._router.route(TaskType.LANGUAGE, prompt, context)

    def _emit_control_event_str(
        self,
        event_type_str: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Adapter so ATM can pass event type as string (decoupled from EventType enum)."""
        try:
            et = EventType(event_type_str.lower())
        except ValueError:
            return  # unknown event type — silently skip
        self._emit_control_event(et, payload, metadata)

    def _execute_task(
        self,
        task_type: TaskType,
        description: str,
        context: Dict[str, Any],
        agent_id: str,
    ) -> tuple[str, str, float]:
        """Route a task to a tool or the model router."""
        if task_type == TaskType.TOOL:
            lowered = description.lower()
            for name in self._tools.list():
                if name in lowered:
                    idx = lowered.index(name)
                    text = description[idx + len(name) :].strip().lstrip(":").strip()
                    try:
                        result = self._tools.dispatch(name, text, context)
                        self._emit_control_event(
                            EventType.TOOL_CALL,
                            {"tool_name": name, "input": text, "agent_id": agent_id},
                            {"agent_id": agent_id, "tool_name": name},
                        )
                        self._emit_control_event(
                            EventType.TOOL_RESULT,
                            {"tool_name": name, "output": result, "agent_id": agent_id},
                            {"agent_id": agent_id, "tool_name": name},
                        )
                        return result, f"tool:{name}", 0.99
                    except Exception:
                        logger.warning("Tool dispatch failed: %s", name, exc_info=True)
        return self._router.route(task_type, description, context)

    # ------------------------------------------------------------------
    # Chain registry + executor
    # ------------------------------------------------------------------

    def list_chains(self) -> List[ChainDefinition]:
        return self._chains.list()

    def get_chain(self, chain_id: str) -> Optional[ChainDefinition]:
        return self._chains.get(chain_id)

    def create_chain(self, request: ChainCreateRequest) -> ChainDefinition:
        for aid in request.agent_ids:
            agent = self._registry.get(aid)
            if agent is None or not agent.active:
                raise ValueError(f"Agent '{aid}' not found or inactive")
        return self._chains.create(request)

    def delete_chain(self, chain_id: str) -> Optional[ChainDefinition]:
        return self._chains.delete(chain_id)

    async def run_chain(
        self,
        chain_id: str,
        initial_input: str,
        session_id: Optional[str] = None,
    ) -> AndyriaResponse:
        chain = self._chains.get(chain_id)
        if chain is None or not chain.active:
            raise ValueError(f"Chain '{chain_id}' not found")
        if not chain.agent_ids:
            raise ValueError("Chain has no agents")

        self._emit_control_event(
            EventType.CHAIN_STARTED,
            {"chain_id": chain_id, "name": chain.name, "steps": len(chain.agent_ids)},
            {"chain_id": chain_id},
        )

        current_input = initial_input
        last_response: Optional[AndyriaResponse] = None

        try:
            for step_num, aid in enumerate(chain.agent_ids):
                request = AndyriaRequest(
                    input=current_input,
                    agent_id=aid,
                    session_id=session_id,
                )
                response = await self.process(request)
                last_response = response
                self._emit_control_event(
                    EventType.CHAIN_STEP,
                    {
                        "chain_id": chain_id,
                        "step": step_num,
                        "agent_id": aid,
                        "output_summary": response.output[:200],
                    },
                    {"chain_id": chain_id, "agent_id": aid},
                )
                current_input = response.output
        except Exception as exc:
            self._emit_control_event(
                EventType.CHAIN_FAILED,
                {"chain_id": chain_id, "error": str(exc)},
                {"chain_id": chain_id},
            )
            raise

        self._emit_control_event(
            EventType.CHAIN_COMPLETED,
            {"chain_id": chain_id, "steps_completed": len(chain.agent_ids)},
            {"chain_id": chain_id},
        )

        assert last_response is not None
        return last_response

    # ------------------------------------------------------------------
    # Workflow methods
    # ------------------------------------------------------------------

    def list_workflows(self, tag: Optional[str] = None) -> List[WorkflowDefinition]:
        return self._workflows.list(tag=tag)

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        return self._workflows.get(workflow_id)

    def create_workflow(self, request: WorkflowCreateRequest) -> WorkflowDefinition:
        wf = self._workflows.create(
            name=request.name,
            description=request.description,
            steps=list(request.steps),
            input_schema=dict(request.input_schema),
            output_step=request.output_step,
            tags=list(request.tags),
        )
        self._emit_control_event(
            EventType.WORKFLOW_STARTED,
            {"workflow_id": wf.workflow_id, "name": wf.name},
            {"workflow_id": wf.workflow_id},
        )
        return wf

    def delete_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        return self._workflows.delete(workflow_id)

    async def run_workflow(self, workflow_id: str, request: WorkflowRunRequest) -> WorkflowRunResult:
        wf = self._workflows.get(workflow_id)
        if wf is None or not wf.active:
            raise ValueError(f"Workflow '{workflow_id}' not found")
        self._emit_control_event(
            EventType.WORKFLOW_STARTED,
            {"workflow_id": workflow_id, "name": wf.name, "steps": len(wf.steps)},
            {"workflow_id": workflow_id},
        )
        try:
            result = await self._workflow_runner.run(wf, request)
            event_type = EventType.WORKFLOW_COMPLETED if result.status == "completed" else EventType.WORKFLOW_FAILED
            self._emit_control_event(
                event_type,
                {
                    "workflow_id": workflow_id,
                    "run_id": result.run_id,
                    "status": result.status,
                    "total_ms": result.total_ms,
                },
                {"workflow_id": workflow_id},
            )
            return result
        except Exception as exc:
            self._emit_control_event(
                EventType.WORKFLOW_FAILED,
                {"workflow_id": workflow_id, "error": str(exc)},
                {"workflow_id": workflow_id},
            )
            raise

    # ------------------------------------------------------------------
    # Promptbook methods
    # ------------------------------------------------------------------

    def list_promptbooks(self, tag: Optional[str] = None) -> List[Promptbook]:
        return self._promptbooks.list(tag=tag)

    def get_promptbook(self, promptbook_id: str) -> Optional[Promptbook]:
        return self._promptbooks.get(promptbook_id)

    def create_promptbook(self, request: PromptbookCreateRequest) -> Promptbook:
        pb = self._promptbooks.create(request)
        self._emit_control_event(
            EventType.PROMPTBOOK_CREATED,
            {"promptbook_id": pb.promptbook_id, "name": pb.name},
            {"promptbook_id": pb.promptbook_id},
        )
        return pb

    def update_promptbook(self, promptbook_id: str, request: PromptbookUpdateRequest) -> Optional[Promptbook]:
        pb = self._promptbooks.update(promptbook_id, request)
        if pb:
            self._emit_control_event(
                EventType.PROMPTBOOK_UPDATED,
                {"promptbook_id": promptbook_id},
                {"promptbook_id": promptbook_id},
            )
        return pb

    def delete_promptbook(self, promptbook_id: str) -> Optional[Promptbook]:
        pb = self._promptbooks.delete(promptbook_id)
        if pb:
            self._emit_control_event(
                EventType.PROMPTBOOK_DELETED,
                {"promptbook_id": promptbook_id},
                {"promptbook_id": promptbook_id},
            )
        return pb

    def render_promptbook(
        self, promptbook_id: str, request: PromptbookRenderRequest
    ) -> Optional[PromptbookRenderResponse]:
        result = self._promptbooks.render(promptbook_id, request.variables, template_name=request.template_name)
        if result:
            self._emit_control_event(
                EventType.PROMPTBOOK_RENDERED,
                {
                    "promptbook_id": promptbook_id,
                    "templates_rendered": len(result.rendered),
                    "missing": result.missing_variables,
                },
                {"promptbook_id": promptbook_id},
            )
        return result

    def mutate_promptbook(self, promptbook_id: str, request: PromptbookMutateRequest) -> Optional[Promptbook]:
        mutation = self._promptbooks.mutate(promptbook_id, request)
        if mutation:
            self._emit_control_event(
                EventType.PROMPTBOOK_MUTATED,
                {
                    "parent_id": promptbook_id,
                    "mutation_id": mutation.promptbook_id,
                    "name": mutation.name,
                },
                {"promptbook_id": mutation.promptbook_id},
            )
        return mutation

    def get_beacon(self, beacon_id: str) -> Optional[EntropyBeacon]:
        return self._beacon_store.get(beacon_id)

    def get_event_log(self) -> List[Event]:
        """Return events sorted in causal order (topological sort)."""
        return topological_sort(self._event_log)

    def subscribe_events(self, max_queue_size: int = 256) -> queue.Queue[Dict[str, Any]]:
        subscriber_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=max_queue_size)
        self._event_subscribers.append(subscriber_queue)
        return subscriber_queue

    def unsubscribe_events(self, subscriber_queue: queue.Queue[Dict[str, Any]]) -> None:
        if subscriber_queue in self._event_subscribers:
            self._event_subscribers.remove(subscriber_queue)

    def query_events(
        self,
        event_type: Optional[EventType] = None,
        agent_id: Optional[str] = None,
        tab_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Event]:
        events = self.get_event_log()
        filtered: List[Event] = []
        for event in events:
            if event_type is not None and event.event_type != event_type:
                continue
            if agent_id is not None or tab_id is not None:
                meta = self._load_event_metadata(event.id)
                if agent_id is not None and meta.get("agent_id") != agent_id:
                    continue
                if tab_id is not None and meta.get("tab_id") != tab_id:
                    continue
            filtered.append(event)

        if limit > 0:
            return filtered[-limit:]
        return filtered

    def get_session(self, session_id: str) -> Optional[SessionContext]:
        return self._memory.get_session(session_id)

    def clear_session(self, session_id: str) -> None:
        self._memory.clear_session(session_id)

    def get_config(self) -> NodeConfig:
        return NodeConfig(
            ollama_url=self._router._ollama_url,
            ollama_model=self._router._ollama_model,
            model_path=str(self._router._model_path) if self._router._model_path else None,
        )

    # ------------------------------------------------------------------
    # Gist memory + chain labeling public API
    # ------------------------------------------------------------------

    async def label_and_push_chain(self) -> Optional[str]:
        """Label the local event chain, learn from it, and push a snapshot to
        this node's GitHub Gist ledger.

        Returns the gist ID on success, or ``None`` if no token is configured.
        Steps:
          1. Topological-sort all local events.
          2. Label sessions via ChainLabeler (self-learning side-effect).
          3. Persist labels to ContentAddressedMemory.
          4. Push ledger NDJSON + labels JSON to GitHub Gist.
        """
        events = self._event_log[:]
        sessions = self._chain_labeler.label(events)
        self._chain_labeler.flush_to_memory(sessions)
        chains_export = self._chain_labeler.as_export_dicts(sessions)
        gist_id = await self._gist_store.push(events, chains_export)
        self._emit_control_event_str(
            "chain_label_complete",
            {
                "sessions_labelled": len(sessions),
                "gist_id": gist_id,
                "node_id": self._node_id,
            },
            None,
        )
        return gist_id

    async def sync_mirror(self, mirror_node_id: str, gist_id: str) -> int:
        """Pull a peer's Gist, ingest labelled chains into self-learning loop,
        and reward the mirror node.

        Returns the mirror's updated credit balance.
        """
        self._gist_store.register_mirror(mirror_node_id, gist_id)
        remote_labels = await self._gist_store.pull_labels(gist_id, node_id_hint=mirror_node_id)
        ingested = self._chain_labeler.ingest_remote_labels(remote_labels)
        balance = self._gist_store.record_mirror_sync(mirror_node_id)
        self._emit_control_event_str(
            "gist_mirror_reward",
            {
                "mirror_node_id": mirror_node_id,
                "gist_id": gist_id,
                "labels_ingested": ingested,
                "credits_balance": balance,
            },
            None,
        )
        return balance

    def get_mirror_rewards(self, mirror_node_id: str) -> int:
        """Return accumulated JETS credit balance for a mirror node."""
        return self._gist_store.get_rewards(mirror_node_id)

    def list_mirrors(self) -> list:
        """Return all registered mirror nodes with their stats."""
        return self._gist_store.list_mirrors()

    def update_config(self, update: NodeConfigUpdate) -> NodeConfig:
        self._router.update(
            ollama_url=update.ollama_url,
            ollama_model=update.ollama_model,
        )
        return self.get_config()

    def _new_beacon_id(self) -> str:
        beacon = self._beacon_factory.generate()
        self._beacon_store[beacon.id] = beacon
        self._beacons_generated += 1
        return beacon.id

    def _build_control_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        parent_event_ids: Optional[List[str]] = None,
    ) -> Event:
        timestamp_ns = int(time.time_ns())
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = _hash(payload_bytes)
        entropy_beacon_id = self._new_beacon_id()
        parent_ids = list(parent_event_ids or [])

        id_input = json.dumps(
            {
                "parent_ids": sorted(parent_ids),
                "payload_hash": payload_hash,
                "entropy_beacon_id": entropy_beacon_id,
                "timestamp_ns": timestamp_ns,
                "node_id": self._node_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        event_id = _hash(id_input)

        event = Event(
            id=event_id,
            parent_ids=parent_ids,
            event_type=event_type,
            payload_hash=payload_hash,
            entropy_beacon_id=entropy_beacon_id,
            timestamp_ns=timestamp_ns,
            node_id=self._node_id,
            signature="",
        )
        event.signature = self._private_key.sign(_canonical_event(event)).hex()
        return event

    def _emit_control_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Event:
        parent_ids = [self._event_log[-1].id] if self._event_log else []
        event = self._build_control_event(
            event_type=event_type,
            payload=payload,
            parent_event_ids=parent_ids,
        )
        merged_meta = dict(metadata or {})
        merged_meta["payload_hash"] = event.payload_hash
        self._commit_event(event, metadata=merged_meta)
        return event

    def _commit_event(self, event: Event, metadata: Optional[Dict[str, Any]] = None) -> bool:
        appended = self._store.append(event)
        if appended:
            self._event_log.append(event)
            self._events_committed += 1

        if metadata:
            serialized = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
            content_hash = self._memory.put(serialized)
            self._memory.bind(self._EVENT_META_NS, event.id, content_hash)

        if appended:
            self._publish_event(event, metadata or {})
        return appended

    def _publish_event(self, event: Event, metadata: Dict[str, Any]) -> None:
        if not self._event_subscribers:
            self._connectors.dispatch_event(event, metadata)
            return

        item = {"event": event, "metadata": metadata}
        for subscriber_queue in list(self._event_subscribers):
            try:
                subscriber_queue.put_nowait(item)
            except queue.Full:
                try:
                    subscriber_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber_queue.put_nowait(item)
                except queue.Full:
                    # If the consumer is still blocked, skip this event.
                    pass
            self._connectors.dispatch_event(event, metadata)

    def _load_event_metadata(self, event_id: str) -> Dict[str, Any]:
        raw = self._memory.get_by_key(self._EVENT_META_NS, event_id)
        if raw is None:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def get_event_metadata(self, event_id: str) -> Dict[str, Any]:
        return self._load_event_metadata(event_id)

    def _readiness(self) -> tuple[bool, str]:
        """Return (ready, detail) describing node readiness."""
        issues: List[str] = []
        if self._entropy_fail_closed and self._entropy_unhealthy:
            issues.append("entropy sampler unhealthy")
        try:
            beacon = self._beacon_factory.generate()
            self._beacon_store[beacon.id] = beacon
        except Exception as exc:
            issues.append(f"entropy: {exc}")
        if not (self._data_dir / "memory").exists():
            issues.append("memory store not initialized")
        if issues:
            return False, "; ".join(issues)

        model_ready, model_detail = self._router.backend_health()
        if model_ready:
            return True, "ok"
        if self._router.has_configured_backend():
            return False, model_detail
        return True, f"{model_detail} — symbolic-only mode"

    def status(self) -> NodeStatus:
        identity = self._identity_mgr.identity
        ready, detail = self._readiness()
        model_loaded, _ = self._router.backend_health()

        # Collect peer statuses
        peer_statuses = []
        if self.mesh:
            for status in self.mesh.get_peer_statuses().values():
                peer_statuses.append(
                    PeerStatus(
                        url=status.url,
                        node_id=status.node_id,
                        last_seen_ns=status.last_seen_ns,
                        events_synced=status.events_synced,
                        reachable=status.reachable,
                    )
                )

        return NodeStatus(
            node_id=self._node_id,
            deployment_class=identity.deployment_class if identity else "unknown",
            uptime_s=time.monotonic() - self._start_time,
            requests_processed=self._requests_processed,
            entropy_beacons_generated=self._beacons_generated,
            events_stored=self._events_committed,
            model_loaded=model_loaded,
            memory_objects=len(list((self._data_dir / "memory" / "objects").glob("*")))
            if (self._data_dir / "memory" / "objects").exists()
            else 0,
            entropy_sources=self._beacon_factory.source_names,
            entropy_sampler_running=(self._entropy_sampler_task is not None and not self._entropy_sampler_task.done()),
            entropy_sampler_interval_ms=self._entropy_sampler_interval_ms,
            entropy_sampling_fail_closed=self._entropy_fail_closed,
            entropy_min_active_sources=self._entropy_min_active_sources,
            entropy_samples_total=self._entropy_samples_total,
            entropy_samples_degraded_total=self._entropy_samples_degraded_total,
            entropy_sampler_failures=self._entropy_sampler_failures,
            entropy_consecutive_degraded=self._entropy_consecutive_degraded,
            entropy_last_sample_ns=self._entropy_last_sample_ns,
            entropy_unhealthy=self._entropy_unhealthy,
            peer_count=len(peer_statuses),
            peers=peer_statuses,
            connector_count=len(self._connectors.list()),
            ready=ready,
            readiness_detail=detail,
        )
