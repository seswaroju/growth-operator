"""Opt-out keyword net (MVP-036).

A customer who replies with a STOP/UNSUB keyword is auto-suppressed by the normalizer. The
MVP ships this platform list (English + romanised Hindi + Telugu); packs may extend it later.

Matching is deliberately strict — the *whole* message, once punctuation is stripped and it is
lower-cased, must equal a keyword. That reliably catches "STOP", "Unsubscribe.", "Band karo"
while never suppressing a hot lead who merely wrote "I couldn't stop thinking about the ring".
"""

from __future__ import annotations

import re
import string

# Whole-message opt-out keywords. Kept lowercase; comparison lower-cases the input.
STOP_KEYWORDS: frozenset[str] = frozenset(
    {
        "stop",
        "unsubscribe",
        "unsub",
        "band karo",   # Hindi (romanised) — "stop it"
        "bandh karo",
        "ఆపండి",       # Telugu — "stop"
    }
)

# Strip ASCII punctuation only — matching Unicode "non-word" would eat Telugu/Devanagari
# combining marks (vowel signs, anusvara) and corrupt non-Latin keywords.
_PUNCT = re.compile(r"[" + re.escape(string.punctuation) + r"]")
_WS = re.compile(r"\s+")


def normalize(body: str) -> str:
    """Lower-case, strip ASCII punctuation, and collapse whitespace."""
    stripped = _PUNCT.sub(" ", body)
    return _WS.sub(" ", stripped).strip().lower()


def is_stop_keyword(body: str) -> bool:
    """True iff the whole message is an opt-out keyword."""
    return normalize(body) in STOP_KEYWORDS
