"""campaigns — the campaign model the analytics engine measures (Phase 3.5-eng, A2.1).

Create/list/get campaign records + a `campaign.executed` consumer that records send counts when a
real send-flow emits it. The send-flow itself (campaigner agent execution) is a future feature; this
module is the durable model + a ready consumer.
"""
