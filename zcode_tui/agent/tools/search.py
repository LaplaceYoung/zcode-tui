"""Search tools: glob (file matching) and grep (content matching, rg-backed)."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import Tool, ToolContext, ToolResult, register

MAX_RESULTS = 200
ZCODE_RG = Path("/Applications/ZCode.app/Contents/Resources/tools/ripgrep")

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    ".idea", ".vscode", "dist", "build", ".next", ".cache",
}


def _rg_binary() -> str | None:
    if shutil.which("rg"):
        return "rg"
    if ZCODE_RG.exists() and os.access(ZCODE_RG, os.X_OK):
        return str(ZCODE_RG)
    return None


async def _glob(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = args.get("pattern", "*")
    base = Path(args.get("path") or ctx.cwd).expanduser()
    if not base.is_absolute():
        base = (ctx.cwd / base).resolve()
    try:
        matches = [p for p in base.glob(pattern) if p.is_file()]
    except (ValueError, OSError) as e:
        return ToolResult(f"bad pattern: {e}", is_error=True)
    matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    shown = matches[:MAX_RESULTS]
    lines = []
    for p in shown:
        try:
            lines.append(str(p.relative_to(ctx.cwd)))
        except ValueError:
            lines.append(str(p))
    suffix = f"\n... and {len(matches) - MAX_RESULTS} more" if len(matches) > MAX_RESULTS else ""
    return ToolResult("\n".join(lines) + suffix if lines else "no matches")


async def _grep_builtin(pattern: str, base: Path, glob_pat: str | None, ignore_case: bool) -> ToolResult:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return ToolResult(f"bad regex: {e}", is_error=True)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if glob_pat and not fp.match(glob_pat):
                continue
            try:
                text = fp.read_text(errors="replace")
            except (OSError, UnicodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    out.append(f"{fp}:{i}:{line.strip()[:300]}")
                    if len(out) >= MAX_RESULTS:
                        return ToolResult("\n".join(out) + "\n... (capped)")
    return ToolResult("\n".join(out) if out else "no matches")


async def _grep(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = args.get("pattern", "")
    glob_pat = args.get("glob")
    ignore_case = bool(args.get("ignore_case", False))
    base = Path(args.get("path") or ctx.cwd).expanduser()
    if not base.is_absolute():
        base = (ctx.cwd / base).resolve()
    rg = _rg_binary()
    if rg:
        cmd = [rg, "--no-heading", "--line-number", "--color", "never",
               "--max-columns", "300", "--max-count", str(MAX_RESULTS)]
        if ignore_case:
            cmd.append("-i")
        if glob_pat:
            cmd += ["-g", glob_pat]
        cmd += ["-e", pattern, str(base)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        lines = stdout.decode("utf-8", "replace").splitlines()[:MAX_RESULTS]
        # Make paths relative to cwd when possible.
        rel = []
        for line in lines:
            if line.startswith(str(ctx.cwd) + os.sep):
                line = line[len(str(ctx.cwd)) + 1:]
            rel.append(line)
        return ToolResult("\n".join(rel) if rel else "no matches")
    return await _grep_builtin(pattern, base, glob_pat, ignore_case)


register(
    Tool(
        name="glob",
        schema={
            "description": "Find files by glob pattern (supports **). Returns matches sorted by recency.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Base directory (default: cwd)"},
                },
                "required": ["pattern"],
            },
        },
        run=_glob,
        summarize=lambda a: a.get("pattern", ""),
    )
)

register(
    Tool(
        name="grep",
        schema={
            "description": "Search file contents with a regex. Returns file:line:text matches.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "File or directory (default: cwd)"},
                    "glob": {"type": "string", "description": "Limit to files matching e.g. *.py"},
                    "ignore_case": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
        run=_grep,
        summarize=lambda a: a.get("pattern", ""),
    )
)
