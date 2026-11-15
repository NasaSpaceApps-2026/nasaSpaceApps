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
        """Fire-and-forget broadcast; safe from the event loop or from worker threads."""
        if not (self.loop and self.clients):
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(self.broadcast(type_, payload))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast(type_, payload), self.loop)
