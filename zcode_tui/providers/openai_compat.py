"""OpenAI-compatible streaming client (chat/completions).

Covers zcode providers with kind "openai-compatible" or "openai"
(aigw, deepseek, openrouter, ...). Translates the canonical
Anthropic-style message representation to OpenAI chat format.
"""

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
    base = provider.base_url
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _translate_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", [])
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "user":
            for b in content:
                if b.get("type") == "image":
                    src = b.get("source", {})
                    if src.get("type") == "base64":
                        media = src.get("media_type", "image/png")
                        out.append({"role": "user", "content": [{
                            "type": "image_url",
                            "image_url": {"url": f"data:{media};base64,{src.get('data', '')}"},
                        }]})
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id", ""),
                        "content": b.get("content", ""),
                    })
        elif role == "assistant":
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            tool_calls = [
                {
                    "id": b.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": b.get("name", ""),
                        "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
                    },
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            if texts or tool_calls:
                out.append(entry)
    return out


def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


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
        "messages": _translate_messages(system, messages),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if model.output_limit:
        body["max_tokens"] = min(model.output_limit, DEFAULT_MAX_TOKENS)
    if tools:
        body["tools"] = _translate_tools(tools)
    apply_reasoning(body, "openai-compatible", level, model)
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
    headers = {"authorization": f"Bearer {provider.api_key}", "content-type": "application/json"}
    usage: dict[str, int] = {}
    open_tools: dict[int, dict[str, str]] = {}
    had_tool_calls = False

    async with get_client().stream(
        "POST", _endpoint(provider), json=body, headers=headers
    ) as resp:
        if resp.status_code != 200:
            text = (await resp.aread()).decode("utf-8", "replace")
            yield {"type": "error", "message": f"HTTP {resp.status_code}: {text[:2000]}"}
            return
        text_open = False
        think_open = False
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if data.get("error"):
                err = data["error"]
                yield {"type": "error", "message": err.get("message", str(err))[:1000]}
                return
            if u := data.get("usage"):
                usage = {
                    "input_tokens": u.get("prompt_tokens", 0),
                    "output_tokens": u.get("completion_tokens", 0),
                    "cache_read_input_tokens": u.get("prompt_cache_hit_tokens", 0),
                }
            for choice in data.get("choices", []):
                delta = choice.get("delta", {})
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    if not think_open:
                        yield {"type": "reasoning_start"}
                        think_open = True
                    yield {"type": "reasoning_delta", "text": reasoning}
                text = delta.get("content")
                if text:
                    if think_open:
                        yield {"type": "block_end", "kind": "thinking"}
                        think_open = False
                    if not text_open:
                        yield {"type": "text_start"}
                        text_open = True
                    yield {"type": "text_delta", "text": text}
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    if text_open:
                        yield {"type": "block_end", "kind": "text"}
                        text_open = False
                    had_tool_calls = True
                    if idx not in open_tools:
                        open_tools[idx] = {"id": tc.get("id") or f"call_{idx}", "name": ""}
                    if tc.get("id"):
                        open_tools[idx]["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        open_tools[idx]["name"] = fn["name"]
                        yield {"type": "tool_use_start", "id": open_tools[idx]["id"], "name": fn["name"]}
                    if fn.get("arguments"):
                        yield {"type": "tool_use_delta", "partial_json": fn["arguments"]}
                if choice.get("finish_reason"):
                    break

    yield {"type": "usage", "usage": dict(usage), "partial": False}
    yield {"type": "finish", "stop_reason": "tool_use" if had_tool_calls else "end_turn"}
