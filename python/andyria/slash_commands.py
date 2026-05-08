"""Shared slash-command registry for CLI and web surfaces."""

from __future__ import annotations

from typing import List, Optional, TypedDict


class SlashCommandDef(TypedDict):
    command: str
    description: str
    arg_hint: Optional[str]
    targets: List[str]


_SLASH_COMMANDS: List[SlashCommandDef] = [
    {
        "command": "/new",
        "description": "Start a new session",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/reset",
        "description": "Clear current session history",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/model",
        "description": "Show or set active model",
        "arg_hint": "<name>",
        "targets": ["cli", "web"],
    },
    {
        "command": "/personality",
        "description": "Show SOUL profile",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/skills",
        "description": "List available skills",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/skill",
        "description": "View one skill",
        "arg_hint": "<name>",
        "targets": ["cli", "web"],
    },
    {
        "command": "/create-skill",
        "description": "Create a reusable skill scaffold",
        "arg_hint": "<name> | <description> | <tags> | <content>",
        "targets": ["cli", "web"],
    },
    {
        "command": "/memory",
        "description": "Show MEMORY and USER notes",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/todo",
        "description": "Show TODO items",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/cron",
        "description": "Show scheduled jobs",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/history",
        "description": "List prior sessions",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/resume",
        "description": "Switch to a prior session",
        "arg_hint": "<id>",
        "targets": ["cli", "web"],
    },
    {
        "command": "/session",
        "description": "Show current session info",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/help",
        "description": "Show available slash commands",
        "arg_hint": None,
        "targets": ["cli", "web"],
    },
    {
        "command": "/usage",
        "description": "Show context token estimate",
        "arg_hint": None,
        "targets": ["cli"],
    },
    {
        "command": "/compress",
        "description": "Manually compress context",
        "arg_hint": None,
        "targets": ["cli"],
    },
    {
        "command": "/exit",
        "description": "Quit session",
        "arg_hint": None,
        "targets": ["cli"],
    },
    {
        "command": "/quit",
        "description": "Quit session",
        "arg_hint": None,
        "targets": ["cli"],
    },
]


def list_slash_commands(target: str) -> List[dict[str, object]]:
    """Return commands available for a surface target (cli or web)."""
    normalized = (target or "").strip().lower() or "cli"
    return [
        {
            "cmd": str(item["command"]),
            "desc": str(item["description"]),
            "arg": item["arg_hint"],
        }
        for item in _SLASH_COMMANDS
        if normalized in item["targets"]
    ]
