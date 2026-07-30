#!/usr/bin/env python
"""Generate core/events/types.py from docs/implementation/events/topics.yaml (MVP-030).

`topics.yaml` is the single source of truth for event payload shapes. This writes a checked-in
`types.py` (payload field specs + a checksum). A drift test recomputes the checksum from the
YAML and fails CI if `types.py` is stale; `outbox.emit` validates payloads against the specs.

    uv run python scripts/gen_events.py          # regenerate
    uv run python scripts/gen_events.py --check   # exit 1 if regeneration would change the file
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TOPICS_YAML = REPO / "docs" / "implementation" / "events" / "topics.yaml"
OUT = REPO / "core" / "events" / "types.py"

_HEADER = '''"""AUTO-GENERATED from docs/implementation/events/topics.yaml — do not edit by hand.

Regenerate with `uv run python scripts/gen_events.py`. A drift test (tests/unit/
test_event_types.py) fails if this file is out of sync with topics.yaml.
"""

from __future__ import annotations

'''


def _specs() -> dict[str, dict[str, str]]:
    data = yaml.safe_load(TOPICS_YAML.read_text())
    return {t["type"]: dict(t.get("payload") or {}) for t in data["topics"]}


def checksum(specs: dict[str, dict[str, str]]) -> str:
    canonical = json.dumps(specs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def render(specs: dict[str, dict[str, str]]) -> str:
    body = "PAYLOAD_SPECS: dict[str, dict[str, str]] = " + json.dumps(specs, indent=4)
    return f'{_HEADER}{body}\n\nTOPICS_CHECKSUM = "{checksum(specs)}"\n'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()

    rendered = render(_specs())
    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != rendered:
            print(
                "gen_events: core/events/types.py is stale — run scripts/gen_events.py",
                file=sys.stderr,
            )
            return 1
        print("gen_events: types.py is up to date")
        return 0
    OUT.write_text(rendered)
    print(f"gen_events: wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
