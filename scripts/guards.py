"""Architecture lint guards (MVP-010).

Four grep/AST-light guards enforcing platform invariants in CI, not memory:

  1. core-not-verticals : `core/` must never import `verticals/` (Rule Zero, §11.3).
  2. industry-nouns     : no vertical-specific nouns in `core/` or `web/src/`.
  3. float-money        : money is integer minor units — never `float()` near a `*_minor`.
  4. send-call-sites    : `messages.send(...)` only inside the channel adapter (`core/channels/`).
  5. session-set-ban    : tenant GUCs must be transaction-local — no session-level
                          `SET app.*` / `set_config('app.*', v, false)` (MVP-016; a leaked
                          GUC on a pooled connection would cross tenants).

False positives are excused via `scripts/lint-allowlist.txt`, where every entry REQUIRES a
`# justification` comment — an entry without one is itself a failure.

Run: `python scripts/guards.py` (exit 0 clean, 1 on any violation). The guard functions
take explicit roots so they are unit-testable against fixtures.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "core"
WEB = REPO / "web" / "src"
ADAPTER_DIR = REPO / "core" / "channels"
ALLOWLIST = REPO / "scripts" / "lint-allowlist.txt"

# Unambiguous vertical nouns only. Generic words that collide with common code/CSS
# (e.g. "ring" — focus:ring, ring buffer) are deliberately excluded to keep the guard
# low-false-positive; the allowlist is for rare exceptions, not pervasive words.
INDUSTRY_NOUNS = [
    "gold", "karat", "carat", "jewelry", "jewellery", "necklace", "diamond",
    "bangle", "pendant", "mangalsutra", "restaurant", "kirana",
]


@dataclass(frozen=True)
class Violation:
    guard: str
    file: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"[{self.guard}] {self.file}:{self.line}: {self.text}"


def _rel(f: Path) -> str:
    try:
        return str(f.relative_to(REPO))
    except ValueError:
        return str(f)


def _py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py")) if root.exists() else []


def _web_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for ext in ("*.ts", "*.tsx") for p in root.rglob(ext))


def _iter_lines(files: list[Path]):
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            yield f, i, line


_VERTICALS_IMPORT = re.compile(r"^\s*(from|import)\s+verticals(\.|\s|$)")
_FLOAT_CALL = re.compile(r"\bfloat\s*\(")
_MINOR_TOKEN = re.compile(r"_minor\b")
_SEND_CALL = re.compile(r"\bmessages\.send\s*\(")
# Session-level tenant-GUC setters (banned): `SET app.` / `SET SESSION app.` (but NOT
# `SET LOCAL app.`), and `set_config('app.*', v, false)` (false == session-level).
_SESSION_SET_SQL = re.compile(r"\bSET\s+(SESSION\s+)?app\.", re.I)
_SESSION_SETCONFIG = re.compile(
    r"set_config\(\s*'app\.[^']*'\s*,[^,]*,\s*false\s*\)", re.I
)


def guard_core_not_verticals(core: Path = CORE) -> list[Violation]:
    return [
        Violation("core-not-verticals", _rel(f), i, line.strip())
        for f, i, line in _iter_lines(_py_files(core))
        if _VERTICALS_IMPORT.search(line)
    ]


def guard_industry_nouns(core: Path = CORE, web: Path = WEB) -> list[Violation]:
    pat = re.compile(r"\b(" + "|".join(re.escape(n) for n in INDUSTRY_NOUNS) + r")\b", re.I)
    files = _py_files(core) + _web_files(web)
    out: list[Violation] = []
    for f, i, line in _iter_lines(files):
        m = pat.search(line)
        if m:
            out.append(Violation("industry-nouns", _rel(f), i, line.strip()))
    return out


def guard_float_money(core: Path = CORE) -> list[Violation]:
    return [
        Violation("float-money", _rel(f), i, line.strip())
        for f, i, line in _iter_lines(_py_files(core))
        if _MINOR_TOKEN.search(line) and _FLOAT_CALL.search(line)
    ]


def guard_send_call_sites(core: Path = CORE, adapter: Path = ADAPTER_DIR) -> list[Violation]:
    out: list[Violation] = []
    for f, i, line in _iter_lines(_py_files(core)):
        if _SEND_CALL.search(line) and adapter not in f.parents:
            out.append(Violation("send-call-sites", _rel(f), i, line.strip()))
    return out


def guard_session_set(core: Path = CORE) -> list[Violation]:
    return [
        Violation("session-set-ban", _rel(f), i, line.strip())
        for f, i, line in _iter_lines(_py_files(core))
        if _SESSION_SET_SQL.search(line) or _SESSION_SETCONFIG.search(line)
    ]


def load_allowlist(path: Path = ALLOWLIST) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (entries, errors). Each entry is (path_substring, token); an entry without a
    `# justification` comment is reported as an error (justification is mandatory)."""
    entries: list[tuple[str, str]] = []
    errors: list[str] = []
    if not path.exists():
        return entries, errors
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rule, _, justification = line.partition("#")
        if not justification.strip():
            errors.append(f"allowlist:{lineno}: entry has no '# justification': {line}")
            continue
        path_sub, _, token = rule.strip().partition("::")
        entries.append((path_sub.strip(), token.strip()))
    return entries, errors


def _excused(v: Violation, entries: list[tuple[str, str]]) -> bool:
    return any(
        path_sub and path_sub in v.file and token.lower() in v.text.lower()
        for path_sub, token in entries
    )


def run_all() -> tuple[list[Violation], list[str]]:
    entries, errors = load_allowlist()
    violations: list[Violation] = []
    for guard in (
        guard_core_not_verticals,
        guard_industry_nouns,
        guard_float_money,
        guard_send_call_sites,
        guard_session_set,
    ):
        violations.extend(v for v in guard() if not _excused(v, entries))
    return violations, errors


def main() -> int:
    violations, errors = run_all()
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    for v in violations:
        print(f"VIOLATION {v}", file=sys.stderr)
    if violations or errors:
        print(
            f"\nlint guards FAILED: {len(violations)} violation(s), "
            f"{len(errors)} allowlist error(s)",
            file=sys.stderr,
        )
        return 1
    print(
        "lint guards passed (core-not-verticals, industry-nouns, float-money, "
        "send-call-sites, session-set-ban)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
