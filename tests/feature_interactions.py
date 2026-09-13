"""Feature checks: ! shell prefix, queued messages, history navigation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zcode_tui.ui.app import ZtuiApp
from zcode_tui.ui.widgets import ToolBlock, UserMsg
from zcode_tui.zconfig import ZConfig
from smoke_tui import wait_idle

CWD = Path(__file__).parent.parent.resolve()


async def main() -> None:
    zconfig = ZConfig()
    zconfig.own.set_default(mode="build")
    app = ZtuiApp(zconfig, CWD)
    async with app.run_test(size=(110, 40)) as pilot:
        # 1. ! shell prefix executes locally and injects into conversation
        app._input.text = "!echo bang-test-123"
        await pilot.press("enter")
        await asyncio.sleep(2.0)
        shell_msgs = [m for m in app.loop.messages
                      if any("bang-test-123" in str(b.get("text", ""))
                             for b in (m.get("content") or []) if isinstance(b, dict))]
        assert shell_msgs, "shell output not injected into loop messages"
        assert any(isinstance(b, ToolBlock) for b in app._chat.query(ToolBlock)), "no shell block rendered"
        print("✓ ! prefix executes and injects output")

        # 2. While busy, a second message queues, then auto-runs
        app._input.text = "Run `ls -1` via bash, briefly."
        await pilot.press("enter")
        await asyncio.sleep(1.0)
        assert app._agent_busy, "agent not busy"
        app._input.text = "second queued message — reply with just: done"
        await pilot.press("enter")
        await asyncio.sleep(0.3)
        assert len(app._queue) == 1, f"queue={app._queue}"
        queued_marks = [w for w in app._chat.query(UserMsg)]
        assert any("queued" in str(w.content) for w in app._chat.query(UserMsg)), "no queued marker"
        await wait_idle(app, timeout=300)
        assert not app._queue, "queue did not drain"
        # the second turn must have actually run
        texts = [str(b.get("text","")) for m in app.loop.messages
                 for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type")=="text"]
        assert any("second queued message" in t for t in texts), "queued turn never dispatched"
        print("✓ queued message auto-ran after the first turn")

        # 3. history navigation
        app._input.text = ""
        await pilot.press("up")
        await pilot.pause(0.2)
        assert "second queued message" in app._input.text, f"history recall failed: {app._input.text!r}"
        await pilot.press("down")
        await pilot.pause(0.2)
        print("✓ input history recall works")

        print("\nALL INTERACTION FEATURES PASSED")


if __name__ == "__main__":
    asyncio.run(main())
