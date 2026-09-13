"""Chat input: Enter sends, Shift+Enter / Ctrl+J newline, '/' opens the command menu."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import OptionList, TextArea
from textual.widgets.option_list import Option


class SlashMenu(OptionList):
    DEFAULT_CSS = ""

    class Picked(Message):
        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command


class ChatInput(TextArea):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, placeholder: str = "", **kwargs) -> None:
        super().__init__(placeholder=placeholder or 'Try "explain this repo"', **kwargs)
        self.show_line_numbers = False

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        menu = getattr(self.app, "_menu_intercept", None)
        if menu is not None and menu(event):
            event.stop()
            event.prevent_default()
            return
        vim = getattr(self.app, "_vim_intercept", None)
        if vim is not None and vim(event):
            event.stop()
            event.prevent_default()
            return
        hist = getattr(self.app, "_history_intercept", None)
        if hist is not None and hist(event):
            event.stop()
            event.prevent_default()
            return
        if key == "enter":
            text = self.text.strip()
            if text:
                self.post_message(self.Submitted(text))
            event.stop()
            event.prevent_default()
            return
        if key == "ctrl+j":
            self.insert("\n")
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)

    async def _on_paste(self, event: events.Paste) -> None:
        text = event.text or ""
        lines = text.count("\n") + 1
        if len(text) > 400 or lines > 7:
            marker = f"[pasted {lines} lines · {len(text)} chars] "
            self.insert(marker)
            store = getattr(self.app, "_store_paste_blob", None)
            if store is not None:
                store(marker, text)
            event.stop()
            event.prevent_default()
            return
        await super()._on_paste(event)

    def clear(self) -> None:
        self.text = ""
