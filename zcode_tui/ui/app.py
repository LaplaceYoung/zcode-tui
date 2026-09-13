"""The Textual application wiring the agent loop to the transcript UI."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import OptionList, Static, TextArea
from rich.text import Text

from ..agent import permission
from ..agent.loop import AgentCallbacks, AgentLoop
from ..agent.session import Session, list_sessions, load_events
from ..agent.skills import skill_prompt
from ..agent.tools import REGISTRY
from ..main import apply_model_override
from ..zconfig import ModelInfo, Provider, ZConfig
from . import theme_tokens as T
from .input_box import ChatInput, SlashMenu
from .prompts import (
    ModelPickerScreen,
    PermissionScreen,
    SessionPickerScreen,
    ThemePickerScreen,
    ZSessionPickerScreen,
)
from .widgets import (
    AssistantMsg,
    AttachBlock,
    Notice,
    ThinkingMsg,
    TodoBlock,
    ToolBlock,
    UserMsg,
    WelcomeBanner,
)
from textual.widgets.option_list import Option

VERSION = "0.1.0"

COMMANDS: list[tuple[str, str]] = [
    ("/help", "show commands & shortcuts"),
    ("/model", "pick provider, model and reasoning level"),
    ("/mode", "cycle plan / build / yolo"),
    ("/init", "generate an AGENTS.md for this project via the agent"),
    ("/compact", "compact context: summarize older conversation now"),
    ("/sessions", "browse & resume ZCode GUI sessions (read-only)"),
    ("/resume", "resume a ztui session"),
    ("/clear", "clear transcript and start a fresh session"),
    ("/cost", "show token usage"),
    ("/undo", "revert last turn: restore file changes and drop the messages"),
    ("/export", "export this conversation to a Markdown file"),
    ("/goal", "set/show/clear the session goal (drives every turn)"),
    ("/loop", "repeat a prompt every N minutes until /loop stop"),
    ("/plugins", "browse plugin marketplaces & their skills (read-only)"),
    ("/search", "search the transcript and jump between matches"),
    ("/bell", "toggle terminal bell on permission & completion"),
    ("/thinking", "show/hide thinking blocks"),
    ("/automations", "list ZCode scheduled automations (read-only)"),
    ("/checkpoints", "browse workspace checkpoints & drift report (read-only)"),
    ("/workflow", "browse dynamic workflow runs (read-only)"),
    ("/agents", "custom agent personas (~/.zcode/agents, slash-usable)"),
    ("/theme", "switch color theme"),
    ("/notify", "cycle desktop notifications off → term → mac"),
    ("/vim", "toggle vim input mode (esc → NORMAL: hjkl w/b 0/$ x y p i A)"),
    ("/remote", "start/stop remote phone control (watch, send, approve)"),
    ("/exit", "quit ztui"),
]

MODE_HINTS = {
    "plan": "plan mode · read-only, agent proposes plans",
    "build": "build mode · agent asks before mutations",
    "yolo": "yolo mode · auto-approves (dangerous commands still ask)",
}


class ZtuiApp(App):
    CSS_PATH = "ztui.tcss"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        ("escape", "interrupt", "Interrupt"),
        ("ctrl+c", "maybe_quit", "Quit"),
        ("ctrl+o", "toggle_expand", "Expand outputs"),
        ("ctrl+f", "search_open", "Search"),
        ("ctrl+n", "search_next", "Next match"),
        ("ctrl+p", "search_prev", "Prev match"),
        ("ctrl+x", "arm_ctrlx", "Arm kill-all"),
        ("ctrl+k", "kill_all", "Stop subagents"),
        Binding("shift+tab", "toggle_mode", "Plan/build", priority=True),
    ]

    def __init__(
        self,
        zconfig: ZConfig,
        cwd: Path,
        resume_id: str | None = None,
        model_override: str | None = None,
    ) -> None:
        super().__init__()
        self.zconfig = zconfig
        self.cwd = cwd
        defaults = zconfig.resolve_defaults()
        if model_override:
            apply_model_override(zconfig, model_override, defaults)
        pid, mid, level = defaults.provider_id, defaults.model_id, defaults.level
        self.provider: Provider = zconfig.provider(pid)  # type: ignore[arg-type]
        self.model: ModelInfo = self.provider.models[mid]  # type: ignore[index]
        self.level = level
        self.mode = defaults.mode if defaults.mode in permission.MODES else "build"
        self.session: Session | None = None
        self.loop = self._new_loop()
        self._resume_id = resume_id
        self._cur_text: AssistantMsg | None = None
        self._cur_thinking: ThinkingMsg | None = None
        self._tool_blocks: dict[str, ToolBlock] = {}
        self._agent_task: asyncio.Task | None = None
        self._agent_busy = False
        self._run_started = 0.0
        self._quit_armed = 0.0
        self._remote = None
        self._relay = None
        self._pairing = None
        self._pending_permission: dict | None = None
        self._file_index: list[str] | None = None
        self._file_index_ts = 0.0
        self._queue: list[str] = []
        self._history: list[str] = []
        self._history_draft = ""
        self._history_nav = 0
        self._loops: list[dict] = []
        self._search_state: dict | None = None
        own = self.zconfig.own.defaults
        self.bell_enabled = own.get("bell") == "on"
        self.notify_mode = own.get("notify", "off")
        self.show_thinking = own.get("thinking", "show") != "hide"
        self.vim_enabled = own.get("vim") == "on"
        self.vim_mode = "INSERT"
        self._vim_register = ""
        theme_name = own.get("theme", "python")
        from . import theme_tokens as T

        T.register_themes(self)
        T.set_palette(theme_name if theme_name in T.PALETTES else "python")
        self.theme = f"ztui-{T.current()}"
        from ..agent import skills as skills_mod

        self._skills = skills_mod.scan_skills()
        self._skill_by_command = {
            "/" + s.name: s
            for s in self._skills
            if "/" + s.name not in {c for c, _ in COMMANDS}
        }
        from ..agent import subagents

        self._personas = subagents.scan_personas()
        self._persona_by_command = {
            "/" + p.name: p
            for p in self._personas
            if "/" + p.name not in {c for c, _ in COMMANDS}
        }
        self._ctrlx_armed = 0.0

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        self._chat = VerticalScroll(id="chat")
        yield self._chat
        with Container(id="input-dock"):
            self._metrics_line = Static("", id="metrics-line")
            yield self._metrics_line
            self._menu = SlashMenu(id="slash-menu")
            yield self._menu
            self._input = ChatInput(id="input")
            yield self._input
            self._hint = Static("", classes="hint")
            yield self._hint
        self._status = Static("", id="status-line")
        yield self._status

    def on_mount(self) -> None:
        self.title = "ztui"
        self._mount(WelcomeBanner(VERSION, self._model_label(), str(self.cwd)))
        if self._resume_id:
            self.call_after_refresh(lambda: asyncio.ensure_future(self._resume(self._resume_id)))
        self._load_history_lines()
        self._apply_mode_border()
        self._input.focus()
        self._refresh_status()
        self.set_interval(0.15, self._tick)

    # -- loop helpers --------------------------------------------------------

    def _new_loop(self) -> AgentLoop:
        return AgentLoop(
            self.zconfig, self.provider, self.model, self.level,
            self.cwd, AppCallbacks(self), session=None, mode=self.mode,
        )

    def _model_label(self) -> str:
        lvl = self.level or "off"
        return f"model: {self.model.id} · reasoning: {lvl} · provider: {self.provider.name}"

    def _ensure_session(self) -> None:
        if self.session is None:
            self.session = Session.create(str(self.cwd), self.provider.id, self.model.id, self.level)
            self.loop.session = self.session

    def _mount(self, widget) -> None:
        self._chat.mount(widget)
        self._chat.scroll_end(animate=False)

    # -- input handling ------------------------------------------------------

    @on(ChatInput.Submitted)
    async def _submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text
        self._input.clear()
        self._update_menu("")
        if text.startswith("/"):
            await self._handle_command(text)
            return
        self._push_history(text)
        if text.startswith("!"):
            await self._shell_prefix(text[1:].strip())
            return
        if self._agent_busy:
            self._queue.append(text)
            self._mount(UserMsg(text, queued=True))
            self._refresh_status()
            return
        await self._dispatch(text)

    async def _dispatch(self, text: str) -> None:
        content = self._with_images(text)
        self._mount(UserMsg(text))
        self._ensure_session()
        self._agent_task = asyncio.create_task(self._run_agent(content))

    def _with_images(self, text: str):
        import base64
        import re

        matches = re.findall(r"@([^\s@]+\.(?:png|jpe?g|gif|webp))", text, re.IGNORECASE)
        paths = []
        for m in matches[:3]:
            p = Path(m).expanduser()
            if not p.is_absolute():
                p = (self.cwd / p).resolve()
            if p.exists() and p.stat().st_size <= 10 * 1024 * 1024:
                paths.append(p)
        if not paths:
            return text
        if "image" not in self.model.input_modalities:
            self._notice(
                f"{self.model.id} can't see images — {len(paths)} image(s) ignored "
                "(switch to a vision-capable model via /model)"
            )
            return text
        mimes = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".webp": "image/webp"}
        blocks = [{"type": "image", "source": {
            "type": "base64",
            "media_type": mimes.get(p.suffix.lower(), "image/png"),
            "data": base64.b64encode(p.read_bytes()).decode(),
        }} for p in paths]
        blocks.append({"type": "text", "text": text})
        self._mount(AttachBlock(paths))
        return blocks

    def _history_intercept(self, event) -> bool:
        if self._menu_visible() or not self._history and event.key == "up":
            return False
        row, _col = self._input.cursor_location
        last_row = self._input.text.count("\n")
        if event.key == "up" and row == 0:
            if not self._history:
                return False
            if self._history_nav >= len(self._history):
                self._history_draft = self._input.text
            self._history_nav = max(0, self._history_nav - 1)
            self._input.text = self._history[self._history_nav]
            self._cursor_end()
            return True
        if event.key == "down" and row == last_row:
            if self._history_nav < len(self._history):
                self._history_nav += 1
                self._input.text = (
                    self._history_draft
                    if self._history_nav >= len(self._history)
                    else self._history[self._history_nav]
                )
                self._cursor_end()
            return True
        return False

    def _push_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
            self._history = self._history[-200:]
        self._history_nav = len(self._history)
        self._save_history()

    def _save_history(self) -> None:
        import json

        from ..zconfig import ZTUI_DATA_DIR

        try:
            ZTUI_DATA_DIR.mkdir(parents=True, exist_ok=True)
            (ZTUI_DATA_DIR / "history.json").write_text(json.dumps(self._history[-200:]))
        except OSError:
            pass

    def _load_history_lines(self) -> None:
        import json

        from ..zconfig import ZTUI_DATA_DIR

        try:
            self._history = json.loads((ZTUI_DATA_DIR / "history.json").read_text())
            self._history_nav = len(self._history)
        except (OSError, ValueError):
            self._history = []

    async def _shell_prefix(self, command: str) -> None:
        self._mount(UserMsg("! " + command))
        decision = permission.check("bash", {"command": command}, self.mode,
                                    self.zconfig.own.permission_rules)
        if decision.verdict == permission.ASK and "dangerous" in decision.reason:
            choice = await self.ask_permission("bash", {"command": command}, decision.reason)
            if choice == "no":
                self._notice("shell command rejected")
                return
        self._ensure_session()
        from ..agent.tools import REGISTRY, ToolContext

        pseudo = f"shell-{id(command)}"
        self.open_tool(pseudo, "shell", {"command": command})
        result = await REGISTRY["bash"].run(
            {"command": command}, ToolContext(cwd=self.cwd, state=self.loop.state, runtime=self.loop)
        )
        self.close_tool(pseudo, result, "")
        self.loop._push_message("user", [{
            "type": "text",
            "text": f"$ {command}\n<shell_output>\n{result.output}\n</shell_output>",
        }])
        self._notice("shell output added to the conversation")

    def action_interrupt(self) -> None:
        dropped = len(self._queue)
        self._queue.clear()
        if self._agent_task and not self._agent_task.done():
            self.loop.cancel()
            self._agent_task.cancel()
            self._notice(f"interrupted" + (f" · dropped {dropped} queued" if dropped else ""))
        elif dropped:
            self._notice(f"dropped {dropped} queued message(s)")
        self.close_text()
        self.close_thinking()

    @on(TextArea.Changed)
    def _changed(self, event: TextArea.Changed) -> None:
        self._update_menu(self._input.text)

    @on(OptionList.OptionSelected, "#slash-menu")
    def _menu_selected(self, event: OptionList.OptionSelected) -> None:
        self._menu_accept(str(event.option.id))

    def _menu_mode(self) -> str | None:
        text = self._input.text
        if text.startswith("/") and "\n" not in text:
            return "slash"
        import re

        m = re.search(r"(?:^|\s)@([^\s@]*)$", text)
        if m and "\n" not in text:
            return "mention"
        return None

    def _menu_visible(self) -> bool:
        return "visible" in self._menu.classes

    def _menu_intercept(self, event) -> bool:
        """Steer the completion menu from ChatInput. True when the key is consumed."""
        if self._menu is None or not self._menu_visible():
            return False
        if event.key in ("up", "down"):
            current = self._menu.highlighted or 0
            step = -1 if event.key == "up" else 1
            total = len(self._menu.options)
            if total:
                self._menu.highlighted = (current + step) % total
            return True
        if event.key == "tab":
            opt = self._menu.get_option_at_index(self._menu.highlighted or 0)
            if opt is not None:
                self._menu_accept(str(opt.id))
            return True
        if event.key == "enter":
            mode = self._menu_mode()
            if mode == "mention":
                opt = self._menu.get_option_at_index(self._menu.highlighted or 0)
                if opt is not None:
                    self._menu_accept(str(opt.id))
                return True
            if mode == "slash":
                cmd = self._input.text.split(" ")[0].lower()
                if cmd not in [c for c, _ in self._all_commands()]:
                    opt = self._menu.get_option_at_index(self._menu.highlighted or 0)
                    if opt is not None:
                        self._menu_accept(str(opt.id))
                    return True
        return False

    def _cursor_end(self) -> None:
        lines = self._input.text.split("\n")
        self._input.cursor_location = (len(lines) - 1, len(lines[-1]))

    def _menu_accept(self, choice: str) -> None:
        mode = self._menu_mode()
        if mode == "slash":
            self._input.text = choice + " "
        elif mode == "mention":
            import re

            self._input.text = re.sub(r"@([^\s@]*)$", choice + " ", self._input.text)
        self._cursor_end()
        self._input.focus()
        self._update_menu(self._input.text)

    def _all_commands(self) -> list[tuple[str, str]]:
        return COMMANDS + [
            (c, f"skill: {s.description[:60]}") for c, s in self._skill_by_command.items()
        ]

    def _update_menu(self, text: str) -> None:
        mode = self._menu_mode()
        if mode is None or self._menu is None or self._agent_busy:
            self._menu.remove_class("visible")
            return
        self._menu.clear_options()
        if mode == "slash":
            prefix = text.split(" ")[0].lower()
            matches = [(c, d) for c, d in self._all_commands() if c.startswith(prefix)]
            for c, d in matches:
                self._menu.add_option(Option(f"{c}  — {d}", id=c))
        else:
            query = text.rsplit("@", 1)[-1].lower()
            for path in self._match_files(query)[:12]:
                self._menu.add_option(Option(path, id=path))
        if not len(self._menu.options):
            self._menu.remove_class("visible")
            return
        self._menu.add_class("visible")
        self._menu.highlighted = 0

    def _match_files(self, query: str) -> list[str]:
        files = self._files()
        if not query:
            return files[:12]
        scored = []
        for f in files:
            lf = f.lower()
            if query in lf:
                base = lf.rsplit("/", 1)[-1]
                score = 0 if base.startswith(query) else (1 if "/" not in lf.split(query, 1)[0][-30:] else 2)
                scored.append((score, len(f), f))
        scored.sort()
        return [f for _, _, f in scored]

    def _files(self) -> list[str]:
        import os as _os

        now = time.monotonic()
        if self._file_index is None or now - self._file_index_ts > 20:
            from ..agent.tools.search import SKIP_DIRS

            out: list[str] = []
            for dirpath, dirnames, filenames in _os.walk(self.cwd):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                for fn in filenames:
                    rel = _os.path.relpath(_os.path.join(dirpath, fn), self.cwd)
                    out.append(rel)
                    if len(out) >= 8000:
                        break
            self._file_index = sorted(set(out))
            self._file_index_ts = now
        return self._file_index

    # -- agents run ----------------------------------------------------------

    async def _run_agent(self, text) -> None:
        self._set_running(True)
        try:
            await self.loop.user_turn(text)
        except Exception as e:
            self._notify_error(f"agent error: {e}")
        finally:
            self._set_running(False)
            if self._queue:
                nxt = self._queue.pop(0)
                self._agent_task = asyncio.create_task(self._run_agent(nxt))

    async def _run_agent_with_persona(self, persona, prompt_body: str) -> None:
        self._set_running(True)
        old_persona = self.loop.persona_prompt
        self.loop.persona_prompt = persona.system_prompt
        try:
            await self.loop.user_turn(prompt_body)
        except Exception as e:
            self._notify_error(f"agent error: {e}")
        finally:
            self.loop.persona_prompt = old_persona
            self._set_running(False)
            if self._queue:
                nxt = self._queue.pop(0)
                self._agent_task = asyncio.create_task(self._run_agent(nxt))

    def _set_running(self, running: bool) -> None:
        self._agent_busy = running
        if running:
            self._run_started = time.monotonic()
        self._refresh_status()

    def _tick(self) -> None:
        for block in self._tool_blocks.values():
            block.tick()
        for thinking in self._chat.query(ThinkingMsg):
            thinking.tick()
        if self._agent_busy:
            self._refresh_status()
        self._refresh_metrics()

    @staticmethod
    def _fmt_num(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _refresh_metrics(self) -> None:
        m = self.loop.metrics
        if not m["calls"]:
            self._metrics_line.update("")
            return
        limit = max(1, self.model.context_limit)
        used = m["ctx_used"]
        frac = min(1.0, used / limit)
        full = round(frac * 10)
        bar = "▮" * full + "▯" * (10 - full)
        frac_color = "#57ab5a" if frac < 0.5 else ("#e0af68" if frac < 0.75 else "#e5534b")
        pct = f"{frac * 100:.0f}%" if frac >= 0.05 else f"{frac * 100:.1f}%"
        t = Text()
        t.append(f" {pct} ", style=f"bold {frac_color}")
        t.append(bar, style=frac_color)
        t.append(f"  {self._fmt_num(used)}/{self._fmt_num(limit)}", style=T.DIM)
        if m["ttft_s"] is not None:
            t.append(f"  ·  ⚡ {m['ttft_s']:.2f}s", style=T.DIM)
        if m["tok_per_s"]:
            t.append(f"  ·  {m['tok_per_s']:.0f} tok/s", style=T.DIM)
        t.append(f"  ·  {m['cache_ratio'] * 100:.0f}%", style=T.ACCENT)
        t.append(f" cache {self._fmt_num(m['cache_read'])}", style=T.ACCENT)
        u = self.loop.usage
        t.append(f"  ·  ↑{self._fmt_num(u.input + u.cache_write)}", style="#61afef")
        t.append(f" ↓{self._fmt_num(u.output)}", style=T.DIM)
        self._metrics_line.update(t)

    # -- transcript factories (called from AppCallbacks) ---------------------

    def open_text(self) -> None:
        self._cur_text = AssistantMsg()
        self._mount(self._cur_text)

    def append_text(self, delta: str) -> None:
        if self._cur_text is None or self._cur_text.finished:
            self.open_text()
        self._cur_text.append(delta)
        self._chat.scroll_end(animate=False)

    def close_text(self) -> None:
        if self._cur_text is not None:
            self._cur_text.finish()
            self._cur_text = None

    def open_thinking(self) -> None:
        if not self.show_thinking:
            return
        self.close_text()
        self._cur_thinking = ThinkingMsg()
        self._mount(self._cur_thinking)

    def append_thinking(self, delta: str) -> None:
        if not self.show_thinking:
            return
        if self._cur_thinking is None:
            self.open_thinking()
        self._cur_thinking.append(delta)
        self._chat.scroll_end(animate=False)

    def close_thinking(self) -> None:
        if self._cur_thinking is not None:
            self._cur_thinking.finish()
            self._cur_thinking = None

    def open_tool(self, tool_id: str, name: str, args: dict) -> None:
        self.close_text()
        self.close_thinking()
        spec = REGISTRY.get(name)
        summary = spec.summarize(args) if spec else str(args)[:80]
        block = ToolBlock(tool_id, name, summary)
        self._tool_blocks[tool_id] = block
        self._mount(block)

    def close_tool(self, tool_id: str, result, note: str) -> None:
        block = self._tool_blocks.pop(tool_id, None)
        if block is not None:
            block.finish(result.is_error, result.output, note, result.meta)
        todos = result.meta.get("todos")
        if todos is not None:
            self._mount(TodoBlock(todos))
        self._chat.scroll_end(animate=False)

    async def _modal(self, screen):
        """push_screen with a callback bridge — safe from any context (no worker needed)."""
        fut = asyncio.get_running_loop().create_future()

        def _done(result) -> None:
            if not fut.done():
                fut.set_result(result)

        self.push_screen(screen, _done)
        return await fut

    async def ask_permission(self, name: str, args: dict, reason: str) -> str:
        preview = dict(args)
        if name in ("write", "edit"):
            diff = self._diff_preview(name, args)
            if diff:
                preview["_diff_preview"] = diff
        self._pending_permission = {"name": name, "args": dict(args)}
        self._bell()
        self._notify_ui("ztui", f"permission requested: {name}")
        try:
            choice = await self._modal(PermissionScreen(name, preview, reason))
        finally:
            self._pending_permission = None
        return {"yes": "once"}.get(choice, choice)

    def _diff_preview(self, name: str, args: dict) -> dict | None:
        path = Path(args.get("path", "")).expanduser()
        if not path.is_absolute():
            path = (self.cwd / path).resolve()
        old = path.read_text(errors="replace") if path.exists() else None
        if name == "write":
            return {"old": old, "new": args.get("content", "")}
        new = old.replace(args.get("old_string", ""), args.get("new_string", ""), 1) if old else None
        return {"old": old, "new": new} if new is not None else None

    def _notice(self, text: str) -> None:
        self._mount(Notice(text))

    def _notify_error(self, text: str) -> None:
        self._mount(Notice(text, style="error"))

    # -- slash commands ------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/init":
            if self._agent_busy:
                self._notice("agent is working — esc to interrupt, or wait")
                return
            INIT_PROMPT = (
                "Analyze this project thoroughly and create an AGENTS.md file at the project root. "
                "Use glob/grep/read to explore: package/build config, directory layout, test setup, "
                "lint/format config, and CI. Then write a concise AGENTS.md covering: what the project "
                "is, how to build/run/test it, code style and conventions a code agent should follow. "
                "Keep it under 60 lines, factual, no marketing language. If an AGENTS.md already exists, "
                "merge missing conventions into it instead of overwriting."
            )
            self._ensure_session()
            self._agent_task = asyncio.create_task(self._run_agent(INIT_PROMPT))
        elif cmd == "/sessions":
            self.run_worker(self._zsessions_picker(), exclusive=False, name="zsessions-picker")
        elif cmd == "/help":
            lines = [f"{c} — {d}" for c, d in COMMANDS]
            if self._skill_by_command:
                names = " ".join(self._skill_by_command)
                lines.append(f"skills loaded: {names}")
            lines += [
                "",
                "enter send · shift+enter/ctrl+j newline · esc interrupt",
                "shift+tab plan/build · ctrl+o expand outputs · @ mentions files",
            ]
            self._notice("\n".join(lines))
        elif cmd == "/model":
            self.run_worker(self._pick_model(), exclusive=False, name="model-picker")
        elif cmd == "/mode":
            self._cycle_mode(arg.strip() or None)
        elif cmd == "/clear":
            self._clear()
        elif cmd == "/cost":
            self._notice("token usage this session:\n" + self.loop.usage.report())
        elif cmd == "/resume":
            self.run_worker(self._resume_picker(), exclusive=False, name="resume-picker")
        elif cmd == "/compact":
            if self._agent_busy:
                self._notice("agent is working — compact applies before the next turn")
                return
            self.run_worker(self._compact_now(), exclusive=False, name="compactor")
        elif cmd == "/goal":
            self._handle_goal(arg)
        elif cmd == "/loop":
            self._handle_loop(arg)
        elif cmd == "/plugins":
            self.run_worker(self._plugins_panel(), exclusive=False, name="plugins")
        elif cmd == "/search":
            self.run_worker(self._search_flow(arg.strip()), exclusive=False, name="search")
        elif cmd == "/undo":
            if self._agent_busy:
                self._notice("agent is working — undo after it finishes")
                return
            stats = self.loop.undo_last_turn()
            if not stats or (stats["removed"] == 0 and stats["restored"] == 0):
                self._notice("nothing to undo")
            else:
                self._rerender()
                self._notice(f"undone: restored {stats['restored']} file(s), removed {stats['removed']} message(s)")
        elif cmd == "/export":
            self._export_markdown()
        elif cmd == "/bell":
            self.bell_enabled = not self.bell_enabled
            self.zconfig.own.set_default(bell="on" if self.bell_enabled else "off")
            self._notice(f"bell {'on' if self.bell_enabled else 'off'}")
        elif cmd == "/thinking":
            self.show_thinking = not self.show_thinking
            self.zconfig.own.set_default(thinking="show" if self.show_thinking else "hide")
            for w in self._chat.query(ThinkingMsg):
                w.display = self.show_thinking
            self._notice(f"thinking blocks {'shown' if self.show_thinking else 'hidden'}")
        elif cmd == "/automations":
            self.run_worker(self._automations_panel(), exclusive=False, name="automations")
        elif cmd == "/checkpoints":
            self.run_worker(self._checkpoints_panel(), exclusive=False, name="checkpoints")
        elif cmd == "/workflow":
            self.run_worker(self._workflow_panel(), exclusive=False, name="workflow")
        elif cmd == "/agents":
            self.run_worker(self._agents_panel(), exclusive=False, name="agents")
        elif cmd in self._persona_by_command:
            if self._agent_busy:
                self._notice("agent is working — esc to interrupt, or wait")
                return
            persona = self._persona_by_command[cmd]
            task_text = arg.strip() or f"按 {persona.name} 的本职完成当前工作区的一项典型任务"
            prompt_body = (
                f"按 agent 角色「{persona.name}」完成任务。\n\n"
                f"# Task\n\n{task_text}"
            )
            self._mount(UserMsg(f"{cmd} {arg}" if arg else cmd))
            self._notice(f"spawning agent persona: {persona.name} ({persona.description[:60]})")
            self._ensure_session()
            self._agent_task = asyncio.create_task(
                self._run_agent_with_persona(persona, prompt_body)
            )
        elif cmd == "/theme":
            self.run_worker(self._theme_picker(), exclusive=False, name="theme")
        elif cmd == "/notify":
            modes = ["off", "term", "mac"]
            self.notify_mode = modes[(modes.index(self.notify_mode) + 1) % 3] if self.notify_mode in modes else "off"
            self.zconfig.own.set_default(notify=self.notify_mode)
            self._notice(f"desktop notifications: {self.notify_mode}")
            if self.notify_mode != "off":
                self._notify_ui("ztui", "notifications are on")
        elif cmd == "/vim":
            self.vim_enabled = not self.vim_enabled
            self.vim_mode = "INSERT"
            self.zconfig.own.set_default(vim="on" if self.vim_enabled else "off")
            self._refresh_status()
            self._notice(f"vim input: {'on — esc switches to NORMAL' if self.vim_enabled else 'off'}")
        elif cmd == "/remote":
            self.run_worker(self._toggle_remote(), exclusive=False, name="remote")
        elif cmd in self._skill_by_command:
            if self._agent_busy:
                self._notice("agent is working — esc to interrupt, or wait")
                return
            skill = self._skill_by_command[cmd]
            prompt_body = skill_prompt(skill, arg.strip())
            self._notice(f"running skill /{skill.name}…")
            self._ensure_session()
            self._agent_task = asyncio.create_task(self._run_agent(prompt_body))
        else:
            self._notice(f"unknown command {cmd} — /help lists them")

    async def _compact_now(self) -> None:
        info = await self.loop.force_compact()
        if info:
            self._notice(f"compacted: {info['replaced']} messages summarized, {info['kept']} kept")
        else:
            self._notice("nothing to compact")

    # -- goal / loop ---------------------------------------------------------

    def _handle_goal(self, arg: str) -> None:
        arg = arg.strip()
        if not arg:
            goal = self.loop.state.get("goal")
            self._notice(f"active goal: {goal}" if goal else "no goal set — /goal <objective> to set one")
            return
        if arg == "clear":
            self.loop.state.pop("goal", None)
            self._notice("goal cleared")
            self._refresh_status()
            return
        self.loop.state["goal"] = arg
        self._notice(f"goal locked in: {arg}")
        self._refresh_status()

    def _handle_loop(self, arg: str) -> None:
        import re as _re

        arg = arg.strip()
        if arg in ("", "list"):
            if not self._loops:
                self._notice("no loops running — /loop 10m <prompt> to start one")
                return
            rows = [
                f"{i}: every {lp['every_s'] // 60 if lp['every_s'] >= 60 else lp['every_s']}"
                f"{'m' if lp['every_s'] >= 60 else 's'} · {lp['prompt'][:60]} (fired {lp['fired']}x)"
                for i, lp in enumerate(self._loops)
            ]
            self._notice("running loops:\n" + "\n".join(rows))
            return
        m = _re.match(r"^(?:stop|cancel)\s*(\d*)$", arg)
        if m:
            idx = int(m.group(1)) if m.group(1) else 0
            if 0 <= idx < len(self._loops):
                lp = self._loops.pop(idx)
                lp["task"].cancel()
                self._notice(f"loop {idx} stopped")
            else:
                self._notice(f"no loop {idx}")
            return
        m = _re.match(r"^(?:(\d+)m\s+|(\d+)\s+)(.+)$", arg, _re.DOTALL)
        if not m:
            self._notice("usage: /loop 10m <prompt> · /loop 300 <prompt>(seconds) · /loop list · /loop stop <i>")
            return
        every_s = int(m.group(1) or m.group(2)) * (60 if m.group(1) else 1)
        prompt = m.group(3).strip()
        if every_s < 30:
            self._notice("loop interval must be >= 30s")
            return
        task = asyncio.create_task(self._loop_runner(every_s, prompt))
        self._loops.append({"task": task, "every_s": every_s, "prompt": prompt, "fired": 0})
        num, unit = (every_s // 60, "m") if every_s >= 60 else (every_s, "s")
        self._notice(f"loop started: every {num}{unit}: {prompt[:70]}")

    async def _loop_runner(self, every_s: int, prompt: str) -> None:
        this = {"every_s": every_s, "prompt": prompt}
        while True:
            await asyncio.sleep(every_s)
            for lp in self._loops:
                if lp["every_s"] == every_s and lp["prompt"] == prompt:
                    lp["fired"] += 1
                    break
            self._push_history(prompt)
            self._mount(UserMsg(prompt))
            if self._agent_busy:
                self._queue.append(prompt)
            else:
                await self._dispatch(prompt)

    async def _agents_panel(self) -> None:
        from .prompts import AgentsScreen

        await self._modal(AgentsScreen(self._personas))

    # -- plugins panel --------------------------------------------------------

    async def _plugins_panel(self) -> None:
        from ..agent.zplugins import list_marketplace_plugins

        entries = list_marketplace_plugins()
        from .prompts import PluginsScreen

        await self._modal(PluginsScreen(entries))

    # -- transcript search ----------------------------------------------------

    async def _search_flow(self, arg: str) -> None:
        if not arg:
            self._notice("usage: /search <text> · ctrl+n/ctrl+p jump matches · esc exits")
            return
        matches = self._search_matches(arg)
        if not matches:
            self._notice(f"no matches for '{arg}' in transcript")
            return
        self._search_state = {"query": arg, "matches": matches, "index": 0}
        self._jump_to_match(0)
        self._notice(f"{len(matches)} match(es) · ctrl+n next · ctrl+p previous")

    def _search_matches(self, query: str):
        q = query.lower()
        out = []
        for child in self._chat.children:
            text_parts = []
            for st in child.query("Static"):
                c = st.content
                s = c.plain if hasattr(c, "plain") else str(c)
                if s:
                    text_parts.append(s)
            if q in "\n".join(text_parts).lower():
                out.append(child)
        return out

    def _jump_to_match(self, index: int) -> None:
        state = self._search_state
        if not state or not state["matches"]:
            return
        matches = state["matches"]
        for w in matches:
            w.remove_class("search-hit")
        idx = index % len(matches)
        state["index"] = idx
        target = matches[idx]
        target.add_class("search-hit")
        target.scroll_visible(duration=0.2)
        self._notice(f"match {idx + 1}/{len(matches)}: '{state['query']}'")

    def action_search_next(self) -> None:
        if getattr(self, "_search_state", None):
            self._jump_to_match(self._search_state["index"] + 1)
        else:
            self._notice("no active search — /search <text> first")

    def action_search_prev(self) -> None:
        if getattr(self, "_search_state", None):
            self._jump_to_match(self._search_state["index"] - 1)
        else:
            self._notice("no active search — /search <text> first")

    def action_search_open(self) -> None:
        self._input.text = "/search "
        self._cursor_end()
        self._input.focus()

    # -- plugins 面板与搜索结束 ------------------------------------------------


    def _bell(self) -> None:
        if self.bell_enabled:
            self.console.bell()

    def _notify_ui(self, title: str, message: str) -> None:
        from . import notify as _notify

        _notify.send(self.notify_mode, title, message)

    async def _theme_picker(self) -> None:
        picked = await self._modal(ThemePickerScreen(T.names(), T.current()))
        if picked:
            T.set_palette(picked)
            self.theme = f"ztui-{picked}"
            self.zconfig.own.set_default(theme=picked)
            self._rerender()
            self._apply_mode_border()
            self._refresh_status()
            self._notice(f"theme: {picked}")

    def _rerender(self) -> None:
        for child in list(self._chat.children):
            child.remove()
        self._mount(WelcomeBanner(VERSION, self._model_label(), str(self.cwd)))
        msgs = [{"type": "message", "role": m.get("role"), "content": m.get("content", [])}
                for m in self.loop.messages]
        self._replay(msgs)

    def _export_markdown(self) -> None:
        from datetime import datetime

        from ..zconfig import ZTUI_DATA_DIR

        out_dir = ZTUI_DATA_DIR / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = out_dir / f"ztui-{datetime.now():%Y%m%d-%H%M%S}.md"
        lines = [f"# ztui session — {self.model.id} @ {self.level or 'off'}\n"]
        for m in self.loop.messages:
            for b in m.get("content") or []:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    lines.append(f"\n## {'user' if m['role'] == 'user' else 'assistant'}\n\n{b.get('text', '')}")
                elif t == "tool_use":
                    lines.append(f"\n### tool: {b.get('name')}\n\n```json\n{b.get('input')}\n```")
                elif t == "tool_result":
                    body = str(b.get("content", ""))
                    lines.append(f"\n> result:\n>\n> " + body.replace("\n", "\n> ")[:4000])
        fname.write_text("\n".join(lines))
        self._notice(f"exported → {fname}")

    async def _automations_panel(self) -> None:
        from ..agent.zautomations import list_automations

        rows = list_automations()
        if rows is None:
            self._notice("no ZCode automation database found")
            return
        from .prompts import AutomationsScreen

        await self._modal(AutomationsScreen(rows))

    async def _checkpoints_panel(self) -> None:
        from ..agent import zcheckpoints
        from .prompts import CheckpointDetailScreen, CheckpointsScreen

        projects = zcheckpoints.list_checkpoints(self.cwd)
        if not projects or not projects[0][2]:
            self._notice(f"no checkpoints for {self.cwd}")
            return
        _phash, workspace_path, cps = projects[0]
        chosen = await self._modal(CheckpointsScreen(workspace_path, cps))
        if not chosen:
            return
        cp = next((c for c in cps if c.full_hash == chosen), None)
        if cp is None:
            return
        try:
            manifest = zcheckpoints.load_manifest(cp.project, cp.full_hash)
            drift = zcheckpoints.analyze_drift(self.cwd, manifest.get("files", []))
            extra = zcheckpoints.load_extra_configs(cp.project)
        except Exception as e:
            self._notify_error(f"cannot read checkpoint: {e}")
            return
        await self._modal(CheckpointDetailScreen(cp, drift, extra))

    async def _workflow_panel(self) -> None:
        from ..agent import zworkflows
        from .prompts import WorkflowDetailScreen, WorkflowScreen

        if not zworkflows.available():
            self._notice("no ZCode database found")
            return
        runs = zworkflows.list_runs()
        chosen = await self._modal(WorkflowScreen(runs))
        if not chosen:
            return
        detail = zworkflows.run_detail(chosen)
        if detail:
            await self._modal(WorkflowDetailScreen(detail))

    async def _pick_model(self) -> None:
        picked = await self._modal(
            ModelPickerScreen(self.zconfig.usable_providers, (self.provider.id, self.model.id, self.level))
        )
        if picked:
            self._switch_model(*picked)

    async def _resume_picker(self) -> None:
        sid = await self._modal(SessionPickerScreen(list_sessions()))
        if sid:
            await self._resume(sid)

    async def _zsessions_picker(self) -> None:
        from ..agent import zsessions

        if not zsessions.available():
            self._notice("no ZCode GUI database found")
            return
        rows = zsessions.list_zcode_sessions()
        sid = await self._modal(ZSessionPickerScreen(rows))
        if not sid:
            return
        try:
            meta, events = zsessions.load_zcode_session(sid)
        except KeyError:
            self._notify_error(f"GUI session {sid} not found")
            return
        self._clear()
        # Target model from the GUI session when it still exists locally.
        provider = self.zconfig.provider(meta.get("provider_id") or "")
        if provider and meta.get("model_id") in provider.models:
            self.provider = provider
            self.model = provider.models[meta["model_id"]]
            self.loop.set_target(self.provider, self.model, self.level)
        self.loop.load_history(events)
        self._replay(events)
        self._notice(
            f"resumed GUI session {sid} ({meta.get('title', '')}) with "
            f"{len(events)} messages — continue typing to keep going"
        )
        self._refresh_status()

    def _switch_model(self, pid: str, mid: str, level: str | None) -> None:
        provider = self.zconfig.provider(pid)
        if provider is None or mid not in provider.models:
            self._notify_error(f"unknown model {pid}/{mid}")
            return
        self.provider, self.model, self.level = provider, provider.models[mid], level
        self.loop.set_target(provider, self.model, level)
        self.zconfig.own.set_default(provider=pid, model=mid, level=level)
        self._mount(WelcomeBanner(VERSION, self._model_label(), str(self.cwd)))
        self._refresh_status()

    def _cycle_mode(self, explicit: str | None = None) -> None:
        if explicit in permission.MODES:
            self.mode = explicit
        else:
            order = ["plan", "build", "yolo"]
            self.mode = order[(order.index(self.mode) + 1) % len(order)]
        self.loop.set_mode(self.mode)
        self.zconfig.own.set_default(mode=self.mode)
        self._apply_mode_border()
        self._notice(f"mode: {self.mode} — {MODE_HINTS[self.mode]}")
        self._refresh_status()

    def _apply_mode_border(self) -> None:
        colors = {"plan": "#61afef", "build": T.ACCENT, "yolo": "#e5534b"}
        color = colors.get(self.mode, T.ACCENT)
        try:
            self._input.styles.border = ("round", color)
        except Exception:
            pass

    def _clear(self) -> None:
        for child in list(self._chat.children):
            child.remove()
        self.session = None
        self.loop = self._new_loop()
        self._mount(WelcomeBanner(VERSION, self._model_label(), str(self.cwd)))
        self._refresh_status()

    async def _resume(self, sid: str) -> None:
        try:
            events = load_events(sid)
        except FileNotFoundError:
            self._notify_error(f"session {sid} not found")
            return
        self._clear()
        meta_event = next((e for e in events if e.get("type") == "session"), None)
        if meta_event:
            meta = meta_event.get("meta", {})
            provider = self.zconfig.provider(meta.get("provider_id", ""))
            if provider and meta.get("model_id") in provider.models:
                self.provider = provider
                self.model = provider.models[meta["model_id"]]
                self.level = meta.get("level")
                self.loop.set_target(self.provider, self.model, self.level)
            from ..agent.session import SessionMeta

            self.session = Session(SessionMeta(**{**meta}), self.session_path(sid))
            self.loop.session = self.session
        self.loop.load_history(events)
        self._replay(events)

    def session_path(self, sid: str) -> Path:
        from ..zconfig import ZTUI_SESSIONS_DIR

        return ZTUI_SESSIONS_DIR / f"{sid}.jsonl"

    def _replay(self, events: list[dict[str, Any]]) -> None:
        tool_names: dict[str, str] = {}
        for ev in events:
            if ev.get("type") != "message":
                continue
            role, content = ev.get("role"), ev.get("content", [])
            if role == "user":
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                if texts:
                    self._mount(UserMsg("\n".join(texts)))
                for b in content:
                    if b.get("type") == "tool_result":
                        name = tool_names.get(b.get("tool_use_id", ""), "tool")
                        header = f"{name} → {'error' if b.get('is_error') else 'ok'}"
                        self._notice(header)
            elif role == "assistant":
                for b in content:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        msg = AssistantMsg()
                        msg.append(b["text"])
                        msg.finish()
                        self._mount(msg)
                    elif b.get("type") == "tool_use":
                        spec = REGISTRY.get(b.get("name", ""))
                        summary = spec.summarize(b.get("input", {})) if spec else ""
                        self._notice(f"{b.get('name', 'tool')}({summary})")
                        tool_names[b.get("id", "")] = b.get("name", "tool")

    # -- keys ----------------------------------------------------------------

    # -- vim mode ------------------------------------------------------------

    def _vim_intercept(self, event) -> bool:
        if not self.vim_enabled:
            return False
        key = event.key
        if self.vim_mode == "INSERT":
            if key == "escape" and not self._agent_busy and not self._menu_visible():
                self.vim_mode = "NORMAL"
                self._refresh_status()
                return True
            return False
        # NORMAL mode
        row, col = self._input.cursor_location
        lines = self._input.text.split("\n")
        line = lines[row] if row < len(lines) else ""

        def finish(input_rows=None, r=row, c=col) -> bool:
            if input_rows is not None:
                self._input.text = "\n".join(input_rows)
            max_r = len((input_rows or lines)) - 1
            r = max(0, min(r, max_r))
            target = (input_rows or lines)[r] if (input_rows or lines) else ""
            self._input.cursor_location = (r, max(0, min(c, len(target))))
            return True

        moves = {
            "h": lambda: (None, row, max(0, col - 1)),
            "l": lambda: (None, row, min(len(line), col + 1)),
            "j": lambda: (None, row + 1, col),
            "k": lambda: (None, row - 1, col),
            "0": lambda: (None, row, 0),
            "$": lambda: (None, row, len(line)),
            "dollar_sign": lambda: (None, row, len(line)),
            "w": lambda: (None, *self._vim_word_forward(lines, row, col)),
            "b": lambda: (None, *self._vim_word_backward(lines, row, col)),
        }
        if key in moves:
            rows, r, c = moves[key]()
            return finish(input_rows=rows, r=r, c=c)
        if key == "x":
            if col < len(line):
                lines[row] = line[:col] + line[col + 1:]
            return finish(input_rows=lines, c=min(col, len(lines[row]) - 1))
        if key == "y":
            self._vim_register = line
            self._notice("yanked line")
            return True
        if key == "p":
            if self._vim_register:
                lines.insert(row + 1, self._vim_register)
                return finish(input_rows=lines, r=row + 1, c=0)
            return True
        if key == "i":
            self.vim_mode = "INSERT"
            self._refresh_status()
            return True
        if key == "I":
            self.vim_mode = "INSERT"
            self._refresh_status()
            return finish(c=0)
        if key == "a":
            self.vim_mode = "INSERT"
            self._refresh_status()
            return finish(c=min(len(line), col + 1))
        if key == "A":
            self.vim_mode = "INSERT"
            self._refresh_status()
            return finish(c=len(line))
        # enter still submits; anything else is consumed silently
        return key != "enter"

    @staticmethod
    def _vim_word_forward(lines: list[str], row: int, col: int) -> tuple[int, int]:
        def is_word(ch: str) -> bool:
            return ch.isalnum() or ch == "_"

        r, c = row, col
        while r < len(lines):
            seg = lines[r]
            i = c + 1 if r == row else 0
            while i < len(seg) and is_word(seg[i]):
                i += 1
            while i < len(seg) and not is_word(seg[i]):
                i += 1
            if i < len(seg):
                return r, i
            r += 1
            c = 0
        return len(lines) - 1, len(lines[-1])

    @staticmethod
    def _vim_word_backward(lines: list[str], row: int, col: int) -> tuple[int, int]:
        def is_word(ch: str) -> bool:
            return ch.isalnum() or ch == "_"

        r, c = row, col
        while r >= 0:
            seg = lines[r]
            i = min(c - 1, len(seg) - 1)
            while i > 0 and not is_word(seg[i]):
                i -= 1
            while i > 0 and is_word(seg[i - 1]):
                i -= 1
            if i >= 0 and (is_word(seg[i]) and (i == 0 or not is_word(seg[i - 1]))):
                return r, i
            r -= 1
            c = len(lines[r]) if r >= 0 else 0
        return 0, 0

    # -- remote phone control (official external relay, GUI-consistent) -------

    async def _toggle_remote(self) -> None:
        if self._relay is not None:
            await self._relay.stop()
            self._relay = None
            self._pairing = None
            self._notice("remote phone control stopped")
            self._refresh_status()
            return
        from uuid import uuid4

        import socket as _socket

        from .relay import RelayClient, RelayPairing, create_pass_hash, create_password

        own = self.zconfig.own
        sid = own.defaults.get("remote_sid")
        pass_hash = own.defaults.get("remote_pass_hash")
        mid = own.defaults.get("remote_mid")
        if not mid:
            mid = str(uuid4())
            own.set_default(remote_mid=mid)
        register_first = not (sid and pass_hash)
        if register_first:
            pass_hash = create_pass_hash(create_password())
            sid = None
        self._pairing = RelayPairing(
            device_sid=sid,
            pass_hash=pass_hash,
            device_mid=mid,
            device_name=_socket.gethostname(),
        )
        self._relay = RelayClient(self._pairing, on_event=self._on_relay_event)
        self._notice("connecting to the official relay…")
        await self._relay.start(register_first)

    async def _on_relay_event(self, kind: str, payload: dict) -> None:
        if kind in ("registered", "authed"):
            from .relay import qr_text

            url = self._pairing.phone_url
            if kind == "registered" and self._pairing.device_sid:
                self.zconfig.own.set_default(
                    remote_sid=self._pairing.device_sid,
                    remote_pass_hash=self._pairing.pass_hash,
                )
            self._mount_qr(qr_text(url))
            notice = (
                "Web 远程控制二维码 — 用手机相机扫码，或在手机上打开链接：\n"
                + url
                + "\n等待手机连接…（再次 /remote 关闭）"
            )
            self._notice(notice)
            self._refresh_status()
        elif kind == "pair_status":
            st = payload.get("pair_status", "")
            if st == "paired":
                self._notice("📱 phone connected — mobile sees this workspace")
            else:
                self._notice(f"pair status: {st} · keep this window open while pairing")
            self._refresh_status()
        elif kind == "data":
            # Next milestone: map mobile actions into ztui commands.
            self._notice(f"[remote frame] {str(payload)[:160]}")
        elif kind == "error":
            self._notify_error(f"remote error: {payload.get('message', '')}")
        elif kind == "relay_error":
            self._notify_error(f"relay error: {payload.get('code', '')}")
            self._relay = None
            self._pairing = None
            self._refresh_status()
        elif kind == "sid_invalid":
            self.zconfig.own.set_default(remote_sid="")
            self._relay = None
            self._pairing = None
            self._notice("stored device invalid — try /remote again to re-register")
            self._refresh_status()

    def _mount_qr(self, text) -> None:
        from rich.text import Text as _Text

        w = Static(text if isinstance(text, _Text) else _Text(text), classes="qr")
        self._mount(w)

    def action_maybe_quit(self) -> None:
        if time.monotonic() - self._quit_armed < 1.5:
            self.exit()
        else:
            self._quit_armed = time.monotonic()
            self._refresh_status(extra="press ctrl+c again to quit")

    def action_toggle_expand(self) -> None:
        blocks = list(self._chat.query(ToolBlock))
        if blocks:
            blocks[-1].toggle_expanded()

    def action_arm_ctrlx(self) -> None:
        self._ctrlx_armed = time.monotonic()
        self._refresh_status(extra="ctrl+k to stop all subagents + the running turn")

    def action_kill_all(self) -> None:
        if time.monotonic() - self._ctrlx_armed > 2.0:
            return
        self._ctrlx_armed = 0.0
        n = self.loop.stop_all_subagents()
        if self._agent_task and not self._agent_task.done():
            self.loop.cancel()
            self._agent_task.cancel()
        self._notice(f"stopped {n} subagent(s) and the running turn")
        self._refresh_status()

    def action_toggle_mode(self) -> None:
        self._cycle_mode("build" if self.mode == "plan" else "plan")

    # -- status --------------------------------------------------------------

    def _refresh_status(self, extra: str = "") -> None:
        from rich.text import Text

        mode_colors = {"plan": "#61afef", "build": "#57ab5a", "yolo": "#e5534b"}
        t = Text()
        t.append(f" {self.model.id}", style="bold #ffffff")
        t.append(f" @ {self.level or 'off'}", style=T.DIM)
        t.append("  ·  ", style="#3c3f4a")
        t.append(self.provider.name, style=T.BODY)
        t.append("  ·  ", style="#3c3f4a")
        t.append(self.mode, style=f"bold {mode_colors.get(self.mode, '#d7d8dd')}")
        if self.vim_enabled:
            t.append("  ·  ", style="#3c3f4a")
            style = f"bold {T.ACCENT}" if self.vim_mode == "NORMAL" else T.DIM
            t.append(self.vim_mode, style=style)
        t.append("  ·  ", style="#3c3f4a")
        t.append(str(self.cwd).replace(str(Path.home()), "~"), style=T.DIM)
        if self._agent_busy:
            frames = "⣾⣽⣻⢿⡿⣟⣯⣷"
            frame = frames[int(time.monotonic() * 8) % len(frames)]
            t.append("  ·  ", style="#3c3f4a")
            t.append(f"{frame} {time.monotonic() - self._run_started:.0f}s", style=T.ACCENT)
        usage = self.loop.usage
        if usage.requests:
            t.append("  ·  ", style="#3c3f4a")
            t.append(usage.fmt(), style=T.DIM)
        if extra:
            t.append("  ·  ", style="#3c3f4a")
            t.append(extra, style="#e0af68")
        if self._pairing is not None and self._pairing.status == "paired":
            t.append("    📱", style="bold")
        self._status.update(t)
        self._hint.update(f" ? /help · shift+tab mode · @ file · ctrl+o expand · {MODE_HINTS[self.mode]}")

    def on_unmount(self) -> None:
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        for server in (self._remote,):
            if server is not None:
                try:
                    asyncio.get_event_loop().create_task(server.stop())
                except Exception:
                    pass
        if self._relay is not None:
            try:
                asyncio.get_event_loop().create_task(self._relay.stop())
            except Exception:
                pass


class AppCallbacks(AgentCallbacks):
    def __init__(self, app: ZtuiApp) -> None:
        self.app = app

    async def on_block_start(self, kind: str) -> None:
        pass

    async def on_block_end(self) -> None:
        self.app.close_text()
        self.app.close_thinking()

    async def on_text_delta(self, text: str) -> None:
        self.app.append_text(text)

    async def on_reasoning_delta(self, text: str) -> None:
        self.app.append_thinking(text)

    async def on_tool_begin(self, tool_id: str, name: str, args: dict) -> None:
        self.app.open_tool(tool_id, name, args)

    async def on_tool_end(self, tool_id: str, name: str, result, note: str = "") -> None:
        self.app.close_tool(tool_id, result, note)

    async def on_permission_ask(self, name: str, args: dict, reason: str) -> str:
        return await self.app.ask_permission(name, args, reason)

    async def on_error(self, message: str) -> None:
        self.app._notify_error(message)

    async def on_usage(self, usage: dict) -> None:
        self.app._refresh_status()

    async def on_compaction(self, info: dict) -> None:
        self.app._notice(
            f"context auto-compacted: {info['replaced']} messages summarized, "
            f"{info['kept']} kept (was ~{info['input_before'] // 1000}k tokens)"
        )

    async def on_sub_progress(self, tool_id: str, sub_id: str, status: str, text: str) -> None:
        block = self.app._tool_blocks.get(tool_id)
        if block is not None:
            block.sub_update(sub_id, status, text)

    async def on_step_finish(self, stop_reason: str) -> None:
        self.app.close_text()
        self.app.close_thinking()

    async def on_turn_finish(self) -> None:
        self.app.close_text()
        self.app.close_thinking()
        self.app._bell()
        self.app._notify_ui("ztui", "turn complete")
