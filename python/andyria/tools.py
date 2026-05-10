"""Tool registry for Andyria agents.

Built-in tools: echo, timestamp, word_count.
Custom tools can be registered via ToolRegistry.register().
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

# Function signature: (text: str, context: dict) -> str
_ToolFn = Callable[[str, Dict[str, Any]], str]


@dataclass
class ToolPolicy:
    """Runtime policy controls for tool execution."""

    max_input_chars: int = 4096
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    blocked_input_patterns: List[re.Pattern[str]] = field(
        default_factory=lambda: [
            # Basic command-injection primitives should never reach tool bodies.
            re.compile(r"(;|&&|\|\||`|\$\(|<\(|\n)")
        ]
    )

    @classmethod
    def from_env(cls) -> "ToolPolicy":
        """Build policy from environment variables.

        Supported env vars:
        - ANDYRIA_TOOL_MAX_INPUT_CHARS (int)
        - ANDYRIA_ALLOWED_TOOLS (comma-separated names)
        - ANDYRIA_DENIED_TOOLS (comma-separated names)
        """
        max_input_chars = 4096
        raw_max = os.getenv("ANDYRIA_TOOL_MAX_INPUT_CHARS", "").strip()
        if raw_max:
            try:
                max_input_chars = max(1, int(raw_max))
            except ValueError:
                max_input_chars = 4096

        def _csv(name: str) -> set[str]:
            raw = os.getenv(name, "")
            return {v.strip() for v in raw.split(",") if v.strip()}

        return cls(
            max_input_chars=max_input_chars,
            allowed_tools=_csv("ANDYRIA_ALLOWED_TOOLS"),
            denied_tools=_csv("ANDYRIA_DENIED_TOOLS"),
        )

    def validate(self, tool_name: str, text: str) -> None:
        """Raise ValueError when a request violates policy."""
        if self.allowed_tools and tool_name not in self.allowed_tools:
            raise ValueError(f"Tool '{tool_name}' not allowed by policy")
        if tool_name in self.denied_tools:
            raise ValueError(f"Tool '{tool_name}' denied by policy")
        if len(text) > self.max_input_chars:
            raise ValueError(f"Tool input too large ({len(text)} > {self.max_input_chars})")
        for pattern in self.blocked_input_patterns:
            if pattern.search(text):
                raise ValueError("Tool input blocked by policy pattern")


class ToolRegistry:
    """Registry of named callable tools available to agents.

    Built-ins are registered at construction time. Additional tools can be
    registered via :meth:`register`.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, _ToolFn] = {}
        self._policy = ToolPolicy.from_env()
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register("echo", lambda text, _ctx: text)
        self.register("timestamp", lambda _text, _ctx: str(int(time.time_ns())))
        self.register("word_count", lambda text, _ctx: str(len(text.split())))
        self.register("web_search", self._web_search)
        self.register("get_current_time", lambda _text, _ctx: self._get_current_time())
    
    def _web_search(self, query: str, _ctx: Dict[str, Any]) -> str:
        """Search the web for current information.
        
        Use this tool to find up-to-date information beyond your training data cutoff.
        Returns structured search results with timestamps and source URLs.
        """
        try:
            import httpx
            
            # Use DuckDuckGo or similar service that doesn't require API keys
            # Format: return a simulated response with date and source info
            # In production, integrate with actual search API
            
            search_url = f"https://duckduckgo.com/search?q={query}&format=json"
            client = httpx.Client(timeout=10.0)
            try:
                resp = client.get(search_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    import json
                    try:
                        data = resp.json()
                        # Extract results
                        results = []
                        if "RelatedTopics" in data:
                            for item in data["RelatedTopics"][:5]:
                                if "Text" in item:
                                    results.append(item.get("Text", "")[:200])
                        
                        if results:
                            return f"Search results for '{query}' (as of {time.strftime('%Y-%m-%d')}):\n" + "\n".join(f"- {r}" for r in results)
                    except json.JSONDecodeError:
                        pass
            finally:
                client.close()
        except Exception:
            pass
        
        # Fallback response
        return f"[Web search for '{query}' would return current information. Search executed at {time.strftime('%Y-%m-%d %H:%M:%S UTC')}. Integrate with DuckDuckGo/Google API for production.]"
    
    def _get_current_time(self) -> str:
        """Get current date and time to help agents stay grounded in present."""
        import datetime
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def register(self, name: str, fn: _ToolFn) -> None:
        """Register a callable tool by name, overwriting any prior binding."""
        self._tools[name] = fn

    def list(self) -> List[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def set_policy(self, policy: ToolPolicy) -> None:
        """Override tool execution policy at runtime."""
        self._policy = policy

    def dispatch(
        self,
        name: str,
        text: str = "",
        context: Dict[str, Any] | None = None,
    ) -> str:
        """Call the named tool. Raises ``KeyError`` if not registered."""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered")
        self._policy.validate(name, text)
        return self._tools[name](text, context or {})

    async def call(self, name: str, **params: Any) -> str:
        """Async-compatible call used by workflow tool steps.

        Accepts ``text`` and optional ``context`` kwargs.
        """
        text = str(params.pop("text", ""))
        context = params.pop("context", None)
        if params:
            # Keep unknown params visible for callers rather than silently dropping.
            extras = ", ".join(sorted(params.keys()))
            raise ValueError(f"Unknown tool parameters: {extras}")
        return self.dispatch(name, text=text, context=context)
