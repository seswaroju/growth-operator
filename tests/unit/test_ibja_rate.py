"""IBJA gold-rate HTTP source (BLOCKER #5) — parse + gated fetch, with the HTTP call mocked.

No network: the fetch tests monkeypatch `httpx.AsyncClient.get`. The gate stays fail-closed until
`rates_provider_enabled`, and only `ibja_gold` is wired (other sources stay on manual entry).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.pricing.functions import PricingError
from core.pricing.rates import HttpRateFetcher, parse_ibja_gold

# A representative /latest body: ₹/gram per fineness, AM + PM sessions.
_SAMPLE: dict[str, Any] = {
    "date": "2026-08-12",
    "lblGold999_AM": "7850.00", "lblGold999_PM": "7860.00",
    "lblGold916_AM": "7190.00", "lblGold916_PM": "7200.00",
    "lblGold750_PM": "5890.00",
    "lblGold585_AM": "4590.00",  # no PM → falls back to AM
}


def test_parse_prefers_pm_and_converts_gram_to_paise() -> None:
    v = parse_ibja_gold(_SAMPLE)
    assert v == {"24K": 786000, "22K": 720000, "18K": 589000, "14K": 459000}


def test_parse_falls_back_to_am_when_no_pm() -> None:
    assert parse_ibja_gold({"lblGold916_AM": "7000.00"}) == {"22K": 700000}


def test_parse_skips_blank_and_unparseable() -> None:
    assert parse_ibja_gold({"lblGold916_PM": "", "lblGold999_PM": "7800.00"}) == {"24K": 780000}


def test_parse_no_usable_rate_raises() -> None:
    with pytest.raises(PricingError):
        parse_ibja_gold({"date": "2026-08-12"})


async def test_fetch_is_gated_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_RATES_PROVIDER_ENABLED", "false")
    with pytest.raises(PricingError):
        await HttpRateFetcher().fetch("ibja_gold", {})


def _fake_get(resp: httpx.Response) -> Any:
    async def get(self: Any, url: str, **_: Any) -> httpx.Response:
        resp.request = httpx.Request("GET", url)
        return resp
    return get


async def test_fetch_parses_a_live_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_RATES_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get(httpx.Response(200, json=_SAMPLE)))
    v = await HttpRateFetcher().fetch("ibja_gold", {})
    assert v["22K"] == 720000 and v["24K"] == 786000


async def test_fetch_uses_fetch_spec_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_RATES_PROVIDER_ENABLED", "true")
    seen: dict[str, str] = {}

    async def get(self: Any, url: str, **_: Any) -> httpx.Response:
        seen["url"] = url
        r = httpx.Response(200, json={"lblGold916_PM": "7100.00"})
        r.request = httpx.Request("GET", url)
        return r

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    v = await HttpRateFetcher().fetch("ibja_gold", {"url": "https://example.test/gold"})
    assert seen["url"] == "https://example.test/gold" and v == {"22K": 710000}


async def test_fetch_non_gold_source_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_RATES_PROVIDER_ENABLED", "true")
    with pytest.raises(PricingError):
        await HttpRateFetcher().fetch("ibja_silver", {})


async def test_fetch_http_error_fails_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_RATES_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get(httpx.Response(500, text="err")))
    with pytest.raises(PricingError):  # a 5xx surfaces as provider_unavailable, not a raw crash
        await HttpRateFetcher().fetch("ibja_gold", {})
