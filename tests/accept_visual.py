"""Render a rich tool-use turn headlessly and export a screenshot for visual QA."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zcode_tui.ui.app import ZtuiApp
from zcode_tui.zconfig import ZConfig

CWD = Path(__file__).parent.parent.resolve()
PROMPT = (
    "Plan briefly with the todo tool (2 items), run `ls -1` via bash, then write a file "
    "zz_demo.md containing '# demo\n\nhello from ztui\n'. Finally reply in one short sentence."
)

sys.path.insert(0, str(Path(__file__).parent.parent))
from smoke_tui import wait_idle  # noqa: E402


async def main() -> None:
    zconfig = ZConfig()
    zconfig.own.set_default(mode="build")
    app = ZtuiApp(zconfig, CWD)
    async with app.run_test(size=(110, 42)) as pilot:
        app._input.text = PROMPT
        await pilot.press("enter")
        # Auto-approve the write when the permission modal appears.
        for _ in range(1200):
            from zcode_tui.ui.prompts import PermissionScreen

            if isinstance(app.screen, PermissionScreen):
                await pilot.press("1")
                break
            if not app._agent_busy and app.loop.messages:
                await asyncio.sleep(0.4)
            await asyncio.sleep(0.25)
        await wait_idle(app)

        def content_of(static) -> str:
            c = static.content
            return c.plain if hasattr(c, "plain") else str(c)

        from zcode_tui.ui.widgets import ToolBlock as TB
        for b in app._chat.query(TB):
            print(f"BLOCK {b.tool_name}: {content_of(b._header)!r}", flush=True)
        svg = app.export_screenshot()
        Path("/tmp/ztui-accept-1.svg").write_text(svg)
        # Also a mid-conversation frame: tool blocks + todos
        print("screenshot exported, transcript blocks:")
        from zcode_tui.ui.widgets import ToolBlock, TodoBlock, ThinkingMsg, AssistantMsg

        print("  tool blocks:", [(b.tool_name, b._done) for b in app._chat.query(ToolBlock)])
        print("  todos:", len(list(app._chat.query(TodoBlock))))
        print("  thinking:", len(list(app._chat.query(ThinkingMsg))))
        print("  assistant msgs:", len(list(app._chat.query(AssistantMsg))))
        print("  usage:", app.loop.usage.fmt())


if __name__ == "__main__":
    asyncio.run(main())
