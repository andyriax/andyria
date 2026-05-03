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
import hashlib
import json
import operator
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .entropy import EntropyBeaconFactory
from .memory import ContentAddressedMemory
from .models import (
    AndyriaRequest,
    AndyriaResponse,
    EntropyBeacon,
    Event,
    NodeStatus,
    TaskResult,
    TaskType,
)
from .node import NodeIdentityManager
from .planner import Planner
from .verifier import Verifier


def _hash(data: bytes) -> str:
    try:
        import blake3  # type: ignore
        return blake3.blake3(data).hexdigest()
    except ImportError:
        return hashlib.sha3_256(data).hexdigest()


# ---------------------------------------------------------------------------
# Safe math evaluator (no eval(), no exec())
# ---------------------------------------------------------------------------

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_math(expr: str) -> float:
    """Evaluate a basic arithmetic expression using AST only. No eval()."""
    tree = ast.parse(expr.strip(), mode="eval")

    def _eval(node: ast.expr) -> float:  # type: ignore[return]
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"Unsupported constant: {node.value!r}")
        if isinstance(node, ast.BinOp):
            op_fn = _SAFE_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op_fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op_fn = _SAFE_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
            return op_fn(_eval(node.operand))
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    return _eval(tree)


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

        if self._model_path and self._model_path.exists():
            try:
                from llama_cpp import Llama  # type: ignore
                self._llm = Llama(
                    model_path=str(self._model_path),
                    n_ctx=2048,
                    n_threads=min(4, __import__("os").cpu_count() or 1),
                    verbose=False,
                )
            except Exception:
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
            return self._llm_inference(description)

        if self._ollama_url:
            return self._ollama_inference(description)

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

    def _llm_inference(self, prompt: str) -> tuple[str, str, float]:
        try:
            resp = self._llm(  # type: ignore[misc]
                f"<|system|>You are Andyria, a helpful assistant.<|user|>{prompt}<|assistant|>",
                max_tokens=512,
                temperature=0.7,
                stop=["<|user|>", "<|system|>"],
            )
            text = resp["choices"][0]["text"].strip()
            return text, "llama_cpp_local", 0.85
        except Exception as exc:
            return f"[LLM error: {exc}]", "llama_cpp_local", 0.0

    def _ollama_inference(self, prompt: str) -> tuple[str, str, float]:
        try:
            import httpx
            model = self._ollama_model or "phi3"
            resp = httpx.post(
                f"{self._ollama_url}/api/generate",
                json={"model": model, "prompt": f"You are Andyria, a helpful assistant.\n\n{prompt}", "stream": False},
                timeout=120.0,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            return text, f"ollama:{model}", 0.80
        except Exception as exc:
            return f"[Ollama error: {exc}]", "ollama", 0.0

    def _stub_response(self, prompt: str) -> tuple[str, str, float]:
        return (
            f"[Andyria stub] Received: '{prompt[:120]}'. "
            "Install llama-cpp-python and a GGUF model for language model inference.",
            "stub",
            0.5,
        )


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class Coordinator:
    """Main intelligence loop for an Andyria node.

    Orchestrates: entropy → plan → route → verify → commit → respond.
    """

    def __init__(
        self,
        data_dir: Path,
        node_id: str,
        deployment_class: str = "edge",
        entropy_sources: Optional[List[str]] = None,
        model_path: Optional[Path] = None,
        ollama_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
    ) -> None:
        self._data_dir = data_dir
        self._node_id = node_id
        self._start_time = time.monotonic()
        self._requests_processed = 0
        self._events_committed = 0
        self._beacons_generated = 0
        self._event_log: List[Event] = []
        self._beacon_store: Dict[str, EntropyBeacon] = {}

        # Identity
        self._identity_mgr = NodeIdentityManager(data_dir, node_id, deployment_class)
        self._identity_mgr.load_or_create()
        private_key = self._identity_mgr.private_key

        # Entropy
        self._beacon_factory = EntropyBeaconFactory(node_id, private_key, entropy_sources)

        # Memory
        self._memory = ContentAddressedMemory(data_dir, node_id, private_key)

        # Intelligence components
        self._router = ModelRouter(model_path, ollama_url, ollama_model)
        self._planner = Planner()
        self._verifier = Verifier(node_id, private_key)

    async def process(self, request: AndyriaRequest) -> AndyriaResponse:
        """Execute the full intelligence loop for one request."""
        # 1. Anchor request to physical entropy
        beacon = self._beacon_factory.generate()
        self._beacon_store[beacon.id] = beacon
        self._beacons_generated += 1

        # 2. Persist request payload
        self._memory.put(request.model_dump())

        # 3. Plan
        tasks = self._planner.plan(
            request_id=request.id,
            user_input=request.input,
            context=request.context,
            entropy_beacon_id=beacon.id,
        )

        # 4. Route → verify → commit
        results: List[TaskResult] = []
        event_ids: List[str] = []
        parent_event_ids: List[str] = []

        for task in tasks:
            output, model_used, confidence = self._router.route(
                task.task_type, task.description, task.context
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
                self._event_log.append(event)
                event_ids.append(event.id)
                parent_event_ids = [event.id]
                self._events_committed += 1

        self._requests_processed += 1

        # 5. Aggregate output
        combined = "\n".join(r.output for r in results)
        avg_confidence = sum(r.confidence for r in results) / max(len(results), 1)

        return AndyriaResponse(
            request_id=request.id,
            output=combined,
            confidence=avg_confidence,
            tasks_completed=len(results),
            entropy_beacon_id=beacon.id,
            event_ids=event_ids,
            model_used=results[-1].model_used if results else "none",
            plan_summary=[t.description for t in tasks],
        )

    def get_beacon(self, beacon_id: str) -> Optional[EntropyBeacon]:
        return self._beacon_store.get(beacon_id)

    def get_event_log(self) -> List[Event]:
        return list(self._event_log)

    def status(self) -> NodeStatus:
        identity = self._identity_mgr.identity
        return NodeStatus(
            node_id=self._node_id,
            deployment_class=identity.deployment_class if identity else "unknown",
            uptime_s=time.monotonic() - self._start_time,
            requests_processed=self._requests_processed,
            entropy_beacons_generated=self._beacons_generated,
            events_stored=self._events_committed,
            model_loaded=self._router._llm is not None,
            memory_objects=len(list((self._data_dir / "memory" / "objects").glob("*")))
                if (self._data_dir / "memory" / "objects").exists() else 0,
            entropy_sources=["os_urandom", "clock_jitter"],
        )
