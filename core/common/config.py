"""Typed, env-layered settings.

See docs/21-platform/core-platform.md and docs/25-implementation-starter-kit/02-week-1-plan.md.

Layering (highest precedence first): process env -> `.env` -> SOPS-decrypted secrets file
(`GROWTH_OPERATOR_SECRETS_FILE`, plaintext YAML once decrypted by the SOPS-wrapped entrypoint
added in MVP-008) -> field defaults. Secret provisioning itself (age keys, `secrets/*.enc.yaml`,
pre-commit scanning) is out of scope here — MVP-008.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# A deterministic, valid Fernet key for LOCAL dev/tests only (derived, not a real secret).
# Production supplies a real key via SOPS (GROWTH_OPERATOR_CREDENTIAL_ENCRYPTION_KEY).
_DEV_CREDENTIAL_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"growth-operator-dev-credential-key").digest()
).decode()

# A deterministic 32-byte ed25519 seed for LOCAL dev/tests only (the execution-token signing key,
# MVP-066). Stable so tokens minted before a restart still verify. Production supplies a real seed
# via SOPS (GROWTH_OPERATOR_EXECUTION_TOKEN_SIGNING_SEED).
_DEV_EXEC_TOKEN_SEED = base64.urlsafe_b64encode(
    hashlib.sha256(b"growth-operator-dev-exec-token-seed").digest()
).decode()

# A deterministic 32-byte ed25519 seed for LOCAL dev/tests only (the platform key that signs
# compiled permission manifests, MVP-061). Production supplies a real seed via SOPS
# (GROWTH_OPERATOR_MANIFEST_SIGNING_SEED). Distinct from the execution-token key (separate purpose).
_DEV_MANIFEST_SEED = base64.urlsafe_b64encode(
    hashlib.sha256(b"growth-operator-dev-manifest-seed").digest()
).decode()


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
    # Browser CORS allow-list for the web app(s). Comma-separated origins. Dev default = the local
    # Vite dev servers — the customer app (:5173) AND the operator app (:5174); set to the real web
    # domain(s) in staging/prod. Empty disables CORS entirely.
    cors_allow_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:5174,http://127.0.0.1:5174")
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    # Error/exception tracking (security S2, audit #16d). OFF by default — with no DSN the SDK is
    # never initialized and no event leaves the process (see core/common/error_tracking.py). Points
    # at a SELF-HOSTED GlitchTip (Sentry-compatible ingest); no third-party SaaS ever receives data.
    # When set, PII collection is disabled and every event is scrubbed before send.
    error_tracking_dsn: str | None = Field(default=None)
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

    # Dev-only convenience (CLAUDE.md §10.3): make the generated OTP a FIXED code (e.g. "000000")
    # so local sign-in is deterministic without any delivery adapter. Same guardrails as the echo:
    # MUST stay None by default, only honoured when env == "dev", and startup fails outside dev (or
    # if the value isn't exactly 6 numeric digits) — see assert_otp_config_safe. The code is chosen
    # by the operator; it is never persisted, returned from an API, or written to normal logs.
    otp_dev_fixed_code: str | None = Field(default=None)

    # Staff invites (MVP-017) are gated OFF until Week 5. Interim kill-switch via config;
    # the real per-tenant `invites.enabled` flag arrives with the feature-flag service
    # (MVP-022). When false, the invite endpoints return 404 (feature not enabled).
    invites_enabled: bool = Field(default=False)

    # The Growth Operator cross-tenant operator plane (support console, etc.). OFF by default — a
    # powerful surface (see core/tenancy/platform_admin.py) that must be explicitly enabled, and is
    # meant to run as its own deployment. When false, every /v1/admin/* endpoint returns 404 (the
    # admin API is hidden entirely, not merely 403'd). Prod stays off unless deliberately turned on.
    admin_plane_enabled: bool = Field(default=False)

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
    # Gate for the general EMAIL CHANNEL adapter (PAY0 — receipts etc.), separate from OTP email.
    # OFF by default: the adapter runs simulated (no network) until the founder flips this AND SMTP
    # is wired (smtp_host + smtp_from). Reuses the smtp_* fields; provider-agnostic (Mailpit /
    # Postal / free-tier relay). Turning it on authorises a real external side effect (§10.4).
    email_live_enabled: bool = Field(default=False)
    # Razorpay payment adapter (PAY1). OFF by default → the adapter runs simulated (no network, no
    # real charge). Enabled without keys fails closed (provider_unavailable). key_secret +
    # webhook_secret are SECRETS (env/SOPS only). Turning it on moves real money (§10.4).
    razorpay_live_enabled: bool = Field(default=False)
    razorpay_key_id: str | None = Field(default=None)
    razorpay_key_secret: str | None = Field(default=None)  # secret — never commit
    razorpay_webhook_secret: str | None = Field(default=None)  # secret — verifies capture webhooks
    # Which payment provider the factory returns (PAY1b): "razorpay" (PSP; free UPI + auto-confirm
    # via webhook) or "upi_intent" (free upi:// QR against upi_vpa; NO auto-confirm — reconcile).
    payment_provider: str = Field(default="razorpay")
    upi_vpa: str | None = Field(default=None)  # merchant UPI id, e.g. name@bank (upi_intent)
    upi_payee_name: str | None = Field(default=None)

    # WhatsApp / Meta webhook verification (MVP-031/032). Secrets in prod come from SOPS;
    # the dev defaults are obvious fakes so local webhook tests can compute signatures.
    # Real Meta sends stay gated (BLOCKERS #3) — these only cover ingress verification.
    whatsapp_app_secret: str = Field(default="dev-whatsapp-app-secret")  # noqa: S105 - dev fake
    whatsapp_verify_token: str = Field(default="dev-verify-token")  # noqa: S105 - dev fake
    # Real Meta API calls (webhook registration, sends) are OFF until API access lands
    # (BLOCKERS #3, §10.4). While false, the Meta client runs in simulated mode.
    whatsapp_live_enabled: bool = Field(default=False)
    # Instagram content publishing (B1, §10.4 social post). OFF until Meta API access lands; while
    # false the client SIMULATES (no network). Enabled needs the IG business user id + a token
    # (secret → SOPS in prod, never in code). A real post also still needs an approved action.
    instagram_live_enabled: bool = Field(default=False)
    instagram_ig_user_id: str | None = Field(default=None)
    instagram_access_token: str | None = Field(default=None)  # secret — never commit
    # Google Ads campaign management (B2, §10.4 advertising). OFF until API access lands; while
    # false the client SIMULATES (no network). Enabled needs the developer token + customer id +
    # an OAuth access token (secrets → SOPS in prod); a real campaign also needs an approved action.
    google_ads_live_enabled: bool = Field(default=False)
    google_ads_customer_id: str | None = Field(default=None)  # e.g. "1234567890" (no dashes)
    google_ads_developer_token: str | None = Field(default=None)  # secret — never commit
    google_ads_access_token: str | None = Field(default=None)  # secret OAuth token — never commit
    # The store-owner web app URL, put into the welcome/setup email when a store is provisioned
    # (CP-2). Unset → the email omits the link. Set it to the customer app's real URL at go-live.
    owner_app_url: str | None = Field(default=None)
    # Audit-chain anchoring (MVP-071). The daily scheduler snapshots each org's audit-chain head to
    # this append-only file; point it at a checkout of a SEPARATE private git repo (trust isolation
    # from the app) and have a cron `git commit && push` it. Unset (default) → anchoring is a no-op.
    audit_anchor_path: str | None = Field(default=None)
    # Fernet key used to encrypt channel credentials (WABA access token) at rest.
    credential_encryption_key: str = Field(default=_DEV_CREDENTIAL_KEY)
    # ed25519 seed (base64, 32 bytes) that signs execution tokens (MVP-066). Prod via SOPS.
    execution_token_signing_seed: str = Field(default=_DEV_EXEC_TOKEN_SEED)
    # ed25519 seed (base64, 32 bytes) that signs compiled permission manifests (MVP-061). SOPS prod.
    manifest_signing_seed: str = Field(default=_DEV_MANIFEST_SEED)

    # WhatsApp media handling (MVP-037). Both OFF by default → simulated adapters (dev/tests).
    # When enabled, the real clamav scanner + S3/MinIO store below are used; if the service is
    # unreachable the scan fails closed (media quarantined), so a no-op scanner never silently
    # runs. Start the services with `docker compose --profile media up` (BLOCKERS #12).
    media_av_enabled: bool = Field(default=False)
    media_storage_enabled: bool = Field(default=False)
    clamav_host: str = Field(default="localhost")
    clamav_port: int = Field(default=3310)
    # Object store (MinIO in dev; leave s3_endpoint_url unset for real AWS S3). The secret key
    # comes from SOPS in prod; the dev defaults match the compose MinIO service.
    s3_endpoint_url: str | None = Field(default="http://localhost:9000")
    s3_region: str = Field(default="us-east-1")
    s3_bucket: str = Field(default="gop-media")
    s3_access_key: str = Field(default="minioadmin")
    s3_secret_key: str = Field(default="minioadmin")  # noqa: S105 - dev fake; prod via SOPS

    # Vertical-pack bundle trust (MVP-039). Dev (default) installs from a directory with no
    # signature. Prod MUST set this False so the installer requires a MANIFEST.sha256 whose
    # digests match and a valid ed25519 signature over it before a pack is accepted.
    packs_dev_mode: bool = Field(default=True)

    # Catalog embeddings (MVP-048 / BLOCKER #16). OFF → a deterministic simulated embedder
    # (dev/tests, no paid API). ON → the OpenAI embedder (operator-held key). Per-store spend is
    # metered to `costs_lite` so it surfaces in the CP-6 cost/margin view.
    embeddings_provider_enabled: bool = Field(default=False)
    embeddings_api_key: str = Field(default="")  # OpenAI key (operator-held); required when enabled
    embeddings_model: str = Field(default="text-embedding-3-small")
    embeddings_api_base: str = Field(default="https://api.openai.com")
    embeddings_price_per_1m_usd: float = Field(default=0.02)  # text-embedding-3-small list price

    # Real IBJA rate fetch (MVP-051). Off = deterministic SimulatedRateFetcher (no external
    # call). Turning it on selects the real HTTP source, which is not wired yet — the founder
    # must first pick/confirm the IBJA endpoint (BLOCKERS #5); enabling it before then fails
    # closed (NotImplementedError). Manual entry works regardless.
    rates_provider_enabled: bool = Field(default=False)
    # The rate HTTP source used when the provider is enabled (BLOCKER #5): the community IBJA API
    # (0xSaurabhx/IBJA-API), no key, for the `ibja_gold` source. A per-source `fetch_spec.url`
    # overrides this. Best-effort — manual entry stays the fallback (repo → manual → official).
    rates_ibja_url: str = Field(default="https://ibja-api.vercel.app/latest")

    # Real LLM provider (MVP-055). Off = deterministic SimulatedModel (no paid API). The provider
    # is chosen at go-live (provider-agnostic decision) — enabling this before a provider is wired
    # fails closed (provider_unavailable). Tests always use the simulated model.
    llm_provider_enabled: bool = Field(default=False)
    # Real LLM wiring (MVP-074). Only consulted when llm_provider_enabled is True; enabled without a
    # key fails closed (provider_unavailable). `llm_api_key` is a SECRET — set via env/SOPS, never
    # committed. Provider is `anthropic` (project default, CLAUDE.md) or `openai`. `llm_api_base`
    # defaults per provider when unset. Model defaults to a capable frontier model.
    llm_provider: str = Field(default="anthropic")  # anthropic | openai
    llm_api_key: str | None = Field(default=None)  # secret — never commit
    llm_model: str = Field(default="claude-sonnet-4-5")
    llm_api_base: str | None = Field(default=None)  # e.g. https://api.anthropic.com
    llm_max_tokens: int = Field(default=1024)

    # USD→INR rate used only to fold LLM provider spend (billed in USD, `costs_lite.cost_usd`) into
    # the store cost/margin view (all other figures are already INR paise). A placeholder until a
    # real FX source; the operator can override per environment (CP-6).
    usd_inr_rate: float = Field(default=83.0)

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
