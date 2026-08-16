"""Tests for the MVP-010 architecture guards.

Each guard must go red on its own violation (fixture files in a tmp dir), the real repo
must be clean, and the allowlist must require a justification comment.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("guards", _REPO / "scripts" / "guards.py")
assert _spec and _spec.loader
guards = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = guards  # dataclass needs the module registered before exec
_spec.loader.exec_module(guards)


def _core(tmp_path: Path) -> Path:
    core = tmp_path / "core"
    core.mkdir()
    return core


def test_core_not_verticals_goes_red(tmp_path: Path) -> None:
    core = _core(tmp_path)
    (core / "x.py").write_text("from verticals.jewelry import pack\n")
    v = guards.guard_core_not_verticals(core=core)
    assert len(v) == 1
    assert v[0].guard == "core-not-verticals"


def test_industry_nouns_goes_red(tmp_path: Path) -> None:
    core = _core(tmp_path)
    (core / "x.py").write_text("gold_rate = 5000  # karat pricing\n")
    v = guards.guard_industry_nouns(core=core, web=tmp_path / "nope")
    assert v and all(n.guard == "industry-nouns" for n in v)


def test_float_money_goes_red(tmp_path: Path) -> None:
    core = _core(tmp_path)
    (core / "x.py").write_text("price_minor = float(user_input)\n")
    v = guards.guard_float_money(core=core)
    assert len(v) == 1


def test_send_call_sites_goes_red_outside_adapter(tmp_path: Path) -> None:
    core = _core(tmp_path)
    (core / "x.py").write_text("messages.send(draft)\n")
    v = guards.guard_send_call_sites(core=core, adapter=core / "channels")
    assert len(v) == 1


def test_send_call_sites_allowed_inside_adapter(tmp_path: Path) -> None:
    core = _core(tmp_path)
    adapter = core / "channels"
    adapter.mkdir()
    (adapter / "whatsapp.py").write_text("messages.send(draft)\n")
    assert guards.guard_send_call_sites(core=core, adapter=adapter) == []


def test_session_set_ban_goes_red_on_session_set(tmp_path: Path) -> None:
    core = _core(tmp_path)
    (core / "a.py").write_text('conn.execute("SET app.org_id = :x")\n')  # session-level SET
    (core / "b.py").write_text("q = \"set_config('app.org_id', v, false)\"\n")  # false == session
    v = guards.guard_session_set(core=core)
    assert len(v) == 2
    assert all(n.guard == "session-set-ban" for n in v)


def test_session_set_ban_allows_transaction_local(tmp_path: Path) -> None:
    core = _core(tmp_path)
    # SET LOCAL and set_config(..., true) are the allowed, txn-local forms.
    (core / "ok.py").write_text(
        'x = "SET LOCAL app.org_id = 1"\n'
        "y = \"set_config('app.user_id', v, true)\"\n"
        'z = "reset value"  # must not trip on the word reset\n'
    )
    assert guards.guard_session_set(core=core) == []


def test_runtime_not_tools_goes_red_on_direct_tool_import(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "a.py").write_text("from core.catalog.search import hybrid_search\n")
    (runtime / "b.py").write_text("from core.mediation.tools import REGISTRY\n")
    (runtime / "c.py").write_text("from core.pricing.service import compute_quote\n")
    v = guards.guard_runtime_not_tools(runtime=runtime)
    assert len(v) == 3
    assert all(n.guard == "runtime-not-tools" for n in v)


def test_runtime_not_tools_allows_the_proxy(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    # The proxy is the sanctioned path; pure/support modules are fine too.
    (runtime / "ok.py").write_text(
        "from core.mediation.proxy import RunContext, call\n"
        "from core.pricing.engine import compute\n"  # pure money engine, not a tool impl
    )
    assert guards.guard_runtime_not_tools(runtime=runtime) == []


def _tests(tmp_path: Path) -> Path:
    d = tmp_path / "tests"
    d.mkdir()
    return d


def test_test_policy_writes_goes_red_on_the_original_incident(tmp_path: Path) -> None:
    """The literal statement that corrupted a live pilot store's policies on the shared database."""
    t = _tests(tmp_path)
    (t / "t.py").write_text(
        "await conn.execute(\n"
        "    \"UPDATE approval_policies SET tier=2 WHERE action_type='action.message.send'\")\n")
    v = guards.guard_test_policy_writes(tests=t)
    assert len(v) == 1 and v[0].guard == "test-policy-writes"


def test_test_policy_writes_goes_red_on_an_unscoped_delete(tmp_path: Path) -> None:
    """A DELETE reaches just as far as an UPDATE — `approval_policies` pack rows are global."""
    t = _tests(tmp_path)
    (t / "t.py").write_text("await conn.execute(\"DELETE FROM approval_policies\")\n")
    assert len(guards.guard_test_policy_writes(tests=t)) == 1


def test_test_policy_writes_allows_a_pack_scoped_write(tmp_path: Path) -> None:
    t = _tests(tmp_path)
    (t / "t.py").write_text(
        "await conn.execute(\"DELETE FROM approval_policies WHERE pack_id=$1\", pack)\n")
    assert guards.guard_test_policy_writes(tests=t) == []


def test_test_policy_writes_allows_scope_on_a_following_line(tmp_path: Path) -> None:
    """These statements are normally split across string literals. A line-by-line check would
    call the fixed `test_send_loop` a violation, which is how a guard trains people to ignore it."""
    t = _tests(tmp_path)
    (t / "t.py").write_text(
        "await conn.execute(\n"
        "    \"UPDATE approval_policies SET tier=2 \"\n"
        "    \"WHERE pack_id=$1 AND scope='pack' AND action_type='action.message.send'\",\n"
        "    scene.pack)\n")
    assert guards.guard_test_policy_writes(tests=t) == []


def test_test_policy_writes_allows_an_org_scoped_write(tmp_path: Path) -> None:
    t = _tests(tmp_path)
    (t / "t.py").write_text(
        "await conn.execute(\"DELETE FROM approval_policies WHERE org_id=$1\", org)\n")
    assert guards.guard_test_policy_writes(tests=t) == []


def test_repo_is_clean() -> None:
    violations, errors = guards.run_all()
    assert violations == []
    assert errors == []


def test_allowlist_requires_justification(tmp_path: Path) -> None:
    al = tmp_path / "allow.txt"
    al.write_text("core/x.py::gold\n")  # missing '# justification'
    entries, errors = guards.load_allowlist(al)
    assert entries == []
    assert errors and "justification" in errors[0]


def test_allowlist_excuses_with_justification(tmp_path: Path) -> None:
    al = tmp_path / "allow.txt"
    al.write_text("core/x.py::gold   # appears in a doc URL, not industry logic\n")
    entries, errors = guards.load_allowlist(al)
    assert errors == []
    v = guards.Violation("industry-nouns", "core/x.py", 1, "gold_rate = 1")
    assert guards._excused(v, entries) is True
