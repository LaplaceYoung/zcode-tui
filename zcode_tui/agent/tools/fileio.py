"""File tools: read, write, edit (exact string replacement, CC-compatible)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import Tool, ToolContext, ToolResult, register

MAX_READ_LINES = 2000
MAX_LINE_LEN = 2000


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ctx.cwd / p
    return p.resolve()


async def _read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    p = _resolve(ctx, args.get("path", ""))
    offset = max(int(args.get("offset", 1)), 1)
    limit = min(int(args.get("limit", MAX_READ_LINES)), MAX_READ_LINES)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return ToolResult(f"file not found: {p}", is_error=True)
    except IsADirectoryError:
        return ToolResult(f"is a directory: {p}", is_error=True)
    if b"\x00" in raw[:8192]:
        return ToolResult(f"refusing to read binary file: {p}", is_error=True)
    lines = raw.decode("utf-8", "replace").splitlines()
    total = len(lines)
    chunk = lines[offset - 1 : offset - 1 + limit]
    body = "\n".join(
        f"{i}: {line[:MAX_LINE_LEN]}" for i, line in enumerate(chunk, start=offset)
    )
    header = f"{p} (lines {offset}-{offset + len(chunk) - 1} of {total})"
    if offset + len(chunk) - 1 < total:
        header += f" — more lines available, use offset={offset + len(chunk)}"
    return ToolResult(f"{header}\n{body}")


async def _write(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    p = _resolve(ctx, args.get("path", ""))
    content = args.get("content", "")
    old = p.read_text(errors="replace") if p.exists() else None
    if ctx.runtime is not None:
        ctx.runtime.record_preimage(str(p), old)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    added = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    removed = (old or "").count("\n") if old is not None else 0
    return ToolResult(
        f"wrote {len(content.encode('utf-8'))} bytes to {p} (+{added} -{removed})",
        meta={"diff": {"path": str(p), "old": old, "new": content}},
    )


async def _edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    p = _resolve(ctx, args.get("path", ""))
    old_s = args.get("old_string", "")
    new_s = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if old_s == new_s:
        return ToolResult("old_string and new_string are identical", is_error=True)
    try:
        text = p.read_text()
    except FileNotFoundError:
        return ToolResult(f"file not found: {p}", is_error=True)
    if ctx.runtime is not None:
        ctx.runtime.record_preimage(str(p), text)
    count = text.count(old_s)
    if count == 0:
        return ToolResult(f"old_string not found in {p}", is_error=True)
    if count > 1 and not replace_all:
        return ToolResult(
            f"old_string occurs {count} times; pass replace_all=true or make it unique",
            is_error=True,
        )
    new_text = text.replace(old_s, new_s) if replace_all else text.replace(old_s, new_s, 1)
    p.write_text(new_text)
    return ToolResult(
        f"edited {p} ({count if replace_all else 1} replacement(s))",
        meta={"diff": {"path": str(p), "old": text, "new": new_text}},
    )


register(
    Tool(
        name="read",
        schema={
            "description": "Read a text file with line numbers. Use offset/limit to page long files.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "1-based start line (default 1)"},
                    "limit": {"type": "integer", "description": f"Max lines (default {MAX_READ_LINES})"},
                },
                "required": ["path"],
            },
        },
        run=_read,
        summarize=lambda a: a.get("path", ""),
    )
)

register(
    Tool(
        name="write",
        schema={
            "description": "Write a file, replacing any existing content. Prefer edit for modifying files.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        run=_write,
        mutating=True,
        summarize=lambda a: a.get("path", ""),
    )
)

register(
    Tool(
        name="edit",
        schema={
            "description": (
                "Replace an exact string in a file with another string. Fails if "
                "old_string is not unique unless replace_all is true."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
        run=_edit,
        mutating=True,
        summarize=lambda a: a.get("path", ""),
    )
)
