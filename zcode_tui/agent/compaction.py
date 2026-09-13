"""Context compaction: summarize history when it nears the model's limit."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .loop import AgentLoop

AUTO_THRESHOLD = 0.85
KEEP_TAIL = 6  # verbatim messages kept at the end when possible
SUMMARY_MAX_TOKENS = 2048

SUMMARIZE_PROMPT = """You produce a conversation state summary so an agent can continue work
after losing its earlier context. Summarize the conversation so far for
another instance of yourself, covering:

1. Task & goal: what the user asked for, current acceptance criteria.
2. Progress: what has been done, what's in flight, what's left.
3. Key decisions and constraints the user specified.
4. Important files, paths, commands, identifiers, and their current state.
5. Any errors hit and how they were resolved.

Be concrete (paths, function names, values). No filler. Chinese if the
conversation is in Chinese, English otherwise. Aim for under 2500 words.
"""


def estimate_input_tokens(loop: "AgentLoop") -> int:
    """Best-effort current prompt size: last API-measured input, else chars/4."""
    if loop.usage.history:
        last = loop.usage.history[-1]
        total = last["input"] + last["cache_read"] + last["cache_write"]
        if total:
            return total
    chars = sum(
        len(str(b.get("text", ""))) + len(str(b.get("input", ""))) + len(str(b.get("content", "")))
        for m in loop.messages
        for b in (m.get("content") or [])
        if isinstance(b, dict)
    )
    return chars // 4 + 3000  # system prompt & tool schemas overhead


def should_compact(loop: "AgentLoop") -> bool:
    limit = loop.model.context_limit
    if not limit:
        return False
    return estimate_input_tokens(loop) > limit * AUTO_THRESHOLD


def _safe_cut_index(messages: list[dict[str, Any]], keep: int) -> int:
    """Cut before `keep` tail messages, nudged forward so the tail never starts
    with an orphaned tool_result or an isolated assistant tool-call turn."""
    idx = max(0, len(messages) - keep)

    def is_tool_result_msg(m: dict) -> bool:
        return m.get("role") == "user" and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in (m.get("content") or [])
        )

    def assistant_has_calls(m: dict) -> bool:
        return m.get("role") == "assistant" and any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in (m.get("content") or [])
        )

    while idx < len(messages):
        current = messages[idx]
        if is_tool_result_msg(current):
            idx += 1  # orphaned results: drop them into the compacted region
            continue
        if idx > 0 and assistant_has_calls(messages[idx - 1]) and current.get("role") == "assistant":
            # assistant followed by assistant (no results seen) — results were dropped; fine
            pass
        # tail must begin with a plain user text turn or an assistant text turn
        if current.get("role") == "assistant" and assistant_has_calls(current):
            # keeping a tool-calling assistant without its results would orphan them
            idx += 1
            continue
        break
    return min(idx, len(messages))


async def compact(loop: "AgentLoop", force: bool = False) -> dict[str, Any] | None:
    """Summarize older history; returns compaction info or None if skipped/failed."""
    if not loop.messages:
        return None
    if not force and not should_compact(loop):
        return None
    if len(loop.messages) <= KEEP_TAIL:
        return None

    from ..providers import anthropic, openai_compat

    stream = (anthropic if loop.provider.kind == "anthropic" else openai_compat).stream_chat
    system = SUMMARIZE_PROMPT
    transcript_lines = []
    for m in loop.messages:
        role = m.get("role", "?")
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                transcript_lines.append(f"{role}: {b.get('text', '')}")
            elif t == "tool_use":
                transcript_lines.append(f"{role} tool_use {b.get('name')}: {str(b.get('input'))[:500]}")
            elif t == "tool_result":
                transcript_lines.append(f"{role} tool_result: {str(b.get('content'))[:1000]}")
    convo = "\n".join(transcript_lines)[-600_000:]
    summary_messages = [{"role": "user", "content": [{"type": "text", "text":
        "Here is the conversation so far. Produce the continuation summary now.\n\n" + convo}]}]

    summary_text = ""
    try:
        async for ev in stream(
            loop.provider, loop.model, None, system, summary_messages, []
        ):
            if ev.get("type") == "text_delta":
                summary_text += ev.get("text", "")
            elif ev.get("type") == "error":
                return None
    except Exception:
        return None
    if not summary_text.strip():
        return None

    cut = _safe_cut_index(loop.messages, KEEP_TAIL)
    replaced = cut
    tail = loop.messages[cut:]
    used_before = estimate_input_tokens(loop)
    summary_msg = {
        "role": "user",
        "content": [{
            "type": "text",
            "text": (
                "[CONTEXT COMPACTED] Earlier history was summarized by the agent:\n\n"
                + summary_text.strip()
                + f"\n\n({replaced} earlier messages condensed; continue from this state.)"
            ),
        }],
    }
    loop.messages = [summary_msg] + tail
    if loop.session:
        loop.session.append({"type": "compaction", "replaced": replaced, "input_before": used_before})
    return {"replaced": replaced, "kept": len(tail), "input_before": used_before}
