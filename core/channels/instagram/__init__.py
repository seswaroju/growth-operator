"""Instagram content-publishing adapter (B1) — gated, mirrors the Meta/WhatsApp client.

The one place a real Instagram post is published (a catalog piece / promo for a store's feed).
**Gated closed by default**: `publish()` runs SIMULATED (a fake media id, no network) unless
`instagram_live_enabled` is on. Enabled but not wired (`instagram_ig_user_id` +
`instagram_access_token`) fails closed with `provider_unavailable`. The real path is the Instagram
Graph API two-step (create a media container, then publish it) over httpx. A real post never leaves
without the gate AND an approved action (§10.4 — publishing to social media is an external action).

Same Graph host + gating shape as `core.channels.whatsapp.meta_client`; switching the flag on is the
only change needed. Nothing here logs the access token.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from core.common.config import Settings, get_settings
from core.common.errors import GrowthOperatorError

GRAPH_BASE = "https://graph.facebook.com/v20.0"
_TIMEOUT = httpx.Timeout(15.0)


@dataclass
class PublishResult:
    ok: bool
    provider_media_id: str | None = None
    simulated: bool = False
    status_code: int | None = None
    error: str | None = None


class InstagramClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def simulated(self) -> bool:
        return not self.settings.instagram_live_enabled

    def _require_wired(self) -> tuple[str, str]:
        s = self.settings
        if not s.instagram_ig_user_id or not s.instagram_access_token:
            raise GrowthOperatorError(
                "provider_unavailable", "instagram enabled but ig_user_id/token not configured")
        return s.instagram_ig_user_id, s.instagram_access_token

    async def publish(self, *, image_url: str, caption: str) -> PublishResult:
        """Publish an image post to the connected IG business account — simulated unless the adapter
        is live + wired. Two-step: create a media container, then publish it."""
        media_id = uuid.uuid4().hex[:16]
        if self.simulated:
            return PublishResult(
                ok=True, simulated=True, provider_media_id=f"ig.SIM-{media_id}")
        ig_user_id, token = self._require_wired()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                created = await client.post(
                    f"{GRAPH_BASE}/{ig_user_id}/media",
                    data={"image_url": image_url, "caption": caption, "access_token": token},
                )
                if created.status_code != 200:
                    return PublishResult(
                        ok=False, status_code=created.status_code,
                        error=f"container create failed ({created.status_code})")
                creation_id = created.json().get("id")
                if not creation_id:
                    return PublishResult(ok=False, error="no creation id returned")
                published = await client.post(
                    f"{GRAPH_BASE}/{ig_user_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": token},
                )
        except httpx.HTTPError as exc:  # network failures surface as a failed result, not a crash
            return PublishResult(ok=False, error=str(exc)[:200])
        if published.status_code != 200:
            return PublishResult(
                ok=False, status_code=published.status_code,
                error=f"publish failed ({published.status_code})")
        return PublishResult(
            ok=True, status_code=200, provider_media_id=str(published.json().get("id") or media_id))
