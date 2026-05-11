"""Minimal Sam v2 daemon package."""

from .main import create_app, create_app_from_config

__all__ = ["create_app", "create_app_from_config"]
