"""FastAPI application entrypoint — see docs/25-implementation-starter-kit/10-api-build-order.md."""

from fastapi import FastAPI

from core.api.health import router as health_router
from core.approvals.api import router as approvals_router
from core.campaigns.api import router as campaigns_router
from core.catalog.router import router as catalog_router
from core.channels.whatsapp.connect import router as whatsapp_connect_router
from core.channels.whatsapp.ingress import router as whatsapp_ingress_router
from core.common.config import assert_secrets_available, get_settings
from core.common.error_tracking import setup_error_tracking
from core.common.errors import register_exception_handlers
from core.common.telemetry import setup_telemetry
from core.competitors.api import router as competitors_router
from core.conversations.api import router as conversations_router
from core.customers.api import router as customers_router
from core.ingestion.api import router as imports_router
from core.insights.api import insight_admin_router, insights_router
from core.insights.api import router as dashboard_router
from core.packs.router import router as packs_router
from core.pricing.api import rates_router
from core.pricing.api import router as pricing_router
from core.runtime.ops_router import router as ops_router
from core.support.api import admin_router as support_admin_router
from core.support.api import owner_router as support_owner_router
from core.tenancy.api_keys import router as api_keys_router
from core.tenancy.flags_router import router as flags_router
from core.tenancy.invites import router as invites_router
from core.tenancy.orgs_router import router as orgs_router
from core.tenancy.otp_delivery import assert_otp_config_safe
from core.tenancy.platform_router import router as platform_router
from core.tenancy.rbac import register_rbac_handlers
from core.tenancy.router import router as auth_router
from core.tenancy.settings_router import router as settings_router

# Fail closed at import/startup: dev-only OTP echo outside dev (§10.3), and a required
# secrets file that decryption did not produce (MVP-008).
_settings = get_settings()
assert_otp_config_safe(_settings)
assert_secrets_available(_settings)

app = FastAPI(title="Growth Operator")
register_exception_handlers(app)
register_rbac_handlers(app)
setup_telemetry(app)  # no-op unless an OTLP endpoint is configured
setup_error_tracking(app)  # no-op unless an error-tracking DSN is configured (security S2)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(orgs_router)
app.include_router(api_keys_router)
app.include_router(invites_router)
app.include_router(settings_router)
app.include_router(flags_router)
app.include_router(whatsapp_ingress_router)
app.include_router(whatsapp_connect_router)
app.include_router(packs_router)
app.include_router(catalog_router)
app.include_router(campaigns_router)
app.include_router(dashboard_router)
app.include_router(insights_router)
app.include_router(insight_admin_router)
app.include_router(conversations_router)
app.include_router(competitors_router)
app.include_router(customers_router)
app.include_router(pricing_router)
app.include_router(rates_router)
app.include_router(ops_router)
app.include_router(approvals_router)
app.include_router(imports_router)
app.include_router(support_owner_router)
app.include_router(support_admin_router)
app.include_router(platform_router)
