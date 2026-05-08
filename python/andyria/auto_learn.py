"""AutoLearner — self-recording loop for Andyria.

Records high-confidence reasoning patterns to MEMORY.md so the system
builds on its own past successes.  Every entry is prefixed with ``[learned]``
for easy identification and selective injection into future prompts.

Design principles:
- Local-first: pure file I/O via PersistentMemory, zero network calls
- Bounded: max 15 learned entries, max 600 chars injected into prompts
- Non-destructive: only appends to MEMORY.md; user entries are preserved
- Deduplicated: skips entries that are near-duplicates of recent ones
- Graceful: never raises — all errors are silently swallowed
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional

LEARN_PREFIX = "[learned] "
MAX_ENTRIES = 15
MAX_INJECT_CHARS = 600
MIN_CONFIDENCE = 0.80
MIN_OUTPUT_LEN = 40  # ignore very short stub-like outputs

_STUB_MARKERS = (
    "[",
    "received:",
    "no language model",
    "no llm",
    "configure ollama",
)

EmitFn = Callable[[str, Dict[str, Any], Optional[Dict[str, Any]]], None]


class AutoLearner:
    """Self-recording loop that distils high-quality responses into MEMORY.md.

    Args:
        persistent_memory: A ``PersistentMemory`` instance (already open).
        confidence_threshold: Minimum confidence to record (default 0.80).
        emit_fn: Optional event emission callback — same signature as ATM.
    """

    def __init__(
        self,
        persistent_memory: Any,  # PersistentMemory — avoid circular import
        confidence_threshold: float = MIN_CONFIDENCE,
        emit_fn: Optional[EmitFn] = None,
    ) -> None:
        self._mem = persistent_memory
        self._threshold = confidence_threshold
        self._emit = emit_fn
        # In-process recent-entry cache to avoid repeated MEMORY.md reads
        self._recent: List[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        prompt: str,
        output: str,
        confidence: float,
        source: str = "direct",
        model_used: str = "unknown",
    ) -> bool:
        """Attempt to record one learned pattern.  Returns True if recorded.

        Args:
            prompt: The original user prompt.
            output: The final assistant response.
            confidence: Quality score (0.0–1.0).
            source: One of 'atm', 'reflection', 'reasoning', 'direct'.
            model_used: Model identifier string.
        """
        try:
            # --- Guards ---
            if confidence < self._threshold:
                return False
            if not output or len(output.strip()) < MIN_OUTPUT_LEN:
                return False
            lo = output.lower().strip()
            if any(lo.startswith(m) for m in _STUB_MARKERS):
                return False

            # --- Extract pattern (compact, first substantive line/sentence) ---
            pattern = self._extract_pattern(prompt, output)
            if not pattern or len(pattern) < 20:
                return False

            # --- Dedup: skip if too similar to anything recent ---
            if self._is_near_duplicate(pattern):
                return False

            # --- Write to MEMORY.md ---
            entry = f"{LEARN_PREFIX}{pattern}  [src={source}, conf={confidence:.2f}]"
            self._mem.add("MEMORY", entry)
            self._recent.append(pattern)
            if len(self._recent) > MAX_ENTRIES:
                self._recent.pop(0)

            # --- Prune oldest learned entries if over cap ---
            self._prune()

            # --- Emit event ---
            if self._emit:
                try:
                    self._emit(
                        "AUTO_LEARN_RECORDED",
                        {
                            "entry_id": uuid.uuid4().hex[:12],
                            "source": source,
                            "confidence": round(confidence, 4),
                            "model_used": model_used,
                            "pattern_preview": pattern[:120],
                        },
                        None,
                    )
                except Exception:
                    pass

            return True

        except Exception:
            return False

    def learned_context_block(self) -> str:
        """Return a prompt-ready block of recent learned patterns (≤600 chars).

        Returns empty string if no learned entries exist.
        """
        try:
            raw = self._mem.read("MEMORY")
            entries = [line.strip() for line in raw.splitlines() if line.strip().startswith(LEARN_PREFIX)]
            if not entries:
                return ""
            # Most recent entries first, cap total chars
            block_lines: List[str] = []
            chars = 0
            for entry in reversed(entries):
                if chars + len(entry) + 1 > MAX_INJECT_CHARS:
                    break
                block_lines.append(entry)
                chars += len(entry) + 1
            if not block_lines:
                return ""
            body = "\n".join(reversed(block_lines))
            return f"## What I've Learned\n{body}"
        except Exception:
            return ""

    def reset(self) -> int:
        """Remove all ``[learned]`` entries from MEMORY.md.  Returns count removed."""
        try:
            raw = self._mem.read("MEMORY")
            lines = raw.splitlines(keepends=True)
            learned = [ln for ln in lines if LEARN_PREFIX in ln]
            for ln in learned:
                # remove just the content portion as PersistentMemory.remove()
                # matches on substring
                self._mem.remove("MEMORY", ln.strip())
            self._recent.clear()
            return len(learned)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_pattern(self, prompt: str, output: str) -> str:
        """Derive a compact, reusable insight from a prompt+output pair."""
        # Strategy: take the first substantive sentence of the output,
        # prefix with the topic from the prompt (first 60 chars).
        topic = prompt.strip()[:60].rstrip(".?,!").strip()
        # Find first sentence (ends at . ! ?)
        import re

        sentences = re.split(r"(?<=[.!?])\s+", output.strip())
        first = ""
        for s in sentences:
            s = s.strip()
            if len(s) >= 20 and not any(s.lower().startswith(m) for m in _STUB_MARKERS):
                first = s
                break
        if not first:
            first = output.strip()[:200]
        # Compact combined insight
        return f"{topic}: {first}"[:300]

    def _is_near_duplicate(self, pattern: str) -> bool:
        """True if pattern overlaps significantly with a recently recorded entry."""
        words = set(pattern.lower().split())
        if len(words) < 4:
            return False
        for recent in self._recent[-8:]:
            r_words = set(recent.lower().split())
            if len(r_words) == 0:
                continue
            overlap = len(words & r_words) / max(len(words), len(r_words))
            if overlap > 0.70:
                return True
        return False

    def _prune(self) -> None:
        """Evict oldest [learned] entries beyond MAX_ENTRIES."""
        try:
            raw = self._mem.read("MEMORY")
            learned_lines = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith(LEARN_PREFIX)]
            excess = len(learned_lines) - MAX_ENTRIES
            for i in range(excess):
                self._mem.remove("MEMORY", learned_lines[i])
        except Exception:
            pass
