from __future__ import annotations

import pytest

from andyria.tools import ToolPolicy, ToolRegistry


def test_async_call_matches_dispatch() -> None:
    registry = ToolRegistry()
    import asyncio

    value = asyncio.run(registry.call("echo", text="hello"))
    assert value == "hello"


def test_policy_denies_tool() -> None:
    registry = ToolRegistry()
    registry.set_policy(ToolPolicy(denied_tools={"echo"}))

    with pytest.raises(ValueError, match="denied by policy"):
        registry.dispatch("echo", "blocked")


def test_policy_input_size_limit() -> None:
    registry = ToolRegistry()
    registry.set_policy(ToolPolicy(max_input_chars=4))

    with pytest.raises(ValueError, match="Tool input too large"):
        registry.dispatch("echo", "12345")


def test_policy_blocks_injection_pattern() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="blocked by policy pattern"):
        registry.dispatch("echo", "hello && rm -rf /")


def test_call_rejects_unknown_params() -> None:
    registry = ToolRegistry()
    import asyncio

    with pytest.raises(ValueError, match="Unknown tool parameters"):
        asyncio.run(registry.call("echo", text="ok", foo="bar"))
