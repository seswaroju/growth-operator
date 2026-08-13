"""053 recovery attempts and worker authority

PILOT-1C. Three additive pieces, no backfill, no destructive change.

**`recovery_attempts`** — the durable identity for one recovery of one silence episode. It exists so
we can answer, without inference: which ghost, which workflow run, which strategy the *owner* chose,
which outbound message, was it accepted, was it delivered, did the customer reply, and when each
transition happened.

*Silence episode.* `silence_episode_anchor` is the customer's last-message timestamp — the fact that
defines the episode. The partial unique index makes at most **one provider-accepted send** per
episode, so a daily re-sweep or a redelivered event cannot touch the same customer twice for the
same silence. A genuine later reply moves the anchor, so a subsequent re-silence is a new episode
(still bounded by the 3-per-30-days cap, which counts only rows that actually reached `sent_at`).

**`messages.idempotency_key` + unique index** — the real fix for duplicate dispatch. A uniqueness
constraint evaluated *after* a send would not help: two workers could both call Meta first. Claiming
this key durably **before** the provider call is what makes at-most-once dispatch true, and it lives
on `messages` because that is the row the authoritative send path already writes.

**`agent_runs` worker-authority columns** — the persisted provenance of an internal-worker grant, so
start, resume, approval-resume and the mediation boundary all re-verify the *same* authority instead
of re-deriving it from whatever the caller passes.

RLS: `recovery_attempts` is org-owned and gets FORCE RLS in this migration, per CLAUDE.md §15.3.
`messages` and `agent_runs` keep their existing policies untouched.

Revision ID: 05ee829beb92
Revises: e605b61fbf0f
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

from migrations.lib.rls import apply_rls, drop_rls

revision = "05ee829beb92"
down_revision = "e605b61fbf0f"
branch_labels = None
depends_on = None

#: Deliberately explicit. `dispatching` and `delivery_unknown` exist because Meta is an external
#: system: the database cannot commit atomically with it, so a crash after the provider may have
#: accepted must resolve to an ambiguous state a human or a status webhook settles — never to an
#: automatic second message.
_STATUS = (
    "proposed", "awaiting_approval", "declined", "blocked", "dispatching",
    "sent", "delivery_unknown", "delivered", "failed", "replied", "expired",
)


def upgrade() -> None:
    op.create_table(
        "recovery_attempts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        # The fact that defines this silence episode.
        sa.Column("silence_episode_anchor", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        # The OWNER's decision — never the model's recommendation.
        sa.Column("selected_option_id", sa.Text(), nullable=True),
        sa.Column("selected_reason", sa.Text(), nullable=True),
        sa.Column("selected_action_id", sa.Text(), nullable=True),
        sa.Column("owner_handled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("template_key", sa.Text(), nullable=True),
        sa.Column("template_language", sa.Text(), nullable=True),
        sa.Column("outbound_message_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inbound_reply_message_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _STATUS) + ")",
            name="ck_recovery_attempts_status"),
    )
    op.create_index("ix_recovery_attempts_lead", "recovery_attempts", ["org_id", "lead_id"])
    op.create_index("ix_recovery_attempts_status", "recovery_attempts", ["org_id", "status"])
    # At most ONE provider-accepted send per silence episode. Partial, so proposed/declined/blocked
    # attempts for the same episode remain recordable — they are history, not touches.
    op.create_index(
        "uq_recovery_attempts_episode_sent", "recovery_attempts",
        ["org_id", "lead_id", "silence_episode_anchor"],
        unique=True, postgresql_where=sa.text("sent_at IS NOT NULL"))
    apply_rls("recovery_attempts")

    # Durable dispatch claim, taken BEFORE the provider call.
    op.add_column("messages", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("recovery_attempt_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "uq_messages_idempotency", "messages", ["org_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    op.create_index(
        "ix_messages_recovery_attempt", "messages", ["recovery_attempt_id"],
        postgresql_where=sa.text("recovery_attempt_id IS NOT NULL"))

    # Persisted internal-worker authority provenance.
    op.add_column("agent_runs", sa.Column("worker_capability", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("worker_task", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("worker_workflow_key", sa.Text(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("worker_definition_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "worker_definition_id")
    op.drop_column("agent_runs", "worker_workflow_key")
    op.drop_column("agent_runs", "worker_task")
    op.drop_column("agent_runs", "worker_capability")
    op.drop_index("ix_messages_recovery_attempt", table_name="messages")
    op.drop_index("uq_messages_idempotency", table_name="messages")
    op.drop_column("messages", "recovery_attempt_id")
    op.drop_column("messages", "idempotency_key")
    drop_rls("recovery_attempts")
    op.drop_index("uq_recovery_attempts_episode_sent", table_name="recovery_attempts")
    op.drop_index("ix_recovery_attempts_status", table_name="recovery_attempts")
    op.drop_index("ix_recovery_attempts_lead", table_name="recovery_attempts")
    op.drop_table("recovery_attempts")
