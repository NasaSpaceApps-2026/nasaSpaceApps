"""WebSocket hub: broadcasts events to every connected viewer."""
import asyncio
import json

from fastapi import WebSocket


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, type_: str, payload: dict):
        msg = json.dumps({"type": type_, **payload})
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def emit(self, type_: str, payload: dict):
        """Fire-and-forget broadcast, safe to call from sync code running on the loop."""
        if self.loop and self.clients:
            self.loop.create_task(self.broadcast(type_, payload))
