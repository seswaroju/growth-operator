"""Ghost-diagnosis eval harness (MVP-073j) — offline, gated-simulated.

Scores a deterministic keyword diagnoser over the synthetic ghost set. It validates **plumbing** —
that the `diagnose → reason → recovery-action` mapping holds end to end — NOT real-world correctness
(which needs the D1/D2 loop: real exported WhatsApp logs + a wired frontier model). The diagnoser
is the **gated-simulated** stand-in: with `llm_provider_enabled` OFF (default) it returns
deterministic ranked output; ON but the model unwired it fails closed (`provider_unavailable`).
**Real-ready:** wire the model + flip the gate and the SAME workflow runs on real threads with no
change. This lives in `scripts/` (jewelry logic, not `core/`).

Run: `uv run python scripts/ghost_eval.py`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.common.config import get_settings
from core.common.errors import GrowthOperatorError

_PACK = Path(__file__).resolve().parents[1] / "verticals" / "jewelry"
_TAXONOMY = _PACK / "playbooks" / "ghost_reason_taxonomy.yaml"
_SYNTH = _PACK / "playbooks" / "synthetic_ghost_set.yaml"


def _gate() -> None:
    """Fail closed if the real LLM is enabled but the frontier diagnosis model isn't wired."""
    if get_settings().llm_provider_enabled:
        raise GrowthOperatorError(
            "provider_unavailable", "real ghost diagnosis needs the frontier LLM (not wired)")


def load_taxonomy() -> dict[str, Any]:
    return yaml.safe_load(_TAXONOMY.read_text())


def load_synthetic() -> list[dict[str, Any]]:
    return yaml.safe_load(_SYNTH.read_text())["cases"]


def simulated_diagnose(thread: str, taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic keyword diagnosis over the 8 reasons — the gated-simulated stand-in for the
    frontier model. No signal in the thread → abstain (routes the owner to pick)."""
    _gate()
    tax = taxonomy or load_taxonomy()
    reasons = tax["reasons"]
    text = thread.lower()
    scores = {name: sum(1 for sig in r.get("signals", []) if sig.lower() in text)
              for name, r in reasons.items()}
    scores = {n: c for n, c in scores.items() if c > 0}
    if not scores:
        return {"top_reason": None, "ranked": [], "abstain": True, "confidence_top": 0.0,
                "recommended_action_id": tax["abstain"]["action"]}
    total = sum(scores.values())
    ranked = sorted(({"reason": n, "confidence": round(c / total, 3)} for n, c in scores.items()),
                    key=lambda x: (-x["confidence"], x["reason"]))
    top = ranked[0]["reason"]
    return {"top_reason": top, "ranked": ranked, "abstain": False,
            "confidence_top": ranked[0]["confidence"],
            "recommended_action_id": reasons[top]["action"]}


def run_eval() -> dict[str, Any]:
    """Diagnose every synthetic case; return accuracy, a confusion map, and per-case results."""
    tax = load_taxonomy()
    cases = load_synthetic()
    confusion: dict[str, dict[str, int]] = {}
    results: list[dict[str, Any]] = []
    correct = 0
    for c in cases:
        d = simulated_diagnose(c["thread"], tax)
        predicted = "abstain" if d["abstain"] else d["top_reason"]
        expected = c["expected_reason"]
        ok = predicted == expected
        correct += int(ok)
        confusion.setdefault(expected, {})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
        results.append({"id": c["id"], "expected": expected, "predicted": predicted, "ok": ok,
                        "recommended_action_id": d["recommended_action_id"]})
    n = len(cases)
    return {"n": n, "correct": correct, "accuracy": round(correct / n, 3) if n else 0.0,
            "confusion": confusion, "results": results}


def main() -> None:
    report = run_eval()
    print(f"ghost-diagnosis eval: {report['correct']}/{report['n']} "
          f"(accuracy {report['accuracy']})")
    for row in report["results"]:
        mark = "ok " if row["ok"] else "MISS"
        print(f"  [{mark}] {row['id']:10} exp={row['expected']:26} "
              f"pred={row['predicted']:26} → {row['recommended_action_id']}")


if __name__ == "__main__":
    main()
