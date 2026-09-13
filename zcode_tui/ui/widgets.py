"""Transcript block widgets — the Claude Code visual vocabulary."""

from __future__ import annotations

import difflib
import os
import random
import time

from rich.markdown import Markdown
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from . import theme_tokens as T


COLLAPSE_LINES = 8
DIFF_CAP = 16

_BARE_URL_RE = __import__("re").compile(r'(?<![(\]"\'])https?://[^\s>"\']+[^\s>"\'.,:;!?)]')


def linkify_markdown(text: str) -> str:
    """Wrap bare URLs in markdown links so Rich emits OSC8 hyperlinks."""
    return _BARE_URL_RE.sub(lambda m: f"[{m.group()}]({m.group()})", text)


def linkify_rich_text(raw: str) -> Text:
    """Build Rich Text with OSC8 hyperlinks for bare URLs in plaintext output."""
    out = Text()
    return _append_linkified(out, raw, "")


def _append_linkified(t: Text, raw: str, base_style: str) -> Text:
    last = 0
    for m in _BARE_URL_RE.finditer(raw):
        if m.start() > last:
            t.append(raw[last : m.start()], style=base_style or None)
        t.append(m.group(), style=f"underline #61afef link {m.group()}")
        last = m.end()
    if last < len(raw):
        t.append(raw[last:], style=base_style or None)
    return t
_THINK_WORDS = [
    "Cogitating", "Pondering", "Cerebrating", "Noodling", "Marinating",
    "Percolating", "Scheming", "Deliberating", "Mulling", "Ruminating",
    "Concocting", "Plotting", "Formulating", "Envisioning", "Synthesizing",
]

# Full-block logo (no box-drawing glyphs), 5 rows, letter boxes Z=8/T=9/U=8/I=3.
LOGO_LINES = [
    "████████ █████████ ██    ██ ██",
    "      ██     ██    ██    ██ ██",
    "     ██      ██    ██    ██ ██",
    "    ██       ██    ██    ██ ██",
    "████████     ██     ██████  ██",
]

# Guardrail: every logo row must be the same cell width.
_LOGO_WIDTH = max(len(line) for line in LOGO_LINES)
assert all(len(line) == _LOGO_WIDTH for line in LOGO_LINES), "logo rows misaligned"


def shorten_path(p: str, cwd: str) -> str:
    try:
        return os.path.relpath(p, cwd)
    except ValueError:
        return p


def build_diff_text(old: str | None, new: str, cap: int = DIFF_CAP) -> tuple[Text, int]:
    """Colored unified-diff body; returns (Text, hidden_line_count)."""
    old_lines = (old or "").splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    body_lines = [l for l in diff if not l.startswith(("---", "+++"))]
    text = Text()
    shown = body_lines[:cap]
    for l in shown:
        if l.startswith("@@"):
            text.append(l + "\n", style=f"bold {T.DIM}")
        elif l.startswith("-"):
            text.append(l + "\n", style="#e5534b")
        elif l.startswith("+"):
            text.append(l + "\n", style="#57ab5a")
        else:
            text.append(l + "\n", style=T.BODY)
    hidden = max(0, len(body_lines) - cap)
    if text.plain.endswith("\n"):
        text.plain = text.plain.rstrip("\n")
    return text, hidden


def truncate_lines(raw: str, cap: int = COLLAPSE_LINES) -> tuple[list[str], int]:
    lines = raw.splitlines()
    if raw and not lines:
        lines = [raw]
    return lines[:cap], max(0, len(lines) - cap)


