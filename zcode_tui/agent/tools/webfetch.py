"""WebFetch tool: fetch a URL and return readable text."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import httpx

from . import Tool, ToolContext, ToolResult, register

MAX_CHARS = 40_000


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", raw)).strip()


async def _run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return ToolResult("url must start with http:// or https://", is_error=True)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "ztui/0.1"})
    except httpx.HTTPError as e:
        return ToolResult(f"fetch failed: {e}", is_error=True)
    ctype = resp.headers.get("content-type", "")
    body = resp.text
    if "html" in ctype:
        parser = _TextExtractor()
        parser.feed(body)
        body = parser.text()
    body = body[:MAX_CHARS]
    if len(resp.text) > MAX_CHARS:
        body += f"\n\n... (truncated at {MAX_CHARS} chars)"
    return ToolResult(f"{url} (HTTP {resp.status_code})\n\n{body}")


register(
    Tool(
        name="webfetch",
        schema={
            "description": "Fetch a URL and return its content as readable text (HTML is stripped).",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        run=_run,
        summarize=lambda a: a.get("url", "")[:80],
    )
)
