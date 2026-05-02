"""Tool registry for Andyria agents.

Built-in tools: echo, timestamp, word_count.
Custom tools can be registered via ToolRegistry.register().
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List

# Function signature: (text: str, context: dict) -> str
_ToolFn = Callable[[str, Dict[str, Any]], str]


class ToolRegistry:
    """Registry of named callable tools available to agents.

    Built-ins are registered at construction time. Additional tools can be
    registered via :meth:`register`.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, _ToolFn] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register("echo", lambda text, _ctx: text)
        self.register("timestamp", lambda _text, _ctx: str(int(time.time_ns())))
        self.register("word_count", lambda text, _ctx: str(len(text.split())))

    def register(self, name: str, fn: _ToolFn) -> None:
        """Register a callable tool by name, overwriting any prior binding."""
        self._tools[name] = fn

    def list(self) -> List[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def dispatch(
        self,
        name: str,
        text: str = "",
        context: Dict[str, Any] | None = None,
    ) -> str:
        """Call the named tool. Raises ``KeyError`` if not registered."""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered")
        return self._tools[name](text, context or {})
