"""The stored copy of an organisation's graph projection.

One row per organisation: a compressed, checksummed copy of the in-memory
projection (``app/modules/intelligence/services/graph_projection.py``) and the
event-log offset it had reached, so a fresh process loads it and replays only
the log after that offset instead of rebuilding from the relational store. The
relational store stays the authority and the row is disposable: deleting it only
costs one rebuild.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

__all__ = ["GraphProjectionSnapshot"]


class GraphProjectionSnapshot(TenantMixin, db.Model):
    __tablename__ = "graph_projection_snapshots"
    __table_args__ = (
        db.UniqueConstraint("organization_id", name="uq_graph_projection_snapshot_org"),
    )

    id = db.Column(db.Integer, primary_key=True)
    format_version = db.Column(db.Integer, nullable=False, default=1)
    # The highest event-log ordinal reflected in the payload.
    last_ordinal = db.Column(db.BigInteger, nullable=False, default=0)
    node_count = db.Column(db.Integer, nullable=False, default=0)
    edge_count = db.Column(db.Integer, nullable=False, default=0)
    # SHA-256 of the stored payload bytes; a payload that no longer matches is ignored.
    fingerprint = db.Column(db.String(64), nullable=False)
    payload = db.Column(db.LargeBinary, nullable=False)
    built_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # When the projection was last compared against a rebuild from the relational store.
    verified_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
