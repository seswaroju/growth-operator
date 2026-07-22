"""FastAPI application entrypoint — see docs/25-implementation-starter-kit/10-api-build-order.md."""

from fastapi import FastAPI

from core.common.config import get_settings
from core.common.errors import register_exception_handlers
from core.tenancy.otp_delivery import assert_otp_config_safe
from core.tenancy.router import router as auth_router

# Fail closed at import/startup if the dev-only OTP echo is enabled outside dev (§10.3).
assert_otp_config_safe(get_settings())

app = FastAPI(title="Growth Operator")
register_exception_handlers(app)
app.include_router(auth_router)
