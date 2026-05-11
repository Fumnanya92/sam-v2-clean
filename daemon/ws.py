"""WebSocket helpers for the Sam v2 daemon skeleton."""

from __future__ import annotations

import asyncio
import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger("sam_v2.daemon.ws")


class WebSocketHub:
    """Small broadcast hub for connected dashboard clients."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        logger.info("WebSocket client connected.")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        logger.info("WebSocket client disconnected.")

    async def broadcast(self, message: dict) -> None:
        dead_clients: Set[WebSocket] = set()
        async with self._lock:
            for client in self._clients:
                try:
                    await client.send_json(message)
                except Exception:
                    dead_clients.add(client)
            self._clients -= dead_clients

    @property
    def client_count(self) -> int:
        return len(self._clients)
