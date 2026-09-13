"""Feature check: compaction produces a paired-safe summary."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zcode_tui.agent import compaction
from zcode_tui.agent.loop import AgentCallbacks, AgentLoop
from zcode_tui.zconfig import ZConfig

CWD = Path(__file__).parent.parent.resolve()


async def main() -> None:
    zconfig = ZConfig()
    providers = zconfig.usable_providers
    provider = next(p for p in providers if p.kind == "anthropic")
    model = next(iter(provider.models.values()))
    cb = AgentCallbacks()

    loop = AgentLoop(zconfig, provider, model, "low", CWD, cb)

    # Fabricate a long history: 30 user/assistant text rounds + tool pairs.
    msgs: list[dict] = []
    for i in range(30):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"user question {i} " + "x" * 400}]})
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"assistant answer {i} " + "y" * 400}]})
    # A tool pair at the tail (must stay paired after compaction).
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": "c1", "name": "bash", "input": {"command": "ls"}}]})
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "ok", "is_error": False}]})
    msgs.append({"role": "user", "content": [{"type": "text", "text": "latest real question"}]})
    msgs.append({"role": "assistant", "content": [{"type": "text", "text": "latest real answer"}]})
    loop.messages = list(msgs)

    info = await compaction.compact(loop, force=True)
    print("compact info:", info)
    assert info, "compaction skipped"
    first = loop.messages[0]
    assert "[CONTEXT COMPACTED]" in first["content"][0]["text"], "summary marker missing"
    # Tail safety: no message may be an orphaned tool result without matching call earlier.
    ids = {b.get("id") for m in loop.messages for b in m["content"] if b.get("type") == "tool_use"}
    orphans = [
        b for m in loop.messages for b in m["content"]
        if b.get("type") == "tool_result" and b.get("tool_use_id") not in ids
    ]
    print("orphaned tool_results:", len(orphans))
    assert not orphans, "orphaned tool result in kept tail"
    print("tail kept:", info["kept"], "| replaced:", info["replaced"])
    print("summary preview:", first["content"][0]["text"][:300].replace("\n", " "))
    follow = await loop.user_turn("用一句话说：我还在继续。")
    print("✓ follow-up turn completes after compaction")


if __name__ == "__main__":
    asyncio.run(main())
