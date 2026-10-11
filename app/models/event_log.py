"""Platform event log — the durable append-only stream every consumer reads.

One table partitioned by month, with per-organisation monotonic offsets.
Writers use ``app/services/event_log_service.py``; entity services emit
through ``app/services/outbox.py`` and the relay copies rows here.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB

from app import db
from app.models.mixins import TenantMixin


class EventLogRecord(TenantMixin, db.Model):
    """One immutable event in the platform-wide ordered log.

    Partitioned by ``created_at`` (monthly range).  ``ordinal`` is
    monotonic within an organisation, assigned by the relay when it
    copies an outbox row here.
    """

    __tablename__ = "event_log"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    event_type = db.Column(db.String(160), nullable=False)
    event_id = db.Column(db.String(36), nullable=False)
    payload_json = db.Column(JSONB, nullable=False)
    ordinal = db.Column(db.BigInteger, nullable=False)
    entity_type = db.Column(db.String(80), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    outbox_event_id = db.Column(db.Integer, nullable=True, index=True)
    relayed_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "event_id", "created_at",
            name="uq_event_log_event_id",
        ),
        db.CheckConstraint("ordinal >= 1", name="ck_event_log_ordinal"),
    )


__all__ = ["EventLogRecord"]