class WelcomeBanner(Static):
    def __init__(self, version: str, model_label: str, cwd: str) -> None:
        super().__init__(classes="banner")
        widths = 32
        info = [
            ("ztui %s" % version, "bold #ffffff"),
            ("a terminal agent on your ZCode models", T.DIM),
            (model_label, T.BODY),
            (f"cwd: {cwd}", T.BODY),
        ]
        text = Text()
        logo_rows = LOGO_LINES + [""] * max(0, len(info) - len(LOGO_LINES))
        for i, logo in enumerate(logo_rows):
            if i < len(LOGO_LINES):
                color = T.ACCENT if i < 2 else T.ACCENT_LIGHT
                text.append(logo.ljust(widths), style=f"bold {color}")
            else:
                text.append(" " * widths)
            if i < len(info):
                line, style = info[i]
                text.append(line + "\n", style=style)
            else:
                text.append("\n")
        text.append("\n")
        text.append("tips: ", style=f"bold {T.DIM}")
        text.append("/help commands · shift+tab plan/build · @ mentions files · esc interrupts · ctrl+f search", style=T.DIM)
        self.update(text)


class UserMsg(Static):
    def __init__(self, text: str, queued: bool = False) -> None:
        super().__init__(classes="user-msg")
        t = Text()
        t.append("> ", style=f"bold {T.ACCENT}")
        lines = text.splitlines() or [""]
        t.append(lines[0], style="bold #ffffff")
        for line in lines[1:]:
            t.append("\n  " + line, style="bold #ffffff")
        if queued:
            t.append("   · queued", style=T.DIM)
        self.update(t)


class AssistantMsg(Horizontal):
    """Streaming assistant text with the ● bullet gutter."""

    def __init__(self) -> None:
        super().__init__(classes="assistant")
        self._bullet = Static(T.GLYPHS["bullet"], classes="bullet")
        self._body = Static("", classes="content")
        self._parts: list[str] = []
        self._last_render = 0.0
        self.finished = False

    def compose(self):
        yield self._bullet
        yield self._body

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def append(self, delta: str) -> None:
        self._parts.append(delta)
        now = time.monotonic()
        # Adaptive gate: short replies stay snappy (50ms), long ones back off
        # to keep total re-render cost bounded (~12/s regardless of length).
        total = sum(len(p) for p in self._parts)
        gate = 0.05 if total < 4000 else 0.12
        if now - self._last_render >= gate:
            self._render_stream()
            self._last_render = now

    def finish(self) -> None:
        self.finished = True
        self._render_markdown()

    def _render_stream(self) -> None:
        joined = "".join(self._parts)
        if joined.strip():
            self._body.update(Text(joined + f" {T.GLYPHS['cursor']}", style=T.BODY))
        else:
            self._body.update("")

    def _render_markdown(self) -> None:
        joined = self.text
        if joined.strip():
            self._body.update(Markdown(linkify_markdown(joined)))
        else:
            self._body.update("")


class ThinkingMsg(Vertical):
    def __init__(self) -> None:
        super().__init__(classes="thinking")
        self._header = Static(classes="tool-footer")
        self._body = Static("", classes="tool-body")
        self._parts: list[str] = []
        self._last_render = 0.0
        self.expanded = True
        self.finished = False
        self._word = random.choice(_THINK_WORDS)

    def compose(self):
        yield self._header
        yield self._body

    @property
    def _text(self) -> str:
        return "".join(self._parts)

    def append(self, delta: str) -> None:
        self._parts.append(delta)
        now = time.monotonic()
        if now - self._last_render > 0.12:
            self._render_header(running=True)
            self._body.update(Text(self._tail(), style="italic"))
            self._last_render = now

    def tick(self) -> None:
        if not self.finished:
            self._render_header(running=True)

    def finish(self) -> None:
        self.finished = True
        self.expanded = False
        self._render_header(running=False)
        self._body.update(Text(self._tail(), style="italic"))

    def _tail(self, n: int = 4) -> str:
        lines = self._text.strip().splitlines()
        if self.expanded:
            return self._text.strip()
        return "\n".join(lines[-n:]) if lines else ""

    def _render_header(self, running: bool) -> None:
        if running:
            frame = (T.GLYPHS["think"], T.GLYPHS["think_alt"])[int(time.monotonic() * 6) % 2]
            self._header.update(Text(f"{frame} {self._word}…", style=f"italic {T.ACCENT_LIGHT}"))
        else:
            words = len(self._text.split())
            self._header.update(Text(f"{T.GLYPHS['think']} Thought for a while ({words} words)", style=f"italic {T.DIM}"))


