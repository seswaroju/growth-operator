"""Lead origin vocabulary + "captured from" presentation (LEAD-1) — pure."""

from __future__ import annotations

from core.customers import origins


def test_vocabulary_covers_every_approved_origin() -> None:
    assert set(origins.LEAD_SOURCES) == {
        "landing_page", "whatsapp", "instagram", "campaign", "walk_in", "referral", "manual"}
    # every origin has an owner-facing label
    assert all(origins.label(s) != "Unknown" for s in origins.LEAD_SOURCES)


def test_validation_and_normalisation() -> None:
    assert origins.is_valid("whatsapp") and origins.is_valid("walk_in")
    assert not origins.is_valid("carrier-pigeon") and not origins.is_valid(None)
    assert origins.normalize("instagram") == "instagram"
    assert origins.normalize("carrier-pigeon") is None  # junk is never persisted as real
    assert origins.normalize(None) is None


def test_unknown_source_presents_as_unknown() -> None:
    assert origins.label(None) == "Unknown"
    assert origins.describe({"source": None}) == "Unknown"  # pre-LEAD-1 rows never crash


def test_landing_origin_names_the_page_and_variant() -> None:
    row = {"source": "landing_page", "landing_slug": "diwali-diamond", "variant": "story"}
    assert origins.describe(row) == "Landing page · diwali-diamond (story)"
    assert origins.describe({"source": "landing_page", "landing_slug": "diwali-diamond"}) == (
        "Landing page · diwali-diamond")
    assert origins.describe({"source": "landing_page"}) == "Landing page"  # page since deleted


def test_channel_and_campaign_origins() -> None:
    # a WhatsApp lead (e.g. the link in an Instagram bio) reads as WhatsApp
    assert origins.describe({"source": "whatsapp"}) == "WhatsApp"
    # a link that carried utm gets the campaign appended
    assert origins.describe(
        {"source": "campaign", "utm": {"campaign": "diwali-push"}}) == "Campaign · diwali-push"
    # offline origins
    assert origins.describe({"source": "walk_in"}) == "Walk-in"
    assert origins.describe({"source": "referral"}) == "Referral"
    assert origins.describe({"source": "manual"}) == "Added manually"


def test_describe_tolerates_bad_shapes() -> None:
    assert origins.describe({}) == "Unknown"
    assert origins.describe({"source": "whatsapp", "utm": "not-a-dict"}) == "WhatsApp"
    # an unrecorded source but a known channel falls back to the channel
    assert origins.describe({"source": None, "channel_type": "instagram"}) == "Instagram"
