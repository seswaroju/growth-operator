"""PILOT-1A — a non-dev process must refuse to boot on development configuration.

The audit that prompted this ran `GROWTH_OPERATOR_ENV=prod` against `main` and found that nothing
changed: sessions would have been signed with a constant published in this repository, and Meta
webhook signatures validated against another. Both fail silently — a system running on them looks
completely healthy — so these tests exist to make that failure loud and keep it that way.

Each test names the specific consequence rather than asserting "it raises", because the reason a
check exists is the thing most likely to be lost when someone later finds it inconvenient.
"""

from __future__ import annotations

import pytest

from core.common.config import (
    _DEV_CREDENTIAL_KEY,
    _DEV_EXEC_TOKEN_SEED,
    _DEV_MANIFEST_SEED,
    Settings,
)
from core.common.safety import (
    DEV_SECRET_DEFAULTS,
    UnsafeEnvironment,
    assert_environment_safe,
    collect_problems,
)

#: A configuration with every dangerous value replaced. Used as the baseline that MUST boot, so a
#: test proving something is refused cannot pass merely because everything is refused.
SAFE_PROD: dict[str, object] = {
    "env": "prod",
    "jwt_secret": "PROD-" + "a" * 40,
    "whatsapp_app_secret": "PROD-" + "b" * 40,
    "whatsapp_verify_token": "PROD-" + "c" * 40,
    "credential_encryption_key": "PROD-" + "d" * 40,
    "execution_token_signing_seed": "PROD-" + "e" * 40,
    "manifest_signing_seed": "PROD-" + "f" * 40,
    "database_url": "postgresql+asyncpg://app_rw:REAL@postgres:5432/growth_operator",
    "database_migrator_url": "postgresql+asyncpg://owner:REAL@postgres:5432/growth_operator",
    "redis_url": "redis://:REAL@redis:6379/0",
    "cors_allow_origins": "https://app.vaylorn.com,https://ops.vaylorn.com",
    "packs_dev_mode": False,
    "require_secrets_file": True,
}


def _prod(**overrides: object) -> Settings:
    return Settings(**{**SAFE_PROD, **overrides})  # type: ignore[arg-type]


# ---- 9 / 10: the baseline behaves ------------------------------------------------------------


def test_a_fully_configured_production_environment_starts() -> None:
    """The control. Without it, every refusal test below could pass by refusing everything."""
    assert collect_problems(_prod()) == []
    assert_environment_safe(_prod())


def test_development_keeps_working_on_repository_defaults() -> None:
    """Local development depends on these values being shared and stable; a check that broke `dev`
    would simply be turned off."""
    assert collect_problems(Settings(env="dev")) == []
    assert_environment_safe(Settings(env="dev"))


# ---- 1-5: the published crypto defaults ------------------------------------------------------


def test_default_jwt_secret_is_refused() -> None:
    """`dev-only-insecure-secret` is in git. With it, anyone can mint a session token for any
    merchant — the whole tenancy model rests on this one value."""
    with pytest.raises(UnsafeEnvironment, match="jwt_secret"):
        assert_environment_safe(_prod(jwt_secret="dev-only-insecure-secret"))


def test_default_whatsapp_app_secret_is_refused() -> None:
    """This validates the HMAC on every inbound webhook. With the published value, a forged
    customer message is indistinguishable from a real one."""
    with pytest.raises(UnsafeEnvironment, match="whatsapp_app_secret"):
        assert_environment_safe(_prod(whatsapp_app_secret="dev-whatsapp-app-secret"))


def test_default_whatsapp_verify_token_is_refused() -> None:
    with pytest.raises(UnsafeEnvironment, match="whatsapp_verify_token"):
        assert_environment_safe(_prod(whatsapp_verify_token="dev-verify-token"))


def test_dev_credential_encryption_key_is_refused() -> None:
    """This Fernet key encrypts stored WABA access tokens. Publishing it means publishing every
    merchant's ability to send as themselves."""
    with pytest.raises(UnsafeEnvironment, match="credential_encryption_key"):
        assert_environment_safe(_prod(credential_encryption_key=_DEV_CREDENTIAL_KEY))


def test_dev_execution_token_seed_is_refused() -> None:
    """Execution tokens are what prove an approval happened. A known seed forges approval."""
    with pytest.raises(UnsafeEnvironment, match="execution_token_signing_seed"):
        assert_environment_safe(_prod(execution_token_signing_seed=_DEV_EXEC_TOKEN_SEED))


def test_dev_manifest_signing_seed_is_refused() -> None:
    """Manifests are what bound an agent's tools. A known seed lets an attacker widen them."""
    with pytest.raises(UnsafeEnvironment, match="manifest_signing_seed"):
        assert_environment_safe(_prod(manifest_signing_seed=_DEV_MANIFEST_SEED))


