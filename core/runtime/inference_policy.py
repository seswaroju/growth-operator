"""Per-node inference policy — reasoning mode and deadlines (PILOT-1D-L, #48).

Two things a node needs decided for it that are **not** model selection. `RoutingModel` answers
"which provider and which model"; this module answers "how should that model be asked, and how long
may it take". Keeping them apart is what lets a store's model override stay a two-field choice
(provider, model) while inference behaviour stays platform-owned.

**Platform-controlled, never tenant-supplied.** Nothing here is read from `model_routes`,
`org_model_routes`, an admin API, or model output. A route's `params` are not merged into a request
body: a tenant able to write arbitrary vendor JSON could disable safety features, redirect
behaviour, or inflate spend, and `models_admin` deliberately exposes only provider and model. The
policy below is code.

**Provider-neutral.** `ReasoningMode` says what Vaylorn wants, not how any vendor spells it. The
translation to a wire field belongs to the adapter for the provider actually selected, so a node
policy keeps meaning if the same node is routed to a different vendor tomorrow.
"""

from __future__ import annotations

from enum import StrEnum


class ReasoningMode(StrEnum):
    """How much deliberation Vaylorn wants from the model on this node.

    `DEFAULT` sends nothing and accepts whatever the vendor does by default — the honest name for
    "we have not decided", and the safe value for any node not listed below, since it cannot change
    the behaviour of a node nobody has thought about.
    """

    #: Answer directly. For conversational turns where deliberation costs latency and tokens
    #: without improving a reply.
    OFF = "off"
    #: Leave it to the vendor. Explicitly *not* a synonym for OFF.
    DEFAULT = "default"


#: Node → mode. Only nodes with a deliberate decision appear; everything else gets `DEFAULT`.
#:
#: `priya.reason` is the customer-facing concierge turn. A greeting like "Hi, can someone help me?"
#: needs a prompt, courteous answer, and DeepSeek V4 defaults thinking **on**, so the pilot was
#: paying reasoning latency on every hello without ever having chosen to. Whether that latency
#: caused the #48 timeout is NOT established; what is established is that the mode was implicit.
#: Setting it explicitly is worth doing on its own terms.
#:
#: Deliberately narrow: future strategy, analysis or planning nodes are not listed and therefore
#: keep full reasoning. Turning thinking off globally would be the wrong trade for a node whose job
#: is to think.
NODE_REASONING: dict[str, ReasoningMode] = {
    "priya.reason": ReasoningMode.OFF,
}


def reasoning_for(node_key: str) -> ReasoningMode:
    """The reasoning mode for a node. Unknown nodes get `DEFAULT` — fail-safe, because it is the
    only value that changes nothing about how a node is currently asked."""
    return NODE_REASONING.get(node_key, ReasoningMode.DEFAULT)


# ---- deadlines ---------------------------------------------------------------------------------
#
# What was observed live (#48): a real customer message reached the model node, an `HTTP 200` from
# the provider appeared in the worker log, and the run ended `provider_unavailable / model_turn
# timeout` after ~30s with no `costs_lite` row and no persisted `model_turn` step.
#
# What that observation does NOT establish: that `provider.complete` had returned before the node
# was cancelled, that telemetry hung, or which of them consumed the time. Those remain open, and
# the split provider/telemetry logs in `routing.py` exist to settle them next time.
#
# What it does establish, from the code alone and independently of the incident: both deadlines
# were 30.0 — the inner HTTP call and the outer node containing it — so a provider taking its full
# budget leaves **zero** time for parsing, normalization, cost telemetry or fallback bookkeeping.
# That is a defect whether or not it is the explanation for this particular run.
#
# The constants below are derived rather than chosen independently, so the invariant cannot be
# broken by editing one number. Two unrelated default arguments that merely happened to differ
# would reproduce the same class of bug the first time someone tuned one of them.

#: One provider attempt, end to end over HTTP. Long enough for a slow-but-working vendor; short
#: enough that a dead one is abandoned while there is still time to try another.
PROVIDER_ATTEMPT_TIMEOUT_S = 20.0

#: Attempts the node deadline is sized for: a primary and one fallback. This is what the seeded
#: global route and the hard-coded fail-safe chain both use.
#:
#: It is a *budget*, not a limit. A route may carry more fallbacks than this — the chain length is
#: whatever an operator seeded — in which case the outer deadline stops the node partway through
#: the chain rather than the chain stopping itself. That backstop is the pre-existing behaviour and
#: is safe (the run interrupts and is resumable); sizing the budget for the routes we actually ship
#: is deliberate, because covering an unbounded chain would mean an unbounded customer wait.
PLANNED_ATTEMPTS = 2

#: Vaylorn-side work inside the node deadline but outside the provider call: route lookup, response
#: parsing, adapter normalization, cost telemetry (a database round trip) and fallback bookkeeping.
#:
#: Deliberately NOT the executor's step checkpoint — that is written after `asyncio.wait_for`
#: returns, so it falls outside `MODEL_NODE_TIMEOUT_S` entirely and this budget does not cover it.
VAYLORN_OVERHEAD_S = 5.0

#: The model node's deadline. Derived, so it is always strictly greater than a full attempt budget.
MODEL_NODE_TIMEOUT_S = PROVIDER_ATTEMPT_TIMEOUT_S * PLANNED_ATTEMPTS + VAYLORN_OVERHEAD_S  # 45.0

# The invariant, asserted at import so a bad edit fails immediately and everywhere rather than
# surfacing as an intermittent timeout on a live customer message.
assert PROVIDER_ATTEMPT_TIMEOUT_S < MODEL_NODE_TIMEOUT_S  # noqa: S101
