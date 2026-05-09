"""ReasoningEngine — multi-step chain-of-thought for Andyria.

Three-phase loop (all via the cheapest available inference path):
  1. Decompose — break the prompt into 2-4 targeted sub-questions
  2. Analyze   — answer each sub-question independently
  3. Synthesize — combine sub-answers into a single coherent response

Falls back gracefully to single-step inference if decomposition fails
(e.g. no LLM available, or model returns only one usable sub-question).

All stages emit signed events via the coordinator event bus.
Zero external API calls — reuses the same InferenceFn bridge as ATM.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

# Signature mirrors ATM's InferenceFn: (prompt, context) → (output, model, confidence)
InferenceFn = Callable[[str, Dict[str, Any]], tuple[str, str, float]]
EmitFn = Callable[[str, Dict[str, Any], Optional[Dict[str, Any]]], None]


# ---------------------------------------------------------------------------
# Internal data types (plain dataclasses — models.py has the Pydantic versions)
# ---------------------------------------------------------------------------


class _Step:
    __slots__ = ("number", "question", "answer", "confidence", "model_used", "elapsed_ms")

    def __init__(
        self,
        number: int,
        question: str,
        answer: str,
        confidence: float,
        model_used: str,
        elapsed_ms: int,
    ) -> None:
        self.number = number
        self.question = question
        self.answer = answer
        self.confidence = confidence
        self.model_used = model_used
        self.elapsed_ms = elapsed_ms


class ReasoningResult:
    """Output of a complete ReasoningEngine.reason() call."""

    def __init__(
        self,
        trace_id: str,
        original_prompt: str,
        steps: List[_Step],
        synthesis: str,
        final_confidence: float,
        total_ms: int,
    ) -> None:
        self.trace_id = trace_id
        self.original_prompt = original_prompt
        self.steps = steps
        self.synthesis = synthesis
        self.final_confidence = final_confidence
        self.total_ms = total_ms
        self.timestamp_ns = int(time.time_ns())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "original_prompt": self.original_prompt,
            "steps": [
                {
                    "step_number": s.number,
                    "question": s.question,
                    "answer": s.answer,
                    "confidence": s.confidence,
                    "model_used": s.model_used,
                    "elapsed_ms": s.elapsed_ms,
                }
                for s in self.steps
            ],
            "synthesis": self.synthesis,
            "final_confidence": self.final_confidence,
            "total_ms": self.total_ms,
            "timestamp_ns": self.timestamp_ns,
        }


# ---------------------------------------------------------------------------
# ReasoningEngine
# ---------------------------------------------------------------------------


class ReasoningEngine:
    """Chain-of-thought reasoning: decompose → analyze → synthesize.

    Designed as a peer to ATM — same InferenceFn bridge, same event emission
    pattern, fully decoupled from the coordinator except through those two
    injected callables.

    Usage::

        engine = ReasoningEngine(
            inference_fn=coordinator._atm_infer,
            emit_event_fn=coordinator._emit_control_event_str,
        )
        result = engine.reason("Why does CAP theorem matter for mesh networks?")
        print(result.synthesis)
    """

    CONFIDENCE_THRESHOLD = 0.88  # stop early after synthesis if above this
    MAX_SUB_QUESTIONS = 4
    MIN_SUB_QUESTIONS = 2

    def __init__(
        self,
        inference_fn: InferenceFn,
        emit_event_fn: Optional[EmitFn] = None,
    ) -> None:
        self._infer = inference_fn
        self._emit = emit_event_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reason(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ReasoningResult:
        """Run the full decompose → analyze → synthesize cycle."""
        ctx = dict(context or {})
        trace_id = f"reason-{uuid.uuid4().hex[:12]}"
        start = time.monotonic()

        self._fire(
            "REASONING_STARTED",
            {
                "trace_id": trace_id,
                "prompt": prompt[:200],
            },
            trace_id,
        )

        # Phase 1 — Decompose
        sub_questions = self._decompose(prompt, ctx)

        # Phase 2 — Analyze each sub-question
        steps: List[_Step] = []
        if len(sub_questions) >= self.MIN_SUB_QUESTIONS:
            for i, question in enumerate(sub_questions[: self.MAX_SUB_QUESTIONS], start=1):
                step = self._analyze(i, question, prompt, ctx)
                steps.append(step)
                self._fire(
                    "REASONING_STEP",
                    {
                        "trace_id": trace_id,
                        "step": i,
                        "question": question[:200],
                        "confidence": round(step.confidence, 4),
                        "model": step.model_used,
                    },
                    trace_id,
                )

        # Phase 3 — Synthesize
        synthesis, synth_model, synth_conf = self._synthesize(prompt, steps, ctx)

        # If synthesis is a stub/empty, fall back to single direct answer
        if self._is_stub(synthesis) or not synthesis.strip():
            synthesis, synth_model, synth_conf = self._infer(prompt, ctx)

        total_ms = int((time.monotonic() - start) * 1000)

        self._fire(
            "REASONING_COMPLETE",
            {
                "trace_id": trace_id,
                "steps_taken": len(steps),
                "final_confidence": round(synth_conf, 4),
                "total_ms": total_ms,
                "model": synth_model,
            },
            trace_id,
        )

        return ReasoningResult(
            trace_id=trace_id,
            original_prompt=prompt,
            steps=steps,
            synthesis=synthesis,
            final_confidence=synth_conf,
            total_ms=total_ms,
        )

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def _decompose(self, prompt: str, ctx: Dict[str, Any]) -> List[str]:
        """Ask the model to break the prompt into focused sub-questions."""
        decompose_prompt = (
            f"[Reasoning: Decompose]\n"
            f"Break the following question into {self.MIN_SUB_QUESTIONS}-{self.MAX_SUB_QUESTIONS} "
            f"focused sub-questions that, when answered together, fully address the original.\n"
            f"Output ONLY a numbered list. No introduction, no explanation.\n\n"
            f"Question: {prompt}"
        )
        try:
            output, _, _ = self._infer(decompose_prompt, ctx)
            if self._is_stub(output):
                return []
            return self._parse_numbered_list(output)
        except Exception:
            return []

    def _analyze(
        self,
        number: int,
        question: str,
        original: str,
        ctx: Dict[str, Any],
    ) -> _Step:
        """Answer one sub-question with the original context in view."""
        t0 = time.monotonic()
        analyze_prompt = (
            f"[Reasoning: Analyze — step {number}]\n"
            f"Original question: {original}\n\n"
            f"Sub-question {number}: {question}\n\n"
            f"Answer this sub-question concisely (2-4 sentences)."
        )
        try:
            answer, model, confidence = self._infer(analyze_prompt, ctx)
            if self._is_stub(answer):
                answer, model, confidence = question, "unavailable", 0.1
        except Exception:
            answer, model, confidence = "", "unavailable", 0.1
        elapsed = int((time.monotonic() - t0) * 1000)
        return _Step(
            number=number,
            question=question,
            answer=answer,
            confidence=confidence,
            model_used=model,
            elapsed_ms=elapsed,
        )

    def _synthesize(
        self,
        original: str,
        steps: List[_Step],
        ctx: Dict[str, Any],
    ) -> tuple[str, str, float]:
        """Combine sub-answers into a final coherent response."""
        if not steps:
            # No sub-answers — direct single inference
            return self._infer(original, ctx)

        sub_answers = "\n".join(f"  {s.number}. Q: {s.question}\n     A: {s.answer}" for s in steps)
        synth_prompt = (
            f"[Reasoning: Synthesize]\n"
            f"Original question: {original}\n\n"
            f"Sub-question answers:\n{sub_answers}\n\n"
            f"Write a single, concise, well-structured answer to the original question "
            f"that integrates all the above findings."
        )
        try:
            return self._infer(synth_prompt, ctx)
        except Exception:
            # Return best single step answer as fallback
            best = max(steps, key=lambda s: s.confidence)
            return best.answer, best.model_used, best.confidence

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_numbered_list(self, text: str) -> List[str]:
        """Extract items from a numbered list (1. ... / 1) ... / - ...)."""
        import re

        lines = text.strip().splitlines()
        items: List[str] = []
        for line in lines:
            # Match patterns: "1.", "1)", "-", "*"
            m = re.match(r"^\s*(?:\d+[.)]\s*|[-*]\s+)(.*)", line)
            if m:
                item = m.group(1).strip()
                if len(item) > 8:  # ignore very short/empty captures
                    items.append(item)
        return items

    @staticmethod
    def _is_stub(text: str) -> bool:
        """Return True if the inference returned a stub/offline response."""
        if not text:
            return True
        lo = text.lower().strip()
        return (
            lo.startswith("[")
            or lo.startswith("received:")
            or lo.startswith("no language model")
            or lo.startswith("no llm")
        )

    def _fire(
        self,
        event_type: str,
        payload: Dict[str, Any],
        trace_id: str,
    ) -> None:
        if self._emit:
            try:
                self._emit(event_type, payload, {"trace_id": trace_id})
            except Exception:
                pass
