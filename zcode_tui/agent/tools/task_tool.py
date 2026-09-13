"""Task tool: spawn a sub-agent to complete a well-scoped task autonomously."""

from __future__ import annotations

from typing import Any

from . import Tool, ToolContext, ToolResult, register

MAX_SUBAGENT_STEPS = 40


class _SubCallbacks:
    """Collects the sub-agent's output and forwards live progress to the parent."""

    def __init__(self, parent_cb, parent_tool_id: str | None) -> None:
        self.texts: list[str] = []
        self.errors: list[str] = []
        self.tool_log: list[str] = []
        self._labels: dict[str, str] = {}
        self._parent_cb = parent_cb
        self._parent_tool_id = parent_tool_id or ""

    async def _emit(self, sub_id: str, status: str, text: str) -> None:
        if self._parent_cb and self._parent_tool_id:
            try:
                await self._parent_cb.on_sub_progress(self._parent_tool_id, sub_id, status, text)
            except Exception:
                pass

    async def on_block_start(self, kind: str) -> None: ...
    async def on_block_end(self) -> None: ...
    async def on_text_delta(self, text: str) -> None:
        self.texts.append(text)

    async def on_reasoning_delta(self, text: str) -> None: ...
    async def on_tool_begin(self, tool_id: str, name: str, args: dict) -> None:
        from . import REGISTRY

        spec = REGISTRY.get(name)
        summary = spec.summarize(args) if spec else str(args)[:60]
        label = f"{name}({summary})"
        self.tool_log.append(label)
        self._labels[tool_id] = label
        await self._emit(tool_id, "running", label)

    async def on_tool_end(self, tool_id: str, name: str, result: ToolResult, note: str = "") -> None:
        status = "error" if result.is_error else "done"
        head = result.output.splitlines()[0][:80] if result.output else ""
        label = self._labels.get(tool_id, name)
        await self._emit(tool_id, status, f"{label} → {head}" if head else label)

    async def on_permission_ask(self, name: str, args: dict, reason: str) -> str:
        if "dangerous" in reason:
            return "no"
        return "once"

    async def on_error(self, message: str) -> None:
        self.errors.append(message)
        await self._emit("", "error", message)

    async def on_usage(self, usage: dict) -> None: ...
    async def on_compaction(self, info: dict) -> None: ...
    async def on_sub_progress(self, tool_id: str, sub_id: str, status: str, text: str) -> None: ...
    async def on_step_finish(self, stop_reason: str) -> None: ...
    async def on_turn_finish(self) -> None: ...


async def _run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    loop = ctx.runtime
    if loop is None:
        return ToolResult("task tool requires an agent runtime", is_error=True)
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required", is_error=True)

    persona_prompt = None
    persona_name = (args.get("persona") or "").strip()
    if persona_name:
        from ..subagents import resolve

        persona = resolve(persona_name)
        if persona is None:
            from ..subagents import scan_personas

            known = ", ".join(p.name for p in scan_personas()) or "none found"
            return ToolResult(f"unknown persona {persona_name!r} (available: {known})", is_error=True)
        persona_prompt = persona.system_prompt
        if persona.inject_agents_md:
            from ..context import instruction_chain_text

            instr = instruction_chain_text(ctx.cwd)
            if instr:
                persona_prompt = f"{persona_prompt}\n\n{instr}"

    from ..loop import AgentLoop

    cb = _SubCallbacks(loop.cb, ctx.state.get("_current_tool_id", ""))
    child = AgentLoop(
        loop.zconfig,
        loop.provider,
        loop.model,
        loop.level,
        ctx.cwd,
        cb,  # type: ignore[arg-type]
        session=None,
        mode=loop.mode,
        excluded_tools={"task"},
        max_steps=MAX_SUBAGENT_STEPS,
        persona_prompt=persona_prompt,
    )
    loop.register_subagent(child)
    try:
        await child.user_turn(prompt)
    except Exception as e:
        return ToolResult(f"sub-agent crashed: {e}", is_error=True)

    answer = "".join(cb.texts).strip()
    parts = []
    if cb.tool_log:
        shown = cb.tool_log[:12]
        more = "" if len(cb.tool_log) <= 12 else f" (+{len(cb.tool_log)-12} more)"
        parts.append("tools used: " + " → ".join(shown) + more)
    if cb.errors:
        parts.append("errors: " + " | ".join(cb.errors[:3]))
    parts.append(answer if answer else "(sub-agent finished without a text answer)")
    return ToolResult(
        "\n\n".join(parts),
        is_error=bool(cb.errors) and not answer,
        meta={"tools_used": cb.tool_log},
    )


register(
    Tool(
        name="task",
        schema={
            "description": (
                "Launch a sub-agent to complete a well-scoped task autonomously "
                "(research, exploration, multi-file refactors). It has the same "
                "tools (except task itself) and returns a final report. Prefer it "
                "when a task needs many steps you don't need to supervise."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "One-line task summary for display"},
                    "prompt": {"type": "string", "description": "Complete instructions for the sub-agent"},
                    "persona": {
                        "type": "string",
                        "description": "Optionally reuse a custom agent persona (~/.zcode/agents/*.md) by name",
                    },
                },
                "required": ["prompt"],
            },
        },
        run=_run,
        summarize=lambda a: a.get("description", "")[:60],
    )
)
