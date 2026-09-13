"""Desktop notifications for ztui: terminal OSC escape or macOS osascript.

- "term": iTerm2 / kitty / wezTerm OSC-9 notification, written straight to
  /dev/tty so it never corrupts Textual's rendering.
- "mac": macOS notification center via osascript (first use may prompt for
  notification permission).
"""

from __future__ import annotations

import subprocess

MODES = ("off", "term", "mac")


def send(mode: str, title: str, message: str) -> None:
    clean = " ".join(message.split())[:120]
    if mode == "term":
        try:
            with open("/dev/tty", "wb") as tty:
                tty.write(f"\x1b]9;{title}: {clean}\x07".encode())
        except OSError:
            pass
    elif mode == "mac":
        safe_title = title.replace('"', "'")
        safe_msg = clean.replace('"', "'")
        try:
            subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    f'display notification "{safe_msg}" with title "{safe_title}"',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
