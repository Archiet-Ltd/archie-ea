"""Where each organisation's change-feed consumer has got to.

One row per organisation and consumer: the ordinal of the last event in the
organisation's event log whose notifications have been committed. The consumer
moves it in the same transaction that writes the notifications, so a crash
replays the range instead of losing it.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

__all__ = ["ChangeFeedOffset"]


class ChangeFeedOffset(TenantMixin, db.Model):
    """The last event ordinal a consumer has finished with, per organisation."""

    __tablename__ = "change_feed_offsets"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "consumer", name="uq_change_feed_offset_consumer"),
    )

    id = db.Column(db.Integer, primary_key=True)
    consumer = db.Column(db.String(48), nullable=False)
    last_ordinal = db.Column(db.BigInteger, nullable=False, default=0, server_default="0")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
