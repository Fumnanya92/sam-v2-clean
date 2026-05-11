"""Runnable entrypoint for the Sam v2 application workspace."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from sam_v2.config import load_config
from sam_v2.core import SamRuntime
from sam_v2.daemon import create_app
from sam_v2.diagnostics import reset_log_workspace
from sam_v2.diagnostics.result import SamResult
from sam_v2.native_ui import run_native_ui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sam v2 from the clean rebuild workspace.")
    parser.add_argument("--config", dest="config_path", default=None, help="Path to the Sam v2 YAML config file.")
    parser.add_argument("--env-file", dest="env_file", default=None, help="Optional .env file for Sam v2.")
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="Override the runtime data directory for db, memory, and session files.",
    )
    parser.add_argument("--once", dest="once_text", default=None, help="Run one Sam request and exit.")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print one-shot results as JSON.")
    parser.add_argument("--daemon", dest="daemon_mode", action="store_true", help="Run the FastAPI daemon.")
    parser.add_argument("--cli", dest="cli_mode", action="store_true", help="Run the terminal REPL instead of the native UI.")
    parser.add_argument("--native-ui", dest="native_ui_mode", action="store_true", help="Run the native Sam desktop shell.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for daemon mode.")
    parser.add_argument("--port", type=int, default=0, help="Port override for daemon mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config_result, config = load_config(config_path=args.config_path, env_file=args.env_file)
    if not config_result.ok or config is None:
        print(config_result.error_message or config_result.summary, file=sys.stderr)
        return 1

    data_dir = _resolve_data_dir(config.daemon.data_dir, args.data_dir)
    db_path = Path(args.data_dir).expanduser() / "sam.db" if args.data_dir else config.daemon.db_path
    db_path = Path(db_path).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    reset_log_workspace()

    should_run_native_ui = args.native_ui_mode or (
        not args.once_text and not args.daemon_mode and not args.cli_mode
    )

    if should_run_native_ui:
        return run_native_ui(data_dir=data_dir, db_path=db_path)

    if args.daemon_mode:
        return _run_daemon(db_path=db_path, host=args.host, port=args.port or config.daemon.port)

    runtime = SamRuntime(
        db_path=db_path,
        memory_path=data_dir / "memory.json",
        session_path=data_dir / "session.json",
    )
    try:
        if args.once_text:
            return _run_once(runtime, args.once_text, json_output=args.json_output)
        return _run_repl(runtime)
    finally:
        runtime.shutdown()


def _run_once(runtime: SamRuntime, text: str, *, json_output: bool) -> int:
    result = runtime.handle_text(text)
    if json_output:
        print(json.dumps(_serialize_result(result), indent=2))
    else:
        print(result.summary)
        if result.metadata:
            print(json.dumps(_serialize_value(result.metadata), indent=2))
    return 0 if result.ok else 2


def _run_repl(runtime: SamRuntime) -> int:
    print("Sam v2 REPL. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            user_text = input("sam> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if user_text.lower() in {"exit", "quit"}:
            return 0
        if not user_text:
            continue
        result = runtime.handle_text(user_text)
        print(result.summary)
        if result.metadata:
            print(json.dumps(_serialize_value(result.metadata), indent=2))


def _run_daemon(*, db_path: Path, host: str, port: int) -> int:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - import error is environment-specific
        print(f"uvicorn is required for daemon mode: {exc}", file=sys.stderr)
        return 1

    app = create_app(db_path)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _resolve_data_dir(configured_data_dir: Path, override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    return Path(configured_data_dir).expanduser()


def _serialize_result(result: SamResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "summary": result.summary,
        "error_type": result.error_type,
        "error_message": result.error_message,
        "next_action": result.next_action,
        "metadata": _serialize_value(result.metadata),
        "ok": result.ok,
    }


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _serialize_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
