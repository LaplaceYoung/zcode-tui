"""Entry point: `ztui` CLI and non-interactive print mode."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .agent.loop import AgentCallbacks, AgentLoop
from .agent.tools import REGISTRY
from .zconfig import ZConfig

DIM = "\033[2m"
RESET = "\033[0m"
ORANGE = "\033[38;2;215;119;87m"


def apply_model_override(zconfig: ZConfig, spec: str, defaults) -> None:
    pid_guess, _, mid = spec.partition("/")
    for p in zconfig.usable_providers:
        if "/" in spec:
            if (p.id == pid_guess or p.name == pid_guess) and mid in p.models:
                defaults.provider_id, defaults.model_id = p.id, mid
                return
        elif spec in p.models:
            defaults.provider_id, defaults.model_id = p.id, spec
            return


class PrintCallbacks(AgentCallbacks):
    def __init__(self) -> None:
        self._thinking = False
        self.usage_note = ""

    async def on_block_start(self, kind: str) -> None:
        self._thinking = kind == "thinking"
        if self._thinking:
            print(f"{DIM}✻ thinking: ", end="", flush=True)

    async def on_block_end(self) -> None:
        if self._thinking:
            print(RESET, flush=True)
            self._thinking = False

    async def on_text_delta(self, text: str) -> None:
        print(text, end="", flush=True)

    async def on_reasoning_delta(self, text: str) -> None:
        print(f"{DIM}{text}{RESET}", end="", flush=True)

    async def on_tool_begin(self, tool_id: str, name: str, args: dict) -> None:
        spec = REGISTRY.get(name)
        summary = spec.summarize(args) if spec else str(args)[:80]
        print(f"\n{ORANGE}⏺ {name}({summary}){RESET}", flush=True)

    async def on_tool_end(self, tool_id: str, name: str, result: ToolResult, note: str = "") -> None:
        lines = result.output.splitlines()
        head = "\n".join(f"  ⎿ {line}" for line in lines[:10])
        more = f"\n  … {len(lines) - 10} more lines" if len(lines) > 10 else ""
        print(f"{DIM}{head}{more}{RESET}\n", flush=True)

    async def on_permission_ask(self, name: str, args: dict, reason: str) -> str:
        if "dangerous" in reason:
            print(f"{ORANGE}✗ refusing dangerous command in print mode{RESET}", flush=True)
            return "no"
        return "once"

    async def on_error(self, message: str) -> None:
        print(f"\n\033[31merror: {message}\033[0m", flush=True)

    async def on_sub_progress(self, tool_id: str, sub_id: str, status: str, text: str) -> None:
        mark = {"running": "▸", "done": "✔", "error": "✘"}.get(status, "▸")
        print(f"{DIM}  {mark} {text}{RESET}", flush=True)

    async def on_turn_finish(self) -> None:
        print("", flush=True)


async def run_print(zconfig: ZConfig, cwd: Path, prompt: str, model_override: str | None) -> int:
    defaults = zconfig.resolve_defaults()
    if model_override:
        apply_model_override(zconfig, model_override, defaults)
    if not defaults.provider_id:
        print("no usable provider found in ~/.zcode/v2/config.json", file=sys.stderr)
        return 1
    provider = zconfig.provider(defaults.provider_id)
    model = provider.models[defaults.model_id]
    callbacks = PrintCallbacks()
    loop = AgentLoop(
        zconfig, provider, model, defaults.level, cwd,
        callbacks, session=None, mode="build",
    )
    await loop.user_turn(prompt)
    print(f"{DIM}─ {loop.usage.fmt()} · {model.id} @ {defaults.level or 'off'}{RESET}")
    return 0


def run() -> None:
    from .ui.app import ZtuiApp

    parser = argparse.ArgumentParser(prog="ztui", description="terminal agent on your ZCode models")
    parser.add_argument("prompt", nargs="*", help="prompt (with -p runs non-interactively)")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true",
                        help="print mode: run once and stream to stdout")
    parser.add_argument("--resume", nargs="?", const="__pick__", default=None,
                        help="resume a session (optionally by id)")
    parser.add_argument("--model", help="model override, e.g. GLM-5.3 or provider/model")
    parser.add_argument("--cwd", help="working directory (default: current)")
    args = parser.parse_args()

    if getattr(sys.stderr, "isatty", lambda: False)():
        os.environ.setdefault("TERM_PROGRAM", os.environ.get("TERM_PROGRAM", "ztui"))

    zconfig = ZConfig()
    cwd = Path(args.cwd or os.getcwd()).expanduser().resolve()

    if args.print_mode:
        prompt = " ".join(args.prompt).strip() or sys.stdin.read().strip()
        if not prompt:
            print("print mode needs a prompt", file=sys.stderr)
            sys.exit(2)
        rc = asyncio.run(run_print(zconfig, cwd, prompt, args.model))
        sys.exit(rc)

    resume_id = args.resume
    if resume_id == "__pick__":
        resume_id = None  # picker opens via /resume inside the app
    app = ZtuiApp(zconfig, cwd, resume_id=resume_id, model_override=args.model)
    if args.prompt and not resume_id:
        # Seed the input with the prompt for convenience.
        app.call_later(lambda: setattr(app._input, "text", " ".join(args.prompt)))
    app.run()


if __name__ == "__main__":
    run()
