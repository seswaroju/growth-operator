"""Customer notes + tags (D2) — add/list notes, idempotent tag add/remove, notes surface in the
activity timeline, and every op is org-scoped (org A can't annotate or read org B's customer).
Skips when the DB (migration 040) is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from core.common import db as dbmod
from core.common.config import get_settings
from core.customers import annotations, service
from core.tenancy.middleware import org_scoped_session


def _dsn() -> str:
    return get_settings().database_migrator_url.replace("+asyncpg", "")


async def _db_ready() -> bool:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=3)
    except Exception:
        return False
    try:
        return bool(await conn.fetchval("SELECT to_regclass('public.contact_tags')"))
    finally:
        await conn.close()


@dataclass
class Scene:
    org: uuid.UUID
    user: uuid.UUID
    contact: uuid.UUID
    other_org: uuid.UUID
    other_contact: uuid.UUID  # a DIFFERENT org's contact


async def _seed(conn: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    org = uuid.uuid4()
    await conn.execute("INSERT INTO organizations (id, name) VALUES ($1,'AN')", org)
    user = await conn.fetchval(
        "INSERT INTO users (id, email) VALUES ($1,$2) RETURNING id",
        uuid.uuid4(), f"u{uuid.uuid4().hex[:8]}@x.test")
    ct = await conn.fetchval("INSERT INTO contacts (org_id) VALUES ($1) RETURNING id", org)
    return org, user, ct


@pytest.fixture()
async def scene() -> AsyncIterator[Scene]:
    if not await _db_ready():
        pytest.skip("Postgres/migration 040 not ready")
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()
    conn = await asyncpg.connect(_dsn())
    try:
        org, user, ct = await _seed(conn)
        other_org, _, other_ct = await _seed(conn)
    finally:
        await conn.close()
    yield Scene(org, user, ct, other_org, other_ct)
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [org, other_org])
        await conn.execute("DELETE FROM users WHERE id=$1", user)
    finally:
        await conn.close()
    await dbmod.get_engine().dispose()
    dbmod.get_engine.cache_clear()
    dbmod.get_sessionmaker.cache_clear()


async def test_add_and_list_notes(scene: Scene) -> None:
    # Separate transactions (like separate requests), so their `now()` differs → stable order.
    async with org_scoped_session(scene.org) as s:
        n1 = await annotations.add_note(
            s, scene.org, scene.contact, author_user_id=scene.user, body="Prefers antique gold")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        await annotations.add_note(
            s, scene.org, scene.contact, author_user_id=scene.user, body="Wedding in November")
        await s.commit()
    assert n1 is not None and n1["body"] == "Prefers antique gold"
    async with org_scoped_session(scene.org) as s:
        notes = await annotations.list_notes(s, scene.org, scene.contact)
    assert notes is not None and len(notes) == 2
    assert notes[0]["body"] == "Wedding in November"  # newest first
    assert notes[1]["author_user_id"] == scene.user


async def test_tags_idempotent_add_and_remove(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        assert await annotations.add_tag(
            s, scene.org, scene.contact, tag="vip", created_by=None) is True
        assert await annotations.add_tag(
            s, scene.org, scene.contact, tag="vip", created_by=None) is False  # idempotent
        await annotations.add_tag(s, scene.org, scene.contact, tag="bridal", created_by=None)
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        assert await annotations.list_tags(s, scene.org, scene.contact) == ["bridal", "vip"]
    async with org_scoped_session(scene.org) as s:
        assert await annotations.remove_tag(s, scene.org, scene.contact, tag="vip") is True
        assert await annotations.remove_tag(s, scene.org, scene.contact, tag="vip") is False
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        assert await annotations.list_tags(s, scene.org, scene.contact) == ["bridal"]


async def test_note_appears_in_timeline(scene: Scene) -> None:
    async with org_scoped_session(scene.org) as s:
        await annotations.add_note(
            s, scene.org, scene.contact, author_user_id=scene.user, body="Called about a bangle")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        tl = await service.customer_timeline(s, scene.org, scene.contact)
    assert tl is not None
    note = next((e for e in tl if e["kind"] == "note"), None)
    assert note is not None and "bangle" in note["detail"]["preview"]


async def test_annotations_are_org_scoped(scene: Scene) -> None:
    # Every op against another org's contact returns None (→ 404), never touching its rows.
    async with org_scoped_session(scene.org) as s:
        assert await annotations.add_note(
            s, scene.org, scene.other_contact, author_user_id=scene.user, body="x") is None
        assert await annotations.list_notes(s, scene.org, scene.other_contact) is None
        assert await annotations.add_tag(
            s, scene.org, scene.other_contact, tag="vip", created_by=None) is None
        assert await annotations.list_tags(s, scene.org, scene.other_contact) is None
        assert await annotations.remove_tag(s, scene.org, scene.other_contact, tag="vip") is None


async def test_rls_hides_other_orgs_notes(scene: Scene) -> None:
    # RLS (not just the explicit org_id filter): seed a note for org B, then under org A's context
    # an UNFILTERED read sees none of B's rows — cross-tenant isolation fails closed at the DB.
    from sqlalchemy import text
    async with org_scoped_session(scene.other_org) as s:
        await annotations.add_note(
            s, scene.other_org, scene.other_contact, author_user_id=None, body="B's private note")
        await s.commit()
    async with org_scoped_session(scene.org) as s:
        visible = (
            await s.execute(text("SELECT count(*) FROM customer_notes"))
        ).scalar_one()  # no WHERE org_id — RLS is the only thing scoping this
    assert visible == 0  # org A sees zero of org B's notes
