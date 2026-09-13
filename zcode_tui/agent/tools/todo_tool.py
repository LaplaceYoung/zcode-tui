"""Todo tool: session-scoped task list the model maintains (CC TodoWrite semantics)."""

from __future__ import annotations

from typing import Any

from . import Tool, ToolContext, ToolResult, register

VALID_STATUS = {"pending", "in_progress", "completed"}
VALID_PRIORITY = {"high", "medium", "low"}


def render(todos: list[dict[str, Any]]) -> str:
    if not todos:
        return "no todos"
    mark = {"pending": "☐", "in_progress": "◐", "completed": "☒"}
    return "\n".join(
        f"{mark.get(t.get('status', 'pending'), '☐')} {t.get('content', '')}"
        for t in todos
    )


async def _run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    todos = args.get("todos", [])
    cleaned = []
    for t in todos:
        if not isinstance(t, dict) or not t.get("content"):
            continue
        cleaned.append(
            {
                "content": str(t["content"]),
                "status": t.get("status") if t.get("status") in VALID_STATUS else "pending",
                "priority": t.get("priority") if t.get("priority") in VALID_PRIORITY else "medium",
            }
        )
    in_progress = sum(1 for t in cleaned if t["status"] == "in_progress")
    if in_progress > 1:
        return ToolResult(
            "exactly one todo may be in_progress at a time", is_error=True
        )
    ctx.state["todos"] = cleaned
    return ToolResult(render(cleaned), meta={"todos": cleaned})


register(
    Tool(
        name="todo",
        schema={
            "description": (
                "Update the session task list. Use it to plan multi-step work and "
                "track progress. Exactly one item may be in_progress."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                                "priority": {"type": "string", "enum": sorted(VALID_PRIORITY)},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
        run=_run,
        summarize=lambda a: f"{len(a.get('todos', []))} items",
    )
)
