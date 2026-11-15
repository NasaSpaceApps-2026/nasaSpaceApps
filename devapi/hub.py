"""WebSocket hub: every connected viewer gets every server event; viewer messages go to handlers.

Architecture: viewer/chat <-> devapi (WS) <- World/Rover/routes publish events; MCP and agent use REST, not this socket.
Envelope: {"type": str, "ts": unix seconds, "payload": object}.
"""
from __future__ import annotations

import time

from fastapi import WebSocket

from .util import clean


class Hub:
    """Set of connected viewer WebSockets; broadcast sends every client the envelope {type, ts, payload} and drops clients whose send fails."""
    def __init__(self):
        self.clients: set[WebSocket] = set()

    @property
    def count(self) -> int:
        return len(self.clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, type_: str, payload) -> None:
        """Event envelope: {"type": <type>, "ts": <unix s>, "payload": <object>}."""
        msg = {"type": type_, "ts": round(time.time(), 3), "payload": clean(payload)}
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self.clients.discard(ws)
