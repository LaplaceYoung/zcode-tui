"""dwf workflow runner for ztui.

Reuses the exact runtime the ZCode GUI uses: the generated launcher
(`.zcode/workflow-runs/dwfrun-*.mjs`) embeds a sandboxed VM + a JSON-line
driver protocol on stdio:

  child stdout → {kind: "create-actor"|"request"|"event"|"complete", ...}
  parent stdin ← {kind: "response", id, ok, value|error}

ztui acts as the driver: `ask` frames are executed by a ztui AgentLoop
(persona = the actor's persona prompt), `world-read` runs whitelisted
local commands via the permission layer, and progress is surfaced live in
the TUI (nested lines + notices).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

ELECTRON = "/Applications/ZCode.app/Contents/MacOS/ZCode"


@dataclass
class Actor:
    local_id: str
    name: str
    persona: str


@dataclass
class WorkflowRun:
    script: str
    proc: asyncio.subprocess.Process | None = None
    status: str = "idle"  # running|complete|error|cancelled
    value: Any = None
    error: str = ""
    actors: dict[str, Actor] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


class WorkflowRunner:
    """Spawn the launcher, drive the stdio protocol, surface progress.

    `on_event(kind, payload)` receives: ask_begin/ask_end (live blocks),
    phase/log, complete/error — for the TUI to render.
    `ask_executor(instructions, persona)` must return the final text.
    """

    def __init__(
        self,
        script: str,
        ask_executor: Callable[[str, str | None], Awaitable[str]],
        on_event: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self.script = script
        self.ask_executor = ask_executor
        self.on_event = on_event or self._noop
        self.run = WorkflowRun(script=script)
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    async def _noop(kind: str, payload: dict) -> None: ...

    def _node_cmd(self) -> list[str] | None:
        node = shutil.which("node")
        if node:
            return [node]
        if os.path.exists(ELECTRON):
            return [ELECTRON]
        return None

    async def execute(self) -> WorkflowRun:
        node = self._node_cmd()
        if not node:
            self.run.status = "error"
            self.run.error = "no node executable found (need node or ZCode.app)"
            await self.on_event("error", {"message": self.run.error})
            return self.run
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env.setdefault("NO_COLOR", "1")
        self._proc = await asyncio.create_subprocess_exec(
            *node, self.script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self.run.proc = self._proc
        self.run.status = "running"
        assert self._proc.stdin and self._proc.stdout
        stdin, stdout = self._proc.stdin, self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                try:
                    frame = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                await self._dispatch(frame, stdin)
                if self.run.status in ("complete", "error", "cancelled"):
                    break
        finally:
            if self._proc.returncode is None:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except Exception:
                    self._proc.kill()
        return self.run

    def cancel(self) -> None:
        self.run.status = "cancelled"
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    async def _respond(self, rid: str, ok: bool, value: Any = None, error: dict | None = None) -> None:
        if not self._proc or not self._proc.stdin:
            return
        frame: dict[str, Any] = {"kind": "response", "id": rid, "ok": ok}
        if ok:
            frame["value"] = value
        else:
            frame["error"] = error or {"message": "driver error"}
        self._proc.stdin.write((json.dumps(frame) + "\n").encode())
        await self._proc.stdin.drain()

    async def _dispatch(self, frame: dict, stdin) -> None:
        kind = frame.get("kind")
        self.run.events.append(frame)
        await self.on_event(kind, frame)
        if kind == "create-actor":
            self.run.actors[frame.get("localId", "")] = Actor(
                local_id=frame.get("localId", ""),
                name=frame.get("name", ""),
                persona=frame.get("persona", ""),
            )
        elif kind == "request":
            rid = frame.get("id", "")
            rtype = frame.get("type")
            try:
                if rtype == "ask":
                    actor = self.run.actors.get(frame.get("actor", ""))
                    persona = actor.persona if actor else None
                    text = await self.ask_executor(frame.get("instructions", ""), persona)
                    await self._respond(rid, True, text)
                elif rtype == "world-read":
                    value = await self._world_read(frame)
                    await self._respond(rid, True, value)
                elif rtype == "publish-artifact":
                    # v1: acknowledged, not persisted (GUI stores artifacts server-side).
                    await self._respond(rid, True, None)
                else:
                    await self._respond(rid, False, error={"message": f"unsupported request type {rtype}"})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._respond(rid, False, error={"message": str(e)[:500]})
        elif kind == "complete":
            if frame.get("ok"):
                self.run.status = "complete"
                self.run.value = frame.get("value")
            else:
                err = frame.get("error", {}) or {}
                self.run.status = "error"
                self.run.error = err.get("message", "workflow failed")
            await self.on_event("done", {"status": self.run.status, "value": self.run.value, "error": self.run.error})
        elif kind == "event":
            pass  # already surfaced via on_event above

    async def _world_read(self, frame: dict) -> dict[str, Any]:
        args = frame.get("args", [])
        if not isinstance(args, list) or not args:
            raise ValueError("world-read args malformed")
        cmd = str(args[0])
        cmd_args = [str(a) for a in (args[1] if isinstance(args[1], list) else [args[1]])]
        # Only read-only-looking commands are auto-allowed.
        allowed = {"echo", "wc", "cat", "head", "tail", "ls", "find", "grep", "stat", "pwd", "date"}
        if cmd not in allowed:
            raise ValueError(f"world-read: command {cmd!r} not allowed (read-only ops only)")
        proc = await asyncio.create_subprocess_exec(
            cmd, *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {
            "exitCode": proc.returncode or 0,
            "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace"),
        }
