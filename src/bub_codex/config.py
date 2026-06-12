"""Bub configuration boundary.

This module declares plugin settings and loads them through Bub. Runtime
construction belongs in `runtime_services.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import bub
from pydantic import Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict


CONFIG_NAME = "codex"
DEFAULT_BUB_TOOLS = ("tape.info", "tape.search", "tape.anchors", "tape.handoff")


@bub.config(name=CONFIG_NAME)
class BubCodexSettings(bub.Settings):
    """Configuration for the Bub-native Codex runtime plugin."""

    model_config = SettingsConfigDict(
        env_prefix="BUB_CODEX_",
        env_file=".env",
        env_parse_none_str="null",
        extra="ignore",
    )

    enabled: bool = True
    codex_bin: Path | None = None
    sdk_python_path: Path | None = None
    workspace: Path | None = None
    approval_policy: str = "never"
    sandbox: str = "danger-full-access"
    config_overrides: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    use_bub_tape_store: bool = True
    bub_tools: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(DEFAULT_BUB_TOOLS))

    @field_validator("codex_bin", "sdk_python_path", "workspace", mode="after")
    @classmethod
    def _expand_path(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value is not None else None

    @field_validator("bub_tools", mode="before")
    @classmethod
    def _parse_tool_list(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("bub_tools JSON value must be a list")
                return parsed
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    def codex_config_overrides(self) -> tuple[str, ...]:
        defaults = (
            f'approval_policy="{self.approval_policy}"',
            f'sandbox_mode="{self.sandbox}"',
        )
        return (*defaults, *self.config_overrides)


def load_settings() -> BubCodexSettings:
    return bub.ensure_config(BubCodexSettings)
