"""Google Ads campaign adapter (B2) — gated, mirrors the Instagram/Meta clients.

The one place a real Google Ads campaign is created (a store's search/display promo). **Gated closed
by default**: `create_campaign()` runs SIMULATED (a fake resource name, no network) unless
`google_ads_live_enabled` is on. Enabled but not wired (customer id + developer token + OAuth token)
fails closed with `provider_unavailable`. The real path is the Google Ads REST API two-step (create
a campaign budget, then a campaign referencing it) over httpx. Two safety rails beyond the flag: a
real campaign is created **PAUSED** (never serves until a human resumes it), and — like every
external action — it needs an approved action upstream (§10.4). Nothing here logs the tokens.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from core.common.config import Settings, get_settings
from core.common.errors import GrowthOperatorError

ADS_BASE = "https://googleads.googleapis.com/v17"
_TIMEOUT = httpx.Timeout(15.0)
_MICROS_PER_MINOR = 10_000  # 1 INR = 1_000_000 micros; amount is in paise (minor) → ×10_000


@dataclass
class AdsResult:
    ok: bool
    resource_name: str | None = None
    simulated: bool = False
    status_code: int | None = None
    error: str | None = None


class GoogleAdsClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def simulated(self) -> bool:
        return not self.settings.google_ads_live_enabled

    def _require_wired(self) -> tuple[str, str, str]:
        s = self.settings
        if not (s.google_ads_customer_id and s.google_ads_developer_token
                and s.google_ads_access_token):
            raise GrowthOperatorError(
                "provider_unavailable", "google ads enabled but customer id/tokens not configured")
        return s.google_ads_customer_id, s.google_ads_developer_token, s.google_ads_access_token

    async def create_campaign(self, *, name: str, daily_budget_minor: int) -> AdsResult:
        """Create a PAUSED search campaign with a daily budget — simulated unless live + wired.
        Two-step: create the budget, then the campaign referencing it. Never serves until resumed;
        resuming is a separate, explicitly-approved action."""
        ref = uuid.uuid4().hex[:16]
        if self.simulated:
            return AdsResult(
                ok=True, simulated=True, resource_name=f"gads.SIM-{ref}")
        customer_id, dev_token, token = self._require_wired()
        headers = {"Authorization": f"Bearer {token}", "developer-token": dev_token}
        base = f"{ADS_BASE}/customers/{customer_id}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                budget = await client.post(
                    f"{base}/campaignBudgets:mutate", headers=headers,
                    json={"operations": [{"create": {
                        "name": f"{name} budget {ref}",
                        "amountMicros": str(daily_budget_minor * _MICROS_PER_MINOR),
                        "deliveryMethod": "STANDARD"}}]})
                if budget.status_code != 200:
                    return AdsResult(
                        ok=False, status_code=budget.status_code,
                        error=f"budget create failed ({budget.status_code})")
                budget_resource = (budget.json().get("results") or [{}])[0].get("resourceName")
                if not budget_resource:
                    return AdsResult(ok=False, error="no budget resource returned")
                campaign = await client.post(
                    f"{base}/campaigns:mutate", headers=headers,
                    json={"operations": [{"create": {
                        "name": name,
                        "status": "PAUSED",  # safety: never serves until a human resumes it
                        "advertisingChannelType": "SEARCH",
                        "campaignBudget": budget_resource}}]})
        except httpx.HTTPError as exc:  # network failures surface as a failed result, not a crash
            return AdsResult(ok=False, error=str(exc)[:200])
        if campaign.status_code != 200:
            return AdsResult(
                ok=False, status_code=campaign.status_code,
                error=f"campaign create failed ({campaign.status_code})")
        resource = (campaign.json().get("results") or [{}])[0].get("resourceName")
        return AdsResult(ok=True, status_code=200, resource_name=str(resource or ref))
