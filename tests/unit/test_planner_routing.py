"""Planner classification + routing (MVP-056) — the routing_golden, over the real jewelry pack.

Loads the jewelry taxonomy from the pack (no DB) and asserts 20 representative customer messages
route to the right archetype+task, plus the concierge+clarify fallback for an unclassifiable
message. This is the routing_golden acceptance (20/20).
"""

from __future__ import annotations

from core.packs.taxonomy import load_taxonomy
from core.runtime.planner import classify, route_message

TAX = load_taxonomy("jewelry")

# (message, expected archetype, expected task)
ROUTING_GOLDEN = [
    ("Hello, good morning!", "concierge", "qualify"),
    ("Show me your latest collection", "concierge", "qualify"),
    ("Looking for something for a wedding", "concierge", "qualify"),
    ("I want a gift for my wife", "concierge", "qualify"),
    ("What's the price range for bangles?", "concierge", "qualify"),
    ("What is the purity of this gold?", "concierge", "catalog_answer"),
    ("How many grams is this bangle?", "concierge", "catalog_answer"),
    ("Do you have this in stock?", "concierge", "catalog_answer"),
    ("Is it available in 22 karat?", "concierge", "catalog_answer"),
    ("Can you customize a design for me?", "concierge", "catalog_answer"),
    ("What is the price of this necklace?", "concierge", "quote"),
    ("How much for this ring?", "concierge", "quote"),
    ("Can you give me the rate?", "concierge", "quote"),
    ("Any discount on this?", "concierge", "quote"),
    ("What is your best price?", "concierge", "quote"),
    ("I want to visit your store", "concierge", "book_visit"),
    ("Can I book an appointment tomorrow?", "concierge", "book_visit"),
    ("My ring is broken, please repair it", "support", "ticket_handle"),
    ("What is the status of my order?", "support", "ticket_handle"),
    ("I have a complaint about a defective item", "support", "ticket_handle"),
]


def test_routing_golden_20() -> None:
    misses = []
    for body, archetype, task in ROUTING_GOLDEN:
        route, _intent, clarify = route_message(body, TAX)
        if route.archetype != archetype or route.task != task or clarify:
            misses.append((body, f"{route.archetype}/{route.task} clarify={clarify}"))
    assert not misses, f"routing_golden misses: {misses}"
    assert len(ROUTING_GOLDEN) == 20


def test_unclassifiable_falls_back_to_concierge_clarify() -> None:
    route, intent, clarify = route_message("asdfghjkl qwerty zzz", TAX)
    assert (route.archetype, route.task) == ("concierge", "qualify")
    assert intent is None and clarify is True


def test_classify_longest_keyword_wins() -> None:
    # "exchange policy" (specific) beats a bare "exchange" if both were present; and a price
    # question routes to price_request, not item_question.
    assert classify("what is your exchange policy", TAX.intent_keywords) == "exchange_policy"
    assert classify("what is the price of this necklace", TAX.intent_keywords) == "price_request"


def test_classify_no_keyword_returns_none() -> None:
    assert classify("xyzzy", TAX.intent_keywords) is None
