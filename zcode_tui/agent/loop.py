"""The agent loop: stream → tool calls → permission → results → repeat."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Coroutine

from ..providers import anthropic, openai_compat
from ..zconfig import ModelInfo, Provider, ZConfig
from . import permission
from .context import build_system_prompt
from .session import Session
from .tools import REGISTRY, ToolContext, ToolResult, anthropic_schemas
from .usage import Usage

MAX_STEPS = 100

Callback = Callable[..., Coroutine[Any, Any, None]]


class AgentCallbacks:
    """UI-agnostic hooks; the TUI and the print-mode CLI subclass this."""

    async def on_text_delta(self, text: str) -> None: ...
    async def on_block_start(self, kind: str) -> None: ...
    async def on_block_end(self) -> None: ...
    async def on_reasoning_delta(self, text: str) -> None: ...
    async def on_tool_begin(self, tool_id: str, name: str, args: dict) -> None: ...
    async def on_tool_end(self, tool_id: str, name: str, result: ToolResult, note: str = "") -> None: ...
    async def on_permission_ask(self, name: str, args: dict, reason: str) -> str:
        """Return 'once' | 'always' | 'no'."""
        return "once"
    async def on_error(self, message: str) -> None: ...
    async def on_usage(self, usage: dict) -> None: ...
    async def on_compaction(self, info: dict) -> None: ...
    async def on_sub_progress(self, tool_id: str, sub_id: str, status: str, text: str) -> None:
        """Nested sub-agent progress: status is running|done|error."""
        ...
    async def on_step_finish(self, stop_reason: str) -> None: ...
    async def on_turn_finish(self) -> None: ...


def _stream_for(provider: Provider):
    if provider.kind == "anthropic":
        return anthropic.stream_chat
    return openai_compat.stream_chat


class AgentLoop:
    def __init__(
        self,
        zconfig: ZConfig,
        provider: Provider,
        model: ModelInfo,
        level: str | None,
        cwd: Path,
        callbacks: AgentCallbacks,
        session: Session | None = None,
        mode: str = "build",
        excluded_tools: set[str] | None = None,
        max_steps: int = MAX_STEPS,
        persona_prompt: str | None = None,
    ) -> None:
        self.zconfig = zconfig
        self.provider = provider
        self.model = model
        self.level = level
        self.cwd = cwd
        self.cb = callbacks
        self.session = session
        self.mode = mode
        self.excluded_tools = excluded_tools or set()
        self.max_steps = max_steps
        self.persona_prompt = persona_prompt
        self.messages: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {}
        self.usage = Usage()
        self._cancelled = False
        self._subagents: list["AgentLoop"] = []
        self.metrics: dict[str, Any] = {
            "ttft_s": None, "tok_per_s": 0.0, "cache_ratio": 0.0,
            "cache_read": 0, "ctx_used": 0, "calls": 0,
        }

    # -- control -------------------------------------------------------------

    def cancel(self) -> None:
        self._cancelled = True
        self.stop_all_subagents()

    def register_subagent(self, child_loop: "AgentLoop") -> None:
        self._subagents.append(child_loop)

    def stop_all_subagents(self) -> int:
        stopped = 0
        for child in list(self._subagents):
            if not child._cancelled:
                child.cancel()
                stopped += 1
        self._subagents.clear()
        return stopped

    def set_target(self, provider: Provider, model: ModelInfo, level: str | None) -> None:
        self.provider, self.model, self.level = provider, model, level

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    # -- history -------------------------------------------------------------

    def load_history(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            if ev.get("type") == "message":
                content = ev.get("content")
                role = ev.get("role")
                if role in ("user", "assistant") and isinstance(content, list):
                    self.messages.append({"role": role, "content": content})

    # -- main loop -----------------------------------------------------------

    # -- main loop -----------------------------------------------------------

    async def user_turn(self, text: str | list) -> None:
        self._cancelled = False
        if isinstance(text, str):
            content: list = [{"type": "text", "text": text}]
            title = text.splitlines()[0][:80]
        else:
            content = text
            title = next(
                (b.get("text", "")[:80] for b in content if b.get("type") == "text"),
                "(attachment)",
            )
        if not self.messages and self.session:
            self.session.touch_title(title)
        self._undo_turn_start = len(self.messages)
        self._push_message("user", content)
        steps = 0
        try:
            while steps < self.max_steps:
                steps += 1
                if self._cancelled:
                    break
                stop = await self._run_model_once()
                if self._cancelled:
                    break
                if stop != "tool_use":
                    break
                had_calls = await self._run_tool_step()
                if not had_calls:
                    break
        except asyncio.CancelledError:
            self._cancelled = True
        finally:
            await self.cb.on_turn_finish()

    # -- undo snapshots ------------------------------------------------------

    def record_preimage(self, path: str, old_content: str | None) -> None:
        """Tools call before mutating a file; first pre-image per (turn, path) wins."""
        if not hasattr(self, "_undo_images"):
            self._undo_images: list[tuple[int, str, str | None]] = []
        turn = getattr(self, "_undo_turn_start", len(self.messages))
        for t, p, _ in self._undo_images:
            if t == turn and p == path:
                return
        self._undo_images.append((turn, path, old_content))

    def undo_last_turn(self) -> dict | None:
        """Restore file pre-images and drop the last user turn. Returns stats."""
        if not getattr(self, "_undo_images", None) and not self.messages:
            return None
        # Find the last plain user text message index.
        cut = None
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.get("role") == "user" and any(
                isinstance(b, dict) and b.get("type") == "text" for b in (m.get("content") or [])
            ):
                cut = i
                break
        restored = 0
        images = getattr(self, "_undo_images", [])
        if cut is not None:
            kept, dropped = [], []
            for turn, path, old in images:
                (kept if turn < cut else dropped).append((turn, path, old))
            for _t, path, old in dropped:
                try:
                    if old is None:
                        Path(path).unlink(missing_ok=True)
                    else:
                        Path(path).write_text(old)
                    restored += 1
                except OSError:
                    pass
            self._undo_images = kept
            removed = len(self.messages) - cut
            self.messages = self.messages[:cut]
        else:
            removed = 0
        if self.session:
            self.session.append({"type": "undo", "restored": restored, "removed": removed})
        return {"restored": restored, "removed": removed}

    def _push_message(self, role: str, content: list[dict[str, Any]]) -> None:
        self.messages.append({"role": role, "content": content})
        if self.session:
            self.session.append({"type": "message", "role": role, "content": content})

    async def force_compact(self) -> dict | None:
        from .compaction import compact

        return await compact(self, force=True)

    async def _run_model_once(self) -> str:
        from .compaction import compact, should_compact

        if should_compact(self):
            info = await compact(self)
            if info:
                await self.cb.on_compaction(info)
        system = build_system_prompt(self.cwd, self.mode, self.state.get("goal"))
        if self.persona_prompt:
            system = f"{self.persona_prompt}\n\n{system}"
        stream = _stream_for(self.provider)(
            self.provider, self.model, self.level, system,
            self.messages, anthropic_schemas(exclude=self.excluded_tools),
        )
        blocks: list[dict[str, Any]] = []
        cur: dict[str, Any] | None = None
        stop_reason = "end_turn"
        monotonic = __import__("time").monotonic
        call_started = monotonic()
        first_token_at: float | None = None
        last_usage: dict[str, int] = {}

        def _flush() -> None:
            nonlocal cur
            if cur is None:
                return
            if cur["type"] == "tool_use":
                raw = cur.pop("input_raw", "")
                try:
                    cur["input"] = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    cur["input"] = {}
            blocks.append(cur)
            cur = None

        # Delta batching: coalesce per-token callbacks into ≤12/s UI updates.
        text_buf: list[str] = []
        think_buf: list[str] = []
        last_flush = monotonic()

        async def _flush_batch() -> None:
            nonlocal last_flush
            if text_buf:
                await self.cb.on_text_delta("".join(text_buf))
                text_buf.clear()
            if think_buf:
                await self.cb.on_reasoning_delta("".join(think_buf))
                think_buf.clear()
            last_flush = monotonic()

        async for ev in stream:
            if self._cancelled:
                break
            t = ev.get("type")
            if first_token_at is None and t in (
                "text_delta", "reasoning_delta",
                "tool_use_start",
            ):
                first_token_at = monotonic()
                self.metrics["ttft_s"] = round(first_token_at - call_started, 2)
            if t == "text_start":
                await _flush_batch()
                _flush()
                cur = {"type": "text", "text": ""}
                await self.cb.on_block_start("text")
            elif t == "reasoning_start":
                await _flush_batch()
                _flush()
                cur = {"type": "thinking", "text": ""}
                await self.cb.on_block_start("thinking")
            elif t == "tool_use_start":
                await _flush_batch()
                _flush()
                cur = {"type": "tool_use", "id": ev.get("id", ""), "name": ev.get("name", ""), "input_raw": ""}
            elif t == "text_delta":
                if cur is not None:
                    cur["text"] += ev.get("text", "")
                text_buf.append(ev.get("text", ""))
            elif t == "reasoning_delta":
                if cur is not None:
                    cur["text"] += ev.get("text", "")
                think_buf.append(ev.get("text", ""))
            elif t in ("tool_use_delta", "usage", "block_end", "finish"):
                await _flush_batch()
                if t == "tool_use_delta":
                    if cur is not None:
                        cur["input_raw"] += ev.get("partial_json", "")
                elif t == "block_end":
                    _flush()
                    await self.cb.on_block_end()
                elif t == "usage":
                    last_usage = dict(ev.get("usage", {}))
                    self.usage.add(last_usage)
                    await self.cb.on_usage(last_usage)
                elif t == "finish":
                    stop_reason = ev.get("stop_reason", "end_turn")
            elif t == "error":
                await _flush_batch()
                await self.cb.on_error(ev.get("message", "unknown error"))
                return "error"
            now = monotonic()
            if (text_buf or think_buf) and now - last_flush >= 0.08:
                await _flush_batch()
        await _flush_batch()
        _flush()
        # Per-call speed/cache metrics for the UI metrics bar.
        duration = max(0.01, monotonic() - call_started)
        out_tok = int(last_usage.get("output_tokens", 0) or 0)
        self.metrics["tok_per_s"] = round(out_tok / duration, 1)
        cache_read = int(last_usage.get("cache_read_input_tokens", 0) or 0)
        in_tok = int(last_usage.get("input_tokens", 0) or 0)
        cache_write = int(last_usage.get("cache_creation_input_tokens", 0) or 0)
        prompt_total = in_tok + cache_read + cache_write
        self.metrics["cache_read"] = cache_read
        self.metrics["cache_ratio"] = (cache_read / prompt_total) if prompt_total else 0.0
        self.metrics["ctx_used"] = prompt_total or self.metrics["ctx_used"]
        self.metrics["calls"] += 1
        self.metrics["elapsed_s"] = round(duration, 2)
        # Store history without thinking blocks (no signature plumbing in M1).
        stored = [b for b in blocks if b["type"] in ("text", "tool_use")]
        if stored:
            self._push_message("assistant", stored)
        await self.cb.on_step_finish(stop_reason)
        return stop_reason

    async def _run_tool_step(self) -> bool:
        assistant = self.messages[-1]
        tool_uses = [b for b in assistant["content"] if b.get("type") == "tool_use"]
        if not tool_uses:
            return False
        ctx = ToolContext(cwd=self.cwd, state=self.state, runtime=self)
        decisions: list[tuple[dict, Any, str, str]] = []

        # Phase 1: permission decisions stay sequential (modals must not stack).
        for tu in tool_uses:
            if self._cancelled:
                return False
            name, args = tu["name"], tu.get("input", {})
            spec = REGISTRY.get(name)
            if spec is None or name in self.excluded_tools:
                decisions.append((tu, None, "error", f"unavailable tool: {name}"))
                continue
            decision = permission.check(name, args, self.mode, self.zconfig.own.permission_rules)
            note = ""
            if decision.verdict == permission.DENY:
                decisions.append((tu, None, "denied", decision.reason))
                continue
            if decision.verdict == permission.ASK:
                choice = await self.cb.on_permission_ask(name, args, decision.reason)
                if choice == "no":
                    decisions.append((tu, None, "rejected", "user rejected this tool call"))
                    continue
                if choice == "always":
                    self.zconfig.own.add_permission_rule(
                        name, permission.pattern_for(name, args), permission.ALLOW
                    )
                    note = "always"
            decisions.append((tu, spec, "run", note))

        results: dict[str, ToolResult] = {}

        async def _execute(tu: dict, spec, note: str) -> None:
            name, args = tu["name"], tu.get("input", {})
            await self.cb.on_tool_begin(tu["id"], name, args)
            started = asyncio.get_event_loop().time()
            ctx.state["_current_tool_id"] = tu["id"]
            try:
                result = await spec.run(args, ctx)
            except Exception as e:
                result = ToolResult(f"tool error: {e}", is_error=True)
            ctx.state.pop("_current_tool_id", None)
            result.meta.setdefault("duration_s", round(asyncio.get_event_loop().time() - started, 2))
            await self.cb.on_tool_end(tu["id"], name, result, note=note)
            results[tu["id"]] = result

        # Phase 2: approved executions run in parallel (e.g. multiple task() calls).
        run_items = [(tu, spec, note) for tu, spec, action, note in decisions if action == "run"]
        if len(run_items) == 1:
            tu, spec, note = run_items[0]
            await _execute(tu, spec, note)
        elif run_items:
            await asyncio.gather(*(_execute(tu, spec, note) for tu, spec, note in run_items))

        # Phase 3: surface denied/rejected/unknown calls as errors, in order.
        for tu, spec, action, note in decisions:
            if action == "run":
                continue
            text = {"denied": f"denied: {note}", "error": note, "rejected": note}[action]
            result = ToolResult(text, is_error=True)
            await self.cb.on_tool_begin(tu["id"], tu["name"], tu.get("input", {}))
            await self.cb.on_tool_end(tu["id"], tu["name"], result,
                                      note=action if action != "error" else "denied")
            results[tu["id"]] = result

        payload = [self._tool_result(tu, results[tu["id"]]) for tu in tool_uses]
        self._push_message("user", payload)
        if self.session:
            self.session.append({"type": "usage", "state": {
                "input": self.usage.input, "output": self.usage.output,
                "cache_read": self.usage.cache_read, "cache_write": self.usage.cache_write,
            }})
        return True

    @staticmethod
    def _tool_result(tu: dict[str, Any], result: ToolResult) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": tu["id"],
            "content": result.output[:60_000],
            "is_error": result.is_error,
        }
