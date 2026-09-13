"""Anthropic-protocol streaming client (covers Bigmodel GLM, Z.ai, Kimi...)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from ..reasoning import apply_reasoning
from .http import get_client
from ..zconfig import ModelInfo, Provider

DEFAULT_MAX_TOKENS = 32_768
TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0)


def _endpoint(provider: Provider) -> str:
    if provider.base_url.endswith("/messages"):
        return provider.base_url
    return f"{provider.base_url}/v1/messages"


def build_body(
    provider: Provider,
    model: ModelInfo,
    level: str | None,
    system: str,
    messages: list[dict],
    tools: list[dict],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model.api_id,
        "max_tokens": max(1024, min(model.output_limit, DEFAULT_MAX_TOKENS)),
        "system": system,
        "messages": messages,
        "stream": True,
    }
    if tools:
        body["tools"] = tools
    apply_reasoning(body, "anthropic", level, model)
    return body


async def stream_chat(
    provider: Provider,
    model: ModelInfo,
    level: str | None,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    body = build_body(provider, model, level, system, messages, tools)
    headers = {
        "x-api-key": provider.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    usage: dict[str, int] = {}
    block_type: str | None = None
    stop_reason = "end_turn"
    client = get_client()
    try:
        async with client.stream(
            "POST", _endpoint(provider), json=body, headers=headers
        ) as resp:
            if resp.status_code != 200:
                text = (await resp.aread()).decode("utf-8", "replace")
                yield {"type": "error", "message": f"HTTP {resp.status_code}: {text[:2000]}"}
                return
            event = ""
            async for line in resp.aiter_lines():
                line = line.rstrip("\r")
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                etype = data.get("type", event)

                if etype == "message_start":
                    msg = data.get("message", {})
                    usage.update(msg.get("usage", {}) or {})
                    yield {"type": "usage", "usage": dict(usage), "partial": True}
                elif etype == "content_block_start":
                    block = data.get("content_block", {})
                    block_type = block.get("type")
                    if block_type == "tool_use":
                        yield {
                            "type": "tool_use_start",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                        }
                    elif block_type == "thinking":
                        yield {"type": "reasoning_start"}
                    elif block_type == "text":
                        yield {"type": "text_start"}
                elif etype == "content_block_delta":
                    delta = data.get("delta", {})
                    dt = delta.get("type")
                    if dt == "text_delta":
                        yield {"type": "text_delta", "text": delta.get("text", "")}
                    elif dt == "thinking_delta":
                        yield {"type": "reasoning_delta", "text": delta.get("thinking", "")}
                    elif dt == "input_json_delta":
                        yield {
                            "type": "tool_use_delta",
                            "partial_json": delta.get("partial_json", ""),
                        }
                elif etype == "content_block_stop":
                    yield {"type": "block_end", "kind": block_type}
                    block_type = None
                elif etype == "message_delta":
                    delta = data.get("delta", {})
                    if delta.get("stop_reason"):
                        stop_reason = delta["stop_reason"]
                    usage.update(data.get("usage", {}) or {})
                    yield {"type": "usage", "usage": dict(usage), "partial": True}
                elif etype == "message_stop":
                    pass
                elif etype == "error":
                    err = data.get("error", {})
                    yield {
                        "type": "error",
                        "message": f"{err.get('type', 'error')}: {err.get('message', data)}",
                    }
                    return
    except httpx.HTTPError as e:
        yield {"type": "error", "message": f"network error: {e}"}
        return
    yield {"type": "usage", "usage": dict(usage), "partial": False}
    yield {"type": "finish", "stop_reason": stop_reason}
