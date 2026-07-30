"""audit — append-only per-org hash chain (ADR-007). See docs/21-platform/audit-logging.md."""

from core.audit.writer import (
    AuditEntry,
    AuditId,
    ChainRecord,
    canonical_json,
    verify_capability,
    verify_chain,
    write,
    write_outcome,
)

__all__ = [
    "AuditEntry",
    "AuditId",
    "ChainRecord",
    "canonical_json",
    "verify_capability",
    "verify_chain",
    "write",
    "write_outcome",
]
