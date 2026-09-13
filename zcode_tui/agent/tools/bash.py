"""Bash tool: foreground commands, background shells, output & kill helpers."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from . import Tool, ToolContext, ToolResult, register
from ...zconfig import ZTUI_DATA_DIR

MAX_OUTPUT = 30_000
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600

SHELLS_DIR = ZTUI_DATA_DIR / "shells"


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return (
        text[:half]
        + f"\n\n... ({len(text) - MAX_OUTPUT} chars truncated) ...\n\n"
        + text[-half:]
    )


def _shells(ctx: ToolContext) -> dict[str, dict[str, Any]]:
    return ctx.state.setdefault("bg_shells", {})


async def _run_background(command: str, ctx: ToolContext) -> ToolResult:
    SHELLS_DIR.mkdir(parents=True, exist_ok=True)
    shell_id = uuid.uuid4().hex[:8]
    log_path = SHELLS_DIR / f"{shell_id}.log"
    log_file = open(log_path, "w", buffering=1)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ctx.cwd),
            executable="/bin/zsh",
        )
    except OSError as e:
        log_file.close()
        return ToolResult(f"failed to start: {e}", is_error=True)
    _shells(ctx)[shell_id] = {
        "proc": proc,
        "log_fh": log_file,
        "log": str(log_path),
        "command": command,
        "started": time.time(),
        "cwd": str(ctx.cwd),
    }
    return ToolResult(
        f"background shell {shell_id} started: {command}\n"
        f"output: {log_path} — check with shell_output(id=\"{shell_id}\")"
    )


async def _run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = args.get("command", "")
    if args.get("run_in_background"):
        return await _run_background(command, ctx)
    timeout = min(int(args.get("timeout", DEFAULT_TIMEOUT_S * 1000)) / 1000, MAX_TIMEOUT_S)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ctx.cwd),
            executable="/bin/zsh",
        )
    except OSError as e:
        return ToolResult(f"failed to start: {e}", is_error=True)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult(f"command timed out after {int(timeout)}s", is_error=True)
    out = stdout.decode("utf-8", "replace")
    err = stderr.decode("utf-8", "replace")
    parts = []
    if out:
        parts.append(out.rstrip("\n"))
    if err:
        parts.append(("STDERR:\n" + err.rstrip("\n")))
    if proc.returncode:
        parts.append(f"(exit code {proc.returncode})")
    text = _truncate("\n".join(parts)) if parts else "(no output)"
    return ToolResult(
        text,
        is_error=proc.returncode != 0,
        meta={"exit_code": proc.returncode},
    )


async def _output(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    shell_id = str(args.get("id", ""))
    shell = _shells(ctx).get(shell_id)
    if shell is None:
        return ToolResult(f"unknown shell {shell_id}", is_error=True)
    proc = shell["proc"]
    running = proc.returncode is None
    try:
        text = open(shell["log"], errors="replace").read()
    except OSError as e:
        return ToolResult(f"cannot read log: {e}", is_error=True)
    since = time.time() - shell["started"]
    status = f"running ({since:.0f}s)" if running else f"exited with code {proc.returncode}"
    body = _truncate(text.rstrip("\n")) if text.strip() else "(no output yet)"
    return ToolResult(f"shell {shell_id} [{status}]: {shell['command']}\n\n{body}")


async def _kill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    shell_id = str(args.get("id", ""))
    shell = _shells(ctx).get(shell_id)
    if shell is None:
        return ToolResult(f"unknown shell {shell_id}", is_error=True)
    proc = shell["proc"]
    if proc.returncode is not None:
        return ToolResult(f"shell {shell_id} already exited with code {proc.returncode}")
    proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass
    fh = shell.get("log_fh")
    if fh:
        try:
            fh.close()
        except OSError:
            pass
    return ToolResult(f"shell {shell_id} killed")


register(
    Tool(
        name="bash",
        schema={
            "description": (
                "Run a shell command in the workspace and return its stdout/stderr. "
                "Use for builds, tests, git, and inspecting the system. Prefer "
                "dedicated tools (read/glob/grep) over cat/find/grep when they fit. "
                "For long-running jobs (dev servers, watch tasks) set run_in_background."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run (zsh)."},
                    "timeout": {
                        "type": "integer",
                        "description": f"Timeout in milliseconds (default {DEFAULT_TIMEOUT_S*1000}, max {MAX_TIMEOUT_S*1000}).",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "Run detached, logging to a file; returns a shell id.",
                    },
                },
                "required": ["command"],
            },
        },
        run=_run,
        mutating=False,  # decided per-command by the permission layer
        summarize=lambda a: a.get("command", "")[:80],
    )
)

register(
    Tool(
        name="shell_output",
        schema={
            "description": "Read the accumulated output of a background shell started by bash.",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Shell id from bash(run_in_background)"}},
                "required": ["id"],
            },
        },
        run=_output,
        summarize=lambda a: str(a.get("id", "")),
    )
)

register(
    Tool(
        name="shell_kill",
        schema={
            "description": "Kill a running background shell by id.",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        run=_kill,
        mutating=True,
        summarize=lambda a: str(a.get("id", "")),
    )
)
