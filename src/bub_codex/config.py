from __future__ import annotations

from pathlib import Path

import bub
from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict


CONFIG_NAME = "codex"


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

    @field_validator("codex_bin", "sdk_python_path", "workspace", mode="after")
    @classmethod
    def _expand_path(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value is not None else None

    def codex_config_overrides(self) -> tuple[str, ...]:
        defaults = (
            f'approval_policy="{self.approval_policy}"',
            f'sandbox_mode="{self.sandbox}"',
        )
        return (*defaults, *self.config_overrides)


def load_settings() -> BubCodexSettings:
    return bub.ensure_config(BubCodexSettings)
