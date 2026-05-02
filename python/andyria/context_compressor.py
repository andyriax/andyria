"""Context compression — mirrors hermes-agent's context window management.

When the accumulated conversation approaches the model's context limit,
this module summarises the middle portion of the conversation while
protecting the system prompt, the most recent turns, and all tool
call/result pairs (which must stay contiguous).

Strategy:
    1. Estimate current token count (4 chars ≈ 1 token heuristic).
    2. If usage < ``trigger_ratio`` of ``max_tokens``, do nothing.
    3. Flush any pending memory/facts to persistent storage (caller hook).
    4. Identify the "compressible window" (exclude first N system turns
       and last ``keep_recent`` turns).
    5. Summarise the middle turns via the model router.
    6. Replace the middle turns with a single summary assistant turn.
    7. Return the compressed messages list.

Usage::

    compressor = ContextCompressor(max_tokens=8192, keep_recent=20)
    if compressor.needs_compression(messages):
        messages = await compressor.compress(messages, summarise_fn)
"""

from __future__ import annotations

import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

_CHARS_PER_TOKEN = 4     # rough heuristic
_DEFAULT_TRIGGER   = 0.50  # compress when 50 % of context used
_DEFAULT_MAX_TOK   = 8192
_DEFAULT_KEEP      = 20    # keep this many recent turns untouched


def _token_estimate(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        total += len(str(m.get("content") or "")) // _CHARS_PER_TOKEN
    return total


def _is_tool_message(m: Dict[str, Any]) -> bool:
    return m.get("role") in ("tool", "function") or bool(m.get("tool_call_id"))


def _is_tool_call(m: Dict[str, Any]) -> bool:
    return bool(m.get("tool_calls"))


def _find_compressible_span(
    messages: List[Dict[str, Any]],
    keep_recent: int,
) -> tuple[int, int]:
    """Return (start, end) indices of the compressible middle section.

    Excludes:
        * The first message (usually system prompt).
        * The last ``keep_recent`` messages.
        * Any tool call + its paired result (keeps pairs together).
    """
    if len(messages) <= keep_recent + 2:
        return (0, 0)   # nothing to compress

    start = 1           # skip system prompt
    end = max(start, len(messages) - keep_recent)

    # Walk backwards from ``end`` to ensure we don't split a tool pair
    while end > start:
        m = messages[end - 1]
        if _is_tool_message(m) or _is_tool_call(m):
            end -= 1
        else:
            break

    if end <= start:
        return (0, 0)

    return (start, end)


class ContextCompressor:
    """Stateless context window compressor.

    Args:
        max_tokens:    Estimated context window size of the model.
        trigger_ratio: Compress when token usage exceeds this fraction.
        keep_recent:   Number of recent turns to leave untouched.
    """

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOK,
        trigger_ratio: float = _DEFAULT_TRIGGER,
        keep_recent: int = _DEFAULT_KEEP,
    ) -> None:
        self.max_tokens = max_tokens
        self.trigger_ratio = trigger_ratio
        self.keep_recent = keep_recent

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def needs_compression(self, messages: List[Dict[str, Any]]) -> bool:
        """True if current token estimate exceeds the trigger threshold."""
        used = _token_estimate(messages)
        return used >= int(self.max_tokens * self.trigger_ratio)

    def token_usage(self, messages: List[Dict[str, Any]]) -> dict:
        """Return token usage stats."""
        used = _token_estimate(messages)
        return {
            "estimated_tokens": used,
            "max_tokens": self.max_tokens,
            "pct_used": round(used / self.max_tokens * 100, 1),
            "needs_compression": used >= int(self.max_tokens * self.trigger_ratio),
        }

    async def compress(
        self,
        messages: List[Dict[str, Any]],
        summarise_fn: Callable[[str], Awaitable[str]],
        on_flush: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
    ) -> List[Dict[str, Any]]:
        """Compress messages in-place.

        Args:
            messages:     The full messages list (mutated in-place).
            summarise_fn: Async callable that takes a text blob and returns
                          a summary string.
            on_flush:     Optional async callback called with the about-to-be-
                          compressed turns before they are replaced.  Use this
                          to persist facts to MEMORY.md.

        Returns the compressed messages list (same object).
        """
        start, end = _find_compressible_span(messages, self.keep_recent)
        if end <= start:
            return messages

        middle = messages[start:end]

        # Optional flush hook (e.g. save facts to MEMORY.md)
        if on_flush:
            await on_flush(middle)

        # Build text for summarisation
        text_parts = []
        for m in middle:
            role = m.get("role", "?")
            content = str(m.get("content") or "")
            if not content.strip():
                continue
            text_parts.append(f"[{role.upper()}]: {content}")
        combined = "\n\n".join(text_parts)

        if not combined.strip():
            messages[start:end] = []
            return messages

        prompt = (
            "The following is a segment of a conversation that needs to be compressed. "
            "Write a dense factual summary in third-person that preserves all important "
            "decisions, facts, code snippets, and context. Be concise but complete.\n\n"
            + combined
        )

        try:
            summary = await summarise_fn(prompt)
        except Exception as exc:
            summary = f"[Context summary unavailable: {exc}]\n\nOriginal segment contained {len(middle)} turns."

        summary_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": f"[CONTEXT SUMMARY — {len(middle)} turns compressed]\n\n{summary}",
            "_compressed": True,
            "_compressed_at": time.time(),
            "_original_turn_count": len(middle),
        }

        messages[start:end] = [summary_msg]
        return messages

    def compress_sync(
        self,
        messages: List[Dict[str, Any]],
        summarise_fn: Callable[[str], str],
        on_flush: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Synchronous variant of :meth:`compress`."""
        start, end = _find_compressible_span(messages, self.keep_recent)
        if end <= start:
            return messages

        middle = messages[start:end]

        if on_flush:
            on_flush(middle)

        text_parts = []
        for m in middle:
            role = m.get("role", "?")
            content = str(m.get("content") or "")
            if content.strip():
                text_parts.append(f"[{role.upper()}]: {content}")
        combined = "\n\n".join(text_parts)

        if not combined.strip():
            messages[start:end] = []
            return messages

        prompt = (
            "Compress the following conversation segment into a dense factual summary "
            "that preserves all decisions, facts, code, and context:\n\n" + combined
        )

        try:
            summary = summarise_fn(prompt)
        except Exception as exc:
            summary = f"[summary unavailable: {exc}]"

        messages[start:end] = [{
            "role": "assistant",
            "content": f"[CONTEXT SUMMARY — {len(middle)} turns compressed]\n\n{summary}",
            "_compressed": True,
            "_compressed_at": time.time(),
        }]
        return messages
