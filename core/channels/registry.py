"""Declarative registry of the channel types the operator can wire per store (CP-4).

Adding a channel is one entry here: the credential fields it needs (what the operator pastes) and
which of those fields becomes the store-facing `channels.external_id`. The operator channel-setup
API and the web-ops form are both driven by this table, so a new channel (e.g. `tiktok`) is a data
change, not new endpoint/form code.

Rule Zero: channel *types* (whatsapp, instagram, …) are platform-invariant concepts, not
industry-specific nouns — they belong in `core/`. Credential values are secrets: this module only
names the fields, never holds a value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelType:
    type: str
    label: str
    credential_fields: tuple[str, ...]  # required keys the operator must paste
    external_id_field: str  # which credential field identifies the account (channels.external_id)


# The wired-up channels. `external_id_field` must be one of `credential_fields`.
CHANNEL_TYPES: dict[str, ChannelType] = {
    "whatsapp": ChannelType(
        "whatsapp", "WhatsApp",
        ("waba_id", "phone_number_id", "access_token"), "phone_number_id"),
    "instagram": ChannelType(
        "instagram", "Instagram", ("ig_user_id", "access_token"), "ig_user_id"),
    "google_ads": ChannelType(
        "google_ads", "Google Ads",
        ("customer_id", "developer_token", "access_token"), "customer_id"),
}


def get_channel_type(type_: str) -> ChannelType | None:
    return CHANNEL_TYPES.get(type_)


def missing_fields(spec: ChannelType, credentials: dict[str, object]) -> list[str]:
    """Required credential fields that are absent or blank in `credentials`."""
    return [
        f for f in spec.credential_fields
        if not str(credentials.get(f, "")).strip()
    ]
