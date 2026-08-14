"""DEMO-UX-1 — fixture hygiene and the dev-only purge.

The founder's development database had accumulated **1171 billing plans**, 1010 of them from one
shared helper, which made the operator console's plan list unusable. The plans were never the
problem; the helper creating a row it did not own was.

These tests cover the fix and the guardrails on the cleanup tool. Nothing here talks to a network.
"""

from __future__ import annotations

import ast
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
    assert "TEST_PLAN_PREFIX" in source
    assert "_purge_test_plans" in source
    assert "DELETE FROM billing_plans WHERE name LIKE" in source


def test_the_purge_fixture_is_session_scoped_and_automatic() -> None:
    """A convention every future fixture must remember is a convention that will be forgotten."""
    tree = ast.parse(CONFTEST.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_purge_test_plans")
    # ast.unparse normalises quoting, so compare against the normalised form.
    decorator = ast.unparse(fn.decorator_list[0]).replace("'", '"')
    assert "autouse=True" in decorator and 'scope="session"' in decorator


def test_subscriptions_are_removed_before_their_plans() -> None:
    """The foreign key refuses otherwise, and a subscription outliving its plan would be worse
    than the leak it replaced."""
    source = CONFTEST.read_text()
    subs = source.index("DELETE FROM billing_subscriptions WHERE plan_id IN")
    plans = source.index("DELETE FROM billing_plans WHERE name LIKE")
    assert subs < plans


def test_teardown_never_fails_the_suite() -> None:
    """A database unreachable at teardown must not turn a green run red."""
    source = CONFTEST.read_text()
    assert "teardown hygiene must never fail the suite" in source


def test_the_campaigns_fixture_removes_its_own_plan() -> None:
    campaigns = (ROOT / "tests/integration/test_campaigns.py").read_text()
    assert 'DELETE FROM billing_plans WHERE name LIKE' in campaigns
    subs = campaigns.index("DELETE FROM billing_subscriptions WHERE org_id")
    orgs = campaigns.index("DELETE FROM organizations WHERE id")
    assert subs < orgs, "subscriptions must go before the organizations they reference"


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


def test_purge_is_a_dry_run_by_default() -> None:
    tree = ast.parse(PURGE.read_text())
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert "--yes" in ast.unparse(main)
    # Deleting only when explicitly asked is the whole safety posture of this tool.
    assert "apply=args.yes" in ast.unparse(main)
    assert "dry run" in PURGE.read_text()


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
