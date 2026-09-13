"""Minimal MCP (Model Context Protocol) stdio client for ztui.

Reads `~/.config/zcode-tui/mcp.json` (compatible with opencode/CC layout):
{
  "mcpServers": {
    "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    "env": {"NO_COLOR": "1"} },
    "my-server":  { "command": "python", "args": ["my_server.py"] }
  }
}

Each server is spawned once per ztui session (lazy), tools are listed via
`tools/list` and exposed locally as flat tools named `mcp_<server>_<tool>`.
Calling goes `tools/call` over JSON-RPC 2.0 framing (newline-delimited JSON
on stdio — the vast majority of MCP servers today).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .tools import REGISTRY, Tool, ToolResult, register

MCP_CONFIG = os.path.expanduser("~/.config/zcode-tui/mcp.json")


@dataclass
class MCPServer:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    proc: asyncio.subprocess.Process | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_id: int = 1
    tools: list[dict[str, Any]] = field(default_factory=list)


class MCPManager:
    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}
        self._loaded = False

    def _load_config(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(MCP_CONFIG):
            return
        try:
            data = json.load(open(MCP_CONFIG))
        except (OSError, ValueError):
            return
        for name, spec in data.get("mcpServers", {}).items():
            if not isinstance(spec, dict) or spec.get("enabled", True) is False:
                continue
            if "url" in spec or (spec.get("type") == "remote" and "url" in spec):
                # HTTP/SSE MCP servers are a bigger surface; explicitly unsupported yet.
                continue
            command = spec.get("command")
            if not command:
                continue
            self.servers[name] = MCPServer(
                name=name,
                command=command,
                args=list(spec.get("args", [])),
                env=dict(spec.get("env", {})),
            )

    async def _start(self, server: MCPServer) -> None:
        if server.proc and server.proc.returncode is None:
            return
        env = dict(os.environ)
        env.update(server.env)
        server.proc = await asyncio.create_subprocess_exec(
            server.command,
            *server.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        await self._rpc(server, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ztui", "version": "0.1.0"},
        })
        await self._notify(server, "notifications/initialized", {})
        server.tools = await self._rpc(server, "tools/list", {})

    async def _notify(self, server: MCPServer, method: str, params: dict) -> None:
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n"
        server.proc.stdin.write(payload.encode())
        await server.proc.stdin.drain()

    async def _rpc(self, server: MCPServer, method: str, params: dict | None) -> Any:
        if not server.proc:
            raise RuntimeError("server not started")
        async with server.lock:
            rid = server.next_id
            server.next_id += 1
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                message["params"] = params
            server.proc.stdin.write((json.dumps(message) + "\n").encode())
            await server.proc.stdin.drain()
            while True:
                line = await server.proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"{server.name} closed stdio during {method}")
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("id") != rid:
                    continue  # ignore server-initiated frames
                if "error" in data:
                    err = data["error"]
                    raise RuntimeError(f"{method} error: {err.get('code')} {err.get('message')}")
                return data.get("result")

    async def start_all(self) -> None:
        self._load_config()
        for server in self.servers.values():
            try:
                await asyncio.wait_for(self._start(server), timeout=20)
            except Exception:
                # Servers that won't start are left unregistered silently;
                # their tools are simply never listed.
                server.proc = None

    async def stop_all(self) -> None:
        for server in self.servers.values():
            if server.proc and server.proc.returncode is None:
                try:
                    server.proc.terminate()
                    await asyncio.wait_for(server.proc.wait(), timeout=3)
                except Exception:
                    server.proc.kill()

    async def list_tool_descriptors(self) -> list[Tool]:
        tools: list[Tool] = []
        for server in self.servers.values():
            if not server.proc or server.proc.returncode is not None:
                continue
            for spec in server.tools.get("tools", []):
                tools.append(self._make_tool(server, spec))
        return tools

    def _make_tool(self, server: MCPServer, spec: dict[str, Any]) -> Tool:
        tool_name = f"mcp_{server.name}_{spec.get('name', 'tool')}"
        desc = spec.get("description") or f"{server.name}/{spec.get('name')}"
        in_schema = spec.get("inputSchema", {"type": "object", "properties": {}})

        async def execute(args: dict[str, Any], ctx) -> ToolResult:
            try:
                result = await self._rpc(server, "tools/call", {
                    "name": spec.get("name"),
                    "arguments": args,
                })
            except Exception as e:
                return ToolResult(f"mcp call failed: {e}", is_error=True)
            if result.get("isError"):
                return ToolResult(_mcp_text(result), is_error=True)
            return ToolResult(_mcp_text(result), meta={"server": server.name, "tool": spec.get("name")})

        return Tool(
            name=tool_name,
            schema={
                "description": f"[MCP:{server.name}] {desc}",
                "input_schema": in_schema,
            },
            run=execute,
            mutating=True,  # treat all MCP calls as needing approval scrutiny anyway
            summarize=lambda a: json.dumps(a.get("input", a), ensure_ascii=False)[:80],
        )


def _mcp_text(result: dict[str, Any]) -> str:
    parts = []
    for content in result.get("content", []):
        t = content.get("type")
        if t == "text":
            parts.append(content.get("text", ""))
        elif t == "resource":
            parts.append(f"[resource] {content.get('uri', '')}")
        else:
            parts.append(f"[{t}] {json.dumps(content, ensure_ascii=False)[:200]}")
    return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)[:4000]


_manager = MCPManager()


async def load_mcp_tools() -> list[Tool]:
    """Boot everything, register served tools into the shared REGISTRY."""
    await _manager.start_all()
    tools = await _manager.list_tool_descriptors()
    for t in tools:
        if t.name in REGISTRY:
            continue
        register(t)
    return tools


async def shutdown_mcp() -> None:
    await _manager.stop_all()


def mcp_status() -> list[tuple[str, int, bool]]:
    """[(server_name, tool_count, running)] for /mcp panel."""
    out = []
    for name, server in _manager.servers.items():
        running = server.proc is not None and server.proc.returncode is None
        out.append((name, len(server.tools.get("tools", [])), running))
    return out
