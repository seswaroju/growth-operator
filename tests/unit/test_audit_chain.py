"""Pure hash-chain logic (MVP-024) — no database.

Covers canonical JSON stability and the `verify_chain` break detector (clean, tamper, gap,
and partial-slice starting points).
"""

from __future__ import annotations

from dataclasses import replace

from core.audit.writer import (
    GENESIS_PREV_HASH,
    ChainRecord,
    _entry_hash,
    canonical_json,
    verify_chain,
)


def _record(seq: int, prev_hash: str, action: str = "message.send") -> ChainRecord:
    payload = {"n": seq}
    entry_hash = _entry_hash(
        prev_hash=prev_hash, seq=seq, actor_type="user", actor_id=None,
        action=action, resource=None, payload=payload, permission_manifest_hash=None,
    )
    return ChainRecord(
        seq=seq, actor_type="user", actor_id=None, action=action, resource=None,
        payload=payload, prev_hash=prev_hash, entry_hash=entry_hash,
        permission_manifest_hash=None,
    )


def _chain(n: int) -> list[ChainRecord]:
    records: list[ChainRecord] = []
    prev = GENESIS_PREV_HASH
    for i in range(1, n + 1):
        rec = _record(i, prev)
        records.append(rec)
        prev = rec.entry_hash
    return records


def test_canonical_json_is_sorted_and_compact() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canonical_json({"a": 2, "b": 1}) == '{"a":2,"b":1}'  # order-independent


def test_clean_chain_verifies() -> None:
    assert verify_chain(_chain(5)) is None
    assert verify_chain([]) is None


def test_tampered_row_detected_at_exact_seq() -> None:
    records = _chain(5)
    # Change seq 3's action without recomputing its stored entry_hash → hash mismatch.
    records[2] = replace(records[2], action="approval.resolved")
    assert verify_chain(records) == 3


def test_broken_link_detected() -> None:
    records = _chain(5)
    records[3] = replace(records[3], prev_hash="deadbeef")  # seq 4's link is wrong
    assert verify_chain(records) == 4


def test_sequence_gap_detected() -> None:
    records = _chain(5)
    del records[2]  # remove seq 3 → seq 4 appears where 3 was expected
    assert verify_chain(records) == 4


def test_partial_slice_from_seq_verifies_forward() -> None:
    # A slice that doesn't start at genesis is trusted from its first row and verified fwd.
    full = _chain(6)
    assert verify_chain(full[2:]) is None  # seq 3..6 intact
    tampered = full[2:]
    tampered[1] = replace(tampered[1], action="x")  # tamper seq 4
    assert verify_chain(tampered) == 4


def test_tampered_genesis_prev_hash_detected() -> None:
    records = _chain(3)
    records[0] = replace(records[0], prev_hash="not-empty")
    assert verify_chain(records) == 1
