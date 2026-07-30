"""Typed, env-layered settings.

See docs/21-platform/core-platform.md and docs/25-implementation-starter-kit/02-week-1-plan.md.

Layering (highest precedence first): process env -> `.env` -> SOPS-decrypted secrets file
(`GROWTH_OPERATOR_SECRETS_FILE`, plaintext YAML once decrypted by the SOPS-wrapped entrypoint
added in MVP-008) -> field defaults. Secret provisioning itself (age keys, `secrets/*.enc.yaml`,
pre-commit scanning) is out of scope here — MVP-008.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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
    # RUNTIME connection — the app/worker/scheduler use the non-superuser, NON-BYPASSRLS
    # `app_rw` role so RLS is actually enforced (MVP-016 / BLOCKERS #11). Create the role
    # with infra/db/roles.sql (`make db-roles`) before the app can connect.
    database_url: str = Field(default="postgresql+asyncpg://app_rw:app_rw@localhost:5432/growth_operator")
    # DDL/migration connection — alembic runs as the owner (`growth_operator`), which has
    # DDL rights and, being the object owner, is what ALTER DEFAULT PRIVILEGES grants FROM.
    # app_rw has no DDL rights, so migrations MUST use this URL, not database_url.
    database_migrator_url: str = Field(default="postgresql+asyncpg://growth_operator:growth_operator@localhost:5432/growth_operator")
    redis_url: str = Field(default="redis://localhost:6379/0")
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    jwt_secret: str = Field(default="dev-only-insecure-secret")
    # OTP delivery channel. INTERIM default is "email" as a bridge while Meta WhatsApp
    # API access is pending (see project-management/TODO.md, DECISIONS.md 2026-07-22).
    # Flip back to "phone" once real WhatsApp delivery is live — phone code path retained.
    otp_channel: Literal["email", "phone"] = Field(default="email")
    # Dev-only convenience (CLAUDE.md §10.3): echo the plaintext OTP to stderr so a
    # developer can complete login without an SMS/WhatsApp provider. MUST stay False
    # by default, only honoured when env == "dev", and startup fails outside dev if
    # set (see core.tenancy.otp_delivery.assert_otp_config_safe). Never persisted,
    # never returned from an API, never written to normal application logs.
    otp_dev_echo: bool = Field(default=False)

    # Staff invites (MVP-017) are gated OFF until Week 5. Interim kill-switch via config;
    # the real per-tenant `invites.enabled` flag arrives with the feature-flag service
    # (MVP-022). When false, the invite endpoints return 404 (feature not enabled).
    invites_enabled: bool = Field(default=False)

    # Real email OTP delivery (interim channel). OFF by default — turning it on is the
    # act of authorising a real external side effect (§10.4), so it is the founder's
    # explicit decision per environment. When enabled, all SMTP fields below must be set
    # (validated at startup by assert_otp_delivery_config); otherwise startup fails.
    # `smtp_password` is a secret — provide it via the SOPS secrets file, never in code.
    otp_email_enabled: bool = Field(default=False)
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587)
    smtp_username: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_from: str | None = Field(default=None, description="From address, e.g. no-reply@x.com")

    # SOPS secrets (MVP-008). In staging/prod the container entrypoint decrypts
    # secrets/<env>.enc.yaml (SOPS+age) to a plaintext file at GROWTH_OPERATOR_SECRETS_FILE,
    # which SopsSecretsSource above reads. Set require_secrets_file=true there so boot
    # hard-fails (assert_secrets_available) if decryption did not produce that file —
    # never silently fall back to insecure defaults.
    require_secrets_file: bool = Field(default=False)

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


def assert_secrets_available(settings: Settings) -> None:
    """Fail closed at startup if a decrypted secrets file is required but absent (MVP-008).

    Guards against a container booting with insecure defaults when SOPS decryption did not
    run or the age key is missing. Enabled per environment via require_secrets_file.
    """
    if not settings.require_secrets_file:
        return
    import os

    path = os.environ.get("GROWTH_OPERATOR_SECRETS_FILE")
    if not path or not Path(path).is_file():
        raise RuntimeError(
            "GROWTH_OPERATOR_REQUIRE_SECRETS_FILE is set but the decrypted secrets file "
            f"(GROWTH_OPERATOR_SECRETS_FILE={path!r}) is missing or unreadable. Run "
            "`scripts/decrypt-secrets.sh <env>` (needs the age key) before boot."
        )
