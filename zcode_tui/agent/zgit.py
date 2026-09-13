"""Read/manage git worktrees for /worktree (runs git via the permission system)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class Worktree:
    path: str
    head: str
    branch: str
    bare: bool = False
    detached: bool = False
    is_main: bool = False


async def _run_git(repo: str, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
    return proc.returncode or 0, out.decode("utf-8", "replace") + err.decode("utf-8", "replace")


async def list_worktrees(repo: str) -> tuple[list[Worktree], str]:
    """Returns (worktrees, error). Empty list with error string on failure."""
    code, out = await _run_git(repo, "worktree", "list", "--porcelain")
    if code != 0:
        return [], out.strip() or "git worktree failed"
    trees: list[Worktree] = []
    cur: dict[str, Any] = {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                trees.append(_mk(cur))
            cur = {"path": line[len("worktree "):], "head": "", "branch": "", "bare": False, "detached": False}
        elif line.startswith("HEAD "):
            cur["head"] = line[5:]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "bare":
            cur["bare"] = True
        elif line == "detached":
            cur["detached"] = True
    if cur:
        trees.append(_mk(cur))
    if trees:
        trees[0].is_main = True
    return trees, ""


def _mk(cur: dict) -> Worktree:
    from typing import Any  # noqa
    return Worktree(
        path=cur.get("path", ""), head=cur.get("head", "")[:9],
        branch=cur.get("branch") or ("(detached)" if cur.get("detached") else "(bare)" if cur.get("bare") else ""),
        bare=cur.get("bare", False), detached=cur.get("detached", False),
    )


from typing import Any  # noqa: E402


async def status_line(repo: str) -> str:
    """One-line summary: branch + dirty flag."""
    code, out = await _run_git(repo, "status", "--porcelain", "-b")
    if code != 0:
        return ""
    lines = out.splitlines()
    if not lines:
        return ""
    head = lines[0].removeprefix("## ").strip()
    dirty = sum(1 for l in lines[1:] if l.strip())
    return f"{head}" + (f" · {dirty} changed" if dirty else " · clean")
