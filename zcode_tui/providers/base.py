"""Provider-agnostic streaming event types.

Canonical message representation is Anthropic-style: a message is
{"role": ..., "content": [blocks]} where blocks are text / thinking /
tool_use / tool_result. The OpenAI-compatible provider translates to and
from this representation so the agent loop never sees wire formats.

Stream events are plain dicts:
  {"type": "text_delta",        "text": str}
  {"type": "reasoning_delta",   "text": str}
  {"type": "tool_use_start",    "id": str, "name": str}
  {"type": "tool_use_delta",    "partial_json": str}
  {"type": "tool_use_end"}
  {"type": "usage",             "usage": {...}}
  {"type": "finish",            "stop_reason": str}
  {"type": "error",             "message": str}
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from ..zconfig import ModelInfo, Provider


class ChatStream(Protocol):
    def __call__(
        self,
        provider: Provider,
        model: ModelInfo,
        level: str | None,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]: ...


STOP_TO_TOOLS = "tool_use"
