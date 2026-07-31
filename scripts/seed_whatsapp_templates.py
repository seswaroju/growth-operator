"""Seed a pack's WhatsApp templates for an org and submit them to Meta (gated, MVP-035).

    uv run python scripts/seed_whatsapp_templates.py <org_id> [templates.yaml]

Upserts each declared template (default: the jewelry_v2 pack) org-scoped, then submits it for
Meta review. Submission is **gated** — with whatsapp_live_enabled off it runs simulated (no
real Meta call), which is the safe default until API access lands (BLOCKERS #3). The org must
already have an active WhatsApp channel (MVP-031 connect) so waba_id + credentials exist.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy import text

from core.channels.whatsapp import templates as tmpl
from core.channels.whatsapp.credentials import load_credentials
from core.tenancy.middleware import org_scoped_session

DEFAULT_MANIFEST = Path("verticals/jewelry/templates/whatsapp.yaml")


async def main(org_id: UUID, manifest_path: Path) -> None:
    data = yaml.safe_load(manifest_path.read_text())
    namespace, templates = data["namespace"], data["templates"]

    async with org_scoped_session(org_id) as session:
        channel = (
            await session.execute(
                text(
                    "SELECT id, waba_id FROM channels "
                    "WHERE type = 'whatsapp' AND status = 'active' LIMIT 1"
                )
            )
        ).mappings().first()
        if channel is None or not channel["waba_id"]:
            print("no active WhatsApp channel for this org — connect first (MVP-031)")
            return

        await tmpl.seed_from_manifest(session, org_id, templates, namespace=namespace)
        creds = await load_credentials(session, org_id=org_id, channel_id=channel["id"])
        assert creds is not None  # active channel always has stored credentials
        for t in templates:
            result = await tmpl.submit_template(
                session, org_id, template_key=t["template_key"], language=t["language"],
                waba_id=channel["waba_id"], access_token=creds["access_token"],
            )
            print(f"{t['template_key']}/{t['language']}: submitted ok={result.ok} "
                  f"id={result.provider_template_id}")


if __name__ == "__main__":
    org = UUID(sys.argv[1])
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MANIFEST
    asyncio.run(main(org, path))
