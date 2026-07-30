"""FastAPI application entrypoint — see docs/25-implementation-starter-kit/10-api-build-order.md."""

from fastapi import FastAPI

from core.api.health import router as health_router
from core.common.config import assert_secrets_available, get_settings
from core.common.errors import register_exception_handlers
from core.common.telemetry import setup_telemetry
from core.tenancy.api_keys import router as api_keys_router
from core.tenancy.invites import router as invites_router
from core.tenancy.orgs_router import router as orgs_router
from core.tenancy.otp_delivery import assert_otp_config_safe
from core.tenancy.rbac import register_rbac_handlers
from core.tenancy.router import router as auth_router

# Fail closed at import/startup: dev-only OTP echo outside dev (§10.3), and a required
# secrets file that decryption did not produce (MVP-008).
_settings = get_settings()
assert_otp_config_safe(_settings)
assert_secrets_available(_settings)

app = FastAPI(title="Growth Operator")
register_exception_handlers(app)
register_rbac_handlers(app)
setup_telemetry(app)  # no-op unless an OTLP endpoint is configured
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(orgs_router)
app.include_router(api_keys_router)
app.include_router(invites_router)