def test_presence_alone_would_not_have_caught_any_of_these() -> None:
    """The reason these are equality checks. Every published default is non-empty, so a
    "is it configured?" check passes on all six — which is exactly how this survived until now."""
    assert all(bool(value) for value in DEV_SECRET_DEFAULTS.values())


# ---- 6-8: secrets delivery, pack trust, data services -----------------------------------------


def test_require_secrets_file_false_is_refused() -> None:
    """Founder ruling: non-dev requires the real secret-delivery mechanism. Without it, boot
    succeeds with no decrypted secrets — so every check above passes only by luck."""
    with pytest.raises(UnsafeEnvironment, match="require_secrets_file"):
        assert_environment_safe(_prod(require_secrets_file=False))


def test_packs_dev_mode_is_refused() -> None:
    """Pack signature verification is what stops an unsigned pack — arbitrary prompts, workflows
    and tool grants — from installing. It must not lapse because the deployment moved off
    localhost."""
    with pytest.raises(UnsafeEnvironment, match="packs_dev_mode"):
        assert_environment_safe(_prod(packs_dev_mode=True))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://app_rw:app_rw@postgres:5432/growth_operator",
        "postgresql+asyncpg://growth_operator:growth_operator@localhost:5432/growth_operator",
    ],
)
def test_development_database_credentials_are_refused(url: str) -> None:
    """A production database reachable with the password `app_rw` is no safer for being on a real
    host. Matched on the credential, not the hostname."""
    with pytest.raises(UnsafeEnvironment, match="database"):
        assert_environment_safe(_prod(database_url=url))


def test_unauthenticated_localhost_redis_is_refused() -> None:
    """Redis carries the event streams, so write access is the ability to forge or drop work."""
    with pytest.raises(UnsafeEnvironment, match="redis_url"):
        assert_environment_safe(_prod(redis_url="redis://localhost:6379/0"))


def test_dev_otp_shortcuts_are_refused() -> None:
    for override in ({"otp_dev_echo": True}, {"otp_dev_fixed_code": "123456"}):
        with pytest.raises(UnsafeEnvironment):
            assert_environment_safe(_prod(**override))


def test_localhost_cors_origin_is_refused() -> None:
    """CORS is what stops one origin using a logged-in browser's credentials against another."""
    with pytest.raises(UnsafeEnvironment, match="cors"):
        assert_environment_safe(_prod(cors_allow_origins="http://localhost:5173"))


# ---- reporting ---------------------------------------------------------------------------------


def test_every_problem_is_reported_at_once() -> None:
    """A first deployment should learn all of its gaps in one boot, not discover them one restart
    at a time over an evening."""
    problems = collect_problems(Settings(env="prod"))
    assert len(problems) >= 10
    for field in DEV_SECRET_DEFAULTS:
        assert any(field in p for p in problems), f"{field} not reported"


def test_no_secret_value_appears_in_the_error() -> None:
    """The error names settings, never values — an exception message reaches logs, terminals and
    screenshots, which is precisely where a credential must not be."""
    problems = collect_problems(Settings(env="prod"))
    blob = " ".join(problems)
    for value in DEV_SECRET_DEFAULTS.values():
        assert value not in blob
    assert "app_rw:app_rw" not in blob


@pytest.mark.parametrize("env", ["staging", "prod", "production", "pilot"])
def test_every_non_dev_environment_is_guarded(env: str) -> None:
    assert collect_problems(Settings(env=env)) != []


# ---- 11: all three processes invoke it ---------------------------------------------------------


@pytest.mark.parametrize(
    "module", ["core/api/main.py", "core/worker.py", "core/scheduler.py"])
def test_all_three_processes_assert_environment_safety(module: str) -> None:
    """The worker is the process that actually messages customers. Guarding only the API would
    leave the loudest external effect unguarded."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / module).read_text()
    assert "assert_environment_safe" in source, f"{module} does not check environment safety"


def test_the_secret_map_covers_every_known_dev_default() -> None:
    """A guard against the guard: a future secret-shaped setting that keeps a published default is
    only protected once it is listed, so this fails when one is added and forgotten."""
    defaults = Settings(env="dev")
    suspicious = {
        name: getattr(defaults, name)
        for name in type(defaults).model_fields
        if isinstance(getattr(defaults, name, None), str)
        and any(marker in str(getattr(defaults, name)).lower() for marker in ("dev-", "-dev"))
    }
    missing = set(suspicious) - set(DEV_SECRET_DEFAULTS)
    assert not missing, (
        f"settings with a development-looking default are not covered by the safety check: "
        f"{sorted(missing)} — add them to DEV_SECRET_DEFAULTS")
