"""ATM — Automated Thought Machine.

An independent thought incubator, peer-level to the main coordinator loop.
Inspired by Jetstreamin's ATM concept: iteratively processes context, logs all
operations to the DAG, and outputs refined results.

Key properties:
- Stateless between sessions (results are logged to the event DAG)
- Configurable iteration depth (thought cycles)
- Produces a structured ThoughtLog persisted in ContentAddressedMemory
- Emits ATM_STARTED, ATM_STEP, ATM_COMPLETE (or ATM_FAILED) events
- Each step includes: critique → revise → score loop
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class ThoughtStep:
    """One cycle of ATM reasoning: initial → critique → revision."""

    def __init__(
        self,
        step_number: int,
        input_text: str,
        output_text: str,
        critique: str,
        confidence: float,
        model_used: str,
        elapsed_ms: int,
    ) -> None:
        self.step_number = step_number
        self.input_text = input_text
        self.output_text = output_text
        self.critique = critique
        self.confidence = confidence
        self.model_used = model_used
        self.elapsed_ms = elapsed_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step_number,
            "input": self.input_text,
            "output": self.output_text,
            "critique": self.critique,
            "confidence": self.confidence,
            "model_used": self.model_used,
            "elapsed_ms": self.elapsed_ms,
        }


class ThoughtLog:
    """Complete record of one ATM think session."""

    def __init__(
        self,
        thought_id: str,
        prompt: str,
        steps: List[ThoughtStep],
        final_output: str,
        final_confidence: float,
        total_ms: int,
        context: Dict[str, Any],
    ) -> None:
        self.thought_id = thought_id
        self.prompt = prompt
        self.steps = steps
        self.final_output = final_output
        self.final_confidence = final_confidence
        self.total_ms = total_ms
        self.context = context
        self.timestamp_ns = int(time.time_ns())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thought_id": self.thought_id,
            "prompt": self.prompt,
            "steps": [s.to_dict() for s in self.steps],
            "final_output": self.final_output,
            "final_confidence": self.final_confidence,
            "total_ms": self.total_ms,
            "timestamp_ns": self.timestamp_ns,
            "context": self.context,
        }


# ---------------------------------------------------------------------------
# Inference delegate type
# ---------------------------------------------------------------------------

# Signature: (prompt, context) → (output, model_used, confidence)
InferenceFn = Callable[[str, Dict[str, Any]], tuple[str, str, float]]


# ---------------------------------------------------------------------------
# ATM core
# ---------------------------------------------------------------------------


class AutomatedThoughtMachine:
    """Iterative thought incubator.

    Runs `max_iterations` think cycles. Each cycle:
      1. Generate initial answer (or refine previous answer)
      2. Self-critique that answer
      3. Decide whether to continue based on confidence threshold

    All output is logged to the DAG via the emit_event callback.
    """

    DEFAULT_MAX_ITERATIONS = 3
    DEFAULT_CONFIDENCE_THRESHOLD = 0.85  # stop early if we exceed this
    DEFAULT_MIN_ITERATIONS = 1

    def __init__(
        self,
        inference_fn: InferenceFn,
        emit_event_fn: Optional[Callable[..., Any]] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        reasoning_engine: Optional[Any] = None,
    ) -> None:
        self._infer = inference_fn
        self._emit = emit_event_fn  # coordinator._emit_control_event
        self._max_iter = max_iterations
        self._conf_threshold = confidence_threshold
        self._reasoning = reasoning_engine  # Optional ReasoningEngine escalation

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def think(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        thought_id: Optional[str] = None,
    ) -> ThoughtLog:
        """Run the full ATM think cycle and return a ThoughtLog."""
        ctx = dict(context or {})
        tid = thought_id or f"atm-{int(time.time_ns()) % (10**12):012d}"
        start = time.monotonic()
        steps: List[ThoughtStep] = []

        if self._emit:
            self._emit(
                "ATM_STARTED",
                {"thought_id": tid, "prompt": prompt[:200], "max_iterations": self._max_iter},
                {"thought_id": tid},
            )

        current_input = prompt
        final_output = ""
        final_confidence = 0.0
        final_model = "stub"

        for i in range(self._max_iter):
            step_start = time.monotonic()

            # --- Step 1: Generate / Refine ---
            gen_output, model_used, confidence = self._infer(current_input, ctx)

            # --- Step 2: Self-Critique ---
            critique_prompt = self._build_critique_prompt(prompt, gen_output, i)
            critique_text, _, _ = self._infer(critique_prompt, ctx)

            # --- Step 3: If not last iteration and critique is meaningful, revise ---
            if i < self._max_iter - 1 and self._should_revise(critique_text, confidence):
                revision_prompt = self._build_revision_prompt(prompt, gen_output, critique_text)
                revised_output, model_used, confidence = self._infer(revision_prompt, ctx)
                gen_output = revised_output

            elapsed = int((time.monotonic() - step_start) * 1000)
            step = ThoughtStep(
                step_number=i + 1,
                input_text=current_input,
                output_text=gen_output,
                critique=critique_text,
                confidence=confidence,
                model_used=model_used,
                elapsed_ms=elapsed,
            )
            steps.append(step)

            final_output = gen_output
            final_confidence = confidence
            final_model = model_used

            if self._emit:
                self._emit(
                    "ATM_STEP",
                    {
                        "thought_id": tid,
                        "step": i + 1,
                        "confidence": round(confidence, 4),
                        "model": model_used,
                        "output_summary": gen_output[:200],
                    },
                    {"thought_id": tid},
                )

            # Early exit if confident enough
            if confidence >= self._conf_threshold:
                break

            # Feed output as input to next iteration
            current_input = self._build_next_input(prompt, gen_output, critique_text, i)

        total_ms = int((time.monotonic() - start) * 1000)

        # Escalate to ReasoningEngine if still low confidence after max iterations
        if final_confidence < 0.6 and self._reasoning is not None:
            try:
                r = self._reasoning.reason(prompt, ctx)
                if r.final_confidence > final_confidence and not self._is_stub(r.synthesis):
                    final_output = r.synthesis
                    final_confidence = r.final_confidence
                    final_model = r.steps[-1].model_used if r.steps else final_model
            except Exception:
                pass

        log = ThoughtLog(
            thought_id=tid,
            prompt=prompt,
            steps=steps,
            final_output=final_output,
            final_confidence=final_confidence,
            total_ms=total_ms,
            context={k: v for k, v in ctx.items() if k in ("agent_id", "agent_name", "session_id")},
        )

        if self._emit:
            self._emit(
                "ATM_COMPLETE",
                {
                    "thought_id": tid,
                    "steps_taken": len(steps),
                    "final_confidence": round(final_confidence, 4),
                    "total_ms": total_ms,
                    "model": final_model,
                },
                {"thought_id": tid},
            )

        return log

    def reflect(
        self,
        original_prompt: str,
        draft_output: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ThoughtLog:
        """Lightweight single-pass self-reflection on an existing draft output.

        Used by the coordinator to silently reflect on its own responses.
        Runs 1 critique + 1 revision cycle (no iterative loop).
        """
        ctx = dict(context or {})
        tid = f"reflect-{int(time.time_ns()) % (10**12):012d}"
        start = time.monotonic()

        if self._emit:
            self._emit(
                "REFLECTION_STARTED",
                {"thought_id": tid, "prompt": original_prompt[:200]},
                {"thought_id": tid},
            )

        # Critique
        critique_prompt = self._build_critique_prompt(original_prompt, draft_output, 0)
        critique_text, model_used, _ = self._infer(critique_prompt, ctx)

        # Revise
        revision_prompt = self._build_revision_prompt(original_prompt, draft_output, critique_text)
        revised_output, model_used, confidence = self._infer(revision_prompt, ctx)

        elapsed = int((time.monotonic() - start) * 1000)

        # Only replace draft if revision is meaningfully different and non-empty
        if not self._is_meaningful_revision(draft_output, revised_output):
            revised_output = draft_output

        step = ThoughtStep(
            step_number=1,
            input_text=original_prompt,
            output_text=revised_output,
            critique=critique_text,
            confidence=confidence,
            model_used=model_used,
            elapsed_ms=elapsed,
        )

        log = ThoughtLog(
            thought_id=tid,
            prompt=original_prompt,
            steps=[step],
            final_output=revised_output,
            final_confidence=confidence,
            total_ms=elapsed,
            context={k: v for k, v in ctx.items() if k in ("agent_id", "agent_name", "session_id")},
        )

        if self._emit:
            self._emit(
                "REFLECTION_COMPLETE",
                {
                    "thought_id": tid,
                    "critique_summary": critique_text[:200],
                    "confidence": round(confidence, 4),
                    "revised": revised_output != draft_output,
                    "total_ms": elapsed,
                },
                {"thought_id": tid},
            )

        return log

    # ------------------------------------------------------------------
    # Prompt construction helpers
    # ------------------------------------------------------------------

    def _build_critique_prompt(self, original: str, output: str, iteration: int) -> str:
        return (
            f"[ATM Self-Critique — pass {iteration + 1}]\n"
            f"Original question: {original}\n\n"
            f"Current answer:\n{output}\n\n"
            "Briefly identify any inaccuracies, gaps, or improvements (2-3 sentences max). "
            "If the answer is already accurate and complete, say 'No issues found.'"
        )

    def _build_revision_prompt(self, original: str, output: str, critique: str) -> str:
        return (
            f"[ATM Revision]\n"
            f"Original question: {original}\n\n"
            f"Previous answer:\n{output}\n\n"
            f"Critique:\n{critique}\n\n"
            "Write an improved answer that addresses the critique. "
            "Be concise and direct."
        )

    def _build_next_input(self, original: str, output: str, critique: str, iteration: int) -> str:
        return (
            f"[ATM Iteration {iteration + 2} — refine based on critique]\n"
            f"Original question: {original}\n"
            f"Previous answer: {output[:300]}\n"
            f"Critique: {critique[:200]}\n\n"
            "Provide a refined answer:"
        )

    # ------------------------------------------------------------------
    # Decision helpers
    # ------------------------------------------------------------------

    def _should_revise(self, critique: str, confidence: float) -> bool:
        """Return True if the critique suggests meaningful revision is needed."""
        if confidence >= self._conf_threshold:
            return False
        critique_lower = critique.lower()
        if "no issues" in critique_lower or "looks good" in critique_lower:
            return False
        return len(critique.strip()) > 20

    def _is_meaningful_revision(self, original: str, revised: str) -> bool:
        """Return True if the revision is non-trivially different from the original."""
        if not revised or not revised.strip():
            return False
        # Normalize whitespace for comparison
        orig_norm = " ".join(original.split()).lower()
        rev_norm = " ".join(revised.split()).lower()
        if rev_norm == orig_norm:
            return False
        # Require at least 10% character change
        diff = abs(len(rev_norm) - len(orig_norm))
        return diff > max(10, len(orig_norm) * 0.05)

    @staticmethod
    def _is_stub(text: str) -> bool:
        """Return True if text looks like a stub/offline placeholder response."""
        if not text:
            return True
        lo = text.lower().strip()
        return (
            lo.startswith("[")
            or lo.startswith("received:")
            or lo.startswith("no language model")
            or lo.startswith("no llm")
        )
