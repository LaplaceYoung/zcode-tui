"""Remote phone control for ztui: a token-protected LAN web server.

Opens a tiny HTTP server inside the app loop; phone on the same WiFi opens
http://<lan-ip>:<port>/?t=<token> to watch the session, approve permission
prompts, and send messages. No cloud relay (that is zcode-GUI-only).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import socket
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

SERVER_HOST = "0.0.0.0"
DEFAULT_PORT = 8795


@dataclass
class RemoteState:
    server: asyncio.base_events.Server | None = None
    port: int = DEFAULT_PORT
    token: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    url: str = ""

    @property
    def running(self) -> bool:
        return self.server is not None


def _lan_ip() -> str:
    """Best-effort active LAN IP (macOS ipconfig first, UDP-stun fallback)."""
    import ipaddress
    import subprocess

    def ok(ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
            return addr.is_private or addr.is_link_local
        except ValueError:
            return False

    for iface in ("en0", "en1", "en2"):
        try:
            out = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if out and ok(out):
                return out
        except (OSError, subprocess.SubprocessError):
            continue
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


MobileHandler = Callable[[str, str, Any], Awaitable[tuple[int, Any]]]


class RemoteServer:
    """Minimal asyncio HTTP/1.0 server; routes stay single-method."""

    def __init__(self, host_handler: MobileHandler) -> None:
        self.state = RemoteState()
        self._handler = host_handler

    async def start(self) -> RemoteState:
        self.state.server = await asyncio.start_server(
            self._serve_conn, SERVER_HOST, self.state.port
        )
        ip = _lan_ip()
        self.state.url = f"http://{ip}:{self.state.port}/?t={self.state.token}"
        return self.state

    async def stop(self) -> None:
        srv, self.state.server = self.state.server, None
        if srv:
            srv.close()
            await srv.wait_closed()

    async def _serve_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionResetError):
            writer.close()
            return
        try:
            lines = head.decode("latin-1", "replace").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
            body = b""
            length = int(headers.get("content-length", 0) or 0)
            if length:
                body = await reader.readexactly(length)
            body_json: Any = None
            if body and headers.get("content-type", "").startswith("application/json"):
                try:
                    body_json = json.loads(body)
                except json.JSONDecodeError:
                    body_json = None
            status, payload = await self._route(method.upper(), path, body_json)
        except Exception:
            status, payload = 500, {"error": "server error"}
        if isinstance(payload, str):
            ctype, data = "text/html; charset=utf-8", payload.encode()
        else:
            ctype, data = "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode()
        reasons = {200: "OK", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}
        reason = reasons.get(status, "OK")
        writer.write(
            f"HTTP/1.0 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode("latin-1")
            + data
        )
        try:
            await writer.drain()
        except ConnectionResetError:
            pass
        writer.close()

    async def _route(self, method: str, path: str, body_json: Any) -> tuple[int, Any]:
        if not self._authorized(method, path, body_json):
            return 401, {"error": "unauthorized"}
        return await self._handler(method, path, body_json)

    def _authorized(self, method: str, path: str, body_json: Any) -> bool:
        m = re.search(r"[?&]t=([^&]+)", path)
        if m and secrets.compare_digest(m.group(1), self.state.token):
            return True
        if isinstance(body_json, dict) and secrets.compare_digest(str(body_json.get("t", "")), self.state.token):
            return True
        return False


# ---------------- mobile page ----------------

PAGE = """<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ztui remote</title>
<meta name="theme-color" content="#1a1b1e">
<style>
  :root { --bg:#1a1b1e; --panel:#222328; --text:#d7d8dd; --dim:#6b6d78; --accent:#D97757; --ok:#57ab5a; --err:#e5534b; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system, system-ui, monospace; }
  header { padding:10px 14px; background:var(--panel); border-bottom:1px solid #3c3f4a; position:sticky; top:0; }
  #model { color:var(--accent); font-weight:700; }
  .dim { color:var(--dim); font-size:12px; }
  #busy { float:right; }
  #chat { padding:12px; padding-bottom:220px; white-space:pre-wrap; word-break:break-word; }
  #perm { position:fixed; left:0; right:0; bottom:96px; margin:0 10px; background:var(--panel); border:1px solid var(--accent); border-radius:10px; padding:12px; display:none; z-index:3; }
  #perm .cmd { font-family:monospace; background:#1a1b1e; border-radius:6px; padding:6px; margin:8px 0; word-break:break-all; }
  .btns { display:flex; gap:8px; }
  .btns button { flex:1; padding:12px 6px; border-radius:8px; border:0; font-size:15px; }
  #a1 { background:var(--ok); color:#fff; }
  #a2 { background:#3c4f3c; color:#c5e6c5; }
  #a3 { background:var(--err); color:#fff; }
  #composer { position:fixed; left:0; right:0; bottom:0; display:flex; gap:8px; padding:10px; background:var(--panel); border-top:1px solid #3c3f4a; }
  #msg { flex:1; padding:12px; border-radius:8px; border:1px solid #3c3f4a; background:#1a1b1e; color:var(--text); font-size:15px; }
  #send { padding:12px 16px; border-radius:8px; border:0; background:var(--accent); color:#fff; font-size:15px; }
  #send:disabled { opacity:.4; }
  #hint { position:fixed; bottom:64px; left:12px; color:var(--dim); font-size:11px; }
</style>
<header>
  <span id="model">ztui</span> <span id="where" class="dim"></span>
  <span id="busy" class="dim"></span>
</header>
<div id="chat">connecting…</div>
<div id="hint" class="dim">auto-refreshes every 1.5s</div>
<div id="perm">
  <div>● <b id="ptool"></b></div>
  <div class="cmd" id="pcmd"></div>
  <div class="btns">
    <button id="a1" onclick="approve('once')">1 Yes</button>
    <button id="a2" onclick="approve('always')">2 Always</button>
    <button id="a3" onclick="approve('no')">3 No</button>
  </div>
</div>
<div id="composer">
  <input id="msg" placeholder="message ztui…" autocapitalize="off" autocorrect="off" autocomplete="off">
  <button id="send" onclick="sendMsg()">send</button>
</div>
<script>
var TOKEN = location.search.match(/t=([^&]+)/) ? location.search.match(/t=([^&]+)/)[1] : "";
function q(s){ return s + (s.indexOf("?")>=0?"&":"?") + "t=" + encodeURIComponent(TOKEN); }
async function poll(){
  try {
    var r = await fetch(q("/api/state"), {cache:"no-store"});
    if (r.status !== 200) { document.getElementById("chat").textContent = "unauthorized — open the link from the TUI again"; return; }
    var s = await r.json();
    document.getElementById("model").textContent = "ztui · " + s.model;
    document.getElementById("where").textContent = s.provider + " · " + s.mode;
    document.getElementById("busy").textContent = s.busy ? "⣾ " + s.elapsed + "s" : "idle";
    document.getElementById("chat").textContent = s.transcript;
    var box = document.getElementById("perm");
    if (s.permission) {
      box.style.display = "block";
      document.getElementById("ptool").textContent = s.permission.tool;
      document.getElementById("pcmd").textContent = s.permission.summary;
    } else box.style.display = "none";
    window.scrollTo(0, document.body.scrollHeight);
  } catch(e) { /* retry next tick */ }
}
async function approve(choice){
  await fetch("/api/approve", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({t: TOKEN, choice: choice})});
  poll();
}
async function sendMsg(){
  var el = document.getElementById("msg");
  var text = el.value.trim();
  if (!text) return;
  el.value = "";
  await fetch("/api/message", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({t: TOKEN, text: text})});
  poll();
}
document.getElementById("msg").addEventListener("keydown", function(e){ if (e.key === "Enter") sendMsg(); });
poll(); setInterval(poll, 1500);
</script>
"""


def _summary_of(tool: str, args: dict) -> str:
    if tool == "bash":
        return args.get("command", "")[:400]
    if tool in ("write", "edit"):
        p = args.get("path", "")
        if tool == "write":
            return f"{p}  (+{str(args.get('content', ''))[:200]})"
        return f"{p}  (-{str(args.get('old_string',''))[:100]} +{str(args.get('new_string',''))[:100]})"
    if tool == "webfetch":
        return args.get("url", "")
    return json.dumps(args, ensure_ascii=False)[:300]
