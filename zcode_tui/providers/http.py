"""Shared httpx client: connection/TLS reuse across model calls (TTFT win)."""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
            headers={"user-agent": "ztui/0.1.0"},
        )
    return _client
