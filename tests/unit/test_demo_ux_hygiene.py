"""DEMO-UX-1 — fixture hygiene and the dev-only purge.

The founder's development database had accumulated **1171 billing plans**, 1010 of them from one
shared helper, which made the operator console's plan list unusable. The plans were never the
problem; the helper creating a row it did not own was.

These tests cover the fix and the guardrails on the cleanup tool. Nothing here talks to a network.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PURGE = ROOT / "scripts/dev_purge_fixtures.py"
CONFTEST = ROOT / "tests/conftest.py"


# ---- the leak is fixed at its source -----------------------------------------------------------


def test_the_shared_helper_owns_the_rows_it_creates() -> None:
    """`entitle_org` inserts a plan per call. Before this, callers deleted their orgs and
    subscriptions but not the plan — the id was usually discarded — so every run leaked."""
    source = CONFTEST.read_text()
    assert "_CREATED_PLAN_IDS" in source
    assert "_purge_test_plans" in source


def test_cleanup_selects_by_id_never_by_name() -> None:
    """The safety property §8 asks for.

    The first version deleted `WHERE name LIKE 'ent-%'`, which would also have removed a plan the
    founder happened to call `ent-anything`. Scope is now the set of ids this process inserted: a
    row it did not create cannot be deleted, whatever it is named. Canonical presets, operator
    plans and founder plans are all out of reach by construction rather than by careful matching.
    """
    source = CONFTEST.read_text()
    assert "DELETE FROM billing_plans WHERE id = ANY($1::uuid[])" in source
    assert "DELETE FROM billing_plans WHERE name LIKE" not in source
    assert "DELETE FROM billing_subscriptions WHERE plan_id = ANY($1::uuid[])" in source


def test_cleanup_is_transactional() -> None:
    """Subscriptions and plans go together or not at all."""
    tree = ast.parse(CONFTEST.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_purge_test_plans")
    assert "async with conn.transaction():" in ast.unparse(fn)


def test_cleanup_does_nothing_when_this_process_created_nothing() -> None:
    """A run that inserted no plans must not open a connection or delete anything at all."""
    tree = ast.parse(CONFTEST.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_purge_test_plans")
    body = ast.unparse(fn)
    assert "if not _CREATED_PLAN_IDS:" in body
    assert body.index("if not _CREATED_PLAN_IDS:") < body.index("asyncpg.connect")


def test_the_purge_fixture_is_session_scoped_and_automatic() -> None:
    """A convention every future fixture must remember is one that will be forgotten."""
    tree = ast.parse(CONFTEST.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_purge_test_plans")
    decorator = ast.unparse(fn.decorator_list[0]).replace("'", '"')
    assert "autouse=True" in decorator and 'scope="session"' in decorator


def test_subscriptions_are_removed_before_their_plans() -> None:
    """The foreign key refuses otherwise, and a subscription outliving its plan would be worse
    than the leak this fixes."""
    source = CONFTEST.read_text()
    subs = source.index("DELETE FROM billing_subscriptions WHERE plan_id = ANY")
    plans = source.index("DELETE FROM billing_plans WHERE id = ANY")
    assert subs < plans


def test_teardown_never_fails_the_suite() -> None:
    """A database unreachable at teardown must not turn a green run red."""
    assert "teardown hygiene must never fail the suite" in CONFTEST.read_text()


def test_the_campaigns_fixture_removes_its_own_plan_by_id() -> None:
    campaigns = (ROOT / "tests/integration/test_campaigns.py").read_text()
    assert "DELETE FROM billing_plans WHERE id = $1" in campaigns
    subs = campaigns.index("DELETE FROM billing_subscriptions WHERE org_id")
    orgs = campaigns.index("DELETE FROM organizations WHERE id")
    assert subs < orgs, "subscriptions must go before the organizations they reference"


def test_no_fixture_deletes_plans_by_a_hardcoded_name_pattern() -> None:
    """Swept across the whole suite. The distinction that matters is ownership, not syntax.

    A **hardcoded** pattern — `"ent-%"`, `"CampPlan-%"` — matches every row every run has ever
    created, plus any row the founder happened to name that way. That is what accumulated 1136
    plans and what made cleanup dangerous.

    A **per-run tag** passed as a parameter (`f"{tag}%"` where `tag` is generated at fixture setup)
    can only match rows that run inserted. That is ownership expressed through a unique name
    instead of an id, and it is safe.

    So this forbids a literal pattern and permits an interpolated one.
    """
    literal_pattern = re.compile(r'"[A-Za-z][\w.-]*%"')
    offenders: list[str] = []
    for path in (ROOT / "tests").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue  # this file necessarily contains the strings it forbids
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "DELETE FROM billing_plans" in line and literal_pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number} {line.strip()[:60]}")
    assert not offenders, f"plan deletion by a hardcoded name pattern: {offenders}"


# ---- the purge tool refuses to be dangerous ----------------------------------------------------


def test_purge_refuses_outside_dev() -> None:
    """Three independent conditions, none of them convenient to bypass."""
    source = PURGE.read_text()
    assert 'settings.env != "dev"' in source
    assert "refusing:" in source


def test_purge_refuses_a_non_local_database() -> None:
    """`env=dev` pointed at a remote host is exactly the accident worth preventing."""
    source = PURGE.read_text()
    assert "does not look local" in source
    assert '"localhost"' in source and '"127.0.0.1"' in source


def test_purge_is_a_dry_run_unless_ids_are_named() -> None:
    """Deleting only what was explicitly named is the whole safety posture of this tool."""
    tree = ast.parse(PURGE.read_text())
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.unparse(main)
    assert "--plan-ids" in body and "--store-ids" in body
    assert "DRY RUN" in PURGE.read_text()


def test_canonical_plans_are_never_purged() -> None:
    """Recover/Grow/Scale are code-managed commercial truth. A cleanup script is not the place to
    remove them, and if it could, someone would eventually run it in the wrong terminal."""
    from scripts.dev_purge_fixtures import CANONICAL_PLANS, FIXTURE_PLAN_PREFIXES

    assert set(CANONICAL_PLANS) == {"Recover", "Grow", "Scale"}
    for canonical in CANONICAL_PLANS:
        assert not canonical.startswith(tuple(FIXTURE_PLAN_PREFIXES))


def test_purge_matches_an_allow_list_not_a_heuristic() -> None:
    """A pattern broad enough to catch every fixture is broad enough to catch a real store called
    "Test Jewellers"."""
    from scripts.dev_purge_fixtures import FIXTURE_ORG_PREFIXES, FIXTURE_PLAN_PREFIXES

    assert all(p for p in FIXTURE_PLAN_PREFIXES)
    assert all(p for p in FIXTURE_ORG_PREFIXES)
    # No prefix may be so short that it matches ordinary words.
    assert all(len(p) >= 2 for p in FIXTURE_PLAN_PREFIXES + FIXTURE_ORG_PREFIXES)


def test_stores_with_history_are_skipped() -> None:
    """More likely a demo tenant the founder built than a stray fixture, and a cleanup tool that
    guesses wrong destroys work."""
    from scripts.dev_purge_fixtures import HISTORY_TABLES

    assert {"messages", "conversations", "audit_log"} <= set(HISTORY_TABLES)
    assert "looks like real work" in PURGE.read_text()


def test_the_purge_runs_in_one_transaction() -> None:
    """A half-purged graph — plans gone, stores left — is worse than either outcome."""
    assert "async with conn.transaction():" in PURGE.read_text()


def test_no_production_web_surface_exposes_the_purge() -> None:
    """§4.3/§5.2: a CLI, never a button. A destructive action reachable from the operator console
    is one misclick from deleting a merchant."""
    for path in (ROOT / "core").rglob("*.py"):
        assert "dev_purge_fixtures" not in path.read_text(), f"{path} imports the purge tool"


@pytest.mark.parametrize("app", ["web", "web-ops"])
def test_no_frontend_references_the_purge(app: str) -> None:
    for path in (ROOT / app / "src").rglob("*.ts*"):
        assert "dev_purge" not in path.read_text()


# ---- destruction requires explicit ids (review §7) ----------------------------------------------


def test_destruction_requires_explicitly_named_ids() -> None:
    """Discovery and destruction are separate concerns.

    Prefix matching is a good way to *find* candidates and a bad way to *authorise* deleting them:
    `Alpha`, `Beta` and `TB` are fixture names today and perfectly plausible names for a real
    store, and five history-table counts are not enough evidence to destroy somebody's tenant. The
    script will not act on its own guess."""
    source = PURGE.read_text()
    assert "--plan-ids" in source and "--store-ids" in source
    assert "--yes" not in source, "a blanket confirmation flag is exactly what was removed"
    assert "DRY RUN — nothing deleted." in source


def test_named_ids_are_still_checked_against_the_safety_rules() -> None:
    """An explicit id is authorisation, not a bypass: discovery still refuses a store holding real
    conversation history, so a mistyped id cannot destroy a demo tenant."""
    source = PURGE.read_text()
    assert "not a disposable fixture in this database" in source
    assert "not an empty fixture store in this database" in source


def test_a_dry_run_prints_the_ids_needed_to_act() -> None:
    """Otherwise the safety measure is just an obstacle, and obstacles get worked around."""
    assert "To delete, name the ids explicitly" in PURGE.read_text()
