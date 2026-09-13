"""Tool registry: schemas, executors, and the context passed to them."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class ToolContext:
    cwd: Path
    state: dict[str, Any] = field(default_factory=dict)  # session-scoped (e.g. todos)
    runtime: Any = None  # the owning AgentLoop (for task/subagent tools)


@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)  # e.g. {"diff": {...}, "todos": [...]}


@dataclass
class Tool:
    name: str
    schema: dict[str, Any]
    run: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]
    mutating: bool = False
    # Short one-line summary of args for the transcript header, e.g. Bash(ls -la)
    summarize: Callable[[dict[str, Any]], str] = lambda a: ""


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def anthropic_schemas(exclude: set[str] | None = None) -> list[dict[str, Any]]:
    skip = exclude or set()
    return [
        {"name": t.name, "description": t.schema["description"], "input_schema": t.schema["input_schema"]}
        for t in REGISTRY.values()
        if "description" in t.schema and t.name not in skip
    ]


# Import side effects register the tools.
from . import bash, fileio, search, todo_tool, webfetch, task_tool  # noqa: E402,F401
