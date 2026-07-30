"""Audit entry taxonomy — the canonical `action` strings (MVP-024).

From docs/21-platform/audit-logging.md. Append-only in MVP; outcome variants are formed by
appending `:succeeded` / `:failed` to a base action (see `audit.write_outcome`).
"""

from __future__ import annotations

MESSAGE_SEND = "message.send"
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_RESOLVED = "approval.resolved"
APPROVAL_EXPIRED = "approval.expired"
PRICING_COMPUTED = "pricing.computed"
SETTINGS_CHANGED = "settings.changed"
FLAG_CHANGED = "flag.changed"
PACK_INSTALLED = "pack.installed"
PACK_UPGRADED = "pack.upgraded"
PACK_UNINSTALLED = "pack.uninstalled"
PROMPT_BINDING_CHANGED = "prompt.binding_changed"
IMPERSONATION_START = "impersonation.start"
IMPERSONATION_END = "impersonation.end"
DSR_OPENED = "dsr.opened"
DSR_FULFILLED = "dsr.fulfilled"
BREAK_GLASS_USED = "break_glass.used"
AGENT_CIRCUIT_OPEN = "agent.circuit_open"
AGENT_CIRCUIT_CLOSE = "agent.circuit_close"
WORKFLOW_ACTIVATED = "workflow.activated"
WORKFLOW_COMPENSATED = "workflow.compensated"
MANIFEST_VIOLATION = "manifest.violation"

# Actor types.
ACTOR_USER = "user"
ACTOR_AGENT = "agent"
ACTOR_SYSTEM = "system"
ACTOR_API_KEY = "api_key"

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
