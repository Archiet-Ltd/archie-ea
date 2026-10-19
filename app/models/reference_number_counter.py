"""The atomic, per-family counter ``app.utils.reference_numbers.next_reference``
allocates from.

Deliberately NOT a ``TenantMixin``/``HybridTenantMixin`` model: it carries no
``organization_id`` at all, because the families it counts (``AD-``,
``ARB-<year>-``, ``ACR-<year>-``, ``ACN-<year>-``) are already required to be
unique across every organisation by their own unique index on the target
table. Row-level security on this table would be a no-op at best and actively
wrong at worst (a tenant-scoped read is exactly the bug this table exists to
remove -- see ``app/utils/reference_numbers.py``).
"""

from __future__ import annotations

from app import db


class ReferenceNumberCounter(db.Model):
    """One row per (table, column, prefix) family; ``last_value`` is the
    highest number already handed out for that family."""

    __tablename__ = "reference_number_counters"

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(128), nullable=False)
    column_name = db.Column(db.String(128), nullable=False)
    prefix = db.Column(db.String(64), nullable=False)
    last_value = db.Column(db.Integer, nullable=False, server_default="0")
    updated_at = db.Column(db.DateTime, server_default=db.func.clock_timestamp())

    __table_args__ = (
        db.UniqueConstraint(
            "table_name", "column_name", "prefix", name="uq_reference_number_counters_family"
        ),
    )
