"""Minimal FastAPI daemon skeleton for Sam v2."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sam_v2.config import load_config_or_raise
from sam_v2.diagnostics.run_logger import RunLogger
from sam_v2.storage.db import init_storage

from .api import build_router
from .ws import WebSocketHub

logger = logging.getLogger("sam_v2.daemon")


def create_app_from_config(
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
) -> FastAPI:
    config = load_config_or_raise(config_path=config_path, env_file=env_file)
    return create_app(config.daemon.db_path)


def create_app(db_path: str | Path) -> FastAPI:
    resolved_db_path = Path(db_path)
    chat_queue: asyncio.Queue = asyncio.Queue()
    websocket_hub = WebSocketHub()
    run_logger = RunLogger("sam_v2 daemon skeleton")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_logger.log("startup_started", {"db_path": str(resolved_db_path)})
        init_result = init_storage(resolved_db_path)
        run_logger.log(
            "storage_init",
            {
                "status": init_result.status,
                "summary": init_result.summary,
                "error_message": init_result.error_message,
            },
        )
        if not init_result.ok:
            raise RuntimeError(init_result.error_message or init_result.summary)

        app.state.db_path = resolved_db_path
        app.state.chat_queue = chat_queue
        app.state.websocket_hub = websocket_hub
        run_logger.log("startup_complete", {"queue_depth": chat_queue.qsize()})
        yield
        run_logger.log("shutdown_complete", {"queue_depth": chat_queue.qsize()})

    app = FastAPI(
        title="Sam v2 Daemon",
        description="Minimal daemon skeleton with health, chat, and websocket support.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(
        build_router(db_path=resolved_db_path, hub=websocket_hub, chat_queue=chat_queue)
    )
    return app
