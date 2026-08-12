"""Permission-manifest compiler + signer (MVP-061).

At agent-instance activation (and on any grant change) the allowed tool surface is **compiled** as
the intersection of level-1 (archetype `capability_allowlist`, code constants) ∩ level-2 (pack
`agent_bindings.tool_grants` + `tier_defaults` + budgets) ∩ level-3 (tenant grants — optional),
then **signed** with the platform ed25519 key and pinned: its hash is stored on the instance and
stamped into every `agent_runs.permission_manifest_hash`. The mediation proxy verifies the hash
**and** the signature on every call (MVP-060 checked only the hash) — so a forged or stale manifest
denies every call, and a tampered one aborts the run.

Signing key: `manifest_signing_seed` (stable dev default, production via SOPS).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.writer import canonical_json
from core.common.config import get_settings

MANIFEST_VERSION = 3
UNTRUSTED_FLAGS = ["web_fetch", "file_ingest", "forwarded_content"]
_READ_ONLY_SUFFIXES = (".read", ".search")
# Tools that COMPUTE an internal artefact (e.g. a quote + its ledger rows) but take no customer-
# facing/committing action — no approval to run (only the SEND of the result is tier-gated), so they
# skip the tier gate. Unlike read-only tools they are NOT auto-added to the untrusted-narrowing
# allow-list: a run that ingested external content must not drive a quote from bad input.
_NO_TIER_TOOLS = frozenset({"pricing.compute", "landing_page.generate"})


def _seed() -> bytes:
    seed = base64.urlsafe_b64decode(get_settings().manifest_signing_seed)
    if len(seed) != 32:
        raise ValueError("manifest_signing_seed must decode to 32 bytes")
    return seed


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_seed())


def _verify_key() -> Ed25519PublicKey:
    return _signing_key().public_key()


def _body(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest minus its own hash/signature — what the hash and signature are computed over."""
    return {k: v for k, v in manifest.items() if k not in ("hash", "signature")}


def manifest_hash(manifest: dict[str, Any]) -> str:
    """The pinned hash (hex sha256 of the canonical body). Stamped on runs; matched by the proxy."""
    import hashlib

    return hashlib.sha256(canonical_json(_body(manifest)).encode()).hexdigest()


def _is_read_only(name: str) -> bool:
    return name.endswith(_READ_ONLY_SUFFIXES)


def compile_manifest(
    *, instance_id: UUID, org_id: UUID, allowlist: list[str], tool_grants: list[dict[str, Any]],
    tier_defaults: list[dict[str, Any]] | None = None, budgets: dict[str, Any] | None = None,
    tenant_allow: set[str] | None = None,
) -> dict[str, Any]:
    """Compile the manifest body (unsigned): archetype ∩ pack ∩ tenant. A read-only tool skips the
    tier gate; every other granted tool `requires_tier_eval` (the engine decides the tier)."""
    allowed = set(allowlist)
    tools: list[dict[str, Any]] = []
    read_only_names: list[str] = []
    for grant in tool_grants:
        name = grant.get("name")
        if not name or name not in allowed:
            continue  # level-1 ∩ level-2
        if tenant_allow is not None and name not in tenant_allow:
            continue  # level-3 narrowing
        tool: dict[str, Any] = {"name": name}
        if _is_read_only(name):
            tool["read_only"] = True
            read_only_names.append(name)  # usable under untrusted-content narrowing
        elif name in _NO_TIER_TOOLS:
            tool["read_only"] = True  # skip the tier gate, but NOT untrusted-narrowing-safe
        else:
            tool["requires_tier_eval"] = True
        if grant.get("rate_limit"):
            tool["rate_limit"] = grant["rate_limit"]
        if grant.get("params_constraints"):
            tool["params_constraints"] = grant["params_constraints"]
        tools.append(tool)
    return {
        "manifest_version": MANIFEST_VERSION,
        "instance_id": str(instance_id), "org_id": str(org_id),
        "compiled_at": datetime.now(UTC).isoformat(), "expires_at": None,
        "tools": tools,
        "budgets": dict(budgets or {}),
        "untrusted_narrowing": {"on_flags": UNTRUSTED_FLAGS, "allow": read_only_names},
    }


def sign(body: dict[str, Any]) -> dict[str, Any]:
    """Return a signed copy: `hash` (sha256 of the body) + `signature` (ed25519 over the body)."""
    manifest = _body(body)  # drop any pre-existing hash/sig
    digest = manifest_hash(manifest)
    signature = _signing_key().sign(canonical_json(manifest).encode())
    manifest["hash"] = f"sha256:{digest}"
    manifest["signature"] = "ed25519:" + base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return manifest


def compile_and_sign(**kwargs: Any) -> dict[str, Any]:
    return sign(compile_manifest(**kwargs))


def verify(manifest: dict[str, Any]) -> bool:
    """True iff the manifest's `hash` matches its body **and** its ed25519 `signature` is valid."""
    embedded = str(manifest.get("hash", "")).removeprefix("sha256:")
    if not embedded or embedded != manifest_hash(manifest):
        return False
    sig_field = str(manifest.get("signature", ""))
    if not sig_field.startswith("ed25519:"):
        return False
    raw = sig_field.removeprefix("ed25519:")
    try:
        signature = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        _verify_key().verify(signature, canonical_json(_body(manifest)).encode())
    except (InvalidSignature, ValueError):
        return False
    return True


async def recompile_instance(
    session: AsyncSession, org_id: UUID, instance_id: UUID
) -> dict[str, Any]:
    """Recompile + re-sign an instance's manifest from its current grants and pin it (call on grant
    change). Returns the new signed manifest; the caller commits."""
    from core.tenancy import repository

    await repository.set_org_context(session, org_id)
    row = (
        await session.execute(
            text(
                "SELECT aa.capability_allowlist, ab.tool_grants, ab.tier_defaults, ai.budget_caps "
                "FROM agent_instances ai "
                "JOIN agent_bindings ab ON ab.id = ai.binding_id "
                "JOIN agent_archetypes aa ON aa.id = ab.archetype_id "
                "WHERE ai.id = :id AND ai.org_id = :o"
            ),
            {"id": str(instance_id), "o": str(org_id)},
        )
    ).mappings().first()
    if row is None:
        raise ValueError(f"unknown agent instance {instance_id}")
    manifest = compile_and_sign(
        instance_id=instance_id, org_id=org_id,
        allowlist=list(row["capability_allowlist"] or []),
        tool_grants=list(row["tool_grants"] or []),
        tier_defaults=list(row["tier_defaults"] or []),
        budgets=dict(row["budget_caps"] or {}),
    )
    import json

    await session.execute(
        text("UPDATE agent_instances SET permission_manifest = CAST(:m AS jsonb) WHERE id = :id"),
        {"m": json.dumps(manifest), "id": str(instance_id)},
    )
    return manifest
