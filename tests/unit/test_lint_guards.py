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
