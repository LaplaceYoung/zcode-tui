"""Headless TUI smoke test: real app, real model, Textual Pilot."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zcode_tui.agent.session import list_sessions
from zcode_tui.ui.app import ZtuiApp
from zcode_tui.ui.prompts import PermissionScreen
from zcode_tui.ui.widgets import AssistantMsg, ToolBlock, UserMsg, WelcomeBanner
from zcode_tui.zconfig import ZConfig

CWD = Path(__file__).parent.parent.resolve()


async def wait_idle(app: ZtuiApp, timeout: float = 240.0) -> None:
    for i in range(int(timeout * 4)):
        if not app._agent_busy and (app._agent_task is None or app._agent_task.done()):
            await asyncio.sleep(0.4)
            return
        if i % 40 == 0 and i:
            task = app._agent_task
            print(
                f"    [wait {i//4}s] busy={app._agent_busy} task={task and not task.done()} "
                f"msgs={len(app.loop.messages)}",
                flush=True,
            )
            if task and not task.done():
                for f in task.get_stack(limit=6):
                    import traceback

                    traceback.print_stack(f)
        await asyncio.sleep(0.25)
    raise TimeoutError("agent did not finish")


async def main() -> None:
    zconfig = ZConfig()
    zconfig.own.set_default(mode="build")  # hermetic: mode persists across apps
    app = ZtuiApp(zconfig, CWD)
    async with app.run_test(size=(100, 36)) as pilot:
        # 1. banner + input focus
        assert app.query_one(WelcomeBanner), "banner missing"
        assert app.focused is app._input, f"focus on {app.focused}"
        print("✓ banner mounted, input focused")

        # 2. submit a real prompt through the real submit path
        import traceback
        orig_sub = ZtuiApp._submitted

        async def logged_sub(self, event):
            print(f"SUBMITTED: {event.text[:50]!r}", flush=True)
            try:
                await orig_sub(self, event)
                print("submitted handler ok", flush=True)
            except Exception:
                print("SUBMITTED CRASHED:", flush=True)
                traceback.print_exc()
                raise

        ZtuiApp._submitted = logged_sub
        orig_run = ZtuiApp._run_agent

        async def logged_run(self, text):
            print("RUN_AGENT start", flush=True)
            try:
                await orig_run(self, text)
                print("RUN_AGENT done", flush=True)
            except Exception:
                print("RUN_AGENT CRASHED:", flush=True)
                traceback.print_exc()
                raise

        ZtuiApp._run_agent = logged_run
        await pilot.click("#input")
        pilot.app._input.text = (
            "Use the glob tool with pattern 'zcode_tui/*.py' to list python "
            "files, then answer with just the count."
        )
        await pilot.press("enter")
        await wait_idle(app)
        tool_blocks = list(app._chat.query(ToolBlock))
        assert tool_blocks, "no ToolBlock rendered"
        assert tool_blocks[0]._done, "tool block not finished"
        texts = list(app._chat.query(AssistantMsg))
        assert texts and texts[-1]._text.strip(), "no assistant answer"
        assert list(app._chat.query(UserMsg)), "user message not rendered"
        assert app.loop.usage.requests >= 1, "no usage recorded"
        print(f"✓ tool turn: {tool_blocks[0].tool_name} block done, answer: "
              f"{texts[-1]._text.strip()[:60]!r}, usage: {app.loop.usage.fmt()}")

        # 3. session must have been persisted
        sessions = list_sessions()
        assert sessions and sessions[0].title, "session not persisted"
        sid = sessions[0].id
        print(f"✓ session persisted: {sid} titled {sessions[0].title[:40]!r}")

        # 4. permission modal for a mutating write (rejected with key '3')
        app._input.text = "Write the word TEST into a file named zz_perm_check.txt"
        await pilot.press("enter")
        for _ in range(600):
            if isinstance(app.screen, PermissionScreen):
                break
            await asyncio.sleep(0.25)
        assert isinstance(app.screen, PermissionScreen), "permission modal missing"
        print("✓ permission modal appeared for write")
        await pilot.press("3")
        await wait_idle(app)
        assert not (CWD / "zz_perm_check.txt").exists(), "file was written despite rejection"
        print("✓ rejection honored, no file written")

        # 5. /model opens the picker, escape closes
        app._input.text = "/model"
        await pilot.press("enter")
        await pilot.pause(0.5)
        from zcode_tui.ui.prompts import ModelPickerScreen
        assert isinstance(app.screen, ModelPickerScreen), f"/model screen: {app.screen}"
        await pilot.press("escape")
        await pilot.pause(0.3)
        print("✓ /model picker opens/closes")

        # 6. shift+tab toggles plan/build
        before = app.mode
        await pilot.press("shift+tab")
        await pilot.pause(0.3)
        assert app.mode != before, "mode did not toggle"
        await pilot.press("shift+tab")
        await pilot.pause(0.3)
        assert app.mode == before, "mode did not toggle back"
        zconfig.own.set_default(mode="build")
        print(f"✓ mode toggles ({before} ↔ plan)")

        # 7. resume the persisted session in a fresh app instance
    app2 = ZtuiApp(zconfig, CWD, resume_id=sid)
    async with app2.run_test(size=(100, 36)):
        for _ in range(80):
            if app2.loop.messages:
                break
            await asyncio.sleep(0.1)
        assert app2.loop.messages, "resume restored no messages"
        assert any(m["role"] == "user" for m in app2.loop.messages)
        assert any(
            b.get("type") == "tool_use"
            for m in app2.loop.messages
            for b in m["content"]
            if isinstance(b, dict)
        ), "no tool_use restored"
        print(f"✓ resume restored {len(app2.loop.messages)} messages incl. tool calls")

    print("\nALL TUI SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