class ToolBlock(Vertical):
    """One tool call: header with status, collapsible └ body."""

    def __init__(self, tool_id: str, name: str, summary: str) -> None:
        super().__init__(classes="tool")
        self.tool_id = tool_id
        self.tool_name = name
        self.summary = summary
        self._header = Static(classes="tool-header")
        self._body = Static("", classes="tool-body")
        self._footer = Static(classes="tool-footer")
        self._header_text = Text()
        self._started = time.monotonic()
        self._done = False
        self.expanded = False
        self._hidden = 0
        self._raw_output = ""
        self._diff: tuple[Text, int] | None = None
        self._diff_args: tuple[str | None, str] | None = None
        self._diff_path: str = ""
        self._sub: dict[str, tuple[str, str]] = {}
        self._sub_order: list[str] = []

    def sub_update(self, sub_id: str, status: str, text: str) -> None:
        """Live nested sub-agent line for this block (task tool)."""
        if sub_id:
            key = sub_id
            if key not in self._sub:
                self._sub_order.append(key)
        else:
            key = f"event-{len(self._sub_order)}"
            self._sub_order.append(key)
        self._sub[key] = (status, text[:160])
        if not self._done:
            self._render_sub_running()

    def _render_sub_running(self) -> None:
        t = Text()
        for sid in self._sub_order[-12:]:
            status, text = self._sub[sid]
            if status == "running":
                t.append(f"  {T.GLYPHS['running']} {text}\n", style=T.DIM)
            elif status == "done":
                t.append(f"  {T.GLYPHS['check']} {text}\n", style="#57ab5a")
            else:
                t.append(f"  {T.GLYPHS['cross']} {text}\n", style="#e5534b")
        if t.plain.endswith("\n"):
            t.plain = t.plain.rstrip("\n")
        self._body.update(t)

    def _sub_finished_text(self) -> Text:
        t = Text()
        t.append("sub-agent activity:\n", style=T.DIM)
        for sid in self._sub_order:
            status, text = self._sub[sid]
            mark, color = {"running": (T.GLYPHS["running"], T.DIM), "done": (T.GLYPHS["check"], "#57ab5a")}.get(status, (T.GLYPHS["cross"], "#e5534b"))
            t.append(f"  {mark} {text}\n", style=color)
        if t.plain.endswith("\n"):
            t.plain = t.plain.rstrip("\n")
        return t

    def compose(self):
        yield self._header
        yield self._body
        yield self._footer

    def on_mount(self) -> None:
        # mount is deferred; a fast tool may already have finished before this fires.
        if not self._done:
            self._render_running()

    def tick(self) -> None:
        if not self._done:
            self._render_running()

    def _elapsed(self) -> str:
        return f"{time.monotonic() - self._started:.0f}s"

    def _render_running(self) -> None:
        spin = T.GLYPHS["spin"]
        frame = spin[int(time.monotonic() * 8) % len(spin)]
        t = Text()
        t.append(f"{T.GLYPHS['bullet']} ", style=f"bold {T.ACCENT}")
        t.append(f"{self.tool_name}(", style="bold")
        t.append(self.summary, style=T.BODY)
        t.append(")", style="bold")
        t.append(f"  {frame} {self._elapsed()} · esc to interrupt", style=T.DIM)
        self._header.update(t)

    def finish(self, is_error: bool, output: str, note: str, meta: dict) -> None:
        self._done = True
        self._raw_output = output
        mark = "×" if is_error else "✓"
        mark_color = "#e5534b" if is_error else "#57ab5a"
        t = Text()
        t.append(f"{T.GLYPHS['bullet']} ", style=f"bold {T.ACCENT}")
        t.append(f"{self.tool_name}(", style="bold")
        t.append(self.summary, style=T.BODY)
        t.append(")", style="bold")
        t.append(f" {mark}", style=f"bold {mark_color}")
        duration = meta.get("duration_s")
        if duration is not None:
            t.append(f" {duration}s", style=T.DIM)
        if note == "denied":
            t.append(" · denied by mode", style="#e5534b")
        elif note == "rejected":
            t.append(" · rejected by user", style="#e5534b")
        elif note == "always":
            t.append(" · always allowed", style=T.DIM)
        self._header.update(t)
        self._render_body(is_error, meta)

    def _render_body(self, is_error: bool, meta: dict) -> None:
        text = Text("")
        hidden = 0
        diff = meta.get("diff")
        if diff:
            self._diff_args = (diff.get("old"), diff.get("new") or "")
            self._diff_path = diff.get("path", "")
        if self._diff_args is not None:
            cap = 400 if self.expanded else DIFF_CAP
            text, hidden = build_diff_text(*self._diff_args, cap=cap)
            self._diff = (text, hidden)
        elif self._raw_output and self._raw_output not in ("(no output)",):
            show_all = self.expanded or is_error
            cap = 10_000 if show_all else COLLAPSE_LINES
            lines, hidden = truncate_lines(self._raw_output, cap=cap)
            text = Text()
            body_style = "#e5534b" if is_error else T.BODY
            for i, line in enumerate(lines):
                text.append(T.GLYPHS["sub"] + " " if i == 0 else "  ", style=T.DIM)
                _append_linkified(text, line + "\n", body_style)
            if text.plain.endswith("\n"):
                text.plain = text.plain.rstrip("\n")
        else:
            text = Text("")
            hidden = 0
        self._hidden = hidden
        final = Text()
        if self._sub:
            final.append_text(self._sub_finished_text())
            final.append("\n")
        if self._diff is not None:
            if self._diff_path:
                adds = sum(1 for l in (self._diff_args[1] or "").splitlines())
                final.append(f"{self._diff_path}\n", style="bold #FFD43B")
            final.append_text(self._diff[0])
        else:
            final.append_text(text)
        self._body.update(final)
        self._render_footer()
        self._render_footer()

    def _diff_render(self, text: Text | None = None) -> None:
        if self._diff is not None:
            self._body.update(self._diff[0])
        elif text is not None:
            self._body.update(text)

    def _render_footer(self) -> None:
        if self._hidden > 0 and not self.expanded:
            self._footer.update(Text(f"(ctrl+o to expand · {self._hidden} more lines)", style=T.DIM))
        else:
            self._footer.update("")

    def toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self._render_body(False, {})


