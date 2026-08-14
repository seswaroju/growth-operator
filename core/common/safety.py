"""Startup safety — refuse to boot a non-dev process on development configuration (PILOT-1A).

The audit that produced this module set `GROWTH_OPERATOR_ENV=prod` on current `main` and got:

    jwt_secret is dev default:          True
    whatsapp_app_secret is dev default: True
    require_secrets_file:               False

Nothing refused. Nothing warned. A live deploy that missed one environment variable would have
signed sessions with a constant published in this repository — anyone could mint a token for any
merchant — and validated Meta webhook signatures against another published constant, so forged
inbound messages would be indistinguishable from real ones. Both fail silently, which is what makes
them dangerous: the system would look completely healthy.

**Why equality checks rather than "is it set".** Every dangerous value here is already non-empty. A
presence check passes on all of them, which is precisely how this class of bug survives review. The
only meaningful question is whether the running value is *the one published in git*.

**Why one module for all three processes.** The API, the worker and the scheduler each hold the same
signing keys and reach the same database. Validating in the API alone would leave the worker — the
process that actually sends messages to customers — unguarded.

This checks configuration, not correctness: a real-looking secret that happens to be wrong still
boots, and should, because that failure is loud. What must never happen is booting on a value an
attacker can read on GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.common.config import (
    _DEV_CREDENTIAL_KEY,
    _DEV_EXEC_TOKEN_SEED,
    _DEV_MANIFEST_SEED,
    Settings,
    get_settings,
)

#: Environments that must never run on repository defaults. `dev` is deliberately absent: local
#: development depends on these values being stable and shared.
NON_DEV_ENVS: frozenset[str] = frozenset({"staging", "prod", "production", "pilot"})

#: Field → its published development default. Membership here is the whole security property, so a
#: new secret-shaped setting is only protected once it appears in this map — which the CI guard in
#: `tests/unit/test_startup_safety.py` enforces by comparing against the Settings model.
DEV_SECRET_DEFAULTS: dict[str, str] = {
    "jwt_secret": "dev-only-insecure-secret",
    "whatsapp_app_secret": "dev-whatsapp-app-secret",
    "whatsapp_verify_token": "dev-verify-token",
    "credential_encryption_key": _DEV_CREDENTIAL_KEY,
    "execution_token_signing_seed": _DEV_EXEC_TOKEN_SEED,
    "manifest_signing_seed": _DEV_MANIFEST_SEED,
}

#: Credential fragments that only ever appear in the repository's own development compose. Matched
#: as substrings of the connection URL because the danger is the *credential*, not the whole string:
#: a production database reachable with the password `app_rw` is no safer for being on a real host.
DEV_DB_CREDENTIALS: tuple[str, ...] = (
    "app_rw:app_rw@",
    "growth_operator:growth_operator@",
    "minioadmin:minioadmin@",
)


class UnsafeEnvironment(RuntimeError):
    """A non-dev process was asked to start on development configuration.

    Raised at import/startup, before any work is processed, so the failure is a refusal to boot
    rather than a compromised system that appears to be running normally."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        detail = "\n  - ".join(problems)
        super().__init__(
            f"refusing to start: {len(problems)} unsafe setting(s) for a non-dev environment.\n"
            f"  - {detail}\n"
            "Every value above is published in this repository. Supply real values through the "
            "SOPS secrets file (scripts/decrypt-secrets.sh) before starting."
        )


@dataclass(frozen=True)
class Check:
    """One named safety condition, so failures name the setting rather than a line number."""

    setting: str
    problem: str


def _secret_problems(settings: Settings) -> list[Check]:
    out: list[Check] = []
    for field, dev_value in DEV_SECRET_DEFAULTS.items():
        if getattr(settings, field, None) == dev_value:
            out.append(Check(field, f"{field} is still the development default from git"))
    return out


def _database_problems(settings: Settings) -> list[Check]:
    out: list[Check] = []
    for field in ("database_url", "database_migrator_url"):
        url = str(getattr(settings, field, "") or "")
        for fragment in DEV_DB_CREDENTIALS:
            if fragment in url:
                # The password is not echoed — naming the setting is enough to act on, and an
                # error string is one of the places a credential must never appear.
                out.append(Check(field, f"{field} uses the development credential from git"))
                break
    return out


def _redis_problems(settings: Settings) -> list[Check]:
    """Redis holds session/rate-limit state and the event streams, so an unauthenticated instance
    is not a lesser problem than an unauthenticated database. The production compose keeps it off
    the public network *and* requires a password; this refuses the default URL that has neither."""
    url = str(settings.redis_url or "")
    if "@" not in url and ("localhost" in url or "127.0.0.1" in url):
        return [Check(
            "redis_url", "redis_url is the unauthenticated localhost development default")]
    return []


def _cors_problems(settings: Settings) -> list[Check]:
    """A production API that trusts `http://localhost` is not merely untidy. CORS is what stops one
    origin using a logged-in browser's credentials against another, so allowing localhost lets any
    page a merchant happens to run locally call the real API as them."""
    origins = [o.strip() for o in str(settings.cors_allow_origins or "").split(",") if o.strip()]
    bad = [o for o in origins if "localhost" in o or "127.0.0.1" in o or o.startswith("http://")]
    if bad:
        return [Check("cors_allow_origins",
                      f"cors_allow_origins includes non-production origins: {bad}")]
    return []


def _policy_problems(settings: Settings) -> list[Check]:
    out: list[Check] = []
    if settings.packs_dev_mode:
        # Pack signature verification is what stops an unsigned vertical pack — arbitrary prompts,
        # workflows and tool grants — from installing. It must not lapse merely because the
        # deployment moved off localhost.
        out.append(Check("packs_dev_mode", "packs_dev_mode=True disables pack signature checks"))
    if not settings.require_secrets_file:
        # Founder ruling: non-dev requires the real secret-delivery mechanism. Without this, every
        # check above can be satisfied only by luck — nothing would have delivered real values.
        out.append(Check(
            "require_secrets_file",
            "require_secrets_file=False lets boot succeed with no decrypted secrets file"))
    if settings.otp_dev_echo:
        out.append(Check("otp_dev_echo", "otp_dev_echo=True would expose one-time codes"))
    if settings.otp_dev_fixed_code:
        out.append(Check("otp_dev_fixed_code", "otp_dev_fixed_code pins a predictable login code"))
    return out


def collect_problems(settings: Settings) -> list[str]:
    """Every unsafe setting, as messages. Empty means safe. Collected rather than raised one at a
    time so a first deployment learns all of its gaps in one boot instead of six."""
    if settings.env not in NON_DEV_ENVS:
        return []
    checks = (
        _secret_problems(settings)
        + _database_problems(settings)
        + _redis_problems(settings)
        + _cors_problems(settings)
        + _policy_problems(settings)
    )
    return [c.problem for c in checks]


def assert_environment_safe(settings: Settings | None = None) -> None:
    """Fail closed unless this environment is safe to run. Call before processing any work.

    A no-op in `dev`, where the published defaults are the point.
    """
    problems = collect_problems(settings or get_settings())
    if problems:
        raise UnsafeEnvironment(problems)
