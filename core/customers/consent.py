"""Canonical marketing-consent semantics (PILOT-1C).

Before this module the platform held **four** different answers to one question:

    core/workflows/guards.py     marketing → {"explicit"}
    core/channels/whatsapp/send  marketing → {"opted_in", "granted"}
    core/campaigns/audience.py   SQL       → IN ('opted_in', 'granted')
    core/landing/leads.py        writes    → 'explicit'

So a contact captured by a landing page passed the recovery guard and was then refused by the send
gate, while an `opted_in` contact was campaign-eligible but blocked by the guard. Both directions
were broken, and neither failure was visible until a message did not go out.

One predicate now answers it everywhere. Legacy `explicit` stays positive indefinitely — the fix is
forward-only, because rewriting historical consent records to make a ticket pass would be the worst
possible way to treat a consent field.

Marketing permission is never inferred from a conversation existing: a customer writing to a store
is not a customer agreeing to be marketed to.
"""

from __future__ import annotations

#: Stored values that mean the customer accepted marketing contact.
#: `granted` is canonical for NEW data — it is what the authoritative send gate and campaign
#: audience already required. `opted_in` and `explicit` are accepted legacy spellings.
POSITIVE_MARKETING: frozenset[str] = frozenset({"granted", "opted_in", "explicit"})

#: The value new writers persist. One spelling for everything created from here on.
CANONICAL_MARKETING_CONSENT = "granted"

#: Additionally acceptable for non-marketing (transactional/service) purposes, which are governed
#: separately — a customer who wrote in may be answered without opting into marketing.
POSITIVE_TRANSACTIONAL: frozenset[str] = POSITIVE_MARKETING | frozenset({"implicit"})


def marketing_allowed(status: str | None) -> bool:
    """May we send this contact a MARKETING message? Absent/unknown/denied → no."""
    return bool(status) and str(status).strip().lower() in POSITIVE_MARKETING


def transactional_allowed(status: str | None) -> bool:
    """May we send a transactional/service message? Broader, and governed separately."""
    return bool(status) and str(status).strip().lower() in POSITIVE_TRANSACTIONAL


def marketing_sql_in_list() -> str:
    """`'a', 'b', …` for an SQL `IN (...)`, so audience queries share this one definition rather
    than embedding a fourth literal list."""
    return ", ".join(f"'{v}'" for v in sorted(POSITIVE_MARKETING))
