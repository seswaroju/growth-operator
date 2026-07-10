"""Typed, env-layered settings.

See docs/21-platform/core-platform.md and docs/25-implementation-starter-kit/02-week-1-plan.md.

Layering (highest precedence first): process env -> `.env` -> SOPS-decrypted secrets file
(`GROWTH_OPERATOR_SECRETS_FILE`, plaintext YAML once decrypted by the SOPS-wrapped entrypoint
added in MVP-008) -> field defaults. Secret provisioning itself (age keys, `secrets/*.enc.yaml`,
pre-commit scanning) is out of scope here — MVP-008.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class SopsSecretsSource(PydanticBaseSettingsSource):
    """Reads a plaintext YAML file at the path named by `GROWTH_OPERATOR_SECRETS_FILE`.

    In production this path points at the output of a SOPS decrypt step run by the
    container entrypoint (MVP-008); the loader itself never invokes SOPS or holds keys.
    """

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        data = self._load()
        return data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._load()

    def _load(self) -> dict[str, Any]:
        import os

        path = os.environ.get("GROWTH_OPERATOR_SECRETS_FILE")
        if not path or not Path(path).is_file():
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GROWTH_OPERATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field(default="dev", description="dev | staging | prod")
    database_url: str = Field(default="postgresql+asyncpg://growth_operator:growth_operator@localhost:5432/growth_operator")
    redis_url: str = Field(default="redis://localhost:6379/0")
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    jwt_secret: str = Field(default="dev-only-insecure-secret")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            SopsSecretsSource(settings_cls),
            file_secret_settings,
        )


def get_settings() -> Settings:
    return Settings()
