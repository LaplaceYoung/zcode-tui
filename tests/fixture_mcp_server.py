#!/usr/bin/env python3
"""Tiny MCP stdio fixture server for ztui tests (newline-JSON, JSON-RPC 2.0).

Tools served:
  - clock( ) -> {"content": [{"type":"text","text":"current unix time"}]}
  - add(a, b) -> number
"""

import json
import sys
import time

TOOLS = [
    {
        "name": "clock",
        "description": "Return the current unix time",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
]


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture-mcp", "version": "0.1"},
                },
            })
        elif method == "notifications/initialized":
            pass  # no response
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {})
            name, args = params.get("name"), params.get("arguments", {})
            if name == "clock":
                out = {"content": [{"type": "text", "text": str(int(time.time()))}]}
            elif name == "add":
                out = {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]}
            else:
                send({"jsonrpc": "2.0", "id": msg.get("id"),
                      "error": {"code": -32601, "message": f"unknown tool {name}"}})
                continue
            send({"jsonrpc": "2.0", "id": msg.get("id"), "result": out})


if __name__ == "__main__":
    main()
