"""Structured config models for Sam v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_DIR = REPO_ROOT / "sam_v2" / "workspace" / "runtime"


@dataclass
class DaemonConfig:
    port: int = 3142
    data_dir: Path = DEFAULT_RUNTIME_DIR
    db_path: Path = DEFAULT_RUNTIME_DIR / "sam.db"


@dataclass
class LlmProviderConfig:
    provider: str = "ollama"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    timeout_seconds: int = 20


@dataclass
class VoiceConfig:
    tts: str = "edge"
    stt: str = "webspeech"
    wake_hotkey: str = "ctrl+alt+s"


@dataclass
class ChannelConfig:
    enabled: bool = False
    token: str = ""


@dataclass
class ChannelsConfig:
    telegram: ChannelConfig = field(default_factory=ChannelConfig)
    discord: ChannelConfig = field(default_factory=ChannelConfig)


@dataclass
class LlmConfig:
    primary: LlmProviderConfig = field(default_factory=LlmProviderConfig)
    fallback: LlmProviderConfig = field(
        default_factory=lambda: LlmProviderConfig(provider="openai", model="gpt-4o-mini", base_url="")
    )


@dataclass
class SamConfig:
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
