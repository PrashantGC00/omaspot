"""Talking to the local Soloist daemon: `soloist ctl` for commands (~10 ms) and its WebSocket for events.

The WebSocket (`--ws 127.0.0.1:0`, port in <data-dir>/ws.port, no auth) sends `auth_state` and a full
`playback_state` on connect, then pushes `playback_state`, `playback_changed`, `position_sync`,
`volume_changed` and `queue_changed` as they happen, so omaspot doesn't need to poll while Omarchy plays.
"""
import asyncio
import base64
import json
import os
import shutil
import struct
from pathlib import Path

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "soloist"


class Soloist:
    def __init__(self):
        self.exe = shutil.which("soloist") or str(Path.home() / ".local/bin/soloist")
        self.active = False           # Omarchy is the active Spotify Connect device
        self.connected = False        # the WebSocket is up (events arrive without polling)

    # ---- ctl ----
    async def _run(self, *args, out: bool = False):
        proc = await asyncio.create_subprocess_exec(
            self.exe, "ctl", *map(str, args),
            stdout=asyncio.subprocess.PIPE if out else asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), 3)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        return proc.returncode, data

    async def ctl(self, *args) -> bool:
        """A control command; False if Omarchy isn't the active device or it failed (caller falls back)."""
        if not self.active:
            return False
        try:
            rc, _ = await self._run(*args)
            return rc == 0
        except (OSError, asyncio.TimeoutError):
            return False

    async def _json(self, *args) -> dict | None:
        try:
            _, out = await self._run(*args, out=True)
            return json.loads(out or b"{}")
        except (OSError, ValueError, asyncio.TimeoutError):
            return None

    async def now(self) -> dict | None:
        """`ctl now --json` (a playback_state); also refreshes `active`."""
        d = await self._json("now", "--json")
        self.active = bool(d and d.get("is_active"))
        return d

    async def queue(self, n: int = 30) -> list[dict]:
        """Upcoming Soloist items (`ctl queue n --json`)."""
        d = await self._json("queue", n, "--json") or {}
        return [u.get("item") for u in d.get("upcoming") or [] if u.get("item")]

    # ---- WebSocket ----
    def ws_port(self) -> int | None:
        try:
            return int((DATA_DIR / "ws.port").read_text().strip())
        except (OSError, ValueError):
            return None

    async def listen(self, on_event):
        """Call on_event(dict) for every event, forever; reconnects (1 s → 10 s backoff) when Soloist restarts."""
        delay = 1.0
        while True:
            port = self.ws_port()
            if port:
                try:
                    r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
                    try:
                        await handshake(r, w, port)
                        self.connected, delay = True, 1.0
                        async for msg in messages(r, w):
                            try:
                                ev = json.loads(msg)
                            except ValueError:
                                continue
                            if ev.get("type") == "auth_state":
                                self.active = bool(ev.get("is_active"))
                            elif ev.get("type") == "playback_state":
                                self.active = bool(ev.get("is_active"))
                            on_event(ev)
                    finally:
                        w.close()
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError):
                    pass
            if self.connected:
                self.connected = False
                on_event({"type": "ws_closed"})
            await asyncio.sleep(delay)
            delay = min(10.0, delay * 2)


# ---- a minimal RFC 6455 client: enough for a local, unauthenticated, text-only event stream ----
async def handshake(r: asyncio.StreamReader, w: asyncio.StreamWriter, port: int):
    key = base64.b64encode(os.urandom(16)).decode()
    w.write((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    await w.drain()
    head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 3)
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        raise ConnectionError("websocket upgrade refused")


def frame(opcode: int, payload: bytes = b"") -> bytes:
    """A masked client frame (clients must mask)."""
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


async def read_frame(r: asyncio.StreamReader):
    b0, b1 = await r.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await r.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await r.readexactly(8))[0]
    mask = await r.readexactly(4) if b1 & 0x80 else None
    data = await r.readexactly(n)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bool(b0 & 0x80), b0 & 0x0F, data


async def messages(r: asyncio.StreamReader, w: asyncio.StreamWriter):
    """Text messages, reassembled from fragments; answers pings, stops on close."""
    parts: list[bytes] = []
    while True:
        fin, op, data = await read_frame(r)
        if op == 0x8:                   # close
            return
        if op == 0x9:                   # ping
            w.write(frame(0xA, data))
            continue
        if op in (0x1, 0x2, 0x0):
            parts.append(data)
            if fin:
                msg, parts = b"".join(parts), []
                yield msg.decode("utf-8", errors="replace")
