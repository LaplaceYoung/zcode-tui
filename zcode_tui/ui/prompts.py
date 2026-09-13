"""Modal prompts: permission approval, model picker, session picker, workflow/checkpoints/automation panels."""

from __future__ import annotations

from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from .widgets import build_diff_text
from . import theme_tokens as T


class PermissionScreen(ModalScreen[str]):
    """CC-style numbered approval: 1 Yes · 2 Yes, don't ask again · 3 No."""

    OPTIONS = [
        ("yes", "1. Yes"),
        ("always", "2. Yes, don't ask again during this session"),
        ("no", "3. No"),
    ]

    def __init__(self, tool: str, args: dict, reason: str) -> None:
        super().__init__()
        self._tool = tool
        self._args = args
        self._reason = reason

    def compose(self) -> ComposeResult:
        titles = {
            "bash": "Run this command?",
            "write": "Write this file?",
            "edit": "Edit this file?",
            "webfetch": "Fetch this URL?",
        }
        with Vertical(classes="dialog"):
            yield Label(titles.get(self._tool, f"Use {self._tool}?"), classes="dialog-title")
            body = Static(classes="dialog-body")
            yield body
            self._render_body(body)
            if self._reason:
                yield Label(Text(f"⚠ {self._reason}", style="#e0af68"), classes="hintline")
            yield OptionList(
                *[Option(label, id=key) for key, label in self.OPTIONS],
                id="perm-options",
            )

    def _render_body(self, stub: Static) -> Static:
        if self._tool == "bash":
            stub.update(Syntax(self._args.get("command", ""), "bash", theme="ansi_dark"))
        elif self._tool == "url" or self._tool == "webfetch":
            stub.update(Text(self._args.get("url", "")))
        elif self._tool in ("write", "edit"):
            diff = self._args.get("_diff_preview")
            if diff:
                text, hidden = build_diff_text(diff.get("old"), diff.get("new") or "", cap=24)
                stub.update(text)
            else:
                stub.update(Text(str(self._args)[:2000]))
        else:
            stub.update(Text(str(self._args)[:2000]))
        return stub

    def on_mount(self) -> None:
        self.query_one("#perm-options", OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def on_key(self, event) -> None:
        key = event.key
        if key == "1" or key == "y":
            self.dismiss("yes")
        elif key == "2":
            self.dismiss("always")
        elif key == "3" or key == "n" or key == "escape":
            self.dismiss("no")


class ModelPickerScreen(ModalScreen[tuple | None]):
    """Two-step provider/model → reasoning-level picker."""

    def __init__(self, providers, current: tuple[str, str, str | None]) -> None:
        super().__init__()
        self._providers = providers
        self._current = current
        self._picked: tuple | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Select model", classes="dialog-title")
            self._options = OptionList()
            for p in self._providers:
                for mid, m in p.models.items():
                    current = (p.id, mid) == self._current[:2]
                    label = f"{'✔ ' if current else '  '}{m.id}  ·  {p.name}"
                    self._options.add_option(Option(label, id=f"{p.id}|||{mid}"))
            yield self._options

    def on_mount(self) -> None:
        self._options.focus()
        cur_id = "|||".join(self._current[:2])
        for i, opt in enumerate(self._options.options):
            if opt.id == cur_id:
                self._options.highlighted = i
                break

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        pid, mid = str(event.option.id).split("|||", 1)
        provider = next(p for p in self._providers if p.id == pid)
        model = provider.models[mid]
        if model.reasoning_variants:
            self._pick_level(provider, model)
        else:
            self.dismiss((pid, mid, None))

    def _pick_level(self, provider, model) -> None:
        def _done(level: str | None) -> None:
            self.dismiss((provider.id, model.id, level))

        self.app.push_screen(
            LevelPickerScreen(model.reasoning_variants, model.reasoning_default, self._current[2]),
            _done,
        )

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class LevelPickerScreen(ModalScreen[str | None]):
    def __init__(self, variants: list[str], default: str | None, current: str | None) -> None:
        super().__init__()
        self._variants = variants
        self._default = default
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Reasoning level", classes="dialog-title")
            options = OptionList()
            for v in self._variants:
                marks = []
                if v == self._default:
                    marks.append("default")
                if v == self._current:
                    marks.append("current")
                suffix = f"  ({', '.join(marks)})" if marks else ""
                options.add_option(Option(f"{v}{suffix}", id=v))
            options.add_option(Option("none (no reasoning)", id="__none__"))
            yield options

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        v = str(event.option.id)
        self.dismiss(None if v == "__none__" else v)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(self._current)


class ZSessionPickerScreen(ModalScreen[str | None]):
    """Picker over ZCode GUI sessions (from the read-only sqlite adapter)."""

    def __init__(self, sessions) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        import time

        with Vertical(classes="dialog"):
            yield Label("ZCode GUI sessions (read-only resume)", classes="dialog-title")
            options = OptionList()
            if not self._sessions:
                options.add_option(Option("(no GUI sessions found)", id="__none__"))
            for s in self._sessions:
                age = _age(time.time() * 1000 - s.updated_ms)
                model = f"  [{s.model_label}]" if s.model_label else ""
                label = f"{s.title}{model}  ·  {age} ago  ·  zcode"
                options.add_option(Option(label, id=s.id))
            yield options

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        sid = str(event.option.id)
        self.dismiss(None if sid == "__none__" else sid)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class SessionPickerScreen(ModalScreen[str | None]):
    def __init__(self, sessions) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        import time

        with Vertical(classes="dialog"):
            yield Label("Resume a session", classes="dialog-title")
            options = OptionList()
            if not self._sessions:
                options.add_option(Option("(no saved sessions yet)", id="__none__"))
            for s in self._sessions:
                age = _age(time.time() - s.updated)
                label = f"{s.title}   [{s.model_id}]  ·  {age} ago  ·  {s.cwd}"
                options.add_option(Option(label, id=s.id))
            yield options

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        sid = str(event.option.id)
        self.dismiss(None if sid == "__none__" else sid)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class PluginsScreen(ModalScreen[None]):
    """Marketplace plugins and their exposed skills (read-only)."""

    def __init__(self, entries) -> None:
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Plugins (marketplace & cache → skills)", classes="dialog-title")
            if not self._entries:
                yield Label(Text("(no plugins found)", style=DIM))
                return
            for e in self._entries:
                head = Text()
                head.append(f"● {e.name}", style="bold #ffffff")
                head.append(f"  [{e.marketplace}]", style=DIM)
                yield Label(head)
                line = "  " + ("  ".join(f"/{s}" for s in e.skills) if e.skills else "no skills")
                yield Label(Text(line, style=BODY))

    def on_key(self, event) -> None:
        if event.key in ("escape", "enter", "q"):
            self.dismiss(None)


class ThemePickerScreen(ModalScreen[str | None]):
    """Choose a color theme; applies immediately on select."""

    def __init__(self, names: list[str], current: str) -> None:
        super().__init__()
        self._names = names
        self._current = current

    def compose(self) -> ComposeResult:
        from .theme_tokens import PALETTES

        with Vertical(classes="dialog"):
            yield Label("Select theme", classes="dialog-title")
            options = OptionList()
            for name in self._names:
                p = PALETTES[name]
                mark = "✔ " if name == self._current else "  "
                label = Text()
                label.append(mark)
                label.append("██ ", style=p["ACCENT"])
                label.append(f"{name:<14}", style="bold")
                label.append(p["desc"], style=T.DIM)
                options.add_option(Option(label, id=name))
            yield options

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class WorkflowScreen(ModalScreen[str | None]):
    """List dynamic workflow runs (read-only from the dwf journal)."""

    def __init__(self, runs) -> None:
        super().__init__()
        self._runs = runs

    def compose(self) -> ComposeResult:
        from datetime import datetime

        with Vertical(classes="dialog"):
            yield Label("ZCode workflows (read-only · 执行能力待评估)", classes="dialog-title")
            if not self._runs:
                yield Label(Text("(no workflow runs)", style=T.DIM))
                return
            options = OptionList()
            for r in self._runs:
                mark, color = {
                    "completed": ("✔", "#57ab5a"),
                    "failed": ("✘", "#e5534b"),
                    "cancelled": ("◦", T.DIM),
                }.get(r.status, ("⣾", T.ACCENT))
                name = r.name or f"run …{r.run_id[-8:]}"
                tokens = f"{r.spent_tokens / 1e6:.1f}M" if r.spent_tokens >= 1e6 else f"{r.spent_tokens / 1e3:.0f}k"
                when = datetime.fromtimestamp(r.updated_ms / 1000).strftime("%m-%d %H:%M")
                label = f"{mark} {name[:34]:34} {when}  · {tokens:>6} tok · {Path(r.cwd).name}"
                options.add_option(Option(label, id=r.run_id))
            yield options

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class WorkflowDetailScreen(ModalScreen[None]):
    """One workflow run: status, actors, node stats, conclusion."""

    def __init__(self, detail) -> None:
        super().__init__()
        self._d = detail

    def compose(self) -> ComposeResult:
        from datetime import datetime

        d = self._d
        with Vertical(classes="dialog"):
            r = d.run
            colors = {"completed": "#57ab5a", "failed": "#e5534b", "cancelled": T.DIM}
            status_color = colors.get(r.status, T.ACCENT)
            yield Label(Text(f"workflow {r.run_id}", style="bold #ffffff"), classes="dialog-title")
            when = datetime.fromtimestamp(r.created_ms / 1000).strftime("%Y-%m-%d %H:%M")
            yield Label(Text(
                f"{r.name or '(unnamed)'} · status: {r.status} · {r.spent_tokens:,} tokens\n"
                f"cwd: {r.cwd} · created {when}",
                style=T.BODY,
            ))
            yield Label(Text(f"● {r.status}", style=f"bold {status_color}"))
            if d.actors:
                yield Label(Text(f"\nactors（{len(d.actors)}）:", style="bold"))
                for a in d.actors[:12]:
                    model = a.model.rsplit("/", 1)[-1]
                    yield Label(Text(f"  · {a.name[:30]}  ({model})", style=T.BODY))
            if d.node_kinds:
                kinds = " · ".join(f"{k}×{n}" for k, n in sorted(d.node_kinds.items()))
                statuses = " · ".join(f"{s}×{n}" for s, n in sorted(d.node_status.items()))
                yield Label(Text(f"\nnodes: {kinds}\nstatus: {statuses}", style=T.DIM))
            if d.conclusion:
                text = d.conclusion if len(d.conclusion) <= 1200 else d.conclusion[:1200] + "…"
                yield Label(Text(f"\n结果摘要:\n{text}", style=T.BODY))
            if d.failure:
                yield Label(Text(f"\nfailure: {d.failure[:600]}", style="#e5534b"))
            yield Label(Text("执行/派生 workflow 的能力评估后实现（M-next）。", style=T.DIM))

    def on_key(self, event) -> None:
        if event.key in ("escape", "enter", "q"):
            self.dismiss(None)


class CheckpointsScreen(ModalScreen[str | None]):
    """List checkpoints (snapshot inventories) for the current workspace."""

    def __init__(self, workspace: str, checkpoints) -> None:
        super().__init__()
        self._workspace = workspace
        self._checkpoints = checkpoints

    def compose(self) -> ComposeResult:
        from datetime import datetime

        from ..agent.zcheckpoints import fmt_bytes

        with Vertical(classes="dialog"):
            yield Label(f"Checkpoints — {self._workspace}", classes="dialog-title")
            if not self._checkpoints:
                yield Label(Text("(no checkpoints for this workspace)", style=T.DIM))
                return
            options = OptionList()
            for cp in self._checkpoints:
                when = datetime.fromtimestamp(cp.created_ms / 1000).strftime("%m-%d %H:%M")
                mark = "✔" if cp.accepted else " "
                label = (
                    f"{mark} {when}  ·  {cp.file_count:>6,} files · {fmt_bytes(cp.total_bytes):>9}"
                    f"  ·  {cp.full_hash[:10]}"
                )
                options.add_option(Option(label, id=cp.full_hash))
            yield options
            yield Label(Text("enter→查看漂移报告 · checkpont 内容仅存于 zcode 云端，本地仅含清单", style=T.DIM))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class CheckpointDetailScreen(ModalScreen[None]):
    """Drift report for one checkpoint."""

    def __init__(self, cp, drift, extra_configs) -> None:
        super().__init__()
        self._cp = cp
        self._drift = drift
        self._extra = extra_configs

    def compose(self) -> ComposeResult:
        from datetime import datetime

        from ..agent.zcheckpoints import fmt_bytes

        cp = self._cp
        with Vertical(classes="dialog"):
            when = datetime.fromtimestamp(cp.created_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
            yield Label(f"Checkpoint {cp.full_hash[:12]} · {when}", classes="dialog-title")
            from rich.text import Text

            yield Label(Text(
                f"{cp.file_count:,} files · {fmt_bytes(cp.total_bytes)} · "
                f"{'accepted' if cp.accepted else 'not accepted'} · "
                f"workspace: {cp.workspace_path}"
            ))
            d = self._drift
            yield Label(Text(
                f"\n漂移（对比当前工作区）：缺失 {len(d.missing)} · 字节数变化 {len(d.changed_size)} · 新增 {len(d.added)}",
                style="bold",
            ))
            shown = 0
            for p in d.missing[:8]:
                yield Label(Text(f"  ✘ 缺失  {p}", style="#e5534b"))
                shown += 1
            for path, old, new in d.changed_size[:8]:
                yield Label(Text(f"  ~ 变化  {path}  ({fmt_bytes(old)} → {fmt_bytes(new)})", style=T.ACCENT))
                shown += 1
            if d.truncated:
                yield Label(Text(f"  …（仅显示前 {shown} 条）", style=T.DIM))
            if self._extra:
                yield Label(Text(f"\n附加配置清单（{len(self._extra)} 项）：", style="bold"))
                for cfg in self._extra:
                    yield Label(Text(
                        f"  {cfg.path}  ({fmt_bytes(cfg.size_bytes)})  ← {cfg.source}",
                        style=T.BODY,
                    ))
            yield Label(Text(
                "\n说明：manifest 仅含路径与字节数（内容加密同步在 zcode 云端），"
                "本地无法恢复文件内容；恢复需通过 ZCode GUI/云端。",
                style=T.DIM,
            ))

    def on_key(self, event) -> None:
        if event.key in ("escape", "enter", "q"):
            self.dismiss(None)


class AutomationsScreen(ModalScreen[None]):
    """Read-only panel listing ZCode scheduled automations."""

    def __init__(self, rows) -> None:
        super().__init__()
        self._rows = rows

    def compose(self) -> ComposeResult:
        from ..agent.zautomations import fmt_ms

        with Vertical(classes="dialog"):
            yield Label("ZCode scheduled automations (read-only, managed by the GUI)", classes="dialog-title")
            if not self._rows:
                yield Label(Text("(no automations)", style=T.DIM))
                return
            for r in self._rows:
                state = "running" if r.running else ("on" if r.enabled else "off")
                state_style = {"running": "#57ab5a", "on": "#61afef", "off": T.DIM}[state]
                t = Text()
                t.append(f"● {state:8}", style=f"bold {state_style}")
                t.append(f" {r.title[:46]:46}", style="#d7d8dd")
                t.append(f" {r.cron:16}", style=T.ACCENT)
                t.append(f" next {fmt_ms(r.next_run_ms):>9}", style=T.BODY)
                last = f"last {fmt_ms(r.last_run_ms)} {r.last_outcome}"
                t.append(f"  {last}", style=T.DIM)
                yield Label(t)
                if r.last_error:
                    yield Label(Text(f"   last error: {r.last_error[:110]}", style="#e5534b"))

    def on_key(self, event) -> None:
        if event.key in ("escape", "enter", "q"):
            self.dismiss(None)


def _age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"