class TodoBlock(Static):
    def __init__(self, todos: list[dict]) -> None:
        super().__init__(classes="todo")
        self.set(todos)

    def set(self, todos: list[dict]) -> None:
        mark = {"pending": (T.GLYPHS["todo_pending"], T.BODY), "in_progress": (T.GLYPHS["todo_progress"], T.ACCENT), "completed": (T.GLYPHS["todo_done"], "#57ab5a")}
        t = Text()
        t.append(f"{T.GLYPHS['bullet']} Updated todos\n", style=f"bold {T.ACCENT}")
        for item in todos:
            m, color = mark.get(item.get("status", "pending"), (T.GLYPHS["todo_pending"], T.BODY))
            t.append(f"  {m} ", style=color)
            t.append(item.get("content", "") + "\n", style=color)
        if t.plain.endswith("\n"):
            t.plain = t.plain.rstrip("\n")
        self.update(t)


class Notice(Static):
    def __init__(self, text: str, style: str = "notice") -> None:
        super().__init__(classes=style)
        self.update(Text(f"{T.GLYPHS['sub']} {text}", style=T.DIM if style == "notice" else "#e5534b"))


class AttachBlock(Static):
    """Attachment chip: file name + open link (OSC8)."""

    def __init__(self, paths: list) -> None:
        super().__init__(classes="notice")
        t = Text()
        t.append("📎 ", style=T.DIM)
        for i, p in enumerate(paths):
            name = p.name
            uri = p.resolve().as_uri()
            t.append(name, style=f"underline #61afef link {uri}")
            if i < len(paths) - 1:
                t.append("  ", style=T.DIM)
        t.append(f"  ({len(paths)} attached — click name to open)", style=T.DIM)
        self.update(t)
