"""Feature check: @file mention completion ui."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zcode_tui.ui.app import ZtuiApp
from zcode_tui.zconfig import ZConfig

CWD = Path(__file__).parent.parent.resolve()


async def main() -> None:
    zconfig = ZConfig()
    zconfig.own.set_default(mode="build")
    app = ZtuiApp(zconfig, CWD)
    async with app.run_test(size=(100, 36)) as pilot:
        app._input.text = "please read @zcode_tui/mai"
        await pilot.pause(0.5)
        assert app._menu_visible(), "mention menu not visible"
        opts = [str(o.id) for o in app._menu.options]
        print("menu options:", opts[:5])
        assert any("main.py" in o for o in opts), f"main.py not in {opts}"
        await pilot.press("tab")
        await pilot.pause(0.3)
        print("after tab:", repr(app._input.text))
        assert "zcode_tui/main.py" in app._input.text
        assert not app._menu_visible(), "menu still visible after accept"
        print("✓ @mention completion works")

        # slash menu still works with keyboard
        app._input.text = "/mo"
        await pilot.pause(0.5)
        assert app._menu_visible()
        await pilot.press("enter")
        await pilot.pause(0.3)
        print("slash accept:", repr(app._input.text))
        assert app._input.text.startswith("/model"), f"got {app._input.text!r}"
        print("✓ slash completion works")


if __name__ == "__main__":
    asyncio.run(main())
