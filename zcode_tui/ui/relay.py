"""External-relay pairing client for ztui.

Reuses the ZCode GUI's own web-remote-control channel (reverse-engineered):
- ws:  wss://zcode.z.ai/ws (CN: wss://zcode.chatglm.site/ws)
- pass = randomBytes(24) base64url
- passHash = sha256(pass) base64
- auth: device_register_init -> device_register_ack {device_sid}
  (first time only; afterward auth_challenge -> HMAC proof)
- proof = HMAC-SHA256(passHash, "{nonce}|device|{device_sid}") base64url
- phone page: https://zcode.z.ai/remote/v4?sid=…&hash=…&t=…&mid=…&name=…&app_version=…
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import json
import secrets
import socket
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import qrcode
import websockets

RELAY_WSS = "wss://zcode.z.ai/ws"
PHONE_BASE = "https://zcode.z.ai/remote/v4"

_APP_VERSION = "ztui-0.1.0"


def create_password() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode()


def create_pass_hash(password: str) -> str:
    return base64.b64encode(hashlib.sha256(password.encode()).digest()).decode()


def calculate_proof(pass_hash: str, nonce: str, kind: str, device_sid: str) -> str:
    msg = f"{nonce}|{kind}|{device_sid}".encode()
    return base64.urlsafe_b64encode(
        hmac_mod.new(pass_hash.encode(), msg, hashlib.sha256).digest()
    ).rstrip(b"=").decode()


def build_phone_url(device_sid: str, pass_hash: str, device_mid: str, device_name: str) -> str:
    q = urllib.parse.urlencode(
        {
            "sid": device_sid,
            "hash": pass_hash,
            "t": str(int(time.time() * 1000)),
            "mid": device_mid,
            "name": device_name,
            "app_version": _APP_VERSION,
        }
    )
    return f"{PHONE_BASE}?{q}"


def qr_text(url: str) -> "object":
    """Scannable terminal QR: black modules on a forced white background
    with a full 2-module+ quiet zone (the only reliable form on dark themes).

    Each text row renders two QR rows (half-block style): '█' when both
    modules are dark, '▀' top-dark, '▄' bottom-dark, ' ' when both light.
    Returns a rich Text laid out with a white background.
    """
    from rich.text import Text

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=4,
        box_size=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # True = dark module, border rows included

    margin = "    "
    text = Text()
    rows = len(matrix)
    for r in range(0, rows, 2):
        line_chars: list[str] = []
        row_top = matrix[r]
        row_bottom = matrix[r + 1] if r + 1 < rows else [False] * len(row_top)
        for top, bottom in zip(row_top, row_bottom):
            pair = (top << 1) | (1 if bottom else 0)
            line_chars.append(" " if pair == 0 else ("▄" if pair == 1 else ("▀" if pair == 2 else "█")))
        text.append(margin + "".join(line_chars) + margin + "\n", style="black on #ffffff")
    if len(text.plain) and text.plain.endswith("\n"):
        text.plain = text.plain.rstrip("\n")
    return text


OnPairEvent = Callable[[str, dict], Awaitable[None]]


@dataclass
class RelayPairing:
    device_sid: str | None
    pass_hash: str
    device_mid: str
    device_name: str
    ws_url: str = RELAY_WSS
    phone_url: str = ""
    status: str = "idle"  # idle|connecting|registering|authenticating|waiting_terminal|paired|error
    error: str = ""
    log: list[str] = field(default_factory=list)


class RelayClient:
    """One pairing session against the external relay."""

    def __init__(self, pairing: RelayPairing, on_event: OnPairEvent | None = None) -> None:
        self.pairing = pairing
        self.on_event = on_event or self._noop
        self._ws = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    @staticmethod
    async def _noop(event: str, payload: dict) -> None: ...

    async def start(self, register_first: bool) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run(register_first))

    async def stop(self) -> None:
        self._stopping = True
        try:
            if self._ws is not None:
                await self._ws.close()
        finally:
            if self._task:
                self._task.cancel()

    # -- internals -----------------------------------------------------------

    async def _run(self, register_first: bool) -> None:
        p = self.pairing
        try:
            async with websockets.connect(p.ws_url, max_size=8 * 1024 * 1024, open_timeout=20) as ws:
                self._ws = ws
                p.status = "registering" if register_first else "authenticating"
                if register_first:
                    await ws.send(json.dumps({
                        "type": "device_register_init",
                        "device_mid": p.device_mid,
                        "pass_hash": p.pass_hash,
                        "meta": {"device_name": p.device_name, "app_version": _APP_VERSION},
                        "client_ts": int(time.time() * 1000),
                    }))
                else:
                    await ws.send(json.dumps({
                        "type": "auth_init",
                        "role": "device",
                        "device_sid": p.device_sid,
                        "meta": {"device_name": p.device_name, "app_version": _APP_VERSION},
                        "client_ts": int(time.time() * 1000),
                    }))
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._dispatch(data)
                    if self._stopping or p.status == "error":
                        break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            p.status = "error"
            p.error = str(e)[:200]
            await self.on_event("error", {"message": p.error})

    async def _dispatch(self, data: dict) -> None:
        p = self.pairing
        t = data.get("type")
        if t == "device_register_ack":
            p.device_sid = data.get("device_sid")
            p.phone_url = build_phone_url(p.device_sid, p.pass_hash, p.device_mid, p.device_name)
            p.status = "authenticating"
            await self.on_event("registered", {"device_sid": p.device_sid, "phone_url": p.phone_url})
            await self._send_auth_init()
        elif t == "auth_challenge":
            nonce = data.get("nonce", "")
            proof = calculate_proof(p.pass_hash, nonce, "device", p.device_sid or "")
            await self._ws.send(json.dumps({
                "type": "auth_response",
                "device_sid": p.device_sid,
                "proof": proof,
                "client_ts": int(time.time() * 1000),
            }))
        elif t in ("auth_ack",):
            p.status = "waiting_terminal"
            await self.on_event("authed", {"phone_url": p.phone_url})
        elif t == "pair_status_ack":
            st = data.get("pair_status", "")
            if st == "paired":
                p.status = "paired"
            else:
                p.status = "waiting_terminal"
            await self.on_event("pair_status", data)
        elif t == "data":
            await self.on_event("data", data.get("payload", {}))
        elif t == "error":
            if data.get("code") == "DEVICE_SID_INVALID" and p.device_sid:
                await self.on_event("sid_invalid", data)
                p.status = "error"
                p.error = "device sid invalid — re-register needed"
            else:
                await self.on_event("relay_error", data)
                p.status = "error"
                p.error = data.get("message", str(data.get("code", "")))
        else:
            await self.on_event("frame", data)

    async def _send_auth_init(self) -> None:
        if self._ws is None:
            return
        p = self.pairing
        await self._ws.send(json.dumps({
            "type": "auth_init",
            "role": "device",
            "device_sid": p.device_sid,
            "meta": {"device_name": p.device_name, "app_version": _APP_VERSION},
            "client_ts": int(time.time() * 1000),
        }))
