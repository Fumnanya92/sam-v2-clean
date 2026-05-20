"""
Sam's logging module.

All of Sam's internals write here — brain decisions, worker activity,
errors, Firestore queries, coding agent calls, everything.

Log file: logs/sam.log  (relative to the sam-v2-clean root)

Usage anywhere in Sam:
    from sam_brain.logger import log, log_error, log_worker

    log("Router decided: chat")
    log_worker("Forge", "Starting work on expensetracker")
    log_error("brain._handle_query", exc)
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

# ── where the log lives ────────────────────────────────────────────────────────

_LOG_DIR  = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "sam.log"


def _setup() -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("sam")
    if logger.handlers:
        return logger  # Already set up

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — always write everything
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler — only INFO and above (to avoid noise in terminal)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


_logger = _setup()


# ── public helpers ─────────────────────────────────────────────────────────────

def log(message: str, level: str = "info") -> None:
    """Log a general Sam message."""
    getattr(_logger, level.lower(), _logger.info)(message)


def log_worker(worker_name: str, message: str) -> None:
    """Log a worker action — replaces print(f'[{name}] ...')."""
    _logger.info(f"[{worker_name}] {message}")
    # Also print to terminal for live visibility
    print(f"[{worker_name}] {message}", flush=True)


def log_error(location: str, exc: Exception | None = None, message: str = "") -> None:
    """Log an error with optional traceback."""
    msg = f"ERROR in {location}"
    if message:
        msg += f": {message}"
    _logger.error(msg)
    if exc is not None:
        _logger.error(traceback.format_exc())


def log_route(message: str, decision: str) -> None:
    """Log a routing decision."""
    short = (message[:60] + "…") if len(message) > 60 else message
    _logger.info(f"Route  [{decision}]  \"{short}\"")


def log_sam_response(response: str) -> None:
    """Log Sam's response (first 120 chars)."""
    short = (response[:120] + "…") if len(response) > 120 else response
    _logger.debug(f"Sam said: {short}")


def log_file_path() -> str:
    """Return the absolute path to the log file."""
    return str(_LOG_FILE.resolve())
