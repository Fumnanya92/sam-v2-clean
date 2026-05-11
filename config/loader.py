"""Config loader for Sam v2."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from sam_v2.diagnostics.error_types import ErrorType
from sam_v2.diagnostics.result import SamResult

from .models import (
    ChannelConfig,
    ChannelsConfig,
    DaemonConfig,
    LlmConfig,
    LlmProviderConfig,
    SamConfig,
    VoiceConfig,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "sam.yaml"
DEFAULT_RUNTIME_DIR = REPO_ROOT / "sam_v2" / "workspace" / "runtime"
ENV_PREFIXES = ("SAM_V2__", "SAM__")


def load_config(
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
) -> tuple[SamResult, SamConfig | None]:
    """Load Sam v2 config from YAML plus env overrides."""
    try:
        resolved_config_path = _resolve_config_path(config_path)
        resolved_env_file = Path(env_file) if env_file is not None else REPO_ROOT / ".env"
        if resolved_env_file.exists():
            load_dotenv(dotenv_path=resolved_env_file, override=False)

        data = _load_yaml(resolved_config_path)
        _apply_env_overrides(data)
        _normalize_paths(data, resolved_config_path.parent)
        config = _build_config(data)
        return (
            SamResult(
                status="success",
                summary="Sam v2 config loaded.",
                next_action="stop",
                metadata={
                    "config_path": str(resolved_config_path),
                    "env_file": str(resolved_env_file),
                },
            ),
            config,
        )
    except FileNotFoundError as exc:
        return (
            SamResult(
                status="failed",
                summary="Sam v2 config file was not found.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
            ),
            None,
        )
    except yaml.YAMLError as exc:
        return (
            SamResult(
                status="failed",
                summary="Sam v2 config file is invalid YAML.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
            ),
            None,
        )
    except (TypeError, ValueError) as exc:
        return (
            SamResult(
                status="failed",
                summary="Sam v2 config content is invalid.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="ask_user",
            ),
            None,
        )
    except OSError as exc:
        return (
            SamResult(
                status="failed",
                summary="Sam v2 config could not be read.",
                error_type=ErrorType.FILE_ACCESS_ERROR,
                error_message=str(exc),
                next_action="retry",
            ),
            None,
        )


def load_config_or_raise(
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
) -> SamConfig:
    result, config = load_config(config_path=config_path, env_file=env_file)
    if not result.ok or config is None:
        raise RuntimeError(result.error_message or result.summary)
    return config


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path is not None:
        return Path(config_path)
    override = os.getenv("SAM_V2_CONFIG_PATH")
    if override:
        return Path(override)
    return DEFAULT_CONFIG_PATH


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping.")
    return raw


def _apply_env_overrides(data: dict[str, Any]) -> None:
    for prefix in ENV_PREFIXES:
        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            parts = key[len(prefix):].lower().split("__")
            target = data
            for part in parts[:-1]:
                current = target.get(part)
                if not isinstance(current, dict):
                    current = {}
                    target[part] = current
                target = current
            leaf = parts[-1]
            target[leaf] = _coerce_env_value(target.get(leaf), value)


def _coerce_env_value(existing: Any, value: str) -> Any:
    lowered = value.lower()
    if isinstance(existing, bool):
        return lowered in {"1", "true", "yes", "on"}
    if isinstance(existing, int) and lowered.isdigit():
        return int(lowered)
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered.isdigit():
        return int(lowered)
    return value


def _normalize_paths(data: dict[str, Any], config_dir: Path) -> None:
    daemon = data.setdefault("daemon", {})
    if not isinstance(daemon, dict):
        raise ValueError("daemon config must be a mapping.")
    for key in ("data_dir", "db_path"):
        value = daemon.get(key)
        if not isinstance(value, str):
            continue
        expanded = Path(value).expanduser()
        if not expanded.is_absolute():
            expanded = (config_dir / expanded).resolve()
        daemon[key] = str(expanded)


def _build_config(data: dict[str, Any]) -> SamConfig:
    daemon_data = _require_mapping(data.get("daemon", {}), "daemon")
    llm_data = _require_mapping(data.get("llm", {}), "llm")
    voice_data = _require_mapping(data.get("voice", {}), "voice")
    channels_data = _require_mapping(data.get("channels", {}), "channels")

    primary_data = _require_mapping(llm_data.get("primary", {}), "llm.primary")
    fallback_data = _require_mapping(llm_data.get("fallback", {}), "llm.fallback")
    telegram_data = _require_mapping(channels_data.get("telegram", {}), "channels.telegram")
    discord_data = _require_mapping(channels_data.get("discord", {}), "channels.discord")

    return SamConfig(
        daemon=DaemonConfig(
            port=int(daemon_data.get("port", 3142)),
            data_dir=Path(str(daemon_data.get("data_dir", DEFAULT_RUNTIME_DIR))).expanduser(),
            db_path=Path(str(daemon_data.get("db_path", DEFAULT_RUNTIME_DIR / "sam.db"))).expanduser(),
        ),
        llm=LlmConfig(
            primary=LlmProviderConfig(
                provider=str(primary_data.get("provider", "ollama")),
                model=str(primary_data.get("model", "llama3.2")),
                base_url=str(primary_data.get("base_url", "http://localhost:11434")),
                timeout_seconds=int(primary_data.get("timeout_seconds", 20)),
            ),
            fallback=LlmProviderConfig(
                provider=str(fallback_data.get("provider", "openai")),
                model=str(fallback_data.get("model", "gpt-4o-mini")),
                base_url=str(fallback_data.get("base_url", "")),
                timeout_seconds=int(fallback_data.get("timeout_seconds", 20)),
            ),
        ),
        voice=VoiceConfig(
            tts=str(voice_data.get("tts", "edge")),
            stt=str(voice_data.get("stt", "webspeech")),
            wake_hotkey=str(voice_data.get("wake_hotkey", "ctrl+alt+s")),
        ),
        channels=ChannelsConfig(
            telegram=ChannelConfig(
                enabled=bool(telegram_data.get("enabled", False)),
                token=str(telegram_data.get("token", "")),
            ),
            discord=ChannelConfig(
                enabled=bool(discord_data.get("enabled", False)),
                token=str(discord_data.get("token", "")),
            ),
        ),
    )


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} config must be a mapping.")
    return value
