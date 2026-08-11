"""Google Ads campaign adapter (B2) — gated + simulated, with the REST API mocked.

Off by default → simulated (no network). Live-but-unwired → provider_unavailable. Live + wired → the
real two-step create (campaignBudgets:mutate → campaigns:mutate, both mocked) returns the campaign
resource name, the campaign is created PAUSED, and the request shapes are pinned. A REST error
surfaces as a failed result, not a crash. No real Ads account, no real spend — §10.4 stays honoured.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.channels.google_ads import GoogleAdsClient
from core.common.errors import GrowthOperatorError


def _resp(code: int, body: dict[str, Any]) -> httpx.Response:
    r = httpx.Response(code, json=body)
    r.request = httpx.Request("POST", "https://googleads.googleapis.com/x")
    return r


def _fake_post(cap: dict[str, Any], *, budget_code: int = 200, campaign_code: int = 200) -> Any:
    async def post(self: Any, url: str, *, json: Any = None, headers: Any = None) -> httpx.Response:
        cap.setdefault("calls", []).append({"url": url, "json": json, "headers": headers})
        if url.endswith("campaignBudgets:mutate"):
            body = {"results": [{"resourceName": "customers/1/campaignBudgets/9"}]}
            return _resp(budget_code, body)
        return _resp(campaign_code, {"results": [{"resourceName": "customers/1/campaigns/77"}]})
    return post


def _live(monkeypatch: pytest.MonkeyPatch, *, wired: bool = True) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_GOOGLE_ADS_LIVE_ENABLED", "true")
    if wired:
        monkeypatch.setenv("GROWTH_OPERATOR_GOOGLE_ADS_CUSTOMER_ID", "1234567890")
        monkeypatch.setenv("GROWTH_OPERATOR_GOOGLE_ADS_DEVELOPER_TOKEN", "DEVTOK")
        monkeypatch.setenv("GROWTH_OPERATOR_GOOGLE_ADS_ACCESS_TOKEN", "OAUTH")


async def test_simulated_by_default_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*a: object, **k: object) -> httpx.Response:
        raise AssertionError("simulated path must not touch the network")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    client = GoogleAdsClient()
    assert client.simulated is True
    r = await client.create_campaign(name="Wedding Season", daily_budget_minor=50000)
    assert r.ok and r.simulated and (r.resource_name or "").startswith("gads.SIM-")


async def test_live_but_unwired_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch, wired=False)  # enabled, but no customer id / tokens
    client = GoogleAdsClient()
    assert client.simulated is False
    with pytest.raises(GrowthOperatorError) as exc:
        await client.create_campaign(name="X", daily_budget_minor=50000)
    assert exc.value.code == "provider_unavailable"


async def test_live_two_step_creates_paused_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap))
    r = await GoogleAdsClient().create_campaign(name="Diwali", daily_budget_minor=50000)
    assert r.ok and not r.simulated and r.resource_name == "customers/1/campaigns/77"
    calls = cap["calls"]
    assert calls[0]["url"].endswith("/customers/1234567890/campaignBudgets:mutate")  # step 1
    # ₹500.00 (50000 paise) → 500 * 1_000_000 = 500_000_000 micros
    assert calls[0]["json"]["operations"][0]["create"]["amountMicros"] == "500000000"
    assert calls[0]["headers"]["developer-token"] == "DEVTOK"
    assert calls[0]["headers"]["Authorization"] == "Bearer OAUTH"  # tokens in headers, never logged
    campaign_op = calls[1]["json"]["operations"][0]["create"]
    assert calls[1]["url"].endswith("/customers/1234567890/campaigns:mutate")  # step 2
    assert campaign_op["status"] == "PAUSED"  # safety: never serves until resumed
    assert campaign_op["campaignBudget"] == "customers/1/campaignBudgets/9"


async def test_live_budget_failure_surfaces_not_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    cap: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post(cap, budget_code=400))
    r = await GoogleAdsClient().create_campaign(name="X", daily_budget_minor=50000)
    assert not r.ok and r.status_code == 400 and "budget create failed" in (r.error or "")
    assert len(cap["calls"]) == 1  # never attempted the campaign step
