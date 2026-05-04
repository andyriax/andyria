# Sovereign Governance Baseline

This baseline makes Andyria safer to run outside centralized provider control by
enforcing policy at tool execution boundaries.

## Why This Matters

Tool execution is where an agent can cause real-world side effects. If you keep
tool calls bounded, auditable, and policy-checked, the system can evolve while
staying controllable.

## Runtime Controls

Andyria now supports policy checks in the tool registry with:

- Allow-list controls (only approved tools can run)
- Deny-list controls (explicitly blocked tools)
- Input length limits to contain prompt/tool payload blast radius
- Blocking patterns for common injection primitives

These checks are applied before tool code is executed.

## Environment Variables

Use these env vars to define policy without code changes:

- ANDYRIA_TOOL_MAX_INPUT_CHARS
- ANDYRIA_ALLOWED_TOOLS
- ANDYRIA_DENIED_TOOLS

Example:

```bash
export ANDYRIA_TOOL_MAX_INPUT_CHARS=2048
export ANDYRIA_ALLOWED_TOOLS=echo,timestamp
export ANDYRIA_DENIED_TOOLS=shell_exec
```

## Programmatic Policy (Python)

```python
from andyria.tools import ToolPolicy

coord._tools.set_policy(
    ToolPolicy(
        max_input_chars=2048,
        allowed_tools={"echo", "timestamp"},
        denied_tools={"shell_exec"},
    )
)
```

## Rollout Strategy

1. Start with observability and conservative limits.
2. Move to allow-list mode for production deployments.
3. Keep denied tools explicit and version-controlled.
4. Review policy drift during release reviews.

## Next Hardening Steps

1. Add per-agent tool policy scopes.
2. Add signed policy snapshots to the event ledger.
3. Add policy violation metrics and alerting.
4. Add a policy simulation mode for CI.
