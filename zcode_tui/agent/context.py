"""System prompt assembly: identity, environment, instructions, memory index."""

from __future__ import annotations

import hashlib
import os
import platform
from datetime import datetime
from pathlib import Path

MAX_INSTRUCTION_BYTES = 100 * 1024
USER_INSTRUCTIONS = Path.home() / ".zcode" / "AGENTS.md"
MEMORY_ROOT = Path.home() / ".zcode" / "cli" / "memories" / "projects"

BASE_PROMPT = """You are ztui, an agentic coding assistant running in the user's terminal.

You help with software engineering tasks: exploring code, making edits, running
commands, debugging, and explaining. You operate through tools — use them
liberally to ground your answers in the actual workspace instead of guessing.

Style:
- Be direct and concise. Short answers for short questions.
- Before a mutating action (edit/write/bash with side effects), one short
  sentence about what you are about to do is enough; never narrate your own
  process at length.
- When you finish, summarize what changed in a sentence or two, with concrete
  file paths. No grandiose closers.
- Refuse destructive commands (rm -rf, dd, wiping data) unless the user
  explicitly asked and you confirmed the target.
- If a tool result contradicts your expectation, trust the tool result.
"""

PLAN_PROMPT = """# Plan mode
You are in plan mode: the user wants a proposal, not changes. You may use
read-only tools (read/glob/grep and read-only bash like ls/git status) to
research, but the system will DENY any mutating tool call. Do not attempt
edits, writes, or state-changing bash commands. End with a concrete,
step-by-step implementation plan.
"""

GOAL_TEMPLATE = """# Active goal
{goal}

Work toward this goal step by step. Every action and answer should serve it;
when it is fully met, summarize the outcome plainly. The goal was set by the
user via /goal and persists until they clear it.
"""

MEMORY_PROMPT = """# Memory
A persistent memory directory for this project exists at {memdir}/.
{index_block}
Conventions: MEMORY.md is the always-loaded index; topic memories live as
individual .md files next to it. When you learn something durable about the
user, their preferences, or this project that is NOT derivable from the repo,
save it as a topic file and add a one-line pointer in MEMORY.md. Do not store
anything obvious from the code or git history. Use your write/edit tools to
maintain these files.
"""


def _read_capped(path: Path) -> str | None:
    try:
        data = path.read_bytes()[:MAX_INSTRUCTION_BYTES]
        return data.decode("utf-8", "replace")
    except OSError:
        return None


def _memory_dir(cwd: Path) -> Path:
    digest = hashlib.sha256(str(cwd).encode()).hexdigest()[:16]
    return MEMORY_ROOT / f"{cwd.name}-{digest}" / "memory"


def _instruction_chain(cwd: Path) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if (text := _read_capped(USER_INSTRUCTIONS)) is not None:
        out.append((USER_INSTRUCTIONS, text))
    # Project files: walk from cwd up to home (or filesystem root), closest last.
    chain: list[Path] = []
    d = cwd.resolve()
    home = Path.home()
    while True:
        candidate = d / "AGENTS.md"
        if candidate.exists():
            chain.append(candidate)
        if d == home or d.parent == d:
            break
        d = d.parent
    for p in reversed(chain):
        if (text := _read_capped(p)) is not None:
            out.append((p, text))
    return out


def instruction_chain_text(cwd: Path) -> str | None:
    """Rendered AGENTS.md instruction block (user + project chain), or None."""
    instructions = _instruction_chain(cwd)
    if not instructions:
        return None
    blocks = [f"Contents of {path}:\n\n{text}" for path, text in instructions]
    return "# Instructions\n\n" + "\n\n".join(blocks)


def build_system_prompt(cwd: Path, mode: str, goal: str | None = None) -> str:
    # Cache: instruction files only change on write; skip re-reading per call.
    sources: list[tuple[str, float]] = []
    for p in _candidate_files(cwd):
        try:
            sources.append((str(p), os.path.getmtime(p)))
        except OSError:
            pass
    memdir = _memory_dir(cwd)
    mem_index = memdir / "MEMORY.md"
    try:
        sources.append((str(mem_index), os.path.getmtime(mem_index)))
    except OSError:
        pass
    key = (str(cwd), mode, goal, tuple(sources))
    if key == build_system_prompt._last and build_system_prompt._last_val:
        return build_system_prompt._last_val
    val = _build(cwd, mode, goal)
    build_system_prompt._last = key
    build_system_prompt._last_val = val
    return val


build_system_prompt._last = None  # type: ignore[attr-defined]
build_system_prompt._last_val = ""  # type: ignore[attr-defined]


def _candidate_files(cwd: Path) -> list[Path]:
    out = [USER_INSTRUCTIONS]
    d = cwd.resolve()
    home = Path.home()
    while True:
        out.append(d / "AGENTS.md")
        if d == home or d.parent == d:
            break
        d = d.parent
    memdir = _memory_dir(cwd)
    out.append(memdir / "MEMORY.md")
    return out


def _build(cwd: Path, mode: str, goal: str | None) -> str:
    parts = [BASE_PROMPT]
    if mode == "plan":
        parts.append(PLAN_PROMPT)
    if goal:
        parts.append(GOAL_TEMPLATE.format(goal=goal))
    env = (
        "# Environment\n"
        f"- Platform: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- Shell: /bin/zsh\n"
        f"- Working directory: {cwd}\n"
        f"- Date: {datetime.now():%Y-%m-%d %H:%M %Z}\n"
    )
    parts.append(env)
    instructions = _instruction_chain(cwd)
    if instructions:
        blocks = [
            f"Contents of {path}:\n\n{text}" for path, text in instructions
        ]
        parts.append("# Instructions\n\n" + "\n\n".join(blocks))
    memdir = _memory_dir(cwd)
    index = _read_capped(memdir / "MEMORY.md")
    index_block = (
        f"Current MEMORY.md index:\n\n{index}"
        if index
        else "The index is empty for now."
    )
    parts.append(MEMORY_PROMPT.format(memdir=memdir, index_block=index_block))
    return "\n\n".join(parts)

