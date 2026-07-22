"""Boot-time secrets guard tests (MVP-008).

`assert_secrets_available` must fail loudly when a decrypted secrets file is required but
absent, so a container never boots on insecure defaults; and be a no-op otherwise.
"""

from __future__ import annotations

import pytest

from core.common.config import Settings, assert_secrets_available


def test_noop_when_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROWTH_OPERATOR_SECRETS_FILE", raising=False)
    assert_secrets_available(Settings(require_secrets_file=False))  # must not raise


def test_raises_when_required_but_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_SECRETS_FILE", "/nonexistent/secrets.yaml")
    with pytest.raises(RuntimeError, match="missing or unreadable"):
        assert_secrets_available(Settings(require_secrets_file=True))


def test_raises_when_required_but_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROWTH_OPERATOR_SECRETS_FILE", raising=False)
    with pytest.raises(RuntimeError):
        assert_secrets_available(Settings(require_secrets_file=True))


def test_passes_when_required_and_file_present(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets_file = tmp_path / "secrets.yaml"  # type: ignore[operator]
    secrets_file.write_text("jwt_secret: x\n")
    monkeypatch.setenv("GROWTH_OPERATOR_SECRETS_FILE", str(secrets_file))
    assert_secrets_available(Settings(require_secrets_file=True))  # must not raise
