"""Add public_visitor_events table.

First-party, cookieless page/event analytics for public marketing pages:
page views, sign-up started/completed, a pricing-plan click, an offer
enquiry submitted. No organisation or user link -- a marketing-page visitor
has neither, same shape as waitlist_signups and product_inquiries. No raw
IP address or user agent is ever stored -- only a one-way, daily-rotating
hash (see app/services/visitor_hash.py).

NOTE ON MIGRATION HEADS: at the time this revision was written, main had
three migration heads (0bae4490fe0d, 20261005_bf_capability_fk and this
revision's parent, 20261006_owner_element_ref), from two independent forks
further up the graph. This revision only adds a brand-new table untouched
by any of the three branches, so it chains onto one of them
(20261006_owner_element_ref, the most recently dated) without resolving the
fork itself -- that reconciliation (one or more merge revisions across the
three branches) is a separate, cross-cutting change this revision does not
attempt.

Revision ID: 20261007_public_visitor_events
Revises: 20261006_owner_element_ref
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261007_public_visitor_events"
down_revision = "20261006_owner_element_ref"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Guard against create_all() (flask init-db) already having created this
    # table on a fresh database before this revision ever runs -- same
    # to_regclass idiom as 20261006_formula_registers.py and
    # 20261005_business_function_capability_fk.py.
    if not bind.execute(text("SELECT to_regclass('public_visitor_events')")).scalar():
        op.create_table(
            "public_visitor_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("path", sa.String(length=255), nullable=True),
            sa.Column("plan", sa.String(length=40), nullable=True),
            sa.Column("offer", sa.String(length=80), nullable=True),
            sa.Column("visitor_hash", sa.String(length=64), nullable=False),
            sa.Column("visitor_day", sa.Date(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "event_type IN ('page_view','signup_started','signup_completed',"
                "'pricing_plan_click','offer_enquiry_submitted')",
                name="ck_public_visitor_events_event_type",
            ),
        )
    op.create_index(
        "ix_public_visitor_events_event_type",
        "public_visitor_events",
        ["event_type"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_public_visitor_events_visitor_hash",
        "public_visitor_events",
        ["visitor_hash"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_public_visitor_events_hash_day",
        "public_visitor_events",
        ["visitor_hash", "visitor_day"],
        if_not_exists=True,
    )


def downgrade():
    op.drop_index(
        "ix_public_visitor_events_hash_day",
        table_name="public_visitor_events",
        if_exists=True,
    )
    op.drop_index(
        "ix_public_visitor_events_visitor_hash",
        table_name="public_visitor_events",
        if_exists=True,
    )
    op.drop_index(
        "ix_public_visitor_events_event_type",
        table_name="public_visitor_events",
        if_exists=True,
    )
    op.drop_table("public_visitor_events", if_exists=True)
