"""API routes for the minimal Sam daemon skeleton."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from diagnostics.result import SamResult

from .ws import WebSocketHub


class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None


def build_router(
    *,
    db_path: Path,
    hub: WebSocketHub,
    chat_queue: asyncio.Queue,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": "sam_daemon",
            "queue_depth": chat_queue.qsize(),
            "websocket_clients": hub.client_count,
            "db_path": str(db_path),
        }

    @router.post("/api/chat")
    async def post_chat(body: ChatMessage) -> dict:
        message_id = str(uuid4())
        session_id = body.session_id or "default"

        await chat_queue.put(
            {
                "message_id": message_id,
                "session_id": session_id,
                "message": body.message,
            }
        )

        await hub.broadcast(
            {
                "type": "chat_message",
                "payload": {
                    "message_id": message_id,
                    "session_id": session_id,
                    "content": body.message,
                    "role": "user",
                },
            }
        )

        result = SamResult(
            status="success",
            summary="Chat accepted by daemon.",
            next_action="stop",
            metadata={
                "message_id": message_id,
                "queue_depth": chat_queue.qsize(),
            },
        )
        return result.__dict__

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await hub.disconnect(websocket)
        except Exception:
            await hub.disconnect(websocket)

    return router
